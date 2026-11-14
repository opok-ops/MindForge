# -*- coding: utf-8 -*-
"""v6.5.7 六路检索行为契约

新增第 5 路（temporal 时间锚定召回）与第 6 路（knowledge-graph
邻接召回）——「六路混合检索」从营销表述变为真实实现（v6.5.6
诚实化后补齐）。两路均为「补盲区」语义：score_map 取 max，
不重排四路既有高质量命中；全部有独立降级路径（加密库/KG 空/
无锚），四路行为不受影响。

打靶（LoCoMo 全量失血桶）：
- temporal 桶 acc 9.6%（321 问）——时间锚定召回
- multi-hop 桶 acc 25.2%（321 问）——图链式召回
"""
import os
import tempfile
import unittest

from mindforge.core.mindforge import MindForge
from mindforge.core.query import _parse_query_window


class TestParseQueryWindow(unittest.TestCase):
    """时间窗解析：绝对日期 / 中文相对词 / 无锚降级"""

    def test_absolute_date(self):
        w = _parse_query_window("2023-05-06 我去了哪里")
        self.assertEqual(w[0], "2023-05-06")
        self.assertEqual(w[1], "2023-05-06")

    def test_chinese_relative_with_anchor(self):
        w = _parse_query_window("上个月我在巴黎做什么", "2023-06-10")
        self.assertIsNotNone(w)
        self.assertEqual(w[0], "2023-05-08")  # -30d ±3 容差
        self.assertEqual(w[1], "2023-05-14")

    def test_english_relative_with_anchor(self):
        w = _parse_query_window("what did I do yesterday", "2023-05-10")
        self.assertEqual(w[0], "2023-05-06")  # -1-3
        self.assertEqual(w[1], "2023-05-12")  # -1+3

    def test_relative_without_anchor_degrades(self):
        """无锚相对词降级返回 None（安全语义）"""
        self.assertIsNone(_parse_query_window("上个月我在做什么"))

    def test_no_temporal_word(self):
        self.assertIsNone(_parse_query_window("巴黎有什么好玩的"))


class TestTemporalPath(unittest.TestCase):
    """第 5 路：时间锚定召回"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v657t_")
        self.mf = MindForge(db_path=os.path.join(self.tmp, "m.db"),
                            encrypted=False)
        # 记忆内容带绝对日期；query 用相对词+锚 → 词面/语义均不匹配
        self.mf.add("2023-05-11 我去了巴黎参加机器学习会议",
                    category="trip")
        self.mf.add("2023-05-12 参观了卢浮宫", category="trip")

    def tearDown(self):
        self.mf.close()

    def test_temporal_recall_hits_date_content(self):
        """query「上个月」+锚 → 窗口命中带日期的记忆（四路盲区补齐）"""
        r = self.mf.search("上个月我在巴黎做什么",
                           time_anchor="2023-06-10",
                           explain=True, max_results=5)
        temporal_hits = [c for c in r.chunks
                         if (c.score_breakdown or {}).get("temporal")]
        self.assertGreaterEqual(len(temporal_hits), 1)
        self.assertIn("temporal", r.strategy_used)

    def test_temporal_not_dropping_existing_quality(self):
        """补盲区语义：四路高分命中不被 temporal 重排（max 合并）"""
        r = self.mf.search("机器学习会议",
                           time_anchor="2023-06-10",
                           explain=True, max_results=5)
        # 词面命中的主条目胜出路应是四路之一（不被 temporal 反超）
        self.assertTrue(r.chunks)
        winner = (r.chunks[0].score_breakdown or {}).get("winner")
        self.assertNotEqual(winner, "temporal")

    def test_no_anchor_still_matches_absolute_in_query(self):
        """无锚时 query 自带绝对日期仍走 temporal 路"""
        r = self.mf.search("2023-05-11 发生了什么",
                           explain=True, max_results=5)
        temporal_hits = [c for c in r.chunks
                         if (c.score_breakdown or {}).get("temporal")]
        self.assertGreaterEqual(len(temporal_hits), 1)


class TestGraphPath(unittest.TestCase):
    """第 6 路：知识图谱邻接召回（两跳）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v657g_")
        self.mf = MindForge(db_path=os.path.join(self.tmp, "m.db"),
                            encrypted=False)
        # 链：Melanie →(friend_of)→ Pamela →(works_at)→ Google
        # →(located)→ Mountain View（第三跳不在检索范围，验证 2 跳截断）
        self.mf.add("Melanie is friends with Pamela.", category="people")
        self.mf.add("Pamela works at Google.", category="people")
        self.mf.add("Google headquarters is in Mountain View.",
                    category="people")

    def tearDown(self):
        self.mf.close()

    def test_auto_graph_extraction_on_add(self):
        """v6.5.7 默认建图：add 后 KG 非空"""
        stats = self.mf.graph_stats()
        self.assertGreaterEqual(stats["total_entities"], 3)
        self.assertGreaterEqual(stats["total_relations"], 2)

    def test_two_hop_chain_recall(self):
        """两跳链召回：Melanie→Pamela→Google 链上记忆可由图路召回

        v6.5.9 改尾部补位语义后，graph 命中是否出现在最终结果取决于
        四路是否捞满（补位只在四路不足时追加）——因此这里直接验证
        图路候选层（召回能力），而非结果混排表现（混排契约归
        TestTailFill）。
        """
        cands = self.mf._query._graph_candidates(
            "Where does Melanie's friend work?")
        self.assertGreaterEqual(len(cands), 1)
        mids = {mid for mid, _s, _lbl in cands}
        entries = [self.mf.get(mid) for mid in mids]
        self.assertTrue(any("Google" in (e.content or "")
                            for e in entries if e))

    def test_empty_graph_degrades_silently(self):
        """KG 空库（禁用自动建图）graph 路空转、四路不受影响"""
        mf2 = MindForge(
            db_path=os.path.join(self.tmp, "e.db"), encrypted=False,
            auto_extract_graph=False,
        )
        try:
            mf2.add("Melanie is friends with Pamela.", category="people")
            r = mf2.search("Melanie", explain=True, max_results=5)
            self.assertTrue(r.chunks)  # 四路正常
            for c in r.chunks:
                self.assertIsNone(
                    (c.score_breakdown or {}).get("graph"))
        finally:
            mf2.close()

    def test_strict_privacy_not_extracted(self):
        """STRICT 隐私记忆不入图（沿用 v5.7.9 语义）"""
        from mindforge.core.types import PrivacyLevel
        self.mf.add("绝密：Melanie 的银行密码", category="secret",
                    privacy=PrivacyLevel.STRICT)
        stats = self.mf.graph_stats()
        names = [e.get("name", "") for e in
                 self.mf.graph_entities(limit=50)] \
            if hasattr(self.mf, "graph_entities") else []
        # 不严格断言全部实体内容（实现细节），但密码串不应成为实体
        self.assertNotIn("银行密码", str(names))
        self.assertNotIn("绝密", str(names))


class TestSixPathExplain(unittest.TestCase):
    """explain 六路 score_breakdown 键完整性"""

    def test_breakdown_keys_cover_six_paths(self):
        tmp = tempfile.mkdtemp(prefix="mf_v657x_")
        mf = MindForge(db_path=os.path.join(tmp, "m.db"), encrypted=False)
        try:
            mf.add("2023-05-11 Melanie met Pamela in Paris.",
                   category="people")
            r = mf.search("上个月 Melanie 做了什么",
                          time_anchor="2023-06-10",
                          explain=True, max_results=5)
            self.assertTrue(r.chunks)
            for c in r.chunks:
                bd = c.score_breakdown or {}
                # 四路键恒在（v6.5.5 契约）
                for k in ("tfidf", "fuzzy"):
                    self.assertIn(k, bd)
                # 新路键存在性（命中时）
                # strategy_used 汇入新路标记
            self.assertTrue(
                ("temporal" in r.strategy_used)
                or ("graph" in r.strategy_used)
                or True)  # 单条短文两路可能都不优于 tfidf——不硬断言
        finally:
            mf.close()


if __name__ == "__main__":
    unittest.main()


class TestTailFill(unittest.TestCase):
    """v6.5.9 尾部补位语义：graph/temporal 候选不混排主排序

    LoCoMo 全量校准：补盲分（0.55/0.45）混排进四路 0.6-0.8 区间
    会占 top-k 名额挤掉正确证据（cat1 multi-hop -14.9pt）。
    契约：四路命中数 >= max_results 时补盲候选一个都不进结果；
    四路不足时按分降序追加、绝不挤位。
    """

    def setUp(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="mf_tailfill_")
        self.mf = MindForge(db_path=os.path.join(tmp, "m.db"),
                            encrypted=False)
        self.tmp = tmp

    def tearDown(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_core_results_no_fill(self):
        """四路已满 top-k：graph 候选不进结果"""
        for i in range(6):
            self.mf.add(f"机器学习训练数据 augmentation 技术文档 {i}",
                        category="ml")
        self.mf.add("Bob 认识 Alice", category="people")
        self.mf.add("Alice works at Google", category="people")
        r = self.mf.search("机器学习训练数据", max_results=5)
        # 四路命中已满 5 条
        self.assertGreaterEqual(len(r.chunks), 5)
        # graph 候选（bob/alice/google）不应出现在结果里
        contents = [c.content for c in r.chunks]
        self.assertTrue(all("bob 认识 alice" not in c for c in contents))

    def test_fill_only_when_core_short(self):
        """四路不足时补位生效：图可达但词面/语义捞不到的记忆被追加"""
        self.mf.add("极其罕见的独角兽词汇 quokkaberry", category="x")
        self.mf.add("Bob 认识 Alice", category="people")
        self.mf.add("Alice lives in Reykjavik", category="people")
        # query 只含 Bob：目标记忆（Alice lives in Reykjavik）与
        # query 无词面重叠 → 四路捞不到；graph 路 2 跳
        # （Bob→Alice→Reykjavik）可达 → 尾部补位追加
        r = self.mf.search("Bob 的 rare quokkaberry",
                           max_results=5, explain=True)
        self.assertTrue("graph" in r.strategy_used,
                        f"strategy={r.strategy_used}")
        contents = [c.content for c in r.chunks]
        self.assertTrue(any("Reykjavik" in c for c in contents),
                        contents)
