# -*- coding: utf-8 -*-
"""v5.7.0 P1 回归测试：加密模式全量解密扫描兜底（方案 A）

背景（v5.5.7 遗留，v5.6.6 部分修复）：加密库 content 列为空、FTS5 刻意不落
明文，进程重启后关键词/模糊搜索曾对加密记忆 0 命中。v5.6.6 修复了 TF-IDF
水合与 fuzzy 的内存解密；v5.7.0 再补一层「全量解密扫描兜底」，保证召回不足时
加密条目仍可被检索，并将结果标记 approximate=True。

安全边界：明文只允许驻留进程内存，磁盘 content 列必须保持为空。
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

from core.storage import StorageEngine
from core.encryption import EncryptionEngine
from core.indexer import IndexEngine
from core.query import QueryEngine
from core.mindforge import MindForge
from core.types import MemoryConfig

PASSWORD = "v570-fallback-password-123"


class _EncryptedCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v570_")
        self.db = os.path.join(self.tmp, "encrypted.db")
        self.engine, self.master_salt = EncryptionEngine.from_password(PASSWORD)
        self.storage = StorageEngine(db_path=self.db,
                                     encryption=self.engine, encrypted=True)

    def reopen(self):
        """模拟进程重启：新建引擎（同密码+同主盐）+ 全新 StorageEngine。"""
        self.storage.close()
        engine, _ = EncryptionEngine.from_password(PASSWORD, salt=self.master_salt)
        self.storage = StorageEngine(db_path=self.db,
                                     encryption=engine, encrypted=True)
        return self.storage

    def _disk_contents(self):
        conn = sqlite3.connect(self.db)
        try:
            return [r[0] for r in conn.execute("SELECT content FROM memories")]
        finally:
            conn.close()

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestEncryptedFallbackDirect(_EncryptedCase):
    """search_encrypted_fallback 单元行为。"""

    def test_fallback_finds_encrypted_entries_after_reopen(self):
        self.storage.add_memory("量子纠缠的最新实验验证了贝尔不等式违背",
                                category="science")
        self.storage.add_memory("团队讨论联邦学习的隐私预算分配方案",
                                category="work")

        st = self.reopen()
        hits = st.search_encrypted_fallback("量子纠缠", limit=5)
        self.assertTrue(
            any("量子纠缠" in h["entry"].content for h in hits),
            [h["entry"].content for h in hits])
        # 兜底只返回加密条目
        self.assertTrue(all(h["entry"].encrypted for h in hits))

    def test_fallback_returns_empty_for_plain_store(self):
        plain = StorageEngine(db_path=os.path.join(self.tmp, "plain.db"),
                              encrypted=False)
        try:
            plain.add_memory("明文记忆", category="ok")
            self.assertEqual(plain.search_encrypted_fallback("明文"), [])
        finally:
            plain.close()

    def test_fallback_defensive_inputs(self):
        self.assertEqual(self.storage.search_encrypted_fallback(None), [])
        self.assertEqual(self.storage.search_encrypted_fallback(""), [])
        self.assertEqual(self.storage.search_encrypted_fallback("   "), [])

    def test_fallback_skips_corrupt_ciphertext(self):
        good = self.storage.add_memory("这条记忆完好无损可以被兜底检索到",
                                       category="ok")
        bad = self.storage.add_memory("这条记忆的密文将被损坏", category="ok")

        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE memories SET ciphertext = ? WHERE id = ?",
                         (b"\x00" * 32, bad.id))
            conn.commit()
        finally:
            conn.close()

        st = self.reopen()
        hits = st.search_encrypted_fallback("完好无损", limit=10, threshold=0.1)
        ids = [h["entry"].id for h in hits]
        self.assertIn(good.id, ids)
        self.assertNotIn(bad.id, ids)

    def test_fallback_respects_category_and_limit(self):
        self.storage.add_memory("量子纠缠实验验证贝尔不等式", category="science")
        self.storage.add_memory("量子计算路线图讨论", category="work")

        st = self.reopen()
        hits = st.search_encrypted_fallback(
            "量子", category="science", limit=5, threshold=0.1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["entry"].category, "science")

    def test_disk_content_stays_empty(self):
        self.storage.add_memory("兜底扫描不应把明文写回磁盘", category="ok")
        st = self.reopen()
        hits = st.search_encrypted_fallback("兜底扫描", limit=5, threshold=0.1)
        self.assertTrue(hits)
        for c in self._disk_contents():
            self.assertTrue(c is None or c == "")


class TestQueryEngineApproximateFlag(_EncryptedCase):
    """QueryEngine.search 在加密模式下的 approximate 标记与召回。"""

    def test_search_marks_approximate_and_returns_encrypted(self):
        self.storage.add_memory("量子纠缠的最新实验验证了贝尔不等式违背",
                                category="science")
        self.storage.add_memory("今天买了草莓和蓝莓做奶昔", category="life")

        st = self.reopen()
        result = QueryEngine(st, IndexEngine()).search(
            "量子纠缠 贝尔不等式", max_results=50, use_embedding=False)
        texts = [c.content for c in result.chunks]
        self.assertTrue(any("量子纠缠" in t for t in texts), texts)
        self.assertTrue(result.approximate)

    def test_plain_store_never_approximate(self):
        plain = StorageEngine(db_path=os.path.join(self.tmp, "plain2.db"),
                              encrypted=False)
        try:
            plain.add_memory("明文记忆内容", category="ok")
            result = QueryEngine(plain, IndexEngine()).search(
                "明文记忆", max_results=50, use_embedding=False)
            self.assertFalse(result.approximate)
            self.assertTrue(result.chunks)
        finally:
            plain.close()

    def test_search_with_category_filter_uses_fallback(self):
        self.storage.add_memory("量子纠缠实验验证贝尔不等式", category="science")
        self.storage.add_memory("量子计算路线图", category="work")

        st = self.reopen()
        result = QueryEngine(st, IndexEngine()).search(
            "量子", categories=["science"], max_results=50, use_embedding=False)
        cats = {c.category for c in result.chunks}
        self.assertEqual(cats, {"science"})
        self.assertTrue(result.approximate)


class TestMindForgeLevel(_EncryptedCase):
    """MindForge 门面层：approximate 经隐私过滤后仍透传。"""

    def test_mindforge_search_propagates_approximate(self):
        key_file = os.path.join(self.tmp, "key.bin")
        config = MemoryConfig(db_path=self.db, encrypted=True, key_file=key_file)
        mf = MindForge(config=config)
        mf.init_with_password(PASSWORD)
        mf.add("量子纠缠的最新实验验证了贝尔不等式违背", category="science")
        mf.close()

        # 模拟进程重启：同一 key 文件 + 密码重建 MindForge
        mf2 = MindForge(config=config)
        mf2.init_with_password(PASSWORD)
        try:
            result = mf2.search("量子纠缠 贝尔不等式", max_results=50,
                                use_embedding=False)
            texts = [c.content for c in result.chunks]
            self.assertTrue(any("量子纠缠" in t for t in texts), texts)
            self.assertTrue(result.approximate)
        finally:
            mf2.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
