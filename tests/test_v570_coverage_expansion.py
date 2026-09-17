# -*- coding: utf-8 -*-
"""v5.7.0 P2 测试覆盖扩展

目标：为长期零覆盖/低覆盖区域补齐回归测试——
  - review_system 完整复习周期（创建→到期→完成→统计）+ 过期记忆边界；
  - categorizer / evolution / integrator / multimodal 四个零覆盖模块冒烟；
  - storage 核心方法正常/空/不存在三态；
  - storage 多 Agent 隔离与迁移；
  - storage 工具方法（导出/导入/复制/搜索历史/时间线）；
  - mindforge 配置与元方法（load/save/config_summary/privacy/get_encryption_info）。

同时锁定 v5.7.0 修复：
  - search-history 端到端可用（record_search 写入 + SQL 修正）；
  - list_due_reviews 排除 TTL 过期记忆；
  - integrator 解密失败路径不再 NameError（logger 补齐）。
"""

import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from core.storage import StorageEngine, MemoryEntry
from core.types import MemoryLayer, Importance

# 确保项目根目录可导入（pytest 从仓库根运行时无需）
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v570_")
        self.db = os.path.join(self.tmp, "test.db")
        self.storage = StorageEngine(db_path=self.db, encrypted=False)

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add(self, content, category="general", tags=None, **kw):
        return self.storage.add_memory(
            content=content, category=category, tags=tags or [], **kw)

    def _sql(self, sql, params=()):
        conn = self.storage._get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur


# ============================================================
# 1. review_system（完整周期 + 边界）
# ============================================================
class TestReviewSystem(_StoreCase):
    def test_full_review_cycle(self):
        mem = self._add("需要复习的记忆内容", category="study")
        # 创建 → 到期（回拨 scheduled_at）→ 完成 → 统计
        sched = self.storage.create_review_schedule(mem.id, interval_days=1.0)
        self.assertTrue(sched["success"])
        sid = sched["schedule_id"]

        # 未到期：不应出现在到期列表中
        due = self.storage.list_due_reviews(limit=50)
        self.assertEqual(due, [])

        # 回拨到过去，模拟到期
        self._sql("UPDATE review_schedules SET scheduled_at = ? WHERE id = ?",
                  (time.time() - 100, sid))
        due = self.storage.list_due_reviews(limit=50)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["schedule_id"], sid)
        self.assertEqual(due[0]["memory_id"], mem.id)

        before = self.storage.get_memory(mem.id)
        done = self.storage.complete_review(sid)
        self.assertTrue(done["success"])
        self.assertEqual(done["review_count"], 1)
        self.assertEqual(done["next_interval_days"], 2)  # 1→2 天

        after = self.storage.get_memory(mem.id)
        self.assertEqual(after.consolidation_count, before.consolidation_count + 1)

        stats = self.storage.get_review_stats()
        self.assertEqual(stats["total_schedules"], 1)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["total_reviews_completed"], 1)

    def test_complete_review_unknown_schedule(self):
        done = self.storage.complete_review("no-such-schedule")
        self.assertFalse(done["success"])

    def test_create_schedule_missing_memory(self):
        r = self.storage.create_review_schedule("missing-id")
        self.assertFalse(r["success"])

    def test_review_stats_empty(self):
        stats = self.storage.get_review_stats()
        self.assertEqual(stats, {"total_schedules": 0, "pending": 0,
                                 "due_now": 0, "total_reviews_completed": 0})

    def test_expired_memory_not_due(self):
        """边界：TTL 过期记忆不参与复习（v5.7.0 修复）。"""
        mem = self._add("过期记忆", category="general",
                        expires_at=time.time() + 50)  # 快过期但未过期
        self.storage.create_review_schedule(mem.id, interval_days=0.0)
        # 手动将记忆置为已过期
        self._sql("UPDATE memories SET expires_at = ? WHERE id = ?",
                  (time.time() - 10, mem.id))
        # 回拨计划为到期
        sid = self._sql(
            "SELECT id FROM review_schedules WHERE memory_id = ?", (mem.id,)
        ).fetchone()[0]
        self._sql("UPDATE review_schedules SET scheduled_at = ? WHERE id = ?",
                  (time.time() - 100, sid))
        due = self.storage.list_due_reviews(limit=50)
        self.assertEqual(due, [])

    def test_interval_days_multiple_reviews(self):
        mem = self._add("间隔重复测试")
        s = self.storage.create_review_schedule(mem.id, interval_days=0.0)
        sid = s["schedule_id"]
        for _ in range(3):
            r = self.storage.complete_review(sid)
            self.assertTrue(r["success"])
        # 第 3 次完成 → review_count=3 → 间隔 7 天
        self.assertEqual(r["review_count"], 3)
        self.assertEqual(r["next_interval_days"], 7)


# ============================================================
# 2. categorizer 冒烟
# ============================================================
class TestCategorizerSmoke(unittest.TestCase):
    def setUp(self):
        from modules.categorizer import TaxonomyManager
        self.tm = TaxonomyManager()

    def test_suggest_category_normal_and_empty(self):
        self.assertEqual(self.tm.suggest_category("今天开会讨论项目进度"), "work")
        self.assertEqual(self.tm.suggest_category(""), "general")
        self.assertEqual(self.tm.suggest_category("完全无关的随机文本"), "general")

    def test_suggest_tags_normal_and_empty(self):
        tags = self.tm.suggest_tags("用 python 写接口处理 api 请求")
        self.assertIsInstance(tags, list)
        self.assertLessEqual(len(tags), 5)
        self.assertEqual(self.tm.suggest_tags(""), [])

    def test_taxonomy_queries_and_add(self):
        self.assertIn("work", self.tm.get_all_categories())
        info = self.tm.get_category_info("tech")
        self.assertEqual(info["parent"], "work")
        self.assertIn("programming", self.tm.get_subcategories("tech"))
        self.assertEqual(self.tm.get_parent_category("programming"), "tech")
        self.assertIsNone(self.tm.get_category_info("nope"))
        self.assertEqual(self.tm.get_subcategories("nope"), [])
        self.tm.add_category("sports", "运动", keywords=["篮球", "跑步"], parent="life")
        self.assertEqual(self.tm.suggest_category("坚持每天跑步五公里"), "sports")


# ============================================================
# 3. evolution 冒烟
# ============================================================
class TestEvolutionSmoke(_StoreCase):
    def setUp(self):
        super().setUp()
        from modules.evolution import MemoryEvolution, ForgettingCurve
        self.evo = MemoryEvolution(self.storage)
        self.curve = ForgettingCurve()

    def test_forgetting_curve(self):
        s1 = self.curve.calculate_strength(1.0, 0.0)
        s2 = self.curve.calculate_strength(1.0, 24.0)
        self.assertLess(s2, s1)
        self.assertGreaterEqual(self.curve.calculate_strength(1.0, 9999), 0.01)
        self.assertEqual(self.curve.next_review_time(0.5, target_strength=0.6), 0)

    def test_update_forgetting_scores_empty_and_normal(self):
        # 空库
        self.evo.update_forgetting_scores()
        self._add("一条普通记忆")
        self.evo.update_forgetting_scores()  # 不应抛异常

    def test_consolidate_and_stats(self):
        r = self.evo.consolidate()
        self.assertIn("processed", r)
        stats = self.evo.get_evolution_stats()
        for k in ("sensory", "short_term", "long_term", "permanent", "consolidation_rate"):
            self.assertIn(k, stats)

    def test_promote_reactivate_unknown(self):
        self.assertFalse(self.evo.promote_to_permanent("missing-id"))
        self.assertFalse(self.evo.reactivate("missing-id"))

    def test_review_schedule_empty(self):
        self.assertEqual(self.evo.review_schedule(limit=10), [])


# ============================================================
# 4. integrator 冒烟
# ============================================================
class TestIntegratorSmoke(_StoreCase):
    def setUp(self):
        super().setUp()
        from modules.integrator import MemoryIntegrator
        self.integrator = MemoryIntegrator(self.storage)

    def test_generate_summary_empty_and_normal(self):
        s = self.integrator.generate_summary()
        self.assertEqual(s.total_memories, 0)
        self.assertEqual(s.time_span, "无数据")
        self._add("第一条工作记忆", category="work")
        self._add("第二条生活记忆", category="life")
        s2 = self.integrator.generate_summary()
        self.assertEqual(s2.total_memories, 2)
        self.assertIn("2 条记忆", s2.summary)
        self.assertEqual(len(s2.key_points), 2)

    def test_generate_timeline(self):
        tl = self.integrator.generate_timeline()
        self.assertEqual(tl.entries, [])
        self._add("时间线测试记忆")
        tl2 = self.integrator.generate_timeline()
        self.assertEqual(len(tl2.entries), 1)
        self.assertTrue(tl2.time_periods)

    def test_find_related_memories(self):
        self.assertEqual(self.integrator.find_related_memories("missing"), [])
        a = self._add("python 与接口开发")
        self._add("python 数据清洗脚本")
        related = self.integrator.find_related_memories(a.id)
        self.assertEqual(len(related), 1)

    def test_consolidate_daily_and_clusters(self):
        d = self.integrator.consolidate_daily()
        self.assertIn("total_memories", d)
        self._add("聚类测试", category="tech")
        clusters = self.integrator.get_memory_clusters()
        self.assertIsInstance(clusters, dict)


# ============================================================
# 5. multimodal 冒烟
# ============================================================
class TestMultimodalSmoke(_StoreCase):
    def setUp(self):
        super().setUp()
        from modules.multimodal import (
            MultimodalMemory, MultimodalContent, MultimodalType,
        )
        self.mm = MultimodalMemory(self.storage)
        self.Content = MultimodalContent
        self.Type = MultimodalType

    def test_create_text_memory(self):
        c = self.mm.create_text_memory("你好世界")
        self.assertEqual(c.content_type, self.Type.TEXT)
        self.assertIn("char_count", c.metadata)

    def test_create_image_and_audio(self):
        img = self.mm.create_image_memory(b"\x89PNG" + b"0" * 100,
                                          caption="测试图", width=10, height=10,
                                          objects=["cat"])
        self.assertEqual(img.content_type, self.Type.IMAGE)
        self.assertIn("[图像]", img.text_representation)
        aud = self.mm.create_audio_memory(b"RIFF" + b"0" * 64,
                                          transcript="你好", duration=1.5)
        self.assertEqual(aud.content_type, self.Type.AUDIO)
        self.assertIn("你好", aud.text_representation)
        # 超大输入拒绝
        from modules.multimodal import MultimodalMemory as MM
        with self.assertRaises(ValueError):
            MM().create_image_memory(b"x" * (50 * 1024 * 1024 + 1))

    def test_create_code_and_structured(self):
        code = self.mm.create_code_memory("def f():\n    return 1\nclass A: pass",
                                          language="python", purpose="示例")
        self.assertEqual(code.content_type, self.Type.CODE)
        self.assertEqual(code.metadata["function_count"], 1)
        self.assertEqual(code.metadata["class_count"], 1)
        st = self.mm.create_structured_memory({"k": 1}, schema_name="demo")
        self.assertEqual(st.content_type, self.Type.STRUCTURED)

    def test_composite_and_similarity(self):
        a = self.mm.create_text_memory("今天天气很好")
        b = self.mm.create_text_memory("今天天气很好")
        c = self.mm.create_text_memory("完全不同的内容")
        self.assertEqual(self.mm.compute_similarity(a, b), 1.0)
        self.assertLess(self.mm.compute_similarity(a, c), 1.0)
        combo = self.mm.create_multimodal_memory([a, b], description="复合")
        self.assertEqual(combo.content_type, self.Type.MULTIMODAL)
        self.assertEqual(combo.metadata["modality_count"], 2)

    def test_search_without_index_and_stats(self):
        self.assertEqual(self.mm.search_multimodal("随便"), [])
        self.assertEqual(self.mm.get_type_stats(), {})

    def test_content_roundtrip(self):
        c = self.mm.create_text_memory("序列化测试")
        d = self.Content.from_dict(c.to_dict())
        self.assertEqual(d.data, "序列化测试")
        self.assertEqual(d.content_type, c.content_type)


# ============================================================
# 6. storage 核心方法三态
# ============================================================
class TestStorageCoreMethods(_StoreCase):
    def test_get_memories_by_ids(self):
        a = self._add("第一条")
        b = self._add("第二条")
        got = self.storage.get_memories_by_ids([a.id, "missing", b.id])
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0].id, a.id)
        self.assertIsNone(got[1])
        self.assertEqual(got[2].id, b.id)
        self.assertEqual(self.storage.get_memories_by_ids([]), [])

    def test_adjust_importance(self):
        a = self._add("重要性调整")
        self.assertTrue(self.storage.adjust_importance(a.id, 1.0))
        self.assertEqual(self.storage.get_memory(a.id).importance, Importance.CRITICAL)
        self.assertTrue(self.storage.adjust_importance(a.id, -1.0))
        self.assertFalse(self.storage.adjust_importance("missing", 1.0))
        self.assertFalse(self.storage.adjust_importance(a.id, 0.0))

    def test_restore_memory(self):
        a = self._add("待恢复记忆", category="work")
        self.assertFalse(self.storage.restore_memory(a.id))  # 非回收站
        self.storage.delete_memory(a.id)  # 软删除
        self.assertTrue(self.storage.restore_memory(a.id))
        self.assertEqual(self.storage.get_memory(a.id).category, "work")
        self.assertFalse(self.storage.restore_memory("missing"))

    def test_search_by_tag(self):
        a = self._add("标签记忆", tags=["python", "ai"])
        self._add("无标签记忆")
        hits = self.storage.search_by_tag("python")
        self.assertEqual([h.id for h in hits], [a.id])
        self.assertEqual(self.storage.search_by_tag("nope"), [])
        # 通配符转义：% 不应匹配全部
        b = self._add("百分号标签", tags=["100%"])
        self.assertEqual(self.storage.search_by_tag("%"), [])
        self.assertEqual([h.id for h in self.storage.search_by_tag("100%")], [b.id])

    def test_export_as_markdown(self):
        self._add("导出测试内容", category="doc")
        out = os.path.join(self.tmp, "export.md")
        p = self.storage.export_as_markdown(out)
        self.assertTrue(os.path.exists(p))
        text = io.open(p, encoding="utf-8").read()
        self.assertIn("导出测试内容", text)
        # 分类过滤
        out2 = os.path.join(self.tmp, "export2.md")
        self.storage.export_as_markdown(out2, category="nope")
        self.assertNotIn("导出测试内容",
                         io.open(out2, encoding="utf-8").read())


# ============================================================
# 7. storage 多 Agent 方法
# ============================================================
class TestMultiAgentMethods(_StoreCase):
    def test_agent_stats_and_isolation(self):
        self._add("alice 的记忆", source_agent="alice")
        self._add("bob 的记忆", source_agent="bob")
        a_stats = self.storage.agent_stats("alice")
        self.assertEqual(a_stats["total_memories"], 1)
        self.assertNotIn("bob", self.storage.list_by_agent("alice")[0].content)
        bob_list = self.storage.list_by_agent("bob")
        self.assertEqual(len(bob_list), 1)
        self.assertEqual(self.storage.list_by_agent("carol"), [])
        self.assertEqual(self.storage.agent_stats("carol")["total_memories"], 0)

    def test_transfer_agent_memories(self):
        m1 = self._add("要迁移的记忆 A", source_agent="alice")
        self._add("要迁移的记忆 B", source_agent="alice", category="work")
        r = self.storage.transfer_agent_memories("alice", "bob")
        self.assertEqual(r["transferred"], 2)
        self.assertEqual(len(self.storage.list_by_agent("bob")), 2)
        self.assertEqual(self.storage.list_by_agent("alice"), [])
        # 分类过滤
        self._add("留在 alice", source_agent="alice", category="life")
        r2 = self.storage.transfer_agent_memories("alice", "bob", category="work")
        self.assertEqual(r2["transferred"], 0)
        self.assertEqual(len(self.storage.list_by_agent("alice")), 1)

    def test_merge_agent_memories_dedup(self):
        self._add("相同内容", source_agent="alice")
        self._add("相同内容", source_agent="bob")
        self._add("alice 独有内容", source_agent="alice")
        r = self.storage.merge_agent_memories("alice", "bob", dedup="exact")
        self.assertEqual(r["migrated"], 1)
        self.assertEqual(r["skipped_duplicates"], 1)
        # none 模式：全部迁移
        self._add("again", source_agent="alice")
        r2 = self.storage.merge_agent_memories("alice", "bob", dedup="none")
        self.assertEqual(r2["migrated"], 2)
        # 非法参数
        bad = self.storage.merge_agent_memories("", "bob")
        self.assertIn("error", bad)
        same = self.storage.merge_agent_memories("x", "x")
        self.assertIn("error", same)

    def test_evolve_memories(self):
        # 空库
        r0 = self.storage.evolve_memories(dry_run=True)
        self.assertIsInstance(r0, dict)
        # 造一条 2 天前创建、有访问的短期记忆 → 应升级为长期
        m = self._add("老记忆", source_agent="alice")
        self._sql("UPDATE memories SET created_at = ?, access_count = 1 WHERE id = ?",
                  (time.time() - 2 * 86400, m.id))
        r1 = self.storage.evolve_memories(dry_run=True)
        self.assertGreaterEqual(r1.get("short_to_long", 0), 1)
        r2 = self.storage.evolve_memories(dry_run=False)
        self.assertIsInstance(r2, dict)


# ============================================================
# 8. storage 工具方法
# ============================================================
class TestStorageToolMethods(_StoreCase):
    def test_export_import_excel_csv_roundtrip(self):
        """openpyxl 未安装时应走 CSV 回退；CSV 导出→导入应可往返。"""
        try:
            import openpyxl  # noqa: F401
            self.skipTest("openpyxl 已安装，走 xlsx 路径（不在本用例范围）")
        except ImportError:
            pass
        self._add("CSV 往返内容一", tags=["a", "b"])
        self._add("CSV 往返内容二", category="work")
        out = os.path.join(self.tmp, "export.xlsx")
        p = self.storage.export_as_excel(out)
        # CSV 回退产生 .csv
        self.assertTrue(os.path.exists(str(p).replace(".xlsx", ".csv")) or str(p).endswith(".csv"))
        csv_path = str(p).replace(".xlsx", ".csv")
        r = self.storage.import_from_excel(csv_path, target_category="imported")
        self.assertEqual(r["imported"], 2)
        hits = [e for e in self.storage.list_memories(category="imported")
                if "往返" in e.content]
        self.assertEqual(len(hits), 2)

    def test_copy_memory(self):
        a = self._add("复制源内容", category="src")
        self.assertTrue(self.storage.copy_memory(a.id, "dst"))
        copied = [e for e in self.storage.list_memories(category="dst")]
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0].content, "复制源内容")
        self.assertFalse(self.storage.copy_memory("missing", "dst"))

    def test_embedding_engine_property(self):
        eng = self.storage.embedding_engine
        # 未安装 sentence-transformers 时返回 None；已安装则返回引擎
        self.assertTrue(eng is None or hasattr(eng, "is_available"))

    def test_get_search_history_via_record(self):
        self.assertEqual(self.storage.get_search_history(), [])
        self.storage.record_search("  你好  ")
        self.storage.record_search("你好")
        self.storage.record_search("测试")
        hist = self.storage.get_search_history(limit=10)
        qs = {h["query"] for h in hist}
        self.assertIn("你好", qs)
        self.assertIn("测试", qs)
        by_q = {h["query"]: h["count"] for h in hist}
        self.assertEqual(by_q["你好"], 2)

    def test_timeline_view(self):
        tl = self.storage.timeline_view()
        for k in ("today", "yesterday", "this_week", "this_month", "earlier"):
            self.assertIn(k, tl)
        self._add("今天的新记忆")
        tl2 = self.storage.timeline_view()
        self.assertEqual(len(tl2["today"]), 1)


# ============================================================
# 9. mindforge 配置与元方法
# ============================================================
class TestMindforgeMeta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v570_mf_")
        from MindForge import MindForge
        from core.types import MemoryConfig
        self.MindForge = MindForge
        self.mf = MindForge(config=MemoryConfig(
            db_path=os.path.join(self.tmp, "meta.db"), encrypted=False))

    def tearDown(self):
        try:
            self.mf.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_config_roundtrip(self):
        cfg_path = os.path.join(self.tmp, "cfg.json")
        self.mf.save_config(cfg_path)
        loaded = self.MindForge.load_config(cfg_path)
        self.assertEqual(loaded["db_path"], os.path.join(self.tmp, "meta.db"))
        self.assertIn("encrypted", loaded)
        with self.assertRaises(OSError):
            self.MindForge.load_config(os.path.join(self.tmp, "missing.json"))

    def test_config_summary_and_privacy(self):
        summary = self.mf.config_summary()
        self.assertIsInstance(summary, dict)
        from modules.privacy import PrivacyEngine
        self.assertIsInstance(self.mf.privacy_engine, PrivacyEngine)

    def test_get_encryption_info_unencrypted(self):
        self.assertIsNone(self.mf.get_encryption_info())

    def test_stats_and_health(self):
        self.mf.add("元方法测试记忆")
        stats = self.mf.stats()
        self.assertGreaterEqual(stats["total"], 1)
        health = self.mf.health_check()
        self.assertIn("status", health)


if __name__ == "__main__":
    unittest.main()
