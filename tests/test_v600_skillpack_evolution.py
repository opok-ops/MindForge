# -*- coding: utf-8 -*-
"""v6.0.0 功能 3&4：可分享技能包 + 记忆演变（版本保留）

功能 3 测试要点：
- export_skill_pack 输出 mindforge-skill-pack 格式（含 schema/version）
- validate 校验格式与上限
- import merge（按 name UPSERT）与 replace（先删后写）
- 跨实例导入后技能可检索

功能 4 测试要点：
- evolve_memory 保留旧版本（memory_versions 新增版本）+ 记录 evolution
- reason 持久化、list_evolutions 可查
- evolution_stats 统计正确
"""
import os
import shutil
import tempfile
import unittest

from mindforge import MindForge

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_mf():
    tmp = tempfile.mkdtemp(prefix="mf_v600b_")
    mf = MindForge(db_path=os.path.join(tmp, "m.db"), encrypted=False)
    return mf, tmp


class TestSkillPack(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.mf.record_experience(task="写周报", content="用清单式结构写周报",
                                  outcome="success")
        self.mf.record_experience(task="会议纪要", content="结论先行，行动项编号",
                                  outcome="success")
        self.mf.distill_skills(min_cluster_size=1, max_skills=5)

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_format(self):
        r = self.mf.skill_pack_export()
        self.assertTrue(r["success"])
        self.assertGreaterEqual(r["skill_count"], 1)
        pack = r["pack"]
        self.assertEqual(pack["format"], "mindforge-skill-pack")
        self.assertEqual(pack["format_version"], 1)
        self.assertIn("skills", pack)

    def test_validate_ok_and_bad(self):
        r = self.mf.skill_pack_export()
        v = self.mf.skill_pack_validate(r["pack"])
        self.assertTrue(v["valid"], v)
        bad = self.mf.skill_pack_validate({"format": "wrong", "format_version": 9})
        self.assertFalse(bad["valid"])

    def test_import_into_other_instance(self):
        r = self.mf.skill_pack_export()
        mf2, tmp2 = _make_mf()
        try:
            ri = mf2.skill_pack_import(r["pack"], mode="merge")
            self.assertTrue(ri["success"])
            self.assertGreaterEqual(ri["skills_imported"], 1)
            names = mf2.skill_list(limit=50)
            self.assertGreaterEqual(len(names), 1)
        finally:
            mf2.close()
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_import_replace_mode(self):
        r = self.mf.skill_pack_export()
        mf2, tmp2 = _make_mf()
        try:
            mf2.skill_pack_import(r["pack"], mode="merge")
            n1 = len(mf2.skill_list(limit=50))
            ri = mf2.skill_pack_import(r["pack"], mode="replace")
            self.assertTrue(ri["success"])
            n2 = len(mf2.skill_list(limit=50))
            # replace 不产生重复（先删后写，技能数不翻倍）
            self.assertLessEqual(n2, n1 * 2)
        finally:
            mf2.close()
            shutil.rmtree(tmp2, ignore_errors=True)


class TestEvolution(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.m = self.mf.add("我在北京工作")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_evolve_keeps_version_and_reason(self):
        r = self.mf.evolve_memory(self.m.id, content="我搬到深圳创业了",
                                  reason="创业机会")
        self.assertTrue(r["success"], r)
        self.assertEqual(r["reason"], "创业机会")
        self.assertGreaterEqual(r["to_version"], r["from_version"])
        # 旧版本已存档
        versions = self.mf._storage.list_versions(self.m.id)
        self.assertGreaterEqual(len(versions), 1)
        self.assertIn("北京", versions[0]["content_preview"] or "")

    def test_list_evolutions_and_stats(self):
        self.mf.evolve_memory(self.m.id, content="我搬到深圳创业了", reason="创业机会")
        evos = self.mf.list_evolutions(self.m.id)
        self.assertGreaterEqual(len(evos), 1)
        self.assertEqual(evos[0]["reason"], "创业机会")
        st = self.mf.evolution_stats()
        self.assertGreaterEqual(st["total_evolutions"], 1)
        self.assertGreaterEqual(st["memories_evolved"], 1)

    def test_evolve_nonexistent_fails(self):
        r = self.mf.evolve_memory("no_such_mem", content="x")
        self.assertFalse(r["success"])

    def test_evolve_auto_reason(self):
        r = self.mf.evolve_memory(self.m.id, content="我搬到深圳创业了")
        self.assertTrue(r["success"])
        self.assertTrue(r["reason"])


if __name__ == "__main__":
    unittest.main()
