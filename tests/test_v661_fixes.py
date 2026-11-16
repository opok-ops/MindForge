# -*- coding: utf-8 -*-
"""v6.6.1 缺陷修复契约测试

对应 .zwork_v661_bugs.md 报告的 8 项缺陷中本轮修复的 6 项 + 检索
quota 兜底 + CJK gram 展开：
- 缺陷 #1 (P0)：rekey 预写临时密钥文件 → 事务重加密 → 原子替换，
  任一步失败可回滚，崩溃窗口靠 .rekey-tmp 残留自动恢复
- 缺陷 #2 (P1)：门面 get()/批量 get_memories_by_ids 加密条目解密回填
- 缺陷 #3 (P1)：batch_delete 无过滤条件不再 TypeError，缓存失效遍历 ids
- 缺陷 #4 (P1)：cleanup_expired 的 FTS 'delete' 用真实旧值（词项/文档一致）
- 缺陷 #5 (P1)：跨线程 close 生效（check_same_thread=False + 锁串行化）
- 缺陷 #6 (P1)：delete_memory 补 @_with_rollback，部分删除不被无关提交带出
- 缺陷 #8 (P2)：analyze_similarity MATCH 转义，特殊字符不再静默降级
- 检索：全空 quota 兜底（有效阈值 max(0.15, min_relevance/2)）
- 检索：CJK >=4 字 token 滑动 3-gram OR 展开
"""
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from mindforge.core.mindforge import MindForge
from mindforge.core import encryption as enc_mod
from mindforge.core.indexer import IndexEngine


def _make_mf(tmp, encrypted=True):
    mf = MindForge(db_path=os.path.join(tmp, "m.db"),
                   key_file=os.path.join(tmp, ".key"),
                   encrypted=encrypted)
    if encrypted:
        mf.init_with_password("pw-v661")
    return mf


class TestBatchDeleteNoFilter(unittest.TestCase):
    """缺陷 #3：batch_delete() 无过滤条件必须可用"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v661bd_")
        self.mf = _make_mf(self.tmp, encrypted=False)

    def tearDown(self):
        self.mf.close()

    def test_no_filter_deletes_all(self):
        for i in range(3):
            self.mf.add("batch del item %d" % i, category="general")
        n = self.mf.storage.batch_delete()
        self.assertEqual(n, 3)
        live = self.mf.storage._get_conn().execute(
            "SELECT COUNT(*) FROM memories WHERE category != 'trash'"
        ).fetchone()[0]
        self.assertEqual(live, 0)

    def test_with_category_invalidates_cache(self):
        e = self.mf.add("cached item", category="general")
        # 预热读缓存
        self.mf.storage.get_memory(e.id)
        self.mf.storage.batch_delete(category="general")
        # 缓存已失效：直接从库读，category 应已是 trash
        row = self.mf.storage._get_conn().execute(
            "SELECT category FROM memories WHERE id = ?", (e.id,)).fetchone()
        self.assertEqual(row[0], "trash")


class TestDeleteMemoryRollback(unittest.TestCase):
    """缺陷 #6：delete_memory 中途失败后部分删除不得被无关提交带出"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v661dm_")
        self.mf = _make_mf(self.tmp, encrypted=False)

    def tearDown(self):
        self.mf.close()

    def test_partial_delete_not_committed_by_unrelated_write(self):
        e = self.mf.add("to delete with subtables")
        st = self.mf.storage
        conn = st._get_conn()
        # 制造子表行，保证硬删除有多步可拆
        conn.execute(
            "INSERT INTO memory_versions(id, memory_id, version_number, content) "
            "VALUES ('v661-seed-1', ?, 1, 'v1')", (e.id,))
        conn.commit()

        def _boom(*a, **k):
            raise RuntimeError("boom")

        with mock.patch.object(st, "_purge_graph_relations", side_effect=_boom):
            with self.assertRaises(RuntimeError):
                st.delete_memory(e.id, hard_delete=True)

        # 同线程下一次无关写提交后，主行必须仍然在（部分删除未泄露）
        st.add_memory({"content": "unrelated write", "category": "general"})
        alive = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE id = ?", (e.id,)).fetchone()[0]
        self.assertEqual(alive, 1, "失败 delete 的部分删除被无关提交带出")


class TestCleanupExpiredFtsConsistency(unittest.TestCase):
    """缺陷 #4：cleanup_expired 后 FTS 词项与文档必须一致"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v661ce_")
        self.mf = _make_mf(self.tmp, encrypted=False)

    def tearDown(self):
        self.mf.close()

    def test_terms_match_live_rows(self):
        e = self.mf.add("uniquewordXYZ marker content", category="general")
        conn = self.mf.storage._get_conn()
        # 伪装成过期 sensory 记忆
        conn.execute(
            "UPDATE memories SET layer = 'sensory', created_at = ? WHERE id = ?",
            (0.0, e.id))
        conn.commit()

        n = self.mf.storage.cleanup_expired(max_age_hours=1, layer="sensory")
        self.assertEqual(n, 1)

        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS fts_v661 "
            "USING fts5vocab('memory_fts', 'row')")
        docs = [r[0] for r in conn.execute(
            "SELECT doc FROM fts_v661 WHERE term = 'xyz'").fetchall()]
        # 词项残留而 docid 失活 = 索引不一致（修复前的形态）
        for docid in docs:
            live = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE rowid = ?",
                (docid,)).fetchone()[0]
            self.assertEqual(live, 1,
                             "FTS 词项指向不存在的文档行（索引不一致）")


class TestFacadeGetDecrypt(unittest.TestCase):
    """缺陷 #2：加密条目经 get()/get_memories_by_ids 必须回填明文"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v661gd_")
        self.mf = _make_mf(self.tmp, encrypted=True)

    def tearDown(self):
        self.mf.close()

    def test_get_returns_plaintext(self):
        e = self.mf.add("hello secret v661")
        got = self.mf.get(e.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.content, "hello secret v661")

    def test_get_memories_by_ids_returns_plaintext(self):
        e = self.mf.add("batch secret v661")
        entries = self.mf.storage.get_memories_by_ids([e.id])
        self.assertEqual(entries[0].content, "batch secret v661")


class TestRekeyAtomicity(unittest.TestCase):
    """缺陷 #1：rekey 各失败点的数据可恢复性"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v661rk_")
        self.db = os.path.join(self.tmp, "enc.db")
        self.key = os.path.join(self.tmp, ".key")
        self.mf = MindForge(db_path=self.db, key_file=self.key, encrypted=True)
        self.mf.init_with_password("oldpass")
        self.e1 = self.mf.add("secret content one")
        self.e2 = self.mf.add("secret content two")

    def tearDown(self):
        try:
            self.mf.close()
        except Exception:
            pass

    def _restart_read(self, password):
        mf2 = MindForge(db_path=self.db, key_file=self.key, encrypted=True)
        mf2.init_with_password(password)
        try:
            return mf2.get(self.e1.id).content
        finally:
            mf2.close()

    def test_keyfile_write_failure_keeps_old_data(self):
        """预写失败：库未动，旧密码照常解开"""
        with mock.patch.object(enc_mod, "_write_key_file",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.mf.rekey("oldpass", "newpass")
        self.assertEqual(self._restart_read("oldpass"), "secret content one")

    def test_replace_failure_rolls_db_back(self):
        """原子替换失败：库重加密回旧密钥，旧密码照常解开"""
        real_replace = os.replace

        def _fail_replace(a, b, **kw):
            raise OSError("replace failed")

        with mock.patch("os.replace", side_effect=_fail_replace):
            with self.assertRaises(Exception):
                self.mf.rekey("oldpass", "newpass")
        self.assertEqual(self._restart_read("oldpass"), "secret content one")

    def test_crash_window_recovery_from_tmp(self):
        """commit 与 replace 之间崩溃：重启后用 .rekey-tmp 自动恢复"""
        with mock.patch("os.replace", lambda a, b, **kw: None):
            # os.replace 被吞：库已新密文、正式密钥文件仍旧、tmp 残留
            result = self.mf.rekey("oldpass", "newpass")
            self.assertIn("rekeyed_count", result)
        self.assertTrue(os.path.exists(self.key + ".rekey-tmp"))
        try:
            self.mf.close()
        except Exception:
            pass

        # 用新密码重启：tmp 的 verify_blob + 试探解密 → 自动完成密钥切换
        mf2 = MindForge(db_path=self.db, key_file=self.key, encrypted=True)
        mf2.init_with_password("newpass")
        try:
            self.assertEqual(mf2.get(self.e1.id).content, "secret content one")
            self.assertFalse(os.path.exists(self.key + ".rekey-tmp"))
        finally:
            mf2.close()


class TestQuotaFallback(unittest.TestCase):
    """检索：全候选低于 min_relevance 时放宽阈值兜底，不再静默空结果"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v661qf_")
        self.mf = _make_mf(self.tmp, encrypted=True)
        self.mf.add("我对花生严重过敏，千万别放花生酱",
                    category="locomo_session")

    def tearDown(self):
        self.mf.close()

    def test_weak_match_not_dropped(self):
        # 修复前：TF-IDF 最相似 ~0.28 < 0.3 → 0 条
        r = self.mf.search("用户对什么食物过敏？", max_results=5,
                           categories=["locomo_session"])
        chunks = list(r.chunks or [])
        self.assertTrue(chunks, "弱相关候选被阈值整条滤掉，返回空结果")
        self.assertTrue(any("花生" in getattr(c, "content", "")
                            for c in chunks))

    def test_strong_match_unaffected(self):
        r = self.mf.search("花生过敏", max_results=5,
                           categories=["locomo_session"])
        chunks = list(r.chunks or [])
        self.assertTrue(chunks)
        self.assertIn("花生", chunks[0].content)


class TestCjkGramExpansion(unittest.TestCase):
    """检索：CJK >=4 字 token 展开为滑动 3-gram OR"""

    def test_long_cjk_query_expands(self):
        esc = IndexEngine._cjk_escaped_or("用户对什么食物过敏")
        self.assertIn(" OR ", esc)
        self.assertTrue(esc.startswith("("))
        # 全停用字 gram（"对什么"）被剔除
        self.assertNotIn('"对什么"', esc)
        # 实义 gram 保留
        self.assertIn('"食物过"', esc)

    def test_short_or_non_cjk_keeps_phrase(self):
        self.assertEqual(IndexEngine._cjk_escaped_or("过敏"), '"过敏"')
        self.assertEqual(IndexEngine._cjk_escaped_or("hello world"),
                         '"hello world"')

    def test_all_stopword_cjk_keeps_phrase(self):
        self.assertEqual(IndexEngine._cjk_escaped_or("的了吗呢"), '"的了吗呢"')


class TestAnalyzeSimilaritySpecialChars(unittest.TestCase):
    """缺陷 #8：含 FTS5 特殊字符的内容不再静默降级"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v661as_")
        self.mf = _make_mf(self.tmp, encrypted=False)

    def tearDown(self):
        self.mf.close()

    def test_special_chars_still_finds_similar(self):
        e1 = self.mf.add('项目状态: "进行中" (阶段一) 复盘纪要',
                         category="general")
        self.mf.add('项目状态: "进行中" (阶段一) 复盘纪要 第二版',
                    category="general")
        results = self.mf.storage.analyze_similarity(e1.id, limit=5)
        self.assertIsInstance(results, list)
        self.assertTrue(results, "含特殊字符的记忆相似度分析返回空")


class TestCrossThreadClose(unittest.TestCase):
    """缺陷 #5：其他线程的遗留连接能被真正关闭"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v661cc_")
        self.mf = _make_mf(self.tmp, encrypted=False)

    def tearDown(self):
        try:
            self.mf.close()
        except Exception:
            pass

    def test_foreign_thread_conn_closed(self):
        st = self.mf.storage
        holder = {}

        def _worker():
            c = st._get_conn()
            c.execute("SELECT 1").fetchone()
            holder["conn"] = c

        t = threading.Thread(target=_worker)
        t.start()
        t.join()
        foreign = holder["conn"]

        st._close_conns()  # 主线程关闭其他线程遗留连接
        with self.assertRaises(sqlite3.ProgrammingError):
            foreign.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
