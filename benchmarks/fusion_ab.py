# -*- coding: utf-8 -*-
"""融合策略离线 A/B（零 API 成本，conv0 199 问）

背景：裸 cos top-5 命中 38.3%，融合后 30.7% → 融合负优化。
病理：两路分数尺度不可比（BGE 底噪 0.4+ vs TF-IDF 0-0.6），
      向量独立命中用原始余弦入榜，底噪条目挤掉词法真命中。

策略矩阵：
  A 现状模拟: max(text, 0.6*text+0.4*cos)；向量独立条目原始 cos
  B RRF 等权: 1/(60+rank_t) + 1/(60+rank_v)
  C RRF 向量加权 1.5x
  D RRF + 查询自适应（含数字/年份时词法 1.5x）
  E 单路 cos 基线 | F 单路词袋基线
裁判：gold 证据 token 覆盖率 ev>=0.5 占比（与判官正确率强相关）
"""
import json
import re
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, "/home/z/my-project/MindForge")
from mindforge.core.embedding import create_backend  # noqa: E402

DATA = "/home/z/my-project/tmp/locomo/locomo-main/data/locomo10.json"
K = 5
POOL = 60  # 每路取前 POOL 条进融合池

STOP = set("""a an the and or but if so of to in on at by for with from about into
over after before between out during without within is are was were be been being
am do does did have has had having will would can could should may might must not
no yes it its this that these those he she they them his her their our your my i
you we me us as than then there here when where why how what which who whom whose
very much more most many few some any all both each other such only own same too
also just now did does get got go went say said tell told know knows think thought
year years month months week weeks day days time times""".split())


def tokens(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower())
            if (w.isdigit() or len(w) >= 3) and w not in STOP]


emb = create_backend("fastembed")
data = json.load(open(DATA, encoding="utf-8"))
conv = data[0]
seg = conv["conversation"]
turns = [f"{p['speaker']}: {p['utt']}" for p in seg]
T = np.asarray(emb.encode_batch(turns), dtype=np.float32)
T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-9)
print(f"语料 {len(turns)} 轮")

qas, q_texts = [], []
for qa in conv["qa"]:
    if qa.get("category") in (1, 2, 3, 4, 5):
        qas.append(qa)
        q_texts.append(qa["question"])
Q = np.asarray(emb.encode_batch(q_texts), dtype=np.float32)
Q /= np.maximum(np.linalg.norm(Q, axis=1, keepdims=True), 1e-9)
SIM = Q @ T.T  # [n_q, n_turn]

STRATS = ["A_cur", "B_rrf", "C_rrf+vec1.5", "D_rrf+adaptive", "E_cos", "F_lex"]
res = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # strat->cat->[ev,n]


def has_digit(qa):
    return bool(re.search(r"\d", qa["question"]))


for qi, qa in enumerate(qas):
    gold = qa.get("answer") or qa.get("adversarial_answer") or ""
    gt = set(tokens(gold))
    cat = qa.get("category", "?")
    if not gt:
        continue
    q_tokens = set(tokens(qa["question"]))
    lex_scores = np.array([len(q_tokens & set(tokens(t))) for t in turns],
                          dtype=np.float32)
    # 每路 rank（top POOL）
    cos_rank = np.argsort(-SIM[qi])[:POOL]         # turn idx 按余弦降序
    lex_rank = np.argsort(-lex_scores)[:POOL]

    # 每策略选 top-K turn idx 列表
    picks = {}
    # A 现状模拟
    scores = {}
    for r, ti in enumerate(lex_rank):
        scores[ti] = float(lex_scores[ti]) / max(1, len(q_tokens))  # 归一文本分
    for r, ti in enumerate(cos_rank):
        s = float(SIM[qi, ti])
        if s < 0.40:
            continue
        ex = scores.get(ti)
        if ex:
            scores[ti] = max(ex * 0.6 + s * 0.4, ex)
        else:
            scores[ti] = s
    picks["A_cur"] = sorted(scores, key=lambda i: -scores[i])[:K]
    # B RRF 等权
    rrf = defaultdict(float)
    for r, ti in enumerate(lex_rank):
        rrf[ti] += 1.0 / (60 + r + 1)
    for r, ti in enumerate(cos_rank):
        rrf[ti] += 1.0 / (60 + r + 1)
    picks["B_rrf"] = sorted(rrf, key=lambda i: -rrf[i])[:K]
    # C 向量 1.5x
    rrf = defaultdict(float)
    for r, ti in enumerate(lex_rank):
        rrf[ti] += 1.0 / (60 + r + 1)
    for r, ti in enumerate(cos_rank):
        rrf[ti] += 1.5 / (60 + r + 1)
    picks["C_rrf+vec1.5"] = sorted(rrf, key=lambda i: -rrf[i])[:K]
    # D 自适应：数字/年份查询词法加权
    w_lex = 1.5 if has_digit(qa) else 1.0
    rrf = defaultdict(float)
    for r, ti in enumerate(lex_rank):
        rrf[ti] += w_lex / (60 + r + 1)
    for r, ti in enumerate(cos_rank):
        rrf[ti] += 1.0 / (60 + r + 1)
    picks["D_rrf+adaptive"] = sorted(rrf, key=lambda i: -rrf[i])[:K]
    # E/F 基线
    picks["E_cos"] = list(cos_rank[:K])
    picks["F_lex"] = list(lex_rank[:K])

    for st, sel in picks.items():
        pt = set(tokens(" ".join(turns[i] for i in sel)))
        ev = sum(1 for w in gt if w in pt) / len(gt)
        r = res[st][cat]
        r[0] += ev >= 0.5
        r[1] += 1

print(f"\n{'策略':>16} {'ALL':>9} " + " ".join(f"cat{c:>1}" for c in (1, 2, 3, 4, 5)))
for st in STRATS:
    tot = [sum(res[st][c][0] for c in res[st]), sum(res[st][c][1] for c in res[st])]
    row = []
    for c in (1, 2, 3, 4, 5):
        r = res[st].get(c, [0, 0])
        row.append(f"{r[0]/max(1,r[1])*100:4.0f}%" if r[1] else "  --")
    print(f"{st:>16} {tot[0]/tot[1]*100:8.1f}% " + " ".join(row))
