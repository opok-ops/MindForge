"""MindForge 规模压测基准（v6.4.6）

延迟曲线 + 吞吐 + 体积。可复现，输出 JSON。
用法：
    python benchmarks/scale_bench.py [--entries 100000] [--out result.json]
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mindforge import MindForge
from mindforge.core.types import MemoryConfig


def bench_search(mf, q, n=40):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        mf.search(q)
        ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    return {"p50": round(ts[len(ts) // 2], 1), "p95": round(ts[int(len(ts) * 0.95)], 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", type=int, default=100000)
    ap.add_argument("--out", default="scale_result.json")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="mf_scale_bench_")
    db = os.path.join(tmp, "m.db")
    mf = MindForge(config=MemoryConfig(db_path=db, key_file=None, encrypted=False))

    t0 = time.perf_counter()
    for i in range(args.entries):
        mf.add(f"规模条目{i} 主题 topic-{i % 2000} 实体 entity-{i % 500} "
               f"关键事实 fact-{i % 10000}：关于条目{i}的陈述性记忆，"
               f"含检索线索词与上下文描述")
    add_s = time.perf_counter() - t0

    queries = {
        "rare_en": "topic-33",
        "mixed_realistic": "fact-777 陈述性记忆 上下文",
        "combined": "entity-88 topic-1500",
        "cjk_all_common_pathological": "条目 55000 陈述性记忆",
    }
    search = {k: bench_search(mf, q) for k, q in queries.items()}

    out = {
        "entries": args.entries,
        "add_total_s": round(add_s, 1),
        "add_throughput_per_s": round(args.entries / add_s),
        "search": search,
        "db_mb": round(os.path.getsize(db) / 1e6, 1),
        "wal_mb": round(os.path.getsize(db + "-wal") / 1e6, 1)
        if os.path.exists(db + "-wal") else 0,
        "date": time.strftime("%Y-%m-%d"),
        "note": "TF-IDF 降级模式；本地 NVMe；v6.4.6 两层剪枝（eps+max_df）。"
                "mixed_realistic=稀有锚+常见词（真实用户查询形态）；"
                "cjk_all_common_pathological=全维全库词（合成模板数据病态，"
                "词表熵过低致剪枝全触发回退，O(N) 遍历为已知特性非缺陷）",
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(out, ensure_ascii=False, indent=2))

    mf.close()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
