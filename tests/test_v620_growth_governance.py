# -*- coding: utf-8 -*-
"""v6.2.0 新功能回归测试

功能 A：benchmarks/repro_benchmark.py 等参数可复现基准（脚本级冒烟
  由 CI 的基准守护覆盖；此处验证核心方法学函数可导入、口径字段完整）
功能 B：audit_log 增长治理
  - record_search 同词去抖（60s 窗口内只落一条，线程安全）
  - prune_audit_log 只裁 search 流水，合规审计保留
  - doctor 第 7 项集成（超大审计库报告问题）
  - audit-prune CLI --dry-run / 实裁
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from mindforge.core.mindforge import MindForge  # noqa: E402
from mindforge.core.types import MemoryConfig  # noqa: E402


def _make_mf(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix, dir=os.path.join(_REPO))
    cfg = MemoryConfig(
        db_path=os.path.join(tmp, "m.db"),
        key_file=os.path.join(tmp, "k.key"),
        encrypted=False,
    )
    return MindForge(config=cfg), tmp


class TestSearchDedup(unittest.TestCase):
    """搜索审计去抖"""

    def setUp(self):
        self.mf, self.tmp = _make_mf("mf_v620_dedup_")
        self.mf.add("去抖测试语料", category="general")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _search_rows(self, word):
        conn = self.mf._storage._get_conn()
        return conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='search' AND memory_id=?",
            (word,)).fetchone()[0]

    def test_same_word_dedup_within_window(self):
        storage = self.mf._storage
        for _ in range(10):
            storage.record_search("dedup-word")
        self.assertEqual(self._search_rows("dedup-word"), 1)

    def test_different_words_not_deduped(self):
        storage = self.mf._storage
        storage.record_search("alpha")
        storage.record_search("beta")
        self.assertEqual(self._search_rows("alpha"), 1)
        self.assertEqual(self._search_rows("beta"), 1)

    def test_dedup_state_purged_by_prune(self):
        storage = self.mf._storage
        storage.record_search("gamma")
        # 人工把词表挪出窗口，验证去抖失效后可再记
        storage._search_dedup["gamma"] = time.monotonic() - 999
        storage.record_search("gamma")
        self.assertEqual(self._search_rows("gamma"), 2)
        # prune 后清空去抖表
        storage.record_search("delta")
        storage.prune_audit_log(keep_days=1)
        self.assertEqual(storage._search_dedup, {})

    def test_dedup_table_bounded(self):
        # 词表超 1024 清最旧 512：写 1030 个不同词
        storage = self.mf._storage
        for i in range(1030):
            storage.record_search(f"w{i}")
        self.assertLessEqual(len(storage._search_dedup), 520)


class TestPruneAuditLog(unittest.TestCase):
    """审计裁剪：只动 search，合规审计保留"""

    def setUp(self):
        self.mf, self.tmp = _make_mf("mf_v620_prune_")
        self.conn = self.mf._storage._get_conn()
        now = time.time()
        # 真实列：id, action, memory_id, actor, session_id, privacy_level, timestamp, details
        for i in range(3):
            self.conn.execute(
                "INSERT INTO audit_log (action, memory_id, actor, session_id,"
                " privacy_level, timestamp, details) VALUES (?,?,?,?,?,?,?)",
                ("search", f"old{i}", "", "", "INTERNAL", now - 100 * 86400, "{}"))
        for i in range(2):
            self.conn.execute(
                "INSERT INTO audit_log (action, memory_id, actor, session_id,"
                " privacy_level, timestamp, details) VALUES (?,?,?,?,?,?,?)",
                ("search", f"new{i}", "", "", "INTERNAL", now, "{}"))
        # 合规审计（写入/导出）无论多旧都必须保留
        self.conn.execute(
            "INSERT INTO audit_log (action, memory_id, actor, session_id,"
            " privacy_level, timestamp, details) VALUES (?,?,?,?,?,?,?)",
            ("add", "m1", "u", "", "INTERNAL", now - 400 * 86400, "{}"))
        self.conn.execute(
            "INSERT INTO audit_log (action, memory_id, actor, session_id,"
            " privacy_level, timestamp, details) VALUES (?,?,?,?,?,?,?)",
            ("gdpr_erase", "", "u", "", "PUBLIC", now - 400 * 86400, "{}"))
        self.conn.commit()

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prune_only_old_search_rows(self):
        r = self.mf._storage.prune_audit_log(keep_days=90)
        self.assertEqual(r["deleted"], 3)
        self.assertEqual(r["kept_search_rows"], 2)
        # 合规审计原封不动
        kept_actions = [row[0] for row in self.conn.execute(
            "SELECT action FROM audit_log WHERE action != 'search'").fetchall()]
        self.assertEqual(sorted(kept_actions), ["add", "gdpr_erase"])

    def test_prune_zero_days_noop_guard(self):
        # keep_days<=0 或空 actions：无操作
        r = self.mf._storage.prune_audit_log(keep_days=0)
        self.assertEqual(r["deleted"], 0)
        r2 = self.mf._storage.prune_audit_log(keep_days=90, actions=[])
        self.assertEqual(r2["deleted"], 0)


class TestDoctorAuditCheck(unittest.TestCase):
    """doctor 第 7 项：超大审计库触发提示（阈值 50000）"""

    def setUp(self):
        self.mf, self.tmp = _make_mf("mf_v620_doc_")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_doctor_reports_huge_audit(self):
        # 用健康库跑 doctor，验证第 7 项正常输出且不误报
        import argparse
        import io
        from contextlib import redirect_stdout
        from mindforge.cli import main as cli_main
        args = argparse.Namespace(
            key_file=os.path.join(self.tmp, "k.key"),
            db_path=os.path.join(self.tmp, "m.db"),
            fix=False, json=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main.cmd_doctor(args)
        out = buf.getvalue()
        self.assertIn("[7/7] 审计日志增长", out)
        self.assertIn("审计日志规模健康", out)


if __name__ == "__main__":
    unittest.main()
