# -*- coding: utf-8 -*-
"""v5.6.6 回归测试

P2（v5.5.7 遗留）：加密库 content 列为空、FTS5 刻意不落明文，导致进程重启后
TF-IDF 水合与 fuzzy/find_similar/check_duplicates 全部对加密记忆 0 命中
（query.py 结果构建处的解密因此成为死路径）。本测试验证修复后：
  - get_indexable_documents 在内存解密后返回加密条目，磁盘 content 仍为空；
  - 全新 QueryEngine（重新水合）关键词搜索可跨“重启”命中加密记忆；
  - fuzzy_search / find_similar / check_duplicates 均能看到加密记忆；
  - 单条密文损坏时跳过该条而不中断整体检索；
  - federated purge_expired_shared_memories 与 share/get/revoke 多线程并发
    不再发生字典迭代竞态（P3）。

明文只允许驻留进程内存，任何路径都不得把解密明文写回磁盘或 FTS5。
"""

import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

from core.storage import StorageEngine
from core.encryption import EncryptionEngine
from core.indexer import IndexEngine
from core.query import QueryEngine

PASSWORD = "v566-test-password-123"


class _EncryptedCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v566_")
        self.db = os.path.join(self.tmp, "encrypted.db")
        # 主盐值等价于真实部署中 key file 持久化的盐：重开进程时用同一盐重建引擎
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


class TestEncryptedCrossProcessSearch(_EncryptedCase):
    def test_indexable_documents_decrypt_in_memory_only(self):
        self.storage.add_memory("量子纠缠的最新实验验证了贝尔不等式违背",
                                category="science")
        self.storage.add_memory("团队讨论联邦学习的隐私预算分配方案",
                                category="work")

        st = self.reopen()
        docs = st.get_indexable_documents()
        joined = " ".join(docs.values())
        self.assertEqual(len(docs), 2)
        self.assertIn("量子纠缠", joined)
        self.assertIn("联邦学习", joined)

        # 安全边界：磁盘 content 列必须保持为空（静态加密不被破坏）
        for c in self._disk_contents():
            self.assertTrue(c is None or c == "")

    def test_query_engine_keyword_search_after_reopen(self):
        self.storage.add_memory("量子纠缠的最新实验验证了贝尔不等式违背",
                                category="science")
        self.storage.add_memory("今天买了草莓和蓝莓做奶昔", category="life")

        st = self.reopen()
        # 全新 IndexEngine -> needs_hydration 为真，search 内部触发解密水合
        result = QueryEngine(st, IndexEngine()).search(
            "量子纠缠 贝尔不等式", max_results=5, use_embedding=False)
        texts = [c.content for c in result.chunks]
        self.assertTrue(any("量子纠缠" in t for t in texts), texts)
        # 未命中的无关记忆不应出现
        self.assertFalse(any("草莓" in t for t in texts), texts)

    def test_fuzzy_search_after_reopen(self):
        self.storage.add_memory("团队讨论联邦学习的隐私预算分配方案",
                                category="work")
        st = self.reopen()
        hits = st.fuzzy_search("联邦学习 隐私预算", limit=5, threshold=0.1)
        self.assertTrue(
            any("联邦学习" in it["entry"].content for it in hits),
            [it["entry"].content for it in hits])

    def test_find_similar_and_check_duplicates_include_encrypted(self):
        sentence = "量子纠缠的最新实验验证了贝尔不等式违背"
        self.storage.add_memory(sentence, category="science")
        st = self.reopen()

        sims = st.find_similar(sentence, limit=5, threshold=0.2)
        self.assertTrue(any("量子纠缠" in e.content for e in sims),
                        [e.content for e in sims])

        dups = st.check_duplicates(sentence, similarity_threshold=0.8)
        self.assertTrue(any(d["match_type"] == "exact" for d in dups), dups)

    def test_corrupt_ciphertext_is_skipped_not_raised(self):
        good = self.storage.add_memory("这条记忆完好无损可以被检索到",
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
        # 不应抛异常；损坏条目被跳过，完好条目仍可被索引
        docs = st.get_indexable_documents()
        self.assertIn(good.id, docs)
        self.assertNotIn(bad.id, docs)
        self.assertIn("完好无损", docs[good.id])

        # fuzzy 全表扫描遇到坏密文也不应崩溃
        hits = st.fuzzy_search("完好无损", limit=10, threshold=0.1)
        self.assertTrue(
            any(it["entry"].id == good.id for it in hits),
            [it["entry"].id for it in hits])


class TestFederatedSharedMemoryLockRace(unittest.TestCase):
    """P3：purge 遍历+pop shared_memories 与 _replay/share/get/revoke 并发竞态。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v566_fed_")
        db = os.path.join(self.tmp, "fed.db")
        self.storage = StorageEngine(db_path=db, encrypted=False)
        from modules.federated import FederatedMemory
        self.fed = FederatedMemory(storage=self.storage, local_peer_id="local")
        self.fed.register_peer("p", "P", trust_level=0.9,
                               public_key=self.fed.local_public_key)
        self.memory_ids = [self.storage.add_memory(f"内容 {i}").id
                           for i in range(60)]

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_purge_removes_expired(self):
        m = self.storage.add_memory("过期共享")
        shared = self.fed.share_memory(m.id, ["p"], expires_hours=1)
        self.assertIsNotNone(shared)
        shared.expires_at = time.time() - 1
        self.assertEqual(self.fed.purge_expired_shared_memories(), 1)
        self.assertEqual(self.fed.get_shared_memories(), [])

    def test_concurrent_purge_share_get_revoke_no_race(self):
        errors = []
        errors_lock = threading.Lock()
        stop = threading.Event()

        def record(fn):
            try:
                fn()
            except Exception as e:  # 任何 RuntimeError/KeyError 都算回归
                with errors_lock:
                    errors.append(repr(e))

        def worker(seed):
            import itertools
            ticks = itertools.count()
            while not stop.is_set() and next(ticks) < 300:
                mid = self.memory_ids[(seed + next(ticks)) % len(self.memory_ids)]
                # 混合读、写、撤销与“全量过期清理”，最大化并发迭代概率
                record(self.fed.get_shared_memories)
                record(lambda: self.fed.get_shared_memories("p"))
                record(lambda: self.fed.share_memory(mid, ["p"], expires_hours=1))
                if next(ticks) % 3 == 0:
                    record(lambda: self.fed.purge_expired_shared_memories(
                        now=time.time() + 10 ** 9))  # 强制清空所有共享
                else:
                    record(self.fed.purge_expired_shared_memories)
                record(lambda mid=mid: self.fed.revoke_share(mid))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        stop.set()

        self.assertEqual(errors, [], f"并发访问 shared_memories 出现异常: {errors[:5]}")
        # 终止后字典仍可一致地读取
        self.assertIsInstance(self.fed.get_shared_memories(), list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
