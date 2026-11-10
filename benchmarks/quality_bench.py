"""MindForge 检索质量基准（v6.4.6）

可复现：固定种子合成数据集，三档难度查询，输出 Recall@1/@5、MRR、
干扰误召回率。无外部依赖（TF-IDF 降级模式同样可跑）。

用法：
    python benchmarks/quality_bench.py [--entries 2000] [--out result.json]
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mindforge import MindForge
from mindforge.core.types import MemoryConfig


TOPICS = ["机器学习", "数据库", "前端开发", "分布式系统", "网络安全",
          "编译原理", "操作系统", "算法设计", "嵌入式", "量化交易"]
ENTITIES = ["项目Alpha", "项目Beta", "模块Core", "模块Edge", "服务A",
            "服务B", "组件X", "组件Y", "平台P", "平台Q"]


def gen_entries(n: int, rng: random.Random):
    entries = []
    for i in range(n):
        topic = TOPICS[i % len(TOPICS)]
        entity = ENTITIES[rng.randrange(len(ENTITIES))]
        fact = f"fact-{i}"
        text = (f"{topic}领域{entity}的记录{i}：关键事实{fact}，"
                f"编号锚点 mark-{i}，上下文描述{i % 7}号场景的执行细节")
        entries.append((text, f"mark-{i}"))
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", type=int, default=2000)
    ap.add_argument("--out", default="quality_result.json")
    ap.add_argument("--keep", action="store_true", help="保留临时库")
    args = ap.parse_args()

    import tempfile, shutil
    tmp = tempfile.mkdtemp(prefix="mf_qbench_")
    db = str(Path(tmp) / "q.db")
    mf = MindForge(config=MemoryConfig(db_path=db, key_file=None, encrypted=False))

    rng = random.Random(20261110)
    entries = gen_entries(args.entries, rng)
    t0 = time.perf_counter()
    for text, _ in entries:
        mf.add(text)
    add_s = time.perf_counter() - t0

    # ---- 查询集：三档难度 ----
    qs = {"needle": [], "topic": [], "noise": []}
    for k in range(50):  # 精确锚（唯一 fact/mark）
        i = rng.randrange(args.entries)
        qs["needle"].append((f"关键事实 fact-{i}", [i]))
    for k in range(25):  # 半模糊（主题+实体，多 gold）
        i = rng.randrange(args.entries)
        topic = TOPICS[i % len(TOPICS)]
        entity = entries[i][0].split("领域")[1].split("的")[0]
        golds = [j for j in range(args.entries) if j % len(TOPICS) == i % len(TOPICS)
                 and entity in entries[j][0]]
        qs["topic"].append((f"{topic} {entity}", golds))
    for k in range(25):  # 干扰（不存在）
        qs["noise"].append((f"不存在的幽灵话题 ghost-{k}", []))

    def mrr_at(pred_txts, golds, k=5):
        """内容级 MRR：top-K 中首个命中 gold 的排名倒数。"""
        for rank, t in enumerate(pred_txts[:k], 1):
            if any(f"记录{g}" in t or f"mark-{g}" in t for g in golds):
                return 1.0 / rank
        return 0.0

    res = {}
    for kind, qlist in qs.items():
        hits1 = hits5 = 0
        mrr = 0.0
        lat = []
        for q, golds in qlist:
            t = time.perf_counter()
            r = mf.search(q)
            lat.append((time.perf_counter() - t) * 1000)
            pred_txt = [getattr(c, "content", "") for c in r.chunks[:5]]
            gold_snips = [f"记录{g}" for g in golds[:1]]
            if golds and any(s in t for s in gold_snips for t in pred_txt):
                hits1 += 1
                hits5 += 1
            elif golds and any(f"mark-{g}" in t for g in golds[:1] for t in pred_txt):
                hits5 += 1
            mrr += mrr_at(pred_txt, golds) if golds else 0.0
        n = len(qlist)
        res[kind] = {
            "n": n,
            "recall_at_1": round(hits1 / n, 3),
            "recall_at_5": round(hits5 / n, 3),
            "mrr": round(mrr / n, 3),
            "p50_ms": round(sorted(lat)[len(lat) // 2], 1),
        }

    out = {
        "entries": args.entries,
        "add_s": round(add_s, 1),
        "quality": res,
        "date": time.strftime("%Y-%m-%d"),
        "note": "TF-IDF 降级模式（无 sentence-transformers）；needle=唯一事实锚，"
                "topic=主题+实体多 gold，noise=干扰查询（期望 0 召回）",
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(out, ensure_ascii=False, indent=2))

    mf.close()
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
