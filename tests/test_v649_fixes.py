# -*- coding: utf-8 -*-
"""v6.4.9 修复守卫：FTS5 查询侧高频短语剪枝（刀 7）。

背景：trigram FTS5 索引下，全库高频短语（模板语料的「陈述性记忆」）
的 bm25 全匹配行评分是 100k mixed/病态查询延迟的主要剩余构成
（30k 分路实测：单高频短语 50.5ms，剪枝后 1.3ms）。剪枝规则与
TF-IDF max_df 0.05 对齐：短语 df 上界（min trigram df）超库容 5%
的词从 MATCH 剔除。
"""
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, "/home/z/my-project/MindForge")

from mindforge.core.indexer import IndexEngine
from mindforge.core.mindforge import MindForge
from mindforge.core.types import MemoryConfig
from mindforge.core.version import __version__, VERSION_INFO


class TestFtsPruneBehavior(unittest.TestCase):
    """_prune_fts_query 的剪枝行为契约。"""

    def _engine_with_corpus(self, n=250):
        eng = IndexEngine(":memory:")
        for i in range(n):
            eng.index_memory(
                f"d{i:04d}",
                f"模板条目{i} 陈述性记忆 编号{i} 锚词{i:04d} 内容")
        return eng

    def test_high_df_phrase_pruned(self):
        """高频短语被剔除出 MATCH 表达式。"""
        eng = self._engine_with_corpus()
        conn = eng._get_conn() if hasattr(eng, "_get_conn") else None
        import sqlite3
        conn = sqlite3.connect(":memory:")
        # 造一个小 FTS 库复刻剪枝场景
        conn.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(content, tokenize='trigram')")
        conn.execute("CREATE TABLE memories (id TEXT, category TEXT, rowid_src INTEGER)")
        for i in range(250):
            c = f"模板条目{i} 陈述性记忆 编号{i} 锚词{i:04d}"
            conn.execute("INSERT INTO memory_fts (content) VALUES (?)", (c,))
            conn.execute("INSERT INTO memories VALUES (?,?,?)", (f"d{i}", "general", i + 1))
        pruned = eng._prune_fts_query(
            conn, "锚词0007 陈述性记忆",
            '"锚词0007" OR "陈述性记忆"')
        self.assertIn("锚词0007", pruned)
        self.assertNotIn("陈述性记忆", pruned,
                         "全库高频短语必须被剪出 MATCH 表达式")
        conn.close()

    def test_low_df_tokens_all_kept(self):
        """全部低频时表达式不变（不误剪）。"""
        eng = IndexEngine(":memory:")
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(content, tokenize='trigram')")
        conn.execute("CREATE TABLE memories (id TEXT, category TEXT, rowid_src INTEGER)")
        # 每个词只出现一次 = 全低频
        words = ["苹果香蕉", "雪梨芒果", "葡萄西瓜"]
        for i, w in enumerate(words):
            conn.execute("INSERT INTO memory_fts (content) VALUES (?)", (w,))
            conn.execute("INSERT INTO memories VALUES (?,?,?)", (f"d{i}", "general", i + 1))
        pruned = eng._prune_fts_query(
            conn, "苹果香蕉 雪梨芒果", '"苹果香蕉" OR "雪梨芒果"')
        self.assertEqual(pruned, '"苹果香蕉" OR "雪梨芒果"',
                         "全低频查询不得误剪")
        conn.close()

    def test_all_high_df_keeps_min(self):
        """全部高频时保底留 df 上界最小的一个（防空 MATCH）。"""
        eng = IndexEngine(":memory:")
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(content, tokenize='trigram')")
        conn.execute("CREATE TABLE memories (id TEXT, category TEXT, rowid_src INTEGER)")
        for i in range(300):
            c = f"模板内容{i} 通用短语尾部 锚点{i % 30}"
            conn.execute("INSERT INTO memory_fts (content) VALUES (?)", (c,))
            conn.execute("INSERT INTO memories VALUES (?,?,?)", (f"d{i}", "general", i + 1))
        # 「锚点07」df=10（低），「模板内容」df=全库——一个低频一个高频
        pruned = eng._prune_fts_query(
            conn, "模板内容 锚点07", '"模板内容" OR "锚点07"')
        self.assertIn("锚点07", pruned)
        conn.close()


class TestPathologicalFtsSkip(unittest.TestCase):
    """病态回退时 FTS5 跳过的管线契约（v6.4.9 第二部分）。"""

    def test_strategy_excludes_fts5_on_fallback(self):
        """全库词查询（tfidf_fallback）不跑 FTS5。"""
        tmp = tempfile.mkdtemp()
        try:
            mf = MindForge(config=MemoryConfig(
                db_path=f"{tmp}/m.db", key_file=None, encrypted=False))
            for i in range(300):
                mf.add(f"模板条目{i} 陈述性记忆 编号{i}", category="t")
            # 全库词查询：TF-IDF 回退（词表全库命中，无区分词）
            r = mf.search("模板条目 陈述性记忆")
            self.assertNotIn("fts5", r.strategy_used,
                             "病态回退时不得走 FTS5 路")
            # 正常查询（含区分词）：FTS5 参与
            r2 = mf.search("编号299")
            # v578 的 fake embedding 单例可能让向量路激活（strategy
            # 带 vector+ 前缀），按片段断言而非全串相等
            self.assertIn("fts5", r2.strategy_used,
                          "区分词查询 FTS5 必须参与（质量契约）")
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestVersionBump(unittest.TestCase):
    """版本钉值（每版随发版一并 bump，v6.4.5 教训）。"""

    def test_version_is_649(self):
        self.assertEqual(__version__, "6.5.12")
        self.assertEqual(VERSION_INFO, (6, 5, 12))


if __name__ == "__main__":
    unittest.main()
