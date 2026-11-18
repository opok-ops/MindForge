# -*- coding: utf-8 -*-
"""指标聚合：acc / 均分 / Recall@K / MRR@K / 延迟分位 / 类别分解。

全部手写实现，零第三方依赖（不引 numpy）。
"""
import math
from typing import Any, Dict, List

CATEGORIES = ["singlehop", "multihop", "temporal",
              "adversarial", "open_domain"]


def percentile(sorted_vals: List[float], p: float) -> float:
    """线性插值分位数。vals 需已升序；空列表返回 0.0。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = (len(sorted_vals) - 1) * p / 100.0
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(sorted_vals[int(idx)])
    frac = idx - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把逐问记录聚合成报告指标。"""
    n = len(records)
    if n == 0:
        return {"count": 0}
    scores = [r.get("score", 0) for r in records]
    correct = sum(1 for r in records if r.get("correct"))
    lat = sorted(float(r.get("latency_ms", 0.0)) for r in records)

    # 检索层指标（must_have 存在时才有意义）
    retr = [r for r in records if r.get("retrieved") is not None]
    recall = (sum(1 for r in retr if r["retrieved"]) / len(retr)
              if retr else None)
    mrr = (sum((1.0 / r["first_hit_rank"]) if r.get("retrieved")
               and r.get("first_hit_rank", 0) > 0 else 0.0
               for r in retr) / len(retr)) if retr else None

    by_cat: Dict[str, Any] = {}
    for cat in CATEGORIES:
        sub = [r for r in records if r.get("category") == cat]
        if sub:
            by_cat[cat] = {
                "count": len(sub),
                "acc": round(sum(1 for r in sub if r.get("correct"))
                             / len(sub) * 100, 1),
                "avg_score": round(sum(r.get("score", 0)
                                       for r in sub) / len(sub), 2),
            }

    return {
        "count": n,
        "acc": round(correct / n * 100, 1),
        "avg_score": round(sum(scores) / n, 2),
        "recall_at_k": round(recall * 100, 1) if recall is not None else None,
        "mrr_at_k": round(mrr, 3) if mrr is not None else None,
        "latency_ms": {
            "p50": round(percentile(lat, 50), 2),
            "p95": round(percentile(lat, 95), 2),
        },
        "by_category": by_cat,
        "judge_failures": sum(1 for r in records
                              if r.get("judge_raw") == "__LLM_ERROR__"),
    }
