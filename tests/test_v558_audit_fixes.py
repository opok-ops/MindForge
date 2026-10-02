"""v5.5.8 全面审计修复的回归测试（commit 75bede7）。

为本次审计发现的 7 项真实 Bug 修复提供回归保护，防止后续改动回退。
"""

import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# P0: storage.character_network() 角色关系网引用未定义 a/b
#     （修复前应为 primary_char / partner_char，否则 NameError 崩溃）
# ---------------------------------------------------------------------------
def test_character_network_builds_edges_without_nameerror():
    from mindforge.core.storage import StorageEngine

    db = tempfile.mktemp(suffix=".db")
    try:
        se = StorageEngine(db, encryption=None)
        conn = se._get_conn()
        conn.execute(
            "INSERT INTO drama_characters (id, drama_id, name, role) "
            "VALUES (?,?,?,?)",
            ("c1", "d1", "Alice", "lead"),
        )
        conn.execute(
            "INSERT INTO drama_characters (id, drama_id, name, role) "
            "VALUES (?,?,?,?)",
            ("c2", "d1", "Bob", "lead"),
        )
        conn.execute(
            "INSERT INTO drama_lines (id, drama_id, scene_id, character_id, line_text) "
            "VALUES (?,?,?,?,?)",
            ("l1", "d1", "s1", "c1", "hello"),
        )
        conn.execute(
            "INSERT INTO drama_lines (id, drama_id, scene_id, character_id, line_text) "
            "VALUES (?,?,?,?,?)",
            ("l2", "d1", "s1", "c2", "hi"),
        )
        conn.commit()

        # 修复前：构建边列表时引用未定义变量 a/b -> NameError
        result = se.character_network("d1")

        assert "error" not in result, f"character_network 返回错误: {result}"
        assert len(result["nodes"]) == 2
        # 两个角色在同一场次共同出场 -> 恰好 1 条共现边
        assert len(result["edges"]) == 1
        edge = result["edges"][0]
        # 共现边两端应分别是 Alice 与 Bob（顺序取决于集合迭代，两端任一均可）
        assert {edge["source_name"], edge["target_name"]} == {"Alice", "Bob"}
        assert edge["weight"] == 1
    finally:
        try:
            os.remove(db)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# P1: cli/main.py 多处 except sqlite3.Error 未导入 sqlite3
#     （修复前真实数据库错误会被 NameError 掩盖）
# ---------------------------------------------------------------------------
def test_cli_imports_sqlite3_for_except_branch():
    import mindforge.cli.main
    import sqlite3 as real_sqlite3

    # 修复前 cli/main.py 未 import sqlite3，except sqlite3.Error 分支会触发
    # 二级 NameError，掩盖真实的数据库错误。此处断言模块级 sqlite3 已正确导入。
    assert mindforge.cli.main.sqlite3 is real_sqlite3


# ---------------------------------------------------------------------------
# P1: cli/main.py _main_dispatch() 未知命令时调用 main() 局部变量 parser
#     （修复前 -> NameError 崩溃；修复后基于 commands dict 干净退出）
# ---------------------------------------------------------------------------
def test_main_dispatch_unknown_command_exits_cleanly():
    import mindforge.cli.main

    class _Args:
        command = "__definitely_not_a_real_command__"

    # 修复前：未知命令时引用 main() 的局部 parser -> NameError 崩溃
    with pytest.raises(SystemExit) as exc:
        mindforge.cli.main._main_dispatch(_Args())
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# P1: intent_router IntentResult.level 分级失效
#     （修复前 `2 if self.fallback else 2` 两支相同，兜底与 LLM 路由无法区分）
# ---------------------------------------------------------------------------
def test_intent_result_level_routing():
    from mindforge.modules.intent_router import IntentResult

    base = dict(intent="x", label="x", confidence=1.0, routing="x")

    # 兜底路由（fallback=True，无规则/关键词） -> level 3
    assert IntentResult(**base, fallback=True).to_dict()["level"] == 3

    # 正常 LLM 路由（无规则/关键词，fallback=False） -> level 2
    assert IntentResult(**base, fallback=False).to_dict()["level"] == 2

    # 规则命中 -> level 0
    assert IntentResult(**base, matched_rules=["r1"]).to_dict()["level"] == 0

    # 关键词命中 -> level 1
    assert IntentResult(**base, keyword_hits={"k": 1}).to_dict()["level"] == 1


# ---------------------------------------------------------------------------
# P1: recall 引擎 truncated = chunk 仅为别名，截断会污染原始 MemoryChunk
#     （修复前导致记忆内容被永久截断）
# ---------------------------------------------------------------------------
def test_recall_optimize_context_does_not_mutate_original():
    from mindforge.core.query import MemoryChunk
    from mindforge.modules.recall import RecallEngine

    engine = RecallEngine(storage=MagicMock(), index=MagicMock())

    c1 = MemoryChunk(memory_id="a", content="x" * 1000)  # 250 tokens
    c2 = MemoryChunk(memory_id="b", content="y" * 1000)  # 250 tokens

    # max_tokens=400: c1 适配(250)，c2 溢出且 remaining=150>50 -> 截断副本
    selected = engine._optimize_context_window([c1, c2], max_tokens=400)

    assert len(selected) == 2
    assert selected[0].content == "x" * 1000
    # 截断副本应为 150*4=600 字符
    assert selected[1].content == "y" * 600
    # 关键：原始 c2 内容未被修改（修复前 truncated=chunk 会原地截断原对象）
    assert c2.content == "y" * 1000


# ---------------------------------------------------------------------------
# P1: api/server.py 工作线程 start() 失败并发计数不回退 -> 服务永久 503
#     （修复前计数泄漏直至永久返回 503）
# ---------------------------------------------------------------------------
def test_api_concurrency_slot_rollback_on_thread_start_failure():
    import threading

    from mindforge.api.server import BoundedThreadingHTTPServer

    # 绕过网络绑定的 __init__，仅构造所需属性
    server = object.__new__(BoundedThreadingHTTPServer)
    server._thread_lock = threading.Lock()
    server._active_threads = 0
    server._max_threads = 10

    fake_request = MagicMock()

    # 强制 Thread.start() 抛 RuntimeError（模拟线程资源耗尽）
    with patch.object(
        threading.Thread, "start", side_effect=RuntimeError("no threads")
    ):
        # 不应抛异常；并发计数应在 start 失败后回退
        server.process_request(fake_request, ("127.0.0.1", 1234))

    # 计数回退到 0（修复前会泄漏到 1，最终累积导致永久 503）
    assert server._active_threads == 0
