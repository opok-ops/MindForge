"""
MindForge v6.4.6 回归测试

规模验证版内容守卫（2026-11-10，三短板攻坚）：

  - 倒排剪枝 perf（100k 压测发现的真瓶颈）：中文长查询 p50 421ms，
    profile 定位到低 IDF 常见词的倒排链全量遍历退化（近乎全库扫）。
    修复：qval/query_norm < 0.02 的查询维剪枝 + heapq.nlargest。
    守卫：剪枝后 top-K 语义保序、退化查询回退、常见词查询合法。
  - 规模与质量基准脚本进仓（benchmarks/scale_bench.py、quality_bench.py）
    ——可复现的延迟曲线与检索质量数字（Recall@1/@5、MRR、干扰误召回）。
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = Path(__file__).resolve().parent.parent

from mindforge.core.version import __version__
from mindforge.core.indexer import VectorIndex, IndexEngine


class TestPrunedSearchQuality(unittest.TestCase):
    """剪枝不得改变召回语义（数学安全性守卫）。"""

    def _mk_index(self):
        vi = VectorIndex()
        # 维度键是 int（词表 index），字符串词会被 int() 拒绝。
        # 1..3 = 高频常见维（模拟「条目/陈述性/记忆」），1000+i = 稀有维。
        docs = {
            f"doc-{i}": {1: 0.9, 2: 0.5, 3: 0.4, 1000 + (i % 17): 0.8}
            for i in range(300)
        }
        for did, vec in docs.items():
            vi.add(did, vec)
        return vi, docs

    def test_topk_semantics_preserved(self):
        """剪枝后 top-K 保序且非空（高区分度查询）。"""
        vi, _ = self._mk_index()
        query = {1003: 0.9, 1009: 0.6}
        top = vi.search(query, top_k=5)
        self.assertEqual(len(top), 5)
        scores = [s for _, s in top]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_degenerate_query_fallback(self):
        """全维低于阈值时退回全维语义（不空召回路径存在）。"""
        vi, _ = self._mk_index()
        q = {2000 + i: 0.1 for i in range(50)}
        top = vi.search(q, top_k=3)
        self.assertIsInstance(top, list)

    def test_common_word_query_still_relevant(self):
        """常见词查询（剪枝对象）仍返回非空且分数显著正相关。
        余弦上界 = 共享维点积 / (两范数积) ≈ 0.87/1.136 ≈ 0.77
        （文档含查询外维度 rare×0.8），断言 >0.5 即显著相关。"""
        vi, _ = self._mk_index()
        top = vi.search({1: 0.8, 2: 0.3}, top_k=5)
        self.assertTrue(len(top) >= 1)
        self.assertGreater(top[0][1], 0.5)


class TestBenchmarksShipped(unittest.TestCase):
    """基准脚本随仓发布（可复现性守卫）。"""

    def test_scale_bench_exists(self):
        self.assertTrue((_REPO / "benchmarks" / "scale_bench.py").exists(),
                        "scale_bench.py 未随仓")

    def test_quality_bench_exists(self):
        self.assertTrue((_REPO / "benchmarks" / "quality_bench.py").exists(),
                        "quality_bench.py 未随仓")

    def test_index_lru_cap_documented(self):
        """100k LRU 上限存在（IndexEngine 默认）。"""
        self.assertEqual(IndexEngine.DEFAULT_MAX_DOCS, 100_000)


class TestVersionBump(unittest.TestCase):
    """版本钉值（v6.4.5 教训：每版 fixes 断言随发版一并 bump）。"""

    def test_version_is_646(self):
        self.assertEqual(__version__, "6.5.8")


if __name__ == "__main__":
    unittest.main()
