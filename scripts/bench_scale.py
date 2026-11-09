#!/usr/bin/env python3
"""百万字规模延迟基准 + CI 守卫（v6.4.1 新增）

背景：v6.4.1 之前，fuzzy_search 兜底路在万条级库（~百万字）会出现 p95
近 900ms 的长尾——根因两层：
  ① SequenceMatcher 对「短 query vs 长文本」白跑（ratio = 2M/(m+n)，
     M <= m，n >= 4m 时数学上不可能超过 0.4 门槛）；
  ② Python 逐条反序列化+打分 ~50μs/条 的全库税。
修复：n >= 4m 数学剪枝 + SQLite 层候选集预筛（LIKE/LENGTH C 速度）。
本脚本钉住修复成果：规模库下 search p50/p95 必须保持毫秒级。

环境变量：
  MF_SCALE_N   库规模（默认 8000 ≈ 100 万字；CI 用 3000 控时长）
用法：python3 scripts/bench_scale.py
"""
import os
import statistics
import sys
import tempfile
import shutil
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindforge import MindForge, MemoryConfig  # noqa: E402

N = int(os.environ.get("MF_SCALE_N", "8000"))

# 程序化中文语料：每条 ~120-150 字，N 条 ≈ N×0.12 万字
SUBJ = ["用户", "团队", "服务器", "数据库", "前端组", "运维", "产品经理", "测试组"]
ACT = ["部署", "重构", "评审", "排查", "优化", "迁移", "压测", "回滚"]
OBJ = ["微服务网关", "SQLite 索引", "Redis 缓存", "K8s 集群", "CI 流水线",
       "消息队列", "TF-IDF 模型", "FTS5 分词"]
OPT = ["在周末", "在发布窗口", "深夜", "灰度期间", "上线前", "复盘会后"]


def make_memory(i: int) -> str:
    core = f"{SUBJ[i % 8]}倾向在{OPT[i % 6]}{ACT[i % 8]}{OBJ[i % 8]}，"
    detail = f"相关上下文包括{(i * 7) % 100}条历史记录、{(i * 13) % 50}个关联工单，"
    pref = f"偏好记录：版本号 v{(i % 90) / 10:.1f}、回滚窗口 {(i % 30) + 5} 分钟、告警阈值 {(i % 40) + 10}ms，"
    note = f"备注：规模化基准条目 {i:05d}，关键词锚点 python-{i % 97} 与 db-{i % 89}。"
    return core + detail + pref + note


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="mf-scale-")
    db = os.path.join(tmp, "scale.db")
    mf = MindForge(config=MemoryConfig(db_path=db, key_file=None, encrypted=False))
    seeds = [make_memory(i) for i in range(N)]
    total_chars = sum(len(s) for s in seeds)
    print(f"语料：{N} 条 / {total_chars} 字 / {total_chars / 10000:.1f} 万字", flush=True)

    add_times = []
    t0 = time.perf_counter()
    for i, text in enumerate(seeds):
        ta = time.perf_counter()
        mf.add(text, category="programming", tags=["bench"], source_agent="scale")
        add_times.append((time.perf_counter() - ta) * 1000)
    load_s = time.perf_counter() - t0

    # warmup：吸收首次 TF-IDF 索引水合的一次性成本（daemon 常驻只付一次）
    for w in range(3):
        mf.search(f"python-{w}", agent_id="scale", max_results=10)

    # 混合两类 query：token 命中型 + 纯锚点型（旧版后者的 fuzzy 兜底全库扫）
    sr = []
    for k in range(60):
        q = f"python-{(k * 31) % 97} 数据库" if k % 2 else f"python-{(k * 31) % 97}"
        t1 = time.perf_counter()
        r = mf.search(q, agent_id="scale", max_results=10)
        sr.append((time.perf_counter() - t1) * 1000)
        assert getattr(r, "total_found", 0) >= 0

    t3 = time.perf_counter()
    st = mf.stats()
    stats_ms = (time.perf_counter() - t3) * 1000

    def p(vals, q):
        s = sorted(vals)
        return s[int(len(s) * q)]

    report = {
        "n_memories": N,
        "total_chars": total_chars,
        "load_s": round(load_s, 1),
        "add_p50": round(statistics.median(add_times), 1),
        "add_p95": round(p(add_times, 0.95), 1),
        "search_p50": round(statistics.median(sr), 1),
        "search_p95": round(p(sr, 0.95), 1),
        "search_max": round(max(sr), 1),
        "stats_ms": round(stats_ms, 1),
        "stats_total": st.get("total", "?"),
    }
    print("SCALE BENCH " + " ".join(f"{k}={v}" for k, v in report.items()))

    # CI 守卫：万级库毫秒级契约（阈值 = 实测值 × 3 容忍 CI 抖动）
    fails = []
    if report["add_p95"] > 15:
        fails.append(f"add p95 {report['add_p95']}ms > 15ms")
    if report["search_p50"] > 70:
        fails.append(f"search p50 {report['search_p50']}ms > 70ms")
    if report["search_p95"] > 150:
        fails.append(f"search p95 {report['search_p95']}ms > 150ms")
    if fails:
        print("SCALE GUARD FAILED: " + "; ".join(fails))
        mf.close()
        shutil.rmtree(tmp, ignore_errors=True)
        return 1

    print("SCALE GUARD OK")
    mf.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
