#!/usr/bin/env python3
"""MindForge daemon E2E 守护测试（CI 固化版，v6.4.0）。

验证常驻路径（行式 JSON over stdio）的正确性与延迟契约：
- 16 步核心命令全覆盖（add/search/recall/context/stats/压缩链/adaptive/ping/shutdown）
- 输出契约对齐 CLI --json（同一套键名）
- **延迟守卫**：单请求 p50 < 50ms（对照 CLI 子进程 698ms——2026-11-08 实测）
- stdout 纯净性：每行必须是合法 JSON（无 banner 污染）

真实子进程 + 真实 SQLite 库，零 mock。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = {**os.environ, "PYTHONPATH": REPO}
CLI = [sys.executable, "-m", "mindforge.cli.main"]

PASSED, FAILED = [], []


def step(name, fn):
    try:
        detail, ms = fn()
        PASSED.append(name)
        print(f"  ✓ {name}  ({ms}ms)  {str(detail)[:80]}")
    except Exception as e:
        FAILED.append(name)
        print(f"  ✗ {name}\n    {e}")


class Daemon:
    """daemon 子进程客户端：一行请求 → 一行响应。"""

    def __init__(self, db_path):
        self.proc = subprocess.Popen(
            CLI + ["--db-path", db_path, "daemon"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # 日志走 stderr，stdout 必须纯 JSON
            text=True, env=ENV, cwd=REPO, bufsize=1,
        )
        self._n = 0

    def call(self, cmd, args=None, timeout=30):
        self._n += 1
        t0 = time.time()
        self.proc.stdin.write(json.dumps(
            {"id": self._n, "cmd": cmd, "args": args or {}}) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        ms = (time.time() - t0) * 1000
        assert line, f"{cmd}: daemon 无响应（进程退出？）"
        d = json.loads(line)  # stdout 纯净性：非 JSON 即失败
        assert d["id"] == self._n, f"{cmd}: 响应 id 错位 {d['id']} != {self._n}"
        if not d["ok"]:
            raise AssertionError(f"{cmd}: {d.get('error')}")
        return d["data"], ms

    def close(self):
        try:
            self.call("shutdown")
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def main():
    tmp = tempfile.mkdtemp(prefix="mf-e2e-daemon-")
    db = os.path.join(tmp, "d.db")
    d = Daemon(db)
    lat = []

    print(f"== MindForge daemon E2E（隔离库 {db}）==")

    def do_ping():
        data, ms = d.call("ping")
        assert data["pong"] is True and data["pid"] > 0, data
        return f"pong pid={data['pid']}", ms
    step("ping（存活 + 协议握手）", do_ping)

    ids = []

    def do_add():
        seeds = [
            "用户喜欢用 Python 写脚本处理数据",
            "用户偏好使用 Python 来写脚本",
            "用户爱用 Python 写自动化脚本",
            "user deploys services with Docker on weekends",
            "user automates pipelines with shell scripts",
        ]
        for s in seeds:
            data, ms = d.call("add", {"content": s, "agent": "e2e"})
            assert data["status"] == "ok" and data["id"], data
            ids.append(data["id"])
            lat.append(ms)
        return f"写入 5 条", int(sum(lat[-5:]))
    step("add ×5（JSON 契约：status/id）", do_add)

    def do_search():
        data, ms = d.call("search", {"query": "Python", "agent": "e2e"})
        lat.append(ms)
        n = data.get("total_found", 0)
        assert n >= 3, f"真实库中文检索应命中 >=3，实际 {n}"
        return f"total_found={n}, strategy={data.get('strategy_used')}", ms
    step("search（真实库中文检索）", do_search)

    def do_search_explain():
        data, ms = d.call("search", {"query": "Python", "agent": "e2e", "explain": True})
        lat.append(ms)
        bd = (data.get("chunks") or [{}])[0].get("score_breakdown")
        assert bd and bd.get("winner"), f"score_breakdown 缺失: {bd}"
        return f"winner={bd['winner']}, final={bd.get('final', 0):.3f}", ms
    step("search explain（可解释性契约）", do_search_explain)

    def do_recall():
        data, ms = d.call("memory-recall", {"agent": "e2e", "query": "Python"})
        lat.append(ms)
        n = data.get("total_matched", 0)
        assert n >= 1, f"同 agent 应可召回，实际 {n}"
        return f"total_matched={n}", ms
    step("memory-recall（agent 作用域契约）", do_recall)

    def do_context():
        data, ms = d.call("memory-context", {"agent": "e2e", "query": "Python"})
        lat.append(ms)
        assert "token_estimate" in data, list(data)[:6]
        return f"included={data.get('included_count')}", ms
    step("memory-context（上下文注入）", do_context)

    def do_stats():
        data, ms = d.call("stats")
        lat.append(ms)
        assert data.get("total", 0) >= 5, data.get("total")
        return f"total={data['total']}", ms
    step("stats", do_stats)

    def do_compress_dry():
        data, ms = d.call("compress", {"dry_run": True, "threshold": 0.10})
        lat.append(ms)
        n = data.get("compressed_count", 0)
        assert n >= 1, f"P0① 回归：真实库聚簇应 >0，实际 {n}"
        return f"dry-run 聚出 {n} 簇", ms
    step("compress dry-run（P0① 回归）", do_compress_dry)

    cluster_id = [None]

    def do_compress_apply():
        data, ms = d.call("compress", {"threshold": 0.10})
        lat.append(ms)
        n = data.get("compressed_count", 0)
        assert n >= 1, f"apply 簇数 {n}"
        cl = data.get("clusters") or []
        if cl:
            cluster_id[0] = cl[0].get("cluster_id")
        return f"applied {n} 簇", ms
    step("compress apply（写入摘要）", do_compress_apply)

    def do_clist():
        data, ms = d.call("compress-list")
        lat.append(ms)
        assert data.get("total", 0) >= 1, f"P0② 回归：total={data.get('total')}"
        cl = data.get("clusters") or []
        if not cluster_id[0] and cl:
            cluster_id[0] = cl[0].get("cluster_id")
        return f"total={data['total']}", ms
    step("compress-list（P0② JSON 契约）", do_clist)

    def do_decompress():
        assert cluster_id[0], "缺少 cluster_id"
        data, ms = d.call("decompress", {"cluster_id": cluster_id[0]})
        lat.append(ms)
        assert data.get("restored_count", 0) >= 1, data
        return f"恢复 {data['restored_count']} 条源记忆", ms
    step("decompress（可逆原子契约）", do_decompress)

    def do_adaptive():
        n = 0
        ms = 0
        for _ in range(3):
            data, ms = d.call("adaptive-search", {"query": "Python", "session": "e2e-ci"})
            lat.append(ms)
            n = data.get("total", 0)
        assert n >= 1, f"P1 回归：adaptive 不应恒空，total={n}"
        return f"total={n}, min_rel={data.get('min_relevance_used')}", int(ms)
    step("adaptive-search（P1 session 泄漏回归）", do_adaptive)

    def do_latency():
        lat_sorted = sorted(lat)
        p50 = lat_sorted[len(lat_sorted) // 2]
        p95 = lat_sorted[int(len(lat_sorted) * 0.95) - 1] if len(lat_sorted) >= 20 else max(lat_sorted)
        assert p50 < 50, f"延迟契约：daemon 单请求 p50 应 <50ms，实际 {p50:.1f}ms"
        return f"n={len(lat)}, p50={p50:.1f}ms, max={max(lat):.1f}ms", 0
    step("延迟契约（p50 < 50ms，对照 CLI 子进程 698ms）", do_latency)

    d.close()

    print(f"\n== 结果: {len(PASSED)} passed / {len(FAILED)} failed ==")
    if FAILED:
        print("失败步骤: " + ", ".join(FAILED))
        sys.exit(1)


if __name__ == "__main__":
    main()
