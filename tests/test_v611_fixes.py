# -*- coding: utf-8 -*-
"""v6.1.1 回归：演示链路两处缺陷 + 主动催办能力

对应 v6.1.1 修复的两个真实缺陷（均为 v6.0.0 引入，1032 项测试当时全绿却未覆盖）：

1. **权重恒为 0.0**：RecallEngine 写入的得分字段是 relevance_score
   （mindforge/modules/recall.py），而 outcome_learning.search_weighted
   读的是 score，getattr 取不到即落回 0.0。结果「按结果学习的记忆」在
   演示分屏上拿不到任何权重数字。
2. **传对象即崩溃**：mf.add() 返回 MemoryEntry 对象，但 learn_from_outcome /
   follow_up_create / explain_forgetting 直接 len(memory_id)，把返回值传回去
   抛 TypeError 而非友好错误。v5.9.0 已为 get() 修过同类问题，v6.0.0 新代码漏了。

新增能力（功能 8 的「主动」部分）：
- follow_ups 表补 nag_count / last_nagged_at / receipt 三列（幂等 ALTER）
- nag_due()：到期 → 催办 → 生成回执 → 发布 memory.follow_up.due
  （v6.0.0 模块文档一直声称与 EventBus 联动，但全文没有一次 publish 调用）
- 间隔守卫与催办次数上限，避免无限骚扰
"""
import os
import shutil
import tempfile
import time
import unittest

from mindforge import MindForge


def _make_mf():
    tmp = tempfile.mkdtemp(prefix="mf_v611_")
    mf = MindForge(db_path=os.path.join(tmp, "m.db"), encrypted=False)
    return mf, tmp


class TestOutcomeWeightsVisible(unittest.TestCase):
    """缺陷 1：加权检索必须给出可用的相关度数字。"""

    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.ex = self.mf.add("谈合作开价方法X：先报区间再锚定高位，客户当场接受并签约",
                              category="method", importance="MEDIUM")
        self.ey = self.mf.add("谈合作开价方法Y：直接一口价报高，客户觉得贵，谈崩了",
                              category="method", importance="MEDIUM")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mark(self):
        self.mf.learn_from_outcome(self.ex.id, outcome="success", note="上次用X谈成")
        self.mf.learn_from_outcome(self.ey.id, outcome="failure", note="上次用Y谈崩")

    def test_chunk_score_reader_handles_real_field_name(self):
        """_chunk_score 必须读到 relevance_score，而不是恒返回 0。"""
        from mindforge.modules.outcome_learning import _chunk_score

        class _C:
            relevance_score = 0.42

        self.assertAlmostEqual(_chunk_score(_C()), 0.42, places=6)
        for alt in (0.42, None):
            class _D:
                score = alt
            if alt is not None:
                self.assertAlmostEqual(_chunk_score(_D()), 0.42, places=6)
        self.assertEqual(_chunk_score(object()), 0.0)
        self.assertAlmostEqual(_chunk_score({"relevance_score": 0.31}), 0.31, places=6)

    def test_weighted_search_returns_nonzero_relevance(self):
        """回归主断言：演示要用权重，relevance 不得全为 0。"""
        self._mark()
        rows = self.mf.search_weighted_by_outcome("帮我谈个合作，怎么开价？", max_results=5)
        self.assertGreaterEqual(len(rows), 1, "应有召回结果")
        nonzero = [r for r in rows if float(r["relevance"]) > 0.0]
        self.assertEqual(len(nonzero), len(rows),
                         "v6.0.0 缺陷复现：读错字段导致 relevance 恒为 0.0 -> %r" % rows)

    def test_success_memory_ranked_first(self):
        """成功方法必须排在失败方法之前（即使后者原始分更高）。"""
        self._mark()
        rows = self.mf.search_weighted_by_outcome("谈合作开价", max_results=5,
                                                  outcome="success")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["outcome"], "success")
        self.assertEqual(rows[0]["importance"], "HIGH")

    def test_outcome_marks_shift_importance(self):
        """成功升档、失败降档，且档位有界。"""
        r1 = self.mf.learn_from_outcome(self.ex.id, outcome="success")
        self.assertTrue(r1["success"])
        self.assertEqual((r1["importance_before"], r1["importance_after"]),
                         ("MEDIUM", "HIGH"))
        r2 = self.mf.learn_from_outcome(self.ey.id, outcome="failure")
        self.assertEqual((r2["importance_before"], r2["importance_after"]),
                         ("MEDIUM", "LOW"))
        # 已是 HIGH 再成功，不应越界
        r3 = self.mf.learn_from_outcome(self.ex.id, outcome="success")
        self.assertEqual(r3["importance_after"], "HIGH")

    def test_stats_after_marking(self):
        self._mark()
        st = self.mf.outcome_stats()
        self.assertEqual(st["total_marked"], 2)
        self.assertAlmostEqual(st["success_rate"], 0.5, places=3)


class TestMemoryEntryArgument(unittest.TestCase):
    """缺陷 2：add() 返回的对象可直接传入，非法类型给友好错误。"""

    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.m = self.mf.add("下周二前提交方案")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_learn_from_outcome_accepts_entry_object(self):
        """曾抛 TypeError: object of type 'MemoryEntry' has no len()。"""
        r = self.mf.learn_from_outcome(self.m, outcome="success")
        self.assertTrue(r["success"])
        self.assertEqual(r["memory_id"], self.m.id)

    def test_follow_up_create_accepts_entry_object(self):
        r = self.mf.follow_up_create(self.m, note="跟进方案")
        self.assertTrue(r["success"])
        self.assertEqual(r["memory_id"], self.m.id)

    def test_forget_explain_accepts_entry_object(self):
        r = self.mf.forget_explain(self.m)
        self.assertTrue(r["success"])

    def test_garbage_input_is_friendly_error_not_crash(self):
        for bad in (12345, 3.14, ["x"], {"id": 1}, object(), None, ""):
            with self.subTest(arg=repr(bad)[:32]):
                r = self.mf.learn_from_outcome(bad, outcome="success")
                self.assertFalse(r["success"])
                self.assertIn("记忆 ID", r["error"])

    def test_too_long_id_rejected(self):
        r = self.mf.learn_from_outcome("x" * 200, outcome="success")
        self.assertFalse(r["success"])

    def test_invalid_outcome_still_rejected(self):
        r = self.mf.learn_from_outcome(self.m.id, outcome="maybe")
        self.assertFalse(r["success"])
        self.assertIn("无效 outcome", r["error"])

    def test_nonexistent_id_reports_memory_missing(self):
        r = self.mf.learn_from_outcome("0" * 12, outcome="success")
        self.assertFalse(r["success"])
        self.assertEqual(r["error"], "记忆不存在")


class TestProactiveFollowUpNag(unittest.TestCase):
    """功能 8「主动收尾」：到期自动催办并生成可展示回执。"""

    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.m = self.mf.add("和老王谈合同，对方尚未回复")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _overdue(self, hours=72):
        return self.mf.follow_up_create(
            self.m.id, due_at=time.time() - hours * 3600, note="跟进和老王的合同")

    def test_new_columns_exist_and_default_zero(self):
        r = self._overdue()
        self.assertTrue(r["success"])
        self.assertEqual(r["nag_count"], 0)
        self.assertEqual(r["receipt"], "")
        due = self.mf.follow_up_due()
        self.assertIn("nag_count", due[0])
        self.assertIn("receipt", due[0])

    def test_nag_generates_chinese_receipt(self):
        self._overdue(72)
        res = self.mf.follow_up_nag()
        self.assertEqual(len(res["nagged"]), 1)
        item = res["nagged"][0]
        self.assertEqual(item["nag_count"], 1)
        self.assertIn("跟进和老王的合同", item["receipt"])
        self.assertIn("已催了一次", item["receipt"])
        self.assertIn("3 天", item["receipt"])

    def test_nag_count_wording_scales(self):
        self._overdue(72)
        self.mf.follow_up_nag()
        second = self.mf.follow_up_nag(interval_hours=0)
        self.assertEqual(second["nagged"][0]["nag_count"], 2)
        self.assertIn("已催了两", second["nagged"][0]["receipt"])
        third = self.mf.follow_up_nag(interval_hours=0)
        self.assertIn("已催了三", third["nagged"][0]["receipt"])

    def test_interval_guard_blocks_immediate_renag(self):
        """紧接着再催不应重复骚扰。"""
        self._overdue()
        first = self.mf.follow_up_nag()
        self.assertEqual(len(first["nagged"]), 1)
        again = self.mf.follow_up_nag()
        self.assertEqual(again["nagged"], [])
        self.assertEqual(again["skipped_recent"], 1)

    def test_first_nag_is_not_blocked_by_interval(self):
        """nag_count=0 时不受间隔限制，否则首次催办会被跳过。"""
        self._overdue()
        one = self.mf.follow_up_nag()
        self.assertEqual(len(one["nagged"]), 1, "首次催办必须执行")

    def test_max_nags_cap_enforced(self):
        self._overdue()
        for _ in range(4):
            res = self.mf.follow_up_nag(interval_hours=0)
        self.assertEqual(res["nagged"], [], "达到上限后不应继续催")
        self.assertGreaterEqual(res["skipped_maxed"], 1)
        due = self.mf.follow_up_due()
        self.assertEqual(due[0]["nag_count"], 3, "默认上限 3 次")

    def test_not_overdue_is_ignored(self):
        self.mf.follow_up_create(self.m.id, due_at=time.time() + 99999, note="未到期")
        res = self.mf.follow_up_nag()
        self.assertEqual(res["nagged"], [])

    def test_resolved_item_not_nagged(self):
        r = self._overdue()
        self.mf.follow_up_resolve(r["follow_up_id"])
        res = self.mf.follow_up_nag()
        self.assertEqual(res["nagged"], [], "已办结事项不应再催")

    def test_receipt_persisted_for_demo_screenshot(self):
        """回执必须落库，list_due 可直接取到（拍视频用）。"""
        self._overdue()
        self.mf.follow_up_nag()
        due = self.mf.follow_up_due()
        self.assertEqual(due[0]["nag_count"], 1)
        self.assertTrue(due[0]["receipt"], "回执需持久化，否则退出进程后就没了")

    def test_receipt_survives_reopen(self):
        self._overdue()
        self.mf.follow_up_nag()
        fid = self.mf.follow_up_due()[0]["follow_up_id"]
        db = self.mf.config.db_path
        self.mf.close()
        reopened = MindForge(db_path=db, encrypted=False)
        self.mf = reopened  # 交回 tearDown 统一关闭并清理 tmp
        rows = [x for x in reopened.follow_up_list(include_resolved=True)
                if x["follow_up_id"] == fid]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["nag_count"], 1)
        self.assertTrue(rows[0]["receipt"])

    def test_nag_is_audited_and_whitelisted(self):
        """新动作必须在审计白名单内，否则被降级记成 other。"""
        self._overdue()
        self.mf.follow_up_nag()
        entries = self.mf.export_audit(limit=50)
        self.assertIsInstance(entries, list, "export_audit 返回条目列表")
        actions = {e.get("action") for e in entries}
        self.assertIn("follow_up_nag", actions,
                      "follow_up_nag 未入审计白名单: %r" % sorted(a for a in actions if a))

    def test_nag_bad_args_do_not_crash(self):
        res = self.mf.follow_up_nag(interval_hours=-5, max_nags=0)
        self.assertIsInstance(res, dict)
        self.assertIn("nagged", res)


class TestEventBusWiring(unittest.TestCase):
    """模块文档自 v6.0.0 起声称联动 EventBus，本版必须真的发出去。"""

    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.m = self.mf.add("和老王确认合同条款")

    def tearDown(self):
        if self.mf is not None:
            self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_due_event_published_on_nag(self):
        from mindforge.modules.event_bus import EventBus

        seen = []
        bus = EventBus()
        bus.subscribe("memory.follow_up.due", lambda p: seen.append(p))
        self.mf._event_bus = bus
        self.mf.follow_up_create(self.m.id, due_at=time.time() - 3600, note="合同")
        self.mf.follow_up_nag()
        self.assertEqual(len(seen), 1, "催办必须发布 memory.follow_up.due")
        # EventBus 用信封包装：事件名/时间戳/来源在外层，业务负载在 data
        self.assertEqual(seen[0]["event"], "memory.follow_up.due")
        payload = seen[0]["data"]
        self.assertIn("receipt", payload)
        self.assertEqual(payload["nag_count"], 1)

    def test_event_failure_does_not_break_nag(self):
        """事件仅为通知面，发布异常不得影响催办落库。"""
        class _Boom:
            def publish(self, *a, **k):
                raise RuntimeError("bus down")

        self.mf._event_bus = _Boom()
        self.mf.follow_up_create(self.m.id, due_at=time.time() - 3600, note="合同")
        res = self.mf.follow_up_nag()
        self.assertEqual(len(res["nagged"]), 1, "事件失败仍须完成催办")
        self.assertEqual(self.mf.follow_up_due()[0]["nag_count"], 1)


class TestToolCountConsistency(unittest.TestCase):
    """新增 follow_up_nag 后，工具数与三层注册必须自洽。"""

    def test_tool_count_is_87(self):
        from mindforge.mcp.server import TOOL_SCHEMAS, HANDLERS

        names = [s["name"] for s in TOOL_SCHEMAS]
        self.assertEqual(len(names), 87)
        self.assertEqual(len(set(names)), len(names), "工具名不得重复")
        self.assertEqual(set(names) - set(HANDLERS), set(), "有 schema 无 handler")
        self.assertEqual(set(HANDLERS) - set(names), set(), "有 handler 无 schema")

    def test_tools_list_endpoint_agrees(self):
        from mindforge.mcp.server import _handle_tools_list

        self.assertEqual(len(_handle_tools_list({})["tools"]), 87)

    def test_nag_tool_schema_shape(self):
        from mindforge.mcp.server import TOOL_SCHEMAS

        sch = {s["name"]: s for s in TOOL_SCHEMAS}["follow_up_nag"]
        self.assertEqual(sch["inputSchema"]["required"], [])
        self.assertIn("interval_hours", sch["inputSchema"]["properties"])
        self.assertIn("max_nags", sch["inputSchema"]["properties"])

    def test_cli_exposes_nag_command(self):
        from mindforge.cli import main as cli_main
        import inspect

        src = inspect.getsource(cli_main)
        self.assertIn('"follow-up-nag"', src, "CLI 未注册 follow-up-nag")
        self.assertIn("def cmd_follow_up_nag", src)

    def test_rest_exposes_nag_route(self):
        import io
        import os as _os

        root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        with io.open(_os.path.join(root, "mindforge", "api", "server.py"),
                     encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"/api/follow-ups/nag"', src, "REST 未接入催办端点")


if __name__ == "__main__":
    unittest.main()
