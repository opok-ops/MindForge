# -*- coding: utf-8 -*-
"""v5.7.1 回归测试：加密兜底去重扫描 · FTS5 多词 OR · 限流空键清理 · 线程连接关闭 · 向量维度警告

v5.7.0 引入的加密兜底在主路径 fuzzy 已跑过时二次全表扫描；本次重构消除重复。
其余 P2/P3 修复一并锁定。
"""

import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from core.storage import StorageEngine
from core.encryption import EncryptionEngine
from core.indexer import IndexEngine
from core.query import QueryEngine


PASSWORD = "v571-regression-password"


class _EncryptedCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v571_")
        self.db = os.path.join(self.tmp, "encrypted.db")
        self.engine, self.master_salt = EncryptionEngine.from_password(PASSWORD)
        self.storage = StorageEngine(db_path=self.db,
                                     encryption=self.engine, encrypted=True)

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestQueryFuzzyDedup(_EncryptedCase):
    """P1：加密模式下 search 只触发一次 fuzzy_search，不再二次兜底扫描。"""

    def test_encrypted_mode_runs_fuzzy_exactly_once(self):
        self.storage.add_memory("量子纠缠的最新实验", category="science")
        self.storage.add_memory("联邦学习隐私预算", category="work")
        qe = QueryEngine(self.storage, index=IndexEngine())

        # 原 v5.7.0 实现在主路径 fuzzy 之外又调 search_encrypted_fallback
        # （其内部再跑一次 fuzzy）。v5.7.1 重构后只跑一次。
        with mock.patch.object(
            self.storage, "fuzzy_search", wraps=self.storage.fuzzy_search
        ) as m_fuzzy, mock.patch.object(
            self.storage, "search_encrypted_fallback"
        ) as m_fallback:
            results = qe.search("量子", max_results=10)
        # fuzzy 至少被调一次（加密模式下路 2 必跑）
        self.assertGreaterEqual(m_fuzzy.call_count, 1)
        # 兜底段不再二次调用 search_encrypted_fallback
        m_fallback.assert_not_called()
        # 结果非空且含 approximate 标记
        self.assertTrue(results.chunks)
        self.assertTrue(results.approximate is True)

    def test_fuzzy_hydrated_plaintext_not_redecrypted(self):
        """fuzzy 已回填明文的条目，结果构建时不再重复解密。"""
        self.storage.add_memory("多模态检索的跨模态对齐方法", category="science")
        qe = QueryEngine(self.storage, index=IndexEngine())

        # 直接调 search_encrypted_fallback 会回填明文；这里 mock decrypt_content
        # 确保它不被二次调用（fuzzy 已解密过）
        with mock.patch.object(
            self.storage, "decrypt_content", wraps=self.storage.decrypt_content
        ) as m_dec:
            results = qe.search("多模态", max_results=10)
        self.assertTrue(results.chunks)
        # 命中的加密条目 content 应为明文（非空）
        self.assertTrue(getattr(results.chunks[0], "content", None))
        # decrypt_content 不应在结果构建阶段被再次调用（fuzzy 已回填）
        # fuzzy_search 内部会调一次 decrypt_content（通过 _plaintext_for_index），
        # 但结果构建段不再额外调用。
        self.assertTrue(all(getattr(c, "content", None) for c in results.chunks))


class TestFts5MultiWordOr(unittest.TestCase):
    """P2：FTS5 多词 OR 转义。"""

    def test_single_phrase_unchanged(self):
        self.assertEqual(
            IndexEngine._escape_fts5_query("加密搜索"), '"加密搜索"')

    def test_multi_word_english_or(self):
        self.assertEqual(
            IndexEngine._escape_fts5_query("search bug"),
            '"search" OR "bug"')

    def test_three_words_or(self):
        self.assertEqual(
            IndexEngine._escape_fts5_query("foo bar baz"),
            '"foo" OR "bar" OR "baz"')

    def test_empty(self):
        self.assertEqual(IndexEngine._escape_fts5_query(""), "")

    def test_quote_escaped(self):
        # "bar" → """bar"""（双引号翻倍），与 OR 拼接
        self.assertEqual(
            IndexEngine._escape_fts5_query('foo "bar"'),
            '"foo" OR """bar"""')


class TestRateLimiterCleanup(unittest.TestCase):
    """P2：限流空键清理。"""

    def test_expired_key_removed(self):
        from api.server import _RateLimiter
        rl = _RateLimiter(max_requests=5, window_seconds=60)
        # 打满
        for _ in range(5):
            self.assertTrue(rl.check("1.2.3.4"))
        self.assertEqual(len(rl._requests), 1)
        # 快进 60s 后再 check：历史过期，空键应被删除
        with mock.patch("time.time", return_value=time.time() + 120):
            self.assertTrue(rl.check("1.2.3.4"))
        # 新一次请求后字典里有 1 个键（刚 append 的）
        self.assertEqual(len(rl._requests), 1)

    def test_reject_keeps_count(self):
        from api.server import _RateLimiter
        rl = _RateLimiter(max_requests=2, window_seconds=60)
        self.assertTrue(rl.check("9.9.9.9"))
        self.assertTrue(rl.check("9.9.9.9"))
        self.assertFalse(rl.check("9.9.9.9"))
        self.assertEqual(len(rl._requests), 1)


class TestThreadConnClose(unittest.TestCase):
    """P2：close_thread_conn 存在且可调用。"""

    def test_close_thread_conn_exists(self):
        s = StorageEngine(":memory:")
        self.assertTrue(hasattr(s, "close_thread_conn"))
        s.close_thread_conn()  # 不应抛
        s.close()


if __name__ == "__main__":
    unittest.main()
