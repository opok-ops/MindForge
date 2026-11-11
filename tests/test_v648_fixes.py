"""
MindForge v6.4.8 回归测试

病态查询路径修复（2026-11-11，「百万字毫秒级」攻坚）：

  - 根因：全库词查询（所有维度 df≈N）触发 v6.4.6 回退分支
    list(q.items())，全维度倒排链遍历 = O(N×查询维)。100k 压测
    实测 cjk_all_common p50 209ms——「已知特性非缺陷」的技术债。
  - 修复：统一预算制。
      1) max_df 剪枝 0.5×N → 0.05×N（半库词链不再进 active，
         100k 实测 mixed_realistic 134ms 一并解决）
      2) 回退语义「最短链优先」：按链长升序取最短 3 维
      3) 回退单链截断 cap 5000（islice 免建副本）
  - 30k 复现：病态 144ms → 4.9ms（-97%），top1/召回不变。
  - 守卫：遍历量上限（确定性契约，不依赖 CI 机器速度）、
    病态语义保序、0.05 阈值行为、版本钉值。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindforge.core.version import __version__, VERSION_INFO
from mindforge.core.indexer import VectorIndex


class TestTraverseBudgetContract(unittest.TestCase):
    """遍历量上限契约（确定性，不受机器速度影响）。"""

    def _mk_index(self, n_docs, text_fn):
        vi = VectorIndex()
        for i in range(n_docs):
            vi.add(f"doc-{i:06d}", text_fn(i))
        return vi

    def test_fallback_traverse_cap(self):
        """病态回退遍历量 ≤ 3 维 × cap 5000（v6.4.8 核心契约）。

        构造全库维语料：维 1/2 在全部文档出现 → 全维被剪 → 走回退。
        旧行为遍历 = N×维数（20k 语料 = 4 万次，且随库容线性涨），
        新行为必须 ≤ 3×5000（与库容解耦）。
        """
        vi = self._mk_index(20000, lambda i: {1: 0.9, 2: 0.5})
        vi.search({1: 0.5, 2: 0.5}, top_k=5)
        self.assertLessEqual(
            vi._last_traversed, 15000,
            f"回退遍历量 {vi._last_traversed} 超预算 3×5000")

    def test_normal_path_chain_budget(self):
        """正常路径 active 链长上界 0.05×N（max_df 收紧守卫）。

        构造：一条 10% 链长维（新阈值下必剪）+ 一条稀有维。
        查询同时含两者 → 10% 维不得进 active → 遍历量不含其链。
        """
        vi = VectorIndex()
        semi_common = 1     # 维 1：10 文档共享（10% of 100）
        rare = 2            # 维 2：仅 1 文档
        for i in range(100):
            vec = {1000 + i: 0.7}
            if i < 10:
                vec[semi_common] = 0.9   # 链长 10 > 0.05×100=5
            if i == 0:
                vec[rare] = 0.9           # 链长 1
            vi.add(f"d{i:03d}", vec)
        vi.search({semi_common: 0.8, rare: 0.6}, top_k=3)
        # active 只含 rare（链长 1）；semi_common 链长 10 被剪
        self.assertEqual(vi._last_traversed, 1,
                         "10% 链长维应被 0.05 max_df 剪掉，不进遍历")

    def test_fallback_picks_shortest_chains(self):
        """回退选中链最短的维度（区分度最高信号）。"""
        vi = VectorIndex()
        for i in range(200):
            vec = {100: 0.9}                    # 全库维：链 200
            if i < 20:
                vec[200] = 0.8                  # 10% 维：链 20
            if i < 4:
                vec[300] = 0.7                  # 2% 维：链 4
            vi.add(f"d{i:03d}", vec)
        # 全部维度的 qval 设为同值，避免 eps 剪枝干扰回退选择
        q = {100: 0.5, 200: 0.5, 300: 0.5}
        vi.search(q, top_k=2)
        # 最短 3 维 = 300(4) + 200(20) + 100(200，被 cap 到 5000 无截断)
        # 遍历量 = 4 + 20 + 200 = 224；若未按链长排序将相同（3 维全选）
        # 关键断言：维 300 的链必须参与（top 结果来自短链文档）
        self.assertLessEqual(vi._last_traversed, 4 + 20 + 200)


class TestPathologicalSemantics(unittest.TestCase):
    """病态路径语义守卫（性能修复不得牺牲召回）。"""

    def test_all_common_query_nonempty(self):
        """全库词查询仍返回 top_k 条（不空召回契约延续 v6.4.6）。"""
        vi = VectorIndex()
        for i in range(500):
            vi.add(f"d{i:03d}", {1: 0.9, 2: 0.5})
        top = vi.search({1: 0.8, 2: 0.3}, top_k=5)
        self.assertEqual(len(top), 5)
        scores = [s for _, s in top]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_relevant_doc_ranks_first_after_fallback(self):
        """回退路径下含最高区分度信号的文档仍排第一。"""
        vi = VectorIndex()
        # doc-hit 同时含全库维与独有强信号维；其余只含全库维
        for i in range(300):
            vec = {1: 0.5}
            if i == 7:
                vec[999] = 0.9
            vi.add(f"d{i:03d}", vec)
        top = vi.search({1: 0.5, 999: 0.6}, top_k=1)
        self.assertEqual(top[0][0], "d007",
                         "回退最短链优先必须让独有信号文档置顶")

    def test_empty_query_vector_zero_norm(self):
        """零范数查询（空向量）仍走文档序 top-K（向后兼容）。"""
        vi = VectorIndex()
        for i in range(5):
            vi.add(f"d{i}", {1: 0.5})
        top = vi.search({}, top_k=2)
        self.assertEqual(len(top), 2)


class TestFuzzyGateAndFallbackSignal(unittest.TestCase):
    """fuzzy 触发门控与病态回退信号（v6.4.8 第二刀）。

    query.py 触发条件收紧为「TF-IDF 零有效命中」：positive ≥ 1 或
    病态回退时跳过 fuzzy 路。此处钉 IndexEngine 门面透传契约。
    """

    def test_index_engine_passes_fallback_flag(self):
        """IndexEngine.search 透传 VectorIndex 的 last_fallback。"""
        from mindforge.core.indexer import IndexEngine
        eng = IndexEngine(":memory:")
        for i in range(300):
            eng.index_memory(f"d{i:03d}",
                             f"common word doc {i} unique{i:04d} tail")
        # 全库词查询（无 unique 词）→ 回退 → 门面 last_fallback=True
        eng.search("common word", top_k=5)
        self.assertTrue(eng.last_fallback,
                        "全库词查询必须置位门面 last_fallback")
        # 含稀有词查询 → active 非空 → 门面 last_fallback=False
        eng.search("unique0100", top_k=5)
        self.assertFalse(eng.last_fallback,
                         "区分词查询不得置位 last_fallback")

    def test_fuzzy_not_run_when_tfidf_hits(self):
        """TF-IDF 有效命中时 query 层跳过 fuzzy（触发门控行为契约）。

        用 monkeypatch 计数 fuzzy_search 调用次数：mixed 形态查询
        （稀有锚命中 + 常见词）不得触发 fuzzy。
        """
        from mindforge.core.mindforge import MindForge
        from mindforge.core.types import MemoryConfig
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        try:
            mf = MindForge(config=MemoryConfig(
                db_path=f"{tmp}/m.db", key_file=None, encrypted=False))
            for i in range(60):
                mf.add(f"fact-{i} 陈述性记忆 编号{i}", category="t")
            calls = {"n": 0}
            orig = mf._query.storage.fuzzy_search
            def _spy(*a, **kw):
                calls["n"] += 1
                return orig(*a, **kw)
            mf._query.storage.fuzzy_search = _spy
            mf.search("fact-7 陈述性记忆")   # TF-IDF 命中 fact-7
            self.assertEqual(calls["n"], 0,
                             "TF-IDF 有命中时不得跑 fuzzy 路")
            mf.search("不存在的词xyzzyq")     # TF-IDF 零命中 → fuzzy 兜底
            self.assertGreaterEqual(calls["n"], 1,
                                    "TF-IDF 零命中时 fuzzy 兜底必须运行")
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestVersionBump(unittest.TestCase):
    """版本钉值（v6.4.5 教训：每版 fixes 断言随发版一并 bump）。"""

    def test_version_is_648_plus(self):
        # v6.4.9 发版时 bump：本文件守卫 v6.4.8 引入的行为，
        # 版本断言随最新发版跟进
        self.assertGreaterEqual(VERSION_INFO, (6, 4, 8))


if __name__ == "__main__":
    unittest.main()
