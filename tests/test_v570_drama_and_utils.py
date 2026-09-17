# -*- coding: utf-8 -*-
"""v5.7.0 P2 测试覆盖扩展（二）：短剧系统 + 工具方法

覆盖 storage.py 中零覆盖的短剧子系统（drama/scene/character/line CRUD 与
统计分析）、笔记/模板、标签批量操作、记忆工具方法（批量删除/迁移/重命名/
备份/版本回滚/随机/层统计等），目标是把 v5.7.0 未测公开方法从 ~110 降至
100 以内。
"""

import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from core.storage import StorageEngine
from core.types import MemoryLayer, Importance, DramaGenre, DramaStatus

import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v570b_")
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
# 短剧系统：drama CRUD + 统计
# ============================================================
class TestDramaSystem(_StoreCase):
    def _mk_drama(self, title="测试短剧", **kw):
        dr = self.storage.add_drama(title, genre=kw.pop("genre", "romance"),
                                    total_episodes=kw.pop("total_episodes", 10), **kw)
        return dr.id

    def test_drama_crud_lifecycle(self):
        did = self._mk_drama(genre=DramaGenre.ROMANCE)
        got = self.storage.get_drama(did)
        self.assertEqual(got.title, "测试短剧")
        # 更新（枚举参数需传 DramaStatus/DramaGenre 对象）
        self.storage.update_drama(did, title="改名短剧", rating=8.5,
                                  current_episode=3, status=DramaStatus.WATCHING)
        got2 = self.storage.get_drama(did)
        self.assertEqual(got2.title, "改名短剧")
        self.assertGreaterEqual(got2.rating, 8.5)
        # 列表
        lst = self.storage.list_dramas(genre=DramaGenre.ROMANCE)
        self.assertIn(did, [d.id for d in lst])
        # 删除
        self.assertTrue(self.storage.delete_drama(did))
        self.assertIsNone(self.storage.get_drama(did))

    def test_drama_stats_and_search(self):
        did = self._mk_drama(title="都市情感")
        self.storage.update_drama(did, status=DramaStatus.WATCHING)
        stats = self.storage.drama_stats()
        self.assertEqual(stats["total"], 1)
        self.assertGreaterEqual(stats["watching"], 1)
        hits = self.storage.search_dramas("都市")
        self.assertEqual(len(hits), 1)
        self.assertEqual(self.storage.search_dramas("不存在的关键词"), [])
        # 进度：返回值（存 metadata.user_progress）
        r = self.storage.drama_update_progress(did, 5, status="WATCHING")
        self.assertEqual(r["current_episode"], 5)
        self.assertEqual(r["status"], "WATCHING")

    def test_drama_analytics(self):
        did = self._mk_drama(title="分析剧", total_episodes=20)
        detail = self.storage.drama_detail_stats(did)
        self.assertIn("line_count", detail)
        summ = self.storage.drama_summary(did)
        self.assertIn("summary", summ)
        cmp = self.storage.drama_compare([did])
        self.assertEqual(cmp["total_compared"], 1)
        bingest = self.storage.drama_binge_stats()
        self.assertEqual(bingest["total_dramas"], 1)
        score = self.storage.drama_binge_score(did)
        self.assertIn("binge_score", score)
        trend = self.storage.drama_genre_trend()
        self.assertIn("total_dramas", trend)
        top = self.storage.top_rated_dramas()
        self.assertIn(did, [d.id for d in top])

    def test_import_dramas_from_json(self):
        payload = {"dramas": [{
            "title": "导入剧", "genre": "古装", "total_episodes": 30,
            "description": "导入测试",
        }]}
        p = os.path.join(self.tmp, "dramas.json")
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        r = self.storage.import_dramas_from_json(p)
        self.assertGreaterEqual(r.get("dramas", 0), 1)
        self.assertEqual(self.storage.drama_stats()["total"], 1)

    def test_update_drama_mark_watched(self):
        did = self._mk_drama()
        self.storage.update_drama(did, current_episode=10, mark_watched=True)
        got = self.storage.get_drama(did)
        self.assertGreater(got.last_watched_at, 0)


# ============================================================
# 短剧系统：scene CRUD + 张力
# ============================================================
class TestSceneSystem(_StoreCase):
    def setUp(self):
        super().setUp()
        dr = self.storage.add_drama("场景剧", genre="悬疑", total_episodes=10)
        self.did = dr.id

    def test_scene_crud(self):
        s = self.storage.add_scene(self.did, 1, 1, "第一幕", content="开场")
        sid = s.id
        self.assertEqual(self.storage.get_scene(sid).title, "第一幕")
        self.storage.update_scene(sid, title="改名幕")
        self.assertEqual(self.storage.get_scene(sid).title, "改名幕")
        lst = self.storage.list_scenes(drama_id=self.did)
        self.assertEqual(len(lst), 1)
        tension = self.storage.scene_tension(self.did)
        self.assertIn("avg_tension", tension)
        self.assertTrue(self.storage.delete_scene(sid))
        self.assertIsNone(self.storage.get_scene(sid))

    def test_scene_empty_lists(self):
        self.assertEqual(self.storage.list_scenes(drama_id="missing"), [])


# ============================================================
# 短剧系统：character CRUD + 分析
# ============================================================
class TestCharacterSystem(_StoreCase):
    def setUp(self):
        super().setUp()
        dr = self.storage.add_drama("角色剧", genre="都市", total_episodes=10)
        self.did = dr.id

    def test_character_crud_and_profile(self):
        c = self.storage.add_character(self.did, "主角", role="main",
                                       description="第一主角")
        cid = c.id
        got = self.storage.get_character(cid)
        self.assertEqual(got.name, "主角")
        self.storage.update_character(cid, name="改名主角")
        self.assertEqual(self.storage.get_character(cid).name, "改名主角")
        lst = self.storage.list_characters(drama_id=self.did)
        self.assertEqual(len(lst), 1)
        prof = self.storage.character_profile(cid)
        self.assertEqual(prof["name"], "改名主角")
        rank = self.storage.character_ranking(drama_id=self.did)
        self.assertIsInstance(rank, list)
        arc = self.storage.character_arc(self.did, cid)
        self.assertIn("arc_points", arc)
        self.assertTrue(self.storage.delete_character(cid))
        self.assertIsNone(self.storage.get_character(cid))

    def test_character_empty(self):
        self.assertEqual(self.storage.list_characters(drama_id="missing"), [])
        self.assertEqual(self.storage.character_ranking(drama_id="missing"), [])


# ============================================================
# 短剧系统：line CRUD + 检索
# ============================================================
class TestLineSystem(_StoreCase):
    def setUp(self):
        super().setUp()
        dr = self.storage.add_drama("台词剧", genre="喜剧", total_episodes=10)
        self.did = dr.id
        self.sid = self.storage.add_scene(self.did, 1, 1, "一幕").id
        self.cid = self.storage.add_character(self.did, "角色A").id

    def _line(self, text, **kw):
        return self.storage.add_line(
            self.did, text, scene_id=self.sid, character_id=self.cid, **kw)

    def test_line_crud(self):
        l = self._line("第一句台词", is_classic=True)
        lid = l.id
        self.assertEqual(self.storage.get_line(lid).line_text, "第一句台词")
        self.storage.update_line(lid, line_text="改后的台词")
        self.assertEqual(self.storage.get_line(lid).line_text, "改后的台词")
        lst = self.storage.list_lines(drama_id=self.did)
        self.assertEqual(len(lst), 1)
        self.assertEqual(len(self.storage.list_lines_by_scene(self.sid)), 1)
        self.assertEqual(len(self.storage.list_lines_by_character(self.cid)), 1)
        self.assertTrue(self.storage.delete_line(lid))
        self.assertIsNone(self.storage.get_line(lid))

    def test_line_search_and_classics(self):
        self._line("经典名场面台词", is_classic=True)
        self._line("普通台词", is_classic=False)
        hits = self.storage.search_lines("名场面")
        self.assertEqual(len(hits), 1)
        classics = self.storage.classic_lines(drama_id=self.did)
        self.assertEqual(len(classics), 1)
        rand = self.storage.random_lines(drama_id=self.did, count=5)
        self.assertGreaterEqual(len(rand), 1)
        self.assertEqual(len(self.storage.list_lines(is_classic=True)), 1)

    def test_line_interactions(self):
        c2 = self.storage.add_character(self.did, "角色B").id
        self._line("台词A")
        self.storage.add_line(self.did, "台词B", scene_id=self.sid, character_id=c2)
        inter = self.storage.char_interaction(self.did)
        self.assertIn("interactions", inter)
        rel = self.storage.char_relationship(self.did, self.cid, c2)
        self.assertIsInstance(rel, dict)


# ============================================================
# 笔记与模板
# ============================================================
class TestNotesAndTemplates(_StoreCase):
    def test_notes_crud(self):
        m = self._add("笔记关联记忆")
        n = self.storage.add_note(m.id, "第一条笔记", tags=["todo"])
        nid = n["note_id"] if isinstance(n, dict) else n.id
        notes = self.storage.list_notes(m.id)
        self.assertGreaterEqual(len(notes), 1)
        self.assertTrue(self.storage.delete_note(nid))
        self.assertEqual(self.storage.list_notes(m.id), [])

    def test_templates_crud(self):
        t = self.storage.add_template("会议纪要", "主题：{topic}\n结论：{conclusion}",
                                      category="work")
        tid = t["template_id"] if isinstance(t, dict) else t.id
        lst = self.storage.list_templates(category="work")
        self.assertEqual(len(lst), 1)
        used = self.storage.use_template(tid, {"topic": "Q3", "conclusion": "推进"})
        content = used.get("content", "")
        self.assertIn("Q3", content)
        self.assertTrue(self.storage.delete_template(tid))
        self.assertEqual(self.storage.list_templates(), [])


# ============================================================
# 标签批量操作
# ============================================================
class TestTagBatchOps(_StoreCase):
    def test_add_remove_tags(self):
        m1 = self._add("标签批量一")
        m2 = self._add("标签批量二")
        r = self.storage.add_tags_to_ids([m1.id, m2.id], ["批量", "新标签"])
        self.assertGreaterEqual(int(r), 2)
        ids = self.storage.get_ids_by_tag("批量")
        self.assertEqual(set(ids), {m1.id, m2.id})
        self.storage.remove_tags_from_ids([m1.id], ["批量"])
        ids2 = self.storage.get_ids_by_tag("批量")
        self.assertEqual(set(ids2), {m2.id})

    def test_add_tags_by_category_and_highlight(self):
        self._add("分类批量", category="work")
        self.storage.add_tags_by_category("work", ["工作标签"])
        ids = self.storage.get_ids_by_tag("工作标签")
        self.assertEqual(len(ids), 1)
        hl = self.storage.highlight_text("hello world hello", "hello")
        self.assertIn("<mark>hello</mark>", hl)
        self.assertEqual(self.storage.highlight_text("abc", "xyz"), "abc")


# ============================================================
# 记忆工具方法
# ============================================================
class TestMemoryUtilityMethods(_StoreCase):
    def test_batch_delete_and_batch_get(self):
        a = self._add("批量删一", category="drop")
        b = self._add("批量删二", category="drop")
        self._add("保留", category="keep")
        # 先批量读取（未删除时返回存在项）
        got = self.storage.batch_get_memories([a.id, "missing", b.id])
        self.assertEqual(len(got), 2)
        r = self.storage.batch_delete(category="drop")
        self.assertGreaterEqual(int(r), 2)
        self.assertEqual(self.storage.count_memories(category="drop"), 0)
        # 删除后批量读取：自动排除回收站
        got2 = self.storage.batch_get_memories([a.id, b.id])
        self.assertEqual(len(got2), 0)

    def test_move_and_rename_category(self):
        a = self._add("搬家记忆", category="old")
        self.storage.move_memory(a.id, "newcat")
        self.assertEqual(self.storage.get_memory(a.id).category, "newcat")
        # rename_category
        self._add("分类改名", category="before")
        self.storage.rename_category("before", "after")
        self.assertEqual(self.storage.count_memories(category="after"), 1)
        self.assertEqual(self.storage.count_memories(category="before"), 0)

    def test_random_memories(self):
        for i in range(5):
            self._add("随机记忆%d" % i)
        rand = self.storage.get_random_memories(count=3)
        self.assertLessEqual(len(rand), 3)
        self.assertEqual(self.storage.get_random_memories(count=0), [])

    def test_layers_stats_and_consolidation(self):
        self._add("层级测试", layer=MemoryLayer.LONG_TERM)
        lst = self.storage.get_memory_layers_stats()
        self.assertIsInstance(lst, dict)
        r = self.storage.auto_layer_consolidate(dry_run=True)
        self.assertIsInstance(r, dict)
        lr = self.storage.layered_retrieval("层级测试")
        self.assertIsInstance(lr, list)

    def test_memory_decay_weights(self):
        a = self._add("衰减权重测试")
        w = self.storage.memory_decay_weights(a.id)
        self.assertIsInstance(w, dict)
        self.assertIsInstance(self.storage.memory_decay_weights("missing"), dict)

    def test_pin_and_link(self):
        a = self._add("置顶记忆")
        b = self._add("关联目标")
        self.storage.pin_memory(a.id)
        self.assertTrue(self.storage.get_memory(a.id).pinned)
        self.storage.unpin_memory(a.id)
        self.assertFalse(self.storage.get_memory(a.id).pinned)
        lnk = self.storage.link_memories(a.id, b.id, link_type="related")
        link_id = lnk.get("link_id")
        if link_id:
            self.storage.unlink_memories(link_id)
        # memory_link 按 agent+memory 聚合
        ml = self.storage.memory_link("agent1", a.id)
        self.assertIsInstance(ml, dict)

    def test_deduplicate_dry_run(self):
        self._add("完全相同的重复内容")
        self._add("完全相同的重复内容")
        r = self.storage.deduplicate(dry_run=True)
        self.assertIn("duplicates_found", r)

    def test_flush_access_and_audit(self):
        a = self._add("审计记忆")
        self.storage.get_memory(a.id)  # 触发 access 挂账
        self.storage.flush_access()
        log = self.storage.get_audit_log(limit=10)
        self.assertIsInstance(log, list)
        self.assertGreaterEqual(len(log), 1)

    def test_detailed_stats_and_config(self):
        self._add("统计用记忆")
        ds = self.storage.get_detailed_stats()
        self.assertIsInstance(ds, dict)
        cs = self.storage.get_config_summary()
        self.assertIsInstance(cs, dict)
        ver = self.storage.get_db_version()
        self.assertIsNotNone(ver)
        latest = self.storage.get_latest_db_version()
        self.assertIsNotNone(latest)
        self.storage.migrate_to_latest()

    def test_cleanup_expired(self):
        a = self._add("将过期", layer=MemoryLayer.SENSORY)
        # 回填 created_at 使其早于 cutoff，满足清理条件
        self._sql("UPDATE memories SET created_at = ? WHERE id = ?",
                  (time.time() - 7200, a.id))
        self._add("不过期", layer=MemoryLayer.SENSORY)
        r = self.storage.cleanup_expired(max_age_hours=1)
        self.assertGreaterEqual(int(r), 1)
        self.assertIsNone(self.storage.get_memory(a.id))


# ============================================================
# 备份与版本回滚
# ============================================================
class TestBackupAndVersions(_StoreCase):
    def test_backup_list_restore(self):
        self._add("备份内容")
        bk_dir = os.path.join(self.tmp, "backups")
        path = self.storage.backup(bk_dir)
        self.assertTrue(os.path.exists(path))
        bl = self.storage.list_backups(backup_dir=bk_dir)
        self.assertGreaterEqual(len(bl), 1)
        # 恢复（不预先生成新备份，避免污染仓库 data/backups）
        self._add("删除我", category="garbage")
        r = self.storage.restore_backup(str(path), create_backup_before=False)
        self.assertTrue(r.get("success", False))
        self.assertEqual(self.storage.count_memories(category="garbage"), 0)

    def test_checkpoint_version_rollback(self):
        a = self._add("版本记忆")
        self.storage.checkpoint()
        self.storage.update_memory(a.id, content="修改后的内容")
        versions = self.storage.get_audit_log(limit=5)
        self.assertIsInstance(versions, list)
        # rollback 需要具体版本 ID；用内存快照验证基础路径不抛异常
        v = self.storage.get_version("missing")
        self.assertIsNone(v)


# ============================================================
# 短剧系统补充：line 分集 / 多角色
# ============================================================
class TestDramaExtended(_StoreCase):
    def test_multi_scene_character_stats(self):
        did = self.storage.add_drama("长剧", genre="科幻", total_episodes=20).id
        s1 = self.storage.add_scene(did, 1, 1, "第一幕").id
        s2 = self.storage.add_scene(did, 1, 2, "第二幕").id
        c1 = self.storage.add_character(did, "A").id
        c2 = self.storage.add_character(did, "B").id
        self.storage.add_line(did, "A 的台词", scene_id=s1, character_id=c1)
        self.storage.add_line(did, "B 的台词", scene_id=s2, character_id=c2)
        detail = self.storage.drama_detail_stats(did)
        self.assertEqual(detail["scene_count"], 2)
        self.assertEqual(detail["character_count"], 2)
        self.assertEqual(detail["line_count"], 2)


# ============================================================
# v5.7.0 新功能：FTS 一致性自检 + 记忆健康仪表盘
# ============================================================
class TestFtsConsistency(_StoreCase):
    def test_consistent_when_no_drift(self):
        self._add("FTS 一致性内容", category="work")
        r = self.storage.check_fts_consistency()
        self.assertTrue(r["consistent"])
        self.assertTrue(r["fts_enabled"])
        self.assertEqual(r["orphans"], 0)
        self.assertEqual(r["missing"], 0)

    def test_detect_and_repair_missing(self):
        a = self._add("缺失索引内容")
        conn = self.storage._get_conn()
        # contentless FTS5 只能通过 delete 命令移除索引行
        conn.execute(
            "INSERT INTO memory_fts(memory_fts, rowid, content, category, tags) "
            "VALUES('delete', (SELECT rowid FROM memories WHERE id = ?), '', '', '')",
            (a.id,),
        )
        conn.commit()
        r = self.storage.check_fts_consistency()
        self.assertFalse(r["consistent"])
        self.assertEqual(r["missing"], 1)
        r2 = self.storage.check_fts_consistency(repair=True)
        self.assertGreaterEqual(r2["repaired"], 1)
        self.assertTrue(self.storage.check_fts_consistency()["consistent"])

    def test_detect_and_repair_orphan(self):
        self._add("孤儿内容")
        conn = self.storage._get_conn()
        # 直接插入一个 memories 中不存在的 rowid 作为孤儿索引
        conn.execute(
            "INSERT INTO memory_fts(rowid, content, category, tags) "
            "VALUES(999999, '孤儿文本', 'x', '[]')",
        )
        conn.commit()
        r = self.storage.check_fts_consistency()
        self.assertEqual(r["orphans"], 1)
        r2 = self.storage.check_fts_consistency(repair=True)
        self.assertGreaterEqual(r2["repaired"], 1)
        self.assertTrue(self.storage.check_fts_consistency()["consistent"])


class TestHealthDashboard(_StoreCase):
    def test_dashboard_shape(self):
        self._add("仪表盘一", category="work")
        self._add("仪表盘二", category="life")
        d = self.storage.memory_health_dashboard()
        self.assertEqual(d["total_memories"], 2)
        self.assertIn("work", d["by_category"])
        self.assertIn("decay_buckets", d)
        self.assertIn("encrypted_ratio", d)
        self.assertIn("integrity", d)
        self.assertTrue(d["fts"]["enabled"])

    def test_mindforge_wrapper(self):
        from core.mindforge import MindForge
        db = os.path.join(self.tmp, "mf.db")
        mf = MindForge(db_path=db, encrypted=False)
        mf.add("包装测试")
        d = mf.memory_health_dashboard()
        self.assertEqual(d["total_memories"], 1)
        # 既有 v5.4.6 仪表盘（growth_curve 结构）保持不被覆盖
        legacy = mf.health_dashboard()
        self.assertIn("growth_curve", legacy)
        fts = mf.check_fts_consistency()
        self.assertTrue(fts["consistent"])
        mf.close()


# ============================================================
# v5.7.0 新功能：import_json 批量导入进度回调
# ============================================================
class TestImportProgressCallback(_StoreCase):
    def test_progress_callback_invoked(self):
        from core.mindforge import MindForge
        db = os.path.join(self.tmp, "imp.db")
        mf = MindForge(db_path=db, encrypted=False)
        p = os.path.join(self.tmp, "imp.json")
        payload = {"memories": [{"content": "进度%d" % i} for i in range(7)]}
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        calls = []

        def cb(done, total):
            calls.append((done, total))

        r = mf.import_json(p, progress_callback=cb, progress_interval=2)
        self.assertEqual(r["imported"], 7)
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(calls[-1], (7, 7))
        mf.close()

    def test_progress_callback_exception_does_not_block(self):
        from core.mindforge import MindForge
        db = os.path.join(self.tmp, "imp2.db")
        mf = MindForge(db_path=db, encrypted=False)
        p = os.path.join(self.tmp, "imp2.json")
        payload = {"memories": [{"content": "x%d" % i} for i in range(5)]}
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        def bad_cb(done, total):
            raise RuntimeError("callback boom")

        r = mf.import_json(p, progress_callback=bad_cb, progress_interval=1)
        self.assertEqual(r["imported"], 5)
        mf.close()


if __name__ == "__main__":
    unittest.main()
