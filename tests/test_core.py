"""MindForge v5.5.2 单元测试"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
import tempfile
import os


class TestCoreTypes(unittest.TestCase):
    """核心类型测试"""

    def test_enums(self):
        from core.types import PrivacyLevel, Importance, MemoryType, MemoryLayer

        self.assertEqual(PrivacyLevel.PUBLIC.value, "PUBLIC")
        self.assertEqual(PrivacyLevel.INTERNAL.value, "INTERNAL")
        self.assertEqual(PrivacyLevel.PRIVATE.value, "PRIVATE")
        self.assertEqual(PrivacyLevel.STRICT.value, "STRICT")

        self.assertEqual(Importance.LOW.value, "LOW")
        self.assertEqual(Importance.MEDIUM.value, "MEDIUM")
        self.assertEqual(Importance.HIGH.value, "HIGH")
        self.assertEqual(Importance.CRITICAL.value, "CRITICAL")

        self.assertEqual(MemoryType.TEXT.value, "text")
        self.assertEqual(MemoryType.IMAGE.value, "image")
        self.assertEqual(MemoryType.AUDIO.value, "audio")
        self.assertEqual(MemoryType.CODE.value, "code")

        self.assertEqual(MemoryLayer.SENSORY.value, "sensory")
        self.assertEqual(MemoryLayer.SHORT_TERM.value, "short_term")
        self.assertEqual(MemoryLayer.LONG_TERM.value, "long_term")
        self.assertEqual(MemoryLayer.PERMANENT.value, "permanent")

    def test_memory_config(self):
        from core.types import MemoryConfig

        config = MemoryConfig(db_path="/tmp/test.db")
        self.assertEqual(config.db_path, "/tmp/test.db")
        self.assertEqual(config.encrypted, True)
        self.assertEqual(config.default_privacy.value, "INTERNAL")
        self.assertEqual(config.default_importance.value, "MEDIUM")
        self.assertEqual(config.default_layer.value, "short_term")


class TestStorageEngine(unittest.TestCase):
    """存储引擎测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_add_and_get_memory(self):
        from core.storage import StorageEngine
        from core.types import MemoryLayer, PrivacyLevel, Importance, MemoryType

        storage = StorageEngine(db_path=self.db_path)

        entry = storage.add_memory(
            content="测试记忆内容",
            category="test",
            tags=["tag1", "tag2"],
            privacy=PrivacyLevel.INTERNAL,
            importance=Importance.MEDIUM,
            memory_type=MemoryType.TEXT,
            layer=MemoryLayer.SHORT_TERM,
        )

        self.assertIsNotNone(entry.id)
        self.assertEqual(entry.content, "测试记忆内容")
        self.assertEqual(entry.category, "test")
        self.assertEqual(len(entry.tags), 2)

        retrieved = storage.get_memory(entry.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.content, "测试记忆内容")
        self.assertEqual(retrieved.category, "test")

    def test_list_memories(self):
        from core.storage import StorageEngine
        from core.types import MemoryLayer

        storage = StorageEngine(db_path=self.db_path)

        for i in range(5):
            storage.add_memory(
                content=f"记忆 {i}",
                category="test" if i % 2 == 0 else "other",
                layer=MemoryLayer.SHORT_TERM,
            )

        all_memories = storage.list_memories(limit=10)
        self.assertEqual(len(all_memories), 5)

        test_memories = storage.list_memories(category="test", limit=10)
        self.assertEqual(len(test_memories), 3)

    def test_update_memory(self):
        from core.storage import StorageEngine
        from core.types import MemoryLayer

        storage = StorageEngine(db_path=self.db_path)

        entry = storage.add_memory(
            content="原始内容",
            category="test",
            layer=MemoryLayer.SHORT_TERM,
        )

        result = storage.update_memory(
            entry_id=entry.id,
            content="更新后的内容",
            category="updated",
        )

        self.assertTrue(result)

        updated = storage.get_memory(entry.id)
        self.assertEqual(updated.content, "更新后的内容")
        self.assertEqual(updated.category, "updated")

    def test_delete_memory(self):
        from core.storage import StorageEngine
        from core.types import MemoryLayer

        storage = StorageEngine(db_path=self.db_path)

        entry = storage.add_memory(
            content="要删除的记忆",
            category="test",
            layer=MemoryLayer.SHORT_TERM,
        )

        result = storage.delete_memory(entry.id, hard_delete=True)
        self.assertTrue(result)

        retrieved = storage.get_memory(entry.id)
        self.assertIsNone(retrieved)

    def test_stats(self):
        from core.storage import StorageEngine
        from core.types import MemoryLayer

        storage = StorageEngine(db_path=self.db_path)

        for i in range(3):
            storage.add_memory(
                content=f"记忆 {i}",
                category="test",
                layer=MemoryLayer.LONG_TERM,
            )

        stats = storage.get_stats()
        self.assertIn("total", stats)
        self.assertEqual(stats["total"], 3)


class TestMindForge(unittest.TestCase):
    """MindForge 主类测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_init(self):
        from core.mindforge import MindForge

        cm = MindForge(db_path=self.db_path, encrypted=False)
        self.assertIsNotNone(cm)
        self.assertIsNotNone(cm.storage)
        self.assertIsNotNone(cm.index)
        self.assertIsNotNone(cm.query)
        cm.close()

    def test_add_and_search(self):
        from core.mindforge import MindForge

        cm = MindForge(db_path=self.db_path, encrypted=False)

        cm.add(
            content="Python 是一种高级编程语言",
            category="tech",
            tags=["python", "编程"],
        )

        cm.add(
            content="数据库优化的关键是建立合适的索引",
            category="tech",
            tags=["数据库", "优化"],
        )

        results = cm.search("Python 编程", max_results=5)
        self.assertGreater(results.total_found, 0)
        cm.close()

    def test_export_import_json(self):
        from core.mindforge import MindForge
        import json

        cm = MindForge(db_path=self.db_path, encrypted=False)

        cm.add(content="导出测试 1", category="test", tags=["export"])
        cm.add(content="导出测试 2", category="test", tags=["export"])

        export_path = os.path.join(self.tmp_dir, "export.json")
        count = cm.export_json(export_path)
        self.assertEqual(count, 2)

        with open(export_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["memories"]), 2)

        new_db = os.path.join(self.tmp_dir, "new.db")
        cm2 = MindForge(db_path=new_db, encrypted=False)
        stats = cm2.import_json(export_path)
        self.assertEqual(stats["imported"], 2)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(stats["failed"], 0)

        results = cm2.search("导出测试", max_results=10)
        self.assertEqual(results.total_found, 2)

        cm.close()
        cm2.close()

    def test_export_csv(self):
        from core.mindforge import MindForge
        import csv

        cm = MindForge(db_path=self.db_path, encrypted=False)

        cm.add(content="CSV 测试 1", category="csv_test")
        cm.add(content="CSV 测试 2", category="csv_test")

        csv_path = os.path.join(self.tmp_dir, "export.csv")
        count = cm.export_csv(csv_path)
        self.assertEqual(count, 2)

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(len(rows), 2)
        self.assertIn("content", rows[0])
        self.assertIn("category", rows[0])

        cm.close()


class TestKnowledgeGraph(unittest.TestCase):
    """知识图谱测试"""

    def test_extract_entities(self):
        from modules.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        entities = kg.extract_entities("Python 和 PostgreSQL 是常用的开发工具")
        self.assertIsInstance(entities, list)

    def test_add_and_get_relations(self):
        from modules.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        kg.add_relation("Python", "编程开发", "is_a", weight=0.9)
        kg.add_relation("PostgreSQL", "数据库", "is_a", weight=0.9)
        kg.add_relation("Python", "PostgreSQL", "uses", weight=0.7)

        related = kg.get_related_entities("Python", depth=1)
        self.assertGreater(len(related), 0)

        stats = kg.get_entity_stats()
        self.assertIn("total_entities", stats)
        self.assertIn("total_relations", stats)


class TestPersonalityEngine(unittest.TestCase):
    """人格化引擎测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_learn_and_profile(self):
        from core.mindforge import MindForge
        from modules.personality import PersonalityEngine

        cm = MindForge(db_path=self.db_path, encrypted=False)
        pe = PersonalityEngine(cm.storage)

        pe.learn_from_interaction(
            "test_user",
            "你好，请帮我写个Python脚本",
            "好的，这是一个简洁的Python脚本示例...",
        )

        profile = pe.get_profile("test_user")
        self.assertIsNotNone(profile)
        self.assertGreaterEqual(profile.total_interactions, 1)

        style = pe.get_recommended_style("test_user")
        self.assertIsInstance(style, dict)

        cm.close()


class TestFTSUpdateSync(unittest.TestCase):
    """v5.0.6: update_memory 的 FTS 索引同步测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_update_content_syncs_fts(self):
        """更新 content 后，FTS 索引应反映新内容而非旧内容"""
        from core.storage import StorageEngine
        from core.types import MemoryLayer

        storage = StorageEngine(db_path=self.db_path)
        entry = storage.add_memory(
            content="original content about python programming",
            category="tech",
            layer=MemoryLayer.SHORT_TERM,
        )

        # 更新为完全不同的内容
        storage.update_memory(entry_id=entry.id, content="updated content about database optimization")

        conn = storage._get_conn()
        # FTS 中应能搜到新内容
        rows = conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH 'database'"
        ).fetchall()
        self.assertGreater(len(rows), 0, "FTS 应包含更新后的新内容")

        # FTS 中不应再命中旧内容的关键词
        rows_old = conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH 'python'"
        ).fetchall()
        self.assertEqual(len(rows_old), 0, "FTS 不应再包含更新前的旧内容")
        storage.close()

    def test_update_category_syncs_fts(self):
        """更新 category 后，FTS 索引的 category 字段应同步"""
        from core.storage import StorageEngine
        from core.types import MemoryLayer

        storage = StorageEngine(db_path=self.db_path)
        entry = storage.add_memory(
            content="测试分类同步的内容",
            category="old_cat",
            layer=MemoryLayer.SHORT_TERM,
        )

        storage.update_memory(entry_id=entry.id, category="new_cat")

        conn = storage._get_conn()
        rows = conn.execute(
            "SELECT category FROM memory_fts WHERE memory_fts MATCH 'new_cat'"
        ).fetchall()
        self.assertGreater(len(rows), 0, "FTS 应能按新分类检索")
        storage.close()


class TestRebuildFTS(unittest.TestCase):
    """v5.0.6: rebuild_fts 测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_rebuild_fts_clears_orphans(self):
        """rebuild_fts 应消除孤立 FTS 记录"""
        from core.storage import StorageEngine
        from core.types import MemoryLayer

        storage = StorageEngine(db_path=self.db_path)
        for i in range(3):
            storage.add_memory(
                content=f"记忆内容 {i}",
                category="test",
                layer=MemoryLayer.SHORT_TERM,
            )

        # 重建前健康检查
        before = storage.health_check()
        self.assertEqual(before["fts_orphans"], 0)

        # 手动制造孤立 FTS 记录
        conn = storage._get_conn()
        conn.execute(
            "INSERT INTO memory_fts (rowid, content, category, tags) VALUES (999999, 'orphan', 'x', '[]')"
        )
        conn.commit()
        orphans = conn.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE rowid NOT IN (SELECT rowid FROM memories)"
        ).fetchone()[0]
        self.assertGreater(orphans, 0, "应已制造孤立记录")

        # 重建
        result = storage.rebuild_fts()
        self.assertTrue(result["rebuilt"])
        self.assertEqual(result["indexed"], 3)

        # 重建后健康检查
        after = storage.health_check()
        self.assertEqual(after["fts_orphans"], 0, "孤立记录应被清除")
        storage.close()


class TestPurgeTrash(unittest.TestCase):
    """v5.0.6: purge_trash 测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_purge_trash_removes_soft_deleted(self):
        """purge_trash 应永久删除所有软删除的记忆"""
        from core.storage import StorageEngine
        from core.types import MemoryLayer

        storage = StorageEngine(db_path=self.db_path)

        # 添加 3 条记忆
        ids = []
        for i in range(3):
            entry = storage.add_memory(
                content=f"待删除记忆 {i}",
                category="test",
                layer=MemoryLayer.SHORT_TERM,
            )
            ids.append(entry.id)

        # 软删除 2 条
        storage.delete_memory(ids[0], hard_delete=False)
        storage.delete_memory(ids[1], hard_delete=False)

        # 回收站应有 2 条
        trash = storage.list_memories(category="trash", limit=100)
        self.assertEqual(len(trash), 2)

        # 清空回收站
        count = storage.purge_trash()
        self.assertEqual(count, 2)

        # 回收站应清空
        trash_after = storage.list_memories(category="trash", limit=100)
        self.assertEqual(len(trash_after), 0)

        # 未删除的那条应仍在
        remaining = storage.list_memories(category="test", limit=100)
        self.assertEqual(len(remaining), 1)
        storage.close()

    def test_purge_trash_empty(self):
        """回收站为空时 purge_trash 应返回 0"""
        from core.storage import StorageEngine

        storage = StorageEngine(db_path=self.db_path)
        count = storage.purge_trash()
        self.assertEqual(count, 0)
        storage.close()


class TestSearchHydration(unittest.TestCase):
    """v5.2.8 修复验证：跨进程搜索（索引水合 + 模糊补充召回）"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_cross_process_search(self):
        """新进程实例中 search 应能搜到历史记忆（索引水合）"""
        from core.mindforge import MindForge

        cm1 = MindForge(db_path=self.db_path, encrypted=False)
        cm1.add("知识图谱让记忆不再孤立", category="tech", tags=["kg"])
        cm1.close()

        # 模拟新进程：全新实例，内存索引为空
        cm2 = MindForge(db_path=self.db_path, encrypted=False)
        self.assertTrue(cm2.index.needs_hydration)
        result = cm2.search("知识图谱")
        self.assertGreaterEqual(result.total_found, 1)
        cm2.close()

    def test_cjk_substring_search(self):
        """CJK 子串查询应通过模糊补充召回命中"""
        from core.mindforge import MindForge

        cm = MindForge(db_path=self.db_path, encrypted=False)
        cm.add("SQLite 是轻量级嵌入式数据库", category="tech")
        result = cm.search("数据库")
        self.assertGreaterEqual(result.total_found, 1)
        cm.close()

    def test_search_no_false_positive(self):
        """不存在的关键词应返回 0 条"""
        from core.mindforge import MindForge

        cm = MindForge(db_path=self.db_path, encrypted=False)
        cm.add("普通的一条记忆", category="test")
        result = cm.search("绝对不存在的关键词zzz")
        self.assertEqual(result.total_found, 0)
        cm.close()


class TestMultiAgentMemory(unittest.TestCase):
    """v5.2.8 新增：多 Agent 记忆空间（实验性）"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make(self):
        from core.mindforge import MindForge
        return MindForge(db_path=self.db_path, encrypted=False)

    def test_space_lifecycle(self):
        """创建空间 → 添加成员 → 共享 → 冲突解决 → 隐私护栏 → 读取 → 统计"""
        from core.types import PrivacyLevel

        cm = self._make()
        entry = cm.add("可共享的团队知识", category="team")
        private = cm.add("私密内容", category="secret", privacy=PrivacyLevel.PRIVATE)

        ma = cm.multi_agent
        r = ma.create_space("s1", owner_agent="leader")
        self.assertTrue(r["success"])

        r = ma.add_member("s1", "worker", role="editor", actor="leader")
        self.assertTrue(r["success"])

        r = ma.share_memory("s1", entry.id, actor="worker")
        self.assertTrue(r["success"])
        self.assertEqual(r["version"], 1)

        # 冲突解决：重复共享 → last-write-wins，版本递增
        r = ma.share_memory("s1", entry.id, actor="leader")
        self.assertTrue(r["success"])
        self.assertEqual(r["version"], 2)
        self.assertEqual(r["conflict_resolved"], "last-write-wins")

        # 隐私护栏：PRIVATE 禁止共享
        r = ma.share_memory("s1", private.id, actor="leader")
        self.assertFalse(r["success"])
        self.assertIn("隐私护栏", r["error"])

        # reader 可读取
        ma.add_member("s1", "guest", role="reader", actor="guest")
        r = ma.list_space_memories("s1", actor="guest")
        self.assertTrue(r["success"])
        self.assertEqual(r["count"], 1)

        # 非成员禁止读取
        r = ma.list_space_memories("s1", actor="outsider")
        self.assertFalse(r["success"])

        stats = ma.space_stats()
        self.assertEqual(stats["total_spaces"], 1)
        self.assertEqual(stats["total_shared_items"], 1)
        cm.close()

    def test_permission_enforcement(self):
        """reader 不能共享；非 owner 不能加人/删空间"""
        cm = self._make()
        entry = cm.add("知识", category="t")
        ma = cm.multi_agent
        ma.create_space("s2", owner_agent="boss")
        ma.add_member("s2", "reader1", role="reader", actor="boss")

        r = ma.share_memory("s2", entry.id, actor="reader1")
        self.assertFalse(r["success"])

        r = ma.add_member("s2", "another", role="editor", actor="reader1")
        self.assertFalse(r["success"])

        r = ma.delete_space("s2", actor="reader1")
        self.assertFalse(r["success"])

        r = ma.delete_space("s2", actor="boss")
        self.assertTrue(r["success"])
        cm.close()

    def test_duplicate_space_name_rejected(self):
        """空间名称唯一"""
        cm = self._make()
        ma = cm.multi_agent
        self.assertTrue(ma.create_space("dup", owner_agent="a")["success"])
        self.assertFalse(ma.create_space("dup", owner_agent="b")["success"])
        cm.close()


class TestIntentRouter(unittest.TestCase):
    """v5.3.9 意图分类路由测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mf_intent_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rule_matching(self):
        """规则正则匹配"""
        from modules.intent_router import IntentRouter
        router = IntentRouter()
        result = router.classify("记住：用户偏好深色主题")
        self.assertEqual(result.label, "记忆存储")
        self.assertGreater(result.confidence, 0.3)

    def test_keyword_matching(self):
        """关键词加权匹配"""
        from modules.intent_router import IntentRouter
        router = IntentRouter()
        result = router.classify("搜索一下之前的部署记录")
        self.assertEqual(result.label, "记忆检索")

    def test_force_override(self):
        """强制覆盖意图"""
        from modules.intent_router import IntentRouter
        router = IntentRouter()
        result = router.classify("随便说点什么", force_override="memory_store")
        self.assertEqual(result.intent, "memory_store")


class TestConflictDetector(unittest.TestCase):
    """v5.3.9 矛盾检测测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mf_conflict_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_antonym_detection(self):
        """反义词对检测"""
        from modules.conflict_detector import ConflictDetector
        detector = ConflictDetector()
        conflicts = detector.detect_antonym(
            "MySQL 已启用，启动成功", "MySQL 已禁用，启动失败", "id1", "id2"
        )
        self.assertIsNotNone(conflicts)
        self.assertGreaterEqual(conflicts.severity, 0.5)

    def test_empty_memories(self):
        """空记忆列表安全"""
        from modules.conflict_detector import ConflictDetector
        detector = ConflictDetector()
        result = detector.scan_memories([])
        self.assertEqual(len(result), 0)


class TestSkillExtractor(unittest.TestCase):
    """v5.3.9 技能转化测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mf_skill_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_extract_from_devops(self):
        """从 DevOps 记忆中抽取技能"""
        from modules.skill_extractor import SkillExtractor
        extractor = SkillExtractor()
        memories = [
            {"id": "1", "content": "部署步骤1 安装依赖 步骤2 初始化 步骤3 启动", "category": "devops"},
            {"id": "2", "content": "部署步骤1 检查环境 步骤2 执行部署 步骤3 验证", "category": "devops"},
        ]
        skills = extractor.extract(memories)
        self.assertIsInstance(skills, list)

    def test_empty_memories(self):
        """空记忆列表安全"""
        from modules.skill_extractor import SkillExtractor
        extractor = SkillExtractor()
        skills = extractor.extract([])
        self.assertEqual(len(skills), 0)


class TestHybridSearch(unittest.TestCase):
    """v5.3.9 混合检索增强测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mf_hybrid_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_query_expansion(self):
        """查询扩展"""
        from modules.hybrid_search import QueryExpander
        expander = QueryExpander()
        result = expander.expand("MySQL 部署")
        self.assertIsNotNone(result)
        self.assertGreater(len(result.expanded_terms), 0)

    def test_reranker(self):
        """Cross-Encoder 重排"""
        from modules.hybrid_search import CrossEncoderReranker
        reranker = CrossEncoderReranker()
        candidates = [
            {"id": "1", "content": "MySQL 部署成功", "importance": 0.8},
            {"id": "2", "content": "Python 安装教程", "importance": 0.5},
            {"id": "3", "content": "MySQL 启动失败 端口占用", "importance": 0.7},
        ]
        results = reranker.rerank("MySQL 部署启动", candidates)
        self.assertEqual(len(results), 3)
        # MySQL 相关结果应排在前面
        self.assertIn(results[0].memory_id, ["1", "3"])


class TestSessionFocus(unittest.TestCase):
    """v5.3.9 会话焦点测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mf_focus_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_focus_summary(self):
        """焦点摘要"""
        from modules.session_focus import SessionFocus
        sf = SessionFocus()
        messages = [
            {"id": "m1", "role": "user", "content": "MySQL 怎么部署?"},
            {"id": "m2", "role": "assistant", "content": "先安装再启动"},
            {"id": "m3", "role": "user", "content": "报错了端口占用"},
        ]
        summary = sf.summarize(messages)
        self.assertIsNotNone(summary)
        self.assertGreater(len(summary.focus_keywords), 0)

    def test_empty_messages(self):
        """空消息安全"""
        from modules.session_focus import SessionFocus
        sf = SessionFocus()
        summary = sf.summarize([])
        self.assertIsNotNone(summary)


# ===== v5.4.1 新增能力测试 =====

class TestMemoryReflection(unittest.TestCase):
    """v5.4.1 记忆反思测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_reflect_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _seed(self, storage, agent="agent-r"):
        from core.types import Importance, MemoryLayer
        items = [
            ("完成了数据库优化任务，效果很好", "work", ["优化", "数据库"], Importance.HIGH),
            ("修复了一个棘手的 bug，成功了", "work", ["bug", "优化"], Importance.MEDIUM),
            ("学习新的记忆算法，收获很大", "study", ["算法", "学习"], Importance.MEDIUM),
            ("开会讨论了产品方向，有冲突", "work", ["会议"], Importance.LOW),
            ("复习了遗忘曲线理论", "study", ["算法", "学习", "复习"], Importance.HIGH),
        ]
        for content, cat, tags, imp in items:
            storage.add_memory(content=content, category=cat, tags=tags,
                               importance=imp, layer=MemoryLayer.SHORT_TERM,
                               source_agent=agent)

    def test_reflection_structure(self):
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        self._seed(storage)
        result = storage.memory_reflection("agent-r", days=30)
        self.assertNotIn("error", result)
        self.assertEqual(result["total_memories"], 5)
        self.assertTrue(result["top_categories"])
        self.assertIn("dominant", result["emotional_tone"])
        self.assertTrue(result["reflection_summary"])
        self.assertIsInstance(result["suggestions"], list)

    def test_reflection_empty(self):
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.memory_reflection("no-such-agent", days=30)
        self.assertEqual(result["total_memories"], 0)
        self.assertEqual(result["emotional_tone"]["dominant"], "no_data")

    def test_reflection_empty_agent_id(self):
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.memory_reflection("", days=30)
        self.assertIn("error", result)


class TestMemoryLineage(unittest.TestCase):
    """v5.4.1 记忆血缘溯源测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_lineage_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_lineage_with_versions_and_links(self):
        from core.storage import StorageEngine
        from core.types import Importance, MemoryLayer
        storage = StorageEngine(db_path=self.db_path)

        e1 = storage.add_memory(content="主记忆", category="core",
                                layer=MemoryLayer.SHORT_TERM)
        e2 = storage.add_memory(content="关联记忆", category="core",
                                layer=MemoryLayer.SHORT_TERM)
        storage.link_memories(e1.id, e2.id, link_type="related", note="测试关联")
        storage.save_version(e1.id, "主记忆 v1", "core", ["t"], Importance.MEDIUM, actor="tester")

        result = storage.memory_lineage(e1.id)
        self.assertNotIn("error", result)
        self.assertEqual(result["memory_id"], e1.id)
        self.assertEqual(result["stats"]["version_count"], 1)
        self.assertEqual(result["stats"]["link_count_out"], 1)
        self.assertEqual(result["stats"]["link_count_in"], 0)
        self.assertTrue(any(ev["event"] == "created" for ev in result["lifecycle_timeline"]))
        self.assertTrue(any(ev["event"] == "version" for ev in result["lifecycle_timeline"]))

    def test_lineage_missing_memory(self):
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.memory_lineage("nonexistent-id")
        self.assertIn("error", result)

    def test_lineage_empty_id(self):
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.memory_lineage("")
        self.assertIn("error", result)


class TestMemoryReinforce(unittest.TestCase):
    """v5.4.1 记忆强化候选测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_reinforce_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_reinforce_ranks_high_value_first(self):
        from core.storage import StorageEngine
        from core.types import Importance, MemoryLayer
        storage = StorageEngine(db_path=self.db_path)

        # 高价值：CRITICAL + 星标
        storage.add_memory(content="关键的架构决策", category="arch",
                           importance=Importance.CRITICAL, starred=True,
                           layer=MemoryLayer.LONG_TERM, source_agent="agent-k")
        # 低价值
        storage.add_memory(content="随手记一条", category="misc",
                           importance=Importance.LOW,
                           layer=MemoryLayer.SHORT_TERM, source_agent="agent-k")

        result = storage.memory_reinforce("agent-k", days=90, limit=10)
        self.assertNotIn("error", result)
        self.assertEqual(result["total_scanned"], 2)
        self.assertEqual(len(result["candidates"]), 2)
        top = result["candidates"][0]
        self.assertEqual(top["importance"], "CRITICAL")
        self.assertIn(top["recommended_action"],
                      ("priority_review", "schedule_review", "keep_monitoring", "promote_importance"))
        self.assertTrue(top["reasons"])
        # 排序正确性：第一条分数 >= 第二条
        self.assertGreaterEqual(result["candidates"][0]["reinforce_score"],
                                result["candidates"][1]["reinforce_score"])

    def test_reinforce_empty(self):
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.memory_reinforce("empty-agent")
        self.assertEqual(result["total_scanned"], 0)
        self.assertEqual(result["candidates"], [])


class TestDramaPlotThread(unittest.TestCase):
    """v5.4.1 剧情伏笔线索追踪测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_thread_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_drama(self, storage):
        from core.types import DramaGenre, DramaStatus
        d = storage.add_drama(title="测试短剧", genre=DramaGenre.SUSPENSE,
                              total_episodes=3, status=DramaStatus.WATCHING)
        # EP1 埋设伏笔
        s1 = storage.add_scene(d.id, 1, 1, "神秘信物", "主角埋下一个秘密伏笔")
        # EP2 再埋一个
        s2 = storage.add_scene(d.id, 2, 1, "未解之谜", "出现新的线索与悬念")
        # EP3 回收第一个
        s3 = storage.add_scene(d.id, 3, 1, "真相大白", "终于揭晓真相，秘密被揭开")
        return d

    def test_plot_thread_detection(self):
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        d = self._make_drama(storage)
        result = storage.drama_plot_thread(d.id)
        self.assertNotIn("error", result)
        self.assertEqual(result["total_scenes"], 3)
        self.assertGreaterEqual(len(result["threads"]), 1)
        self.assertGreater(result["resolved_count"], 0)
        self.assertGreater(result["resolution_rate"], 0)

    def test_plot_thread_missing_drama(self):
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.drama_plot_thread("no-such-drama")
        self.assertIn("error", result)

    def test_plot_thread_no_scenes(self):
        from core.storage import StorageEngine
        from core.types import DramaGenre
        storage = StorageEngine(db_path=self.db_path)
        d = storage.add_drama(title="空剧", genre=DramaGenre.OTHER)
        result = storage.drama_plot_thread(d.id)
        self.assertEqual(result["total_scenes"], 0)
        self.assertEqual(result["threads"], [])


class TestDramaEpisodeCurve(unittest.TestCase):
    """v5.4.1 分集张力曲线测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_curve_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_episode_curve_shape(self):
        from core.storage import StorageEngine
        from core.types import DramaGenre
        storage = StorageEngine(db_path=self.db_path)
        d = storage.add_drama(title="张力剧", genre=DramaGenre.ACTION, total_episodes=3)
        c1 = storage.add_character(d.id, "主角", role="lead")
        # EP1 平淡，EP3 高冲突
        s1 = storage.add_scene(d.id, 1, 1, "开场")
        s3 = storage.add_scene(d.id, 3, 1, "决战")
        storage.add_line(d.id, "今天天气不错", scene_id=s1.id, character_id=c1.id, episode=1)
        for txt in ("你必须马上离开！", "不！我要战斗到底！", "危险！快跑！"):
            storage.add_line(d.id, txt, scene_id=s3.id, character_id=c1.id, episode=3)

        result = storage.drama_episode_curve(d.id)
        self.assertNotIn("error", result)
        self.assertGreaterEqual(len(result["curve"]), 2)
        # EP3 张力应高于 EP1
        ep3 = next(p for p in result["curve"] if p["episode"] == 3)
        ep1 = next(p for p in result["curve"] if p["episode"] == 1)
        self.assertGreater(ep3["tension"], ep1["tension"])
        self.assertEqual(result["climax_episode"], 3)
        self.assertIn(result["shape"], ("rising", "falling", "mid_peak", "steady"))

    def test_episode_curve_no_data(self):
        from core.storage import StorageEngine
        from core.types import DramaGenre
        storage = StorageEngine(db_path=self.db_path)
        d = storage.add_drama(title="空剧", genre=DramaGenre.OTHER)
        result = storage.drama_episode_curve(d.id)
        self.assertEqual(result["shape"], "no_data")
        self.assertIsNone(result["climax_episode"])


class TestDramaScreenTime(unittest.TestCase):
    """v5.4.1 角色戏份平衡测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_screen_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_screen_time_balance(self):
        from core.storage import StorageEngine
        from core.types import DramaGenre
        storage = StorageEngine(db_path=self.db_path)
        d = storage.add_drama(title="群像剧", genre=DramaGenre.DRAMA, total_episodes=1)
        lead = storage.add_character(d.id, "主角", role="lead")
        sup = storage.add_character(d.id, "配角", role="supporting")
        s1 = storage.add_scene(d.id, 1, 1, "对手戏")
        # 主角 3 句，配角 1 句
        for txt in ("第一句", "第二句", "第三句"):
            storage.add_line(d.id, txt, scene_id=s1.id, character_id=lead.id, episode=1)
        storage.add_line(d.id, "我也说一句", scene_id=s1.id, character_id=sup.id, episode=1)

        result = storage.drama_screen_time(d.id)
        self.assertNotIn("error", result)
        self.assertEqual(result["total_lines"], 4)
        self.assertEqual(len(result["characters"]), 2)
        top = result["characters"][0]
        self.assertEqual(top["name"], "主角")
        self.assertEqual(top["line_count"], 3)
        self.assertIn(result["balance"]["structure"], ("one_lead", "dual_lead", "ensemble"))
        self.assertGreaterEqual(result["balance"]["gini_coefficient"], 0.0)

    def test_screen_time_no_characters(self):
        from core.storage import StorageEngine
        from core.types import DramaGenre
        storage = StorageEngine(db_path=self.db_path)
        d = storage.add_drama(title="无人剧", genre=DramaGenre.OTHER)
        result = storage.drama_screen_time(d.id)
        self.assertEqual(result["characters"], [])
        self.assertEqual(result["total_lines"], 0)


class TestContentLengthGuard(unittest.TestCase):
    """v5.4.1 安全修复：update/batch_add 内容长度校验"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_guard_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_update_memory_rejects_oversized(self):
        from core.storage import StorageEngine, MAX_CONTENT_LEN
        storage = StorageEngine(db_path=self.db_path)
        entry = storage.add_memory(content="正常内容")
        oversized = "x" * (MAX_CONTENT_LEN + 1)
        with self.assertRaises(ValueError):
            storage.update_memory(entry.id, content=oversized)

    def test_batch_add_skips_oversized(self):
        from core.storage import StorageEngine, MAX_CONTENT_LEN
        storage = StorageEngine(db_path=self.db_path)
        entries = [
            {"content": "正常条目"},
            {"content": "y" * (MAX_CONTENT_LEN + 1)},  # 超长，应被跳过
        ]
        added = storage.batch_add(entries)
        self.assertEqual(added, 1)
        self.assertEqual(storage.count_memories(), 1)

    def test_add_memory_still_guarded(self):
        from core.storage import StorageEngine, MAX_CONTENT_LEN
        storage = StorageEngine(db_path=self.db_path)
        with self.assertRaises(ValueError):
            storage.add_memory(content="z" * (MAX_CONTENT_LEN + 1))


# ===== v5.4.2 新增能力测试 =====

class TestFederatedACL(unittest.TestCase):
    """v5.4.2 联邦记忆细粒度 ACL 测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_acl_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_default_deny(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        acl = FederatedACLManager(storage)
        result = acl.check_access("peerA", "mem1", "read")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["effect"], "deny")
        self.assertIsNone(result["matched_rule"])

    def test_allow_rule_grants_access(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        acl = FederatedACLManager(storage)
        add = acl.add_rule("peerA", "memory:mem1", "read", effect="allow")
        self.assertTrue(add["success"])
        result = acl.check_access("peerA", "mem1", "read")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["matched_rule"], add["rule_id"])
        # 其他 peer 不受影响（仍默认拒绝）
        self.assertFalse(acl.check_access("peerB", "mem1", "read")["allowed"])
        # 其他操作不受影响
        self.assertFalse(acl.check_access("peerA", "mem1", "write")["allowed"])

    def test_deny_overrides_at_same_priority(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        acl = FederatedACLManager(storage)
        acl.add_rule("peerA", "all", "read", effect="allow", priority=100)
        acl.add_rule("peerA", "all", "read", effect="deny", priority=100)
        result = acl.check_access("peerA", "mem1", "read")
        self.assertFalse(result["allowed"])

    def test_higher_priority_wins(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        acl = FederatedACLManager(storage)
        acl.add_rule("peerA", "all", "read", effect="deny", priority=50)
        acl.add_rule("peerA", "all", "read", effect="allow", priority=200)
        result = acl.check_access("peerA", "mem1", "read")
        self.assertTrue(result["allowed"])

    def test_expired_rule_ignored(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        acl = FederatedACLManager(storage)
        acl.add_rule("peerA", "all", "read", effect="allow", expires_hours=-1)
        result = acl.check_access("peerA", "mem1", "read")
        self.assertFalse(result["allowed"])

    def test_trust_min_enforced(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        acl = FederatedACLManager(storage)
        acl.add_rule("peerA", "all", "read", effect="allow", trust_min=0.7)
        # 低信任：规则不生效 → 默认拒绝
        self.assertFalse(acl.check_access("peerA", "m", "read", peer_trust=0.5)["allowed"])
        # 高信任：规则生效
        self.assertTrue(acl.check_access("peerA", "m", "read", peer_trust=0.9)["allowed"])

    def test_category_and_tag_resources(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        acl = FederatedACLManager(storage)
        acl.add_rule("peerA", "category:work", "read", effect="allow")
        self.assertTrue(acl.check_access("peerA", "m", "read",
                                         memory_category="work")["allowed"])
        self.assertFalse(acl.check_access("peerA", "m", "read",
                                          memory_category="life")["allowed"])
        acl.add_rule("peerB", "tag:secret", "read", effect="allow")
        self.assertTrue(acl.check_access("peerB", "m", "read",
                                         memory_tags=["Secret"])["allowed"])
        self.assertFalse(acl.check_access("peerB", "m", "read",
                                          memory_tags=["public"])["allowed"])

    def test_filter_peers(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        acl = FederatedACLManager(storage)
        acl.add_rule("peerA", "all", "read", effect="allow")
        verdict = acl.filter_peers("m1", ["peerA", "peerB", "peerC"], "read")
        self.assertEqual(verdict["allowed"], ["peerA"])
        self.assertEqual(verdict["denied_count"], 2)

    def test_remove_rule_and_stats(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        acl = FederatedACLManager(storage)
        add = acl.add_rule("peerA", "all", "read", effect="allow")
        self.assertTrue(acl.check_access("peerA", "m", "read")["allowed"])
        self.assertTrue(acl.remove_rule(add["rule_id"])["success"])
        self.assertFalse(acl.check_access("peerA", "m", "read")["allowed"])
        stats = acl.acl_stats()
        self.assertEqual(stats["total_rules"], 0)
        self.assertGreaterEqual(stats["deny_audit_events"], 1)

    def test_invalid_inputs(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        acl = FederatedACLManager(storage)
        self.assertFalse(acl.add_rule("", "all")["success"])
        self.assertFalse(acl.add_rule("p", "badresource")["success"])
        self.assertFalse(acl.add_rule("p", "all", "fly")["success"])
        self.assertFalse(acl.add_rule("p", "all", "read", effect="maybe")["success"])


class TestFederatedShareFiltering(unittest.TestCase):
    """v5.4.2 修复：share_memory 信任过滤 + ACL 强制"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_fedshare_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_low_trust_peer_filtered(self):
        from core.storage import StorageEngine
        from modules.federated import FederatedMemory
        storage = StorageEngine(db_path=self.db_path)
        entry = storage.add_memory(content="待共享记忆")
        fed = FederatedMemory(storage=storage, local_peer_id="local")
        fed.register_peer("trusted", "可信节点", trust_level=0.8)
        fed.register_peer("untrusted", "低信任节点", trust_level=0.1)
        shared = fed.share_memory(entry.id, ["trusted", "untrusted", "ghost"])
        self.assertIsNotNone(shared)
        self.assertEqual(shared.shared_with, ["trusted"])
        self.assertIn("untrusted", fed.last_share_skipped)
        self.assertIn("ghost", fed.last_share_skipped)

    def test_acl_blocks_share(self):
        from core.storage import StorageEngine
        from modules.federated import FederatedMemory
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path)
        entry = storage.add_memory(content="受控记忆")
        acl = FederatedACLManager(storage)
        fed = FederatedMemory(storage=storage, local_peer_id="local", acl=acl)
        fed.register_peer("peerA", "节点A", trust_level=0.8)
        # 无 allow 规则 → 默认拒绝 → 无合格节点
        self.assertIsNone(fed.share_memory(entry.id, ["peerA"]))
        # 加 allow 规则后可共享
        acl.add_rule("peerA", f"memory:{entry.id}", "read", effect="allow")
        shared = fed.share_memory(entry.id, ["peerA"])
        self.assertIsNotNone(shared)
        self.assertEqual(shared.shared_with, ["peerA"])

    def test_accept_incoming_conflict_manual(self):
        from core.storage import StorageEngine
        from modules.federated import FederatedMemory
        from modules.share_conflict import SharedConflictResolver
        storage = StorageEngine(db_path=self.db_path)
        resolver = SharedConflictResolver(storage)
        # P1-004: 测试聚焦于冲突解决功能，显式启用未签名节点以隔离测试范围
        fed = FederatedMemory(storage=storage, local_peer_id="local",
                              conflict_resolver=resolver,
                              config={"allow_unsigned_peers": True})
        local_entry = storage.add_memory(content="本地版本")
        fed.register_peer("peerA", "节点A", trust_level=0.9)
        ok = fed.receive_memory("peerA", {
            "memory_id": local_entry.id,
            "content": "远端不同版本",
            "version": 1,
        })
        self.assertTrue(ok)
        # manual 策略：冲突挂起，不入新库
        result_id = fed.accept_incoming(resolve_strategy="manual")
        self.assertIsNone(result_id)
        conflicts = resolver.list_conflicts(status="open")
        self.assertEqual(len(conflicts), 1)
        # 本地内容未被改动
        self.assertEqual(storage.get_memory(local_entry.id).content, "本地版本")


class TestShareConflict(unittest.TestCase):
    """v5.4.2 共享记忆冲突解决测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_cfl_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make(self):
        from core.storage import StorageEngine
        from modules.share_conflict import SharedConflictResolver
        storage = StorageEngine(db_path=self.db_path)
        return storage, SharedConflictResolver(storage)

    def test_detect_new_and_noop(self):
        storage, resolver = self._make()
        # 本地无对应记忆 → new
        det = resolver.detect_incoming({"memory_id": "no-such", "content": "x"})
        self.assertFalse(det["conflict"])
        self.assertEqual(det["action"], "new")
        # 内容一致 → noop
        entry = storage.add_memory(content="一致内容")
        det = resolver.detect_incoming({"memory_id": entry.id, "content": "一致内容"})
        self.assertFalse(det["conflict"])
        self.assertEqual(det["action"], "noop")
        self.assertEqual(det["local_memory_id"], entry.id)

    def test_detect_conflict_and_stats(self):
        storage, resolver = self._make()
        entry = storage.add_memory(content="本地版本")
        det = resolver.detect_incoming({
            "memory_id": entry.id, "content": "远端版本",
            "version": 3, "from_peer": "peerA"})
        self.assertTrue(det["conflict"])
        self.assertIn(det["conflict_type"], ("content", "version"))
        stats = resolver.stats()
        self.assertEqual(stats["total_conflicts"], 1)
        self.assertEqual(stats["open"], 1)

    def test_resolve_lww_incoming_wins(self):
        storage, resolver = self._make()
        entry = storage.add_memory(content="旧的本地内容")
        det = resolver.detect_incoming({
            "memory_id": entry.id, "content": "更新的远端内容",
            "version": 5, "timestamp": 9999999999.0, "from_peer": "peerA"})
        result = resolver.resolve(det["conflict_id"], "lww", actor="tester")
        self.assertTrue(result["success"])
        self.assertEqual(result["resolution"], "lww_incoming")
        self.assertEqual(result["resolved_memory_id"], entry.id)
        self.assertEqual(storage.get_memory(entry.id).content, "更新的远端内容")
        # 旧版本已备份到版本历史
        versions = storage.list_versions(entry.id)
        self.assertGreaterEqual(len(versions), 1)

    def test_resolve_lww_local_wins(self):
        storage, resolver = self._make()
        entry = storage.add_memory(content="本地新内容")
        det = resolver.detect_incoming({
            "memory_id": entry.id, "content": "远端旧内容",
            "version": 0, "timestamp": 1.0, "from_peer": "peerA"})
        result = resolver.resolve(det["conflict_id"], "lww")
        self.assertTrue(result["success"])
        self.assertEqual(result["resolution"], "lww_local")
        self.assertEqual(storage.get_memory(entry.id).content, "本地新内容")

    def test_resolve_keep_both(self):
        storage, resolver = self._make()
        entry = storage.add_memory(content="本地版本")
        det = resolver.detect_incoming({
            "memory_id": entry.id, "content": "远端分支版本", "from_peer": "peerB"})
        result = resolver.resolve(det["conflict_id"], "keep_both")
        self.assertTrue(result["success"])
        self.assertEqual(result["resolution"], "keep_both")
        branch_id = result["resolved_memory_id"]
        self.assertNotEqual(branch_id, entry.id)
        branch = storage.get_memory(branch_id)
        self.assertEqual(branch.content, "远端分支版本")
        self.assertIn("conflict:branch", branch.tags)
        # 本地原记忆不变
        self.assertEqual(storage.get_memory(entry.id).content, "本地版本")
        # 建立了关联
        links = storage.list_links(entry.id)
        self.assertTrue(any(l.get("link_type") == "conflict_branch" for l in links))

    def test_dismiss_and_double_resolve(self):
        storage, resolver = self._make()
        entry = storage.add_memory(content="本地")
        det = resolver.detect_incoming({"memory_id": entry.id, "content": "远端"})
        # 重复解决前先测 dismiss 的另一条
        entry2 = storage.add_memory(content="本地2")
        det2 = resolver.detect_incoming({"memory_id": entry2.id, "content": "远端2"})
        self.assertTrue(resolver.dismiss(det2["conflict_id"])["success"])
        self.assertFalse(resolver.dismiss(det2["conflict_id"])["success"])
        # 解决后再解决应失败
        self.assertTrue(resolver.resolve(det["conflict_id"], "lww")["success"])
        self.assertFalse(resolver.resolve(det["conflict_id"], "lww")["success"])
        stats = resolver.stats()
        self.assertEqual(stats["resolved"], 1)
        self.assertEqual(stats["dismissed"], 1)

    def test_invalid_strategy_and_missing(self):
        storage, resolver = self._make()
        self.assertFalse(resolver.resolve("cfl_none", "merge")["success"])
        self.assertFalse(resolver.resolve("cfl_none", "lww")["success"])
        self.assertFalse(resolver.dismiss("cfl_none")["success"])




# ===== v5.4.3 新增功能测试 =====

class TestAgentInfluenceMap(unittest.TestCase):
    """v5.4.3: Agent 记忆影响力图谱测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_influence_map_empty_agent(self):
        """空 Agent 应返回空图谱"""
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.agent_influence_map("nonexistent_agent", days=30)
        self.assertEqual(result["total_nodes"], 1)
        self.assertEqual(result["total_edges"], 0)
        storage.close()

    def test_influence_map_invalid_input(self):
        """无效输入应返回错误"""
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.agent_influence_map("", days=30)
        self.assertIn("error", result)
        storage.close()

    def test_influence_map_with_data(self):
        """有数据的 Agent 应返回影响力图谱"""
        from core.storage import StorageEngine
        from core.types import MemoryLayer
        storage = StorageEngine(db_path=self.db_path)

        storage.add_memory(content="Agent A 的记忆", category="tech",
                           tags=["python", "ai"], layer=MemoryLayer.SHORT_TERM,
                           source_agent="agent_a")
        storage.add_memory(content="Agent B 的记忆", category="tech",
                           tags=["python", "web"], layer=MemoryLayer.SHORT_TERM,
                           source_agent="agent_b")

        result = storage.agent_influence_map("agent_a", days=365)
        self.assertNotIn("error", result)
        self.assertIn("total_nodes", result)
        self.assertIn("influence_score", result)
        storage.close()


class TestMemoryOverlap(unittest.TestCase):
    """v5.4.3: 记忆重叠分析测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_overlap_identical_agents_error(self):
        """相同 Agent ID 应返回错误"""
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.memory_overlap("agent_a", "agent_a", days=30)
        self.assertIn("error", result)
        storage.close()

    def test_overlap_with_shared_tags(self):
        """有共享标签的 Agent 应返回重叠结果"""
        from core.storage import StorageEngine
        from core.types import MemoryLayer
        storage = StorageEngine(db_path=self.db_path)

        storage.add_memory(content="Python 编程技巧", category="tech",
                           tags=["python", "ai"], layer=MemoryLayer.SHORT_TERM,
                           source_agent="agent_a")
        storage.add_memory(content="Python Web 开发", category="tech",
                           tags=["python", "web"], layer=MemoryLayer.SHORT_TERM,
                           source_agent="agent_b")

        result = storage.memory_overlap("agent_a", "agent_b", days=365)
        self.assertNotIn("error", result)
        self.assertIn("tags", result)
        self.assertIn("python", result["tags"]["shared"])
        self.assertIn("overall_similarity", result)
        storage.close()

    def test_overlap_no_shared_data(self):
        """无共享数据的 Agent 应返回低相似度"""
        from core.storage import StorageEngine
        from core.types import MemoryLayer
        storage = StorageEngine(db_path=self.db_path)

        storage.add_memory(content="量子物理研究", category="physics",
                           tags=["quantum", "physics"], layer=MemoryLayer.SHORT_TERM,
                           source_agent="agent_a")
        storage.add_memory(content="美食烹饪指南", category="cooking",
                           tags=["recipe", "food"], layer=MemoryLayer.SHORT_TERM,
                           source_agent="agent_b")

        result = storage.memory_overlap("agent_a", "agent_b", days=365)
        self.assertNotIn("error", result)
        self.assertEqual(result["similarity_level"], "low")
        storage.close()


class TestConflictGraph(unittest.TestCase):
    """v5.4.3: 记忆冲突检测图测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_conflict_graph_empty(self):
        """空 Agent 应返回零冲突"""
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.conflict_graph("nonexistent_agent", days=30)
        self.assertEqual(result["conflict_count"], 0)
        self.assertEqual(result["conflict_density"], 0.0)
        storage.close()

    def test_conflict_graph_with_conflicts(self):
        """有冲突标签的记忆应检测到冲突"""
        from core.storage import StorageEngine
        from core.types import MemoryLayer, Importance
        storage = StorageEngine(db_path=self.db_path)

        storage.add_memory(content="Python is great for data science",
                           category="tech", tags=["python", "programming"],
                           importance=Importance.CRITICAL,
                           layer=MemoryLayer.SHORT_TERM, source_agent="agent_a")
        storage.add_memory(content="Python is bad for data science performance",
                           category="tech", tags=["python", "programming"],
                           importance=Importance.LOW,
                           layer=MemoryLayer.SHORT_TERM, source_agent="agent_a")

        result = storage.conflict_graph("agent_a", days=365)
        self.assertNotIn("error", result)
        self.assertGreater(result["total_memories"], 0)
        self.assertIn("severity_distribution", result)
        storage.close()


class TestDramaQuoteMap(unittest.TestCase):
    """v5.4.3: 经典台词地图测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_quote_map_nonexistent_drama(self):
        """不存在的短剧应返回错误"""
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.drama_quote_map("nonexistent")
        self.assertIn("error", result)
        storage.close()

    def test_quote_map_with_data(self):
        """有台词数据的短剧应返回台词地图"""
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)

        drama = storage.add_drama(title="测试短剧", total_episodes=5)
        did = drama.id
        char = storage.add_character(drama_id=did, name="主角", role="protagonist")
        cid = char.id
        storage.add_line(drama_id=did, scene_id="scene_1",
                         character_id=cid, character_name="主角",
                         line_text="这是经典台词", episode=1, is_classic=True)
        storage.add_line(drama_id=did, scene_id="scene_1",
                         character_id=cid, character_name="主角",
                         line_text="这是普通台词", episode=1, is_classic=False)

        result = storage.drama_quote_map(did)
        self.assertNotIn("error", result)
        self.assertEqual(result["total_lines"], 2)
        self.assertEqual(result["classic_count"], 1)
        self.assertIn("density_rating", result)
        storage.close()


class TestCharacterGrowth(unittest.TestCase):
    """v5.4.3: 角色成长深度分析测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_growth_nonexistent_character(self):
        """不存在的角色应返回错误"""
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.character_growth("nonexistent", "nonexistent")
        self.assertIn("error", result)
        storage.close()

    def test_growth_with_data(self):
        """有台词数据的角色应返回成长分析"""
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)

        drama = storage.add_drama(title="成长短剧", total_episodes=6)
        did = drama.id
        char = storage.add_character(drama_id=did, name="主角", role="protagonist")
        cid = char.id

        for ep in range(1, 4):
            storage.add_line(drama_id=did, scene_id=f"scene_{ep}",
                             character_id=cid, character_name="主角",
                             line_text=f"第{ep}集的台词 content word test",
                             episode=ep)

        result = storage.character_growth(did, cid)
        self.assertNotIn("error", result)
        self.assertEqual(result["character_name"], "主角")
        self.assertGreater(result["total_lines"], 0)
        self.assertIn("growth_score", result)
        self.assertIn("growth_summary", result)
        storage.close()


class TestSceneRhythm(unittest.TestCase):
    """v5.4.3: 场景节奏分析测试"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_rhythm_nonexistent_drama(self):
        """不存在的短剧应返回错误"""
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        result = storage.scene_rhythm("nonexistent")
        self.assertIn("error", result)
        storage.close()

    def test_rhythm_with_data(self):
        """有场景数据的短剧应返回节奏分析"""
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)

        drama = storage.add_drama(title="节奏短剧", total_episodes=3)
        did = drama.id
        scene1 = storage.add_scene(drama_id=did, episode=1, scene_number=1,
                                    title="开场", content="场景描述" * 10)
        scene2 = storage.add_scene(drama_id=did, episode=1, scene_number=2,
                                    title="高潮", content="高潮场景" * 5)

        for i in range(15):
            storage.add_line(drama_id=did, scene_id=scene1.id,
                             character_id="char_x", character_name="主角",
                             line_text=f"台词{i}", episode=1)
        for i in range(3):
            storage.add_line(drama_id=did, scene_id=scene2.id,
                             character_id="char_x", character_name="主角",
                             line_text=f"台词{i}", episode=1)

        result = storage.scene_rhythm(did)
        self.assertNotIn("error", result)
        self.assertEqual(result["total_scenes"], 2)
        self.assertIn("overall_pace", result)
        self.assertIn("pace_distribution", result)
        self.assertIn("suggestions", result)
        storage.close()
if __name__ == "__main__":
    unittest.main(verbosity=2)
