"""
MindForge v6.0.0 功能 8：记忆主动收尾

记忆不再被动等你问，而是在正确时间主动说"你上次那件事还没办完"。

- create_follow_up(): 从记忆登记待跟进事项（due_at 到期时间）
- list_due(): 查询已到期未办结事项
- nag_due(): 主动催办并生成回执（v6.1.1）
- resolve_follow_up(): 办结
- list_follow_ups(): 列出（含已办结）
- 与 EventBus 联动：nag_due() 催办时 publish "memory.follow_up.due" 事件

存储：follow_ups 表（独立于 memories，避免污染记忆表）
v6.1.1 为该表补 nag_count / last_nagged_at / receipt 三列（幂等 ALTER，旧库自动升级）。

历史更正：本模块文档自 v6.0.0 起就写着"与 EventBus 联动：到期时 publish 事件"，
但 v6.0.0 全文没有任何 publish 调用，也没有可记录催办次数的字段——"记忆主动帮你
收尾"实际只能被动查询。v6.1.1 起该声明由 nag_due() 真正实现。
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

# v6.1.1：催办计数与回执。此前 follow_ups 只有 open/resolved 两态，
# 「已催了一次」无处可记，模块文档声称的到期事件也从无 publish 调用。
_NAG_COLUMNS = (
    ("nag_count", "INTEGER DEFAULT 0"),
    ("last_nagged_at", "REAL DEFAULT 0"),
    ("receipt", "TEXT DEFAULT ''"),
)

_SELECT_COLS = ("id, memory_id, note, due_at, status, created_at, actor, "
                "nag_count, last_nagged_at, receipt")
# v6.0.0 的 follow_ups 只有前 7 列。新增三列靠 _ensure_table() 懒加，而该函数
# 整体包在 except: pass 里；若迁移因任何原因未完成，直接 SELECT 新列会抛
# OperationalError。故查询列需按实际表结构自适应。
_SELECT_COLS_LEGACY = "id, memory_id, note, due_at, status, created_at, actor"


def _select_cols(conn) -> str:
    """返回当前表可用的 SELECT 列清单。"""
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(follow_ups)")}
    except Exception:
        return _SELECT_COLS_LEGACY
    if {"nag_count", "last_nagged_at", "receipt"} <= have:
        return _SELECT_COLS
    return _SELECT_COLS_LEGACY


def _ensure_table(storage) -> None:
    try:
        conn = storage._get_conn()
        conn.executescript(_TABLE_SQL)
        for col, ddl in _NAG_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE follow_ups ADD COLUMN {col} {ddl}")
            except Exception:
                pass  # 已存在
        conn.commit()
    except Exception:
        pass


def _normalize_memory_id(memory_id: Any) -> tuple:
    """接受 str 或 add() 返回的 MemoryEntry，返回 (id, error)。

    v6.0.0 直接 len(memory_id)，传对象会抛 TypeError 而非友好报错。
    """
    if memory_id is None:
        return None, "记忆 ID 无效"
    if not isinstance(memory_id, str):
        memory_id = getattr(memory_id, "id", None)
        if not isinstance(memory_id, str):
            return None, "记忆 ID 必须是字符串或 add() 返回的 MemoryEntry 对象"
    if not memory_id or len(memory_id) > 128:
        return None, "记忆 ID 无效"
    return memory_id, None


def _publish_due_event(mf, payload: Dict[str, Any]) -> bool:
    """发布 memory.follow_up.due 事件；失败不影响主流程。"""
    try:
        if getattr(mf, "_event_bus", None) is None:
            from mindforge.modules.event_bus import EventBus
            mf._event_bus = EventBus()
        res = mf._event_bus.publish("memory.follow_up.due", payload)
        return not (isinstance(res, dict) and res.get("skipped"))
    except Exception:
        return False


def create_follow_up(mf, memory_id: str, due_at: float = 0.0,
                     note: str = "", actor: str = "",
                     session_id: str = "") -> Dict[str, Any]:
    """从记忆登记一条待跟进事项。due_at 缺省为 24 小时后。"""
    memory_id, id_error = _normalize_memory_id(memory_id)
    if id_error:
        return {"success": False, "error": id_error}
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
        "resolved_at, actor, nag_count, last_nagged_at, receipt) "
        "VALUES (?, ?, ?, ?, 'open', ?, 0, ?, 0, 0, '')",
        (fu_id, memory_id, (note or "")[:500], due, now, actor or ""))
    conn.commit()
    mf._storage._add_audit(
        "follow_up_create", memory_id, actor or "", session_id or "", "",
        {"follow_up_id": fu_id, "due_at": due})
    return {"success": True, "follow_up_id": fu_id, "memory_id": memory_id,
            "due_at": due, "created_at": now, "note": note or "",
            "nag_count": 0, "receipt": ""}


def list_due(mf, limit: int = 20) -> List[Dict[str, Any]]:
    """查询已到期未办结的跟进事项"""
    _ensure_table(mf._storage)
    now = time.time()
    conn = mf._storage._get_conn()
    rows = conn.execute(
        "SELECT " + _select_cols(conn) + " "
        "FROM follow_ups WHERE status = 'open' AND due_at <= ? "
        "ORDER BY due_at ASC LIMIT ?", (now, max(1, min(500, int(limit))))).fetchall()
    return [_row_to_dict(r) for r in rows]


def nag_due(mf, interval_hours: float = 24.0, max_nags: int = 3,
            actor: str = "memory", session_id: str = "") -> Dict[str, Any]:
    """主动催办到期未办结的跟进事项，并生成回执（v6.1.1）。

    此前 follow_up 只能被动查询：模块文档写着「与 EventBus 联动：到期时
    publish memory.follow_up.due」，但全文没有任何 publish 调用，表里也没有
    可记录「已催了几次」的字段，所以「记忆主动帮你收尾」无法演示。

    - 已到期且距上次催办超过 interval_hours 的 open 事项 → nag_count + 1
    - 生成人读回执文本（receipt），并审计 follow_up_nag
    - 向 EventBus 发布 memory.follow_up.due；事件失败不影响催办落库
    - 同一事项最多催 max_nags 次，避免无限骚扰

    Args:
        interval_hours: 两次催办之间的最小间隔（小时）。
        max_nags: 单条事项的催办次数上限。

    Returns:
        {"nagged": [回执字典...], "skipped_recent": int, "skipped_maxed": int}
    """
    _ensure_table(mf._storage)
    now = time.time()
    interval = max(0.0, float(interval_hours)) * 3600.0
    cap = max(1, int(max_nags))
    conn = mf._storage._get_conn()
    if _select_cols(conn) == _SELECT_COLS_LEGACY:
        # 迁移未完成（例如只读库或表结构异常）：明确报错，
        # 不让下面的 UPDATE 抛裸的 OperationalError
        return {"error": "follow_ups 表缺少催办字段，无法记录催办次数",
                "nagged": [], "skipped_recent": 0, "skipped_maxed": 0}
    rows = conn.execute(
        "SELECT " + _select_cols(conn) + " "
        "FROM follow_ups WHERE status = 'open' AND due_at <= ? "
        "ORDER BY due_at ASC LIMIT 500", (now,)).fetchall()

    nagged: List[Dict[str, Any]] = []
    skipped_recent = 0
    skipped_maxed = 0

    for r in rows:
        cur = _row_to_dict(r)
        if int(cur["nag_count"]) >= cap:
            skipped_maxed += 1
            continue
        if cur["nag_count"] and (now - float(cur["last_nagged_at"])) < interval:
            skipped_recent += 1
            continue

        overdue_hours = (now - float(cur["due_at"])) / 3600.0
        times = int(cur["nag_count"]) + 1
        note = (cur["note"] or "跟进事项").strip() or "跟进事项"
        receipt = _build_receipt(note, times, overdue_hours)

        conn.execute(
            "UPDATE follow_ups SET nag_count = ?, last_nagged_at = ?, receipt = ? "
            "WHERE id = ? AND status = 'open'",
            (times, now, receipt, cur["follow_up_id"]))
        conn.commit()

        mf._storage._add_audit(
            "follow_up_nag", cur["memory_id"], actor, session_id, "",
            {"follow_up_id": cur["follow_up_id"], "nag_count": times,
             "overdue_hours": round(overdue_hours, 2)})

        item = {
            "follow_up_id": cur["follow_up_id"],
            "memory_id": cur["memory_id"],
            "note": note,
            "receipt": receipt,
            "nag_count": times,
            "last_nagged_at": now,
            "due_at": float(cur["due_at"]),
            "overdue_hours": round(overdue_hours, 2),
            "status": "open",
        }
        nagged.append(item)
        # 事件仅为通知面，发布失败不回滚催办
        _publish_due_event(mf, {
            "follow_up_id": cur["follow_up_id"],
            "memory_id": cur["memory_id"],
            "receipt": receipt,
            "nag_count": times,
            "actor": actor,
            "session_id": session_id,
        })

    return {"nagged": nagged, "skipped_recent": skipped_recent,
            "skipped_maxed": skipped_maxed}


def _build_receipt(note: str, times: int, overdue_hours: float) -> str:
    """把「还没回 + 已催几次 + 逾期多久」拼成一句可直接展示的回执。"""
    if overdue_hours < 24:
        span = "今天"
    elif overdue_hours < 24 * 7:
        span = "%d 天" % int(overdue_hours // 24)
    else:
        span = "%d 周" % int(overdue_hours // (24 * 7))
    cn = {1: "一", 2: "两", 3: "三"}.get(times, str(times))
    return "「%s」到期 %s 仍无结果，已催了%s次。" % (note, span, cn)


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
            "SELECT " + _select_cols(conn) + " "
            "FROM follow_ups ORDER BY due_at ASC LIMIT ?",
            (max(1, min(500, int(limit))),)).fetchall()
    else:
        rows = conn.execute(
            "SELECT " + _select_cols(conn) + " "
            "FROM follow_ups WHERE status = 'open' ORDER BY due_at ASC LIMIT ?",
            (max(1, min(500, int(limit))),)).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(r) -> Dict[str, Any]:
    """把查询行转成字典。

    兼容两种列形状：v6.1.1 的 10 列，以及迁移尚未发生时的 v6.0.0 7 列
    （见 _select_cols）。缺列时补默认值，保证调用方永远拿到全部键。
    """
    row = list(r) + [0, 0.0, ""]  # v6.0.0 形状缺省补齐
    return {
        "follow_up_id": row[0],
        "memory_id": row[1],
        "note": row[2],
        "due_at": row[3],
        "status": row[4],
        "created_at": row[5],
        "actor": row[6],
        "nag_count": int(row[7] or 0),
        "last_nagged_at": float(row[8] or 0.0),
        "receipt": row[9] or "",
    }
