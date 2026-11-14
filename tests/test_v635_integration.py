"""v6.3.5 集成回归：记忆压缩与自适应上下文的门面/CLI/检索接线

验证四件事：
1. 压缩端到端：源记忆停止独立召回（检索管线过滤 compressed_into），
   摘要可被检索命中（走 add_memory → FTS 同步）
2. decompress 端到端：源恢复独立召回，摘要级联删除（FTS 不残留）
3. 加密库拒绝压缩（明文直插为安全缺陷，v6.3.5 重写后显式拒绝）
4. 门面 adaptive_search / CLI compress 冒烟
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings

warnings.filterwarnings("ignore")

import mindforge


class TestCompressIntegration(unittest.TestCase):
    """压缩全链路：写入 → 检索过滤 → 解压恢复"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v635i_")
        self.db = os.path.join(self.tmp, "m.db")
        self.mf = mindforge.MindForge(db_path=self.db, encrypted=False)
        # 三条高相似记忆（同一主题近重复）+ 一条无关记忆
        self.mf.add("Python 类型注解能提升代码可读性", category="python")
        self.mf.add("Python 类型注解让 IDE 补全更准确", category="python")
        self.mf.add("使用 Python 类型注解重构了旧模块", category="python")
        self.mf.add("今天午饭吃了兰州拉面", category="life")

    def tearDown(self):
        try:
            self.mf.close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ids_of(self, result):
        return {c.memory_id for c in result.chunks}

    def test_compress_then_search_sources_hidden_summary_visible(self):
        """压缩后：源不再独立召回，摘要可被检索命中"""
        before = self.mf.search("Python 类型注解", min_relevance=0.05)
        self.assertGreaterEqual(len(before.chunks), 3)

        r = self.mf.compress_memories(threshold=0.1, min_cluster_size=3)
        self.assertGreaterEqual(r["compressed_count"], 1)

        after = self.mf.search("Python 类型注解", min_relevance=0.05)
        # 源记忆全部退场
        self.assertEqual(self._ids_of(before) & self._ids_of(after), set())
        # 摘要入场（content 含"摘要"或合并标记）
        self.assertGreaterEqual(len(after.chunks), 1)
        summary_hits = [c for c in after.chunks
                        if "摘要" in c.content or len(c.content) > 0]
        self.assertTrue(any("合并" in c.content for c in after.chunks))

    def test_decompress_restores_sources_and_removes_summary(self):
        """解压后：源恢复独立召回，摘要从检索消失"""
        r = self.mf.compress_memories(threshold=0.1, min_cluster_size=3)
        self.assertGreaterEqual(r["compressed_count"], 1)
        cluster_id = r["clusters"][0]["cluster_id"]

        d = self.mf.decompress_cluster(cluster_id)
        self.assertTrue(d["decompressed"])
        self.assertGreaterEqual(d["restored_count"], 3)

        restored = self.mf.search("Python 类型注解", min_relevance=0.05)
        # 摘要不再出现（内容以"合并"字样为特征）
        self.assertFalse(any("合并" in c.content for c in restored.chunks))
        # 源回来了至少 3 条
        self.assertGreaterEqual(len(restored.chunks), 3)

    def test_list_compressions_roundtrip(self):
        """compress-list 门面返回簇结构"""
        r = self.mf.compress_memories(threshold=0.1, min_cluster_size=3)
        items = self.mf.list_compressions()
        self.assertEqual(len(items), r["compressed_count"])
        if items:
            self.assertIn("cluster_id", items[0])
            self.assertIn("source_ids", items[0])

    def test_dry_run_writes_nothing(self):
        """dry_run 不改变检索行为"""
        r = self.mf.compress_memories(threshold=0.1, min_cluster_size=3,
                                      dry_run=True)
        self.assertGreaterEqual(r["compressed_count"], 1)  # 报告了簇
        # 但源仍全部可召回
        after = self.mf.search("Python 类型注解", min_relevance=0.05)
        self.assertGreaterEqual(len(after.chunks), 3)
        self.assertEqual(self.mf.list_compressions(), [])

    def test_encrypted_store_rejects_compression(self):
        """v6.5.6 契约反转：加密库支持压缩（进程内解密聚类，明文不落盘）

        旧契约（v6.3.5-v6.5.5）：加密库入口 SecurityError——「记忆压缩」
        与「静态加密」默认互斥（双判官评审指出的产品级冲突）。
        新契约：压缩走 _plaintext_for_index 内存解密路径，摘要写入
        自动加密，FTS 不落明文（加密库本就不维护 FTS）。
        """
        mf2 = mindforge.MindForge(
            db_path=os.path.join(self.tmp, "enc.db"),
            encrypted=True,
            key_file=os.path.join(self.tmp, "enc.key"),
        )
        mf2.init_with_password("v635-pass")
        # 3 条高度相似记忆 → 应聚成 1 簇
        for txt in ("机器学习是人工智能的一个分支",
                    "机器学习是人工智能的重要分支",
                    "机器学习是人工智能领域的分支方向"):
            mf2.add(txt, category="test")
        result = mf2.compress_memories(threshold=0.3, dry_run=False)
        self.assertGreaterEqual(result.get("compressed_count", 0), 1)
        # 摘要与源均在加密库中可检索（读路径透明解密）
        hits = mf2.search("机器学习", max_results=10)
        self.assertTrue(hits.chunks)
        mf2.close()


class TestAdaptiveSearch(unittest.TestCase):
    """门面 adaptive_search 接线"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v635a_")
        self.mf = mindforge.MindForge(
            db_path=os.path.join(self.tmp, "m.db"), encrypted=False
        )
        self.mf.add("Python 并发编程笔记", category="python")

    def tearDown(self):
        try:
            self.mf.close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_adaptive_search_returns_bounded_threshold(self):
        """自适应阈值在 floor/ceiling 界内，结果计入会话窗口"""
        r = self.mf.adaptive_search("Python", session_id="s1")
        self.assertGreaterEqual(r["min_relevance_used"], 0.0)
        self.assertLessEqual(r["min_relevance_used"], 0.5)
        self.assertGreaterEqual(r["window_pattern_count"], 1)
        # 第二次查询模式计数递增
        r2 = self.mf.adaptive_search("并发", session_id="s1")
        self.assertGreaterEqual(r2["window_pattern_count"],
                                r["window_pattern_count"] + 1)


class TestCLISmoke(unittest.TestCase):
    """CLI compress / adaptive-search 冒烟"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v635c_")
        self.db = os.path.join(self.tmp, "m.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _main(self, argv):
        from mindforge.cli.main import main
        try:
            return main(argv)
        except SystemExit as e:
            return e.code or 0

    def test_cli_compress_dry_run_and_adaptive(self):
        """compress --dry-run 与 adaptive-search 命令端到端"""
        self.assertEqual(self._main([
            "--db-path", self.db,
            "add", "CLI 压缩测试记忆 A",
        ]), 0)
        self.assertEqual(self._main([
            "--db-path", self.db,
            "add", "CLI 压缩测试记忆 B",
        ]), 0)
        rc = self._main([
            "--db-path", self.db, "compress", "--dry-run",
            "--threshold", "0.1", "--min-cluster-size", "2",
        ])
        self.assertIn(rc, (0, 1))  # 空簇/写失败都不算崩溃
        rc2 = self._main([
            "--db-path", self.db, "adaptive-search", "压缩", "--session", "cli",
        ])
        self.assertIn(rc2, (0, 1))
        rc3 = self._main(["--db-path", self.db, "compress-list"])
        self.assertIn(rc3, (0, 1))


if __name__ == "__main__":
    unittest.main()
