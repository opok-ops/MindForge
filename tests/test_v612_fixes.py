# -*- coding: utf-8 -*-
"""v6.1.2 回归测试：本轮修复的真实缺陷 + 三个「写在文档里却从未实现」的参数。

与既往版本一样，这些缺陷在 v6.1.1 的 1063 项测试里全绿——因为它们都在
「调用方把 add() 的返回值原样传回」「REST 传了 dict」这类非典型入参路径上，
而降级分支（except 里记日志）本身又是坏的。分成四组：

A. 崩溃 / 数据损坏类
   1. MCP `tools/call` 无论工具成败都返回成功信封 → 客户端把报错当结果。
   2. `list_evolutions` / `list_versions` / `save_evolution` / `evolve_memory`
      对 `mf.add()` 返回的 MemoryEntry 直接 `len()` → TypeError（v6.1.1 同类）。
   3. `evolve_memory(content=dict)` 被 `_strip_control()` 静默 `str()`，
      把 Python repr 写进库后又在 `to_preview[:200]` 崩溃 → 半途失败。
   4. `record_experience(content=dict)` 抛 `sqlite3.ProgrammingError`，
      REST/MCP 的友好错误分支接不住 → 对外 500。
   5. intent_router / personality / share_conflict 的降级分支引用未定义的
      `logger` → 兜底路径本身 NameError。
   6. `search_multimodal` 拿 MemoryType 枚举和 str 列表比对：带过滤 = 静默漏检，
      不带过滤 = `MultimodalType(枚举)` 直接 ValueError。
   7. `CrossEncoderReranker.rerank` 里唯一一处 `c.get(...)` 破坏 v5.4.7 H-5
      承诺的 dataclass 支持（MemoryChunk 没有 .get）。
   8. `incremental_index_stats` 读 StorageEngine 上不存在的方法，异常被宽
      except 吞掉 → `embedding_available` 恒为 False。
   9. `list_skills(limit=N)` 先截断后排序 → 返回「插入序前 N」而非全局 top-N。
  10. `timeline_viz` 读不存在的 `is_archived` 字段 → active 恒等于 total。

B. 文档承诺但未实现的参数（本版真正接通，三层一致）
  11. `forget_run(days=...)`：函数体从未引用 days → 现在是真实的冷却窗口。
  12. `close_preview_channel(purge=...)`：签名里没有该参数，无条件删文件。
  13. `MAX_EXPORT_SIZE`：常量存在但全项目零引用 → 现在是读取前的真护栏。

C. 其他
  14. `distill_context` MEDIUM 层注释承诺「保头 60% + 保尾 20%」，代码只保头。
  15. `distill_context` 的 id 列表混入对象元素 → 整次蒸馏失败。
"""
import importlib
import inspect
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from mindforge import MindForge
from mindforge.core.types import MemoryType, coerce_memory_id


def _make_mf():
    tmp = tempfile.mkdtemp(prefix="mf_v612_")
    mf = MindForge(db_path=os.path.join(tmp, "m.db"), encrypted=False)
    return mf, tmp


class _MfCase(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# A-1 MCP 失败必须被标记
# ---------------------------------------------------------------------------

class TestMCPErrorIsFlagged(_MfCase):
    """工具层失败必须以 isError 标记，而不是伪装成成功结果。"""

    def test_is_tool_error_only_accepts_explicit_false(self):
        from mindforge.mcp.server import _is_tool_error

        self.assertTrue(_is_tool_error({"ok": False, "error": "boom"}))
        self.assertTrue(_is_tool_error({"success": False}))
        # ok=True 但 success=False：任一显式 False 即视为失败
        self.assertTrue(_is_tool_error({"ok": True, "success": False}))
        # 缺失字段 / 真值 / 非 dict 都按成功处理（避免误判正常结果）
        self.assertFalse(_is_tool_error({"items": []}))
        self.assertFalse(_is_tool_error({"ok": True, "n": 5}))
        self.assertFalse(_is_tool_error({"ok": 0}))   # 只认显式 False，不当 0 用
        self.assertFalse(_is_tool_error("text"))
        self.assertFalse(_is_tool_error(None))

    def test_tools_call_sets_is_error_on_failure(self):
        from mindforge.mcp.server import _handle_tools_call

        env = _handle_tools_call(self.mf, {"params": {
            "name": "preview_close", "arguments": {}}})   # 缺 channel_id
        self.assertTrue(env.get("isError") is True)
        self.assertEqual(env["content"][0]["type"], "text")
        self.assertIn("Missing required parameter", env["content"][0]["text"])

    def test_tools_call_marks_business_failure(self):
        from mindforge.mcp.server import _handle_tools_call

        env = _handle_tools_call(self.mf, {"params": {
            "name": "preview_close", "arguments": {"channel_id": "不存在"}}})
        self.assertTrue(env.get("isError") is True)

    def test_success_envelope_has_no_is_error(self):
        from mindforge.mcp.server import _handle_tools_call

        env = _handle_tools_call(self.mf, {"params": {
            "name": "preview_channels", "arguments": {}}})
        self.assertNotIn("isError", env)
        self.assertIn("content", env)


# ---------------------------------------------------------------------------
# A-2 memory_id 归一（v6.1.1 同类缺陷的收口）
# ---------------------------------------------------------------------------

class TestMemoryIdCoercion(_MfCase):

    def test_helper_accepts_str_and_entry(self):
        e = self.mf.add("归一测试")
        self.assertEqual(coerce_memory_id(e.id), e.id)
        self.assertEqual(coerce_memory_id(e), e.id)

    def test_helper_rejects_junk(self):
        self.assertIsNone(coerce_memory_id(None))
        self.assertIsNone(coerce_memory_id(""))
        self.assertIsNone(coerce_memory_id(12345))
        self.assertIsNone(coerce_memory_id({"id": "abc"}))    # dict 的 id 是属性而非键
        self.assertIsNone(coerce_memory_id(object()))
        self.assertIsNone(coerce_memory_id("x" * 129))        # 超长视为非法

    def test_helper_tolerates_raising_id_property(self):
        """「.id 属性访问抛异常」的病态对象必须与非法类型同等对待，不得漏出。"""
        class _Boom:
            @property
            def id(self):
                raise RuntimeError("boom")

        self.assertIsNone(coerce_memory_id(_Boom()))

    def test_list_evolutions_and_versions_accept_entry_object(self):
        e = self.mf.add("演变入参归一", importance="MEDIUM")
        r = self.mf.evolve_memory(e, content="演变后的内容", reason="测试归一")
        self.assertTrue(r["success"], r.get("error"))
        self.assertGreaterEqual(len(self.mf.list_evolutions(e)), 1)
        self.assertGreaterEqual(len(self.mf.list_versions(e)), 1)

    def test_junk_id_gives_empty_list_not_crash(self):
        self.assertEqual(self.mf.list_evolutions({"bad": 1}), [])
        self.assertEqual(self.mf.list_versions(None), [])
        self.assertEqual(
            self.mf._storage.save_evolution(object())["success"], False)

    def test_facade_evolve_memory_accepts_entry_object(self):
        """门面此前在 get_memory 处就抛 ValueError（v6.1.1 修过同类三处）。"""
        e = self.mf.add("门面归一")
        r = self.mf.evolve_memory(e, content="新内容", reason="门面")
        self.assertTrue(r["success"], r.get("error"))


# ---------------------------------------------------------------------------
# A-3 非字符串内容：拒绝，而不是写坏再崩
# ---------------------------------------------------------------------------

class TestEvolveRejectsNonStringWithoutPartialWrite(_MfCase):

    def test_dict_content_rejected_before_write(self):
        e = self.mf.add("原始内容", category="note")
        r = self.mf.evolve_memory(e.id, content={"a": 1}, reason="错误入参")
        self.assertFalse(r["success"])
        self.assertIn("content 必须是字符串", r["error"])
        # 关键：库里的内容必须仍是原始文本（修复前会先写入 repr 再崩溃）
        self.assertEqual(self.mf._storage.get_memory(e.id).content, "原始内容")
        self.assertEqual(self.mf.list_evolutions(e.id), [])

    def test_non_string_reason_rejected(self):
        e = self.mf.add("原因类型测试")
        r = self.mf.evolve_memory(e.id, content="新内容", reason=["列", "表"])
        self.assertFalse(r["success"])
        # 内容同样不得被半路写坏
        self.assertEqual(self.mf._storage.get_memory(e.id).content, "原因类型测试")

    def test_save_evolution_serializes_non_string_preview(self):
        e = self.mf.add("预览序列化")
        r = self.mf._storage.save_evolution(
            e.id, from_version=1, to_version=2,
            to_preview={"new": "值"}, reason=None, category=["x"], actor=7)
        self.assertTrue(r["success"], r.get("error"))
        rows = self.mf.list_evolutions(e.id)
        self.assertEqual(len(rows), 1)
        self.assertIn("new", rows[0]["to_preview"])
        self.assertEqual(rows[0]["reason"], "")     # None → 空串
        self.assertEqual(rows[0]["actor"], "7")     # 数字转文本，落库形态一致


# ---------------------------------------------------------------------------
# A-4 经验链路入参类型
# ---------------------------------------------------------------------------

class TestExperienceInputTypes(_MfCase):

    def test_dict_content_raises_value_error_not_programming_error(self):
        with self.assertRaises(ValueError) as ctx:
            self.mf.record_experience("任务", content={"a": 1})
        self.assertIn("content", str(ctx.exception))

    def test_dict_result_and_agent_id_also_rejected(self):
        for kwargs in ({"result": {"r": 1}}, {"agent_id": ["a"]}):
            with self.assertRaises(ValueError):
                self.mf.record_experience("任务", **kwargs)

    def test_none_text_fields_become_empty_strings(self):
        case = self.mf.record_experience("任务", content=None, result=None,
                                        agent_id=None)
        self.assertEqual(case["content"], "")
        self.assertEqual(case["result"], "")
        self.assertEqual(case["agent_id"], "")

    def test_invalid_still_recordable_after_rejection(self):
        """拒绝坏入参不得污染存储：坏之后仍要能正常记录。"""
        with self.assertRaises(ValueError):
            self.mf.record_experience("坏任务", content={"x": 1})
        case = self.mf.record_experience("好任务", content="正文")
        self.assertEqual(case["content"], "正文")


# ---------------------------------------------------------------------------
# A-5 降级分支的 logger
# ---------------------------------------------------------------------------

class _BoomStorage:
    def list_memories(self, *a, **k):
        raise sqlite3.OperationalError("no such table: memories")


class TestDegradationPathsHaveLogger(_MfCase):

    def test_modules_define_module_logger(self):
        for mod in ("intent_router", "personality", "share_conflict"):
            m = importlib.import_module("mindforge.modules." + mod)
            self.assertIsInstance(getattr(m, "logger", None), logging.Logger,
                                 f"{mod} 缺少模块级 logger（降级分支会 NameError）")
            self.assertEqual(m.logger.name, "mindforge.modules." + mod)

    def test_personality_load_degrades_without_name_error(self):
        with self.assertLogs("mindforge.modules.personality", level="WARNING"):
            engine = importlib.import_module(
                "mindforge.modules.personality").PersonalityEngine(_BoomStorage())
        self.assertEqual(engine.profiles, {})

    def test_intent_router_llm_failure_falls_back(self):
        mod = importlib.import_module("mindforge.modules.intent_router")

        def boom(_text):
            raise RuntimeError("LLM 已下线")

        router = mod.IntentRouter(llm_classifier=boom, min_confidence=0.99)
        with self.assertLogs("mindforge.modules.intent_router", level="WARNING"):
            result = router.classify("zzz 完全无意义的输入 qqq")
        self.assertTrue(result.intent)


# ---------------------------------------------------------------------------
# A-6 多模态过滤
# ---------------------------------------------------------------------------

class _StubEntry:
    def __init__(self, memory_type, content="正文"):
        self.memory_type = memory_type
        self.content = content


class _StubIndex:
    def __init__(self, pairs):
        self._pairs = pairs

    def search(self, query, top_k=10):
        return list(self._pairs)


class _StubStorage:
    def __init__(self, mapping):
        self._mapping = mapping

    def get_memory(self, doc_id):
        return self._mapping.get(doc_id)


def _mm():
    from mindforge.modules.multimodal import MultimodalMemory
    storage = _StubStorage({
        "a": _StubEntry(MemoryType.CODE),
        "b": _StubEntry(MemoryType.TEXT),
        "c": _StubEntry("unknown-type"),
    })
    index = _StubIndex([("a", 0.9), ("b", 0.5), ("c", 0.4)])
    return MultimodalMemory(storage=storage, index=index)


class TestMultimodalTypeFilter(unittest.TestCase):

    def test_filter_with_enum_returns_hits(self):
        """修复前：枚举 vs 字符串永不相等 → 只要传过滤就全被丢弃。"""
        from mindforge.modules.multimodal import MultimodalType
        hits = _mm().search_multimodal("查询", content_types=[MultimodalType.CODE])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0].content_type, MultimodalType.CODE)

    def test_no_filter_does_not_crash(self):
        """修复前：不传过滤走到 MultimodalType(枚举对象) → ValueError。"""
        from mindforge.modules.multimodal import MultimodalType
        hits = _mm().search_multimodal("查询")
        self.assertEqual(len(hits), 3)
        types = {h[0].content_type for h in hits}
        self.assertIn(MultimodalType.CODE, types)
        self.assertIn(MultimodalType.TEXT, types)

    def test_bare_string_filter_is_accepted(self):
        hits = _mm().search_multimodal("查询", content_types=["text"])
        self.assertEqual(len(hits), 1)

    def test_unknown_type_exposed_as_text_instead_of_dropped(self):
        hits = _mm().search_multimodal("查询", limit=10)
        self.assertTrue(any(h[0].content_type.value == "text" for h in hits))


# ---------------------------------------------------------------------------
# A-7 重排支持 dataclass 候选
# ---------------------------------------------------------------------------

class TestRerankAcceptsDataclass(unittest.TestCase):

    def test_memory_chunk_candidates(self):
        from mindforge.core.query import MemoryChunk
        from mindforge.modules.hybrid_search import CrossEncoderReranker

        chunk = MemoryChunk(memory_id="mem_1", content="季度营收增长两成")
        out = CrossEncoderReranker().rerank("营收", [chunk], top_k=5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].memory_id, "mem_1")

    def test_dict_candidates_still_work(self):
        from mindforge.modules.hybrid_search import CrossEncoderReranker

        out = CrossEncoderReranker().rerank(
            "营收", [{"memory_id": "mem_2", "content": "营收上涨",
                      "original_score": 1.5}], top_k=5)
        self.assertEqual(out[0].memory_id, "mem_2")

    def test_id_field_fallback(self):
        """MemoryChunk 用 memory_id，但部分候选只有 id：两者都要能取到。"""
        from mindforge.modules.hybrid_search import CrossEncoderReranker

        class _Only:
            id = "mem_3"
            content = "只有 id 字段的候选"

        out = CrossEncoderReranker().rerank("候选", [_Only()], top_k=5)
        self.assertEqual(out[0].memory_id, "mem_3")


# ---------------------------------------------------------------------------
# A-8 增量索引统计的嵌入可用性
# ---------------------------------------------------------------------------

class TestEmbeddingAvailabilityReported(_MfCase):

    def _stats(self):
        from mindforge.modules.v6 import MemoryPreviewPort
        return MemoryPreviewPort(self.mf._storage).incremental_index_stats()

    def test_true_when_engine_is_ready(self):
        class _Ready:
            is_available = True

        self.mf._storage._embedding_eng = _Ready()
        ix = self._stats()
        self.assertTrue(ix["index"]["embedding_available"],
                        "嵌入后端就绪时健康报告必须显示可用（此前恒为 False）")

    def test_false_when_engine_missing_or_unavailable(self):
        self.mf._storage._embedding_eng = None
        self.assertFalse(self._stats()["index"]["embedding_available"])

        class _NotReady:
            is_available = False

        self.mf._storage._embedding_eng = _NotReady()
        self.assertFalse(self._stats()["index"]["embedding_available"])

    def test_other_fields_intact(self):
        self.mf.add("用于统计的记忆")
        ix = self._stats()
        self.assertTrue(ix["success"])
        self.assertGreaterEqual(ix["total_memories"], 1)
        self.assertGreaterEqual(ix["new_24h"], 1)
        self.assertIn("fts_available", ix["index"])


# ---------------------------------------------------------------------------
# A-9 技能列表：全局 top-N
# ---------------------------------------------------------------------------

class TestSkillListGlobalTopN(_MfCase):

    def _store(self):
        from mindforge.modules.experience import SkillStore
        from mindforge.modules.skill_extractor import SkillTemplate
        store = SkillStore(storage=self.mf._storage)
        confidences = {"早入库低分": 0.10, "早入库高分": 0.90,
                       "晚入库次高": 0.80, "晚入库低分": 0.05}
        for name, conf in confidences.items():
            store._skills[name] = SkillTemplate(
                name=name, description="d", confidence=conf, cluster_size=1)
        return store

    def test_top_n_is_global_not_insertion_order(self):
        store = self._store()
        out = store.list_skills(limit=2)
        self.assertEqual([s["name"] for s in out], ["早入库高分", "晚入库次高"])

    def test_sorted_desc_and_clamped(self):
        store = self._store()
        all_skills = store.list_skills(limit=100)
        self.assertEqual(len(all_skills), 4)
        confs = [s["confidence"] for s in all_skills]
        self.assertEqual(confs, sorted(confs, reverse=True))
        self.assertEqual(len(store.list_skills(limit=0)), 1)
        self.assertEqual(len(store.list_skills(limit=99999)), 4)


# ---------------------------------------------------------------------------
# A-10 时间线的活跃计数
# ---------------------------------------------------------------------------

class TestTimelineActiveMemories(_MfCase):

    def test_expired_window_not_counted_active(self):
        from mindforge.modules.timeline_viz import build_timeline_visual
        self.mf.add("仍在有效期内的记忆")
        self.mf.add("已过有效期的记忆", valid_to=time.time() - 3600)
        viz = build_timeline_visual(self.mf._storage, days=7)
        self.assertEqual(viz["total_memories"], 2)
        self.assertEqual(viz["active_memories"], 1)

    def test_no_boundary_counts_as_active(self):
        from mindforge.modules.timeline_viz import build_timeline_visual
        self.mf.add("无边界记忆", valid_to=0.0)
        viz = build_timeline_visual(self.mf._storage, days=7)
        self.assertEqual(viz["active_memories"], viz["total_memories"])


# ---------------------------------------------------------------------------
# B-1 forget_run(days) 冷却窗口
# ---------------------------------------------------------------------------

class TestForgetCoolingWindow(_MfCase):

    def setUp(self):
        super().setUp()
        for i in range(3):
            self.mf.add(f"噪音{i}", category="note", importance="LOW")

    def test_days_blocks_fresh_memories(self):
        r = self.mf.forget_run(dry_run=True, days=30)
        self.assertEqual(r["days"], 30)
        self.assertGreaterEqual(r["skipped_too_fresh"], 3)
        self.assertEqual(r["candidates"], 0, "冷却期内不得出现归档候选")

    def test_days_zero_disables_cooling(self):
        r = self.mf.forget_run(dry_run=True, days=0)
        self.assertEqual(r["skipped_too_fresh"], 0)
        self.assertEqual(r["days"], 0)
        self.assertGreaterEqual(r["scanned"], 3)

    def test_high_importance_stays_protected(self):
        self.mf.add("高价值", importance="HIGH")
        r = self.mf.forget_run(dry_run=True, days=0)
        self.assertGreaterEqual(r["protected_high_importance"], 1)

    def test_bad_days_falls_back_to_default(self):
        r = self.mf.forget_run(dry_run=True, days="abc")
        self.assertEqual(r["days"], 30)


# ---------------------------------------------------------------------------
# B-2 close_preview_channel(purge)
# ---------------------------------------------------------------------------

class TestPreviewChannelPurge(_MfCase):

    def _open(self):
        from mindforge.modules.v6 import MemoryPreviewPort
        port = MemoryPreviewPort(self.mf._storage)
        ch = port.open_preview_channel(config={"name": "purge-test"})
        self.assertTrue(ch["success"])
        return port, ch["channel_id"], ch["db_path"]

    def test_purge_false_keeps_file(self):
        """文档自 v6.0.0 就承诺 purge=False 保留文件，此前签名里根本没这个参数。"""
        port, cid, db_path = self._open()
        r = port.close_preview_channel(cid, purge=False)
        self.assertTrue(r["success"])
        self.assertEqual(r["status"], "closed")
        self.assertFalse(r["purged"])
        self.assertTrue(os.path.exists(db_path), "purge=False 必须保留预览库文件")
        self.assertEqual(r["db_path_kept"], db_path)

    def test_purge_true_removes_file(self):
        port, cid, db_path = self._open()
        r = port.close_preview_channel(cid)
        self.assertTrue(r["purged"])
        self.assertFalse(os.path.exists(db_path))

    def test_close_missing_channel_still_fails_gracefully(self):
        port, _, _ = self._open()
        r = port.close_preview_channel("no-such-channel")
        self.assertFalse(r["success"])

    def test_three_layers_expose_purge(self):
        from mindforge.mcp.server import TOOL_SCHEMAS
        sch = {s["name"]: s for s in TOOL_SCHEMAS}
        self.assertIn("purge", sch["preview_close"]["inputSchema"]["properties"])


# ---------------------------------------------------------------------------
# B-3 MAX_EXPORT_SIZE 真护栏
# ---------------------------------------------------------------------------

class TestExportSizeGuard(_MfCase):

    def test_helper(self):
        from mindforge.modules.importer import (MAX_EXPORT_SIZE,
                                                export_size_error)
        self.assertIsNone(export_size_error(0))
        self.assertIsNone(export_size_error(MAX_EXPORT_SIZE))
        msg = export_size_error(MAX_EXPORT_SIZE + 1)
        self.assertIn("超过上限", msg)

    def test_facade_rejects_oversized_raw_payload(self):
        import mindforge.modules.importer as importer
        old = importer.MAX_EXPORT_SIZE
        importer.MAX_EXPORT_SIZE = 64
        try:
            r = importer.import_chat_history(self.mf, "x" * 100)
            self.assertFalse(r["success"])
            self.assertIn("超过上限", r["error"])
        finally:
            importer.MAX_EXPORT_SIZE = old

    def test_cli_checks_size_before_parsing(self):
        """CLI 必须在读文件之前拦截，否则护栏形同虚设。"""
        from mindforge.cli import main as cli_main
        src = inspect.getsource(cli_main.cmd_import_chat)
        self.assertIn("export_size_error(os.path.getsize", src)
        self.assertLess(src.index("export_size_error"),
                        src.index("json.load(f)"))


# ---------------------------------------------------------------------------
# C 上下文蒸馏：保头保尾 + 元素归一
# ---------------------------------------------------------------------------

class TestDistillContext(_MfCase):

    def _port(self):
        from mindforge.modules.v6 import MemoryPreviewPort
        return MemoryPreviewPort(self.mf._storage)

    def test_medium_layer_keeps_head_and_tail(self):
        e = self.mf.add("开头标记" + "中" * 200 + "结尾标记",
                        category="note", importance="MEDIUM")
        d = self._port().distill_context([e.id], max_tokens=2000)
        self.assertTrue(d["success"])
        text = d["layers"]["MEDIUM"][0]["content"]
        self.assertTrue(text.startswith("开头标记"))
        self.assertTrue(text.endswith("结尾标记"), "注释承诺保尾，此前结尾整段丢失")
        self.assertIn("中段已压缩", text)
        self.assertLess(len(text), len(e.content))

    def test_short_medium_content_left_intact(self):
        e = self.mf.add("短内容不需要压缩", category="note", importance="MEDIUM")
        d = self._port().distill_context([e.id], max_tokens=2000)
        self.assertEqual(d["layers"]["MEDIUM"][0]["content"], e.content)

    def test_entry_objects_in_id_list_are_accepted(self):
        e1 = self.mf.add("对象混入测试一", importance="HIGH")
        e2 = self.mf.add("对象混入测试二", category="note", importance="MEDIUM")
        d = self._port().distill_context([e1, e2], max_tokens=400)
        self.assertTrue(d["success"])
        self.assertEqual(len(d["layers"]["HIGH"]), 1)
        self.assertEqual(len(d["layers"]["MEDIUM"]), 1)

    def test_unrecognisable_ids_are_skipped(self):
        e = self.mf.add("跳过坏 ID", importance="HIGH")
        d = self._port().distill_context([{"nope": 1}, e], max_tokens=400)
        self.assertTrue(d["success"])
        self.assertEqual(len(d["layers"]["HIGH"]), 1)


# ---------------------------------------------------------------------------
# 三层接线 + 数量守卫
# ---------------------------------------------------------------------------

class TestThreeLayerWiring(_MfCase):

    def test_mcp_forget_run_schema_has_days(self):
        from mindforge.mcp.server import TOOL_SCHEMAS
        sch = {s["name"]: s for s in TOOL_SCHEMAS}
        self.assertIn("days", sch["forget_run"]["inputSchema"]["properties"])

    def test_mcp_forget_run_passes_days(self):
        from mindforge.mcp.server import _handle_tools_call
        env = _handle_tools_call(self.mf, {"params": {
            "name": "forget_run", "arguments": {"dry_run": True, "days": 7}}})
        payload = json.loads(env["content"][0]["text"])
        self.assertEqual(payload["days"], 7)
        self.assertNotIn("isError", env)

    def test_mcp_preview_close_passes_purge(self):
        from mindforge.mcp.server import _handle_tools_call
        from mindforge.modules.v6 import MemoryPreviewPort
        port = MemoryPreviewPort(self.mf._storage)
        cid = port.open_preview_channel(config={"name": "mcp-purge"})["channel_id"]
        env = _handle_tools_call(self.mf, {"params": {
            "name": "preview_close",
            "arguments": {"channel_id": cid, "purge": False}}})
        payload = json.loads(env["content"][0]["text"])
        self.assertFalse(payload["purged"])

    def test_cli_flags_exist(self):
        """走真实 argparse 入口，确认新参数在 CLI 层可用。"""
        import contextlib
        import io

        from mindforge.cli import main as cli_main

        for argv, flag in ((["forget-run", "--help"], "--days"),
                           (["preview-close", "--help"], "--keep-files")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    cli_main.main(argv)
                except SystemExit:
                    pass      # argparse --help 以 SystemExit(0) 结束
            self.assertIn(flag, buf.getvalue(), f"{argv[0]} 未暴露 {flag}")

    def test_rest_routes_pass_the_new_params(self):
        from mindforge.api import server as api_server
        src = inspect.getsource(api_server)
        self.assertIn('body.get("days"', src)
        self.assertIn('body.get("purge"', src)

    def test_tool_count_still_consistent(self):
        from mindforge.mcp.server import TOOL_SCHEMAS, HANDLERS
        names = [s["name"] for s in TOOL_SCHEMAS]
        self.assertEqual(len(names), 87)
        self.assertEqual(set(names) ^ set(HANDLERS), set())


class TestVersionConsistency(unittest.TestCase):

    def test_version_is_612(self):
        from mindforge.core.version import VERSION_INFO, __version__
        # v6.1.3：版本钉值随发版推进更新（历史方法名保留）
        self.assertEqual(__version__, "6.4.2")
        self.assertEqual(VERSION_INFO, (6, 4, 2))

    def test_json_round_trip_safety_of_preview(self):
        """save_evolution 的预览序列化不得抛（不可 json 化的对象走 str 兜底）。"""
        class _Odd:
            def __str__(self):
                return "odd-object"

        mf, tmp = _make_mf()
        try:
            e = mf.add("序列化兜底")
            r = mf._storage.save_evolution(e.id, to_preview=_Odd())
            self.assertTrue(r["success"])
            self.assertEqual(mf.list_evolutions(e.id)[0]["to_preview"],
                             "odd-object"[:200])
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
