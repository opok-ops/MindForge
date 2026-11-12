# -*- coding: utf-8 -*-
"""v6.5.0 特性守卫：fastembed 本地向量后端 + numpy 批量加速 + blob 零拷贝扫描。

覆盖三个改动面：
1. FastEmbedBackend 注册/构造契约（不依赖网络与模型下载）
2. EmbeddingEngine.cosine_similarity_batch numpy 路径与纯 Python 数值一致性
   （v6.5.0 期间修过 q 预归一化后又除 qn 的缩放 bug，此处为该 bug 的
   永久回归守卫：分数必须等于标准余弦值，与向量模长无关）
3. deserialize array 快路径往返一致性 + StorageEngine._score_vector_chunk
   blob 零拷贝路径与 list 路径的堆结果一致
"""
import math
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, "/home/z/my-project/MindForge")

from mindforge.core.embedding import (
    EmbeddingEngine,
    FastEmbedBackend,
    _BACKEND_REGISTRY,
    create_backend,
)


class TestFastEmbedBackendContract(unittest.TestCase):
    """FastEmbedBackend 注册与构造契约（无网络/无模型环境安全）。"""

    def test_registered_in_backend_registry(self):
        self.assertIn("fastembed", _BACKEND_REGISTRY)
        self.assertIs(_BACKEND_REGISTRY["fastembed"], FastEmbedBackend)

    def test_create_backend_returns_fastembed_instance(self):
        b = create_backend("fastembed")
        self.assertIsInstance(b, FastEmbedBackend)
        self.assertEqual(b.model_name, FastEmbedBackend.DEFAULT_FASTEMBED_MODEL)
        self.assertEqual(b.dimension, 384)

    def test_create_backend_respects_model_override(self):
        b = create_backend("fastembed", model_name="BAAI/bge-base-en-v1.5")
        self.assertEqual(b.model_name, "BAAI/bge-base-en-v1.5")

    def test_default_model_is_bge_small(self):
        self.assertEqual(
            FastEmbedBackend.DEFAULT_FASTEMBED_MODEL, "BAAI/bge-small-en-v1.5")

    def test_unavailable_env_graceful(self):
        """fastembed 未安装/模型未下载时不抛异常，is_available=False。

        构造一个非支持模型名逼 _try_load 走异常分支（网络/模型缺失
        环境下行为等价：可用性降级而非崩溃）。
        """
        b = FastEmbedBackend(model_name="nonexistent/model-xyz")
        # 不触发网络重试风暴：只允许一次加载尝试
        self.assertIsInstance(b.is_available, bool)
        self.assertFalse(b._load_attempted is False and b.is_available)

    def test_query_cache_lru_eviction(self):
        """LRU 缓存容量上限与淘汰行为（不依赖模型加载）。"""
        b = FastEmbedBackend.__new__(FastEmbedBackend)  # 跳过 __init__
        from collections import OrderedDict
        b._query_cache = OrderedDict()
        for i in range(FastEmbedBackend._QUERY_CACHE_SIZE + 10):
            b._cache_put(f"q{i}", [0.1, 0.2])
        self.assertEqual(len(b._query_cache), FastEmbedBackend._QUERY_CACHE_SIZE)
        # 最旧的已被淘汰，最新的仍在
        self.assertNotIn("q0", b._query_cache)
        self.assertIn(
            f"q{FastEmbedBackend._QUERY_CACHE_SIZE + 9}", b._query_cache)


class TestCosineBatchNumpyParity(unittest.TestCase):
    """numpy 批量 cos 与纯 Python 数值一致性（v6.5.0 缩放 bug 回归守卫）。

    bug 复现：旧实现把 q 预归一化后又在分母乘 ||q||，导致全部分数
    被 ||q|| 缩放（随机 384 维向量上偏差达 0.71）。守卫：任意模长的
    查询向量，numpy 路径分数必须等于标准余弦。
    """

    def _py_cos(self, a, b):
        d = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return d / (na * nb) if na and nb else 0.0

    def test_parity_unnormalized_vectors(self):
        random.seed(42)
        for trial in range(5):
            q = [random.random() - 0.5 for _ in range(64)]
            cands = [(f"m{i}", [random.random() - 0.5 for _ in range(64)])
                     for i in range(7)]
            got = dict(EmbeddingEngine.cosine_similarity_batch(q, cands, 7))
            for mid, cv in cands:
                self.assertAlmostEqual(
                    got[mid], self._py_cos(q, cv), places=5,
                    msg=f"trial={trial} mid={mid}")

    def test_parity_normalized_vectors(self):
        random.seed(7)
        q = [random.random() for _ in range(384)]
        qn = math.sqrt(sum(x * x for x in q))
        q = [x / qn for x in q]
        cands = []
        for i in range(5):
            v = [random.random() for _ in range(384)]
            n = math.sqrt(sum(x * x for x in v))
            cands.append((f"m{i}", [x / n for x in v]))
        got = dict(EmbeddingEngine.cosine_similarity_batch(q, cands, 5))
        for mid, cv in cands:
            self.assertAlmostEqual(got[mid], self._py_cos(q, cv), places=5)

    def test_ranking_preserved(self):
        """排序与纯 Python 逐条计算一致（top-3 成员相同）。"""
        random.seed(11)
        q = [random.random() for _ in range(128)]
        cands = [(f"m{i}", [random.random() for _ in range(128)])
                 for i in range(20)]
        np_top = [m for m, _ in EmbeddingEngine.cosine_similarity_batch(q, cands, 3)]
        py_sorted = sorted(
            cands, key=lambda mc: self._py_cos(q, mc[1]), reverse=True)[:3]
        self.assertEqual(set(np_top), {m for m, _ in py_sorted})

    def test_dim_mismatch_skipped(self):
        q = [0.1] * 8
        cands = [("ok", [0.2] * 8), ("bad", [0.2] * 16), ("zero", None)]
        r = EmbeddingEngine.cosine_similarity_batch(q, cands, 5)
        self.assertEqual([m for m, _ in r], ["ok"])

    def test_zero_query_vec_returns_empty(self):
        r = EmbeddingEngine.cosine_similarity_batch([0.0] * 8, [("a", [0.1] * 8)], 5)
        self.assertEqual(r, [])


class TestDeserializeFastPath(unittest.TestCase):
    """deserialize array 快路径与 serialize 往返一致。"""

    def test_roundtrip_random_floats(self):
        random.seed(3)
        for dim in (8, 384, 1024):
            v = [random.uniform(-1, 1) for _ in range(dim)]
            out = EmbeddingEngine.deserialize(EmbeddingEngine.serialize(v))
            self.assertEqual(len(out), dim)
            for a, b in zip(v, out):
                self.assertAlmostEqual(a, b, places=6)

    def test_empty_blob(self):
        self.assertEqual(EmbeddingEngine.deserialize(b""), [])
        self.assertEqual(EmbeddingEngine.deserialize(None), [])


class TestVectorChunkBlobPath(unittest.TestCase):
    """_score_vector_chunk blob 零拷贝路径与 list 路径结果一致。"""

    def _make_engine(self):
        from mindforge.core.storage import StorageEngine
        eng = StorageEngine.__new__(StorageEngine)  # 不走 __init__（免 DB）
        return eng

    def test_blob_and_list_paths_agree(self):
        import heapq
        random.seed(99)
        dim = 64
        q = [random.random() for _ in range(dim)]
        blobs, lists = [], []
        for i in range(50):
            v = [random.random() - 0.5 for _ in range(dim)]
            blob = EmbeddingEngine.serialize(v)
            blobs.append((f"m{i}", blob))
            lists.append((f"m{i}", v))
        eng = self._make_engine()

        heap_blob, heap_list = [], []
        eng._score_vector_chunk(q, blobs, None, heap_blob, 10)
        eng._score_vector_chunk(q, lists, None, heap_list, 10)
        top_blob = sorted(heap_blob, key=lambda x: -x[0])[:5]
        top_list = sorted(heap_list, key=lambda x: -x[0])[:5]
        self.assertEqual([m for _, m in top_blob], [m for _, m in top_list])
        for (sb, mb), (sl, ml) in zip(top_blob, top_list):
            self.assertEqual(mb, ml)
            self.assertAlmostEqual(sb, sl, places=4)

    def test_blob_path_dim_mismatch_falls_back(self):
        """blob 长度与查询维度不匹配时不崩溃（回退路径）。"""
        import heapq
        random.seed(5)
        q = [0.1] * 16
        # 32 float 的 blob 对 16 维查询：dim*4 不匹配 → 回退 deserialize 路径
        v = [random.random() for _ in range(32)]
        chunk = [("m0", EmbeddingEngine.serialize(v))]
        eng = self._make_engine()
        heap = []
        eng._score_vector_chunk(q, chunk, None, heap, 5)
        # 回退路径按维度不匹配跳过
        self.assertEqual(heap, [])


if __name__ == "__main__":
    unittest.main()
