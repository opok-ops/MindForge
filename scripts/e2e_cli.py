#!/usr/bin/env python3
"""MindForge CLI 端到端守护测试（CI 固化版）。

背景（2026-11-07 E2E 战役）：dsh-mindforge v3.0 真实桥接测试发现，
单元测试的 mock 索引掩盖了「真实库恒为 0 簇」这类集成缺陷——
mock 全绿 ≠ 真实可用。本脚本以纯真实子进程调用覆盖核心命令路径，
每次发版在 CI 里跑，防止集成层回归。

设计原则：
- 零 mock：每条命令都是真实 CLI 子进程 + 真实 SQLite 库（隔离临时目录）
- 每步断言对应一个历史实测缺陷或关键契约
- 任何一步失败 → exit 1（CI 红）

字段契约（2026-11-08 实测对齐）：
- search --json      → {chunks, total_found, strategy_used, ...}
- search --explain   → chunks[i].score_breakdown = {tfidf,fts5,fuzzy,vector,winner,final}
- memory-recall      → {agent_id, query, recalled[], total_matched, ...}
- compress(-list)    → {total, clusters[{cluster_id, ...}]}
- adaptive-search    → {chunks, total, min_relevance_used, window_pattern_count}
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = {**os.environ, "PYTHONPATH": REPO}
CLI = [sys.executable, "-m", "mindforge.cli.main"]

PASSED, FAILED = [], []


def run(args, timeout=120, raw=False):
    """真实调用 CLI。raw=True 时返回 (stdout 文本, ms)——用于无 --json 的命令。"""
    t0 = time.time()
    proc = subprocess.run(CLI + args, capture_output=True, text=True,
                          env=ENV, timeout=timeout, cwd=REPO)
    ms = int((time.time() - t0) * 1000)
    out = proc.stdout.strip()
    if proc.returncode != 0:
        raise AssertionError(
            f"exit={proc.returncode}\nstdout: {out[:400]}\nstderr: {proc.stderr[:400]}")
    if raw:
        return out, ms
    try:
        return json.loads(out), ms
    except json.JSONDecodeError:
        raise AssertionError(f"stdout 非纯 JSON: {out[:400]}")


def step(name, fn):
    try:
        note, ms = fn()
        PASSED.append(name)
        print(f"  ✓ {name}  ({ms}ms)  {note}")
    except AssertionError as e:
        FAILED.append(name)
        print(f"  ✗ {name}\n    {str(e)[:600]}")


def main():
    tmp = tempfile.mkdtemp(prefix="mf-e2e-ci-")
    g = ["--db-path", os.path.join(tmp, "e2e.db")]
    print(f"== MindForge CLI E2E 守护（隔离库 {tmp}）==")

    seeds = [
        "用户喜欢用 Python 写脚本处理数据",
        "用户偏好使用 Python 来写脚本",
        "用户爱用 Python 写自动化脚本",
        "The team deploys services with Docker on weekends",
        "He automates deployment pipelines with shell scripts",
    ]
    ids = []

    def do_add():
        nonlocal ids
        for text in seeds:
            out, ms = run(g + ["add", text, "--json", "--agent", "e2e",
                                "--category", "programming"])
            assert out.get("status") == "ok", out
            ids.append(out["id"])
        assert len(ids) == len(seeds), f"种子写入 {len(ids)}/{len(seeds)}"
        return f"写入 {len(ids)} 条", ms
    step("add ×5（JSON 契约）", do_add)

    def do_search():
        out, ms = run(g + ["search", "python", "--json", "--limit", "5"])
        n = out.get("total_found", 0)
        assert n >= 3, f"FTS5/fuzzy 中文检索应命中近重复种子，实际 total_found={n}"
        return f"total_found={n}, strategy={out.get('strategy_used')}", ms
    step("search（真实库中文检索）", do_search)

    def do_explain():
        out, ms = run(g + ["search", "python", "--json", "--explain", "--limit", "3"])
        c = out.get("chunks", [{}])[0]
        sb = c.get("score_breakdown")
        assert isinstance(sb, dict) and "winner" in sb, f"explain 应输出分数分解: {c}"
        return f"winner={sb.get('winner')}, final={sb.get('final')}", ms
    step("search --explain（可解释性契约）", do_explain)

    def do_recall():
        out, ms = run(g + ["memory-recall", "e2e", "编程语言偏好", "--json"])
        n = len(out.get("recalled", []))
        assert n >= 1, f"同 agent recall 应可见（agent 契约），recalled={n}"
        return f"recalled={n}, total_matched={out.get('total_matched')}", ms
    step("memory-recall（agent 作用域契约）", do_recall)

    def do_context():
        out, ms = run(g + ["memory-context", "e2e", "编程偏好", "--json"])
        assert isinstance(out, dict), out
        return "JSON 契约 OK", ms
    step("memory-context", do_context)

    def do_stats():
        out, ms = run(g + ["stats", "--json"])
        total = out.get("total_memories", out.get("total", 0))
        assert total >= 5, f"stats 应 >= 5, 实际 {total}"
        return f"total={total}", ms
    step("stats", do_stats)

    def do_compress_dry():
        out, ms = run(g + ["compress", "--json", "--dry-run",
                           "--threshold", "0.10", "--min-cluster-size", "2"])
        n = out.get("total", len(out.get("clusters", [])))
        assert n >= 1, f"P0 回归：真实库聚簇应 > 0（2026-11-07 恒 0 缺陷），实际 {n}"
        return f"dry-run 聚出 {n} 簇", ms
    step("compress --dry-run（P0①：真实库聚簇 > 0）", do_compress_dry)

    cluster_id = [None]

    def do_compress_apply():
        out, ms = run(g + ["compress", "--json",
                           "--threshold", "0.10", "--min-cluster-size", "2"])
        assert out.get("status", "ok") == "ok", out
        cl = out.get("clusters", [])
        assert cl, "apply 后应返回簇列表"
        cluster_id[0] = cl[0].get("cluster_id")
        return f"applied {out.get('compressed_count', '?')} 条", ms
    step("compress --apply（写入摘要）", do_compress_apply)

    def do_compress_list():
        out, ms = run(g + ["compress-list", "--json"])
        n = out.get("total", len(out.get("clusters", [])))
        assert out.get("clusters") is not None, f"P0 回归：--json 应输出结构化簇列表: {out}"
        assert n >= 1, f"簇列表应 >= 1，实际 {n}"
        if not cluster_id[0]:
            cluster_id[0] = out["clusters"][0].get("cluster_id")
        return f"total={n}", ms
    step("compress-list --json（P0②：JSON 契约）", do_compress_list)

    def do_decompress():
        assert cluster_id[0], "缺少 cluster_id（上游步骤失败）"
        out, ms = run(g + ["decompress", str(cluster_id[0])], raw=True)
        assert out, "decompress 输出为空"
        return f"还原输出: {out[:60]}", ms
    step("decompress（可逆原子还原）", do_decompress)

    def do_adaptive():
        n = 0
        for _ in range(3):  # 连续查询触发会话模式追踪
            out, ms = run(g + ["adaptive-search", "python", "--json",
                               "--session", "e2e-ci", "--limit", "5"])
            n = out.get("total", len(out.get("chunks", [])))
        assert n >= 1, f"P1 回归：adaptive-search 不应恒空（2026-11-07 session 泄漏缺陷），total={n}"
        return f"total={n}, min_rel={out.get('min_relevance_used')}, window={out.get('window_pattern_count')}", ms
    step("adaptive-search（P1：session 泄漏回归）", do_adaptive)

    def do_reflection():
        out, ms = run(g + ["memory-reflection", "e2e", "--json"])
        assert isinstance(out, dict), out
        return "JSON 契约 OK", ms
    step("memory-reflection", do_reflection)

    def do_reinforce():
        out, ms = run(g + ["memory-reinforce", "e2e", "--json"])
        assert isinstance(out, dict), out
        return "强化成功", ms
    step("memory-reinforce", do_reinforce)

    def do_link():
        out, ms = run(g + ["memory-link", "e2e", ids[0]], raw=True)
        assert out, "memory-link 输出为空"
        return f"关联输出: {out[:60]}", ms
    step("memory-link", do_link)

    def do_lineage():
        out, ms = run(g + ["memory-lineage", ids[0], "--json"])
        assert isinstance(out, dict), out
        return "JSON 契约 OK", ms
    step("memory-lineage", do_lineage)

    def do_rerank():
        out, ms = run(g + ["rerank-search", "python", "--json", "--top", "3"])
        assert isinstance(out, dict), out
        return "JSON 契约 OK", ms
    step("rerank-search", do_rerank)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n== 结果: {len(PASSED)} passed / {len(FAILED)} failed ==")
    if FAILED:
        print("失败步骤: " + ", ".join(FAILED))
        sys.exit(1)


if __name__ == "__main__":
    main()
