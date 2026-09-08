#!/usr/bin/env python3
"""
MindForge v5.6.0 衰减/GC 单元测试

覆盖：
1. ForgettingCurve / DecayConfig 数学模型
2. MemoryDecayEngine.compute_strength 衰减计算
3. 保护规则（PERMANENT/CRITICAL/最近创建）
4. archive_decayed 归档流程
5. purge_old_archives 永久删除
6. run_gc 完整 GC 周期
7. dry-run 预览模式
8. 三套衰减策略
"""

import os
import sys
import tempfile
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.storage import StorageEngine, MemoryEntry
from core.types import MemoryLayer, Importance
from modules.memory_decay import (
    MemoryDecayEngine, DecayConfig, DecayPolicy,
)


class TestDecayConfig(unittest.TestCase):

    def test_default_config_is_balanced(self):
        """默认配置是 balanced 策略"""
        config = DecayConfig()
        self.assertEqual(config.policy, DecayPolicy.BALANCED)
        self.assertEqual(config.decay_rate, 0.05)
        self.assertEqual(config.archive_threshold, 0.15)

    def test_from_policy_conservative(self):
        """conservative 策略参数更保守"""
        config = DecayConfig.from_policy(DecayPolicy.CONSERVATIVE)
        self.assertEqual(config.policy, DecayPolicy.CONSERVATIVE)
        self.assertLess(config.decay_rate, 0.05)
        self.assertLess(config.archive_threshold, 0.15)
        self.assertGreater(config.purge_after_days, 90)

    def test_from_policy_aggressive(self):
        """aggressive 策略参数更激进"""
        config = DecayConfig.from_policy(DecayPolicy.AGGRESSIVE)
        self.assertEqual(config.policy, DecayPolicy.AGGRESSIVE)
        self.assertGreater(config.decay_rate, 0.05)
        self.assertGreater(config.archive_threshold, 0.15)
        self.assertLess(config.purge_after_days, 90)

    def test_to_dict_serialization(self):
        """to_dict 序列化完整"""
        config = DecayConfig.from_policy(DecayPolicy.AGGRESSIVE)
        d = config.to_dict()
        self.assertEqual(d["policy"], "aggressive")
        self.assertIn("decay_rate", d)
        self.assertIn("archive_threshold", d)
        self.assertIn("purge_after_days", d)
        self.assertIn("importance_multiplier", d)


class TestComputeStrength(unittest.TestCase):
    """衰减强度计算"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.storage = StorageEngine(db_path=self.db_path, encrypted=False)
        self.engine = MemoryDecayEngine(
            storage=self.storage,
            config=DecayConfig.from_policy(DecayPolicy.BALANCED),
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_fresh_memory_full_strength(self):
        """刚创建的记忆强度为 1.0"""
        entry = self.storage.add_memory("fresh memory", category="test")
        # 模拟刚创建
        entry.last_accessed_at = time.time()
        entry.strength = 1.0
        strength = self.engine.compute_strength(entry)
        self.assertGreater(strength, 0.99)

    def test_decayed_memory_lower_strength(self):
        """长时间未访问的记忆强度下降"""
        entry = self.storage.add_memory("old memory", category="test")
        entry.last_accessed_at = time.time() - 3600 * 24  # 24 小时前
        entry.strength = 1.0
        strength = self.engine.compute_strength(entry)
        self.assertLess(strength, 0.5)
        self.assertGreater(strength, 0.01)

    def test_critical_importance_decays_slower(self):
        """CRITICAL 重要性衰减更慢"""
        entry_low = MemoryEntry(
            id="low-1", content="low importance", category="test",
            importance=Importance.LOW, layer=MemoryLayer.SHORT_TERM,
            created_at=time.time() - 3600 * 48,
            last_accessed_at=time.time() - 3600 * 48,
            strength=1.0, access_count=0, consolidation_count=0,
        )
        entry_critical = MemoryEntry(
            id="crit-1", content="critical", category="test",
            importance=Importance.CRITICAL, layer=MemoryLayer.SHORT_TERM,
            created_at=time.time() - 3600 * 48,
            last_accessed_at=time.time() - 3600 * 48,
            strength=1.0, access_count=0, consolidation_count=0,
        )
        strength_low = self.engine.compute_strength(entry_low)
        strength_critical = self.engine.compute_strength(entry_critical)
        self.assertGreater(strength_critical, strength_low)

    def test_consolidation_count_slows_decay(self):
        """巩固次数越多衰减越慢"""
        base_time = time.time() - 3600 * 24
        entry_fresh = MemoryEntry(
            id="fresh", content="test", category="test",
            importance=Importance.MEDIUM, layer=MemoryLayer.SHORT_TERM,
            created_at=base_time, last_accessed_at=base_time,
            strength=1.0, access_count=0, consolidation_count=0,
        )
        entry_consolidated = MemoryEntry(
            id="consol", content="test", category="test",
            importance=Importance.MEDIUM, layer=MemoryLayer.LONG_TERM,
            created_at=base_time, last_accessed_at=base_time,
            strength=1.0, access_count=0, consolidation_count=5,
        )
        s1 = self.engine.compute_strength(entry_fresh)
        s2 = self.engine.compute_strength(entry_consolidated)
        self.assertGreater(s2, s1)

    def test_strength_bounded(self):
        """强度始终在 [0.01, 1.0] 范围内"""
        entry = MemoryEntry(
            id="extreme", content="test", category="test",
            importance=Importance.LOW, layer=MemoryLayer.SENSORY,
            created_at=time.time() - 3600 * 1000,
            last_accessed_at=time.time() - 3600 * 1000,
            strength=1.0, access_count=0, consolidation_count=0,
        )
        strength = self.engine.compute_strength(entry)
        self.assertGreaterEqual(strength, 0.01)
        self.assertLessEqual(strength, 1.0)


class TestProtectionRules(unittest.TestCase):
    """保护规则测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.storage = StorageEngine(db_path=self.db_path, encrypted=False)
        self.engine = MemoryDecayEngine(storage=self.storage)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_permanent_layer_protected(self):
        """PERMANENT 层记忆受保护，不归档"""
        entry = self.storage.add_memory(
            "permanent memory", category="important",
            layer=MemoryLayer.PERMANENT,
        )
        now = time.time()
        is_protected = self.engine._is_protected(
            self.storage.get_memory(entry.id) or entry, now
        )
        self.assertTrue(is_protected)

    def test_critical_importance_protected(self):
        """CRITICAL 重要性记忆受保护"""
        entry = self.storage.add_memory(
            "critical memory", category="alert",
            importance=Importance.CRITICAL,
        )
        now = time.time()
        result = self.engine._is_protected(
            self.storage.get_memory(entry.id) or entry, now
        )
        self.assertTrue(result)

    def test_recent_memory_protected(self):
        """最近创建的记忆受保护"""
        entry = self.storage.add_memory("recent", category="test")
        now = time.time()
        is_protected = self.engine._is_protected(
            self.storage.get_memory(entry.id) or entry, now
        )
        self.assertTrue(is_protected)

    def test_old_low_importance_not_protected(self):
        """旧的 LOW 重要性记忆不受保护"""
        entry = self.storage.add_memory(
            "old low memory", category="test",
            importance=Importance.LOW,
        )
        # 修改创建时间为很久以前
        self.storage.update_memory(
            entry_id=entry.id,
            metadata={
                **entry.metadata,
                "created_at": time.time() - 3600 * 48,
                "last_accessed_at": time.time() - 3600 * 48,
            }
        )
        now = time.time()
        updated = self.storage.get_memory(entry.id) or entry
        # 手动设置属性
        updated.created_at = time.time() - 3600 * 48
        updated.last_accessed_at = time.time() - 3600 * 48
        is_protected = self.engine._is_protected(updated, now)
        self.assertFalse(is_protected)


class TestArchiveDecayed(unittest.TestCase):
    """归档流程测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.storage = StorageEngine(db_path=self.db_path, encrypted=False)
        self.config = DecayConfig.from_policy(DecayPolicy.AGGRESSIVE)
        self.engine = MemoryDecayEngine(
            storage=self.storage, config=self.config
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_archive_decayed_dry_run(self):
        """dry-run 模式不实际归档"""
        # 添加几条记忆，并直接用 SQL 设旧时间
        old_time = time.time() - 3600 * 100
        for i in range(5):
            entry = self.storage.add_memory(f"memory {i}", category="test")
            conn = self.storage._get_conn()
            conn.execute(
                "UPDATE memories SET last_accessed_at = ?, created_at = ? WHERE id = ?",
                (old_time, old_time, entry.id),
            )
            conn.commit()

        result = self.engine.archive_decayed(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["archived"], 0)
        self.assertGreater(result["candidates"], 0)

    def test_archive_decayed_actually_archives(self):
        """实际执行时归档低强度记忆"""
        # 添加旧记忆，用 SQL 设旧时间
        old_time = time.time() - 3600 * 200
        entry = self.storage.add_memory("old decayed", category="test")
        conn = self.storage._get_conn()
        conn.execute(
            "UPDATE memories SET last_accessed_at = ?, created_at = ? WHERE id = ?",
            (old_time, old_time, entry.id),
        )
        conn.commit()

        result = self.engine.archive_decayed(dry_run=False)
        self.assertGreater(result["archived"], 0)

        # 验证记忆已在归档表中
        archived = self.storage.list_archived(limit=100)
        self.assertGreater(len(archived), 0)

    def test_protected_memories_not_archived(self):
        """受保护的记忆不被归档"""
        entry = self.storage.add_memory(
            "protected", category="test",
            importance=Importance.CRITICAL,
        )
        self.storage.update_memory(
            entry_id=entry.id,
            metadata={
                **entry.metadata,
                "last_accessed_at": time.time() - 3600 * 200,
                "created_at": time.time() - 3600 * 200,
            }
        )

        result = self.engine.archive_decayed(dry_run=True)
        self.assertGreater(result["protected"], 0)
        # CRITICAL 记忆不在候选列表中
        self.assertEqual(result["candidates"], 0)


class TestPurgeOldArchives(unittest.TestCase):
    """永久删除测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.storage = StorageEngine(db_path=self.db_path, encrypted=False)
        self.config = DecayConfig.from_policy(DecayPolicy.BALANCED)
        self.engine = MemoryDecayEngine(
            storage=self.storage, config=self.config
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_purge_dry_run(self):
        """dry-run 模式不实际删除"""
        # 先归档一条记忆
        entry = self.storage.add_memory("to archive", category="test")
        self.storage.archive_memories_by_ids([entry.id], reason="decayed")

        result = self.engine.purge_old_archives(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["purged"], 0)

    def test_purge_with_high_retention(self):
        """高保留天数不删除新归档"""
        entry = self.storage.add_memory("recently archived", category="test")
        self.storage.archive_memories_by_ids([entry.id], reason="decayed")

        # 设置保留 365 天，刚归档的不应该被删
        self.config.purge_after_days = 365
        result = self.engine.purge_old_archives(dry_run=False)
        self.assertEqual(result["purged"], 0)


class TestRunGC(unittest.TestCase):
    """完整 GC 周期测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.storage = StorageEngine(db_path=self.db_path, encrypted=False)
        self.engine = MemoryDecayEngine(storage=self.storage)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_run_gc_dry_run(self):
        """dry-run GC 不修改任何数据"""
        for i in range(3):
            self.storage.add_memory(f"memory {i}", category="test")

        report = self.engine.run_gc(dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertGreater(report["decay_scores"]["scanned"], 0)
        # dry-run 下不应有实际归档
        self.assertEqual(report["archive_decayed"]["archived"], 0)
        self.assertIn("duration_ms", report)

    def test_run_gc_empty_database(self):
        """空数据库 GC 不报错"""
        report = self.engine.run_gc(dry_run=False)
        self.assertEqual(report["decay_scores"]["scanned"], 0)
        self.assertEqual(report["archive_decayed"]["archived"], 0)

    def test_run_gc_with_skip_purge(self):
        """skip_purge 跳过永久删除步骤"""
        for i in range(3):
            self.storage.add_memory(f"memory {i}", category="test")

        report = self.engine.run_gc(dry_run=False, skip_purge=True)
        self.assertTrue(report["purge_old_archives"]["skipped"])

    def test_run_gc_full_cycle(self):
        """完整 GC 周期：评分 → 清理 → 归档 → 删除"""
        # 添加一些旧记忆，用 SQL 设旧时间
        old_time = time.time() - 3600 * 200
        for i in range(5):
            entry = self.storage.add_memory(f"old memory {i}", category="test")
            conn = self.storage._get_conn()
            conn.execute(
                "UPDATE memories SET last_accessed_at = ?, created_at = ? WHERE id = ?",
                (old_time, old_time, entry.id),
            )
            conn.commit()

        report = self.engine.run_gc(dry_run=False, skip_purge=False)
        self.assertIn("decay_scores", report)
        self.assertIn("temporary_cleanup", report)
        self.assertIn("archive_decayed", report)
        self.assertIn("purge_old_archives", report)
        self.assertIn("started_at", report)
        self.assertIn("finished_at", report)


class TestGCStats(unittest.TestCase):
    """GC 统计测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.storage = StorageEngine(db_path=self.db_path, encrypted=False)
        self.engine = MemoryDecayEngine(storage=self.storage)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_gc_stats_empty(self):
        """空数据库统计"""
        stats = self.engine.gc_stats()
        self.assertEqual(stats["total_memories"], 0)
        self.assertEqual(stats["below_threshold"], 0)
        self.assertEqual(stats["avg_strength"], 0)

    def test_gc_stats_with_memories(self):
        """有记忆时的统计"""
        old_time = time.time() - 3600 * 100
        for i in range(5):
            entry = self.storage.add_memory(f"memory {i}", category="test")
            conn = self.storage._get_conn()
            conn.execute(
                "UPDATE memories SET last_accessed_at = ?, created_at = ? WHERE id = ?",
                (old_time, old_time, entry.id),
            )
            conn.commit()

        stats = self.engine.gc_stats()
        self.assertEqual(stats["total_memories"], 5)
        self.assertGreater(stats["avg_strength"], 0)
        self.assertIn("policy", stats)
        self.assertIn("by_layer", stats)


if __name__ == "__main__":
    unittest.main()
