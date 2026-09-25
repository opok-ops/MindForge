# -*- coding: utf-8 -*-
"""v5.7.8 向量检索点亮 回归测试

真实增量（以代码现状为准）：
  1. core/embedding.py 新增 FakeBackend（确定性伪向量后端，注册表 + create_backend 分支）
  2. benchmarks/embedding_eval.py 可复现评测基线（四路对比 + Recall@5/MRR@10/NDCG@10）
  3. api/server.py 新增 GET /api/embedding/status

覆盖：FakeBackend 确定性 / 注册表 / 评测可复现性 / 向量融合路由 / API / CLI / 降级路径。
"""

import io
import json
import os
import shutil
import sys
import tempfile
import socket
import threading
import time
import unittest
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "benchmarks"))

from core.embedding import EmbeddingEngine, FakeBackend, create_backend  # noqa: E402
from core.mindforge import MindForge  # noqa: E402
from core.types import MemoryConfig  # noqa: E402

_SAVED_ENV = {}


def _reset_engine():
    EmbeddingEngine._instance = None


def _make_mf(encrypted=False):
    tmp = tempfile.mkdtemp(prefix="mf_v578_", dir=_REPO)
    db = os.path.join(tmp, "m.db")
    key = os.path.join(tmp, "k.key")
    cfg = MemoryConfig(db_path=db, key_file=key, encrypted=encrypted)
    return MindForge(config=cfg), tmp


class TestFakeBackend(unittest.TestCase):
    """FakeBackend：确定性、归一化、维度、区分度"""

    def test_deterministic_and_distinguishable(self):
        b = FakeBackend(seed=42, dimension=64)
        v1 = b.encode("python 连接 mysql 数据库")
        v2 = b.encode("python 连接 mysql 数据库")
        v3 = b.encode("cooking recipe with tomatoes")
        self.assertEqual(v1, v2)  # 确定性
        self.assertNotEqual(v1, v3)  # 区分度
        # L2 归一化
        norm = sum(x * x for x in v1) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=6)
        # 维度
        self.assertEqual(len(v1), 64)
        self.assertEqual(b.dimension, 64)

    def test_shared_ngram_similarity(self):
        b = FakeBackend(seed=7, dimension=384)
        a = b.encode("memory decay and forgetting curve")
        c = b.encode("memory decay rate accelerates")
        d = b.encode("quantum entanglement teleportation")
        cos_ac = sum(x * y for x, y in zip(a, c))
        cos_ad = sum(x * y for x, y in zip(a, d))
        self.assertGreater(cos_ac, cos_ad)  # 共享词面 → 更高相似度

    def test_encode_empty_returns_none(self):
        b = FakeBackend()
        self.assertIsNone(b.encode(""))
        self.assertIsNone(b.encode_batch([]))

    def test_registry_and_factory(self):
        from core.embedding import _BACKEND_REGISTRY
        self.assertIn("fake", _BACKEND_REGISTRY)
        b = create_backend("fake", seed=11, dimension=128)
        self.assertIsInstance(b, FakeBackend)
        self.assertEqual(b.dimension, 128)
        # 未知后端报错列出可用名
        with self.assertRaises(ValueError) as ctx:
            create_backend("no-such-backend")
        self.assertIn("fake", str(ctx.exception))


class TestEvalBaseline(unittest.TestCase):
    """评测基线：可复现、四路指标结构"""

    def test_eval_reproducible_same_seed(self):
        import embedding_eval
        r1 = embedding_eval.run_eval(backend="fake", seed=42, limit=10)
        r2 = embedding_eval.run_eval(backend="fake", seed=42, limit=10)
        self.assertEqual(r1["metrics"], r2["metrics"])
        self.assertEqual(r1["backend"], "fake")
        self.assertEqual(r1["dataset_size"], 10)
        for name in ("hybrid", "vector", "fts5", "tfidf"):
            self.assertIn(name, r1["metrics"])
            for k in ("recall@5", "mrr@10", "ndcg@10"):
                self.assertIn(k, r1["metrics"][name])
                self.assertGreaterEqual(r1["metrics"][name][k], 0.0)
                self.assertLessEqual(r1["metrics"][name][k], 1.0)

    def test_eval_hybrid_not_worse_than_single(self):
        # FakeBackend 下融合应至少不差于单路（词面重合场景）
        import embedding_eval
        r = embedding_eval.run_eval(backend="fake", seed=42, limit=20)
        h = r["metrics"]["hybrid"]
        t = r["metrics"]["tfidf"]
        self.assertGreaterEqual(h["recall@5"], t["recall@5"] - 0.01)


class TestVectorRoute(unittest.TestCase):
    """向量融合路由：FakeBackend 注入后 search 走 vector 路"""

    def _env_fake(self):
        _reset_engine()
        os.environ["MINDFORGE_EMBEDDING_BACKEND"] = "fake"

    def test_search_uses_vector_route_with_fake_backend(self):
        self._env_fake()
        mf, tmp = _make_mf()
        try:
            e = mf.add("python 连接 mysql 数据库执行查询")
            r = mf.search("python 连接 mysql", max_results=5)
            self.assertGreaterEqual(r.total_found, 1)
            self.assertIn("vector", r.strategy_used)
            self.assertTrue(any("mysql" in c.content for c in r.chunks))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_rebuild_embeddings_with_fake(self):
        self._env_fake()
        mf, tmp = _make_mf()
        try:
            mf.add("向量重建测试内容")
            res = mf.rebuild_embeddings(incremental=False)
            self.assertTrue(res["success"])
            self.assertGreaterEqual(res["embedded"], 1)
            st = mf.get_embedding_status()
            self.assertTrue(st["available"])
            self.assertIn("fake", st["model_name"].lower())
            self.assertGreaterEqual(st["embedding_count"], 1)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_degradation_without_backend(self):
        # 无可用后端（sentence_transformers 未装 + 环境无 key）→ 降级仍可搜索
        _reset_engine()
        os.environ["MINDFORGE_EMBEDDING_BACKEND"] = "sentence_transformers"
        os.environ.pop("MINDFORGE_EMBEDDING_MODEL", None)
        mf, tmp = _make_mf()
        try:
            mf.add("降级路径搜索内容")
            r = mf.search("降级路径", max_results=5)
            self.assertGreaterEqual(r.total_found, 1)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestAPI(unittest.TestCase):
    """REST API：GET /api/embedding/status"""

    def test_embedding_status_endpoint(self):
        from api.server import start_api_server
        _reset_engine()
        os.environ["MINDFORGE_ALLOW_NOAUTH"] = "1"
        os.environ["MINDFORGE_EMBEDDING_BACKEND"] = "fake"
        mf, tmp = _make_mf()
        try:
            mf.add("API 嵌入状态内容")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _sock:
                _sock.bind(("127.0.0.1", 0))
                port = _sock.getsockname()[1]
            srv = threading.Thread(
                target=start_api_server,
                kwargs={"mindforge_instance": mf, "host": "127.0.0.1", "port": port},
                daemon=True,
            )
            srv.start()
            time.sleep(1.5)
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/embedding/status", timeout=5) as resp:
                st = json.loads(resp.read().decode())
            self.assertTrue(st["available"])
            self.assertIn("fake", st["model_name"].lower())
            self.assertGreaterEqual(st["embedding_count"], 0)
            self.assertGreater(st["dimension"], 0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestCLI(unittest.TestCase):
    """CLI：embedding-status 冒烟（fake 后端）"""

    def test_cli_embedding_status(self):
        _reset_engine()
        os.environ["MINDFORGE_EMBEDDING_BACKEND"] = "fake"
        from cli.main import cmd_embedding_status, _get_memory
        mf, tmp = _make_mf()
        try:
            mf.add("CLI 嵌入状态内容")
            mf.close()
            # 用 argparse 模拟 --db/--key
            import argparse
            args = argparse.Namespace(db_path=os.path.join(tmp, "m.db"),
                                      key_file=os.path.join(tmp, "k.key"))
            buf = io.StringIO()
            import contextlib
            with contextlib.redirect_stdout(buf):
                code = cmd_embedding_status(args)
            self.assertEqual(code, 0)
            out = buf.getvalue()
            self.assertIn("可用", out)
            self.assertIn("fake", out.lower())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
