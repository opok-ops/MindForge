"""
MindForge 多 Agent 记忆空间（实验性 — v6.0.0 全量推送预览）

多 Agent 共享记忆空间：权限隔离、角色协作、冲突解决。

EXPERIMENTAL: 本模块为 v6.0.0 前瞻预览，API 在全量发布前可能发生变化。
与联邦记忆（federated.py，跨节点对等共享）不同，本模块面向同一本地库内的
多 Agent 协作场景：多个 Agent 在共享空间中交换记忆，并按角色隔离权限。

核心概念：
- Space（记忆空间）：命名的共享容器，归属某个 owner Agent
- Member（成员）：加入空间的 Agent，角色为 owner / editor / reader
- Item（空间条目）：被共享进空间的记忆引用，带版本号（冲突时 last-write-wins）

隐私护栏：PRIVATE / STRICT 级别的记忆永远禁止进入共享空间。
"""

import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional


class SpaceRole(Enum):
    """空间成员角色"""
    OWNER = "owner"
    EDITOR = "editor"
    READER = "reader"


_ROLE_RANK = {
    SpaceRole.OWNER: 3,
    SpaceRole.EDITOR: 2,
    SpaceRole.READER: 1,
}

# 允许共享的隐私级别（PRIVATE / STRICT 禁止共享）
_SHAREABLE_PRIVACY = {"PUBLIC", "INTERNAL"}

# 空间策略
SPACE_POLICIES = ("shared", "broadcast")


@dataclass
class AgentSpace:
    """记忆空间"""
    space_id: str
    name: str
    owner_agent: str
    description: str = ""
    policy: str = "shared"  # shared=协作读写 / broadcast=仅 owner 可写
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "space_id": self.space_id,
            "name": self.name,
            "owner_agent": self.owner_agent,
            "description": self.description,
            "policy": self.policy,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class SpaceMember:
    """空间成员"""
    space_id: str
    agent_id: str
    role: SpaceRole = SpaceRole.READER
    joined_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "space_id": self.space_id,
            "agent_id": self.agent_id,
            "role": self.role.value,
            "joined_at": self.joined_at,
        }


class MultiAgentMemoryManager:
    """多 Agent 记忆空间管理器（实验性）

    通过 SQLite 持久化空间/成员/条目，随主库一起备份。
    所有变更操作都会写入审计日志（复用 storage._add_audit）。
    """

    EXPERIMENTAL = True
    TARGET_VERSION = "6.0.0"

    def __init__(self, storage):
        self.storage = storage
        self._init_tables()

    # ===== 内部基础设施 =====

    def _conn(self):
        return self.storage._get_conn()

    def _init_tables(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_spaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                owner_agent TEXT NOT NULL,
                policy TEXT DEFAULT 'shared',
                created_at REAL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS agent_space_members (
                space_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                role TEXT DEFAULT 'reader',
                joined_at REAL,
                PRIMARY KEY (space_id, agent_id),
                FOREIGN KEY (space_id) REFERENCES agent_spaces(id)
            );
            CREATE INDEX IF NOT EXISTS idx_space_members_agent
                ON agent_space_members(agent_id);

            CREATE TABLE IF NOT EXISTS agent_space_items (
                space_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                added_by TEXT DEFAULT '',
                added_at REAL,
                version INTEGER DEFAULT 1,
                PRIMARY KEY (space_id, memory_id),
                FOREIGN KEY (space_id) REFERENCES agent_spaces(id),
                FOREIGN KEY (memory_id) REFERENCES memories(id)
            );
            CREATE INDEX IF NOT EXISTS idx_space_items_memory
                ON agent_space_items(memory_id);
        """)
        conn.commit()

    def _row_to_space(self, row) -> AgentSpace:
        return AgentSpace(
            space_id=row["id"],
            name=row["name"],
            owner_agent=row["owner_agent"],
            description=row["description"] or "",
            policy=row["policy"] or "shared",
            created_at=row["created_at"] or 0.0,
            updated_at=row["updated_at"] or 0.0,
        )

    def _resolve_space(self, space_ref: str) -> Optional[AgentSpace]:
        """按 ID 或名称解析空间"""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM agent_spaces WHERE id = ?", (space_ref,)
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM agent_spaces WHERE name = ?", (space_ref,)
            ).fetchone()
        return self._row_to_space(row) if row else None

    def _get_role(self, space_id: str, agent_id: str) -> Optional[SpaceRole]:
        conn = self._conn()
        row = conn.execute(
            "SELECT role FROM agent_space_members WHERE space_id = ? AND agent_id = ?",
            (space_id, agent_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return SpaceRole(row["role"])
        except ValueError:
            return SpaceRole.READER

    def check_permission(self, space_id: str, agent_id: str,
                         required: SpaceRole = SpaceRole.READER) -> bool:
        """检查 Agent 在空间中是否具备所需角色（含）以上权限"""
        role = self._get_role(space_id, agent_id)
        if role is None:
            return False
        return _ROLE_RANK[role] >= _ROLE_RANK[required]

    # ===== 空间管理 =====

    def create_space(self, name: str, owner_agent: str,
                     description: str = "", policy: str = "shared") -> Dict[str, Any]:
        """创建记忆空间，创建者自动成为 owner 成员"""
        name = (name or "").strip()
        if not name:
            return {"success": False, "error": "空间名称不能为空"}
        if len(name) > 64:
            return {"success": False, "error": "空间名称不能超过 64 字符"}
        if not owner_agent:
            return {"success": False, "error": "必须指定 owner Agent（--agent）"}
        if policy not in SPACE_POLICIES:
            return {"success": False,
                    "error": f"无效的空间策略: {policy}（可选: {'/'.join(SPACE_POLICIES)}）"}

        conn = self._conn()
        if conn.execute("SELECT 1 FROM agent_spaces WHERE name = ?",
                        (name,)).fetchone():
            return {"success": False, "error": f"空间名称已存在: {name}"}

        now = time.time()
        space_id = f"space_{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO agent_spaces (id, name, description, owner_agent, policy,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (space_id, name, (description or "").strip(), owner_agent, policy, now, now),
        )
        conn.execute(
            "INSERT INTO agent_space_members (space_id, agent_id, role, joined_at)"
            " VALUES (?, ?, ?, ?)",
            (space_id, owner_agent, SpaceRole.OWNER.value, now),
        )
        conn.commit()
        self.storage._add_audit("space_create", space_id, owner_agent, "",
                                "INTERNAL", {"name": name, "policy": policy})
        return {"success": True, "space_id": space_id, "name": name,
                "owner_agent": owner_agent, "policy": policy}

    def get_space(self, space_ref: str) -> Optional[Dict[str, Any]]:
        """获取空间详情（含成员数与条目数）"""
        space = self._resolve_space(space_ref)
        if space is None:
            return None
        conn = self._conn()
        members = conn.execute(
            "SELECT COUNT(*) AS c FROM agent_space_members WHERE space_id = ?",
            (space.space_id,)).fetchone()["c"]
        items = conn.execute(
            "SELECT COUNT(*) AS c FROM agent_space_items WHERE space_id = ?",
            (space.space_id,)).fetchone()["c"]
        data = space.to_dict()
        data["member_count"] = members
        data["item_count"] = items
        return data

    def list_spaces(self, agent_id: str = "") -> List[Dict[str, Any]]:
        """列出空间；指定 agent_id 时仅列出其加入的空间"""
        conn = self._conn()
        if agent_id:
            rows = conn.execute(
                "SELECT s.* FROM agent_spaces s"
                " JOIN agent_space_members m ON m.space_id = s.id"
                " WHERE m.agent_id = ? ORDER BY s.created_at DESC",
                (agent_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_spaces ORDER BY created_at DESC").fetchall()
        result = []
        for row in rows:
            space = self._row_to_space(row)
            info = self.get_space(space.space_id)
            if agent_id:
                role = self._get_role(space.space_id, agent_id)
                info["my_role"] = role.value if role else None
            result.append(info)
        return result

    def delete_space(self, space_ref: str, actor: str) -> Dict[str, Any]:
        """删除空间（仅 owner）；空间内条目仅移除引用，不删除原始记忆"""
        space = self._resolve_space(space_ref)
        if space is None:
            return {"success": False, "error": "空间不存在"}
        if space.owner_agent != actor:
            return {"success": False, "error": "仅空间 owner 可以删除空间"}
        conn = self._conn()
        conn.execute("DELETE FROM agent_space_items WHERE space_id = ?",
                     (space.space_id,))
        conn.execute("DELETE FROM agent_space_members WHERE space_id = ?",
                     (space.space_id,))
        conn.execute("DELETE FROM agent_spaces WHERE id = ?", (space.space_id,))
        conn.commit()
        self.storage._add_audit("space_delete", space.space_id, actor, "",
                                "INTERNAL", {"name": space.name})
        return {"success": True, "space_id": space.space_id, "name": space.name}

    # ===== 成员管理 =====

    def add_member(self, space_ref: str, agent_id: str,
                   role: str = "reader", actor: str = "") -> Dict[str, Any]:
        """添加成员。

        - owner 可添加任意角色成员；
        - 非 owner 只能以 reader 身份加入自己（self-join）。
        """
        space = self._resolve_space(space_ref)
        if space is None:
            return {"success": False, "error": "空间不存在"}
        agent_id = (agent_id or "").strip()
        if not agent_id:
            return {"success": False, "error": "Agent ID 不能为空"}
        try:
            role_enum = SpaceRole(role)
        except ValueError:
            return {"success": False,
                    "error": f"无效角色: {role}（可选: owner/editor/reader）"}

        is_owner_action = (actor == space.owner_agent)
        if not is_owner_action:
            # 非 owner：仅允许 self-join 为 reader
            if agent_id != actor:
                return {"success": False, "error": "仅 owner 可以添加其他成员"}
            if role_enum != SpaceRole.READER:
                return {"success": False, "error": "自助加入仅能使用 reader 角色"}
            if role_enum == SpaceRole.OWNER:
                return {"success": False, "error": "不能添加 owner 角色成员"}
        elif role_enum == SpaceRole.OWNER:
            return {"success": False, "error": "暂不支持转移 owner（v6.0.0 提供）"}

        conn = self._conn()
        existing = self._get_role(space.space_id, agent_id)
        if existing is not None:
            if not is_owner_action:
                return {"success": False, "error": f"{agent_id} 已在空间中"}
            # owner 调整成员角色
            conn.execute(
                "UPDATE agent_space_members SET role = ?"
                " WHERE space_id = ? AND agent_id = ?",
                (role_enum.value, space.space_id, agent_id))
            conn.commit()
            return {"success": True, "space_id": space.space_id,
                    "agent_id": agent_id, "role": role_enum.value,
                    "updated": True}

        conn.execute(
            "INSERT INTO agent_space_members (space_id, agent_id, role, joined_at)"
            " VALUES (?, ?, ?, ?)",
            (space.space_id, agent_id, role_enum.value, time.time()))
        conn.commit()
        self.storage._add_audit("space_join", space.space_id, actor or agent_id, "",
                                "INTERNAL", {"agent_id": agent_id, "role": role_enum.value})
        return {"success": True, "space_id": space.space_id,
                "agent_id": agent_id, "role": role_enum.value, "updated": False}

    def remove_member(self, space_ref: str, agent_id: str,
                      actor: str) -> Dict[str, Any]:
        """移除成员（owner 操作，或成员自行退出）；owner 不可被移除"""
        space = self._resolve_space(space_ref)
        if space is None:
            return {"success": False, "error": "空间不存在"}
        if agent_id == space.owner_agent:
            return {"success": False, "error": "不能移除空间 owner"}
        is_owner_action = (actor == space.owner_agent)
        if not is_owner_action and agent_id != actor:
            return {"success": False, "error": "仅 owner 可以移除其他成员"}
        conn = self._conn()
        cur = conn.execute(
            "DELETE FROM agent_space_members WHERE space_id = ? AND agent_id = ?",
            (space.space_id, agent_id))
        conn.commit()
        if cur.rowcount == 0:
            return {"success": False, "error": f"{agent_id} 不在空间中"}
        self.storage._add_audit("space_leave", space.space_id, actor, "",
                                "INTERNAL", {"agent_id": agent_id})
        return {"success": True, "space_id": space.space_id, "agent_id": agent_id}

    def list_members(self, space_ref: str) -> List[Dict[str, Any]]:
        """列出空间成员"""
        space = self._resolve_space(space_ref)
        if space is None:
            return []
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM agent_space_members WHERE space_id = ?"
            " ORDER BY joined_at ASC", (space.space_id,)).fetchall()
        members = []
        for row in rows:
            try:
                role = SpaceRole(row["role"])
            except ValueError:
                role = SpaceRole.READER
            members.append(SpaceMember(
                space_id=row["space_id"], agent_id=row["agent_id"],
                role=role, joined_at=row["joined_at"] or 0.0,
            ).to_dict())
        return members

    # ===== 记忆共享 =====

    def share_memory(self, space_ref: str, memory_id: str,
                     actor: str) -> Dict[str, Any]:
        """共享记忆到空间。

        权限：editor 及以上（broadcast 策略下仅 owner）。
        护栏：PRIVATE / STRICT 记忆禁止共享；回收站记忆禁止共享。
        冲突：重复共享同一条记忆 = last-write-wins，条目版本号 +1。
        """
        space = self._resolve_space(space_ref)
        if space is None:
            return {"success": False, "error": "空间不存在"}

        required = SpaceRole.OWNER if space.policy == "broadcast" else SpaceRole.EDITOR
        if not self.check_permission(space.space_id, actor, required):
            return {"success": False,
                    "error": f"权限不足：{space.policy} 策略需要 {required.value} 角色"}

        entry = self.storage.get_memory(memory_id)
        if entry is None:
            return {"success": False, "error": "记忆不存在"}
        if entry.category == "trash":
            return {"success": False, "error": "回收站中的记忆不能共享"}
        if entry.privacy.value not in _SHAREABLE_PRIVACY:
            return {"success": False,
                    "error": f"隐私护栏：{entry.privacy.value} 级别记忆禁止进入共享空间"}

        conn = self._conn()
        existing = conn.execute(
            "SELECT version FROM agent_space_items"
            " WHERE space_id = ? AND memory_id = ?",
            (space.space_id, memory_id)).fetchone()

        now = time.time()
        if existing:
            # 冲突解决：last-write-wins，版本号递增
            new_version = existing["version"] + 1
            conn.execute(
                "UPDATE agent_space_items SET added_by = ?, added_at = ?, version = ?"
                " WHERE space_id = ? AND memory_id = ?",
                (actor, now, new_version, space.space_id, memory_id))
            conn.commit()
            return {"success": True, "space_id": space.space_id,
                    "memory_id": memory_id, "version": new_version,
                    "conflict_resolved": "last-write-wins"}

        conn.execute(
            "INSERT INTO agent_space_items (space_id, memory_id, added_by, added_at,"
            " version) VALUES (?, ?, ?, ?, 1)",
            (space.space_id, memory_id, actor, now))
        conn.commit()
        self.storage._add_audit("space_share", memory_id, actor, "",
                                entry.privacy.value, {"space_id": space.space_id})
        return {"success": True, "space_id": space.space_id,
                "memory_id": memory_id, "version": 1,
                "conflict_resolved": None}

    def unshare_memory(self, space_ref: str, memory_id: str,
                       actor: str) -> Dict[str, Any]:
        """从空间移除记忆引用（不删除原始记忆）"""
        space = self._resolve_space(space_ref)
        if space is None:
            return {"success": False, "error": "空间不存在"}
        if not self.check_permission(space.space_id, actor, SpaceRole.EDITOR):
            return {"success": False, "error": "权限不足：需要 editor 及以上角色"}
        conn = self._conn()
        cur = conn.execute(
            "DELETE FROM agent_space_items WHERE space_id = ? AND memory_id = ?",
            (space.space_id, memory_id))
        conn.commit()
        if cur.rowcount == 0:
            return {"success": False, "error": "该记忆不在空间中"}
        return {"success": True, "space_id": space.space_id, "memory_id": memory_id}

    def list_space_memories(self, space_ref: str, actor: str,
                            limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """列出空间中的记忆（reader 及以上可读）"""
        space = self._resolve_space(space_ref)
        if space is None:
            return {"success": False, "error": "空间不存在", "items": []}
        if not self.check_permission(space.space_id, actor, SpaceRole.READER):
            return {"success": False, "error": "权限不足：仅空间成员可读取",
                    "items": []}
        limit = max(1, min(200, int(limit)))
        conn = self._conn()
        rows = conn.execute(
            "SELECT i.memory_id, i.added_by, i.added_at, i.version"
            " FROM agent_space_items i"
            " WHERE i.space_id = ? ORDER BY i.added_at DESC LIMIT ? OFFSET ?",
            (space.space_id, limit, offset)).fetchall()
        items = []
        for row in rows:
            entry = self.storage.get_memory(row["memory_id"])
            if entry is None:
                continue  # 原始记忆已被删除
            items.append({
                "memory_id": row["memory_id"],
                "content_preview": (entry.content or "")[:80],
                "category": entry.category,
                "tags": entry.tags,
                "privacy": entry.privacy.value,
                "source_agent": entry.source_agent,
                "added_by": row["added_by"],
                "added_at": row["added_at"],
                "version": row["version"],
            })
        return {"success": True, "space_id": space.space_id,
                "space_name": space.name, "items": items, "count": len(items)}

    # ===== 统计 =====

    def space_stats(self, space_ref: str = "") -> Dict[str, Any]:
        """空间统计；不指定空间时返回全局概览"""
        conn = self._conn()
        if space_ref:
            space = self._resolve_space(space_ref)
            if space is None:
                return {"success": False, "error": "空间不存在"}
            by_role = conn.execute(
                "SELECT role, COUNT(*) AS c FROM agent_space_members"
                " WHERE space_id = ? GROUP BY role", (space.space_id,)).fetchall()
            top_contributors = conn.execute(
                "SELECT added_by, COUNT(*) AS c FROM agent_space_items"
                " WHERE space_id = ? GROUP BY added_by ORDER BY c DESC LIMIT 5",
                (space.space_id,)).fetchall()
            return {
                "success": True,
                "space": self.get_space(space.space_id),
                "members_by_role": {r["role"]: r["c"] for r in by_role},
                "top_contributors": [
                    {"agent_id": r["added_by"], "shared": r["c"]}
                    for r in top_contributors
                ],
            }
        spaces = conn.execute("SELECT COUNT(*) AS c FROM agent_spaces").fetchone()["c"]
        members = conn.execute(
            "SELECT COUNT(*) AS c FROM agent_space_members").fetchone()["c"]
        items = conn.execute(
            "SELECT COUNT(*) AS c FROM agent_space_items").fetchone()["c"]
        agents = conn.execute(
            "SELECT COUNT(DISTINCT agent_id) AS c FROM agent_space_members"
        ).fetchone()["c"]
        return {
            "success": True,
            "total_spaces": spaces,
            "total_memberships": members,
            "total_shared_items": items,
            "participating_agents": agents,
            "experimental": True,
            "target_version": self.TARGET_VERSION,
        }
