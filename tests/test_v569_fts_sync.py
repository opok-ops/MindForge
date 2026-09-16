# -*- coding: utf-8 -*-
"""v5.6.9 回归测试

锁定本轮两处 FTS（全文检索）同步缺陷的修复：

  P2 ``batch_update()`` 缺 FTS 同步：批量改写 ``category`` / ``tags``（均为
      ``memory_fts`` 索引列）后必须同步 ``memory_fts``，否则搜索结果滞后。
  P3 ``get()`` 自动过期路径缺 FTS 同步：TTL 过期将 ``category`` 置为 ``'trash'``
      后必须同步 ``memory_fts``，否则索引里残留旧分类条目。

验证方式：对 contentless FTS5 表只能用 ``MATCH`` 查询其索引内容（SELECT 取不到
列值），故直接以 MATCH 断言「新值可被检索、旧值已从索引移除」，这正是修复要解决的
搜索滞后问题。
"""

import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from core.storage import StorageEngine


class _FtsSyncCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v569_")
        self.db = os.path.join(self.tmp, "mf.db")
        # 明文库才会建 FTS 索引（self.encrypted 在 encryption is None 时为 False）
        self.st = StorageEngine(db_path=self.db, encrypted=False)

    def tearDown(self):
        try:
            self.st.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mem_rowid(self, memory_id):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT rowid FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()[0]
        finally:
            conn.close()

    def _mem_category(self, memory_id):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT category FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()[0]
        finally:
            conn.close()

    def _fts_match_rowids(self, term):
        """返回 memory_fts 中 MATCH 命中 term 的 rowid 列表。"""
        conn = sqlite3.connect(self.db)
        try:
            return [r[0] for r in conn.execute(
                "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", (term,))]
        finally:
            conn.close()


class TestBatchUpdateFtsSync(_FtsSyncCase):
    def test_category_and_tags_update_syncs_fts(self):
        entry = self.st.add_memory(
            "alpha beta gamma", category="zzcatOO", tags=["zztagOO"])
        mid = entry.id
        rid = self._mem_rowid(mid)

        # 初始：旧分类/标签可被检索
        self.assertIn(rid, self._fts_match_rowids("zzcatOO"))
        self.assertIn(rid, self._fts_match_rowids("zztagOO"))

        res = self.st.batch_update([mid], category="zzcatNN", tags=["zztagNN"])
        self.assertTrue(res["success"])
        self.assertEqual(res["updated"], 1)

        # 修复后：新值可检索，旧值已从索引移除（搜索滞后即源于此）
        self.assertIn(rid, self._fts_match_rowids("zzcatNN"))
        self.assertIn(rid, self._fts_match_rowids("zztagNN"))
        self.assertNotIn(rid, self._fts_match_rowids("zzcatOO"))
        self.assertNotIn(rid, self._fts_match_rowids("zztagOO"))

        # 业务表与索引一致，且 get_memory 能读到新分类
        self.assertEqual(self._mem_category(mid), "zzcatNN")
        self.assertEqual(self.st.get_memory(mid).category, "zzcatNN")

    def test_fts_only_synced_when_index_columns_change(self):
        entry = self.st.add_memory(
            "delta epsilon zeta", category="zzc1", tags=["zzt1"])
        mid = entry.id
        rid = self._mem_rowid(mid)
        # 仅改 importance（非 FTS 列）不应改变 FTS 命中情况
        self.st.batch_update([mid], importance="high")
        self.assertIn(rid, self._fts_match_rowids("zzc1"))
        self.assertIn(rid, self._fts_match_rowids("zzt1"))


class TestGetAutoExpireFtsSync(_FtsSyncCase):
    def test_expired_memory_syncs_fts_to_trash(self):
        entry = self.st.add_memory(
            "theta iota kappa", category="zzcatX", tags=["zztagX"],
            expires_at=time.time() - 100)  # 已经过期
        mid = entry.id
        rid = self._mem_rowid(mid)

        # 读取触发自动过期：置 trash 且返回 None
        self.assertIsNone(self.st.get_memory(mid))
        self.assertEqual(self._mem_category(mid), "trash")

        # P3：FTS 中旧的 zzcatX 已移除、category 已同步为 trash
        # （tags 保持不变属预期：搜索经 category != 'trash' 过滤排除该条目）
        self.assertIn(rid, self._fts_match_rowids("trash"))
        self.assertNotIn(rid, self._fts_match_rowids("zzcatX"))


if __name__ == "__main__":
    unittest.main()
