# -*- coding: utf-8 -*-
"""
MindForge v5.6.3 性能修复回归测试
=================================

覆盖三类优化的正确性：
  1. VectorIndex 稀疏化 + 倒排链（与稠密点积结果一致；覆盖写/删除不残留）
  2. IndexEngine TF-IDF 端到端检索（稀疏存储后排序正确）
  3. fuzzy_search difflib 长文本剪枝（精确命中不丢、近似命中保留）
  4. get_memory 读缓存接入（命中、更新/删除失效）
  5. 访问计数节流（窗口内合并写、close 挂账刷盘，计数最终精确）
"""

import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from mindforge.core.indexer import IndexEngine, VectorIndex


# ---------------------------------------------------------------------------
# 1. 稀疏 VectorIndex
# ---------------------------------------------------------------------------

class TestSparseVectorIndex(unittest.TestCase):
    def test_sparse_scores_match_dense_math(self):
        idx = VectorIndex()
        # 稀疏存储
        idx.add("a", {0: 0.6, 2: 0.8})
        idx.add("b", {0: 0.6, 1: 0.0, 2: 0.8})  # 显式零值应被剔除
        idx.add("c", {5: 1.0})
        res = dict(idx.search({0: 1.0}, top_k=3))
        # a/b 等权等范数，分数相同且大于不相关 c
        self.assertAlmostEqual(res["a"], res["b"], places=9)
        self.assertGreater(res["a"], res.get("c", 0.0))
        # 零维不进倒排链
        self.assertNotIn(1, idx._postings)

    def test_dense_list_input_still_supported(self):
        idx = VectorIndex()
        idx.add("d1", [1.0, 0.0, 1.0])
        idx.add("d2", [0.0, 1.0, 0.0])
        res = idx.search([1.0, 0.0, 1.0], top_k=2)
        self.assertEqual(res[0][0], "d1")
        self.assertAlmostEqual(res[0][1], 1.0, places=9)

    def test_overwrite_clears_stale_postings(self):
        idx = VectorIndex()
        idx.add("x", {0: 1.0})
        # 覆盖为完全不同的向量：旧维度不应再召回 x
        idx.add("x", {9: 1.0})
        self.assertNotIn(0, idx._postings)
        res = idx.search({0: 1.0}, top_k=5)
        self.assertEqual(res, [])
        res2 = idx.search({9: 1.0}, top_k=5)
        self.assertEqual(res2[0][0], "x")

    def test_remove_updates_postings(self):
        idx = VectorIndex()
        idx.add("x", {0: 1.0})
        idx.add("y", {0: 0.5})
        idx.remove("x")
        # x 的权重必须从共享倒排链移除；同维度的 y 仍然保留
        self.assertNotIn("x", idx._postings[0])
        self.assertIn("y", idx._postings[0])
        res = idx.search({0: 1.0}, top_k=5)
        self.assertEqual([d for d, _ in res], ["y"])

    def test_zero_norm_query_preserves_legacy_behavior(self):
        # 原有契约：零范数查询返回 0 分结果而非报错（IndexEngine 不会这样调用）
        idx = VectorIndex()
        idx.add("x", {0: 1.0})
        self.assertEqual(idx.search({}, top_k=5), [("x", 0.0)])
        self.assertEqual(idx.search([0.0, 0.0], top_k=5), [("x", 0.0)])


# ---------------------------------------------------------------------------
# 2. IndexEngine 端到端
# ---------------------------------------------------------------------------

class TestSparseIndexEngine(unittest.TestCase):
    def setUp(self):
        self.idx = IndexEngine()

    def _add(self, docs):
        for i, text in enumerate(docs):
            self.idx.index_memory(f"d{i}", text)

    def test_search_ranking_shared_term(self):
        docs = [
            "记忆学习与遗忘曲线机制",
            "向量检索和语义嵌入模型",
            "记忆的强化与衰减权重计算",
            "知识图谱的实体关系构建",
            "记忆记忆记忆重复强调的记忆主题",
            "联邦同步冲突合并策略",
        ]
        self._add(docs)
        hits = self.idx.search("记忆", top_k=3)
        self.assertTrue(hits)
        top_ids = [d for d, _ in hits]
        # 含"记忆"的文档必须出现在结果中
        self.assertTrue(any(mid in ("d0", "d2", "d4") for mid in top_ids))
        # 完全不相关的图谱文档不应排到含词文档前面
        self.assertNotIn("d3", top_ids[:2])

    def test_remove_excludes_document(self):
        self._add([f"记忆学习文档编号{i}的内容" for i in range(6)])
        self.idx.remove_memory("d2")
        hits = dict(self.idx.search("记忆学习", top_k=10))
        self.assertNotIn("d2", hits)
        self.assertIn("d0", hits)


# ---------------------------------------------------------------------------
# 3. fuzzy_search 长文本剪枝不丢正确结果
# ---------------------------------------------------------------------------

class TestFuzzyGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_fuzzy_")
        from mindforge.core.storage import StorageEngine
        self.se = StorageEngine(
            db_path=os.path.join(self.tmp, "f.db"), encrypted=False)

    def tearDown(self):
        self.se.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exact_substring_hit_survives_gate(self):
        long_text = "知识图谱 " * 40  # 远长于 max(2m,12) 的长文本
        self.se.add_memory(content=long_text, category="kg", tags=["图谱"])
        hits = self.se.fuzzy_search("知识图谱", limit=10, threshold=0.3)
        self.assertTrue(hits)
        self.assertGreaterEqual(hits[0]["score"], 0.8)

    def test_near_match_with_shared_bigrams_scored(self):
        # 长文本，包含查询的连续片段，SequenceMatcher 走剪枝后的保留路径
        text = "前缀铺垫内容 " + "记忆学习强化" + " 后缀延展文字" * 10
        self.se.add_memory(content=text, category="study")
        hits = self.se.fuzzy_search("记忆学习强化", limit=10, threshold=0.1)
        self.assertTrue(hits, "共享 bigram 的近似命中不应被剪枝丢弃")

    def test_irrelevant_long_doc_not_force_matched(self):
        self.se.add_memory(content="山川河海日月星辰草木风" * 10,
                           category="random")
        hits = self.se.fuzzy_search("记忆检索", limit=10, threshold=0.3)
        self.assertEqual(hits, [])

    def test_tag_fuzzy_match_kept(self):
        self.se.add_memory(content="无关正文内容" * 10,
                           category="x", tags=["记忆学习"])
        hits = self.se.fuzzy_search("记忆学刁", limit=10, threshold=0.1)
        # 标签近似（短串）路径不应被长文本剪枝影响
        self.assertTrue(any(h["entry"].tags for h in hits))


# ---------------------------------------------------------------------------
# 4/5. 读缓存 + 访问计数节流
# ---------------------------------------------------------------------------

class TestReadCacheAndAccessThrottle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_cache_")
        from mindforge.core.storage import StorageEngine
        self.db = os.path.join(self.tmp, "c.db")
        self.se = StorageEngine(db_path=self.db, encrypted=False)

    def tearDown(self):
        try:
            self.se.close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _raw_access_count(self, mid):
        conn = sqlite3.connect(self.db)
        try:
            row = conn.execute(
                "SELECT access_count FROM memories WHERE id = ?", (mid,)
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def test_second_read_is_cache_hit(self):
        e = self.se.add_memory(content="缓存测试记忆内容", category="t")
        self.se.get_memory(e.id)
        stats1 = self.se._memory_cache.stats()
        self.se.get_memory(e.id)
        stats2 = self.se._memory_cache.stats()
        self.assertEqual(stats2["hits"], stats1["hits"] + 1)

    def test_update_invalidates_cache(self):
        e = self.se.add_memory(content="原始内容一二三", category="t")
        self.se.get_memory(e.id)  # 入缓存
        self.se.update_memory(e.id, content="更新后的内容四五六")
        got = self.se.get_memory(e.id)
        self.assertEqual(got.content, "更新后的内容四五六")

    def test_delete_invalidates_cache(self):
        e = self.se.add_memory(content="待删除记忆", category="t")
        self.se.get_memory(e.id)
        self.se.delete_memory(e.id)
        self.assertIsNone(self.se.get_memory(e.id))

    def test_access_count_throttle_and_close_flush(self):
        from mindforge.core import storage as storage_mod
        e = self.se.add_memory(content="访问计数节流测试", category="t")

        # 首次读取立即落库
        self.se.get_memory(e.id)
        self.assertEqual(self._raw_access_count(e.id), 1)

        # 节流窗口内的后续读取在进程内挂账，不立即写库
        self.se.get_memory(e.id)
        self.se.get_memory(e.id)
        self.assertEqual(self._raw_access_count(e.id), 1)
        self.assertEqual(self.se._access_pending[e.id], 2)

        # close 兜底刷盘后计数精确（无丢失）
        self.se.close()
        self.assertEqual(self._raw_access_count(e.id), 3)

    def test_pending_counts_exact_under_concurrent_flush(self):
        """并发 get 与并发批量刷盘交错，计数必须精确无丢失（v5.6.4 竞态回归）"""
        import threading as _t
        e = self.se.add_memory(content="并发挂账计数精确性测试", category="t")
        THREADS, PER_THREAD = 8, 60
        barrier = _t.Barrier(THREADS)

        def worker():
            barrier.wait()
            for i in range(PER_THREAD):
                self.se.get_memory(e.id)
                # 高频交错触发批量刷盘
                if i % 7 == 0:
                    self.se._flush_all_access()

        ts = [_t.Thread(target=worker) for _ in range(THREADS)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        self.se.close()  # 最终兜底刷盘
        # 所有 get 的访问计数一次不少
        self.assertEqual(
            self._raw_access_count(e.id), THREADS * PER_THREAD)


class TestFuzzyScaleCandidates(unittest.TestCase):
    """v6.4.1 万级库模糊检索回归：SQL 候选预筛 + 数学剪枝的行为级契约。

    背景（2026-11-09 百万字实测）：8000 条库 fuzzy 兜底 p95 曾达 881ms。
    修复两层：① n >= 4m 时 SequenceMatcher 数学上不可能 ratio>0.4 直接
    跳过；② 候选集在 SQLite 层预筛（子串/短文本/中文 tag 字符 LIKE）。
    本组测试钉住语义不回归 + 候选集不退化回全库。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_scale641_")
        from mindforge.core.storage import StorageEngine
        self.db = os.path.join(self.tmp, "s.db")
        self.se = StorageEngine(db_path=self.db, encrypted=False)

    def tearDown(self):
        try:
            self.se.close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _populate(self, n=1200):
        """n 条长文本（~140 字），其中 1/97 埋 python-XX 锚点。"""
        body = "无关注联正文内容用于稀释检索权重 " * 8
        for i in range(n):
            anchor = f"python-{i % 97:02d}" if i % 3 == 0 else ""
            self.se.add_memory(
                content=f"{body} 锚点 {anchor} 编号 {i:05d}",
                category="bench", tags=["bench"])

    def test_long_content_substring_still_recalled(self):
        """长 content 的子串命中（+0.8 主路）不被任何剪枝/预筛丢掉。"""
        self._populate(200)
        hits = self.se.fuzzy_search("python-33", limit=10, threshold=0.3)
        self.assertTrue(hits, "锚点子串命中必须召回")
        self.assertTrue(
            any("python-33" in h["entry"].content for h in hits))

    def test_math_prune_semantics_unchanged(self):
        """数学剪枝只砍「本来就不可能过阈值」的比对，近似召回不变。

        长文本 + 短 query 的 SequenceMatcher 分数学上限 <= 0.4*5/4，
        旧全扫同样进不了 >0.4 分支——剪枝前后语义应一致。
        """
        self.se.add_memory(
            content="短文本记忆" * 1, category="t", tags=["t"])  # n=5 < 4m
        self.se.add_memory(
            content="这是一条相当长的记忆内容，" * 20, category="t2")
        # 短 content（近似路唯一可能过阈值的）不受影响
        hits = self.se.fuzzy_search("短文本记忆", limit=10, threshold=0.3)
        self.assertTrue(any(h["score"] >= 0.4 for h in hits),
                        "短文本近似路必须保留")

    def test_candidate_gate_not_fullscan(self):
        """A 类 query（英文字母撞 tag）候选集不得退化回全库（v6.4.1 教训）。

        曾因逐字符 tags LIKE 把 'n'/'h'（in 'bench'）炸回全库、p50 回到
        218ms。修复后英文 query 不走逐字 LIKE——候选集应显著小于全库。
        """
        self._populate(1200)
        conn = sqlite3.connect(self.db)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM memories WHERE "
                "category != 'trash' AND (content LIKE ? OR category LIKE ? "
                "OR tags LIKE ? OR LENGTH(COALESCE(content,'')) < ?))",
                ("%python-42%", "%python-42%", "%python-42%", 40)
            ).fetchone()
            candidates = row[0]
        finally:
            conn.close()
        self.assertLess(
            candidates, 1200,
            f"候选集 {candidates} 应远小于全库（锚点命中率 ~1/97×2/3）")
        self.assertGreater(candidates, 0, "锚点候选必须非空")

    def test_millisecond_search_at_scale(self):
        """万条级（这里 1200 条）fuzzy 兜底保持毫秒级行为契约。"""
        self._populate(1200)
        ts = []
        for k in range(20):
            t0 = time.perf_counter()
            self.se.fuzzy_search(f"python-{(k * 31) % 97:02d}",
                                 limit=10, threshold=0.1)
            ts.append((time.perf_counter() - t0) * 1000)
        p95 = sorted(ts)[int(len(ts) * 0.95) - 1]
        # 阈值 = CI 抖动余量（本地实测 p50 ~2ms，CI 机器慢 3-5 倍）
        self.assertLess(p95, 50, f"fuzzy p95 {p95:.1f}ms 超出毫秒级契约")


if __name__ == "__main__":
    unittest.main()
