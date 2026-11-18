# -*- coding: utf-8 -*-
"""v6.7.0 mindforge.bench 契约测试。

守卫基准的评测口径不被破坏：
- deterministic 判官双模式（must_have / gold 子串 fallback）
- LoCoMo 类别映射（与 v6.5.12 全量报告类别分布逐项对齐）
- runner 三段式管道端到端（零成本链路）
- 评测防泄漏：answerer 不得接触 gold_answer
- 环境声明与报告结构
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindforge.bench.datasets import (
    load_dataset, load_locomo, LOCOMO_CATEGORY_MAP,
    SMOKE_SESSIONS, SMOKE_QUESTIONS)
from mindforge.bench.backends import (
    MindForgeBackend, ContextWindowBackend, BACKEND_REGISTRY)
from mindforge.bench.judges import DeterministicJudge
from mindforge.bench.answerers import ExtractiveAnswerer
from mindforge.bench.metrics import aggregate, percentile
from mindforge.bench.runner import run_benchmark
from mindforge.bench.report import aggregate_markdown
from mindforge.bench.cli import main as cli_main


class TestSmokeDataset(unittest.TestCase):

    def test_schema_and_counts(self):
        items = load_dataset("smoke")
        self.assertEqual(len(items), len(SMOKE_QUESTIONS))
        self.assertEqual(len(SMOKE_SESSIONS), 12)
        for it in items:
            for key in ("qid", "question", "must_have", "category",
                        "source_turns", "meta"):
                self.assertIn(key, it, f"schema 缺 {key}")
            self.assertIn(it["category"],
                          ("singlehop", "multihop", "temporal"))
            self.assertTrue(it["must_have"], "smoke 必须带 must_have")

    def test_unknown_dataset_rejected(self):
        with self.assertRaises(ValueError):
            load_dataset("不存在")


class TestLocomoMapper(unittest.TestCase):

    def test_category_map_aligns_with_full_report(self):
        # 与 v6.5.12 全量报告类别分布逐项对齐的映射
        self.assertEqual(LOCOMO_CATEGORY_MAP,
                         {1: "temporal", 2: "multihop",
                          3: "open_domain", 4: "singlehop",
                          5: "adversarial"})

    def test_load_locomo_real_file(self):
        data = "/home/z/my-project/tmp/locomo/locomo-main/data/locomo10.json"
        if not os.path.isfile(data):
            self.skipTest("本地无 LoCoMo 数据")
        items = load_locomo(data, convs=1)
        self.assertEqual(len(items), 199)  # conv-26 实测问数
        conv0 = items[0]["meta"]["conv_id"]
        for it in items:
            self.assertIn(it["category"],
                          ("singlehop", "multihop", "temporal",
                           "adversarial", "open_domain"))
            self.assertEqual(it["meta"]["conv_id"], conv0)
            self.assertTrue(it["source_turns"], "必须有入库轮")

    def test_locomo_requires_data_path(self):
        with self.assertRaises(FileNotFoundError):
            load_dataset("locomo")


class TestDeterministicJudge(unittest.TestCase):

    def setUp(self):
        self.j = DeterministicJudge()

    def test_must_have_mode(self):
        it = {"question": "q", "must_have": ["花生"], "gold_answer": ""}
        self.assertEqual(self.j.score(it, "用户对花生过敏", [])["score"], 10)
        self.assertEqual(self.j.score(it, "用户对大豆过敏", [])["score"], 1)

    def test_gold_substring_mode(self):
        # must_have 为空 → gold 子串 fallback（LoCoMo QA 式）
        it = {"question": "q", "must_have": [], "gold_answer": "Sommelier"}
        self.assertEqual(self.j.score(it, "he is a sommelier", [])["score"], 10)
        self.assertEqual(self.j.score(it, "a chef", [])["score"], 1)

    def test_empty_answer_scores_one(self):
        it = {"question": "q", "must_have": ["x"], "gold_answer": ""}
        self.assertEqual(self.j.score(it, "", [])["score"], 1)


class TestMetrics(unittest.TestCase):

    def test_percentile(self):
        self.assertEqual(percentile([], 50), 0.0)
        self.assertEqual(percentile([3.0], 50), 3.0)
        self.assertEqual(percentile(sorted([1.0, 2.0, 3.0, 4.0]), 50), 2.5)
        self.assertEqual(percentile(sorted([1.0, 2.0, 3.0, 4.0]), 95), 3.85)

    def test_aggregate_counts(self):
        recs = [{"score": 10, "correct": True, "latency_ms": 1.0,
                 "retrieved": True, "first_hit_rank": 1, "category": "a"},
                {"score": 1, "correct": False, "latency_ms": 3.0,
                 "retrieved": False, "first_hit_rank": 0, "category": "a"}]
        agg = aggregate(recs)
        self.assertEqual(agg["count"], 2)
        self.assertEqual(agg["acc"], 50.0)
        self.assertEqual(agg["avg_score"], 5.5)
        self.assertEqual(agg["recall_at_k"], 50.0)
        self.assertAlmostEqual(agg["mrr_at_k"], 0.5)
        self.assertEqual(agg["latency_ms"]["p50"], 2.0)

    def test_aggregate_empty(self):
        self.assertEqual(aggregate([])["count"], 0)


class TestRunnerEndToEnd(unittest.TestCase):
    """零成本链路端到端：smoke + mindforge + extractive + deterministic。"""

    def test_full_pipeline(self):
        items = load_dataset("smoke")
        backend = MindForgeBackend()
        report = run_benchmark(
            items, backend, ExtractiveAnswerer(), DeterministicJudge(),
            k=5, seed=42, dataset_id="smoke",
            args_decl={"backend": "mindforge", "answerer": "extractive",
                       "judge": "deterministic", "k": 5, "seed": 42})
        backend.close()
        agg = report["aggregate"]
        self.assertEqual(agg["count"], len(items))
        self.assertIn("acc", agg)
        self.assertIn("env", report)
        self.assertEqual(report["env"]["dataset"], "smoke")
        self.assertEqual(report["env"]["backend"], "mindforge")
        # 环境声明可 JSON 序列化（报告落盘的前提）
        json.dumps(report["env"])

    def test_per_conv_isolation(self):
        # 不同 conv_id 分组 → 独立记忆库（不串记忆）
        items = load_dataset("smoke")
        items = [dict(it, meta=dict(it["meta"], conv_id=it["qid"]))
                 for it in items]
        backend = ContextWindowBackend()
        report = run_benchmark(items, backend, ExtractiveAnswerer(),
                               DeterministicJudge(), k=5, dataset_id="smoke")
        backend.close()
        # 每问独立库（1 轮），检索层命中率应大幅下降——证明分组生效
        retrieved = [r["retrieved"] for r in report["records"]]
        self.assertFalse(all(retrieved), "串库则全部命中")

    def test_baseline_underperforms_mindforge(self):
        # context_window 基线 acc 不得高于 mindforge（增量价值守卫）
        items = load_dataset("smoke")
        accs = {}
        for name in ("mindforge", "context_window"):
            backend = BACKEND_REGISTRY[name]()
            rep = run_benchmark(items, backend, ExtractiveAnswerer(),
                                DeterministicJudge(), k=5, dataset_id="smoke")
            backend.close()
            accs[name] = rep["aggregate"]["acc"]
        self.assertGreater(accs["mindforge"], accs["context_window"],
                           f"记忆系统必须优于裸上下文基线: {accs}")


class TestLeakGuard(unittest.TestCase):
    """评测防泄漏：answerer 接口上不存在接触 gold 的通道。"""

    def test_answerer_signature_has_no_gold(self):
        import inspect
        sig = inspect.signature(ExtractiveAnswerer.answer)
        self.assertEqual(list(sig.parameters), ["self", "question", "chunks"])


class TestReportAndCLI(unittest.TestCase):

    def test_aggregate_markdown(self):
        items = load_dataset("smoke")
        backend = ContextWindowBackend()
        rep = run_benchmark(items[:4], backend, ExtractiveAnswerer(),
                            DeterministicJudge(), k=2, dataset_id="smoke")
        backend.close()
        path = "/tmp/bench_test_run.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rep, f)
        md = aggregate_markdown([path])
        self.assertIn("| 指标 |", md)
        self.assertIn("acc%", md)
        self.assertIn("## 环境声明", md)

    def test_cli_report_cmd(self):
        rc = cli_main(["report", "/tmp/bench_test_run.json"])
        self.assertEqual(rc, 0)

    def test_cli_run_smoke_json(self):
        out = "/tmp/bench_test_cli.json"
        rc = cli_main(["run", "--json", out, "--k", "2"])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(d["aggregate"]["count"], 12)

    def test_cli_unknown_subcmd_exit_2(self):
        rc = cli_main(["不存在"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
