"""
MindForge 联邦记忆细粒度 ACL

为联邦记忆共享提供细粒度访问控制（Access Control List）：
按「主体（peer）× 资源（记忆/分类/标签/全部）× 操作（read/write/reshare）」
配置 allow / deny 规则，支持优先级、信任阈值与过期时间。

评估语义（参考 AWS IAM / RBAC 混合模型）：
- 默认拒绝（default-deny）：没有任何规则匹配时拒绝访问
- 规则按 priority 从高到低评估，同优先级下 deny 优先于 allow
- 规则可限定 peer 信任阈值（trust_min）与过期时间（expires_at）
- 所有拒绝决策写入审计日志（acl_deny）

与 federated.py（跨节点对等共享）配套：FederatedMemory 可注入本管理器，
在 share_memory / federated_search 时按 ACL 过滤目标节点。
"""

import time
import uuid
from typing import Any, Dict, List, Optional


# 合法操作集合（* 表示全部操作）
ACL_OPERATIONS = ("read", "write", "reshare", "*")

# 合法资源类型
ACL_RESOURCE_TYPES = ("memory", "category", "tag", "all")

# 合法效果
ACL_EFFECTS = ("allow", "deny")


class FederatedACLManager:
    """联邦记忆细粒度 ACL 管理器

    通过 SQLite 持久化 ACL 规则，随主库一起备份。
    拒绝决策写入审计日志（复用 storage._add_audit）。
    """

    def __init__(self, storage):
        self.storage = storage
        self._init_tables()

    # ===== 内部基础设施 =====

    def _conn(self):
        return self.storage._get_conn()

    def _init_tables(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS federated_acl_rules (
                id TEXT PRIMARY KEY,
                principal TEXT NOT NULL,
                resource_type TEXT NOT NULL DEFAULT 'all',
                resource_value TEXT DEFAULT '',
                operations TEXT NOT NULL DEFAULT 'read',
                effect TEXT NOT NULL DEFAULT 'deny',
                priority INTEGER DEFAULT 100,
                trust_min REAL DEFAULT 0.0,
                expires_at REAL DEFAULT 0.0,
                note TEXT DEFAULT '',
                created_by TEXT DEFAULT '',
                created_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_acl_principal
                ON federated_acl_rules(principal);
            CREATE INDEX IF NOT EXISTS idx_acl_resource
                ON federated_acl_rules(resource_type, resource_value);
            CREATE INDEX IF NOT EXISTS idx_acl_effect
                ON federated_acl_rules(effect);
        """)
        conn.commit()

    @staticmethod
    def _parse_resource(resource: str):
        """解析资源表达式。

        支持：
        - "all" 或 "*"            → (all, "")
        - "memory:<memory_id>"     → (memory, <memory_id>)
        - "category:<name>"        → (category, <name>)
        - "tag:<name>"             → (tag, <name>)
        """
        r = (resource or "").strip()
        if not r or r in ("all", "*"):
            return "all", ""
        if ":" not in r:
            raise ValueError(f"无效资源表达式: {resource}（应为 all / memory:<id> / category:<名> / tag:<名>）")
        rtype, rvalue = r.split(":", 1)
        rtype = rtype.strip().lower()
        rvalue = rvalue.strip()
        if rtype not in ACL_RESOURCE_TYPES:
            raise ValueError(f"无效资源类型: {rtype}（可选: {'/'.join(ACL_RESOURCE_TYPES)}）")
        if rtype != "all" and not rvalue:
            raise ValueError(f"资源类型 {rtype} 需要指定值，如 {rtype}:example")
        return rtype, rvalue

    @staticmethod
    def _normalize_operations(operations) -> str:
        """规范化操作列表为逗号分隔字符串"""
        if isinstance(operations, str):
            ops = [o.strip().lower() for o in operations.split(",")]
        else:
            ops = [str(o).strip().lower() for o in (operations or [])]
        valid = []
        for op in ops:
            if not op:
                continue
            if op not in ACL_OPERATIONS:
                raise ValueError(f"无效操作: {op}（可选: {'/'.join(ACL_OPERATIONS)}）")
            valid.append(op)
        if not valid:
            raise ValueError("至少需要指定一个操作（read/write/reshare/*）")
        if "*" in valid:
            return "*"
        # 去重保序
        seen = set()
        uniq = []
        for op in valid:
            if op not in seen:
                seen.add(op)
                uniq.append(op)
        return ",".join(uniq)

    def _row_to_rule(self, row) -> Dict[str, Any]:
        return {
            "rule_id": row["id"],
            "principal": row["principal"],
            "resource_type": row["resource_type"],
            "resource_value": row["resource_value"],
            "operations": row["operations"],
            "effect": row["effect"],
            "priority": row["priority"],
            "trust_min": row["trust_min"],
            "expires_at": row["expires_at"],
            "note": row["note"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }

    # ===== 规则管理 =====

    def add_rule(self, principal: str, resource: str,
                 operations: Any = "read", effect: str = "allow",
                 priority: int = 100, trust_min: float = 0.0,
                 expires_hours: Optional[float] = None,
                 note: str = "", created_by: str = "") -> Dict[str, Any]:
        """添加一条 ACL 规则。

        Args:
            principal: 主体（peer_id），"*" 表示任意节点
            resource: 资源表达式 all / memory:<id> / category:<名> / tag:<名>
            operations: 操作 read/write/reshare/*（字符串逗号分隔或列表）
            effect: allow / deny
            priority: 优先级（越大越先评估，默认 100）
            trust_min: peer 信任阈值（peer 信任度需 >= 该值，规则才生效）
            expires_hours: 规则有效时长（小时），None 表示永久
            note: 备注
            created_by: 创建者
        """
        principal = (principal or "").strip()
        if not principal:
            return {"success": False, "error": "principal 不能为空"}
        if len(principal) > 128:
            return {"success": False, "error": "principal 过长（>128）"}
        try:
            rtype, rvalue = self._parse_resource(resource)
            ops_str = self._normalize_operations(operations)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        effect = (effect or "").strip().lower()
        if effect not in ACL_EFFECTS:
            return {"success": False,
                    "error": f"无效 effect: {effect}（可选: {'/'.join(ACL_EFFECTS)}）"}
        try:
            priority = max(0, min(10000, int(priority)))
            trust_min = max(0.0, min(1.0, float(trust_min)))
        except (TypeError, ValueError):
            return {"success": False, "error": "priority / trust_min 参数非法"}

        now = time.time()
        expires_at = now + float(expires_hours) * 3600 if expires_hours else 0.0
        rule_id = f"acl_{uuid.uuid4().hex[:12]}"

        conn = self._conn()
        conn.execute(
            "INSERT INTO federated_acl_rules (id, principal, resource_type,"
            " resource_value, operations, effect, priority, trust_min,"
            " expires_at, note, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rule_id, principal, rtype, rvalue, ops_str, effect, priority,
             trust_min, expires_at, (note or "").strip()[:500],
             (created_by or "").strip()[:128], now))
        conn.commit()
        self.storage._add_audit("acl_add_rule", rule_id, created_by or principal,
                                "", "INTERNAL",
                                {"principal": principal, "resource": f"{rtype}:{rvalue}",
                                 "operations": ops_str, "effect": effect})
        return {"success": True, "rule_id": rule_id, "principal": principal,
                "resource": resource if rtype != "all" else "all",
                "operations": ops_str, "effect": effect, "priority": priority}

    def remove_rule(self, rule_id: str, actor: str = "") -> Dict[str, Any]:
        """删除一条 ACL 规则"""
        rule_id = (rule_id or "").strip()
        if not rule_id:
            return {"success": False, "error": "rule_id 不能为空"}
        conn = self._conn()
        cur = conn.execute(
            "DELETE FROM federated_acl_rules WHERE id = ?", (rule_id,))
        conn.commit()
        if cur.rowcount == 0:
            return {"success": False, "error": "规则不存在"}
        self.storage._add_audit("acl_remove_rule", rule_id, actor, "",
                                "INTERNAL", {})
        return {"success": True, "rule_id": rule_id}

    def list_rules(self, principal: Optional[str] = None,
                   effect: Optional[str] = None,
                   limit: int = 200) -> List[Dict[str, Any]]:
        """列出 ACL 规则"""
        limit = max(1, min(1000, int(limit)))
        conn = self._conn()
        sql = "SELECT * FROM federated_acl_rules WHERE 1=1"
        params: List[Any] = []
        if principal:
            sql += " AND (principal = ? OR principal = '*')"
            params.append(principal)
        if effect:
            sql += " AND effect = ?"
            params.append(effect.strip().lower())
        sql += " ORDER BY priority DESC, created_at ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_rule(r) for r in rows]

    # ===== 访问评估 =====

    def check_access(self, peer_id: str, memory_id: str,
                     operation: str = "read",
                     peer_trust: Optional[float] = None,
                     memory_category: Optional[str] = None,
                     memory_tags: Optional[List[str]] = None,
                     is_owner: bool = False) -> Dict[str, Any]:
        """评估某个 peer 对某条记忆的访问权限。

        Returns:
            {"allowed": bool, "effect": "allow"|"deny",
             "reason": str, "matched_rule": rule_id|None}
        """
        peer_id = (peer_id or "").strip()
        operation = (operation or "read").strip().lower()
        if not peer_id:
            return {"allowed": False, "effect": "deny",
                    "reason": "peer_id 为空", "matched_rule": None}
        if is_owner:
            return {"allowed": True, "effect": "allow",
                    "reason": "owner 始终允许", "matched_rule": None}
        if operation == "*":
            # v5.4.4 修复 #7：不再静默转为 read，而是返回明确拒绝
            return {"allowed": False, "effect": "deny",
                    "reason": "operation='*' 不允许用于查询，请指定具体操作（read/write/reshare）",
                    "matched_rule": None}
        if operation not in ACL_OPERATIONS:
            return {"allowed": False, "effect": "deny",
                    "reason": f"无效操作: {operation}（可选: read/write/reshare）",
                    "matched_rule": None}

        tags_lower = {str(t).strip().lower() for t in (memory_tags or []) if str(t).strip()}
        now = time.time()
        conn = self._conn()
        # 候选规则：主体匹配（精确或通配）
        # v5.4.2：同优先级下 deny 先于 allow 评估（deny-overrides）
        rows = conn.execute(
            "SELECT * FROM federated_acl_rules"
            " WHERE (principal = ? OR principal = '*')"
            " ORDER BY priority DESC,"
            " CASE effect WHEN 'deny' THEN 0 ELSE 1 END,"
            " created_at ASC",
            (peer_id,)).fetchall()

        for row in rows:
            # 过期规则跳过
            if row["expires_at"] and row["expires_at"] > 0 and row["expires_at"] < now:
                continue
            # 信任阈值：仅当提供了 peer_trust 时校验
            if row["trust_min"] and row["trust_min"] > 0:
                if peer_trust is None or peer_trust < row["trust_min"]:
                    continue
            # 操作匹配
            ops = row["operations"]
            if ops != "*" and operation not in {o.strip() for o in ops.split(",")}:
                continue
            # 资源匹配
            rtype = row["resource_type"]
            rvalue = row["resource_value"]
            matched = False
            if rtype == "all":
                matched = True
            elif rtype == "memory":
                matched = (memory_id and rvalue == memory_id)
            elif rtype == "category":
                matched = (memory_category is not None
                           and str(memory_category).strip() == rvalue)
            elif rtype == "tag":
                matched = rvalue.lower() in tags_lower
            if not matched:
                continue

            # 命中规则
            allowed = (row["effect"] == "allow")
            result = {
                "allowed": allowed,
                "effect": row["effect"],
                "reason": f"命中规则 {row['id']}（{row['effect']}, "
                          f"{rtype}:{rvalue or '*'}, priority={row['priority']}）",
                "matched_rule": row["id"],
            }
            if not allowed:
                self._audit_deny(peer_id, memory_id, operation, row["id"])
            return result

        # 默认拒绝
        self._audit_deny(peer_id, memory_id, operation, None)
        return {"allowed": False, "effect": "deny",
                "reason": "默认拒绝：无任何匹配规则（default-deny）",
                "matched_rule": None}

    def filter_peers(self, memory_id: str, peer_ids: List[str],
                     operation: str = "read",
                     trust_map: Optional[Dict[str, float]] = None,
                     memory_category: Optional[str] = None,
                     memory_tags: Optional[List[str]] = None) -> Dict[str, Any]:
        """对一组 peer 做访问过滤，返回允许/拒绝明细。

        供 FederatedMemory.share_memory / federated_search 批量过滤使用。
        """
        trust_map = trust_map or {}
        allowed_list: List[str] = []
        denied_map: Dict[str, str] = {}
        for pid in (peer_ids or []):
            decision = self.check_access(
                peer_id=pid, memory_id=memory_id, operation=operation,
                peer_trust=trust_map.get(pid),
                memory_category=memory_category, memory_tags=memory_tags)
            if decision["allowed"]:
                allowed_list.append(pid)
            else:
                denied_map[pid] = decision["reason"]
        return {"memory_id": memory_id, "operation": operation,
                "allowed": allowed_list, "denied": denied_map,
                "allowed_count": len(allowed_list),
                "denied_count": len(denied_map)}

    def _audit_deny(self, peer_id: str, memory_id: str,
                    operation: str, rule_id: Optional[str]):
        """拒绝决策写入审计日志"""
        try:
            self.storage._add_audit(
                "acl_deny", memory_id or "", peer_id, "", "INTERNAL",
                {"operation": operation, "matched_rule": rule_id})
        except Exception:
            # 审计失败不影响访问决策
            pass

    # ===== 统计 =====

    def acl_stats(self) -> Dict[str, Any]:
        """ACL 统计：规则分布 + 拒绝审计计数"""
        conn = self._conn()
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM federated_acl_rules").fetchone()["c"]
        by_effect = conn.execute(
            "SELECT effect, COUNT(*) AS c FROM federated_acl_rules"
            " GROUP BY effect").fetchall()
        by_resource = conn.execute(
            "SELECT resource_type, COUNT(*) AS c FROM federated_acl_rules"
            " GROUP BY resource_type").fetchall()
        now = time.time()
        expired = conn.execute(
            "SELECT COUNT(*) AS c FROM federated_acl_rules"
            " WHERE expires_at > 0 AND expires_at < ?", (now,)).fetchone()["c"]
        deny_events = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'acl_deny'"
        ).fetchone()["c"]
        return {
            "total_rules": total,
            "by_effect": {r["effect"]: r["c"] for r in by_effect},
            "by_resource_type": {r["resource_type"]: r["c"] for r in by_resource},
            "expired_rules": expired,
            "deny_audit_events": deny_events,
        }
