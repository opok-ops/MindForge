# -*- coding: utf-8 -*-
"""v6.0.0 功能 7&8：有原则的遗忘 + 记忆主动收尾

功能 7 测试要点：
- forget_explain 输出可解释原因列表 + forget_tendency + 因子分解
- HIGH 重要度受保护（tendency 低）
- forget_run(dry_run) 扫描不落库
- forgetting_stats 统计

功能 8 测试要点：
- follow_up_create 缺省 due_at=24h 后；记忆不存在时报错
- follow_up_due 只返回到期未办结
- follow_up_resolve 办结；已办结不再出现在 open 列表
- follow_up_list include_resolved 开关
"""
import os
import shutil
import tempfile
import time
import unittest

from mindforge import MindForge

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_mf():
    tmp = tempfile.mkdtemp(prefix="mf_v600d_")
    mf = MindForge(db_path=os.path.join(tmp, "m.db"), encrypted=False)
    return mf, tmp


class TestPrincipledForgetting(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_explain_returns_reasons(self):
        m = self.mf.add("临时碎片想法", importance="LOW")
        r = self.mf.forget_explain(m.id)
        self.assertTrue(r["success"])
        self.assertGreaterEqual(len(r["explanation"]), 1, "应有可解释原因")
        self.assertIn("forget_tendency", r)
        self.assertIn("factors", r)
        self.assertEqual(r["privacy"], "aggregate-only")

    def test_high_importance_protected(self):
        m = self.mf.add("重要的人生目标", importance="HIGH")
        r = self.mf.forget_explain(m.id)
        self.assertTrue(r["success"])
        self.assertLess(r["forget_tendency"], 0.72, "HIGH 记忆不应倾向遗忘")
        self.assertTrue(any("受保护" in s or "保护" in s for s in r["explanation"]),
                        r["explanation"])

    def test_dry_run_no_archive(self):
        self.mf.add("噪音残留", importance="LOW")
        r = self.mf.forget_run(dry_run=True, days=7)
        self.assertTrue(r["success"])
        self.assertTrue(r["dry_run"])
        st = self.mf.forget_stats()
        self.assertGreaterEqual(st["total_memories"], 1)

    def test_forgetting_stats(self):
        self.mf.add("x", importance="LOW")
        st = self.mf.forget_stats()
        self.assertGreaterEqual(st["total_memories"], 1)
        self.assertIn("archived", st)
        self.assertIn("high_importance", st)


class TestFollowUp(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.m = self.mf.add("用户说下周二前提交方案")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_default_due_24h(self):
        r = self.mf.follow_up_create(self.m.id, note="跟进方案")
        self.assertTrue(r["success"])
        self.assertAlmostEqual(r["due_at"], r["created_at"] + 86400, delta=30)

    def test_create_nonexistent_memory_fails(self):
        r = self.mf.follow_up_create("no_such_id")
        self.assertFalse(r["success"])

    def test_due_only_open(self):
        self.mf.follow_up_create(self.m.id, due_at=time.time() - 60, note="已到期")
        self.mf.follow_up_create(self.m.id, due_at=time.time() + 99999, note="未到期")
        due = self.mf.follow_up_due()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["note"], "已到期")

    def test_resolve_removes_from_open(self):
        r = self.mf.follow_up_create(self.m.id, due_at=time.time() - 60, note="待办")
        rr = self.mf.follow_up_resolve(r["follow_up_id"])
        self.assertTrue(rr["success"])
        due = self.mf.follow_up_due()
        self.assertEqual(len(due), 0)
        all_ = self.mf.follow_up_list(include_resolved=True)
        resolved = [x for x in all_ if x["status"] == "resolved"]
        self.assertGreaterEqual(len(resolved), 1)


if __name__ == "__main__":
    unittest.main()
