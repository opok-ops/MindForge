# -*- coding: utf-8 -*-
"""v5.9.5 终极稳定版：WAL 在线备份一致性修复

测试要点：
1. WAL 模式下在线备份必须包含备份时刻的全部已提交数据（不丢 -wal 事务）
2. 备份文件落盘后无 .tmp 残留（原子 rename）
3. 备份出来的库可被 SQLite 直接打开并通过 integrity_check
4. create_backup 后 restore_backup 数据完整
"""
import os
import shutil
import sqlite3
import tempfile
import unittest

from mindforge import MindForge

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_mf():
    tmp = tempfile.mkdtemp(prefix="mf_v595_")
    mf = MindForge(db_path=os.path.join(tmp, "m.db"), encrypted=False)
    return mf, tmp


class TestOnlineBackupConsistency(unittest.TestCase):
    """v5.9.5 P0：WAL 模式下裸 copy2 会丢未 checkpoint 事务，
    改用 sqlite3 Connection.backup() 后必须包含全部已提交数据。"""

    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.backup_dir = os.path.join(self.tmp, "bak")
        os.makedirs(self.backup_dir)

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_contains_all_committed_rows(self):
        # 写入两条记忆（触发 WAL 未 checkpoint 段）
        e1 = self.mf.add("v5.9.5 backup row alpha")
        e2 = self.mf.add("v5.9.5 backup row beta")
        # 不手动 checkpoint——模拟线上 WAL 状态，直接备份
        self.mf._storage.backup(self.backup_dir)
        backups = [f for f in os.listdir(self.backup_dir) if f.endswith(".db")]
        self.assertEqual(len(backups), 1, f"应恰好一个备份: {backups}")

        # 备份文件直接用 sqlite3 打开，断言两条都在
        bpath = os.path.join(self.backup_dir, backups[0])
        conn = sqlite3.connect(bpath)
        try:
            rows = conn.execute(
                "SELECT id, content FROM memories WHERE id IN (?, ?)",
                (e1.id, e2.id)).fetchall()
            ids = {r[0] for r in rows}
            self.assertIn(e1.id, ids, "备份丢失第一条记忆——WAL 事务未落主库")
            self.assertIn(e2.id, ids, "备份丢失第二条记忆")
            # 备份库自身完整性 ok
            chk = conn.execute("PRAGMA integrity_check").fetchone()
            self.assertEqual(str(chk[0]).lower(), "ok")
        finally:
            conn.close()

    def test_no_tmp_residue_after_backup(self):
        self.mf.add("atomic rename check")
        self.mf._storage.backup(self.backup_dir)
        leftovers = [f for f in os.listdir(self.backup_dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"原子 rename 失败，残留: {leftovers}")

    def test_create_backup_returns_path_and_roundtrips(self):
        e = self.mf.add("roundtrip payload")
        result = self.mf._storage.create_backup(self.backup_dir)
        self.assertTrue(result["success"])
        bp = result["path"]
        self.assertTrue(os.path.exists(bp))
        # 恢复后该记忆仍在
        self.mf._storage.restore_backup(bp, create_backup_before=False)
        again = self.mf.get(e.id)
        self.assertIsNotNone(again)
        self.assertIn("roundtrip payload", again.content)


if __name__ == "__main__":
    unittest.main()
