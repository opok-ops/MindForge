"""
MindForge v6.0.0 功能 2：记忆时间线可视化

把记忆按时间组织成可截图分享的 Web 可视化：记忆时间线、四层分布、
偏好主题、偏好趋势。**只输出聚合统计，不含明文内容**（隐私安全）。

- build_timeline_visual(): 按日序列连续桶统计
- layers_distribution / categories / preferences 聚合
- preference_trend: 偏好标签随时间变化
- graph 规模：知识图谱节点/边（供可视化页展示）
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# 偏好标签前缀（从记忆 tags 提取主题）
_PREFERENCE_PREFIXES = ("user_preference", "preference", "profile")
# 需要排除的通用标签（不算偏好主题）
_GENERIC_TAGS = {"imported", "general", "favorite", "important"}


def _day_key(ts: float) -> str:
    """时间戳 -> YYYY-MM-DD"""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return datetime.fromtimestamp(0).strftime("%Y-%m-%d")


def _tag_theme(tags: List[str]) -> Optional[str]:
    """从标签中提取偏好主题（去重、过滤通用标签）。"""
    for t in tags or []:
        t = (t or "").strip()
        if not t or t in _GENERIC_TAGS:
            continue
        for p in _PREFERENCE_PREFIXES:
            if t.startswith(p):
                name = t[len(p):].strip(":_- ")
                if name:
                    return name
        return t
    return None


def _build_daily_buckets(start_ts: float, end_ts: float,
                         days: int) -> "OrderedDict[str, Dict[str, Any]]":
    """构建从 start 到 end 的 days 个连续日历日桶（含边界修正）。"""
    if days <= 0:
        days = 1
    # 边界修正：保证 days 个日历日（start_dt = end_dt - (days-1)）
    end_dt = datetime.fromtimestamp(end_ts).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=days - 1)
    buckets: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for i in range(days):
        d = start_dt + timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        buckets[key] = {
            "date": key,
            "count": 0,
            "layers": {},
            "categories": {},
        }
    return buckets


def build_timeline_visual(storage, category: Optional[str] = None,
                          layer: Optional[str] = None,
                          days: int = 365,
                          limit: int = 20000) -> Dict[str, Any]:
    """构建时间线可视化聚合数据（不含明文）。

    返回 dict：
    {
      "generated_at", "days", "start_date", "end_date",
      "total_memories", "active_memories",
      "timeline": [{"date","count","layers","categories"}...],
      "layers_distribution": {layer: count},
      "categories": {category: count},
      "preferences": {"themes": [{name,count}...], "trend": [{date, themes:[{name,count}]}...]},
      "graph": {"entities": n, "relations": n},
      "four_tier_summary": {"sensory":n,"short_term":n,"long_term":n,"permanent":n},
      "privacy": "aggregate-only"
    }
    """
    from mindforge.core.types import MemoryLayer

    now = time.time()
    layer_names = [e.value for e in MemoryLayer]
    buckets = _build_daily_buckets(now - 86400 * max(1, days), now, days)

    layers_dist: Dict[str, int] = {}
    categories: Dict[str, int] = {}
    theme_count: Dict[str, int] = {}
    total = 0
    active = 0

    # 解析 layer 参数（字符串 -> 枚举）
    layer_enum = None
    if layer:
        try:
            layer_enum = MemoryLayer.from_string(str(layer))
        except Exception:
            layer_enum = None

    mems = storage.list_memories(
        category=category, layer=layer_enum, limit=limit)
    for m in mems or []:
        total += 1
        if not getattr(m, "is_archived", False):
            active += 1
        ts = getattr(m, "created_at", 0) or 0
        key = _day_key(ts)
        bucket = buckets.get(key)
        if bucket is None:
            continue
        bucket["count"] += 1
        lname = getattr(m, "layer", "") or "short_term"
        lname = getattr(lname, "value", lname) or "short_term"
        bucket["layers"][lname] = bucket["layers"].get(lname, 0) + 1
        cname = getattr(m, "category", "") or "general"
        bucket["categories"][cname] = bucket["categories"].get(cname, 0) + 1
        layers_dist[lname] = layers_dist.get(lname, 0) + 1
        categories[cname] = categories.get(cname, 0) + 1
        theme = _tag_theme(list(getattr(m, "tags", []) or []))
        if theme:
            theme_count[theme] = theme_count.get(theme, 0) + 1

    # 偏好趋势：按月窗口聚合 top 主题
    trend_buckets: "OrderedDict[str, Dict[str, int]]" = OrderedDict()
    for m in mems or []:
        theme = _tag_theme(list(getattr(m, "tags", []) or []))
        if not theme:
            continue
        ts = getattr(m, "created_at", 0) or 0
        try:
            month = datetime.fromtimestamp(ts).strftime("%Y-%m")
        except (TypeError, ValueError, OSError):
            continue
        tb = trend_buckets.setdefault(month, {})
        tb[theme] = tb.get(theme, 0) + 1

    preference_trend = []
    for month, themes in trend_buckets.items():
        top = sorted(themes.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        preference_trend.append({"date": month, "themes": [
            {"name": name, "count": cnt} for name, cnt in top]})

    # 知识图谱规模（仅计数，不含实体明文）
    entities = 0
    relations = 0
    try:
        conn = storage._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM knowledge_graph").fetchone()
        entities = int(row[0]) if row and row[0] else 0
        row = conn.execute("SELECT COUNT(*) FROM graph_relations").fetchone()
        relations = int(row[0]) if row and row[0] else 0
    except Exception:
        pass

    timeline = []
    for b in buckets.values():
        timeline.append({
            "date": b["date"], "count": b["count"],
            "layers": b["layers"], "categories": b["categories"],
        })

    four_tier = {}
    for ln in layer_names:
        four_tier[ln] = layers_dist.get(ln, 0)

    start_key = next(iter(buckets), "")
    end_key = next(reversed(buckets), "") if buckets else ""

    return {
        "success": True,
        "generated_at": round(now, 3),
        "days": days,
        "start_date": start_key,
        "end_date": end_key,
        "total_memories": total,
        "active_memories": active,
        "timeline": timeline,
        "layers_distribution": layers_dist,
        "categories": categories,
        "preferences": {
            "themes": [{"name": k, "count": v}
                       for k, v in sorted(
                           theme_count.items(), key=lambda kv: (-kv[1], kv[0]))[:20]],
            "trend": preference_trend,
        },
        "graph": {"entities": entities, "relations": relations},
        "four_tier_summary": four_tier,
        "privacy": "aggregate-only",
    }
