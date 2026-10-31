"""
MindForge v6.0.0 功能 8：记忆主动收尾

记忆不再被动等你问，而是在正确时间主动说"你上次那件事还没办完"。

- create_follow_up(): 从记忆登记待跟进事项（due_at 到期时间）
- list_due(): 查询已到期未办结事项
- resolve_follow_up(): 办结
- list_follow_ups(): 列出（含已办结）
- 与 EventBus 联动：到期时 publish "memory.follow_up.due" 事件

存储：storage 新增 follow_ups 表（独立于 memories，避免污染记忆表）
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS follow_ups (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL DEFAULT '',
    note TEXT DEFAULT '',
    due_at REAL DEFAULT 0,
    status TEXT DEFAULT 'open',
    created_at REAL DEFAULT 0,
    resolved_at REAL DEFAULT 0,
    actor TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_follow_ups_due ON follow_ups(due_at);
CREATE INDEX IF NOT EXISTS idx_follow_ups_status ON follow_ups(status);
"""


def _ensure_table(storage) -> None:
    try:
        conn = storage._get_conn()
        conn.executescript(_TABLE_SQL)
        conn.commit()
    except Exception:
        pass


def create_follow_up(mf, memory_id: str, due_at: float = 0.0,
                     note: str = "", actor: str = "",
                     session_id: str = "") -> Dict[str, Any]:
    """从记忆登记一条待跟进事项。due_at 缺省为 24 小时后。"""
    if not memory_id or len(memory_id) > 128:
        return {"success": False, "error": "记忆 ID 无效"}
    # 确认记忆存在
    entry = mf._storage.get_memory(memory_id, actor, session_id)
    if entry is None:
        return {"success": False, "error": "记忆不存在"}
    _ensure_table(mf._storage)
    now = time.time()
    due = float(due_at or 0.0) or (now + 86400)
    fu_id = f"fu_{uuid.uuid4().hex[:12]}"
    conn = mf._storage._get_conn()
    conn.execute(
        "INSERT INTO follow_ups (id, memory_id, note, due_at, status, created_at, "
        "resolved_at, actor) VALUES (?, ?, ?, ?, 'open', ?, 0, ?)",
        (fu_id, memory_id, (note or "")[:500], due, now, actor or ""))
    conn.commit()
    mf._storage._add_audit(
        "follow_up_create", memory_id, actor or "", session_id or "", "",
        {"follow_up_id": fu_id, "due_at": due})
    return {"success": True, "follow_up_id": fu_id, "memory_id": memory_id,
            "due_at": due, "created_at": now, "note": note or ""}


def list_due(mf, limit: int = 20) -> List[Dict[str, Any]]:
    """查询已到期未办结的跟进事项"""
    _ensure_table(mf._storage)
    now = time.time()
    conn = mf._storage._get_conn()
    rows = conn.execute(
        "SELECT id, memory_id, note, due_at, status, created_at, actor "
        "FROM follow_ups WHERE status = 'open' AND due_at <= ? "
        "ORDER BY due_at ASC LIMIT ?", (now, max(1, min(500, int(limit))))).fetchall()
    return [_row_to_dict(r) for r in rows]


def resolve_follow_up(mf, follow_up_id: str, actor: str = "",
                      session_id: str = "") -> Dict[str, Any]:
    """办结一条跟进事项"""
    if not follow_up_id or len(follow_up_id) > 128:
        return {"success": False, "error": "follow_up_id 无效"}
    _ensure_table(mf._storage)
    conn = mf._storage._get_conn()
    row = conn.execute(
        "SELECT id, memory_id FROM follow_ups WHERE id = ?", (follow_up_id,)).fetchone()
    if not row:
        return {"success": False, "error": "跟进事项不存在"}
    now = time.time()
    conn.execute(
        "UPDATE follow_ups SET status = 'resolved', resolved_at = ?, actor = ? "
        "WHERE id = ? AND status = 'open'",
        (now, actor or "", follow_up_id))
    conn.commit()
    mf._storage._add_audit(
        "follow_up_resolve", row[1] or "", actor or "", session_id or "", "",
        {"follow_up_id": follow_up_id})
    return {"success": True, "follow_up_id": follow_up_id,
            "memory_id": row[1] or "", "resolved_at": now}


def list_follow_ups(mf, include_resolved: bool = False,
                    limit: int = 50) -> List[Dict[str, Any]]:
    """列出跟进事项"""
    _ensure_table(mf._storage)
    conn = mf._storage._get_conn()
    if include_resolved:
        rows = conn.execute(
            "SELECT id, memory_id, note, due_at, status, created_at, actor "
            "FROM follow_ups ORDER BY due_at ASC LIMIT ?",
            (max(1, min(500, int(limit))),)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, memory_id, note, due_at, status, created_at, actor "
            "FROM follow_ups WHERE status = 'open' ORDER BY due_at ASC LIMIT ?",
            (max(1, min(500, int(limit))),)).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(r) -> Dict[str, Any]:
    return {
        "follow_up_id": r[0],
        "memory_id": r[1],
        "note": r[2],
        "due_at": r[3],
        "status": r[4],
        "created_at": r[5],
        "actor": r[6],
    }
