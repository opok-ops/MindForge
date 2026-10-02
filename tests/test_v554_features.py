"""
MindForge v5.5.4 功能测试
覆盖：记忆合并、最常访问、最近访问、批量更新、标签统计、索引一致性检查、
统计过滤修复（get_stats/count_memories 排除软删除）
"""

import os
import time
import tempfile
import pytest

from mindforge import MindForge


@pytest.fixture
def mf():
    """每个测试使用独立的临时数据库"""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    instance = MindForge(db_path=db_path, encrypted=False)
    yield instance
    instance.close()
    if os.path.exists(db_path):
        os.unlink(db_path)


class TestMemoryMerge:
    """v5.5.4: 记忆合并"""

    def test_merge_basic(self, mf):
        """基本合并：内容去重、标签并集"""
        e1 = mf.add("第一行内容\n第二行内容", category="work", tags=["tag1", "tag2"])
        e2 = mf.add("第二行内容\n第三行内容", category="work", tags=["tag2", "tag3"])

        result = mf.merge_memories(e1.id, e2.id)
        assert result is not None
        assert result.id == e2.id

        # 内容按行去重
        lines = result.content.splitlines()
        assert "第一行内容" in lines
        assert "第二行内容" in lines
        assert "第三行内容" in lines
        assert len(lines) == 3

        # 标签并集
        assert set(result.tags) == {"tag1", "tag2", "tag3"}

        # source 已被软删除（category 变为 trash）
        # v5.5.5 fix: get_memory 已排除回收站，直接查 SQL 验证
        conn = mf._storage._get_conn()
        row = conn.execute(
            "SELECT category FROM memories WHERE id = ?", (e1.id,)
        ).fetchone()
        assert row is not None
        assert row["category"] == "trash"

    def test_merge_same_id(self, mf):
        """source 和 target 相同则失败"""
        e1 = mf.add("测试内容")
        result = mf.merge_memories(e1.id, e1.id)
        assert result is None

    def test_merge_importance_takes_higher(self, mf):
        """重要性取较高值"""
        from mindforge.core.types import Importance
        e1 = mf.add("低重要度", importance=Importance.LOW)
        e2 = mf.add("高重要度", importance=Importance.HIGH)
        result = mf.merge_memories(e1.id, e2.id)
        assert result.importance == Importance.HIGH

        # 反过来也一样
        e3 = mf.add("低重要度2", importance=Importance.LOW)
        e4 = mf.add("高重要度2", importance=Importance.CRITICAL)
        result2 = mf.merge_memories(e4.id, e3.id)
        assert result2.importance == Importance.CRITICAL

    def test_merge_access_count_adds(self, mf):
        """访问次数相加"""
        e1 = mf.add("记忆A")
        e2 = mf.add("记忆B")
        # 各访问几次
        for _ in range(3):
            mf.get(e1.id)
        for _ in range(5):
            mf.get(e2.id)

        # 重新获取确认访问次数
        e1_fetched = mf._storage.get_memory(e1.id)
        e2_fetched = mf._storage.get_memory(e2.id)

        result = mf.merge_memories(e1.id, e2.id)
        assert result is not None
        # 访问次数 = A + B（初始 1 + 实际访问次数）
        assert result.access_count >= 8  # 至少 8 次

    def test_merge_source_moved_to_trash(self, mf):
        """source 记忆被移入回收站"""
        e1 = mf.add("源记忆")
        e2 = mf.add("目标记忆")
        mf.merge_memories(e1.id, e2.id)

        # 从 storage 层直接查（绕过 category 过滤）
        conn = mf._storage._get_conn()
        row = conn.execute(
            "SELECT category FROM memories WHERE id = ?", (e1.id,)
        ).fetchone()
        assert row["category"] == "trash"


class TestMostAccessed:
    """v5.5.4: 最常访问"""

    def test_most_accessed_basic(self, mf):
        """按访问次数排序"""
        e1 = mf.add("访问最多")
        e2 = mf.add("访问中等")
        e3 = mf.add("访问最少")
        # 访问次数不同
        for _ in range(5):
            mf.get(e1.id)
        for _ in range(3):
            mf.get(e2.id)
        mf.get(e3.id)

        result = mf.most_accessed(limit=10)
        assert len(result) == 3
        assert result[0].id == e1.id
        assert result[1].id == e2.id
        assert result[2].id == e3.id

    def test_most_accessed_limit(self, mf):
        """限制返回数量"""
        for i in range(10):
            mf.add(f"记忆{i}")
        result = mf.most_accessed(limit=3)
        assert len(result) == 3

    def test_most_accessed_by_category(self, mf):
        """按分类筛选"""
        mf.add("工作记忆", category="work")
        mf.add("生活记忆", category="life")
        mf.add("另一个工作记忆", category="work")

        result = mf.most_accessed(category="work")
        assert len(result) == 2
        for r in result:
            assert r.category == "work"

    def test_most_accessed_empty(self, mf):
        """空数据库返回空列表"""
        result = mf.most_accessed()
        assert result == []


class TestRecentlyAccessed:
    """v5.5.4: 最近访问"""

    def test_recently_accessed_basic(self, mf):
        """按最近访问时间排序"""
        e1 = mf.add("最早访问")
        e2 = mf.add("中间访问")
        e3 = mf.add("最晚访问")

        # 确保访问时间有差异
        mf.get(e1.id)
        time.sleep(0.01)
        mf.get(e2.id)
        time.sleep(0.01)
        mf.get(e3.id)

        result = mf.recently_accessed(limit=10)
        assert len(result) >= 3
        assert result[0].id == e3.id
        assert result[1].id == e2.id

    def test_recently_accessed_limit(self, mf):
        """限制返回数量"""
        for i in range(10):
            e = mf.add(f"记忆{i}")
            mf.get(e.id)
        result = mf.recently_accessed(limit=5)
        assert len(result) == 5

    def test_recently_accessed_by_layer(self, mf):
        """按层级筛选"""
        from mindforge.core.types import MemoryLayer
        e1 = mf.add("短期记忆")
        e2 = mf.add("长期记忆")
        # 直接通过 SQL 更新层级（storage 层没有单独的 update_layer 方法）
        conn = mf._storage._get_conn()
        conn.execute(
            "UPDATE memories SET layer = ? WHERE id = ?",
            (MemoryLayer.LONG_TERM.value, e2.id)
        )
        conn.commit()
        mf.get(e1.id)
        mf.get(e2.id)

        result = mf.recently_accessed(layer=MemoryLayer.LONG_TERM)
        assert len(result) == 1
        assert result[0].id == e2.id


class TestBulkUpdateByFilter:
    """v5.5.4: 按筛选批量更新"""

    def test_bulk_update_add_tags_by_category(self, mf):
        """按分类批量添加标签"""
        mf.add("工作1", category="work", tags=["existing"])
        mf.add("工作2", category="work")
        mf.add("生活1", category="life")

        count = mf.bulk_update_by_filter(
            category="work",
            updates={"add_tags": ["new-tag"]}
        )
        assert count == 2

        # 验证标签已添加
        work_entries = mf.list(category="work", limit=10)
        for e in work_entries:
            assert "new-tag" in e.tags

    def test_bulk_update_remove_tags_by_tag(self, mf):
        """按标签批量移除标签"""
        mf.add("A", tags=["tag1", "tag2"])
        mf.add("B", tags=["tag1", "tag3"])
        mf.add("C", tags=["tag2"])

        count = mf.bulk_update_by_filter(
            tag="tag1",
            updates={"remove_tags": ["tag2"]}
        )
        assert count == 2  # 只有含 tag1 的记忆被处理

    def test_bulk_update_category_and_importance(self, mf):
        """批量更新分类和重要性"""
        from mindforge.core.types import Importance
        mf.add("旧分类1", category="oldcat", importance=Importance.LOW)
        mf.add("旧分类2", category="oldcat", importance=Importance.MEDIUM)
        mf.add("其他分类", category="other")

        count = mf.bulk_update_by_filter(
            category="oldcat",
            updates={"new_category": "newcat", "new_importance": Importance.HIGH}
        )
        assert count == 2

        results = mf.list(category="newcat", limit=10)
        assert len(results) == 2
        for r in results:
            assert r.importance == Importance.HIGH

    def test_bulk_update_starred(self, mf):
        """批量设置星标"""
        mf.add("A", category="test")
        mf.add("B", category="test")
        mf.add("C", category="other")

        count = mf.bulk_update_by_filter(
            category="test",
            updates={"starred": True}
        )
        assert count == 2

        test_entries = mf.list(category="test", limit=10)
        for e in test_entries:
            assert e.starred is True

    def test_bulk_update_no_filter_returns_zero(self, mf):
        """无筛选条件返回 0"""
        mf.add("测试")
        count = mf.bulk_update_by_filter(updates={"starred": True})
        assert count == 0


class TestTagStats:
    """v5.5.4: 标签统计"""

    def test_tag_stats_basic(self, mf):
        """基本标签统计"""
        mf.add("A", tags=["python", "coding"])
        mf.add("B", tags=["python", "ai"])
        mf.add("C", tags=["python", "coding", "ai"])
        mf.add("D")  # 无标签

        stats = mf.tag_stats()
        assert stats["total_tags"] == 3
        assert stats["memories_with_tags"] == 3
        assert stats["memories_without_tags"] == 1
        assert stats["avg_tags_per_memory"] > 0

        # python 出现 3 次最多
        assert stats["top_tags"][0]["tag"] == "python"
        assert stats["top_tags"][0]["count"] == 3

    def test_tag_stats_top_limit(self, mf):
        """限制 Top 数量"""
        for i in range(10):
            mf.add(f"记忆{i}", tags=[f"tag{i}"])
        stats = mf.tag_stats(limit=3)
        assert len(stats["top_tags"]) == 3

    def test_tag_stats_empty(self, mf):
        """空数据库统计"""
        stats = mf.tag_stats()
        assert stats["total_tags"] == 0
        assert stats["memories_with_tags"] == 0
        assert stats["avg_tags_per_memory"] == 0

    def test_tag_stats_categories(self, mf):
        """标签涉及的分类统计"""
        mf.add("工作笔记", category="work", tags=["important"])
        mf.add("生活笔记", category="life", tags=["important"])
        mf.add("学习笔记", category="study", tags=["important"])

        stats = mf.tag_stats()
        important_tag = next(t for t in stats["top_tags"] if t["tag"] == "important")
        assert important_tag["count"] == 3
        assert set(important_tag["categories"]) == {"work", "life", "study"}


class TestIndexConsistency:
    """v5.5.4: 索引一致性检查"""

    def test_check_consistency_clean(self, mf):
        """正常情况下索引一致"""
        mf.add("测试记忆1", category="general", tags=["tag1"])
        mf.add("测试记忆2", category="general", tags=["tag2"])

        result = mf.check_index_consistency()
        assert result["status"] == "ok"
        assert result["issues_found"] == 0
        assert result["total_main"] == 2

    def test_check_consistency_after_delete(self, mf):
        """删除后记索引可能不一致（contentless FTS5 需手动维护）

        直接从 FTS 表删除记录（模拟索引缺失），验证能检测出问题。
        """
        e = mf.add("待删除")
        eid = e.id
        # 直接从 FTS 表删除对应记录（模拟索引缺失）
        conn = mf._storage._get_conn()
        conn.execute(
            "INSERT INTO memory_fts(memory_fts, rowid) VALUES('delete', "
            "(SELECT rowid FROM memories WHERE id = ?))",
            (eid,)
        )
        conn.commit()

        result = mf.check_index_consistency()
        assert result["status"] == "issues_found"
        assert result["issues_found"] > 0
        assert len(result["issues"]["missing_in_fts"]) == 1


class TestStatsFilterFix:
    """v5.5.4: get_stats / count_memories 排除软删除修复"""

    def test_count_memories_excludes_trash(self, mf):
        """count_memories 不统计回收站"""
        mf.add("记忆1")
        mf.add("记忆2")
        e3 = mf.add("要删除的记忆")
        mf.delete(e3.id, hard_delete=False)

        count = mf.count_memories()
        assert count == 2  # 排除了软删除的 1 条

    def test_get_stats_excludes_trash(self, mf):
        """stats() 不统计回收站，且有 trash_count 字段"""
        mf.add("A")
        mf.add("B")
        e = mf.add("C")
        mf.delete(e.id, hard_delete=False)

        stats = mf.stats()
        assert stats["total"] == 2
        assert "trash_count" in stats
        assert stats["trash_count"] == 1

    def test_get_stats_starred_excludes_trash(self, mf):
        """starred_count 也排除回收站"""
        e1 = mf.add("星标1", starred=True)
        e2 = mf.add("星标2", starred=True)
        mf.delete(e2.id, hard_delete=False)

        stats = mf.stats()
        assert stats["starred_count"] == 1
