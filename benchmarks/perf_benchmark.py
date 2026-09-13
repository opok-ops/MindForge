# -*- coding: utf-8 -*-
"""
MindForge v5.6.3 性能基准
=========================

独立、可复跑的性能与并发正确性基准，结果输出为 JSON：
    benchmarks/results/perf_results.json

覆盖：
  1. 单条写入延迟 / 吞吐（p50/p95/p99）
  2. 批量写入吞吐（batch_add）
  3. 随机读取 get（冷/热缓存）
  4. 列表分页
  5. 搜索延迟随数据量曲线（500/1000/2000/3000）+ 混合/模糊两路
  6. 更新 / 删除延迟
  7. 加密模式开销（加密 DB 同负载 vs 明文）
  8. AES-GCM 原语加解密吞吐（1KB / 10KB）
  9. 备份 / 恢复耗时与产物大小
 10. 8 线程混合并发：吞吐 + 正确性（无异常、计数一致）——回归 P0 #4 / P2 #21
 11. 磁盘探测结果缓存（P3 #24）
 12. 内存（RSS）与库文件体积

用法：
    python benchmarks/perf_benchmark.py
    MF_BENCH_N=1000 python benchmarks/perf_benchmark.py   # 缩小规模快速冒烟

注意：基准只写系统临时目录，不触碰仓库数据目录。
"""

import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

# 保证可从仓库根目录直接运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

from core.mindforge import MindForge  # noqa: E402
from core import MemoryConfig  # noqa: E402

N = int(os.environ.get("MF_BENCH_N", "3000"))
SEARCH_CHECKPOINTS = [n for n in (500, 1000, 2000, 3000) if n <= N] or [N]
ENC_N = min(1000, N // 2)
MUT_N = min(500, N // 4)
CONC_THREADS = 8
CONC_PER_THREAD = 250

WORDS = (
    "记忆 学习 推理 知识 图谱 向量 检索 强化 学习 遗忘 曲线 注意力 机制 "
    "语义 嵌入 缓存 索引 事务 加密 签名 联邦 同步 冲突 合并 会话 代理 "
    "决策 规划 反思 感知 工作 记忆 长期 短期 归档 标签 分类 评分 衰减"
).split()

QUERIES = [
    "记忆学习", "向量检索", "知识图谱", "遗忘曲线", "加密签名",
    "联邦同步", "冲突合并", "会话代理", "缓存索引", "语义嵌入",
    "衰减评分", "归档标签", "决策规划", "注意力机制", "事务加密",
]


def _content(i: int) -> str:
    rng = random.Random(i)
    k = 8 + rng.randrange(8)
    body = " ".join(rng.choice(WORDS) for _ in range(k))
    return f"bench-memory-{i:06d} {body} 序号{i} 的记忆内容片段"


def pct(samples, q):
    """百分位（samples 为毫秒列表）"""
    if not samples:
        return 0.0
    xs = sorted(samples)
    idx = min(len(xs) - 1, int(round(q / 100.0 * (len(xs) - 1))))
    return xs[idx]


def stats_block(samples_ms):
    return {
        "n": len(samples_ms),
        "mean_ms": round(statistics.fmean(samples_ms), 4),
        "p50_ms": round(pct(samples_ms, 50), 4),
        "p95_ms": round(pct(samples_ms, 95), 4),
        "p99_ms": round(pct(samples_ms, 99), 4),
        "max_ms": round(max(samples_ms), 4),
        "stdev_ms": round(statistics.pstdev(samples_ms), 4) if len(samples_ms) > 1 else 0.0,
    }


def rss_mb():
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def new_plain(tmp, name="plain"):
    db = os.path.join(tmp, name)
    os.makedirs(db, exist_ok=True)
    return MindForge(db_path=os.path.join(db, "m.db"), encrypted=False)


def new_encrypted(tmp, name="enc", password="bench-pass-123"):
    db = os.path.join(tmp, name)
    os.makedirs(db, exist_ok=True)
    key_file = os.path.join(db, "m.key")
    cfg = MemoryConfig(db_path=os.path.join(db, "m.db"),
                       key_file=key_file, encrypted=True)
    mf = MindForge(config=cfg)
    mf.init_with_password(password)
    return mf, os.path.join(db, "m.db"), key_file


def grow(mf, total, lat_store, tag=""):
    start = len(mf.list(limit=1))
    for i in range(start, total):
        t0 = time.perf_counter_ns()
        mf.add(content=_content(i),
               category=random.Random(i).choice(["general", "work", "idea", "ref"]),
               tags=[random.Random(i).choice(WORDS)],
               source_agent="bench")
        lat_store.append((time.perf_counter_ns() - t0) / 1e6)


# ---------------------------------------------------------------------------
# 各基准阶段
# ---------------------------------------------------------------------------

def bench_write_single(tmp, result):
    print("[1/12] 单条写入 ...", flush=True)
    mf = new_plain(tmp, "w1")
    lats = []
    rss0 = rss_mb()
    t0 = time.perf_counter()
    grow(mf, N, lats)
    dt = time.perf_counter() - t0
    rss1 = rss_mb()
    result["write_single"] = {
        "records": N, "elapsed_s": round(dt, 3),
        "throughput_ops_s": round(N / dt, 1),
        **stats_block(lats),
        "rss_before_mb": round(rss0, 1), "rss_after_mb": round(rss1, 1),
        "rss_delta_mb": round(rss1 - rss0, 1),
    }
    mf.close()
    print(f"       {N} 条 {dt:.2f}s, {N/dt:.0f} ops/s", flush=True)


def bench_write_batch(tmp, result):
    print("[2/12] 批量写入 ...", flush=True)
    mf = new_plain(tmp, "w2")
    chunk = 500
    t0 = time.perf_counter()
    done = 0
    for base in range(0, N, chunk):
        entries = [{
            "content": _content(10_000_000 + base + j),
            "category": "batch",
            "tags": ["batch"],
            "source_agent": "bench",
        } for j in range(min(chunk, N - base))]
        done += mf.batch_add(entries)
    dt = time.perf_counter() - t0
    assert done == N, f"batch_add 计数不一致: {done} != {N}"
    result["write_batch"] = {
        "records": done, "elapsed_s": round(dt, 3),
        "throughput_ops_s": round(done / dt, 1),
        "chunk_size": chunk,
    }
    mf.close()
    print(f"       {done} 条 {dt:.2f}s, {done/dt:.0f} ops/s", flush=True)


def bench_read_and_search(tmp, result):
    print("[3-6/12] 读取 / 列表 / 搜索 / 更新 / 删除 ...", flush=True)
    mf = new_plain(tmp, "main")

    # 增长主库，同时在检查点测搜索延迟曲线
    search_curve = []
    add_lats = []
    for cp in SEARCH_CHECKPOINTS:
        grow(mf, cp, add_lats)
        rng = random.Random(cp)
        qs = rng.sample(QUERIES, min(5, len(QUERIES)))
        lats_hybrid, lats_fuzzy = [], []
        for q in qs:
            t0 = time.perf_counter_ns()
            mf.search(q, max_results=10, use_embedding=False, agent_id="bench")
            lats_hybrid.append((time.perf_counter_ns() - t0) / 1e6)
            t0 = time.perf_counter_ns()
            mf.fuzzy_search(q, limit=10)
            lats_fuzzy.append((time.perf_counter_ns() - t0) / 1e6)
        search_curve.append({
            "records": cp,
            "hybrid_ms": round(statistics.fmean(lats_hybrid), 3),
            "fuzzy_ms": round(statistics.fmean(lats_fuzzy), 3),
        })
    result["search_curve"] = search_curve

    ids = [e.id for e in mf.list(limit=N)]
    assert len(ids) == SEARCH_CHECKPOINTS[-1], "主库计数不一致"

    # 随机读取
    rng = random.Random(42)
    sample_ids = rng.sample(ids, min(1000, len(ids)))
    lats = []
    for mid in sample_ids:
        t0 = time.perf_counter_ns()
        got = mf.get(mid, actor="bench", session_id="")
        lats.append((time.perf_counter_ns() - t0) / 1e6)
        assert got is not None, "get 返回 None"
    result["read_get"] = stats_block(lats)

    # 热缓存读取（同一批 id 连续读）
    hot_ids = sample_ids[:50]
    lats = []
    for _ in range(10):
        for mid in hot_ids:
            t0 = time.perf_counter_ns()
            mf.get(mid, actor="bench", session_id="")
            lats.append((time.perf_counter_ns() - t0) / 1e6)
    result["read_get_cached"] = stats_block(lats)

    # 列表分页
    lats = []
    pages = 0
    for off in range(0, min(1000, len(ids)), 50):
        t0 = time.perf_counter_ns()
        rows = mf.list(limit=50, offset=off, sort_order="desc")
        lats.append((time.perf_counter_ns() - t0) / 1e6)
        pages += 1
        assert len(rows) == 50 or off + 50 > len(ids), "分页大小异常"
    result["read_list_page50"] = stats_block(lats)

    # 更新
    upd_ids = rng.sample(ids, MUT_N)
    lats = []
    for mid in upd_ids:
        t0 = time.perf_counter_ns()
        ok = mf.update(mid, content="更新后的内容 " + mid[:8],
                       actor="bench", session_id="")
        lats.append((time.perf_counter_ns() - t0) / 1e6)
        assert ok, "update 返回 False"
    result["update"] = stats_block(lats)
    mf.close()

    # 删除（独立库，避免影响后续阶段）
    mf = new_plain(tmp, "del")
    grow(mf, MUT_N, [])
    del_ids = [e.id for e in mf.list(limit=MUT_N)]
    lats = []
    for mid in del_ids:
        t0 = time.perf_counter_ns()
        ok = mf.delete(mid, actor="bench", session_id="")
        lats.append((time.perf_counter_ns() - t0) / 1e6)
        assert ok, "delete 返回 False"
    result["delete"] = stats_block(lats)
    assert mf.count_memories() == 0, "删除后计数应为 0"
    mf.close()
    print("       完成", flush=True)


def bench_encryption(tmp, result):
    print("[7-8/12] 加密模式 + 原语吞吐 ...", flush=True)
    mf, db_path, key_file = new_encrypted(tmp)

    add_lats, get_lats, search_lats = [], [], []
    t0 = time.perf_counter()
    for i in range(ENC_N):
        t = time.perf_counter_ns()
        e = mf.add(content=_content(20_000_000 + i), category="enc",
                   source_agent="bench")
        add_lats.append((time.perf_counter_ns() - t) / 1e6)
    dt_add = time.perf_counter() - t0

    ids = [e.id for e in mf.list(limit=ENC_N)]
    rng = random.Random(7)
    for mid in rng.sample(ids, min(500, len(ids))):
        t = time.perf_counter_ns()
        got = mf.get(mid, actor="bench", session_id="")
        get_lats.append((time.perf_counter_ns() - t) / 1e6)
        assert got is not None
    for q in random.Random(8).sample(QUERIES, 8):
        t = time.perf_counter_ns()
        mf.search(q, max_results=10, use_embedding=False, agent_id="bench")
        search_lats.append((time.perf_counter_ns() - t) / 1e6)

    result["encrypted_mode"] = {
        "records": ENC_N,
        "add_throughput_ops_s": round(ENC_N / dt_add, 1),
        "add": stats_block(add_lats),
        "get": stats_block(get_lats),
        "search_ms": round(statistics.fmean(search_lats), 3),
    }

    # 原语：直接测 EncryptionEngine
    from core.encryption import EncryptionEngine
    engine, _salt = EncryptionEngine.from_password("bench-pass-123")
    prim = {}
    for size_name, payload in (("1KB", "x" * 1024), ("10KB", "x" * (10 * 1024))):
        n = 200
        enc_ms, dec_ms = [], []
        blobs = []
        for _ in range(n):
            t = time.perf_counter_ns()
            b = engine.encrypt(payload)
            enc_ms.append((time.perf_counter_ns() - t) / 1e6)
            blobs.append(b)
        total_bytes = len(payload) * n
        for b in blobs:
            t = time.perf_counter_ns()
            engine.decrypt(b)
            dec_ms.append((time.perf_counter_ns() - t) / 1e6)
        prim[size_name] = {
            "encrypt_mb_s": round(total_bytes / (sum(enc_ms) / 1000) / 1048576, 1),
            "decrypt_mb_s": round(total_bytes / (sum(dec_ms) / 1000) / 1048576, 1),
            "encrypt_mean_ms": round(statistics.fmean(enc_ms), 5),
            "decrypt_mean_ms": round(statistics.fmean(dec_ms), 5),
        }
    result["crypto_primitive"] = prim
    mf.close()
    print("       完成", flush=True)


def bench_backup(tmp, result):
    print("[9/12] 备份 / 恢复 ...", flush=True)
    mf = new_plain(tmp, "bk")
    grow(mf, ENC_N, [])
    bk_dir = os.path.join(tmp, "bk_out")
    os.makedirs(bk_dir, exist_ok=True)
    zip_path = os.path.join(bk_dir, "backup.zip")

    t0 = time.perf_counter()
    info = mf.backup(zip_path)
    dt_bk = time.perf_counter() - t0
    zip_size = os.path.getsize(zip_path)

    # 恢复到新库
    restore_dir = os.path.join(tmp, "bk_restore")
    os.makedirs(restore_dir, exist_ok=True)
    restore_db = os.path.join(restore_dir, "r.db")

    t0 = time.perf_counter()
    # backup-restore 为 MindForge 静态方法（v5.6.0 增强）
    info_restore = MindForge.restore_backup(zip_path, restore_db, force=True)
    dt_rs = time.perf_counter() - t0

    chk = MindForge(db_path=restore_db, encrypted=False)
    restored = chk.count_memories()
    chk.close()
    assert restored == ENC_N, f"恢复计数不一致: {restored} != {ENC_N}"

    result["backup_restore"] = {
        "records": ENC_N,
        "backup_s": round(dt_bk, 3),
        "restore_s": round(dt_rs, 3),
        "backup_mb_s": round((ENC_N * 200) / dt_bk / 1048576, 2),
        "zip_size_mb": round(zip_size / 1048576, 2),
        "restored_records": restored,
        "backup_info_keys": sorted(list(info.keys())) if isinstance(info, dict) else [],
    }
    mf.close()
    print(f"       备份 {dt_bk:.2f}s / 恢复 {dt_rs:.2f}s", flush=True)


def bench_concurrency(tmp, result):
    print("[10/12] 8 线程混合并发 ...", flush=True)
    mf = new_plain(tmp, "conc")
    barrier = threading.Barrier(CONC_THREADS + 1)
    errors = []
    errors_lock = threading.Lock()
    counters = {"add": 0, "get": 0, "search": 0, "list": 0}
    counters_lock = threading.Lock()

    def worker(tid):
        rng = random.Random(1000 + tid)
        own_ids = []
        try:
            barrier.wait()
            for j in range(CONC_PER_THREAD):
                op = j % 4
                if op == 0:
                    e = mf.add(content=f"并发线程{tid}序号{j} " + _content(30_000_000 + tid * 100000 + j),
                               source_agent=f"t{tid}")
                    own_ids.append(e.id)
                    with counters_lock:
                        counters["add"] += 1
                elif op == 1:
                    if own_ids:
                        mf.get(rng.choice(own_ids), actor=f"t{tid}", session_id="")
                    with counters_lock:
                        counters["get"] += 1
                elif op == 2:
                    mf.search(rng.choice(QUERIES), max_results=5,
                              use_embedding=False, agent_id=f"t{tid}")
                    with counters_lock:
                        counters["search"] += 1
                else:
                    mf.list(limit=20, actor=f"t{tid}", session_id="")
                    with counters_lock:
                        counters["list"] += 1
        except Exception as ex:  # noqa: BLE001
            with errors_lock:
                errors.append(f"t{tid}: {type(ex).__name__}: {ex}")

    threads = [threading.Thread(target=worker, args=(t,), daemon=True)
               for t in range(CONC_THREADS)]
    for t in threads:
        t.start()
    t0 = time.perf_counter()
    barrier.wait()
    for t in threads:
        t.join(timeout=120)
    dt = time.perf_counter() - t0

    alive = [t.is_alive() for t in threads]
    total = sum(counters.values())
    time.sleep(0.5)  # 等写盘落库
    db_count = mf.count_memories()
    expected_adds = CONC_THREADS * (CONC_PER_THREAD // 4 +
                                    (1 if CONC_PER_THREAD % 4 > 0 else 0))
    result["concurrency"] = {
        "threads": CONC_THREADS,
        "ops_per_thread": CONC_PER_THREAD,
        "total_ops": total,
        "elapsed_s": round(dt, 3),
        "throughput_ops_s": round(total / dt, 1),
        "errors": len(errors),
        "error_samples": errors[:5],
        "threads_still_alive": sum(alive),
        "ops_breakdown": counters,
        "db_records": db_count,
        "expected_adds": expected_adds,
        "adds_consistent": db_count == expected_adds,
    }
    mf.close()
    print(f"       {total} ops / {dt:.2f}s, errors={len(errors)}", flush=True)


def bench_misc(tmp, result):
    print("[11-12/12] 磁盘探测缓存 / 体积 / RSS ...", flush=True)
    from core.storage import HardwareProfiler

    # P3 #24：磁盘探测应被缓存，第二次近乎零成本（类变量缓存，仅首次写 1MB）。
    # 先清类缓存，确保 first 测到的是真正的冷探测而非此前流程遗留的缓存命中。
    HardwareProfiler._cached_disk_type = None
    t0 = time.perf_counter_ns()
    d1 = HardwareProfiler._detect_disk_type()
    first_ms = (time.perf_counter_ns() - t0) / 1e6
    t0 = time.perf_counter_ns()
    d2 = HardwareProfiler._detect_disk_type()
    second_ms = (time.perf_counter_ns() - t0) / 1e6

    # 限流器记账吞吐（不同 key，避免触发窗口上限）
    from core.storage import _rate_limiter as rl
    n_rl = 20000
    t0 = time.perf_counter()
    for i in range(n_rl):
        rl.check(f"10.0.{(i >> 8) & 0xff}.{i & 0xff}")
    dt = time.perf_counter() - t0

    # 主库体积
    main_db = os.path.join(tmp, "main", "m.db")
    db_mb = round(os.path.getsize(main_db) / 1048576, 2) if os.path.exists(main_db) else 0

    result["misc"] = {
        "disk_type_first": d1,
        "disk_type_second": d2,
        "disk_probe_first_ms": round(first_ms, 3),
        "disk_probe_cached_ms": round(second_ms, 4),
        "disk_probe_cached": second_ms < first_ms / 5,
        "rate_limiter_ops_s": round(n_rl / dt, 0),
        "main_db_mb": db_mb,
        "main_db_records": SEARCH_CHECKPOINTS[-1],
        "bytes_per_record": round(db_mb * 1048576 / max(1, SEARCH_CHECKPOINTS[-1]), 1),
        "rss_peak_mb": round(rss_mb(), 1),
    }
    print("       完成", flush=True)


def main():
    print(f"MindForge 性能基准  N={N}  Python {sys.version.split()[0]}", flush=True)
    tmp = tempfile.mkdtemp(prefix="mf_bench_")
    result = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scale": {"N": N, "encrypted_N": ENC_N,
                  "search_checkpoints": SEARCH_CHECKPOINTS,
                  "mutation_N": MUT_N,
                  "concurrency_threads": CONC_THREADS,
                  "concurrency_per_thread": CONC_PER_THREAD},
        "env": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "cpu_count": psutil.cpu_count(logical=True),
            "mem_total_gb": round(psutil.virtual_memory().total / 1073741824, 1),
        },
    }
    try:
        from core.version import __version__
        result["env"]["mindforge_version"] = __version__
    except Exception:  # noqa: BLE001
        result["env"]["mindforge_version"] = "unknown"

    failures = []
    phases = [
        bench_write_single,
        bench_write_batch,
        bench_read_and_search,
        bench_encryption,
        bench_backup,
        bench_concurrency,
        bench_misc,
    ]
    for fn in phases:
        try:
            fn(tmp, result)
        except Exception as ex:  # noqa: BLE001
            import traceback
            failures.append({"phase": fn.__name__,
                             "error": f"{type(ex).__name__}: {ex}",
                             "trace": traceback.format_exc(limit=4)})
            print(f"  !! 阶段失败 {fn.__name__}: {ex}", flush=True)

    result["failures"] = failures
    result["passed"] = not failures

    out_dir = ROOT / "benchmarks" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "perf_results.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {out}", flush=True)
    print("PASSED" if result["passed"] else f"FAILED ({len(failures)} phase(s))",
          flush=True)

    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
