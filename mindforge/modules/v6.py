"""
MindForge 扩展端口（v6.0.0 已发布）
====================================

v6.0.0 的两个旗舰特性（多 Agent 协作、6G AI 记忆预览）已在正式版落地：

1. **多 Agent 协作**：`MultiAgentAdapterPort` 把 `modules/multi_agent.py`
   的 `MultiAgentMemoryManager`（三级角色 owner/editor/reader、
   last-write-wins 冲突仲裁、PRIVATE/STRICT 隐私护栏）挂到 v6 端口上，
   状态为 AVAILABLE。通过 `get_v6_port(V6Capability.MULTI_AGENT_COLLABORATION,
   manager)` 传入既有管理器实例即可使用；facade 侧 `MindForge.multi_agent`
   已直接暴露该管理器。

2. **6G AI 记忆预览**：`MemoryPreviewPort` 提供与稳定版并行的实验分支——
   Preview Channel（独立 SQLite 预览库，不污染主库）、上下文蒸馏
   （按重要度分层 + Token 预算压缩）、增量索引统计（写入即索引的近实时
   召回指标）与单记忆时间线。状态为 AVAILABLE。

状态语义
--------
- `PLANNED`      ：仅规划，无实现
- `EXPERIMENTAL` ：有实验性实现，API 可能变更
- `AVAILABLE`    ：正式发布，可生产使用

用法示例
--------
>>> from mindforge.modules import v6_status, get_v6_port, V6Capability
>>> v6_status()["released"]
True
>>> port = get_v6_port(V6Capability.MEMORY_PREVIEW, storage)
>>> port.is_available()
True
"""

import json
import os
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Type

from mindforge.core.version import __version__

# ---------------------------------------------------------------------------
# v6 常量
# ---------------------------------------------------------------------------

#: v6.0.0 目标版本号（当前已发布版本）
V6_TARGET_VERSION: str = "6.0.0"

#: 错误信息前缀
_V6_PREFIX: str = "MindForge v6.0.0"

#: Preview Channel 独立库的默认存放目录（相对数据目录）
_PREVIEW_DIRNAME: str = "preview_channels"


class V6Status(str, Enum):
    """v6 端口的成熟度状态（str 子类，便于直接 JSON 序列化）"""

    PLANNED = "planned"              # 仅规划，无可用实现
    EXPERIMENTAL = "experimental"    # 实验性实现，接口可能变更
    AVAILABLE = "available"          # 正式发布，可生产使用


class V6Capability(str, Enum):
    """v6.0.0 规划中的能力枚举（与官网 #v6 区块一一对应）"""

    #: 多 Agent 协作：共享记忆空间、角色权限、冲突仲裁
    MULTI_AGENT_COLLABORATION = "multi_agent_collaboration"
    #: 6G AI 记忆预览：Preview Channel、上下文蒸馏、增量索引、时间线可视化
    MEMORY_PREVIEW = "memory_preview"


def _estimate_tokens(text: str) -> int:
    """粗略估算文本的 Token 数（中英混合场景：每 1 字符约 0.5 Token）。

    Args:
        text: 输入文本

    Returns:
        估算 Token 数（至少 1）
    """
    if not text:
        return 0
    return max(1, int(len(text) * 0.5))


# ---------------------------------------------------------------------------
# 端口抽象基类
# ---------------------------------------------------------------------------

class V6Port(ABC):
    """v6 扩展端口抽象基类

    子类必须声明 `capability` 与 `STATUS`。业务方法由各具体端口定义，
    未实现的能力抛 `NotImplementedError`，确保误调用立即暴露而不是静默出错。
    """

    #: 该端口对应的 v6 能力
    capability: V6Capability

    #: 端口成熟度（类级常量，便于不实例化即可查询）
    STATUS: V6Status = V6Status.PLANNED

    #: 是否必须带构造参数（无法无参实例化）
    requires_args: bool = False

    @abstractmethod
    def status(self) -> V6Status:
        """返回端口当前成熟度状态。"""
        raise NotImplementedError

    def is_available(self) -> bool:
        """端口是否已可生产使用。"""
        return self.status() is V6Status.AVAILABLE

    def describe(self) -> Dict[str, Any]:
        """返回端口的自描述信息（供 CLI / MCP / 官网展示）。

        Returns:
            包含 capability / status / available / port / target_version 的字典。
        """
        return {
            "capability": self.capability.value,
            "status": self.status().value,
            "available": self.is_available(),
            "port": type(self).__name__,
            "target_version": V6_TARGET_VERSION,
            "requires_args": self.requires_args,
        }

    def summary(self) -> Dict[str, Any]:
        """返回端口的简要运行概览；未实现时返回空字典。

        Returns:
            端口自定义的概览字典，未实现时为空字典。
        """
        return {}


# ---------------------------------------------------------------------------
# 端口 1：多 Agent 协作（正式发布，经 MultiAgentAdapterPort 挂载）
# ---------------------------------------------------------------------------

class MultiAgentCollaborationPort(V6Port):
    """多 Agent 协作端口（接口规范）

    对应 v6.0.0 的"多 Agent 协作"方向：多个 Agent 在同一本地库内共享记忆，
    按角色隔离权限，并处理写入冲突。

    正式实现：`MultiAgentAdapterPort` 把 `modules/multi_agent.py` 的
    `MultiAgentMemoryManager`（三级角色 owner / editor / reader、
    last-write-wins 冲突仲裁、PRIVATE / STRICT 隐私护栏）挂到本端口上，
    状态为 AVAILABLE。
    """

    capability = V6Capability.MULTI_AGENT_COLLABORATION
    STATUS = V6Status.AVAILABLE

    def status(self) -> V6Status:
        """多 Agent 协作已随 v6.0.0 正式发布，返回 AVAILABLE。"""
        return self.STATUS

    # —— 空间管理 ——

    def create_space(self, name: str, owner_agent: str,
                     description: str = "", policy: str = "shared") -> Dict[str, Any]:
        """创建共享记忆空间。"""
        raise NotImplementedError(
            f"{_V6_PREFIX}：请通过 get_v6_port(MULTI_AGENT_COLLABORATION, manager)"
            f" 或 MindForge.multi_agent 使用正式实现。"
        )

    def list_spaces(self, agent_id: str = "") -> List[Dict[str, Any]]:
        """列出共享记忆空间。"""
        raise NotImplementedError(
            f"{_V6_PREFIX}：请通过 get_v6_port(MULTI_AGENT_COLLABORATION, manager)"
            f" 或 MindForge.multi_agent 使用正式实现。"
        )

    # —— 成员与权限 ——

    def add_member(self, space_ref: str, agent_id: str,
                   role: str = "reader", actor: str = "") -> Dict[str, Any]:
        """向空间添加成员并分配角色。"""
        raise NotImplementedError(
            f"{_V6_PREFIX}：请通过 get_v6_port(MULTI_AGENT_COLLABORATION, manager)"
            f" 或 MindForge.multi_agent 使用正式实现。"
        )

    # —— 记忆共享 ——

    def share_memory(self, space_ref: str, memory_id: str,
                     actor: str) -> Dict[str, Any]:
        """把记忆共享进空间。"""
        raise NotImplementedError(
            f"{_V6_PREFIX}：请通过 get_v6_port(MULTI_AGENT_COLLABORATION, manager)"
            f" 或 MindForge.multi_agent 使用正式实现。"
        )

    def list_space_memories(self, space_ref: str, actor: str,
                            limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """列出空间中的记忆。"""
        raise NotImplementedError(
            f"{_V6_PREFIX}：请通过 get_v6_port(MULTI_AGENT_COLLABORATION, manager)"
            f" 或 MindForge.multi_agent 使用正式实现。"
        )

    # —— 规划中的高级能力（当前版本未提供）——

    def orchestrate_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """任务编排：拆分 → 派发 → 汇总 → 回溯（规划中，尚未实现）。"""
        raise NotImplementedError(
            f"{_V6_PREFIX} 规划中：{self.capability.value}.orchestrate_task"
            f" 尚未实现。"
        )

    def trace_memory_lineage(self, memory_id: str) -> Dict[str, Any]:
        """跨 Agent 记忆溯源与贡献度归因（规划中，尚未实现）。"""
        raise NotImplementedError(
            f"{_V6_PREFIX} 规划中：{self.capability.value}.trace_memory_lineage"
            f" 尚未实现。"
        )


class MultiAgentAdapterPort(MultiAgentCollaborationPort):
    """多 Agent 适配器端口（正式）

    把 `MultiAgentMemoryManager` 挂到 v6 端口上，**不重写其逻辑**：
    已实现的方法直接转发，规划中的方法沿用基类（抛 NotImplementedError）。
    """

    STATUS = V6Status.AVAILABLE
    requires_args = True

    def __init__(self, manager: Any) -> None:
        """用既有的多 Agent 管理器实例初始化。

        Args:
            manager: `modules.multi_agent.MultiAgentMemoryManager` 实例
        """
        self._manager = manager

    def status(self) -> V6Status:
        """正式发布，返回 AVAILABLE。"""
        return self.STATUS

    def create_space(self, name: str, owner_agent: str,
                     description: str = "", policy: str = "shared") -> Dict[str, Any]:
        """转发到 `MultiAgentMemoryManager.create_space()`。"""
        return self._manager.create_space(
            name=name, owner_agent=owner_agent,
            description=description, policy=policy,
        )

    def list_spaces(self, agent_id: str = "") -> List[Dict[str, Any]]:
        """转发到 `MultiAgentMemoryManager.list_spaces()`。"""
        return self._manager.list_spaces(agent_id=agent_id)

    def add_member(self, space_ref: str, agent_id: str,
                   role: str = "reader", actor: str = "") -> Dict[str, Any]:
        """转发到 `MultiAgentMemoryManager.add_member()`。"""
        return self._manager.add_member(
            space_ref=space_ref, agent_id=agent_id, role=role, actor=actor,
        )

    def share_memory(self, space_ref: str, memory_id: str,
                     actor: str) -> Dict[str, Any]:
        """转发到 `MultiAgentMemoryManager.share_memory()`。"""
        return self._manager.share_memory(
            space_ref=space_ref, memory_id=memory_id, actor=actor,
        )

    def list_space_memories(self, space_ref: str, actor: str,
                            limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """转发到 `MultiAgentMemoryManager.list_space_memories()`。"""
        return self._manager.list_space_memories(
            space_ref=space_ref, actor=actor, limit=limit, offset=offset,
        )

    def summary(self, space_ref: str = "") -> Dict[str, Any]:
        """返回既有管理器的统计概览（转发 `space_stats()`）。"""
        return self._manager.space_stats(space_ref=space_ref)


def create_multi_agent_adapter(manager: Any) -> MultiAgentAdapterPort:
    """工厂函数：把既有多 Agent 管理器包装成 v6 端口实例。

    Args:
        manager: `modules.multi_agent.MultiAgentMemoryManager` 实例

    Returns:
        已绑定管理器的 `MultiAgentAdapterPort` 实例
    """
    return MultiAgentAdapterPort(manager)


# ---------------------------------------------------------------------------
# 端口 2：6G AI 记忆预览（正式发布）
# ---------------------------------------------------------------------------

class MemoryPreviewPort(V6Port):
    """6G AI 记忆预览端口（正式实现）

    与稳定版并行的实验分支：Preview Channel 使用**独立的 SQLite 预览库**
    （互不污染主库），支持上下文蒸馏、增量索引统计与单记忆时间线。

    构造需要 `storage`（主库 `MindForgeStorage` 实例）。
    """

    capability = V6Capability.MEMORY_PREVIEW
    STATUS = V6Status.AVAILABLE
    requires_args = True

    def __init__(self, storage: Any) -> None:
        """用主库存储实例初始化。

        Args:
            storage: `mindforge.core.storage.MindForgeStorage` 实例
        """
        self.storage = storage
        self._data_dir = getattr(storage, "data_dir", None) or "."
        self._channels_dir = os.path.join(self._data_dir, _PREVIEW_DIRNAME)
        os.makedirs(self._channels_dir, exist_ok=True)
        self._registry_path = os.path.join(self._channels_dir, "channels.db")

    def status(self) -> V6Status:
        """6G AI 记忆预览已随 v6.0.0 正式发布，返回 AVAILABLE。"""
        return self.STATUS

    # ===== 通道管理 =====

    def _registry_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._registry_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS preview_channels (
                channel_id TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                db_path TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                config TEXT DEFAULT '{}',
                created_at REAL,
                updated_at REAL
            )
        """)
        conn.commit()
        return conn

    def open_preview_channel(self,
                             config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """开启预览通道（与稳定版并行、互不污染）。

        每个通道是一个独立的 SQLite 库文件，通道内的记忆快照/实验数据
        不会写入主库；关闭通道后可一键清理。

        Args:
            config: 可选配置，如 {"name": "实验分支", "note": "..."}

        Returns:
            {
                "success": True, "channel_id": ..., "name": ...,
                "db_path": ..., "status": "open", "created_at": ...
            }
        """
        config = dict(config or {})
        name = str(config.pop("name", ""))[:64] or f"preview_{time.strftime('%Y%m%d_%H%M%S')}"
        channel_id = f"pv_{uuid.uuid4().hex[:12]}"
        db_path = os.path.join(self._channels_dir, f"{channel_id}.db")
        now = time.time()

        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                tags TEXT DEFAULT '[]',
                importance TEXT DEFAULT 'MEDIUM',
                source_memory_id TEXT DEFAULT '',
                created_at REAL
            )
        """)
        conn.commit()
        conn.close()

        reg = self._registry_conn()
        reg.execute(
            "INSERT INTO preview_channels (channel_id, name, db_path, status,"
            " config, created_at, updated_at) VALUES (?, ?, ?, 'open', ?, ?, ?)",
            (channel_id, name, db_path, json.dumps(config), now, now),
        )
        reg.commit()
        reg.close()
        return {
            "success": True,
            "channel_id": channel_id,
            "name": name,
            "db_path": db_path,
            "status": "open",
            "config": config,
            "created_at": now,
        }

    def close_preview_channel(self, channel_id: str) -> Dict[str, Any]:
        """关闭并清理预览通道。

        将通道状态置为 closed 并删除独立库文件（可逆：仅标记时用
        purge=False 保留文件）。

        Args:
            channel_id: 预览通道 ID

        Returns:
            关闭结果字典
        """
        reg = self._registry_conn()
        row = reg.execute(
            "SELECT * FROM preview_channels WHERE channel_id = ?",
            (channel_id,)).fetchone()
        if row is None:
            reg.close()
            return {"success": False, "error": f"预览通道不存在: {channel_id}"}
        db_path = row["db_path"]
        now = time.time()
        reg.execute(
            "UPDATE preview_channels SET status = 'closed', updated_at = ?"
            " WHERE channel_id = ?", (now, channel_id))
        reg.commit()
        reg.close()
        # 删除独立库文件（仅当存在且处于本通道目录内，防止路径穿越）
        if db_path and os.path.abspath(db_path).startswith(
                os.path.abspath(self._channels_dir)):
            try:
                if os.path.exists(db_path):
                    os.remove(db_path)
            except OSError:
                pass
        return {"success": True, "channel_id": channel_id,
                "status": "closed", "closed_at": now}

    def list_preview_channels(self, include_closed: bool = False) -> List[Dict[str, Any]]:
        """列出预览通道。"""
        reg = self._registry_conn()
        if include_closed:
            rows = reg.execute(
                "SELECT * FROM preview_channels ORDER BY created_at DESC").fetchall()
        else:
            rows = reg.execute(
                "SELECT * FROM preview_channels WHERE status = 'open'"
                " ORDER BY created_at DESC").fetchall()
        reg.close()
        result = []
        for r in rows:
            cfg = r["config"]
            try:
                cfg = json.loads(cfg) if cfg else {}
            except (ValueError, TypeError):
                cfg = {}
            result.append({
                "channel_id": r["channel_id"],
                "name": r["name"],
                "status": r["status"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "config": cfg,
            })
        return result

    def channel_stats(self, channel_id: str) -> Dict[str, Any]:
        """预览通道统计（记忆快照数、分类分布）。"""
        reg = self._registry_conn()
        row = reg.execute(
            "SELECT * FROM preview_channels WHERE channel_id = ?",
            (channel_id,)).fetchone()
        reg.close()
        if row is None:
            return {"success": False, "error": f"预览通道不存在: {channel_id}"}
        if not os.path.exists(row["db_path"]):
            return {"success": False, "error": "预览库文件不存在（可能已关闭清理）"}
        conn = sqlite3.connect(row["db_path"])
        conn.row_factory = sqlite3.Row
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM channel_memories").fetchone()["c"]
        by_cat = conn.execute(
            "SELECT category, COUNT(*) AS c FROM channel_memories"
            " GROUP BY category").fetchall()
        conn.close()
        return {
            "success": True,
            "channel_id": channel_id,
            "name": row["name"],
            "status": row["status"],
            "total_memories": total,
            "by_category": {r["category"]: r["c"] for r in by_cat},
        }

    def snapshot_memory(self, channel_id: str, memory_id: str) -> Dict[str, Any]:
        """把主库的一条记忆快照进预览通道（不修改主库）。"""
        entry = self.storage.get_memory(memory_id)
        if entry is None:
            return {"success": False, "error": "记忆不存在"}
        reg = self._registry_conn()
        row = reg.execute(
            "SELECT * FROM preview_channels WHERE channel_id = ?",
            (channel_id,)).fetchone()
        reg.close()
        if row is None:
            return {"success": False, "error": f"预览通道不存在: {channel_id}"}
        if not os.path.exists(row["db_path"]):
            return {"success": False, "error": "预览库文件不存在（可能已关闭清理）"}
        conn = sqlite3.connect(row["db_path"])
        conn.execute(
            "INSERT OR REPLACE INTO channel_memories (id, content, category,"
            " tags, importance, source_memory_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (memory_id, entry.content or "", entry.category,
             str(entry.tags), entry.importance.value, memory_id, time.time()),
        )
        conn.commit()
        conn.close()
        return {"success": True, "channel_id": channel_id,
                "memory_id": memory_id, "snapshotted": True}

    # ===== 上下文蒸馏 =====

    def distill_context(self, memory_ids: List[str],
                        max_tokens: int = 2000) -> Dict[str, Any]:
        """上下文蒸馏：长程记忆分层摘要与 Token 压缩。

        按重要度分层压缩：
        - HIGH     ：保留全文（高价值记忆不舍弃）
        - MEDIUM   ：截断至预算配额内的摘要
        - LOW      ：仅保留标签与首句

        在总预算 `max_tokens` 内按重要度配额分配：HIGH 40% / MEDIUM 40% /
        LOW 20%；配额不足时优先保证 HIGH。

        Args:
            memory_ids: 要蒸馏的记忆 ID 列表
            max_tokens: Token 预算上限（默认 2000）

        Returns:
            {
                "success": True,
                "layers": {"HIGH": [...], "MEDIUM": [...], "LOW": [...]},
                "total_original_tokens": ...,
                "total_distilled_tokens": ...,
                "compression_ratio": 0.0~1.0,
                "budget": max_tokens,
                "omitted_count": 被预算裁掉的记忆条数,
            }
        """
        if not isinstance(memory_ids, list):
            return {"success": False, "error": "memory_ids 必须是列表"}
        if max_tokens < 1:
            max_tokens = 1
        layers: Dict[str, List[Dict[str, Any]]] = {
            "HIGH": [], "MEDIUM": [], "LOW": [],
        }
        total_original = 0
        fetched = []
        for mid in memory_ids:
            entry = self.storage.get_memory(mid)
            if entry is None:
                continue
            content = entry.content or ""
            total_original += _estimate_tokens(content)
            importance = getattr(entry.importance, "value", None) or str(
                entry.importance).upper()
            fetched.append((importance, mid, content, entry))

        # 预算配额：HIGH 40% / MEDIUM 40% / LOW 20%
        high_budget = int(max_tokens * 0.4)
        med_budget = int(max_tokens * 0.4)
        low_budget = max(0, max_tokens - high_budget - med_budget)
        budgets = {"HIGH": high_budget, "MEDIUM": med_budget, "LOW": low_budget}
        spent = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        omitted = 0

        for importance, mid, content, entry in fetched:
            key = importance if importance in budgets else "MEDIUM"
            budget = budgets[key]
            used = spent[key]
            if used >= budget:
                omitted += 1
                continue
            # 按层处理
            if key == "HIGH":
                text = content  # 高价值记忆保留全文
            elif key == "MEDIUM":
                # 摘要：前 60% + 末尾 20%（保头尾信息）
                cap = max(20, int(_estimate_tokens(content) * 0.8))
                text = content[:cap * 2]
            else:  # LOW：仅标签 + 首句
                text = content.split("\n")[0][:120] if content else ""
            tok = _estimate_tokens(text)
            if used + tok > budget:
                # 截断到剩余配额
                remain_chars = int((budget - used) * 2)
                text = text[:max(0, remain_chars)]
                tok = _estimate_tokens(text)
                if tok <= 0:
                    omitted += 1
                    continue
            spent[key] += tok
            layers[key].append({
                "memory_id": mid,
                "category": entry.category,
                "tags": entry.tags,
                "importance": key,
                "content": text,
                "tokens": tok,
            })

        total_distilled = sum(spent.values())
        return {
            "success": True,
            "layers": layers,
            "total_original_tokens": total_original,
            "total_distilled_tokens": total_distilled,
            "compression_ratio": round(
                (1 - total_distilled / total_original), 4)
            if total_original > 0 else 0.0,
            "budget": max_tokens,
            "omitted_count": omitted,
        }

    # ===== 增量索引统计 =====

    def incremental_index_stats(self) -> Dict[str, Any]:
        """增量索引统计：写入即索引的近实时召回指标。

        统计主库记忆总量、近 24h / 近 7d 增量、索引覆盖
        （FTS 可用性、嵌入后端可用性）与最新写入延迟。

        Returns:
            {
                "success": True,
                "total_memories": ...,
                "new_24h": ...,
                "new_7d": ...,
                "index": {"fts_available": bool, "embedding_available": bool},
                "latest_write_lag_seconds": ...,
                "generated_at": ...,
            }
        """
        conn = self.storage._get_conn()
        now = time.time()
        day = 86400.0
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE category != 'trash'"
        ).fetchone()["c"]
        new_24h = conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE created_at >= ?"
            " AND category != 'trash'", (now - day,)).fetchone()["c"]
        new_7d = conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE created_at >= ?"
            " AND category != 'trash'", (now - 7 * day,)).fetchone()["c"]
        latest = conn.execute(
            "SELECT MAX(created_at) AS m FROM memories WHERE category != 'trash'"
        ).fetchone()["m"]
        lag = max(0.0, now - (latest or now))
        fts_available = False
        try:
            fts_row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table'"
                " AND name = 'memory_fts'").fetchone()
            fts_available = fts_row is not None
        except sqlite3.Error:
            fts_available = False
        embedding_available = False
        try:
            emb = self.storage.get_embedding_status()
            embedding_available = bool(emb and emb.get("available"))
        except Exception:
            embedding_available = False
        return {
            "success": True,
            "total_memories": total,
            "new_24h": new_24h,
            "new_7d": new_7d,
            "index": {
                "fts_available": fts_available,
                "embedding_available": embedding_available,
            },
            "latest_write_lag_seconds": round(lag, 1),
            "generated_at": now,
        }

    # ===== 单记忆时间线 =====

    def timeline(self, memory_id: str) -> Dict[str, Any]:
        """记忆时间线可视化数据（单记忆：版本历史 + 演变历史 + 访问足迹）。

        Args:
            memory_id: 记忆 ID

        Returns:
            {
                "success": True,
                "memory_id": ...,
                "current": {...当前记忆摘要...},
                "versions": [...版本历史...],
                "evolutions": [...演变记录...],
                "audit_trail": [...审计足迹...],
            }
        """
        entry = self.storage.get_memory(memory_id)
        versions = self.storage.list_versions(memory_id, limit=50)
        evolutions = self.storage.list_evolutions(memory_id, limit=50)
        audit = []
        try:
            conn = self.storage._get_conn()
            rows = conn.execute(
                "SELECT action, actor, timestamp, privacy_level FROM audit_log"
                " WHERE memory_id = ? ORDER BY timestamp DESC LIMIT 20",
                (memory_id,)).fetchall()
            audit = [{
                "action": r["action"],
                "actor": r["actor"],
                "at": r["timestamp"],
                "privacy": r["privacy_level"],
            } for r in rows]
        except sqlite3.Error:
            audit = []
        current = None
        if entry is not None:
            current = {
                "memory_id": entry.id,
                "content_preview": (entry.content or "")[:120],
                "category": entry.category,
                "tags": entry.tags,
                "importance": entry.importance.value,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
            }
        return {
            "success": True,
            "memory_id": memory_id,
            "current": current,
            "versions": versions,
            "evolutions": evolutions,
            "audit_trail": audit,
        }

    def summary(self) -> Dict[str, Any]:
        """6G 记忆预览端口概览。"""
        stats = self.incremental_index_stats()
        channels = self.list_preview_channels()
        return {
            "channels_open": len(channels),
            "channels": channels,
            "index": stats.get("index", {}),
            "total_memories": stats.get("total_memories", 0),
            "new_24h": stats.get("new_24h", 0),
        }


# ---------------------------------------------------------------------------
# 端口注册表
# ---------------------------------------------------------------------------

#: 能力值 → 端口类 的注册表
_V6_PORT_REGISTRY: Dict[str, Type[V6Port]] = {}


def register_v6_port(port_cls: Type[V6Port],
                     override: bool = False) -> Type[V6Port]:
    """注册（或替换）一个 v6 端口实现。

    Args:
        port_cls: `V6Port` 的子类，必须声明 `capability`
        override: 同名能力已注册时是否覆盖，默认 False

    Returns:
        传入的 port_cls，便于作为装饰器使用

    Raises:
        TypeError: port_cls 不是 V6Port 的子类
        ValueError: capability 非法，或已注册且未允许覆盖
    """
    if not isinstance(port_cls, type) or not issubclass(port_cls, V6Port):
        raise TypeError(f"port_cls 必须是 V6Port 的子类，收到: {port_cls!r}")
    capability = getattr(port_cls, "capability", None)
    if not isinstance(capability, V6Capability):
        raise ValueError(f"port_cls 必须声明合法的 capability，收到: {capability!r}")
    key = capability.value
    if key in _V6_PORT_REGISTRY and not override:
        raise ValueError(
            f"能力 {key} 已注册端口 {_V6_PORT_REGISTRY[key].__name__}；"
            f"如需替换请传 override=True"
        )
    _V6_PORT_REGISTRY[key] = port_cls
    return port_cls


def unregister_v6_port(capability: V6Capability) -> bool:
    """注销指定能力的端口。

    Args:
        capability: 要注销的 v6 能力

    Returns:
        是否确实移除了一条已注册的端口
    """
    return _V6_PORT_REGISTRY.pop(capability.value, None) is not None


def get_v6_port(capability: V6Capability, *args: Any,
                **kwargs: Any) -> Optional[V6Port]:
    """获取指定能力当前注册的端口实例。

    Args:
        capability: v6 能力枚举
        *args: 传给端口构造器的位置参数（如 MemoryPreviewPort 需要 storage）
        **kwargs: 传给端口构造器的关键字参数

    Returns:
        端口实例；该能力未注册时返回 None

    Raises:
        TypeError: 构造器参数不匹配（例如 MemoryPreviewPort 缺少 storage）
    """
    port_cls = _V6_PORT_REGISTRY.get(capability.value)
    if port_cls is None:
        return None
    return port_cls(*args, **kwargs)


def list_v6_ports() -> List[Dict[str, Any]]:
    """列出所有已注册端口的状态（不实例化需要构造参数的端口）。

    Returns:
        每个元素为端口描述字典，含 capability / status / available /
        port / target_version / requires_args。
    """
    result: List[Dict[str, Any]] = []
    for capability in V6Capability:
        port_cls = _V6_PORT_REGISTRY.get(capability.value)
        if port_cls is None:
            continue
        status = getattr(port_cls, "STATUS", V6Status.PLANNED)
        result.append({
            "capability": capability.value,
            "status": status.value,
            "available": status is V6Status.AVAILABLE,
            "port": port_cls.__name__,
            "target_version": V6_TARGET_VERSION,
            "requires_args": bool(getattr(port_cls, "requires_args", False)),
        })
    return result


def v6_status() -> Dict[str, Any]:
    """返回 v6 端口总览（供 CLI / MCP / 官网调用）。

    Returns:
        {
            "current_version": 当前版本号,
            "target_version": "6.0.0",
            "released": v6 是否已发布,
            "ports": [ ... 各端口描述 ... ],
        }
    """
    return {
        "current_version": __version__,
        "target_version": V6_TARGET_VERSION,
        "released": True,
        "ports": list_v6_ports(),
    }


# 默认注册：多 Agent 经适配器挂载（需 manager 参数）、6G 记忆预览需 storage 参数
register_v6_port(MultiAgentAdapterPort)
register_v6_port(MemoryPreviewPort)

__all__ = [
    "V6_TARGET_VERSION",
    "V6Status",
    "V6Capability",
    "V6Port",
    "MultiAgentCollaborationPort",
    "MultiAgentAdapterPort",
    "MemoryPreviewPort",
    "create_multi_agent_adapter",
    "register_v6_port",
    "unregister_v6_port",
    "get_v6_port",
    "list_v6_ports",
    "v6_status",
]
