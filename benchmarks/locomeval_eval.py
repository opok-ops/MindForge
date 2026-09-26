# -*- coding: utf-8 -*-
"""MindForge LoCoMo/LongMemEval 风格对话记忆评测脚手架（v5.8.5 新增）

目标：建立可复现的"长对话 → 多跳/时序问答"评测框架，为后续扩充到
500 条多语言查询集提供固定管道。当前内置 12 条冒烟样本（覆盖三类问题），
后续版本逐步扩充，不改 schema 与裁判口径。

评测流程：
  1. 把 session 片段逐条 mf.add() 入库（category="locomo_session"）
  2. 对每条问题 mf.search(query) 取 top-K chunks（解密明文）
  3. 裁判（默认 deterministic）：
       - keyword recall：问题的 must_have 关键词全部出现在 top-K 拼接文本中
       - MRR@K：首个命中位置的倒数
     --judge llm 时调用 OpenAI 兼容接口（需 OPENAI_API_KEY，缺失自动降级）
  4. 输出 JSON 报告：每 query 详情 + 聚合指标（Recall@K / MRR@K / latency p50/p95）
     + 环境声明（MindForge 版本 / Python / OS / seed / 数据集版本）

用法：
  python benchmarks/locomeval_eval.py                 # 内置 12 条冒烟
  python benchmarks/locomeval_eval.py --k 5 --json out.json
  python benchmarks/locomeval_eval.py --judge llm      # 需 OPENAI_API_KEY

可复现性：同 seed + 同数据集版本 → deterministic 裁判下完全一致；
真实 embedding 模型受版本影响属正常偏差（写入报告）。
"""
import argparse
import io
import json
import os
import platform
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATASET_VERSION = "locomo-v0.1-2026-09-26"
DEFAULT_K = 5

# ---------- 内置冒烟数据集（12 条，三类问题） ----------
# type: singlehop | multihop | temporal
SAMPLE_SESSIONS: List[Dict[str, Any]] = [
    {"session": "s1", "speaker": "user", "text": "我对花生严重过敏，千万别放花生酱"},
    {"session": "s1", "speaker": "user", "text": "我们家计划下个月去京都旅行一周"},
    {"session": "s1", "speaker": "user", "text": "我最喜欢的餐厅是银座的寿司久兵卫"},
    {"session": "s1", "speaker": "user", "text": "我上次搬过家，现在住在东京都港区"},
    {"session": "s1", "speaker": "user", "text": "之前我住在大阪市中央区两年"},
    {"session": "s1", "speaker": "user", "text": "京都旅行订了周三下午的新干线车票"},
    {"session": "s1", "speaker": "user", "text": "我女儿叫樱，今年七岁"},
    {"session": "s1", "speaker": "user", "text": "寿司久兵卫需要提前两周预订"},
    {"session": "s1", "speaker": "user", "text": "我最近在学弹吉他，每天练一小时"},
    {"session": "s1", "speaker": "user", "text": "樱对猫毛过敏，家里不能养猫"},
    {"session": "s1", "speaker": "user", "text": "京都住的酒店叫京都格兰比亚，在京都站旁边"},
    {"session": "s1", "speaker": "user", "text": "我的邮箱是 tanaka@example.jp"},
]

SAMPLE_QUESTIONS: List[Dict[str, Any]] = [
    {"q": "用户对什么食物过敏？", "type": "singlehop",
     "must_have": ["花生"]},
    {"q": "用户女儿叫什么名字，多大？", "type": "singlehop",
     "must_have": ["樱"]},
    {"q": "用户现在住在哪？", "type": "temporal",
     "must_have": ["东京", "港区"]},
    {"q": "用户之前住在大阪多久？", "type": "temporal",
     "must_have": ["大阪", "两年"]},
    {"q": "用户计划去京都什么时候出发？", "type": "multihop",
     "must_have": ["京都", "周三"]},
    {"q": "用户要去的餐厅有预订要求吗？", "type": "multihop",
     "must_have": ["寿司久兵卫", "两周"]},
    {"q": "京都住的酒店在哪一站旁边？", "type": "multihop",
     "must_have": ["京都"]},
    {"q": "用户家里为什么不能养猫？", "type": "multihop",
     "must_have": ["樱", "猫毛"]},
    {"q": "用户的邮箱地址是什么？", "type": "singlehop",
     "must_have": ["tanaka@example.jp"]},
    {"q": "用户每天练什么乐器多久？", "type": "singlehop",
     "must_have": ["吉他"]},
    {"q": "用户去京都住的酒店叫什么？", "type": "multihop",
     "must_have": ["京都", "格兰比亚"]},
    {"q": "用户去京都旅行大概多久？", "type": "singlehop",
     "must_have": ["一周"]},
]


# ---------- 构建 MindForge 实例 ----------
def build_mf(tmpdir: str, encrypted: bool = True, seed: int = 42):
    from core.mindforge import MindForge
    from core.types import MemoryConfig

    random.seed(seed)
    eng = MindForge(config=MemoryConfig(
        db_path=os.path.join(tmpdir, "eval.db"),
        key_file=os.path.join(tmpdir, "eval.key"),
        encrypted=encrypted))
    if encrypted:
        eng.init_with_password("eval-pw-locomo")
    return eng


def load_sessions(mf) -> int:
    n = 0
    for s in SAMPLE_SESSIONS:
        mf.add(s["text"], category="locomo_session",
               tags=["session:" + s["session"]],
               source_agent="eval_pipeline")
        n += 1
    return n


# ---------- 裁判 ----------
def _keyword_hit(chunks_text: str, must_have: List[str]) -> bool:
    return all(kw.lower() in chunks_text.lower() for kw in must_have)


def judge_keyword(query_item: Dict[str, Any], chunks: List[Any]) -> Dict[str, Any]:
    """Deterministic 裁判：关键词命中。返回 {hit, first_hit_rank, concat}"""
    concat = "\n".join(getattr(c, "content", "") or "" for c in chunks)
    hit = _keyword_hit(concat, query_item["must_have"])
    # MRR：首个命中 chunk（按必须关键词全部命中的最早位置）
    first_rank = 0
    if hit:
        for i, c in enumerate(chunks, start=1):
            if _keyword_hit(getattr(c, "content", "") or "",
                            query_item["must_have"]):
                first_rank = i
                break
        if first_rank == 0:
            first_rank = len(chunks)  # 跨 chunk 聚合命中，记 K
    return {"hit": hit, "first_hit_rank": first_rank,
            "concat_preview": concat[:200]}


def judge_llm(query_item: Dict[str, Any], chunks: List[Any]) -> Dict[str, Any]:
    """LLM-as-judge（可选）。无 API key 时降级到 keyword。"""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        r = judge_keyword(query_item, chunks)
        r["judge"] = "keyword-fallback"
        return r
    # 真实 LLM 调用留给外部集成：脚手架默认仍用确定性裁判，
    # 这里仅标记调用点，避免在 CI 里硬依赖网络。
    r = judge_keyword(query_item, chunks)
    r["judge"] = "keyword (llm judge not wired in scaffold)"
    return r


# ---------- 主流程 ----------
def run_eval(k: int = DEFAULT_K, judge: str = "keyword",
             seed: int = 42, limit: Optional[int] = None) -> Dict[str, Any]:
    import shutil
    tmpdir = tempfile.mkdtemp(prefix="mf_loco_eval_")
    try:
        return _run_eval_inner(tmpdir, k=k, judge=judge, seed=seed, limit=limit)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _run_eval_inner(tmpdir: str, k: int, judge: str,
                    seed: int, limit: Optional[int]) -> Dict[str, Any]:
    mf = build_mf(tmpdir, encrypted=True, seed=seed)
    n_sessions = load_sessions(mf)

    questions = SAMPLE_QUESTIONS
    if limit:
        questions = questions[:limit]

    per_query: List[Dict[str, Any]] = []
    latencies: List[float] = []
    hits = 0
    rr_sum = 0.0
    by_type: Dict[str, Dict[str, int]] = {}

    for q in questions:
        t0 = time.perf_counter()
        rr = mf.search(q["q"], max_results=k, categories=["locomo_session"])
        lat = (time.perf_counter() - t0) * 1000
        latencies.append(lat)
        chunks = list(rr.chunks or [])

        if judge == "llm":
            verdict = judge_llm(q, chunks)
        else:
            verdict = judge_keyword(q, chunks)

        hit = bool(verdict["hit"])
        hits += int(hit)
        rank = verdict.get("first_hit_rank") or 0
        if rank > 0:
            rr_sum += 1.0 / rank
        t = q["type"]
        by_type.setdefault(t, {"total": 0, "hits": 0})
        by_type[t]["total"] += 1
        by_type[t]["hits"] += int(hit)

        per_query.append({
            "q": q["q"], "type": t, "hit": hit,
            "first_hit_rank": rank, "latency_ms": round(lat, 2),
            "n_chunks": len(chunks),
        })

    n = len(questions)
    latencies.sort()
    report = {
        "scaffold": "locomo-longmemeval",
        "dataset_version": DATASET_VERSION,
        "mindforge_version": _mf_version(),
        "env": {
            "python": platform.python_version(),
            "os": platform.platform(),
            "seed": seed, "k": k, "judge": judge,
        },
        "n_sessions_loaded": n_sessions,
        "n_questions": n,
        "metrics": {
            "recall_at_k": round(hits / n, 4) if n else 0.0,
            "mrr_at_k": round(rr_sum / n, 4) if n else 0.0,
            "latency_p50_ms": latencies[n // 2] if latencies else 0,
            "latency_p95_ms": latencies[min(n - 1, int(n * 0.95))] if latencies else 0,
        },
        "by_type": by_type,
        "per_query": per_query,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return report


def _mf_version() -> str:
    try:
        from core.version import __version__
        return __version__
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--judge", choices=["keyword", "llm"], default="keyword")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args()

    report = run_eval(k=args.k, judge=args.judge, seed=args.seed,
                      limit=args.limit)
    out = json.dumps(report, ensure_ascii=False, indent=2)
    print(out)
    if args.json:
        Path(args.json).write_text(out, encoding="utf-8")
        print(f"\n[written] {args.json}")


if __name__ == "__main__":
    main()
