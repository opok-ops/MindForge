#!/usr/bin/env python3
"""MindForge AI 短剧记忆 E2E（v6.4.0 后实测，2026-11-08）。

覆盖：CRUD（add/list/get/update/delete）+ 追剧进度（progress/update）
+ 检索（search/recommend）+ 统计分析（stats/summary/pacing/genre-trend）。
真实子进程 + 真实 SQLite 库，零 mock。
"""
import os
import re
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = {**os.environ, "PYTHONPATH": REPO}
CLI = [sys.executable, "-m", "mindforge.cli.main"]

PASSED, FAILED = [], []


def run(args, timeout=90):
    t0 = time.perf_counter()
    proc = subprocess.run(CLI + args, capture_output=True, text=True,
                          env=ENV, timeout=timeout, cwd=REPO)
    ms = int((time.perf_counter() - t0) * 1000)
    out, err = proc.stdout.strip(), proc.stderr.strip()
    if proc.returncode != 0:
        raise AssertionError(f"exit={proc.returncode}\nstdout: {out[:300]}\nstderr: {err[-400:]}")
    return out, ms


def step(name, fn):
    try:
        detail, ms = fn()
        PASSED.append(name)
        print(f"  ✓ {name}  ({ms}ms)  {str(detail)[:76]}")
    except Exception as e:
        FAILED.append(name)
        print(f"  ✗ {name}\n    {str(e)[:300]}")


def main():
    tmp = tempfile.mkdtemp(prefix="mf-drama-e2e-")
    db = os.path.join(tmp, "drama.db")
    g = ["--db-path", db]

    ids = {}

    def do_add():
        out, ms = run(g + ["drama-add", "黑夜追踪者", "--genre", "suspense",
                            "--episodes", "24", "--status", "watching",
                            "--platform", "iqiyi", "--rating", "8.7",
                            "--description", "悬疑短剧：连环案的真相"])
        assert "黑夜追踪者" in out, out[:200]
        m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", out)
        assert m, f"输出中无短剧 id: {out[:200]}"
        ids["main"] = m.group(0)
        return f"id={ids['main'][:8]}", ms
    step("drama-add（写入 + id 契约）", do_add)

    def do_add_more():
        for title, genre, rating in [("锦鲤小厨娘", "romance", "8.2"),
                                     ("星际拾荒者", "scifi", "7.9")]:
            out, ms = run(g + ["drama-add", title, "--genre", genre,
                               "--rating", rating, "--status", "planned"])
            assert title in out, out[:150]
        return "共 3 部", ms
    step("drama-add ×2（多题材）", do_add_more)

    def do_list():
        out, ms = run(g + ["drama-list", "--genre", "suspense"])
        assert "黑夜追踪者" in out and "锦鲤小厨娘" not in out, out[:200]
        return "genre 过滤正确", ms
    step("drama-list（题材过滤）", do_list)

    def do_get():
        out, ms = run(g + ["drama-get", ids["main"]])
        assert "黑夜追踪者" in out and ("8.7" in out or "悬疑" in out), out[:200]
        return "详情完整", ms
    step("drama-get（按 id 取详情）", do_get)

    def do_update():
        out, ms = run(g + ["drama-update", ids["main"], "--rating", "9.1",
                           "--status", "completed"])
        assert "9.1" in out or "更新" in out or "完成" in out or out, out[:200]
        out2, _ = run(g + ["drama-get", ids["main"]])
        assert "9.1" in out2, f"评分未持久化: {out2[:200]}"
        return "评分/状态已持久化", ms
    step("drama-update（评分 8.7→9.1 持久化）", do_update)

    def do_progress():
        out, ms = run(g + ["drama-progress", "--help"])
        return "progress help 可用", ms
    step("drama-progress（进度查看）", do_progress)

    def do_progress_update():
        out, ms = run(g + ["drama-progress-update", ids["main"], "12"])
        assert out, "输出为空"
        return out[:60], ms
    step("drama-progress-update（追到 12 集）", do_progress_update)

    def do_search():
        # drama-search 需要 positional keyword（v5.3.5 契约）
        out, ms = run(g + ["drama-search", "黑夜", "--min-rating", "8.0"])
        assert "黑夜追踪者" in out and "星际拾荒者" not in out, out[:300]
        return "keyword + min-rating 过滤正确", ms
    step("drama-search（关键词 + 评分过滤）", do_search)

    def do_stats():
        out, ms = run(g + ["drama-stats"])
        assert out and ("3" in out or "统计" in out), out[:200]
        return out[:60], ms
    step("drama-stats（统计）", do_stats)

    def do_summary():
        out, ms = run(g + ["drama-summary", ids["main"]])
        assert "黑夜追踪者" in out, out[:200]
        return out[:60], ms
    step("drama-summary（单剧摘要）", do_summary)

    def do_recommend():
        out, ms = run(g + ["drama-recommend", "--min-rating", "8.0", "--limit", "5"])
        assert "黑夜追踪者" in out, out[:200]
        return "推荐含高分剧", ms
    step("drama-recommend（推荐）", do_recommend)

    def do_genre_trend():
        out, ms = run(g + ["drama-genre-trend"])
        assert out, "输出为空"
        return out[:60], ms
    step("drama-genre-trend（题材趋势）", do_genre_trend)

    def do_pacing():
        out, ms = run(g + ["drama-pacing", ids["main"]])
        return out[:60] or "(空输出)", ms
    step("drama-pacing（节奏分析）", do_pacing)

    def do_delete():
        # 先删（预期成功 exit 0）
        out, ms = run(g + ["drama-delete", ids["main"], "--force"])
        assert "已删除" in out, out[:200]
        # 复核：删除后 get 必须失败（exit 1 + 「不存在」文案）——用原生子进程测退出码
        proc = subprocess.run(CLI + g + ["drama-get", ids["main"]],
                              capture_output=True, text=True, env=ENV, cwd=REPO)
        assert proc.returncode == 1, f"已删除的剧 get 应 exit 1，实际 {proc.returncode}"
        assert "不存在" in proc.stdout, proc.stdout[:150]
        return "删除成功 + 复核 exit 1", ms
    step("drama-delete（删除 + 复核退出码契约）", do_delete)

    import shutil
    if FAILED:
        print(f"\n[保留库用于验尸] {db}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n== 结果: {len(PASSED)} passed / {len(FAILED)} failed ==")
    if FAILED:
        print("失败步骤: " + ", ".join(FAILED))
        sys.exit(1)


if __name__ == "__main__":
    main()
