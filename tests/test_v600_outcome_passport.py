# -*- coding: utf-8 -*-
"""v6.0.0 功能 5&6：按结果学习的记忆 + 记忆护照（跨模型迁移）

功能 5 测试要点：
- learn_from_outcome: success 上调重要度 / failure 下调
- 无效 outcome 拒绝
- outcome_stats 统计
- search_weighted_by_outcome: success 命中的记忆排前

功能 6 测试要点：
- export_passport 含 schema/passport_id/记忆/偏好/技能
- import 到新实例后可检索（跨模型迁移）
- passport_status 显示来源
- revoke 撤销后来源记忆删除（可逆）
"""
import os
import shutil
import tempfile
import unittest

from mindforge import MindForge

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_mf():
    tmp = tempfile.mkdtemp(prefix="mf_v600c_")
    mf = MindForge(db_path=os.path.join(tmp, "m.db"), encrypted=False)
    return mf, tmp


class TestOutcomeLearning(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.m = self.mf.add("用 A/B 测试方法谈成了大客户", importance="MEDIUM")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_success_raises_importance(self):
        r = self.mf.learn_from_outcome(self.m.id, outcome="success",
                                       note="谈判成功")
        self.assertTrue(r["success"])
        self.assertEqual(r["importance_before"], "MEDIUM")
        self.assertEqual(r["importance_after"], "HIGH")

    def test_failure_lowers_importance(self):
        m = self.mf.add("失败的方案 B", importance="MEDIUM")
        r = self.mf.learn_from_outcome(m.id, outcome="failure")
        self.assertTrue(r["success"])
        self.assertEqual(r["importance_after"], "LOW")

    def test_invalid_outcome_rejected(self):
        r = self.mf.learn_from_outcome(self.m.id, outcome="maybe")
        self.assertFalse(r["success"])

    def test_outcome_stats(self):
        self.mf.learn_from_outcome(self.m.id, outcome="success")
        st = self.mf.outcome_stats()
        self.assertGreaterEqual(st["total_marked"], 1)
        self.assertGreaterEqual(st["distribution"].get("success", 0), 1)

    def test_weighted_search_prefers_success(self):
        self.mf.add("失败的谈判套路", importance="MEDIUM")
        self.mf.learn_from_outcome(self.m.id, outcome="success")
        w = self.mf.search_weighted_by_outcome("大客户", outcome="success",
                                               max_results=5)
        self.assertGreaterEqual(len(w), 1)
        self.assertEqual(w[0]["outcome"], "success")


class TestMemoryPassport(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.mf.add("我喜欢极简设计", category="preference",
                    tags=["user_preference"])
        self.mf.add("长期目标是做独立开发者", category="goal")
        self.mf.record_experience(task="写周报", content="清单式结构", outcome="success")
        self.mf.distill_skills(min_cluster_size=1, max_skills=5)

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_format(self):
        r = self.mf.passport_export()
        self.assertTrue(r["success"])
        pp = r["passport"]
        self.assertEqual(pp["format"], "mindforge-memory-passport")
        self.assertEqual(pp["format_version"], 1)
        self.assertTrue(pp["passport_id"])
        self.assertGreaterEqual(pp["counts"]["memories"], 2)
        self.assertGreaterEqual(pp["counts"]["preferences"], 1)
        self.assertGreaterEqual(pp["counts"]["skills"], 1)

    def test_import_into_new_instance(self):
        r = self.mf.passport_export()
        mf2, tmp2 = _make_mf()
        try:
            ri = mf2.passport_import(r["passport"], mode="merge")
            self.assertTrue(ri["success"])
            self.assertGreaterEqual(ri["memories_imported"], 2)
            # 跨实例可检索（迁移成功）
            res = mf2.search("极简设计", max_results=5)
            texts = [c.content for c in res.chunks]
            self.assertTrue(any("极简设计" in t for t in texts),
                            "护照导入后记忆可被新实例检索")
        finally:
            mf2.close()
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_status_and_revoke(self):
        r = self.mf.passport_export()
        mf2, tmp2 = _make_mf()
        try:
            mf2.passport_import(r["passport"], mode="merge")
            st = mf2.passport_status()
            self.assertGreaterEqual(st["total_sources"], 1)
            n_before = mf2.stats()["total"]
            rv = mf2.passport_revoke(r["passport"]["passport_id"])
            self.assertTrue(rv["success"])
            self.assertGreaterEqual(rv["memories_removed"], 1)
            n_after = mf2.stats()["total"]
            self.assertLess(n_after, n_before, "撤销后来源记忆应被删除")
        finally:
            mf2.close()
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_invalid_passport_rejected(self):
        r = self.mf.passport_import({"format": "x"}, mode="merge")
        self.assertFalse(r["success"])


if __name__ == "__main__":
    unittest.main()
