# -*- coding: utf-8 -*-
"""多 run 聚合对比：几份评测 JSON → 一张 Markdown 对比表。

用途：横向对比 backend（mindforge vs context_window vs 自定义）
或纵向对比版本迭代（v6.6.4 vs v6.7.0）。
"""
import json
from typing import Any, Dict, List


def _load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _label(rep: Dict[str, Any], fallback: str) -> str:
    env = rep.get("env", {})
    return (f"{env.get('backend', '?')}/"
            f"{env.get('judge', '?')}/{fallback}")


def aggregate_markdown(paths: List[str]) -> str:
    """生成对比表。第一列是指标，后面每列一个 run。"""
    reps = [(_load(p), p) for p in paths]
    if not reps:
        return "（无 run）"

    labels = []
    seen: Dict[str, int] = {}
    for rep, p in reps:
        lab = _label(rep, p.rsplit("/", 1)[-1][:24])
        n = seen.get(lab, 0) + 1
        seen[lab] = n
        labels.append(lab if n == 1 else f"{lab}#{n}")

    cats = ["singlehop", "multihop", "temporal",
            "adversarial", "open_domain"]
    rows: List[str] = ["| 指标 | " + " | ".join(labels) + " |",
                       "|---|" + "---|" * len(labels)]

    def cell(rep: Dict[str, Any], fn) -> str:
        agg = rep.get("aggregate", {})
        v = fn(agg)
        return "—" if v is None else str(v)

    rows.append("| 问数 | " + " | ".join(
        cell(r, lambda a: a.get("count")) for r, _ in reps) + " |")
    rows.append("| 总 acc% | " + " | ".join(
        cell(r, lambda a: a.get("acc")) for r, _ in reps) + " |")
    rows.append("| 均分 | " + " | ".join(
        cell(r, lambda a: a.get("avg_score")) for r, _ in reps) + " |")
    rows.append("| Recall@K% | " + " | ".join(
        cell(r, lambda a: a.get("recall_at_k")) for r, _ in reps) + " |")
    rows.append("| MRR@K | " + " | ".join(
        cell(r, lambda a: a.get("mrr_at_k")) for r, _ in reps) + " |")
    rows.append("| 延迟 p50 ms | " + " | ".join(
        cell(r, lambda a: a.get("latency_ms", {}).get("p50"))
        for r, _ in reps) + " |")
    rows.append("| 延迟 p95 ms | " + " | ".join(
        cell(r, lambda a: a.get("latency_ms", {}).get("p95"))
        for r, _ in reps) + " |")
    for cat in cats:
        rows.append(f"| {cat} acc% | " + " | ".join(
            cell(r, lambda a, c=cat: a.get("by_category", {})
                 .get(c, {}).get("acc")) for r, _ in reps) + " |")

    env_lines = ["", "## 环境声明", ""]
    for (rep, p), lab in zip(reps, labels):
        e = rep.get("env", {})
        env_lines.append(
            f"- **{lab}**: bench {e.get('bench_version')} | "
            f"py{e.get('python')} | dataset {e.get('dataset')} | "
            f"k={e.get('k')} | seed={e.get('seed')} | `{p}`")
    return "\n".join(rows + env_lines) + "\n"
