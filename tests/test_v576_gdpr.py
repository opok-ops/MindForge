# -*- coding: utf-8 -*-
"""v5.7.6 GDPR 合规工具包回归测试

对应竞品对比 P2「合规认证（SOC2 / GDPR 工具包）」中可本地实现的部分：
  - gdpr_report：数据类别/数量/加密状态/留存机制/可行使权利
  - gdpr_export_all：数据可携权（memories/versions/links/audit/archived 全量导出）
  - gdpr_erase_all：被遗忘权（先备份再永久删除，含 FTS/缓存清理）

覆盖：存储层三方法、CLI 三命令、加密库路径、备份路径。
"""

import json
import os
import shutil
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from core.mindforge import MindForge  # noqa: E402
from core.types import MemoryConfig, PrivacyLevel  # noqa: E402


def _make_mf(encrypted=False):
    tmp = tempfile.mkdtemp(prefix="mf_v576_gdpr_", dir=_REPO)
    db = os.path.join(tmp, "m.db")
    key = os.path.join(tmp, "k.key")
    cfg = MemoryConfig(db_path=db, key_file=key, encrypted=encrypted)
    return MindForge(config=cfg), tmp


class TestGdprReport(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.mf.add("合规测试记忆A", category="fact", tags=["gdpr"],
                    privacy=PrivacyLevel.PRIVATE)
        self.mf.add("合规测试记忆B")
        # 制造一条版本历史与审计（与 add 相同默认身份，保证隐私校验通过）
        e = self.mf.add("将被更新的事实")
        self.mf.update(e.id, content="更新后的事实")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_report_shape_and_counts(self):
        rep = self.mf.gdpr_report()
        self.assertIn("generated_at", rep)
        self.assertIn("db_path", rep)
        self.assertFalse(rep["encrypted_at_rest"])
        self.assertTrue(rep["local_only"])
        cats = rep["data_categories"]
        self.assertGreaterEqual(cats["memories"], 3)
        self.assertGreaterEqual(cats["memory_versions"], 1)
        self.assertGreaterEqual(cats["audit_log"], 1)
        self.assertIn("export_all", rep["rights_exercisable"])
        self.assertIn("erase", rep["rights_exercisable"])
        self.assertTrue(rep["retention_mechanisms"]["bi_temporal_valid_to"])


class TestGdprExportAll(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.mf.add("导出记忆A", category="fact", tags=["gdpr"])
        e = self.mf.add("导出记忆B")
        self.mf.update(e.id, content="导出记忆B-更新")
        self.mf.link_memories(e.id, self.mf.add("关联目标").id, link_type="related")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_all_format_and_sections(self):
        data = self.mf.gdpr_export_all()
        self.assertEqual(data["format"], "mindforge-gdpr-export")
        self.assertEqual(data["version"], "1.0")
        self.assertIn("exported_at", data)
        self.assertGreaterEqual(len(data["memories"]), 3)
        self.assertGreaterEqual(len(data["memory_versions"]), 1)
        self.assertGreaterEqual(len(data["audit_log"]), 1)
        self.assertGreaterEqual(len(data["memory_links"]), 1)
        # 字段可读（tags/metadata 已反序列化）
        mem = data["memories"][0]
        self.assertIsInstance(mem.get("tags", []), list)
        self.assertIn("valid_from", mem)  # bi-temporal 字段随导出


class TestGdprErase(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.mf.add("将被擦除的记忆", category="fact", tags=["erase"])
        self.mf.add("搜索命中词 unique-needle-2026")
        e = self.mf.add("擦除前的历史")
        self.mf.update(e.id, content="擦除前的历史-更新")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_erase_removes_everything(self):
        # 擦除前搜索有命中
        self.assertGreaterEqual(len(self.mf.search("unique-needle-2026").chunks), 1)
        res = self.mf.gdpr_erase_all(backup=False)
        self.assertGreaterEqual(res["deleted"]["memories"], 3)
        self.assertGreaterEqual(res["deleted"]["memory_versions"], 1)
        self.assertGreaterEqual(res["deleted"]["audit_log"], 1)
        rep = self.mf.gdpr_report()
        self.assertEqual(rep["data_categories"]["memories"], 0)
        self.assertEqual(rep["data_categories"]["memory_versions"], 0)
        # FTS 已清：搜索不再命中
        self.assertEqual(len(self.mf.search("unique-needle-2026").chunks), 0)
        # 可携导出为空
        self.assertEqual(self.mf.gdpr_export_all()["memories"], [])

    def test_erase_creates_backup(self):
        res = self.mf.gdpr_erase_all(backup=True)
        self.assertTrue(res["backup"], "应生成备份文件")
        self.assertTrue(os.path.exists(res["backup"]), res["backup"])

    def test_erase_encrypted_store_with_key_backup(self):
        mf, tmp = _make_mf(encrypted=True)
        try:
            mf.init_with_password("testpassword123")
            mf.add("加密库擦除测试")
            key_file = os.path.join(tmp, "k.key")
            self.assertTrue(os.path.exists(key_file))
            res = mf.gdpr_erase_all(backup=True)
            self.assertTrue(res["backup"])
            # 备份目录应同时含数据库与密钥副本
            backup_dir = os.path.dirname(res["backup"])
            names = {f for f in os.listdir(backup_dir) if f.startswith("memory_backup")}
            self.assertTrue(any(f.endswith(".db") for f in names), names)
            self.assertTrue(any(f.endswith(".key") for f in names), names)
            self.assertEqual(mf.gdpr_report()["data_categories"]["memories"], 0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestGdprCli(unittest.TestCase):
    def setUp(self):
        from cli.main import main
        self.main = main
        self.tmp = tempfile.mkdtemp(prefix="mf_v576_gdprcli_", dir=_REPO)
        os.environ["MINDFORGE_DB"] = os.path.join(self.tmp, "cli.db")
        os.environ.pop("MINDFORGE_PASSWORD", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv):
        try:
            return self.main(argv)
        except SystemExit as e:
            return e.code or 0

    def test_gdpr_report_json(self):
        self.assertEqual(self._run(["add", "GDPR CLI 记忆"]), 0)
        self.assertEqual(self._run(["gdpr-report", "--json"]), 0)

    def test_gdpr_export_all_command(self):
        self.assertEqual(self._run(["add", "GDPR 导出记忆"]), 0)
        out = os.path.join(self.tmp, "gdpr.json")
        self.assertEqual(self._run(["gdpr-export-all", out]), 0)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["format"], "mindforge-gdpr-export")
        self.assertGreaterEqual(len(data["memories"]), 1)

    def test_gdpr_erase_requires_force(self):
        self.assertEqual(self._run(["gdpr-erase"]), 1)
        self.assertEqual(self._run(["gdpr-erase", "--force", "--no-backup"]), 0)
        self.assertEqual(self._run(["gdpr-report", "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
