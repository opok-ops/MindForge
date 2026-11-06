"""v6.3.2 回归测试：explain 全接口对齐（REST + MCP）

v6.3.0 给 CLI 与门面加了 search --explain，但 REST /api/search 与
MCP memory_search 未暴露——违背「三层接口一致暴露」承诺。本版补齐：

- MCP memory_search 新增 explain 参数（默认 false 零开销）
- REST /api/search 新增 explain 查询参数（1/true/yes/on）
- 不请求 explain 时响应形状与旧版完全一致（不新增 null 键）
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings

warnings.filterwarnings("ignore")


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _MFMixin:
    def _make_mf(self):
        import mindforge
        tmp = tempfile.mkdtemp(prefix="mf_v632_")
        mf = mindforge.MindForge(
            db_path=os.path.join(tmp, "m.db"), encrypted=False
        )
        mf.add(
            "User prefers type-hinted Python code",
            category="preferences",
            importance="HIGH",
        )
        return mf


class TestMCPExplain(unittest.TestCase, _MFMixin):
    """MCP memory_search 的 explain 透传"""

    @classmethod
    def setUpClass(cls):
        cls.mf = cls._make_mf(cls)

    def test_schema_has_explain_param(self):
        """工具 schema 必须声明 explain 布尔参数（默认 false）"""
        from mindforge.mcp.server import TOOL_SCHEMAS
        schema = next(t for t in TOOL_SCHEMAS if t["name"] == "memory_search")
        props = schema["inputSchema"]["properties"]
        self.assertIn("explain", props)
        self.assertEqual(props["explain"]["type"], "boolean")
        self.assertFalse(props["explain"]["default"])

    def test_explain_off_keeps_legacy_shape(self):
        """不传 explain：结果键集与 v6.3.1 完全一致（无 score_breakdown）"""
        from mindforge.mcp.server import h_memory_search
        r = h_memory_search(self.mf, {"query": "python type"})
        self.assertGreaterEqual(r["count"], 1)
        keys = set(r["results"][0].keys())
        self.assertNotIn("score_breakdown", keys)
        self.assertEqual(
            keys,
            {"relevance_score", "memory_id", "content", "category", "tags", "layer"},
        )

    def test_explain_on_returns_breakdown(self):
        """explain=True：每条结果携带得分分解（含 winner 与 final）"""
        from mindforge.mcp.server import h_memory_search
        r = h_memory_search(self.mf, {"query": "python type", "explain": True})
        self.assertGreaterEqual(r["count"], 1)
        item = r["results"][0]
        self.assertIn("score_breakdown", item)
        bd = item["score_breakdown"]
        self.assertIsNotNone(bd)
        for field in ("winner", "final"):
            self.assertIn(field, bd)
        # 至少一条召回路给出数值分
        roads = [k for k in ("tfidf", "fts5", "fuzzy", "vector") if k in bd]
        self.assertGreaterEqual(len(roads), 1)
        # v6.3.0 语义（与 test_v630 同口径）：
        # winner ∈ {tfidf, fts5, fuzzy, vector, text+vector(融合)}；
        # final 与 relevance_score 同一排序口径（CI 装有 sentence-transformers
        # 时会命中 text+vector 融合分支：final = max(fused, 文本最优分)，
        # fused 本身 4 位舍入，故不做 final==fused 的数值断言）
        self.assertIn(bd["winner"], ("tfidf", "fts5", "fuzzy", "vector", "text+vector"))
        self.assertAlmostEqual(
            item["relevance_score"], bd["final"], places=5
        )
        if bd["winner"] == "text+vector":
            self.assertIn("fusion", bd)
            self.assertIn("fused", bd["fusion"])

    def test_explain_non_bool_coerced_off(self):
        """explain 传非布尔值：按 false 处理（类型安全，不炸）"""
        from mindforge.mcp.server import h_memory_search
        r = h_memory_search(self.mf, {"query": "python", "explain": "yes"})
        self.assertGreaterEqual(r["count"], 0)
        if r["results"]:
            self.assertNotIn("score_breakdown", r["results"][0])


class TestRESTExplain(unittest.TestCase, _MFMixin):
    """REST /api/search 的 explain 查询参数"""

    @classmethod
    def setUpClass(cls):
        from mindforge.api.server import start_api_server
        cls.mf = cls._make_mf(cls)
        os.environ["MINDFORGE_ALLOW_NOAUTH"] = "1"
        cls.port = _free_port()
        cls._server = threading.Thread(
            target=start_api_server,
            kwargs={
                "mindforge_instance": cls.mf,
                "host": "127.0.0.1",
                "port": cls.port,
            },
            daemon=True,
        )
        cls._server.start()
        time.sleep(1.2)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("MINDFORGE_ALLOW_NOAUTH", None)

    def _get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            return json.loads(resp.read())

    def test_explain_absent_keeps_legacy_shape(self):
        """不带 explain：响应键集与 v6.3.1 完全一致"""
        r = self._get("/api/search?q=python+type")
        self.assertGreaterEqual(r["total"], 1)
        keys = set(r["results"][0].keys())
        self.assertNotIn("score_breakdown", keys)
        self.assertEqual(
            keys, {"id", "content", "category", "relevance_score", "tags"}
        )

    def test_explain_1_returns_breakdown(self):
        """explain=1：结果携带得分分解"""
        r = self._get("/api/search?q=python+type&explain=1")
        self.assertGreaterEqual(r["total"], 1)
        item = r["results"][0]
        self.assertIn("score_breakdown", item)
        bd = item["score_breakdown"]
        self.assertIsNotNone(bd)
        self.assertIn("winner", bd)
        self.assertIn("final", bd)

    def test_explain_true_returns_breakdown(self):
        """explain=true：等价于 1"""
        r = self._get("/api/search?q=python+type&explain=true")
        self.assertGreaterEqual(r["total"], 1)
        self.assertIn("score_breakdown", r["results"][0])

    def test_explain_0_keeps_legacy_shape(self):
        """explain=0：显式关闭，形状与旧版一致"""
        r = self._get("/api/search?q=python+type&explain=0")
        self.assertGreaterEqual(r["total"], 1)
        self.assertNotIn("score_breakdown", r["results"][0])


if __name__ == "__main__":
    unittest.main()
