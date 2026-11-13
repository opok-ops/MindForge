# -*- coding: utf-8 -*-
"""离线证据命中率诊断（零 API 成本）

问题：LoCoMo acc=24.2%，判官双峰（捞到证据≈满分）。
本脚本量化：每问的 gold 证据是否出现在 top-K 检索结果里，
把失分拆成「检索的锅」vs「组装的锅」。

方法：gold answer 的 distinctive token（数字/专名/实词）在
top-K 召回文本中的覆盖率 -> evidence_recall。分桶对照 correct。
用法: python offline_evidence_diag.py [--embedding fastembed] [--k 5]
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA = "/home/z/my-project/tmp/locomo/locomo-main/data/locomo10.json"

STOP = set("""a an the and or but if so of to in on at by for with from about into
over after before between out during without within is are was were be been being
am do does did have has had having will would can could should may might must not
no yes it its this that these those he she they them his her their our your my i
you we me us as than then there here when where why how what which who whom whose
very much more most many few some any all both each other such only own same too
also just now""".split())


def tokens(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower())
            if (w.isdigit() or len(w) >= 3) and w not in STOP]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--convs", type=int, default=1)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--embedding", default="")
    ap.add_argument("--expand", type=int, default=8,
                    help="检索池大小（取前 k 做命中分析）")
    args = ap.parse_args()

    if args.embedding:
        os.environ["MINDFORGE_EMBEDDING_BACKEND"] = args.embedding
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    data = json.load(open(DATA, encoding="utf-8"))
    from mindforge.core.mindforge import MindForge
    from mindforge.core.types import MemoryConfig

    from collections import defaultdict
    buckets = defaultdict(lambda: dict(n=0, ev=0, ev_strong=0, corr=0))
    lat = []
    for ci in range(min(args.convs, len(data))):
        conv = data[ci]
        cid = conv.get("sample_id", f"conv{ci}")
        tmp = tempfile.mkdtemp()
        mf = MindForge(config=MemoryConfig(
            db_path=f"{tmp}/mf.db", key_file=None, encrypted=False))
        seg = conv.get("conversation", {})
        turn_text = []  # (idx, text) 全轮索引，用于 gold 全文定位
        for day in sorted(k for k in seg if k.startswith("session_")
                          and not k.endswith("_date_time")):
            for t in seg[day]:
                content = f"{t.get('speaker','?')}: {t.get('text','')}"
                mf.add(content, category="locomo_session",
                       tags=[f"day:{day}", f"conv:{cid}"])
                turn_text.append(content)
        qas = conv.get("qa", [])
        if args.embedding:
            t0 = time.perf_counter()
            r = mf.rebuild_embeddings()
            print(f"[{cid}] 向量 {r.get('embedded', 0)} 条 "
                  f"({time.perf_counter()-t0:.1f}s)")
        # gold 在全文的覆盖率（gold 本身是否真的来自对话）
        all_tokens = set()
        for txt in turn_text:
            all_tokens.update(tokens(txt))
        for qi, qa in enumerate(qas):
            gold = str(qa.get("answer") or qa.get("adversarial_answer") or "")
            gt = tokens(gold)
            gold_in_corpus = (sum(1 for w in gt if w in all_tokens)
                              / len(gt)) if gt else 0
            t0 = time.perf_counter()
            hits = mf.search(qa["question"], max_results=args.expand)
            lat.append((time.perf_counter() - t0) * 1000)
            pool = " ".join(h.content for h in hits.chunks[:args.k])
            pt = set(tokens(pool))
            ev_recall = (sum(1 for w in gt if w in pt) / len(gt)) if gt else 0
            cat = qa.get("category", "?")
            b = buckets[cat]
            b["n"] += 1
            b["ev"] += ev_recall >= 0.5
            b["ev_strong"] += ev_recall >= 0.8
        mf.close()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    lat.sort()
    print(f"\n== 证据命中率诊断 (k={args.k}, pool={args.expand}, "
          f"emb={args.embedding or 'lexical'}) ==")
    print(f"检索 p50 {lat[len(lat)//2]:.1f}ms")
    print(f"{'cat':>4} {'n':>5} {'ev>=0.5':>9} {'ev>=0.8':>9}")
    tot = dict(n=0, ev=0, ev_strong=0, corr=0)
    for c in sorted(buckets):
        b = buckets[c]
        for kk in tot:
            tot[kk] += b[kk]
        print(f"{c:>4} {b['n']:>5} {b['ev']/b['n']*100:>8.1f}% "
              f"{b['ev_strong']/b['n']*100:>8.1f}%")
    n = tot["n"]
    print(f"{'ALL':>4} {n:>5} {tot['ev']/n*100:>8.1f}% "
          f"{tot['ev_strong']/n*100:>8.1f}%")
    print(f"\n判官正确率上限参照: ev>=0.5 桶 {tot['ev']/n*100:.1f}% "
          f"vs 实测 acc 20.6%")


if __name__ == "__main__":
    main()
