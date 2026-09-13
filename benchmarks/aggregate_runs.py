# -*- coding: utf-8 -*-
"""
把多次 perf_benchmark.py 的结果按字段取中位数，合并成 perf_results.json。

用法：
    python benchmarks/aggregate_runs.py run1.json run2.json run3.json
"""
import json
import statistics
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "benchmarks" / "results"


def median_merge(base, others, path=""):
    if isinstance(base, dict):
        out = {}
        for k, v in base.items():
            vals = [o.get(k) for o in others if isinstance(o, dict) and k in o]
            out[k] = median_merge(v, vals, f"{path}.{k}") if vals else v
        return out
    if isinstance(base, list):
        # search_curve：按 records 对齐
        if base and isinstance(base[0], dict) and "records" in base[0]:
            out = []
            for i, item in enumerate(base):
                aligned = []
                for o in others:
                    if isinstance(o, list) and i < len(o):
                        aligned.append(o[i])
                out.append(median_merge(item, aligned, f"{path}[{i}]"))
            return out
        return base
    if isinstance(base, bool):
        return all([base] + others)
    if isinstance(base, (int, float)):
        nums = [base] + [x for x in others if isinstance(x, (int, float))]
        m = statistics.median(nums)
        return round(m, 4) if isinstance(m, float) else m
    return base


def main():
    files = [Path(a) if Path(a).is_absolute() else RESULTS / a
             for a in sys.argv[1:]]
    runs = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    merged = median_merge(runs[0], runs[1:])
    merged["aggregated_from"] = [f.name for f in files]
    merged["aggregation"] = "3 次独立运行逐字段中位数"
    out = RESULTS / "perf_results.json"
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("merged ->", out)


if __name__ == "__main__":
    main()
