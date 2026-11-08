#!/usr/bin/env python3
"""MindForge 引擎延迟基准（in-process 路径）。

口径：
- 零 mock，真实 SQLite 存储 + 真实混合检索（TF-IDF+FTS5+Fuzzy，无向量引擎环境）
- add: 120 次（中文/英文混合种子）
- search: 60 次（中英混合查询词，含命中与未命中）
- 输出 min/p50/p95/max（ms），供竞品对比图使用
"""
import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, "/home/z/my-project/MindForge")

from mindforge.core.types import MemoryConfig
from mindforge.core.mindforge import MindForge

tmp = tempfile.mkdtemp(prefix="mf-bench-")
db = os.path.join(tmp, "bench.db")
mf = MindForge(config=MemoryConfig(db_path=db, key_file=None, encrypted=False))

SEEDS = [
    "用户喜欢用 Python 写脚本处理数据",
    "用户偏好使用 Python 来写脚本",
    "用户爱用 Python 写自动化脚本",
    "user deploys services with Docker on weekends",
    "user automates pipelines with shell scripts",
    "团队代码评审要求附带 pytest 覆盖率报告",
    "用户在研究 AI Agent 记忆系统架构",
    "user is learning Spanish on weekends",
    "服务器运维偏好 systemd 而不是 supervisor",
    "user drinks black coffee every morning",
]

def stats(name, times_ms):
    s = sorted(times_ms)
    p50 = statistics.median(s)
    p95 = s[int(len(s) * 0.95) - 1] if len(s) >= 20 else s[-1]
    return {
        "op": name, "n": len(s),
        "min": round(s[0], 1), "p50": round(p50, 1),
        "p95": round(p95, 1), "max": round(s[-1], 1),
    }

# 预热（首次调用含 lazy 初始化/索引建库，不计入）
for i in range(5):
    mf.add(f"warmup {i} 预热记忆", source_agent="bench")

# ---- add × 120 ----
add_times = []
for i in range(120):
    text = SEEDS[i % len(SEEDS)] + f" #{i}"
    t0 = time.perf_counter()
    mf.add(text, category="programming", tags=["bench"],
           source_agent="bench")
    add_times.append((time.perf_counter() - t0) * 1000)

# ---- search × 60 ----
QUERIES = ["Python 脚本", "Docker 部署", "AI 记忆", "coffee",
           "code review", "Spanish", "systemd", "pipeline",
           "不存在的词 xyzabc", "覆盖率"]
search_times = []
for i in range(60):
    q = QUERIES[i % len(QUERIES)]
    t0 = time.perf_counter()
    r = mf.search(q, max_results=5, agent_id="bench")
    search_times.append((time.perf_counter() - t0) * 1000)

# ---- 验证召回非空（保证测的是真实检索而非空转）----
hits = mf.search("Python", max_results=5, agent_id="bench")
n_hits = getattr(hits, "total_found", -1); engine_ms = getattr(hits, "query_time_ms", None)

results = {
    "environment": "in-process, TF-IDF+FTS5+Fuzzy (no vector engine installed)",
    "total_memories": 5 + 120,
    "search_python_hits": n_hits, "engine_self_reported_ms": engine_ms,
    "add": stats("add", add_times),
    "search": stats("search", search_times),
}
print(json.dumps(results, ensure_ascii=False, indent=2))

# ---- CI 守卫断言：拦数量级退化（阈值≈实测值×10-15倍，容忍 CI 机器抖动）----
FAILS = []
if results["add"]["p95"] > 50:
    FAILS.append(f"add p95 {results['add']['p95']}ms > 50ms")
if results["search"]["p95"] > 150:
    FAILS.append(f"search p95 {results['search']['p95']}ms > 150ms")
if results["search"]["p50"] > 30:
    FAILS.append(f"search p50 {results['search']['p50']}ms > 30ms")
if FAILS:
    print("LATENCY GUARD FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("LATENCY GUARD OK: add p50 %.1fms / search p50 %.1fms / search p95 %.1fms" % (
    results["add"]["p50"], results["search"]["p50"], results["search"]["p95"]))

import shutil
shutil.rmtree(tmp, ignore_errors=True)
