"""
MindForge v6.4.3 回归测试

覆盖测试过程中发现的 1 个文档-代码不一致缺陷（2026-11-10）：

  - Bug-A（P0）: v6.4.2 宣称的「WAL + synchronous=NORMAL」五刀第一刀
    从未落地——CHANGELOG/README/website 与 commit message 均宣称该
    组合已上线，但 _get_conn() 仅设置了 journal_mode=WAL，
    synchronous 维持 SQLite 默认 FULL（WAL 下逐 commit fsync）。
    修复：WAL 分支补 PRAGMA synchronous=NORMAL（per-connection，
    每连接创建时设置；网络 FS 降级 DELETE 分支维持 FULL 从严）。

守卫目标：让「PRAGMA 只在部分连接生效」「优化宣称了但没落地」
这两类回归在测试里显形。
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindforge.core import storage as core_storage
from mindforge.core.version import __version__


class _WALCase(unittest.TestCase):
    """本地盘临时库（走 WAL 分支）公共脚手架。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="mf_v643_fix_")
        self.db = os.path.join(self.tmpdir.name, "memory.db")
        self.storage = core_storage.StorageEngine(
            db_path=self.db, encrypted=False
        )

    def tearDown(self):
        try:
            self.storage.close()
        finally:
            self.tmpdir.cleanup()


class TestSynchronousNormalGuard(_WALCase):
    """Bug-A 守卫：WAL + synchronous=NORMAL 必须真实生效。"""

    def test_synchronous_normal_on_local_wal(self):
        # SQLite PRAGMA synchronous 返回值：0=OFF, 1=NORMAL, 2=FULL。
        # v6.4.2 缺陷形态即此值恒为 2（FULL）。
        conn = self.storage._get_conn()
        mode = conn.execute("PRAGMA synchronous").fetchone()[0]
        self.assertEqual(
            mode, 1,
            f"synchronous 应为 NORMAL(1)，实际 {mode}——"
            "v6.4.3 的 WAL+synchronous=NORMAL 修复未生效或被回退",
        )

    def test_journal_mode_wal_on_local_disk(self):
        conn = self.storage._get_conn()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(
            str(journal).lower(), "wal",
            f"本地盘 journal_mode 应为 wal，实际 {journal}",
        )

    def test_synchronous_is_per_connection(self):
        # synchronous 是 per-connection 参数：StorageEngine 用
        # threading.local 维护连接，新线程的连接也必须带上 NORMAL，
        # 否则只保护了首线程（REST/daemon 多线程场景将静默回退 FULL）。
        results = {}

        def probe():
            try:
                conn = self.storage._get_conn()
                results["mode"] = conn.execute(
                    "PRAGMA synchronous"
                ).fetchone()[0]
            except Exception as exc:  # pragma: no cover - 显形优先
                results["error"] = repr(exc)

        t = threading.Thread(target=probe)
        t.start()
        t.join(timeout=10)
        self.assertNotIn("error", results, results.get("error", ""))
        self.assertEqual(results.get("mode"), 1)


class TestVersionBump(unittest.TestCase):
    """版本钉值：忘记 bump 时在此显形（与 v568/v575/v612 守卫同一约定）。
    v6.4.5 教训：每版 fixes 文件的版本断言在后续发版都要一并 bump——
    钉点集合随版本数递增，发版链清单必须扫全部 test_v*fixes.py。"""

    def test_version_is_current(self):
        self.assertEqual(__version__, "6.5.4")


if __name__ == "__main__":
    unittest.main()
