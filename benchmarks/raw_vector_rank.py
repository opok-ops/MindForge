# -*- coding: utf-8 -*-
"""裸向量排序 vs 融合排序 命中率对照（零 API 成本）

若裸向量命中显著高于融合后 -> 融合权重稀释是 bug；
若裸向量也低 -> BGE-small 嵌入质量受限。
"""
import json
import re
import sys
import numpy as np

sys.path.insert(0, "/home/z/my-project/MindForge")
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


from mindforge.core.embedding import create_backend  # noqa: E402

emb = create_backend("fastembed")

data = json.load(open(DATA, encoding="utf-8"))
conv = data[0]
seg = conv["conversation"]
turns = []
for day in sorted(k for k in seg if k.startswith("session_")
                  and not k.endswith("_date_time")):
    for t in seg[day]:
        turns.append(f"{t.get('speaker','?')}: {t.get('text','')}")

t = emb.encode(turns)
M = np.asarray(t, dtype=np.float32)
norms = np.linalg.norm(M, axis=1, keepdims=True)
norms[norms == 0] = 1.0
M = M / norms
print(f"语料 {len(turns)} 轮，维度 {M.shape[1]}")

from collections import defaultdict
K = 5
res = defaultdict(lambda: dict(n=0, cos=0, lex=0))
qas = conv.get("qa", [])
qs = [qa["question"] for qa in qas]
Q = np.asarray(emb.encode(qs), dtype=np.float32)
Qn = np.linalg.norm(Q, axis=1, keepdims=True)
Qn[Qn == 0] = 1.0
Q = Q / Qn
S = Q @ M.T  # [n_q, n_turns]
order = np.argsort(-S, axis=1)

# 词法基线：简单词袋重叠重排（近似 MindForge 词法路）
for qi, qa in enumerate(qas):
    gold = str(qa.get("answer") or qa.get("adversarial_answer") or "")
    gt = set(tokens(gold))
    if not gt:
        continue
    cat = qa.get("category", "?")
    # 裸向量 top-K
    top_cos = [turns[j] for j in order[qi][:K]]
    pt = set(tokens(" ".join(top_cos)))
    res[cat]["cos"] += sum(1 for w in gt if w in pt) / len(gt) >= 0.5
    # 词袋重叠 top-K（近似词法）
    q_tokens = set(tokens(qa["question"]))
    scores = [len(q_tokens & set(tokens(tx))) for tx in turns]
    top_lex = sorted(range(len(turns)), key=lambda i: -scores[i])[:K]
    pt2 = set(tokens(" ".join(turns[i] for i in top_lex)))
    res[cat]["lex"] += sum(1 for w in gt if w in pt2) / len(gt) >= 0.5
    res[cat]["n"] += 1

print(f"{'cat':>4} {'n':>5} {'裸cos>=0.5':>11} {'词袋>=0.5':>10}")
tn = tc = tl = 0
for c in sorted(res):
    r = res[c]
    tn += r["n"]; tc += r["cos"]; tl += r["lex"]
    print(f"{c:>4} {r['n']:>5} {r['cos']/r['n']*100:>10.1f}% "
          f"{r['lex']/r['n']*100:>9.1f}%")
print(f"{'ALL':>4} {tn:>5} {tc/tn*100:>10.1f}% {tl/tn*100:>9.1f}%")
