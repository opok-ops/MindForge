# -*- coding: utf-8 -*-
"""v6.6.2 检索链路修复契约测试

对应 .zwork_v662_search.md 主题 B 的 8 项修复（只涉及 indexer.py / query.py）：
- B-1 (P1)：IndexEngine 一把 threading.RLock 串行化内存结构读写——
  并发 fit×index 不再 RuntimeError，倒排链无幽灵权重
- B-2 (P2)：TFIDFVectorizer df/doc_count 漂移——同 doc_id 覆盖写先按
  旧文本回退（_retract_doc），remove_memory 同步回收
- B-3 (P2)：IndexEngine.invalidate_fts_vocab_cache() 失活钩子（UPDATE
  后剪枝缓存失效；调用点由 mindforge.update_memory 由同事接线）
- B-4 (P2)：graph 路 weight=0 边得 0 分，不再被 (w or 1.0) 反转成最强
- B-5 (P2)：temporal 覆盖同步更新 winner/final；仅 s > existing 才覆盖
- B-6 (P2)：expand_context 邻居加密行走 decrypt_content 解密回填，
  解密失败跳过该邻居
- B-7 (P2)：_normalize_temporal 中文 X十 分支（二十天前=20 天）
- B-10.1 (P3)：_CJK_GRAM_MAX 超限改均匀采样（首尾 gram 必选）
"""
import math
import os
import shutil
import tempfile
import threading
import unittest

from mindforge.core.indexer import IndexEngine
from mindforge.core.query import QueryEngine, _normalize_temporal  # noqa: F401
from mindforge.core.mindforge import MindForge


def _make_mf(tmp, encrypted=True):
    mf = MindForge(db_path=os.path.join(tmp, "m.db"),
                   key_file=os.path.join(tmp, ".key"),
                   encrypted=encrypted)
    if encrypted:
        mf.init_with_password("pw-v662")
    return mf


class TestB1IndexLock(unittest.TestCase):
    """B-1：并发 search×index / fit×index 不再 RuntimeError"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662b1_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_two_threads(self, searcher, indexer):
        errors = []
        stop = threading.Event()
        done = {"indexer": False}

        def _searcher():
            try:
                while not stop.is_set():
                    searcher()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                stop.set()

        def _indexer():
            try:
                indexer()
                done["indexer"] = True
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                stop.set()

        t1 = threading.Thread(target=_searcher)
        t2 = threading.Thread(target=_indexer)
        t1.start()
        t2.start()
        t2.join(timeout=120)
        stop.set()
        t1.join(timeout=120)
        return errors, done

    def test_concurrent_fit_and_index_no_error(self):
        """hydrate 后未 fit：search 触发 _fit_vectorizer 长迭代
        _doc_texts，与并发 index_memory 插入不再交错崩溃"""
        eng = IndexEngine(db_path=os.path.join(self.tmp, "fit.db"))
        eng.hydrate({("h%d" % i): "hydrate alpha beta %d content" % i
                     for i in range(800)})

        errors, done = self._run_two_threads(
            lambda: eng.search("alpha beta"),
            lambda: [eng.index_memory("n%d" % i, "gamma delta new %d" % i)
                     for i in range(400)])
        self.assertEqual(errors, [])
        self.assertTrue(done["indexer"], "index 线程未在超时内完成")

    def test_concurrent_search_and_index_chain_consistent(self):
        """已 fit 状态：search 遍历倒排链 × index/remove 改链不崩，
        结束后链上无幽灵 doc（残留已删文档权重）"""
        eng = IndexEngine(db_path=os.path.join(self.tmp, "run.db"))
        for i in range(50):
            eng.index_memory("seed%d" % i, "alpha beta gamma seed %d" % i)
        eng.search("alpha beta")  # 完成首轮 fit

        def _indexer():
            for i in range(300):
                eng.index_memory("doc%d" % i, "delta epsilon zeta %d" % i)
                if i % 3 == 0:
                    eng.remove_memory("seed%d" % (i % 50))

        errors, done = self._run_two_threads(
            lambda: eng.search("alpha beta gamma"), _indexer)
        self.assertEqual(errors, [])
        self.assertTrue(done["indexer"], "index 线程未在超时内完成")
        vi = eng.vector_index
        for chain in vi._postings.values():
            for doc_id in chain:
                self.assertIn(doc_id, vi.vectors,
                              "倒排链残留已删除 doc（幽灵权重）")


class TestB2DfDocCountDrift(unittest.TestCase):
    """B-2：覆盖写先回退旧文本计数；remove_memory 回收 df/doc_count"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662b2_")
        self.eng = IndexEngine(db_path=os.path.join(self.tmp, "m.db"))
        for i in range(5):
            self.eng.index_memory("d%d" % i, "alpha beta doc %d" % i)
        self.vec = self.eng.vectorizer
        # 基线：fit 5 篇，alpha/beta/doc 各 df=5
        self.assertEqual(self.vec.doc_count, 5)
        self.assertEqual(self.vec._df["alpha"], 5)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reindex_same_doc_no_drift(self):
        """update 路径：同 doc_id 覆盖写不再 +doc_count / df 漂移"""
        self.eng.index_memory("d0", "alpha gamma newtext")
        self.assertEqual(self.vec.doc_count, 5,
                         "覆盖写重复 +doc_count（B-2 漂移）")
        # 旧文本独有 token 回退、新文本 token 计入、共有 token 不变
        self.assertEqual(self.vec._df["beta"], 4)
        self.assertEqual(self.vec._df["gamma"], 1)
        self.assertEqual(self.vec._df["alpha"], 5)
        # 覆盖写后按新文本可搜
        top = self.eng.search("newtext", top_k=3)
        self.assertTrue(top and top[0][0] == "d0")

    def test_reindex_identical_text_no_drift(self):
        """覆盖写相同文本：df/doc_count 净变化为零"""
        before = (self.vec.doc_count, dict(self.vec._df))
        self.eng.index_memory("d1", "alpha beta doc 1")
        self.assertEqual((self.vec.doc_count, dict(self.vec._df)), before)

    def test_remove_memory_retracts_counts(self):
        self.eng.index_memory("d0", "alpha gamma newtext")
        self.eng.remove_memory("d0")
        self.assertEqual(self.vec.doc_count, 4, "remove 未回退 doc_count")
        self.assertEqual(self.vec._df["gamma"], 0)
        self.assertEqual(self.vec._df["newtext"], 0)
        self.assertEqual(self.vec._df["alpha"], 4)
        # 幂等：移除不存在的 doc 不崩、计数不动
        self.eng.remove_memory("nonexistent")
        self.assertEqual(self.vec.doc_count, 4)

    def test_idf_tracks_retracted_df(self):
        self.eng.index_memory("d0", "alpha gamma newtext")
        # alpha：df 回到 5、doc_count 5 → idf = log(6/6)+1
        self.assertAlmostEqual(self.vec.idf["alpha"], math.log(6 / 6) + 1)


class TestB3InvalidateFtsVocabCache(unittest.TestCase):
    """B-3：invalidate_fts_vocab_cache() 失活钩子契约"""

    def test_clears_cache_and_rowid(self):
        eng = IndexEngine()
        eng._fts_vocab_cache = {"abc": 10, "ghi": 7}
        eng._fts_vocab_rowid = 42
        eng.invalidate_fts_vocab_cache()
        self.assertIsNone(eng._fts_vocab_cache,
                          "UPDATE 后剪枝缓存未失活（B-3）")
        self.assertEqual(eng._fts_vocab_rowid, -1)

    def test_safe_on_fresh_engine(self):
        eng = IndexEngine()
        eng.invalidate_fts_vocab_cache()  # 不应抛
        self.assertIsNone(eng._fts_vocab_cache)


class TestB4GraphWeightZero(unittest.TestCase):
    """B-4：weight=0 的关系边得 0 分，不再反压 query 词直接命中"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662b4_")
        self.mf = _make_mf(self.tmp, encrypted=False)
        self.alice_doc = self.mf.add(
            "Alice presided over the quarterly project review meeting")
        self.bob_doc = self.mf.add("Bob keeps daily notebooks")
        # 生产写入路径建 0 权重边，memory_ids 指回 Bob 文档
        self.mf._knowledge_graph.add_relation(
            "Alice", "Bob", "works_with", weight=0.0,
            memory_id=self.bob_doc.id)

    def tearDown(self):
        self.mf.close()

    def test_zero_weight_edge_does_not_outrank_direct_hit(self):
        r = self.mf.search("Alice", max_results=5)
        chunks = list(r.chunks or [])
        self.assertTrue(chunks)
        self.assertEqual(chunks[0].memory_id, self.alice_doc.id,
                         "0 权重边候选压过 query 词直接命中（B-4）")
        bob = next((c for c in chunks
                    if c.memory_id == self.bob_doc.id), None)
        if bob is not None:  # fill 补位允许 0 分候选出现在尾部
            self.assertAlmostEqual(bob.relevance_score, 0.0,
                                   msg="0 权重边候选得分非 0（B-4）")


class TestB5TemporalOverrideExplain(unittest.TestCase):
    """B-5：temporal 覆盖同步 winner/final；仅 s > existing 才覆盖"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662b5_")
        self.mf = _make_mf(self.tmp, encrypted=False)
        # 弱文本命中 + 内容窗口日期：temporal 精确命中 0.6 > 文本分
        self.weak = self.mf.add("2026-11-11 Alice 简短同步")
        # 强文本命中 + 标签窗口日期：temporal 0.6 < 文本分
        self.strong = self.mf.add("评审评审评审评审评审评审",
                                  tags=["due:2026-11-11"])

    def tearDown(self):
        self.mf.close()

    def _explain(self, query):
        return self.mf.search(query, max_results=10,
                              time_anchor="2026-11-17", explain=True)

    def test_override_updates_winner_and_final(self):
        r = self._explain("上周 Alice 评审")
        c = next((x for x in r.chunks
                  if x.memory_id == self.weak.id), None)
        self.assertIsNotNone(c)
        bd = c.score_breakdown or {}
        self.assertEqual(bd.get("winner"), "temporal",
                         "temporal 覆盖未更新 winner（B-5）")
        self.assertAlmostEqual(bd.get("final", -1), c.relevance_score)
        self.assertAlmostEqual(c.relevance_score, 0.6)

    def test_lower_temporal_does_not_override(self):
        r = self._explain("上周 评审")
        c = next((x for x in r.chunks
                  if x.memory_id == self.strong.id), None)
        self.assertIsNotNone(c)
        bd = c.score_breakdown or {}
        self.assertNotEqual(bd.get("winner"), "temporal",
                            "temporal 低分仍抢占胜者标签（B-5）")
        self.assertAlmostEqual(bd.get("final", -1), c.relevance_score)

    def test_final_always_matches_relevance(self):
        """explain 不变量：breakdown.final == chunk.relevance_score"""
        for query in ("上周 Alice 评审", "上周 评审"):
            r = self._explain(query)
            for c in r.chunks:
                bd = c.score_breakdown or {}
                if "final" in bd:
                    self.assertAlmostEqual(
                        bd["final"], c.relevance_score,
                        msg="breakdown.final 与 relevance_score 矛盾（%s）"
                            % c.memory_id)


class TestB6ExpandContextDecrypt(unittest.TestCase):
    """B-6：加密库邻居轮解密回填，解密失败跳过该邻居"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662b6_")
        self.mf = _make_mf(self.tmp, encrypted=True)
        g = "b6grp"
        self.e0 = self.mf.add("会议开场白，大家轮流自我介绍",
                              tags=["ctxgrp:%s" % g, "ctxseq:0",
                                    "ctxdate:2026-11-10"])
        self.e1 = self.mf.add("Alice 产品评审会的会议纪要",
                              tags=["ctxgrp:%s" % g, "ctxseq:1",
                                    "ctxdate:2026-11-10"])
        self.e2 = self.mf.add("会后行动项已归档跟踪",
                              tags=["ctxgrp:%s" % g, "ctxseq:2",
                                    "ctxdate:2026-11-10"])

    def tearDown(self):
        self.mf.close()

    def _hit_chunk(self, result):
        return next((c for c in result.chunks
                     if c.memory_id == self.e1.id), None)

    def test_neighbors_decrypted(self):
        r = self.mf.search("产品评审", max_results=5, expand_context=2)
        chunk = self._hit_chunk(r)
        self.assertIsNotNone(chunk)
        ce = chunk.context_expansion
        self.assertIsNotNone(ce)
        by_seq = dict(ce)
        self.assertEqual(by_seq.get(0), "会议开场白，大家轮流自我介绍",
                         "加密邻居轮未解密回填（B-6）")
        self.assertEqual(by_seq.get(2), "会后行动项已归档跟踪")
        for _, text in ce:
            self.assertTrue(text, "邻居轮出现静默空串（B-6）")

    def test_decrypt_failure_skips_neighbor(self):
        # 破坏 seq0 的密文：解密失败 → 该邻居跳过，其余邻居仍在
        conn = self.mf.storage._get_conn()
        conn.execute("UPDATE memories SET ciphertext = ? WHERE id = ?",
                     (os.urandom(64), self.e0.id))
        conn.commit()
        r = self.mf.search("产品评审", max_results=5, expand_context=2)
        chunk = self._hit_chunk(r)
        self.assertIsNotNone(chunk)
        ce = chunk.context_expansion or []
        by_seq = dict(ce)
        self.assertNotIn(0, by_seq, "解密失败的邻居未被跳过（B-6）")
        self.assertEqual(by_seq.get(2), "会后行动项已归档跟踪")


class TestB7CnNumberNormalize(unittest.TestCase):
    """B-7：中文两位数换算——X十=X*10（不再恒多加 10）"""

    def test_x_shi_is_x_times_ten(self):
        anchor = "2026-11-17"
        self.assertIn("[2026-10-28]", _normalize_temporal("二十天前立项", anchor),
                      "二十天前应为 20 天（旧实现算成 30）")
        self.assertIn("[2026-10-18]", _normalize_temporal("三十天前复盘", anchor))
        self.assertIn("[2026-10-23]", _normalize_temporal("二十五天前复盘", anchor))
        self.assertIn("[2026-11-02]", _normalize_temporal("十五天前上线", anchor))

    def test_shi_x_and_bare_shi_unchanged(self):
        anchor = "2026-11-17"
        # 十二=12（原正确行为回归保护）、裸「十」=10
        self.assertIn("[2026-11-05]", _normalize_temporal("十二天前上线", anchor))
        self.assertIn("[2026-11-07]", _normalize_temporal("十天前上线", anchor))


class TestB8CjkGramUniformSampling(unittest.TestCase):
    """B-10.1：_CJK_GRAM_MAX 超限改均匀采样，首尾 gram 必选"""

    def test_tail_gram_included_for_long_token(self):
        # 24 字无停用字 token → 22 个 gram，超 12 上限
        token = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
        esc = IndexEngine._cjk_escaped_or(token)
        self.assertTrue(esc.startswith("("))
        # gram 数仍受上限约束（12 个 gram × 2 个引号）
        self.assertLessEqual(esc.count('"'), 2 * IndexEngine._CJK_GRAM_MAX + 2)
        # 首 gram 与尾 gram 都在（顺序截断会丢尾部）
        self.assertIn('"甲乙丙"', esc)
        self.assertIn('"酉戌亥"', esc, "长 token 尾部 gram 被顺序截断丢弃（B-10.1）")

    def test_gram_count_capped(self):
        token = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
        esc = IndexEngine._cjk_escaped_or(token)
        n_grams = esc.count('"') // 2
        self.assertLessEqual(n_grams, IndexEngine._CJK_GRAM_MAX)

    def test_short_token_keeps_all_grams(self):
        # 6 字 token → 4 个 gram ≤ 上限：全量保留，行为不变
        esc = IndexEngine._cjk_escaped_or("甲乙丙丁戊己")
        for g in ("甲乙丙", "乙丙丁", "丙丁戊", "丁戊己"):
            self.assertIn('"%s"' % g, esc)


if __name__ == "__main__":
    unittest.main()
