# -*- coding: utf-8 -*-
"""MindForge × LoCoMo 公开基准评测（DeepSeek LLM 判官，v6.4.9 新增）

口径对齐 Mem0/Zep 公开评测玩法：
  1. LoCoMo 原集 10 长对话，逐轮入库（mf.add，category=locomo_session）
  2. 每问 mf.search(question, top_k=K) 取记忆片段
  3. LLM 生成答案（top-K 片段为上下文，deepseek-chat）
  4. LLM 判官对「生成答案 vs 金标准答案」打分 1-10（>=9 记正确，
     Mem0 式 LLM-as-judge）

成本控制：--convs 1 先试水（199 问 ≈ ¥1.2），全量 10 对话 ≈ ¥12。
断点续跑：结果逐问 append JSONL，重跑自动跳过已完成。

环境变量：
  DEEPSEEK_API_KEY  必填（运行时注入，不落盘）
用法：
  python benchmarks/locomo_deepseek_eval.py --convs 1 --k 5
  python benchmarks/locomo_deepseek_eval.py --convs 10 --k 5   # 全量
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA = "/home/z/my-project/tmp/locomo/locomo-main/data/locomo10.json"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"


def llm_call(messages, key, max_tokens=400, retries=5):
    body = json.dumps({"model": MODEL, "messages": messages,
                       "max_tokens": max_tokens, "temperature": 0.0}).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API_URL, data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=90) as r:
                out = json.loads(r.read())
            return out["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt == retries - 1:
                return f"__LLM_ERROR__ {type(e).__name__}"
            time.sleep(3 * (attempt + 1))
    return "__LLM_ERROR__"


GEN_PROMPT = """基于以下记忆片段回答问题。只给出简洁的最终答案，不要解释。

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

# v6.5.0 MemoryCompressor LLM 抽取式入库（Mem0 式预处理，架构验证实验）
EXTRACT_PROMPT = """从以下对话片段中提取所有可独立检索的原子事实。

要求：
- 每条事实一个自然语言短句（含说话人、对象、时间/地点/数字等细节）
- 只提取明确陈述的事实，不要推测
- 覆盖：事件、偏好、关系、计划、状态变化、时间点
- 时间一律写成绝对日期（如 8 May 2023），把 today/yesterday/last week
  按「本段对话日期」换算
- 输出 JSON 数组，每个元素 {{"speaker": "...", "fact": "..."}}，无事实输出 []

本段对话日期：{session_date}

对话片段：
{turns}

JSON 输出："""


def llm_extract_facts(turns, key, batch=12, session_dates=None):
    """批量抽取对话轮的原子事实。返回 [{speaker, fact}, ...]

    v6.5.0 修正：session_dates 提供每轮所属 session 的绝对日期
    （temporal 题型换算锚点）；无日期信息时退化为无锚抽取。
    """
    facts = []
    for i in range(0, len(turns), batch):
        chunk = turns[i:i + batch]
        text = "\n".join(f"{t['speaker']}: {t['text']}" for t in chunk)
        # v6.5.0：用本批首轮的 session 日期作换算锚
        date = (session_dates or {}).get(chunk[0].get("session", ""), "")
        out = llm_call(
            [{"role": "user", "content": EXTRACT_PROMPT.format(
                turns=text, session_date=date or "未知")}],
            key, max_tokens=1500)
        if out.startswith("__LLM_ERROR__"):
            continue
        # 提取 JSON 数组（容忍 markdown 代码围栏）
        s, e = out.find("["), out.rfind("]")
        if s < 0 or e <= s:
            continue
        try:
            items = json.loads(out[s:e + 1])
            for it in items:
                if isinstance(it, dict) and it.get("fact"):
                    facts.append({
                        "speaker": str(it.get("speaker", "?")),
                        "fact": str(it["fact"]).strip(),
                        "turn_idx": i,  # 源轮偏移（审计追溯用）
                    })
        except Exception:
            continue
    return facts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--convs", type=int, default=1, help="跑前 N 个对话")
    ap.add_argument("--k", type=int, default=5, help="检索 top-K")
    ap.add_argument("--out", default=None, help="结果 JSONL 路径")
    ap.add_argument("--embedding", default="",
                    help="向量后端（空=词法降级；fastembed=本地 ONNX 向量）")
    ap.add_argument("--extract", action="store_true",
                    help="LLM 抽取式入库（v6.5 MemoryCompressor 管道验证）")
    ap.add_argument("--seats", action="store_true",
                    help="分席融合：top-K 强制保留原始轮席位（默认同权混排，"
                         "conv-26 实验证明混排更优）")
    args = ap.parse_args()

    # v6.5.0：向量口径。须在 MindForge 导入前设置环境变量
    if args.embedding:
        os.environ["MINDFORGE_EMBEDDING_BACKEND"] = args.embedding
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        print(f"向量后端: {args.embedding}")

    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print("缺 DEEPSEEK_API_KEY（运行前 export）")
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    suffix = f"_{args.embedding}" if args.embedding else ""
    if args.extract:
        suffix += "_ext"
    out_path = args.out or os.path.join(
        OUT_DIR, f"locomo_deepseek_c{args.convs}_k{args.k}{suffix}.jsonl")
    done = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["qid"])
                except Exception:
                    pass
    print(f"断点续跑：已完成 {len(done)} 问")

    data = json.load(open(DATA, encoding="utf-8"))
    from mindforge.core.mindforge import MindForge
    from mindforge.core.types import MemoryConfig
    import tempfile

    scores, lat, correct = [], [], 0
    n_new = 0
    for ci in range(min(args.convs, len(data))):
        conv = data[ci]
        cid = conv.get("sample_id", f"conv{ci}")
        tmp = tempfile.mkdtemp()
        mf = MindForge(config=MemoryConfig(
            db_path=f"{tmp}/mf.db", key_file=None, encrypted=False))
        # 入库：逐轮（speaker+text）。结构为 session_N 平铺列表
        seg = conv.get("conversation", {})
        n_turns = 0
        all_turns = []  # v6.5.0：收集原始轮（--extract 抽取用）
        session_dates = {}  # v6.5.0：session_N → 绝对日期（时间锚）
        for day in sorted(k for k in seg if k.startswith("session_")
                          and not k.endswith("_date_time")):
            session_dates[day] = seg.get(f"{day}_date_time", "")
            for t in seg[day]:
                mf.add(f"{t.get('speaker','?')}: {t.get('text','')}",
                       category="locomo_session",
                       tags=[f"day:{day}", f"conv:{cid}"])
                all_turns.append({"speaker": t.get('speaker', '?'),
                                  "text": t.get('text', ''),
                                  "session": day})
                n_turns += 1
        # v6.5.0 MemoryCompressor 管道：LLM 抽取原子事实入库
        n_facts = 0
        if args.extract:
            t_ext = time.perf_counter()
            facts = llm_extract_facts(all_turns, key,
                                      session_dates=session_dates)
            for f in facts:
                mf.add(f"{f['speaker']}: {f['fact']}",
                       category="locomo_fact",
                       tags=[f"conv:{cid}", f"turn:{f['turn_idx']}"])
                n_facts += 1
            print(f"  LLM 抽取: {n_facts} 条原子事实"
                  f"（{time.perf_counter()-t_ext:.0f}s）")
        qas = conv.get("qa", [])
        emb_stat = ""
        if args.embedding:
            t_emb = time.perf_counter()
            r = mf.rebuild_embeddings()
            emb_stat = (f"，向量 {r.get('embedded', 0)} 条"
                        f"（{time.perf_counter()-t_emb:.1f}s）")
        print(f"[{cid}] {n_turns} 轮入库，{len(qas)} 问{emb_stat}")
        for qi, qa in enumerate(qas):
            qid = f"{cid}:{qi}"
            if qid in done:
                continue
            t0 = time.perf_counter()
            hits = mf.search(qa["question"],
                             max_results=args.k * 2 if args.seats else args.k)
            dt_ms = (time.perf_counter() - t0) * 1000
            # v6.5.0 融合策略（conv-26 四轮对照实验结论）：
            # - 默认同权混排（轮 3 最优：temporal +1.30 / adversarial +0.30）
            # - --seats 分席（轮 4 实验证明负优化，保留供复现对照）：
            #   时间锚使抽取更保守（583 vs 697 条）+ 原始轮被挤出召回池
            #   造成上下文缩水，temporal 反崩 -2.30
            if args.seats and args.extract:
                sessions = [h for h in hits.chunks
                            if h.category == "locomo_session"]
                facts_hit = [h for h in hits.chunks
                             if h.category == "locomo_fact"]
                n_fact_seats = max(1, args.k // 3)  # k=5 → 2
                picked = (sessions[:args.k - n_fact_seats]
                          + facts_hit[:n_fact_seats])
            else:
                picked = hits.chunks[:args.k]
            ctx = "\n".join(f"- {h.content}" for h in picked[:args.k])
            gen = llm_call([{"role": "user", "content": GEN_PROMPT.format(
                ctx=ctx, q=qa["question"])}], key, max_tokens=200)
            score_s = llm_call([{"role": "user", "content": JUDGE_PROMPT.format(
                q=qa["question"],
                gold=qa.get("answer") or qa.get("adversarial_answer") or "",
                pred=gen)}], key,
                max_tokens=8)
            try:
                sc = int("".join(c for c in score_s if c.isdigit()) or 0)
            except ValueError:
                sc = 0
            ok = sc >= 9
            rec = {"qid": qid, "cat": qa.get("category", "?"),
                   "q": qa["question"],
                   "gold": str(qa.get("answer")
                               or qa.get("adversarial_answer") or ""),
                   "pred": gen, "judge": sc, "correct": ok,
                   "search_ms": round(dt_ms, 2)}
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            scores.append(sc); lat.append(dt_ms); correct += ok
            n_new += 1
            if n_new % 20 == 0:
                print(f"  {n_new} 问 | 均分 {sum(scores)/len(scores):.2f}"
                      f" | 正确率 {correct/len(scores)*100:.1f}%"
                      f" | 检索 p50 {sorted(lat)[len(lat)//2]:.1f}ms")
        mf.close()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    if scores:
        lat.sort()
        print("\n== 汇总 ==")
        print(f"新评 {n_new} 问（含断点前共 {len(done)+n_new}）")
        print(f"LLM 判官均分: {sum(scores)/len(scores):.2f} / 10")
        print(f"正确率（>=9 分）: {correct/len(scores)*100:.1f}%")
        print(f"检索延迟 p50: {lat[len(lat)//2]:.1f}ms  p95: "
              f"{lat[int(len(lat)*0.95)]:.1f}ms")
        print(f"结果: {out_path}")
    else:
        print("无新增（全部已完成）")


if __name__ == "__main__":
    main()
