# -*- coding: utf-8 -*-
"""v6.1.3 数据完整性收口回归测试

修复内容：
  1. gdpr_erase_all 漏删 5 张子表（memory_embeddings 隐私残留等）
  2. snapshot_memory tags 以 Python repr 落库（非 JSON）

新功能：
  3. repair_orphaned_data()：图谱失效引用移除 + 子表孤儿行清理
     （修复 v6.1.2 之前级联删除 bug 留下的历史脏数据）
"""
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from mindforge.core.mindforge import MindForge
from mindforge.core.types import MemoryConfig
from mindforge.modules.v6 import MemoryPreviewPort


def _make_mf(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix, dir=os.path.dirname(os.path.abspath(__file__)))
    cfg = MemoryConfig(
        db_path=os.path.join(tmp, "m.db"),
        key_file=os.path.join(tmp, "k.key"),
        encrypted=False,
    )
    return MindForge(config=cfg), tmp


def _insert_graph_relation(storage, rel_id, from_e, to_e, memory_ids):
    conn = storage._get_conn()
    conn.execute(
        "INSERT INTO graph_relations (id, from_entity, to_entity, relation_type,"
        " weight, memory_ids, metadata, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (rel_id, from_e, to_e, "related", 1.0,
         json.dumps(memory_ids, ensure_ascii=False), "{}", time.time()))
    conn.commit()


class TestGdprEraseAllCompleteness(unittest.TestCase):
    """被遗忘权：全部子表清理，无隐私残留"""

    def setUp(self):
        self.mf, self.tmp = _make_mf("mf_v613_gdpr_")
        self.m1 = self.mf.add("隐私数据 A", category="private")
        self.m2 = self.mf.add("隐私数据 B", category="private")
        conn = self.mf._storage._get_conn()
        # 铺满所有子表
        conn.execute(
            "INSERT INTO memory_notes (id, memory_id, content, author, tags, created_at, updated_at)"
            " VALUES ('n1', ?, 'note', 'tester', '[]', 1.0, 1.0)", (self.m1.id,))
        conn.execute(
            "INSERT INTO review_schedules (id, memory_id, scheduled_at, interval_days,"
            " review_count, status, created_at) VALUES ('r1', ?, 2.0, 7, 0, 'pending', 1.0)",
            (self.m1.id,))
        conn.execute(
            "INSERT INTO memory_versions (id, memory_id, version_number, content,"
            " category, tags, importance, actor, changed_at)"
            " VALUES ('v1', ?, 1, 'old', 'private', '[]', 'MEDIUM', 't', 1.0)",
            (self.m1.id,))
        conn.execute(
            "INSERT INTO memory_links (id, source_id, target_id, link_type, note, created_at)"
            " VALUES ('l1', ?, ?, 'related', '', 1.0)", (self.m1.id, self.m2.id))
        conn.execute(
            "INSERT INTO memory_evolutions (id, memory_id, from_version, to_version,"
            " reason, actor, created_at) VALUES ('e1', ?, 1, 2, 'test', 't', 1.0)",
            (self.m1.id,))
        _insert_graph_relation(self.mf._storage, "g1", "eA", "eB", [self.m1.id])
        conn.commit()

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_erase_all_leaves_no_child_rows(self):
        result = self.mf._storage.gdpr_erase_all(backup=False)
        conn = self.mf._storage._get_conn()
        checks = {
            "memories": "SELECT COUNT(*) FROM memories",
            "memory_versions": "SELECT COUNT(*) FROM memory_versions",
            "memory_notes": "SELECT COUNT(*) FROM memory_notes",
            "review_schedules": "SELECT COUNT(*) FROM review_schedules",
            "memory_links": "SELECT COUNT(*) FROM memory_links",
            "memory_evolutions": "SELECT COUNT(*) FROM memory_evolutions",
            "graph_relations": "SELECT COUNT(*) FROM graph_relations",
            "memory_embeddings": "SELECT COUNT(*) FROM memory_embeddings",
        }
        for table, sql in checks.items():
            cnt = conn.execute(sql).fetchone()[0]
            self.assertEqual(cnt, 0, f"gdpr_erase_all 后 {table} 残留 {cnt} 行（隐私残留）")
        # deleted 计数应包含铺的行
        self.assertEqual(result["deleted"].get("memory_notes"), 1)
        self.assertEqual(result["deleted"].get("review_schedules"), 1)
        self.assertEqual(result["deleted"].get("memory_evolutions"), 1)
        self.assertEqual(result["deleted"].get("graph_relations"), 1)


class TestSnapshotTagsJson(unittest.TestCase):
    """预览通道快照 tags 必须是合法 JSON"""

    def setUp(self):
        self.mf, self.tmp = _make_mf("mf_v613_snap_")
        self.entry = self.mf.add("快照测试", tags=["alpha", "beta"])
        self.port = MemoryPreviewPort(self.mf._storage)
        self.ch = self.port.open_preview_channel({"name": "v613"})

    def tearDown(self):
        try:
            self.port.close_preview_channel(self.ch["channel_id"])
        except Exception:
            pass
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tags_stored_as_valid_json(self):
        result = self.port.snapshot_memory(self.ch["channel_id"], self.entry.id)
        self.assertTrue(result["success"])
        conn = sqlite3.connect(self.ch["db_path"])
        raw = conn.execute("SELECT tags FROM channel_memories").fetchone()[0]
        conn.close()
        parsed = json.loads(raw)
        self.assertEqual(parsed, ["alpha", "beta"])


class TestRepairOrphanedData(unittest.TestCase):
    """孤儿数据修复：图谱引用 + 子表孤儿行"""

    def setUp(self):
        self.mf, self.tmp = _make_mf("mf_v613_repair_")
        self.live = self.mf.add("存活记忆", category="general")
        # 模拟历史 bug 遗留：直接写库绕过级联（v6.1.2 前的硬删除不清理）
        # 注：memory_versions 带 FOREIGN KEY 约束（schema 唯一一张），
        # FK ON 时插不进 ghost 行；真实孤儿来源是无 FK 的表——
        # notes/schedules/links/embeddings/evolutions/graph。
        conn = self.mf._storage._get_conn()
        conn.execute(
            "INSERT INTO memory_notes (id, memory_id, content, author, tags, created_at, updated_at)"
            " VALUES ('n_orphan', 'ghost-mem', 'note', 't', '[]', 1.0, 1.0)")
        conn.execute(
            "INSERT INTO review_schedules (id, memory_id, scheduled_at, interval_days,"
            " review_count, status, created_at)"
            " VALUES ('r_orphan', 'ghost-mem', 2.0, 7, 0, 'pending', 1.0)")
        conn.execute(
            "INSERT INTO memory_links (id, source_id, target_id, link_type, note, created_at)"
            " VALUES ('l_orphan', 'ghost-mem', 'ghost-other', 'related', '', 1.0)")
        conn.commit()

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _graph_relations(self):
        conn = self.mf._storage._get_conn()
        return {
            r[0]: json.loads(r[1])
            for r in conn.execute("SELECT id, memory_ids FROM graph_relations")
        }

    def test_dry_run_reports_without_modifying(self):
        _insert_graph_relation(self.mf._storage, "g_mix", "e1", "e2",
                               [self.live.id, "ghost-1"])
        _insert_graph_relation(self.mf._storage, "g_dead", "e1", "e3", ["ghost-2"])
        rep = self.mf._storage.repair_orphaned_data(dry_run=True)
        self.assertTrue(rep["dry_run"])
        self.assertEqual(rep["graph"]["stale_refs_removed"], 2)
        self.assertEqual(rep["graph"]["relations_updated"], 1)
        self.assertEqual(rep["graph"]["relations_deleted"], 1)
        self.assertEqual(rep["orphans"].get("memory_notes"), 1)
        self.assertEqual(rep["orphans"].get("memory_links.source_id"), 1)
        self.assertEqual(rep["orphans"].get("review_schedules"), 1)
        # dry_run 不动数据
        rels = self._graph_relations()
        self.assertEqual(rels["g_mix"], [self.live.id, "ghost-1"])
        self.assertEqual(rels["g_dead"], ["ghost-2"])
        conn = self.mf._storage._get_conn()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM memory_notes WHERE memory_id='ghost-mem'").fetchone()[0], 1)

    def test_repair_removes_stale_and_cleans_orphans(self):
        _insert_graph_relation(self.mf._storage, "g_mix", "e1", "e2",
                               [self.live.id, "ghost-1"])
        _insert_graph_relation(self.mf._storage, "g_dead", "e1", "e3", ["ghost-2"])
        # 原本就为空的关系（无记忆支撑）必须被保留
        _insert_graph_relation(self.mf._storage, "g_builtin", "e4", "e5", [])

        rep = self.mf._storage.repair_orphaned_data(dry_run=False)
        self.assertFalse(rep["dry_run"])
        self.assertEqual(rep["graph"]["stale_refs_removed"], 2)
        self.assertEqual(rep["graph"]["relations_updated"], 1)
        self.assertEqual(rep["graph"]["relations_deleted"], 1)

        rels = self._graph_relations()
        # g_mix: 失效引用被移除，保留存活引用
        self.assertEqual(rels.get("g_mix"), [self.live.id])
        # g_dead: 支撑全失效，整体删除
        self.assertNotIn("g_dead", rels)
        # g_builtin: 原生空数组不受牵连
        self.assertIn("g_builtin", rels)

        conn = self.mf._storage._get_conn()
        for table, col in (("memory_notes", "memory_id"),
                           ("review_schedules", "memory_id"),
                           ("memory_links", "source_id")):
            cnt = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col}='ghost-mem'"
            ).fetchone()[0]
            self.assertEqual(cnt, 0, f"{table} 孤儿行未清理")
        # 存活记忆的数据不受影响
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM memories WHERE id=?", (self.live.id,)).fetchone()[0], 1)

    def test_clean_db_reports_zero_issues(self):
        _insert_graph_relation(self.mf._storage, "g_ok", "e1", "e2", [self.live.id])
        rep = self.mf._storage.repair_orphaned_data(dry_run=False)
        # 清掉 setUp 铺的 ghost 子表行后……不，dry_run=False 会清掉它们
        self.assertEqual(rep["graph"]["stale_refs_removed"], 0)
        self.assertEqual(rep["graph"]["relations_deleted"], 0)
        # 存活关系保留
        self.assertIn("g_ok", self._graph_relations())

    def test_doctor_exit_code_zero_when_clean(self):
        """doctor 集成：干净库退出码 0"""
        conn = self.mf._storage._get_conn()
        conn.execute("DELETE FROM memory_notes WHERE memory_id='ghost-mem'")
        conn.execute("DELETE FROM review_schedules WHERE memory_id='ghost-mem'")
        conn.execute("DELETE FROM memory_links WHERE source_id='ghost-mem'")
        conn.commit()
        _insert_graph_relation(self.mf._storage, "g_ok", "e1", "e2", [self.live.id])
        # 直接调用存储层，CLI 集成由 test_v6_ports.py 的 CLI harness 覆盖
        rep = self.mf._storage.repair_orphaned_data(dry_run=True)
        self.assertEqual(rep["graph"]["stale_refs_removed"], 0)
        self.assertEqual(sum(rep["orphans"].values()), 0)


if __name__ == "__main__":
    unittest.main()
