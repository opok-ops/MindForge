# -*- coding: utf-8 -*-
"""LoCoMo 证据组装层实验（v6.5.3）：ctxwin 窗口扩展 + 时间锚 + 拒答防御

对照基线：locomo_deepseek_c1_k5_fastembed.jsonl（4.51 avg / 20.6% acc）
组装层改动（检索口径完全不动）：
  1. 入库 tags 加 turn 坐标（全局序 + dia_id）
  2. ctx 构造：命中轮 ±ctxwin 轮窗口 + session 日期头
  3. GEN_PROMPT 加拒答防御（adversarial cat5）

用法：
  python benchmarks/locomo_ctxwin_eval.py --convs 1 --k 5 --ctxwin 2
"""
import argparse
import json
import os
import sys
import time
import tempfile
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA = "/home/z/my-project/tmp/locomo/locomo-main/data/locomo10.json"
KEYFILE = os.path.expanduser("~/.secrets/deepseek.key")
API = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

GEN_PROMPT = """基于以下记忆片段回答问题。只给出简洁的最终答案，不要解释。

规则：
- 片段中明确相关信息缺失时才回答：not mentioned in the conversation
- 合理推断（would/likely/what might）应基于证据作答，不要轻易拒答
- 不要预设问题中的事物一定存在
- 相对时间（ago/before/last）一律结合片段日期与参考日期精确换算
- 参考日期（最近一次对话）：{now}

记忆片段：
{ctx}

问题：{q}
答案："""

JUDGE_PROMPT = """你是评测判官。根据金标准答案给系统生成的答案打分。

问题：{q}
金标准答案：{gold}
系统生成答案：{pred}

评分标准（1-10 整数）：
- 10：完全正确（含时间/数量等细节全对）
- 7-9：核心正确但有细节瑕疵或表述含糊
- 4-6：部分正确
- 1-3：错误或未回答


只输出一个整数分数。"""


def llm_call(messages, key, max_tokens=400, retries=5):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API,
                data=json.dumps({"model": MODEL, "messages": messages,
                                 "max_tokens": max_tokens,
                                 "temperature": 0.2}).encode(),
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"})
            out = json.load(urllib.request.urlopen(req, timeout=90))
            return out["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt == retries - 1:
                return f"__LLM_ERROR__ {type(e).__name__}"
            time.sleep(3 * (attempt + 1))
    return "__LLM_ERROR__"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--convs", type=int, default=1)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--ctxwin", type=int, default=2,
                    help="命中轮上下文窗口半径（0=旧行为）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 问（试水）")
    ap.add_argument("--no-refuse", action="store_true",
                    help="关闭拒答防御（对照）")
    args = ap.parse_args()

    out_path = args.out or (
        "benchmarks/results/locomo_ctxwin_"
        f"{'w' + str(args.ctxwin) if args.ctxwin else 'base'}"
        f"{'_norefuse' if args.no_refuse else ''}.jsonl")
    done = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["qid"])
                except Exception:
                    pass
    print(f"断点续跑：已完成 {len(done)} 问")

    key = open(KEYFILE).read().strip()
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ["MINDFORGE_EMBEDDING_BACKEND"] = "fastembed"

    data = json.load(open(DATA, encoding="utf-8"))
    from mindforge.core.mindforge import MindForge
    from mindforge.core.types import MemoryConfig

    scores, lat, correct = [], [], 0
    n_new = 0
    for ci in range(min(args.convs, len(data))):
        conv = data[ci]
        cid = conv.get("sample_id", f"conv{ci}")
        tmp = tempfile.mkdtemp()
        mf = MindForge(config=MemoryConfig(
            db_path=f"{tmp}/mf.db", key_file=None, encrypted=False))
        seg = conv.get("conversation", {})
        turns = []       # (session, idx_in_session, speaker, text)
        sess_turns = {}  # session -> [ (speaker, text) ]
        sess_dates = {}
        for day in sorted(k for k in seg if k.startswith("session_")
                          and not k.endswith("_date_time")):
            sess_dates[day] = seg.get(f"{day}_date_time", "")
            sess_turns[day] = [(t.get("speaker", "?"), t.get("text", ""))
                               for t in seg[day]]
            for i, t in enumerate(seg[day]):
                turns.append((day, i, t.get("speaker", "?"),
                              t.get("text", ""), t.get("dia_id", "")))

        for (day, i, sp, tx, dia) in turns:
            d = sess_dates.get(day, "")
            mf.add(f"{sp}: {tx}",
                   category="locomo_session",
                   tags=[f"day:{day}", f"conv:{cid}",
                         f"ti:{i}", f"dia:{dia}",
                         f"ctxgrp:{cid}-{day}", f"ctxseq:{i}"]
                   + ([f"ctxdate:{d}"] if d else []))

        qas = [qa for qa in conv.get("qa", [])
               if str(qa.get("category")) in ("1", "2", "3", "4", "5")]
        if args.limit:
            qas = qas[:args.limit]

        with open(out_path, "a", encoding="utf-8") as out_f:
            for qi, qa in enumerate(qas):
                qid = f"{cid}-q{qi}"
                if qid in done:
                    continue
                t0 = time.perf_counter()
                hits = mf.search(qa["question"], max_results=args.k,
                                 expand_context=args.ctxwin)
                lat.append((time.perf_counter() - t0) * 1000)

                # ---- 组装层：窗口扩展 ----
                ctx_parts = []
                seen_blocks = set()
                for h in hits.chunks[:args.k]:
                    tags = {t.split(":", 1)[0]: t.split(":", 1)[1]
                            for t in (h.tags or [])
                            if ":" in t}
                    day = tags.get("day", "")
                    ti = tags.get("ti", "")
                    if h.context_expansion:
                        date = sess_dates.get(day, "")
                        block = [f"[{date}]"]
                        for seq, text in h.context_expansion:
                            block.append(text)
                        ctx_parts.append("\n".join(block))
                    else:
                        ctx_parts.append(f"- {h.content}")
                ctx = "\n\n".join(ctx_parts)

                now_ref = max(
                    (d for d in sess_dates.values() if d), default="") or ""
                gen = llm_call(
                    [{"role": "user",
                      "content": GEN_PROMPT.format(
                          ctx=ctx, q=qa["question"], now=now_ref)}],
                    key, max_tokens=200)
                gold_raw = qa.get("answer")
                is_adv = str(qa.get("category")) == "5"
                if is_adv:
                    # v6.5.3：adversarial 题金标准语义直换（对齐 LoCoMo
                    # 原意：测幻觉，正确行为=承认未提及）
                    gold_for_judge = (
                        "对话中未提及该信息；正确行为是回答"
                        "『not mentioned / 无法从对话确认』")
                else:
                    gold_for_judge = gold_raw or ""
                score_s = llm_call(
                    [{"role": "user", "content": JUDGE_PROMPT.format(
                        q=qa["question"],
                        gold=gold_for_judge,
                        pred=gen)}], key, max_tokens=8)
                try:
                    sc = int("".join(c for c in score_s
                                     if c.isdigit()) or 0)
                except ValueError:
                    sc = 0
                sc = min(10, max(0, sc))
                ok = sc >= 9
                rec = {"qid": qid, "cat": qa.get("category", "?"),
                       "q": qa["question"], "gold": qa.get("answer")
                       or qa.get("adversarial_answer") or "",
                       "pred": gen, "judge": sc, "correct": ok,
                       "ctxwin": args.ctxwin}
                out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out_f.flush()
                scores.append(sc)
                correct += ok
                n_new += 1
                if n_new % 20 == 0:
                    print(f"  {n_new} 问 | 均分 {sum(scores)/len(scores):.2f}"
                          f" | 正确率 {correct/len(scores)*100:.1f}%")
        mf.close()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    if scores:
        lat.sort()
        print(f"\n== 汇总（ctxwin={args.ctxwin}, k={args.k}）==")
        print(f"新评 {n_new} 问")
        print(f"LLM 判官均分: {sum(scores)/len(scores):.2f} / 10")
        print(f"正确率（>=9 分）: {correct/len(scores)*100:.1f}%")
        print(f"检索延迟 p50: {lat[len(lat)//2]:.1f}ms")
        print(f"结果: {out_path}")


if __name__ == "__main__":
    main()
