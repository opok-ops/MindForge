"""
回收站硬删数据主权守卫（2026-11-10 数据/安全专项审计发现）

缺陷形态：MindForge.delete(id, hard_delete=True) 对已软删（trash 态）
条目静默返回 False——get_memory 过滤 trash（WHERE category != 'trash'），
上层 None 分支直接拒绝，CLI 对用户显示「记忆不存在」。物理数据仍在
库中，误导用户以为已彻底清除。GDPR erase 语义（storage 权利清单）
要求：能软删活条目，就必须能物理清除其 trash 态。

修复：delete() None 分支 + hard_delete 时查 is_in_trash 放行物理清除
（隐私读检查不适用：内容已在 trash，仅物理清除；审计仍同事务写入）。

对照组语义（不得回归）：
- 活条目正常软删/硬删路径不变；
- 对 trash 条目软删（重复软删）仍返回 False；
- 不存在的 id 一切删除返回 False。
"""

import os
import sys
import shutil
import sqlite3
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindforge import MindForge
from mindforge.core.types import MemoryConfig


def _mk(tmp):
    mf = MindForge(config=MemoryConfig(
        db_path=os.path.join(tmp, "m.db"), key_file=None, encrypted=False))
    mf.add("守卫探针 alpha-key-1 活条目")
    mf.add("守卫探针 beta-key-2 待删条目")
    raw = sqlite3.connect(os.path.join(tmp, "m.db"))
    # memories.id 是 UUID（无序），必须按内容锚定——SELECT 顺序映射会让
    # aid/bid 张冠李戴（本文件首次编写即踩此坑，ghost 断言假失败）。
    aid = raw.execute(
        "SELECT id FROM memories WHERE content LIKE '%alpha%'").fetchone()[0]
    bid = raw.execute(
        "SELECT id FROM memories WHERE content LIKE '%beta%'").fetchone()[0]
    raw.close()
    return mf, (aid, bid)


class TestTrashHardDelete(unittest.TestCase):

    def test_hard_delete_soft_deleted_entry(self):
        """核心：软删后 hard_delete 必须物理清除（此前静默 False）。"""
        tmp = tempfile.mkdtemp(prefix="mf_thd_")
        try:
            mf, (aid, bid) = _mk(tmp)
            self.assertTrue(mf.delete(bid))                       # 软删
            self.assertIsNone(mf.get(bid))                        # trash 被 get 过滤
            self.assertTrue(mf.delete(bid, hard_delete=True))     # ← 修复点
            raw = sqlite3.connect(os.path.join(tmp, "m.db"))
            rows = raw.execute(
                "SELECT count(*) FROM memories WHERE id = ?", (bid,)).fetchone()[0]
            fts = raw.execute("SELECT count(*) FROM memory_fts").fetchone()[0]
            raw.close()
            self.assertEqual(rows, 0, "trash 条目未被物理清除")
            self.assertEqual(fts, 1, "FTS 未同步（应只剩活条目）")
            self.assertEqual(len(mf.search("alpha-key-1").chunks), 1)
            # 注意：不能断言 search("beta-key-2") chunks==0——fuzzy 宽召回
            # 会把相近活条目（alpha）召回给 beta 查询（tfidf+fuzzy 策略设计）。
            # 正确断言：召回内容中不含已物理删除的 beta 内容（无幽灵）。
            ghost = [c for c in mf.search("beta-key-2").chunks
                     if "beta-key-2" in getattr(c, "content", "")]
            self.assertEqual(len(ghost), 0, "已物理删除内容被召回（真幽灵）")
            mf.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_soft_delete_on_trash_still_false(self):
        """对照：对 trash 条目再软删仍 False（重复软删无意义）。"""
        tmp = tempfile.mkdtemp(prefix="mf_thd2_")
        try:
            mf, (aid, bid) = _mk(tmp)
            mf.delete(bid)
            self.assertFalse(mf.delete(bid))   # 软删 trash = False
            mf.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_nonexistent_id_hard_delete_false(self):
        """对照：不存在 id 一切删除 False（不因修复误报成功）。"""
        tmp = tempfile.mkdtemp(prefix="mf_thd3_")
        try:
            mf, (aid, bid) = _mk(tmp)
            self.assertFalse(mf.delete("ghost-id-404", hard_delete=True))
            self.assertFalse(mf.delete("ghost-id-404"))
            mf.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_is_in_trash_query(self):
        """storage.is_in_trash 三态：活条目 False / 软删后 True / 物理删后 False。"""
        tmp = tempfile.mkdtemp(prefix="mf_thd4_")
        try:
            mf, (aid, bid) = _mk(tmp)
            st = mf._storage
            self.assertFalse(st.is_in_trash(bid))
            mf.delete(bid)
            self.assertTrue(st.is_in_trash(bid))
            mf.delete(bid, hard_delete=True)
            self.assertFalse(st.is_in_trash(bid))
            mf.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
