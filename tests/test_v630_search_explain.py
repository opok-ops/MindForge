# -*- coding: utf-8 -*-
"""v6.3.0 检索可解释性（search --explain）回归测试

覆盖：
  - explain=False：score_breakdown 恒为 None（默认零开销、序列化兼容）
  - explain=True：四路字段齐全（tfidf/fts5/fuzzy/vector + winner + final）
  - winner 与 relevance_score 一致性：final == 胜出路的贡献分
  - 各路分数与单路召回一致性：breakdown.tfidf ∈ TF-IDF 直接召回结果集
  - facade 透传：MindForge.search(explain=True) 生效
  - CLI --explain 输出含「路径:」与各路分数行；JSON 模式含 score_breakdown 键
"""
import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from mindforge.core.mindforge import MindForge  # noqa: E402
from mindforge.core.types import MemoryConfig  # noqa: E402


def _make_mf(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix, dir=_REPO)
    cfg = MemoryConfig(
        db_path=os.path.join(tmp, "m.db"),
        key_file=os.path.join(tmp, "k.key"),
        encrypted=False,
    )
    return MindForge(config=cfg), tmp


class TestSearchExplain(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mf, cls.tmp = _make_mf("mf_v630_exp_")
        cls.mf.add("Python 并发编程指南：线程池与 GIL 深度解析", category="tech")
        cls.mf.add("Python 数据分析实战：pandas 与 numpy", category="tech")
        cls.mf.add("Go 语言高并发网络编程", category="tech")
        cls.mf.add("机器学习入门：梯度下降与反向传播", category="ml")

    @classmethod
    def tearDownClass(cls):
        cls.mf.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_default_off_is_none(self):
        """explain=False：breakdown 为 None（不破坏现有契约）"""
        r = self.mf.search("Python 并发", max_results=5, use_embedding=False)
        self.assertTrue(r.chunks)
        for c in r.chunks:
            self.assertIsNone(c.score_breakdown)

    def test_explain_breakdown_schema(self):
        """explain=True：四路字段 + winner + final 齐全"""
        r = self.mf._query.search(
            "Python 并发", max_results=5, use_embedding=False, explain=True)
        self.assertTrue(r.chunks)
        for c in r.chunks:
            bd = c.score_breakdown
            self.assertIsNotNone(bd, "explain=True 必须携带 breakdown")
            for road in ("tfidf", "fts5", "fuzzy", "vector"):
                self.assertIn(road, bd)
                self.assertIsInstance(bd[road], float)
            self.assertIn(bd["winner"],
                          ("tfidf", "fts5", "fuzzy", "vector", "text+vector"))
            self.assertIn("final", bd)

    def test_final_matches_relevance(self):
        """final 与 relevance_score 一致（同一次排序口径）"""
        r = self.mf._query.search(
            "Python 并发", max_results=5, use_embedding=False, explain=True)
        for c in r.chunks:
            if c.score_breakdown and "final" in c.score_breakdown:
                self.assertAlmostEqual(
                    c.relevance_score, c.score_breakdown["final"], places=5)

    def test_tfidf_road_consistent_with_direct_recall(self):
        """breakdown.tfidf 与 TF-IDF 直接召回同源"""
        r = self.mf._query.search(
            "Python", max_results=10, use_embedding=False, explain=True)
        direct = dict(self.mf._query.index.search("Python", top_k=30))
        for c in r.chunks:
            bd = c.score_breakdown
            if bd and bd["tfidf"] > 0:
                self.assertAlmostEqual(
                    bd["tfidf"], direct.get(c.memory_id, 0.0), places=5)

    def test_facade_passthrough(self):
        """facade MindForge.search(explain=True) 透传生效"""
        r = self.mf.search("Python 并发", max_results=5,
                           use_embedding=False, explain=True)
        self.assertTrue(r.chunks)
        self.assertIsNotNone(r.chunks[0].score_breakdown)

    def test_cli_explain_output(self):
        """CLI --explain 输出路径行；JSON 模式含 score_breakdown 键"""
        from mindforge.cli import main as cli_main
        base = argparse.Namespace(
            query="Python 并发",
            key_file=os.path.join(self.tmp, "k.key"),
            db_path=os.path.join(self.tmp, "m.db"),
            agent="", session="", category=None, limit=5,
            no_embedding=True, explain=True, json_output=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main.cmd_search(base)
        out = buf.getvalue()
        self.assertIn("路径:", out)
        self.assertIn("tfidf", out)
        self.assertIn("final", out)

        base.json_output = True
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main.cmd_search(base)
        data = json.loads(buf.getvalue())
        self.assertIn("score_breakdown", data["chunks"][0])
        self.assertIsNotNone(data["chunks"][0]["score_breakdown"])


if __name__ == "__main__":
    unittest.main()
