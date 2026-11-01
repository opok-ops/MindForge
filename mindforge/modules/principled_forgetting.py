"""
MindForge v6.0.0 功能 7：有原则的遗忘（可解释）

不是删数据，而是像人一样：噪音自然淡出、重要模式留下来，
并且能解释"为什么忘了这段"。

- explain_forgetting(): 对单条记忆给出可解释的遗忘评分分解
  （时效衰减 / 访问频率 / 重要度保护 / 结果标记 / 噪音判定）
- run_principled_gc(): 执行有原则的遗忘 GC（复用 MemoryDecayEngine），
  输出每条被归档记忆的遗忘原因（explainable）
- forgetting_stats(): 遗忘引擎统计

设计：
- 复用 mindforge.modules.memory_decay.DecayConfig / MemoryDecayEngine
- 每条遗忘决策附带 reason 列表（人类可读）
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

_HALF_LIFE_DAYS = 30.0


def _decay_score(storage, entry, now: float) -> Dict[str, Any]:
    """计算单条记忆的遗忘评分分解"""
    try:
        from mindforge.modules.memory_decay import MemoryDecayEngine
        engine = MemoryDecayEngine(storage)
    except Exception:
        engine = None

    reasons = []
    factors = {}

    # 1. 时效衰减（最后访问距今）
    last_accessed = float(getattr(entry, "last_accessed_at", 0) or 0)
    created = float(getattr(entry, "created_at", 0) or 0)
    updated = float(getattr(entry, "updated_at", 0) or 0)
    ref_ts = max(last_accessed, updated, created) or now
    age_days = max(0.0, (now - ref_ts) / 86400.0)
    recency = 1.0 / (1.0 + age_days / _HALF_LIFE_DAYS)
    factors["recency"] = round(recency, 4)
    if age_days > _HALF_LIFE_DAYS * 2:
        reasons.append(f"超过 {int(age_days)} 天未被访问/更新，时效衰减")

    # 2. 访问频率
    access_count = int(getattr(entry, "access_count", 0) or 0)
    frequency = min(1.0, access_count / 10.0)
    factors["frequency"] = round(frequency, 4)
    if access_count == 0 and age_days > 7:
        reasons.append("从未被检索/访问，缺乏使用证据")

    # 3. 重要度保护
    importance_val = getattr(entry, "importance", "") or "MEDIUM"
    importance = getattr(importance_val, "value", importance_val)
    importance = str(importance or "MEDIUM").upper()
    importance_protect = {"HIGH": 0.9, "MEDIUM": 0.5, "LOW": 0.2}.get(importance, 0.5)
    factors["importance_protection"] = importance_protect
    if importance == "HIGH":
        reasons.append("重要度 HIGH：受保护，不易遗忘")
    elif importance == "LOW":
        reasons.append("重要度 LOW：弱保护，更容易淡出")

    # 4. 结果标记（功能 5 联动）：success 记忆保留、failure 记忆允许淡出
    outcome = ""
    try:
        conn = storage._get_conn()
        row = conn.execute(
            "SELECT outcome FROM memories WHERE id = ?",
            (getattr(entry, "id", ""),)).fetchone()
        outcome = row[0] if row else ""
    except Exception:
        pass
    outcome_boost = {"success": 0.3, "partial": 0.1, "failure": -0.2}.get(outcome, 0.0)
    factors["outcome_boost"] = outcome_boost
    if outcome == "success":
        reasons.append("结果标记 success：优先保留")
    elif outcome == "failure":
        reasons.append("结果标记 failure：允许降权淡出")

    # 5. 噪声判定（短且无实义）
    content = (getattr(entry, "content", "") or "").strip()
    is_noisy = len(content) < 12 and not any(
        k in content for k in ("工作", "喜欢", "住在", "住在", "是", "目标"))
    factors["noise"] = 1.0 if is_noisy else 0.0
    if is_noisy:
        reasons.append("内容过短且无明确信息量（噪声型记忆）")

    # 综合遗忘倾向（0~1，越高越该遗忘）
    forget_tendency = round(
        (1.0 - recency) * 0.45
        + (1.0 - frequency) * 0.25
        + (1.0 - importance_protect) * 0.2
        + (1.0 - (0.5 + outcome_boost)) * 0.1, 4)
    factors["forget_tendency"] = forget_tendency
    factors["age_days"] = round(age_days, 2)
    factors["access_count"] = access_count

    if engine is not None:
        try:
            strength = engine.compute_strength(entry)
            factors["engine_strength"] = round(float(strength or 0.0), 4)
        except Exception:
            pass
    return {"factors": factors, "reasons": reasons,
            "forget_tendency": forget_tendency}


def _coerce_memory_id(memory_id: Any) -> tuple:
    """接受 str 或 add() 返回的 MemoryEntry，返回 (id, error)。

    v6.0.0 直接 len(memory_id)，传对象会抛 TypeError 而非友好报错（v6.1.1 修）。
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


def explain_forgetting(storage, memory_id: str,
                       actor: str = "", session_id: str = "") -> Dict[str, Any]:
    """解释一条记忆的遗忘决策"""
    memory_id, id_error = _coerce_memory_id(memory_id)
    if id_error:
        return {"success": False, "error": id_error}
    entry = storage.get_memory(memory_id, actor, session_id)
    if entry is None:
        return {"success": False, "error": "记忆不存在"}
    now = time.time()
    d = _decay_score(storage, entry, now)
    return {
        "success": True,
        "memory_id": memory_id,
        "content_preview": (getattr(entry, "content", "") or "")[:120],
        "explanation": d["reasons"],
        "forget_tendency": d["forget_tendency"],
        "factors": d["factors"],
        "privacy": "aggregate-only",
    }


def run_principled_gc(mf, dry_run: bool = False, days: int = 30) -> Dict[str, Any]:
    """执行有原则的遗忘 GC：噪音淡出、重要模式保留，输出可解释决策。

    - 遍历未归档记忆，计算 forget_tendency
    - 阈值以上且非保护记忆 → 归档（archive），并记录原因
    """
    try:
        from mindforge.modules.memory_decay import MemoryDecayEngine
        engine = MemoryDecayEngine(mf._storage)
    except Exception:
        engine = None

    now = time.time()
    entries = mf._storage.list_memories(limit=10000) or []
    threshold = 0.72
    archived = []
    protected = 0
    scanned = 0

    for entry in entries:
        imp_val = getattr(entry, "importance", "") or "MEDIUM"
        importance = str(getattr(imp_val, "value", imp_val) or "MEDIUM").upper()
        if importance == "HIGH":
            protected += 1
            continue
        scanned += 1
        d = _decay_score(mf._storage, entry, now)
        if d["forget_tendency"] < threshold:
            continue
        reasons = d["reasons"] or ["长期未使用，噪音淡出"]
        mem_id = getattr(entry, "id", "")
        if not dry_run:
            ok = False
            try:
                if engine is not None:
                    ok = bool(engine.storage.archive_memories_by_ids(
                        [mem_id], reason="principled_forgetting", actor="gc") or 0)
                if not ok:
                    ok = bool(mf._storage.archive_memories_by_ids(
                        [mem_id], reason="principled_forgetting", actor="gc") or 0)
            except Exception:
                try:
                    ok = bool(mf._storage.archive_memories_by_ids(
                        [mem_id], reason="principled_forgetting", actor="gc") or 0)
                except Exception:
                    ok = False
        else:
            ok = True
        archived.append({
            "memory_id": mem_id,
            "content_preview": (getattr(entry, "content", "") or "")[:80],
            "tendency": d["forget_tendency"],
            "reasons": reasons,
            "archived": ok,
        })
        if len(archived) >= 200:
            break

    return {
        "success": True,
        "dry_run": bool(dry_run),
        "scanned": scanned,
        "protected_high_importance": protected,
        "candidates": len(archived),
        "archived": sum(1 for a in archived if a["archived"]),
        "decisions": archived[:50],
        "threshold": threshold,
    }


def forgetting_stats(mf) -> Dict[str, Any]:
    """遗忘引擎统计"""
    stats = {"total_memories": 0, "archived": 0, "high_importance": 0,
             "outcome_marked": 0, "explainable": True}
    try:
        conn = mf._storage._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        stats["total_memories"] = row[0] if row else 0
        row = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE importance = 'HIGH'").fetchone()
        stats["high_importance"] = row[0] if row else 0
        row = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE outcome != ''").fetchone()
        stats["outcome_marked"] = row[0] if row else 0
        row = conn.execute("SELECT COUNT(*) FROM archived_memories").fetchone()
        stats["archived"] = row[0] if row else 0
    except Exception:
        pass
    return stats
