# -*- coding: utf-8 -*-
"""v5.8.5 LoCoMo/LongMemEval 评测脚手架回归测试

验证脚手架端到端可跑、指标字段齐全、deterministic。
不依赖真实 LLM（keyword 裁判），不依赖外部网络。
"""
import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


class TestLocomoEvalScaffold(unittest.TestCase):
    def test_run_eval_end_to_end(self):
        from benchmarks.locomeval_eval import run_eval
        report = run_eval(k=5, judge="keyword", seed=42)
        # 顶层字段齐全
        for key in ("scaffold", "dataset_version", "mindforge_version",
                    "env", "n_sessions_loaded", "n_questions",
                    "metrics", "by_type", "per_query"):
            self.assertIn(key, report)
        # 指标合理
        m = report["metrics"]
        self.assertGreaterEqual(m["recall_at_k"], 0.5)
        self.assertGreaterEqual(m["mrr_at_k"], 0.3)
        self.assertGreater(m["latency_p50_ms"], 0)
        # 三类问题都覆盖
        self.assertEqual(set(report["by_type"].keys()),
                         {"singlehop", "multihop", "temporal"})
        # per_query 与 n_questions 一致
        self.assertEqual(len(report["per_query"]), report["n_questions"])

    def test_deterministic_same_seed(self):
        from benchmarks.locomeval_eval import run_eval
        r1 = run_eval(k=5, seed=42)
        r2 = run_eval(k=5, seed=42)
        # 指标聚合在同 seed 下完全一致（latency 除外）
        self.assertEqual(r1["metrics"]["recall_at_k"],
                         r2["metrics"]["recall_at_k"])
        self.assertEqual(r1["metrics"]["mrr_at_k"],
                         r2["metrics"]["mrr_at_k"])
        self.assertEqual([q["hit"] for q in r1["per_query"]],
                         [q["hit"] for q in r2["per_query"]])

    def test_limit_param(self):
        from benchmarks.locomeval_eval import run_eval
        report = run_eval(k=5, seed=42, limit=4)
        self.assertEqual(report["n_questions"], 4)
        self.assertEqual(len(report["per_query"]), 4)

    def test_keyword_judge_unit(self):
        from benchmarks.locomeval_eval import _keyword_hit
        self.assertTrue(_keyword_hit("用户对花生严重过敏", ["花生"]))
        self.assertFalse(_keyword_hit("今天天气不错", ["花生"]))
        self.assertTrue(_keyword_hit("东京都港区", ["东京", "港区"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
