"""
MindForge v6.0.0 功能 5：按结果学习的记忆

不止记"发生过什么"，还要记"哪段记忆带来了好结果"：
- 成功的方法 → 记忆重要度上调，检索时优先
- 失败的方法 → 自动降权
- 可在记忆中标注 outcome 并给出解释

实现：
- memories 表新增 outcome / outcome_note / outcome_at 三列（migration）
- apply_outcome(): 更新记忆的结果标记并调整 importance
- search_weighted(): 检索时按 outcome 加权（success 优先）
- outcome_stats(): 结果分布统计
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

_OUTCOME_IMPORTANCE_MAP = {
    "success": {"HIGH": 0, "MEDIUM": 1, "LOW": 1},    # 成功 → 上调
    "partial": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
    "failure": {"HIGH": 0, "MEDIUM": -1, "LOW": -1},  # 失败 → 下调
}

_IMPORTANCE_ORDER = ["LOW", "MEDIUM", "HIGH"]


def _shift_importance(importance: str, outcome: str) -> str:
    """按结果调整重要度档位"""
    imp = (importance or "MEDIUM").upper()
    if imp not in _IMPORTANCE_ORDER:
        imp = "MEDIUM"
    shift = _OUTCOME_IMPORTANCE_MAP.get(outcome, {}).get(imp, 0)
    idx = _IMPORTANCE_ORDER.index(imp)
    idx = max(0, min(len(_IMPORTANCE_ORDER) - 1, idx + shift))
    return _IMPORTANCE_ORDER[idx]


def _ensure_columns(conn) -> None:
    """幂等添加 outcome 列"""
    for col, ddl in (
        ("outcome", "TEXT DEFAULT ''"),
        ("outcome_note", "TEXT DEFAULT ''"),
        ("outcome_at", "REAL DEFAULT 0"),
    ):
        try:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {ddl}")
        except Exception:
            pass  # 已存在


def apply_outcome(storage, memory_id: str, outcome: str = "success",
                  note: str = "", actor: str = "",
                  session_id: str = "") -> Dict[str, Any]:
    """给记忆标记结果并调整重要度。outcome: success/partial/failure"""
    outcome = (outcome or "success").lower()
    if outcome not in ("success", "partial", "failure"):
        return {"success": False, "error": f"无效 outcome: {outcome}"}
    if not memory_id or len(memory_id) > 128:
        return {"success": False, "error": "记忆 ID 无效"}

    conn = storage._get_conn()
    _ensure_columns(conn)
    row = conn.execute(
        "SELECT importance, outcome FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if not row:
        return {"success": False, "error": "记忆不存在"}
    old_importance, old_outcome = row[0] or "MEDIUM", row[1] or ""
    new_importance = _shift_importance(old_importance, outcome)

    now = time.time()
    conn.execute(
        "UPDATE memories SET outcome = ?, outcome_note = ?, outcome_at = ?, "
        "importance = ? WHERE id = ?",
        (outcome, (note or "")[:500], now, new_importance, memory_id))
    conn.commit()
    storage._add_audit("outcome_learn", memory_id, actor or "", session_id or "",
                       "", {"outcome": outcome, "importance": new_importance,
                            "note": (note or "")[:200]})
    return {"success": True, "memory_id": memory_id, "outcome": outcome,
            "importance_before": old_importance, "importance_after": new_importance,
            "outcome_at": now}


def outcome_stats(storage) -> Dict[str, Any]:
    """结果标记分布统计"""
    conn = storage._get_conn()
    _ensure_columns(conn)
    try:
        rows = conn.execute(
            "SELECT outcome, COUNT(*) FROM memories WHERE outcome != '' "
            "GROUP BY outcome").fetchall()
    except Exception:
        rows = []
    dist = {r[0]: r[1] for r in rows}
    total = sum(dist.values())
    return {
        "total_marked": total,
        "distribution": dist,
        "success_rate": round(dist.get("success", 0) / total, 3) if total else 0.0,
    }


def search_weighted(mf, query: str, max_results: int = 10,
                    outcome: str = "success",
                    min_relevance: float = 0.0) -> List[Dict[str, Any]]:
    """按结果加权检索：先用正常检索召回，再把命中 outcome 的记忆提到前面。

    返回 [{memory_id, content_preview, outcome, importance, relevance}...]
    """
    from mindforge.modules.outcome_learning import _ensure_columns

    results = mf.search(query=query, max_results=max(5, int(max_results) * 2),
                        min_relevance=float(min_relevance or 0.0))
    chunks = getattr(results, "chunks", None) or (results if isinstance(results, list) else [])
    out = []
    for r in chunks:
        mid = getattr(r, "memory_id", None) or getattr(r, "id", None)
        if not mid:
            continue
        row = None
        try:
            conn = mf._storage._get_conn()
            _ensure_columns(conn)
            row = conn.execute(
                "SELECT outcome, importance, content FROM memories WHERE id = ?",
                (mid,)).fetchone()
        except Exception:
            pass
        out.append({
            "memory_id": mid,
            "content_preview": (getattr(r, "content", "") or "")[:200],
            "outcome": row[0] if row else "",
            "importance": row[1] if row else "",
            "relevance": round(float(getattr(r, "score", 0.0) or 0.0), 4),
        })

    # 目标 outcome 的记忆前置
    out.sort(key=lambda x: (0 if x["outcome"] == outcome else 1,
                            -x["relevance"]))
    return out[:max(1, int(max_results))]
