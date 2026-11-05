#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MindForge 等参数可复现基准（v6.2.0 新增）

背景：出报告/对外引用性能数字时，最常见的伪影来自「对照组参数不对」——
语料规模不同、预热状态不同、轮次不同，都会让结论失真（v6.1.3 发布当天
就抓到一例：加密写入 616 条/s vs 明文 526 条/s 的假对比，真因是加密库
刻意不维护 FTS5 索引 + 两边语料规模不同）。本工具固化等参数方法学：

  - 明文 vs 加密对照：同 N、同内容模板、交替各两轮取均值
  - 延迟采样：同查询词 50 轮，报 P50 / P95
  - 并发压测：8 线程 × 50 条，报吞吐与错误数
  - 孤儿修复验证：注入 → 修复 → 复查，报残留

用法：
  python benchmarks/repro_benchmark.py                # 全量（~2 分钟）
  python benchmarks/repro_benchmark.py --quick        # 减半采样（~40 秒）
  python benchmarks/repro_benchmark.py --json out.json  # 机器可读输出

输出口径约定（引用时请注明）：
  环境：Python 3.12 / SQLite 3.53 / .venv 本机
  写入：每轮 N 条连续 add()，含 FTS 与审计同步落盘
  检索：max_results=10，use_embedding=False（向量后端另测）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from mindforge.core.mindforge import MindForge  # noqa: E402
from mindforge.core.types import MemoryConfig  # noqa: E402

DEFAULT_N = 500
DEFAULT_ROUNDS = 2
DEFAULT_LATENCY_ROUNDS = 50
QUERY_CN = "并发"
CONTENT_TMPL = "基准测试记忆第{i}条：Python 并发与检索性能研究 #{i}"


def _make_mf(prefix: str, encrypted: bool = False):
    tmp = tempfile.mkdtemp(prefix=prefix, dir=str(_REPO))
    cfg = MemoryConfig(
        db_path=os.path.join(tmp, "m.db"),
        key_file=os.path.join(tmp, "k.key"),
        encrypted=encrypted,
    )
    mf = MindForge(config=cfg)
    if encrypted:
        mf.init_with_password("benchmark-pass")
    return mf, tmp


def bench_write(n: int, rounds: int) -> dict:
    """明文 vs 加密写入，交替各 rounds 轮取均值（消除顺序偏差）。"""
    out = {"plain": [], "encrypted": []}
    for _ in range(rounds):
        for mode in ("plain", "encrypted"):
            mf, tmp = _make_mf(f"mf_bench_{mode}_", encrypted=(mode == "encrypted"))
            t0 = time.perf_counter()
            for i in range(n):
                mf.add(CONTENT_TMPL.format(i=i), category="benchmark")
            dt = time.perf_counter() - t0
            out[mode].append(round(n / dt))
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)
    p = statistics.mean(out["plain"])
    e = statistics.mean(out["encrypted"])
    return {
        "n_per_round": n, "rounds": rounds,
        "plain_per_s": round(p), "encrypted_per_s": round(e),
        "note": ("加密库刻意不维护 FTS5（磁盘零明文），写入省下索引工作，"
                 "加密常快于明文——快是用「不索引」换的"),
    }


def bench_latency(n: int, rounds: int) -> dict:
    """同一 500 条库上采样各检索路径 P50/P95。"""
    mf, tmp = _make_mf("mf_bench_lat_")
    for i in range(n):
        mf.add(CONTENT_TMPL.format(i=i), category="benchmark")

    def _sample(fn):
        lat = []
        for _ in range(rounds):
            t = time.perf_counter()
            fn()
            lat.append((time.perf_counter() - t) * 1000)
        lat.sort()
        return {"p50_ms": round(statistics.median(lat), 2),
                "p95_ms": round(lat[max(0, int(len(lat) * 0.95) - 1)], 2)}

    res = {
        "keyword_en": _sample(lambda: mf.search("Python 检索", max_results=10, use_embedding=False)),
        "chinese": _sample(lambda: mf.search(QUERY_CN, max_results=10, use_embedding=False)),
        "miss": _sample(lambda: mf.search("绝不存在的词xyzzy", max_results=10, use_embedding=False)),
        "fuzzy": _sample(lambda: mf.fuzzy_search("Pthyon 并发", limit=10)),
    }
    mf.close()
    shutil.rmtree(tmp, ignore_errors=True)

    enc_mf, enc_tmp = _make_mf("mf_bench_latenc_", encrypted=True)
    for i in range(n):
        enc_mf.add(CONTENT_TMPL.format(i=i), category="benchmark")
    lat = []
    for _ in range(rounds):
        t = time.perf_counter()
        enc_mf.search(QUERY_CN, max_results=10, use_embedding=False)
        lat.append((time.perf_counter() - t) * 1000)
    lat.sort()
    res["encrypted_chinese"] = {"p50_ms": round(statistics.median(lat), 2),
                                "p95_ms": round(lat[max(0, int(len(lat) * 0.95) - 1)], 2)}
    res["encrypted_note"] = "加密库走全量解密扫描兜底（FTS 零落盘）"
    enc_mf.close()
    shutil.rmtree(enc_tmp, ignore_errors=True)
    return res


def bench_concurrency(threads: int = 8, per_thread: int = 50) -> dict:
    mf, tmp = _make_mf("mf_bench_conc_")
    errs: list = []

    def worker(tid: int):
        for i in range(per_thread):
            try:
                mf.add(f"并发写入 t{tid}-{i} 多线程安全验证", category="concurrent")
            except Exception as ex:  # noqa: BLE001
                errs.append(f"{type(ex).__name__}: {ex}")

    t0 = time.perf_counter()
    ths = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    dt = time.perf_counter() - t0
    total = threads * per_thread
    mf.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return {"threads": threads, "total": total, "seconds": round(dt, 2),
            "per_s": round(total / dt), "errors": len(errs)}


def bench_repair(rows: int = 50) -> dict:
    """孤儿注入 → 修复 → 复查，验证数据治理链路。"""
    mf, tmp = _make_mf("mf_bench_repair_")
    live = mf.add("存活记忆", category="general")
    conn = mf._storage._get_conn()
    now = time.time()
    for i in range(rows):
        conn.execute(
            "INSERT INTO graph_relations VALUES (?,?,?,?,?,?,?,?)",
            (f"orphan_rel_{i}", f"e{i}", "e_common", "rel", 1.0,
             json.dumps(["deleted-mem"]), "{}", now))
        conn.execute(
            "INSERT INTO memory_notes (id,memory_id,content,author,tags,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (f"orphan_note_{i}", "deleted-mem", "x", "t", "[]", now, now))
    conn.commit()
    t0 = time.perf_counter()
    rep = mf._storage.repair_orphaned_data(dry_run=False)
    seconds = round(time.perf_counter() - t0, 3)
    after = mf._storage.repair_orphaned_data(dry_run=True)
    residual = after["graph"]["stale_refs_removed"] + sum(after["orphans"].values())
    mf.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return {"injected_rows": rows * 2, "seconds": seconds,
            "fixed": rep["graph"]["stale_refs_removed"] + sum(rep["orphans"].values()),
            "residual_after_recheck": residual}


def main() -> int:
    ap = argparse.ArgumentParser(description="MindForge 等参数可复现基准")
    ap.add_argument("--quick", action="store_true", help="减半采样（快速冒烟）")
    ap.add_argument("--json", metavar="OUT", help="结果写入 JSON 文件")
    args = ap.parse_args()

    n = DEFAULT_N // 2 if args.quick else DEFAULT_N
    rounds = 1 if args.quick else DEFAULT_ROUNDS
    lat_rounds = DEFAULT_LATENCY_ROUNDS // 2 if args.quick else DEFAULT_LATENCY_ROUNDS

    print("=" * 60)
    print("MindForge 等参数可复现基准（v6.2.0）")
    print(f"语料 N={n} × 轮次={rounds} · 延迟采样 {lat_rounds} 轮")
    print("=" * 60)

    result = {"meta": {
        "tool": "repro_benchmark", "version": "v6.2.0",
        "python": sys.version.split()[0], "n": n, "rounds": rounds,
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
    }}

    print("\n[1/4] 写入吞吐（明文 vs 加密，交替对照）...")
    result["write"] = bench_write(n, rounds)
    print(f"  明文 {result['write']['plain_per_s']} 条/s | "
          f"加密 {result['write']['encrypted_per_s']} 条/s")

    print("\n[2/4] 检索延迟（等语料 P50/P95）...")
    result["latency"] = bench_latency(n, lat_rounds)
    for k, v in result["latency"].items():
        if isinstance(v, dict):
            print(f"  {k:20} P50 {v['p50_ms']:>7}ms | P95 {v['p95_ms']:>7}ms")

    print("\n[3/4] 并发压测（8 线程）...")
    result["concurrency"] = bench_concurrency()
    c = result["concurrency"]
    print(f"  {c['total']} 条 / {c['seconds']}s = {c['per_s']} 条/s · 错误 {c['errors']}")

    print("\n[4/4] 孤儿修复验证...")
    result["repair"] = bench_repair()
    r = result["repair"]
    print(f"  注入 {r['injected_rows']} → 修复 {r['fixed']} → 复查残留 {r['residual_after_recheck']}"
          f"（{r['seconds']}s）")

    if args.json:
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n结果已写入: {args.json}")
    print("\n完成。引用口径见文件头注释。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
