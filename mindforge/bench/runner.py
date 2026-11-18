# -*- coding: utf-8 -*-
"""评测编排：三段式管道 + 会话分组 + 环境声明。

口径细节：
- 按会话分组（meta.conv_id，缺省 "default"），每组独立记忆库
  （backend.reset()）——与 v6.5.12 LoCoMo 全量管道同口径
  （每会话独立 MindForge 实例，防跨会话串记忆）。
- LLM 判官失败显式记 score=0，报告里 judge_failures 计数，
  绝不静默漂白。
"""
import platform
import random
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from mindforge.bench.metrics import aggregate


def _env_decl(args: Dict[str, Any], dataset_id: str) -> Dict[str, Any]:
    from mindforge.core.version import __version__
    return {
        "bench_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "seed": args.get("seed"),
        "dataset": dataset_id,
        "backend": args.get("backend"),
        "answerer": args.get("answerer"),
        "judge": args.get("judge"),
        "k": args.get("k"),
        "encrypted": bool(args.get("encrypted")),
    }


def run_benchmark(items: List[Dict[str, Any]], backend: Any,
                  answerer: Any, judge: Any, k: int = 5,
                  seed: Optional[int] = None,
                  progress: Optional[Callable[[str], None]] = None,
                  dataset_id: str = "", args_decl: Optional[Dict[str, Any]] = None
                  ) -> Dict[str, Any]:
    """执行评测，返回完整 JSON 报告（含逐问明细 + 聚合 + 环境声明）。"""
    if seed is not None:
        random.seed(seed)

    # 按会话分组，保持组内顺序
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it.get("meta", {}).get("conv_id", "default"),
                          []).append(it)

    records: List[Dict[str, Any]] = []
    for gid, gitems in groups.items():
        turns = gitems[0].get("source_turns") or []
        if progress:
            progress(f"[{gid}] 入库 {len(turns)} 轮 / {len(gitems)} 问")
        backend.reset()
        backend.add(turns, category="bench_session")

        for it in gitems:
            t0 = time.perf_counter()
            chunks = backend.search(it["question"], k)
            lat_ms = (time.perf_counter() - t0) * 1000

            # 检索层指标（must_have 为空的 LoCoMo 类条目记 None）
            must = it.get("must_have") or []
            retrieved = None
            first_rank = 0
            if must:
                concat_all = "\n".join(c.get("content", "") for c in chunks)
                retrieved = all(m.lower() in concat_all.lower() for m in must)
                first_rank = 0
                for i, c in enumerate(chunks, start=1):
                    txt = c.get("content", "").lower()
                    if all(m.lower() in txt for m in must):
                        first_rank = i
                        break
                if retrieved and first_rank == 0:
                    first_rank = len(chunks)  # 跨片段聚合命中，记 K

            pred = answerer.answer(it["question"], chunks)
            verdict = judge.score(it, pred, chunks)
            records.append({
                "qid": it["qid"],
                "category": it.get("category", "?"),
                "score": verdict["score"],
                "correct": verdict["correct"],
                "latency_ms": round(lat_ms, 2),
                "retrieved": retrieved,
                "first_hit_rank": first_rank,
                "pred_preview": (pred or "")[:120],
                "judge_raw": verdict.get("judge_raw", ""),
            })

    report: Dict[str, Any] = {
        "env": _env_decl(dict(args_decl or {}), dataset_id),
        "aggregate": aggregate(records),
        "records": records,
    }
    if progress:
        agg = report["aggregate"]
        progress(f"完成: {agg['count']} 问 | acc {agg['acc']}% | "
                 f"均分 {agg['avg_score']}")
    return report
