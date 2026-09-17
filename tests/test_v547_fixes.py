# -*- coding: utf-8 -*-
"""v5.4.7 回归测试（v5.6.9 重写为可收集的 pytest 用例）

锁定 v5.4.7 的三处修复，防止后续改动静默破坏：

  修复 1 ``MindForge.get_embedding_status()``：embedding engine 不可用时
      仍须查询 DB 返回**实际**向量数量（修复前直接返回 0，掩盖了历史向量存在的事实）。
  修复 2 ``IndexEngine._escape_fts5_query()`` + ``fts_search()``：FTS5 MATCH 语法中
      ``+ - * ( ) " :`` 等是特殊字符，未转义会抛 ``OperationalError``（C++、hello:world 这类查询直接崩）。
  修复 3 ``StorageEngine.vector_search(query_vector=...)``：engine 不可用时，
      传入预计算向量仍能走 fallback 反序列化 + 余弦相似度完成检索。

历史问题（本文件被重写的原因）：原版是一个纯 ``print`` 脚本——0 个 ``test_`` 函数、
0 个 ``assert``、无 ``__main__`` 块，且 CI 从不调用它。结果是这 3 处修复长期
处于「零回归保护」状态。现改为标准 unittest，由 ``pytest tests/`` 正常收集执行。
"""

import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.indexer import IndexEngine
from core.storage import StorageEngine
from core.types import MemoryLayer


class _V547Case(unittest.TestCase):
    """共用夹具：隔离的明文 DB（明文库才会建 FTS 索引）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v547_")
        self.db_path = os.path.join(self.tmp, "test.db")
        self.storage = StorageEngine(db_path=self.db_path, encrypted=False)
        # 禁用 embedding engine，避免单测触发模型下载
        self.storage._embedding_eng = None

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ============================================================
# 修复 1: get_embedding_status() 在 engine 不可用时仍返回 DB 实际数量
# ============================================================
class TestEmbeddingStatusWithUnavailableEngine(_V547Case):
    def _make_mindforge(self):
        """构造一个共享同一 DB 的 MindForge 实例（engine 保持不可用）。"""
        from core.mindforge import MindForge
        from core.types import MemoryConfig

        config = MemoryConfig(db_path=self.db_path, encrypted=False)
        mf = MindForge(config=config)
        # 关键：确保 engine 不可用，走 v5.4.7 修复的那条分支
        mf._storage._embedding_eng = None
        return mf

    def _insert_vectors(self, ids):
        """直接往 memory_embeddings 写 float32 小端序向量 blob。"""
        conn = self.storage._get_conn()
        for mem_id in ids:
            blob = struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)
            conn.execute(
                "INSERT OR REPLACE INTO memory_embeddings"
                " (memory_id, embedding, model_name, dimension, created_at)"
                " VALUES (?,?,?,?,?)",
                (mem_id, blob, "test-model", 4, 1000.0),
            )
        conn.commit()

    def test_returns_real_db_count_when_engine_unavailable(self):
        """engine 不可用 ≠ 向量不存在：必须返回 DB 里的真实条数。"""
        entries = [
            self.storage.add_memory(
                f"v547 embedding probe {i}", category="v547cat",
                layer=MemoryLayer.LONG_TERM)
            for i in range(3)
        ]
        self._insert_vectors([e.id for e in entries])

        mf = self._make_mindforge()
        status = mf.get_embedding_status()

        # 修复核心断言：不可用时 embedding_count 仍是 3，而不是 0
        self.assertEqual(status["embedding_count"], 3)
        self.assertFalse(status["available"])
        self.assertEqual(status["model_name"], "")
        self.assertEqual(status["dimension"], 0)

    def test_empty_db_returns_zero_count(self):
        """空库时计数应为 0（确认上面不是恒真断言）。"""
        mf = self._make_mindforge()
        status = mf.get_embedding_status()
        self.assertEqual(status["embedding_count"], 0)
        self.assertFalse(status["available"])


# ============================================================
# 修复 2: FTS5 查询特殊字符转义
# ============================================================
class TestFts5QueryEscaping(_V547Case):
    def test_escape_wraps_as_phrase(self):
        """特殊字符不再直接进入 MATCH 语法，而是被包成短语查询。"""
        cases = [
            ("C++", '"C++"'),
            ("hello:world", '"hello:world"'),
            ("(test)", '"(test)"'),
            ("MySQL 复制", '"MySQL 复制"'),
        ]
        for raw, expect in cases:
            with self.subTest(query=raw):
                self.assertEqual(IndexEngine._escape_fts5_query(raw), expect)

    def test_escape_doubles_inner_quotes(self):
        """内部双引号必须成对转义，否则短语提前闭合导致语法错。"""
        self.assertEqual(IndexEngine._escape_fts5_query('a"b'), '"a""b"')

    def test_blank_input_returns_blank(self):
        """空串/纯空白返回空，调用方据此短路返回 []，不进 MATCH。"""
        self.assertEqual(IndexEngine._escape_fts5_query(""), "")
        self.assertEqual(IndexEngine._escape_fts5_query("   "), "")

    def test_fts_search_survives_special_chars(self):
        """端到端：含特殊字符的查询不得抛 OperationalError，且能找到命中项。"""
        self.storage.add_memory(
            "C++ is a programming language", category="v547fts",
            layer=MemoryLayer.LONG_TERM)
        self.storage.add_memory(
            "Python decorator pattern", category="v547fts",
            layer=MemoryLayer.LONG_TERM)

        conn = self.storage._get_conn()
        conn.execute("""
            INSERT INTO memory_fts (rowid, content, category, tags)
            SELECT rowid, content, category, tags FROM memories
        """)
        conn.commit()

        indexer = IndexEngine()

        results = indexer.fts_search(conn, "C++", top_k=5)
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0, "含 '+' 的查询应能命中 C++ 那条")

        # 这几个在修复前会直接抛 sqlite3.OperationalError
        for tricky in ("decorator(pattern)", "test:value", "(x)", 'a"b'):
            with self.subTest(query=tricky):
                self.assertIsInstance(
                    indexer.fts_search(conn, tricky, top_k=5), list)


# ============================================================
# 修复 3: vector_search 支持预计算 query_vector
# ============================================================
class TestVectorSearchWithQueryVector(_V547Case):
    def _seed(self):
        """建 3 条记忆并注入可预测的 4 维向量。"""
        e_phone = self.storage.add_memory(
            "手机是通讯工具", category="v547vec", layer=MemoryLayer.LONG_TERM)
        e_apple = self.storage.add_memory(
            "苹果是水果", category="v547vec", layer=MemoryLayer.LONG_TERM)
        e_tablet = self.storage.add_memory(
            "平板电脑是电子设备", category="v547vec", layer=MemoryLayer.LONG_TERM)

        vectors = {
            e_phone.id: [1.0, 0.0, 0.0, 0.0],
            e_apple.id: [0.0, 1.0, 0.0, 0.0],
            e_tablet.id: [0.9, 0.1, 0.0, 0.0],
        }
        conn = self.storage._get_conn()
        for mem_id, vec in vectors.items():
            blob = struct.pack(f"<{len(vec)}f", *vec)
            conn.execute(
                "INSERT OR REPLACE INTO memory_embeddings"
                " (memory_id, embedding, model_name, dimension, created_at)"
                " VALUES (?,?,?,?,?)",
                (mem_id, blob, "test", 4, 1000.0),
            )
        conn.commit()
        return e_phone, e_apple, e_tablet

    def test_no_query_vector_returns_empty_when_engine_down(self):
        """engine 不可用又没给向量 → 无法 encode query → 返回空（不崩）。"""
        self._seed()
        self.assertEqual(
            self.storage.vector_search(query="通讯", top_k=5), [])

    def test_query_vector_enables_search_when_engine_down(self):
        """修复核心：给了预计算向量就能在 engine 不可用时完成检索与排序。"""
        e_phone, _e_apple, e_tablet = self._seed()

        results = self.storage.vector_search(
            query="", top_k=5, query_vector=[1.0, 0.0, 0.0, 0.0])

        self.assertGreaterEqual(len(results), 2)
        self.assertTrue(
            all(r["strategy"] == "vector" for r in results))
        # 余弦相似度排序：与 [1,0,0,0] 最近的是手机，其次是平板
        self.assertEqual(results[0]["entry"].id, e_phone.id)
        self.assertEqual(results[1]["entry"].id, e_tablet.id)

    def test_deserialize_vector_fallback(self):
        """fallback 反序列化：维度必须严格匹配，空 blob 返回 None。"""
        blob = struct.pack("<4f", 1.0, 2.0, 3.0, 4.0)
        self.assertEqual(
            StorageEngine._deserialize_vector_fallback(blob, 4),
            [1.0, 2.0, 3.0, 4.0])
        self.assertIsNone(
            StorageEngine._deserialize_vector_fallback(blob, 3))
        self.assertIsNone(
            StorageEngine._deserialize_vector_fallback(b"", 4))

    def test_cosine_similarity_batch_fallback(self):
        """fallback 余弦批量计算：top_k 截断 + 相关性排序正确。"""
        candidates = [
            ("a", [1.0, 0.0]),
            ("b", [0.0, 1.0]),
            ("c", [0.707, 0.707]),
        ]
        batch = StorageEngine._cosine_similarity_batch_fallback(
            [1.0, 0.0], candidates, top_k=2)
        self.assertEqual(len(batch), 2)
        self.assertEqual(batch[0][0], "a")
        # 零向量与维度不匹配的候选都必须被跳过
        self.assertEqual(
            StorageEngine._cosine_similarity_batch_fallback([0.0, 0.0], candidates),
            [])
        self.assertEqual(
            StorageEngine._cosine_similarity_batch_fallback([1.0, 0.0, 0.0], candidates),
            [])


if __name__ == "__main__":
    unittest.main()
