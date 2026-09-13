"""
MindForge v5.4.2 共享记忆冲突解决

联邦/多 Agent 场景下，多个节点可能并发更新同一条共享记忆。
本模块提供持久化的冲突检测与解决：

- detect_incoming：传入更新指向本地已有记忆且内容不一致时，登记冲突
- resolve：按策略解决
  * lww        last-write-wins，按（版本, 时间戳, peer_id）决胜，新者覆盖
  * keep_both  传入内容另存分支记忆，并与本地记忆建立 conflict_branch 关联
- dismiss：人工判定无需处理时关闭冲突
- stats：冲突态势统计

与 federated.py 配套：FederatedMemory 可注入本解析器，accept_incoming
时自动检测并按策略解决冲突；未解决冲突保持 open 状态等待人工处理。
"""

import json
import time
import uuid
from typing import Any, Dict, List, Optional


# 冲突类型
CONFLICT_TYPES = ("content", "version", "concurrent")

# 冲突状态
CONFLICT_STATUS = ("open", "resolved", "dismissed", "archived")

# 解决策略
RESOLVE_STRATEGIES = ("lww", "keep_both")


class SharedConflictResolver:
    """共享记忆冲突解析器

    通过 SQLite 持久化冲突记录，随主库一起备份。
    关键变更写入审计日志（复用 storage._add_audit）。
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
            CREATE TABLE IF NOT EXISTS share_conflicts (
                id TEXT PRIMARY KEY,
                memory_key TEXT DEFAULT '',
                local_memory_id TEXT NOT NULL,
                incoming_peer TEXT DEFAULT '',
                conflict_type TEXT DEFAULT 'content',
                status TEXT DEFAULT 'open',
                resolution TEXT DEFAULT '',
                local_snapshot TEXT DEFAULT '{}',
                incoming_snapshot TEXT DEFAULT '{}',
                resolved_memory_id TEXT DEFAULT '',
                resolved_by TEXT DEFAULT '',
                resolved_at REAL DEFAULT 0.0,
                created_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_conflict_status
                ON share_conflicts(status);
            CREATE INDEX IF NOT EXISTS idx_conflict_local
                ON share_conflicts(local_memory_id);
            CREATE INDEX IF NOT EXISTS idx_conflict_created
                ON share_conflicts(created_at);
        """)
        conn.commit()

    def _row_to_conflict(self, row) -> Dict[str, Any]:
        def _loads(raw):
            try:
                return json.loads(raw) if raw else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {
            "conflict_id": row["id"],
            "memory_key": row["memory_key"],
            "local_memory_id": row["local_memory_id"],
            "incoming_peer": row["incoming_peer"],
            "conflict_type": row["conflict_type"],
            "status": row["status"],
            "resolution": row["resolution"],
            "local_snapshot": _loads(row["local_snapshot"]),
            "incoming_snapshot": _loads(row["incoming_snapshot"]),
            "resolved_memory_id": row["resolved_memory_id"],
            "resolved_by": row["resolved_by"],
            "resolved_at": row["resolved_at"],
            "created_at": row["created_at"],
        }

    # ===== 冲突检测 =====

    def detect_incoming(self, incoming: Dict[str, Any]) -> Dict[str, Any]:
        """检测传入更新是否与本地记忆冲突。

        Args:
            incoming: {
                "memory_id": 本地目标记忆 ID（必需），
                "content":   传入内容（必需），
                "version":   传入版本号（可选 int），
                "timestamp": 传入时间戳（可选 float），
                "from_peer": 来源节点（可选），
                "federated_key": 逻辑键（可选，仅记录用）,
            }

        Returns:
            {"conflict": False, "action": "new", ...}      本地无对应记忆
            {"conflict": False, "action": "noop", ...}     内容一致，无需处理
            {"conflict": True,  "conflict_id": ..., ...}   已登记冲突
        """
        memory_id = str(incoming.get("memory_id") or "").strip()
        content = incoming.get("content")
        from_peer = str(incoming.get("from_peer") or "").strip()
        if not memory_id or content is None:
            return {"conflict": False, "action": "new",
                    "reason": "缺少 memory_id 或 content，按新记忆处理"}

        local = self.storage.get_memory(memory_id)
        if local is None:
            return {"conflict": False, "action": "new",
                    "reason": "本地无对应记忆，按新记忆处理",
                    "memory_id": memory_id}

        if (local.content or "") == str(content):
            return {"conflict": False, "action": "noop",
                    "reason": "内容一致，无需处理",
                    "local_memory_id": memory_id}

        # 冲突类型：版本号不一致 → version；仅内容不一致 → content
        try:
            incoming_version = int(incoming.get("version", 0) or 0)
        except (TypeError, ValueError):
            incoming_version = 0
        local_version = int(getattr(local, "consolidation_count", 0) or 0)
        conflict_type = "version" if incoming_version != local_version else "content"

        now = time.time()
        conflict_id = f"cfl_{uuid.uuid4().hex[:12]}"
        local_snapshot = {
            "content_preview": (local.content or "")[:200],
            "version": local_version,
            "updated_at": getattr(local, "updated_at", 0.0),
            "category": local.category,
        }
        # v5.6.2 P0 修复：必须存完整 content，否则 LWW 冲突解决时
        # 用 200 字符 preview 覆盖完整内容 → 永久数据丢失（v5.4.4 #8 回归）。
        # 数据库膨胀风险 < 数据丢失风险，完整内容保留到冲突关闭后清理。
        _content_str = str(content)
        import hashlib as _hl
        incoming_snapshot = {
            "content": _content_str,
            "content_preview": _content_str[:200],
            "content_hash": _hl.sha256(_content_str.encode("utf-8")).hexdigest()[:16],
            "content_length": len(_content_str),
            "version": incoming_version,
            "timestamp": incoming.get("timestamp") or now,
            "from_peer": from_peer,
        }

        conn = self._conn()
        conn.execute(
            "INSERT INTO share_conflicts (id, memory_key, local_memory_id,"
            " incoming_peer, conflict_type, status, resolution,"
            " local_snapshot, incoming_snapshot, resolved_memory_id,"
            " resolved_by, resolved_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'open', '', ?, ?, '', '', 0.0, ?)",
            (conflict_id, str(incoming.get("federated_key") or memory_id)[:256],
             memory_id, from_peer, conflict_type,
             json.dumps(local_snapshot, ensure_ascii=False),
             json.dumps(incoming_snapshot, ensure_ascii=False), now))
        conn.commit()
        self.storage._add_audit("conflict_detected", memory_id, from_peer, "",
                                "INTERNAL",
                                {"conflict_id": conflict_id,
                                 "conflict_type": conflict_type})
        return {"conflict": True, "conflict_id": conflict_id,
                "conflict_type": conflict_type, "local_memory_id": memory_id}

    # ===== 冲突解决 =====

    def resolve(self, conflict_id: str, strategy: str,
                actor: str = "") -> Dict[str, Any]:
        """解决一个 open 状态的冲突。

        Args:
            conflict_id: 冲突 ID
            strategy: lww / keep_both
            actor: 操作者
        """
        strategy = (strategy or "").strip().lower()
        if strategy not in RESOLVE_STRATEGIES:
            return {"success": False,
                    "error": f"无效策略: {strategy}（可选: {'/'.join(RESOLVE_STRATEGIES)}）"}
        conflict_id = (conflict_id or "").strip()
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM share_conflicts WHERE id = ?", (conflict_id,)).fetchone()
        if row is None:
            return {"success": False, "error": "冲突不存在"}
        if row["status"] != "open":
            return {"success": False,
                    "error": f"冲突已处于 {row['status']} 状态，不能重复解决"}

        local_id = row["local_memory_id"]
        local = self.storage.get_memory(local_id)
        if local is None:
            # 本地记忆已被删除：直接关闭冲突
            self._mark(conflict_id, "resolved", f"{strategy}_local_deleted",
                       "", actor)
            return {"success": True, "conflict_id": conflict_id,
                    "resolution": f"{strategy}_local_deleted",
                    "resolved_memory_id": None,
                    "note": "本地记忆已不存在，冲突自动关闭"}

        try:
            incoming_snapshot = json.loads(row["incoming_snapshot"] or "{}")
        except json.JSONDecodeError:
            incoming_snapshot = {}
        # v5.6.2 P0 修复：从完整 content 字段读取，而非 200 字符 preview
        # （v5.4.4 #8 引入的回归：用 preview 覆盖完整内容导致永久数据丢失）
        incoming_content = incoming_snapshot.get("content", "")
        if not incoming_content:
            # 数据完整性修复（N1）：v5.6.2 之前的冲突行只存了 200 字符
            # content_preview，没有完整正文。原实现会回退到 preview 并继续
            # 走 LWW 覆盖分支 —— 等于用截断文本覆盖本地完整内容，永久丢数据，
            # v5.6.2 的 P0 修复在旧数据路径上被完全绕过。
            # 这里改为 fail-safe：既不覆盖本地，也不据此伪造一条截断的分支记忆，
            # 保持冲突 open 交由人工/重新同步处理。
            return {
                "success": False,
                "error": (
                    "冲突快照缺少完整 content（仅有 200 字符 content_preview，"
                    "属于 v5.6.2 之前的旧数据），为防止永久数据丢失已拒绝自动解决。"
                    "请让对端重新同步该记忆以生成新冲突，或人工核对后再处理。"
                ),
                "conflict_id": conflict_id,
                "legacy_snapshot": True,
            }
        incoming_content = str(incoming_content)
        incoming_version = int(incoming_snapshot.get("version", 0) or 0)
        incoming_ts = float(incoming_snapshot.get("timestamp", 0.0) or 0.0)
        from_peer = str(incoming_snapshot.get("from_peer", "") or row["incoming_peer"])

        now = time.time()
        if strategy == "lww":
            local_version = int(getattr(local, "consolidation_count", 0) or 0)
            local_ts = float(getattr(local, "updated_at", 0.0) or 0.0)
            incoming_key = (incoming_version, incoming_ts, from_peer)
            local_key = (local_version, local_ts, "")
            if incoming_key > local_key:
                # 传入方更新：先备份本地旧版本，再覆盖内容
                try:
                    self.storage.save_version(
                        local_id, local.content or "", local.category,
                        list(local.tags or []), local.importance, actor="conflict-lww")
                except Exception:
                    pass
                # v5.4.2 修复：检查 update_memory 返回值，失败时回退到 keep_both
                updated = self.storage.update_memory(local_id, content=incoming_content,
                                                     actor=actor or from_peer)
                if not updated:
                    # 更新失败（如记忆已被并发删除），回退到 keep_both 策略
                    try:
                        new_entry = self.storage.add_memory(
                            content=incoming_content,
                            category=local.category or "federated",
                            tags=["conflict:lww_fallback", f"from:{from_peer}"] if from_peer
                                 else ["conflict:lww_fallback"],
                            source_agent=f"federated:{from_peer}" if from_peer else "conflict",
                            metadata={
                                "conflict_parent": local_id,
                                "conflict_id": conflict_id,
                                "fallback": "update_failed",
                            })
                        resolved_memory_id = new_entry.id
                    except (ValueError, TypeError):
                        return {"success": False, "error": "LWW 更新失败且回退分支创建失败"}
                    resolution = "lww_incoming_fallback"
                else:
                    resolution = "lww_incoming"
                    resolved_memory_id = local_id
            else:
                resolution = "lww_local"
                resolved_memory_id = local_id
        else:  # keep_both
            try:
                new_entry = self.storage.add_memory(
                    content=incoming_content,
                    category=local.category or "federated",
                    tags=["conflict:branch", f"from:{from_peer}"] if from_peer
                         else ["conflict:branch"],
                    source_agent=f"federated:{from_peer}" if from_peer else "conflict",
                    metadata={
                        "conflict_parent": local_id,
                        "conflict_id": conflict_id,
                        "incoming_version": incoming_version,
                    })
            except (ValueError, TypeError) as e:
                return {"success": False, "error": f"分支记忆创建失败: {e}"}
            resolved_memory_id = new_entry.id
            try:
                self.storage.link_memories(local_id, new_entry.id,
                                           link_type="conflict_branch",
                                           note=f"v5.4.2 冲突分支（{conflict_id}）")
            except Exception:
                pass
            resolution = "keep_both"

        self._mark(conflict_id, "resolved", resolution, resolved_memory_id, actor)
        self.storage._add_audit("conflict_resolved", local_id, actor or from_peer,
                                "", "INTERNAL",
                                {"conflict_id": conflict_id,
                                 "strategy": strategy, "resolution": resolution})
        return {"success": True, "conflict_id": conflict_id,
                "strategy": strategy, "resolution": resolution,
                "resolved_memory_id": resolved_memory_id}

    def _mark(self, conflict_id: str, status: str, resolution: str,
              resolved_memory_id: str, actor: str):
        conn = self._conn()
        # N2 修复（无界增长）：一旦冲突不再 open，就把 incoming_snapshot 里的
        # 完整正文压缩成「哈希 + 长度」摘要。该字段保存的是整条记忆正文，
        # 原先从不清理，每个冲突都会在 share_conflicts 里永久留一份全文副本，
        # 长期运行（月级无人值守）会导致数据库单表无界膨胀。
        compact_snapshot = None
        if status != "open":
            row = conn.execute(
                "SELECT incoming_snapshot FROM share_conflicts WHERE id = ?",
                (conflict_id,)).fetchone()
            if row is not None and row["incoming_snapshot"] not in (None, "", "{}"):
                try:
                    payload = json.loads(row["incoming_snapshot"])
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                content = str(payload.get("content", "") or "")
                if content:
                    import hashlib as _hl
                    compact_snapshot = json.dumps({
                        "purged": True,
                        "reason": "conflict_closed",
                        "content_hash": _hl.sha256(
                            content.encode("utf-8")).hexdigest()[:16],
                        "content_length": len(content),
                        "preview": content[:200],
                        "version": payload.get("version", 0),
                        "from_peer": payload.get("from_peer", ""),
                    }, ensure_ascii=False)
                else:
                    compact_snapshot = "{}"

        if compact_snapshot is None:
            conn.execute(
                "UPDATE share_conflicts SET status = ?, resolution = ?,"
                " resolved_memory_id = ?, resolved_by = ?, resolved_at = ?"
                " WHERE id = ?",
                (status, resolution, resolved_memory_id or "",
                 (actor or "")[:128], time.time(), conflict_id))
        else:
            conn.execute(
                "UPDATE share_conflicts SET status = ?, resolution = ?,"
                " resolved_memory_id = ?, resolved_by = ?, resolved_at = ?,"
                " incoming_snapshot = ?"
                " WHERE id = ?",
                (status, resolution, resolved_memory_id or "",
                 (actor or "")[:128], time.time(), compact_snapshot,
                 conflict_id))
        conn.commit()

    # ===== 查询与维护 =====

    def list_conflicts(self, status: Optional[str] = None,
                       limit: int = 50) -> List[Dict[str, Any]]:
        """列出冲突记录"""
        limit = max(1, min(500, int(limit)))
        conn = self._conn()
        if status:
            status = status.strip().lower()
            if status not in CONFLICT_STATUS:
                return []
            rows = conn.execute(
                "SELECT * FROM share_conflicts WHERE status = ?"
                " ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM share_conflicts"
                " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_conflict(r) for r in rows]

    def dismiss(self, conflict_id: str, actor: str = "") -> Dict[str, Any]:
        """关闭冲突（人工判定无需处理）"""
        conflict_id = (conflict_id or "").strip()
        conn = self._conn()
        row = conn.execute(
            "SELECT status FROM share_conflicts WHERE id = ?",
            (conflict_id,)).fetchone()
        if row is None:
            return {"success": False, "error": "冲突不存在"}
        if row["status"] != "open":
            return {"success": False,
                    "error": f"冲突已处于 {row['status']} 状态"}
        self._mark(conflict_id, "dismissed", "dismissed", "", actor)
        self.storage._add_audit("conflict_dismissed", conflict_id, actor, "",
                                "INTERNAL", {})
        return {"success": True, "conflict_id": conflict_id,
                "status": "dismissed"}

    def stats(self) -> Dict[str, Any]:
        """冲突态势统计"""
        conn = self._conn()
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM share_conflicts").fetchone()["c"]
        by_status = conn.execute(
            "SELECT status, COUNT(*) AS c FROM share_conflicts"
            " GROUP BY status").fetchall()
        by_type = conn.execute(
            "SELECT conflict_type, COUNT(*) AS c FROM share_conflicts"
            " GROUP BY conflict_type").fetchall()
        by_resolution = conn.execute(
            "SELECT resolution, COUNT(*) AS c FROM share_conflicts"
            " WHERE status = 'resolved' GROUP BY resolution").fetchall()
        status_map = {r["status"]: r["c"] for r in by_status}
        return {
            "total_conflicts": total,
            "open": status_map.get("open", 0),
            "resolved": status_map.get("resolved", 0),
            "dismissed": status_map.get("dismissed", 0),
            "by_type": {r["conflict_type"]: r["c"] for r in by_type},
            "by_resolution": {r["resolution"]: r["c"] for r in by_resolution},
        }

    def cleanup_branches(self, days_old: int = 30,
                         dry_run: bool = True,
                         actor: str = "") -> Dict[str, Any]:
        """v5.4.4 新增 #9：清理 keep_both 策略产生的旧分支记忆。

        长期运行后 keep_both 会堆积大量分支记忆，本方法可定期清理：
        - 查找 resolved 状态、resolution 为 keep_both 且超过 days_old 天的冲突
        - dry_run=True 时仅返回统计，不实际删除
        - dry_run=False 时删除分支记忆和 conflict_branch 关联，并将冲突标记为 archived

        Args:
            days_old: 清理多少天前的已解决冲突（默认 30 天）
            dry_run: True 时仅统计不删除，False 时实际执行清理
            actor: 操作者标识

        Returns:
            {"total_found": int, "cleaned": int, "freed_memory_ids": [...], ...}
        """
        days_old = max(1, min(3650, int(days_old)))
        now = time.time()
        cutoff = now - days_old * 86400

        conn = self._conn()
        rows = conn.execute(
            "SELECT id, local_memory_id, resolved_memory_id, resolution, created_at"
            " FROM share_conflicts"
            " WHERE status = 'resolved' AND resolution = 'keep_both'"
            " AND resolved_at > 0 AND resolved_at < ?"
            " ORDER BY resolved_at ASC",
            (cutoff,)).fetchall()

        result = {
            "total_found": len(rows),
            "cleaned": 0,
            "freed_memory_ids": [],
            "archived_conflicts": [],
            "dry_run": dry_run,
            "days_old": days_old,
        }

        if dry_run or not rows:
            return result

        for row in rows:
            branch_id = row["resolved_memory_id"]
            conflict_id = row["id"]
            if branch_id:
                try:
                    branch = self.storage.get_memory(branch_id)
                    if branch is not None:
                        self.storage.delete_memory(branch_id)
                        result["freed_memory_ids"].append(branch_id)
                except Exception:
                    pass
            try:
                conn.execute(
                    "DELETE FROM memory_links"
                    " WHERE ((source_id = ? AND target_id = ?)"
                    " OR (source_id = ? AND target_id = ?))"
                    " AND link_type = 'conflict_branch'",
                    (row["local_memory_id"], branch_id,
                     branch_id, row["local_memory_id"]))
            except Exception:
                pass
            conn.execute(
                "UPDATE share_conflicts SET status = 'archived'"
                " WHERE id = ?",
                (conflict_id,))
            result["archived_conflicts"].append(conflict_id)
            result["cleaned"] += 1

        conn.commit()
        self.storage._add_audit("conflict_cleanup_branches", "", actor, "",
                                "INTERNAL",
                                {"cleaned": result["cleaned"],
                                 "days_old": days_old})
        return result
