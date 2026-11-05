# -*- coding: utf-8 -*-
"""v6.1.2 级联删除 bug 回归测试

修复内容（对应 2026-11 审计）：
  B1: kg_edges 表不存在（4 处）→ graph_relations 按语义正确清理
  B2: memory_templates 不存在 memory_id 列（4 处死代码）→ 已移除
  B3: purge_agent_memories FTS 空值删除 → 真实值删除，补齐级联表
  B4: passport replace/revoke 裸删 → 走存储层级联删除

核心断言：
  - 硬删除后 graph_relations 无孤儿引用
  - 多记忆支撑的关系在删除一条支撑记忆后被保留（数组更新）
  - 支撑记忆全部删除后关系被整体清理
  - purge agent 后 FTS 无孤儿词条（rowid 复用不串台）
  - passport replace/revoke 后子表与 FTS 同步清理
"""
import json
import os
import shutil
import tempfile
import unittest

from mindforge.core.mindforge import MindForge
from mindforge.core.types import MemoryConfig, MemoryLayer


def _make_mf(tmp_name):
    tmp = tempfile.mkdtemp(prefix=tmp_name, dir=os.path.dirname(os.path.abspath(__file__)))
    db = os.path.join(tmp, "m.db")
    cfg = MemoryConfig(db_path=db, key_file=os.path.join(tmp, "k.key"), encrypted=False)
    return MindForge(config=cfg), tmp


def _insert_graph_relation(storage, rel_id, from_e, to_e, memory_ids):
    """直接写一条 graph_relations 记录（绕过抽取器，精确控制数据）"""
    conn = storage._get_conn()
    conn.execute(
        "INSERT INTO graph_relations (id, from_entity, to_entity, relation_type,"
        " weight, memory_ids, metadata, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (rel_id, from_e, to_e, "related", 1.0,
         json.dumps(memory_ids), "{}", 0.0),
    )
    conn.commit()


class TestGraphCascadeCleanup(unittest.TestCase):
    """B1/B2: 硬删除后图谱关系清理"""

    def setUp(self):
        self.mf, self.tmp = _make_mf("mf_v612_graph_")
        self.m1 = self.mf.add("记忆一", category="general")
        self.m2 = self.mf.add("记忆二", category="general")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _graph_rows(self):
        conn = self.mf._storage._get_conn()
        return conn.execute(
            "SELECT id, memory_ids FROM graph_relations"
        ).fetchall()

    def test_hard_delete_removes_orphan_relation(self):
        """唯一支撑记忆被删 → 关系整体清理"""
        _insert_graph_relation(self.mf._storage, "r1", "e1", "e2", [self.m1.id])
        self.mf.delete(self.m1.id, hard_delete=True)
        self.assertEqual(self._graph_rows(), [])

    def test_hard_delete_keeps_multi_supported_relation(self):
        """多记忆支撑 → 删一条后关系保留且数组正确更新"""
        _insert_graph_relation(self.mf._storage, "r1", "e1", "e2",
                               [self.m1.id, self.m2.id])
        self.mf.delete(self.m1.id, hard_delete=True)
        rows = self._graph_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][1]), [self.m2.id])

    def test_last_supporter_delete_removes_relation(self):
        """删两条支撑记忆 → 关系最终被清理"""
        _insert_graph_relation(self.mf._storage, "r1", "e1", "e2",
                               [self.m1.id, self.m2.id])
        self.mf.delete(self.m1.id, hard_delete=True)
        self.mf.delete(self.m2.id, hard_delete=True)
        self.assertEqual(self._graph_rows(), [])

    def test_dirty_data_rows_not_touched(self):
        """空串/NULL 脏数据行不被误删、不炸流程"""
        conn = self.mf._storage._get_conn()
        for rel_id, dirty in [("d1", ""), ("d2", None)]:
            conn.execute(
                "INSERT INTO graph_relations (id, from_entity, to_entity,"
                " relation_type, weight, memory_ids, metadata, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (rel_id, "e1", "e2", "related", 1.0, dirty, "{}", 0.0),
            )
        conn.commit()
        # 触发硬删除（不应抛异常）
        self.mf.delete(self.m1.id, hard_delete=True)
        remaining = {r[0] for r in self._graph_rows()}
        self.assertIn("d1", remaining)
        self.assertIn("d2", remaining)

    def test_batch_delete_cleans_relations(self):
        """batch_delete 路径同样正确清理"""
        m = self.mf.add("记忆三", category="batchgraph")
        _insert_graph_relation(self.mf._storage, "r1", "e1", "e2", [m.id])
        self.mf.batch_delete(category="batchgraph", hard_delete=True)
        self.assertEqual(self._graph_rows(), [])

    def test_hard_delete_no_swallowed_sql_errors(self):
        """硬删除全程无 no such table/column 噪音日志"""
        import logging
        from io import StringIO
        handler = logging.StreamHandler(StringIO())
        handler.setLevel(logging.WARNING)
        root = logging.getLogger()
        backup_handlers = root.handlers[:]
        root.addHandler(handler)
        try:
            _insert_graph_relation(self.mf._storage, "r1", "e1", "e2", [self.m1.id])
            self.mf.delete(self.m1.id, hard_delete=True)
            log_output = handler.stream.getvalue()
        finally:
            root.removeHandler(handler)
            root.handlers[:] = backup_handlers
        for bad in ("kg_edges", "memory_templates", "no such table", "no such column"):
            self.assertNotIn(bad, log_output)


class TestAgentPurgeFTS(unittest.TestCase):
    """B3: purge_agent_memories 的 FTS 与级联清理"""

    def setUp(self):
        self.mf, self.tmp = _make_mf("mf_v612_purge_")
        self.m1 = self.mf.add("docker container orchestration notes",
                              category="dev", source_agent="agent-A")
        self.m2 = self.mf.add("python asyncio patterns",
                              category="dev", source_agent="agent-A")
        self.other = self.mf.add("unrelated history memory",
                                 category="general", source_agent="agent-B")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _search_ids(self, query):
        result = self.mf.search(query, max_results=10)
        return [c.memory_id for c in result.chunks]

    def test_purge_uses_real_fts_values_no_cross_talk(self):
        """purge 后 FTS 无孤儿词条：复用 rowid 的旧词搜索不串台"""
        conn = self.mf._storage._get_conn()
        old_rowids = [r[0] for r in conn.execute(
            "SELECT rowid FROM memories WHERE source_agent = 'agent-A'"
        ).fetchall()]
        self.assertTrue(old_rowids)

        result = self.mf._storage.agent_purge("agent-A", dry_run=False)
        self.assertEqual(result.get("purged"), 2)

        # FTS 索引中 agent-A 的 rowid 已全部移除
        fts_rowids = {r[0] for r in conn.execute("SELECT rowid FROM memory_fts")}
        for rid in old_rowids:
            self.assertNotIn(rid, fts_rowids)

        # 新记忆复用 rowid（SQLite 空洞复用），搜索旧词不应命中新记忆
        fresh = self.mf.add("fresh cooking recipe content", category="food")
        self.mf.add("another gardening tips", category="hobby")
        for old_term in ("docker", "asyncio"):
            hits = self._search_ids(old_term)
            self.assertNotIn(fresh.id, hits,
                             f"搜索旧词 {old_term} 不应命中 rowid 复用的新记忆")
        # 新记忆自身可正常搜到
        hits = self._search_ids("recipe")
        self.assertIn(fresh.id, hits)

    def test_purge_cleans_child_tables(self):
        """purge 补齐 review_schedules / memory_embeddings / graph_relations"""
        conn = self.mf._storage._get_conn()
        now = 0.0
        conn.execute(
            "INSERT INTO review_schedules (id, memory_id, scheduled_at,"
            " interval_days, review_count, status, created_at)"
            " VALUES (?,?,?,?,?,?,?)", ("s1", self.m1.id, now, 1, 0, "pending", now))
        conn.execute(
            "INSERT INTO graph_relations (id, from_entity, to_entity, relation_type,"
            " weight, memory_ids, metadata, created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("g1", "e1", "e2", "related", 1.0, json.dumps([self.m1.id]), "{}", now))
        conn.commit()

        self.mf._storage.agent_purge("agent-A", dry_run=False)

        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM review_schedules").fetchone()[0], 0)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM graph_relations").fetchone()[0], 0)
        # agent-B 的记忆不受影响
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM memories WHERE id = ?",
                         (self.other.id,)).fetchone()[0], 1)


class TestPassportCascade(unittest.TestCase):
    """B4: passport replace/revoke 级联清理"""

    def setUp(self):
        from mindforge.modules import memory_passport as mp
        self.mp = mp
        self.mf, self.tmp = _make_mf("mf_v612_passport_")
        self.passport = {
            "format": self.mp.PASSPORT_FORMAT,
            "format_version": self.mp.FORMAT_VERSION,
            "passport_id": "pp-test-001",
            "memories": [
                {"content": "container orchestration knowledge", "category": "dev"},
                {"content": "quantum computing basics", "category": "science"},
            ],
        }

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _imported_ids(self):
        conn = self.mf._storage._get_conn()
        return [r[0] for r in conn.execute(
            "SELECT id FROM memories WHERE passport_id = 'pp-test-001'"
        ).fetchall()]

    def _search_ids(self, query):
        result = self.mf.search(query, max_results=10)
        return [c.memory_id for c in result.chunks]

    def test_revoke_cascade_cleans_fts_and_children(self):
        result = self.mp.import_passport(self.mf, self.passport, mode="merge")
        self.assertTrue(result["success"])
        ids = self._imported_ids()
        self.assertEqual(len(ids), 2)
        # 导入的记忆可被全文搜到
        self.assertIn(ids[0], self._search_ids("orchestration"))

        conn = self.mf._storage._get_conn()
        now = 0.0
        conn.execute(
            "INSERT INTO memory_versions (id, memory_id, version_number, content,"
            " category, tags, importance, actor, changed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            ("v1", ids[0], 1, "old content", "dev", "[]", "MEDIUM", "t", now))
        conn.commit()

        result = self.mp.revoke_passport(self.mf, "pp-test-001")
        self.assertTrue(result["success"])
        self.assertEqual(result["memories_removed"], 2)

        # 记忆、子表、FTS 全部清理
        self.assertEqual(self._imported_ids(), [])
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0], 0)
        fts_rowids = conn.execute("SELECT rowid FROM memory_fts").fetchall()
        self.assertEqual(fts_rowids, [])
        # 旧词搜索不再命中
        self.assertEqual(self._search_ids("orchestration"), [])
        self.assertEqual(self._search_ids("quantum"), [])

    def test_replace_mode_cascades(self):
        r1 = self.mp.import_passport(self.mf, self.passport, mode="merge")
        self.assertTrue(r1["success"])
        old_ids = set(self._imported_ids())

        r2 = self.mp.import_passport(self.mf, self.passport, mode="replace")
        self.assertTrue(r2["success"])
        new_ids = set(self._imported_ids())
        # replace 后旧记忆全删、新记忆写入，id 不重叠
        self.assertEqual(len(new_ids), 2)
        self.assertNotEqual(old_ids, new_ids)

        conn = self.mf._storage._get_conn()
        # FTS 无重复孤儿（行数与 memories 一致）
        fts_count = conn.execute(
            "SELECT COUNT(*) FROM memory_fts fts JOIN memories m"
            " ON fts.rowid = m.rowid").fetchone()[0]
        mem_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        self.assertEqual(fts_count, mem_count)


if __name__ == "__main__":
    unittest.main()
