"""
MindForge v5.5.6 功能测试
覆盖：
- 置顶功能（pin/unpin/list_pinned/add pinned/list pinned filter）
- 批量获取记忆（batch_get）
- 记忆时间线视图（timeline）
- 搜索建议（search_suggestions）
- 添加前去重检测（check_duplicates）
- 按 ID 批量增删标签（add_tags_to_memories / remove_tags_from_memories）
- Bug 修复：fuzzy_search 空查询、rename_tag 大小写不敏感、
  batch_add 支持 expires_at/pinned、stats pinned_count
"""

import os
import time
import tempfile
import pytest

from mindforge import MindForge, __version__


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


# ===== 版本号验证 =====

class TestVersion:
    def test_version_at_least_557(self):
        """版本号应 >= 5.5.7"""
        parts = [int(x) for x in __version__.split(".")]
        assert parts >= [5, 5, 7], f"版本 {__version__} < 5.5.7"

    def test_pyproject_version(self):
        """pyproject.toml 动态版本指向 core/version.py 的唯一真值"""
        import re
        pyproject_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pyproject.toml")
        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r'attr\s*=\s*"mindforge\.core\.version\.__version__"', content)
        assert m, "pyproject.toml 中未找到 dynamic version attr"
        assert 'dynamic = ["version"]' in content or '"version"' in content, \
            "pyproject.toml 应声明 dynamic version"
        from mindforge.core.version import __version__ as _truth
        assert _truth == __version__, f"core/version.py {_truth} != __version__ {__version__}"


# ===== 置顶功能 =====

class TestPinning:
    def test_pin_basic(self, mf):
        """基本置顶操作"""
        e = mf.add("测试记忆")
        assert e.pinned is False
        result = mf.pin(e.id)
        assert result is True
        # 重新获取确认
        fetched = mf.get(e.id)
        assert fetched.pinned is True

    def test_unpin(self, mf):
        """取消置顶"""
        e = mf.add("测试记忆")
        mf.pin(e.id)
        assert mf.get(e.id).pinned is True
        result = mf.unpin(e.id)
        assert result is True
        assert mf.get(e.id).pinned is False

    def test_pin_nonexistent(self, mf):
        """置顶不存在的记忆返回 False"""
        result = mf.pin("nonexistent-id")
        assert result is False

    def test_add_with_pinned(self, mf):
        """添加时直接置顶"""
        e = mf.add("置顶记忆", pinned=True)
        assert e.pinned is True
        fetched = mf.get(e.id)
        assert fetched.pinned is True

    def test_list_pinned(self, mf):
        """列出所有置顶记忆"""
        e1 = mf.add("普通记忆")
        e2 = mf.add("置顶记忆1", pinned=True)
        e3 = mf.add("置顶记忆2", pinned=True)
        pinned = mf.list_pinned(limit=10)
        assert len(pinned) == 2
        pinned_ids = {p.id for p in pinned}
        assert e2.id in pinned_ids
        assert e3.id in pinned_ids
        assert e1.id not in pinned_ids

    def test_list_pinned_empty(self, mf):
        """无置顶记忆时返回空列表"""
        mf.add("普通记忆")
        assert mf.list_pinned() == []

    def test_list_filter_pinned(self, mf):
        """list() 支持 pinned 筛选"""
        mf.add("普通1")
        mf.add("置顶1", pinned=True)
        mf.add("普通2")
        mf.add("置顶2", pinned=True)

        pinned_only = mf.list(pinned=True, limit=10)
        assert len(pinned_only) == 2
        for e in pinned_only:
            assert e.pinned is True

        unpinned = mf.list(pinned=False, limit=10)
        assert len(unpinned) == 2
        for e in unpinned:
            assert e.pinned is False

    def test_pinned_priority_in_list(self, mf):
        """置顶记忆在 list() 中优先展示"""
        e1 = mf.add("普通记忆")
        time.sleep(0.01)
        e2 = mf.add("置顶记忆", pinned=True)
        # 默认按 created_at desc，置顶应排第一
        result = mf.list(limit=10, sort_by="created_at", sort_order="desc")
        assert result[0].id == e2.id
        assert result[0].pinned is True

    def test_update_pinned(self, mf):
        """通过 update() 设置 pinned"""
        e = mf.add("测试")
        mf.update(e.id, pinned=True)
        assert mf.get(e.id).pinned is True
        mf.update(e.id, pinned=False)
        assert mf.get(e.id).pinned is False

    def test_pinned_survives_delete_restore(self, mf):
        """置顶状态在删除恢复后保持"""
        e = mf.add("置顶记忆", pinned=True)
        mf.delete(e.id, hard_delete=False)
        mf.restore(e.id)
        restored = mf.get(e.id)
        assert restored is not None
        assert restored.pinned is True


# ===== 批量获取 =====

class TestBatchGet:
    def test_batch_get_basic(self, mf):
        """基本批量获取"""
        e1 = mf.add("记忆A")
        e2 = mf.add("记忆B")
        e3 = mf.add("记忆C")

        result = mf.batch_get([e1.id, e2.id, e3.id])
        assert len(result) == 3
        assert result[0].id == e1.id
        assert result[1].id == e2.id
        assert result[2].id == e3.id

    def test_batch_get_empty(self, mf):
        """空列表返回空"""
        assert mf.batch_get([]) == []
        assert mf.batch_get(None) == []

    def test_batch_get_nonexistent(self, mf):
        """不存在的 ID 被跳过"""
        e1 = mf.add("记忆A")
        result = mf.batch_get([e1.id, "fake-id-1", "fake-id-2"])
        assert len(result) == 1
        assert result[0].id == e1.id

    def test_batch_get_preserves_order(self, mf):
        """保持输入顺序"""
        e1 = mf.add("第一个")
        e2 = mf.add("第二个")
        e3 = mf.add("第三个")
        result = mf.batch_get([e3.id, e1.id, e2.id])
        assert [r.id for r in result] == [e3.id, e1.id, e2.id]

    def test_batch_get_deduplicates(self, mf):
        """重复 ID 去重"""
        e1 = mf.add("记忆A")
        result = mf.batch_get([e1.id, e1.id, e1.id])
        assert len(result) == 1


# ===== 时间线视图 =====

class TestTimeline:
    def test_timeline_basic(self, mf):
        """基本时间线分组"""
        mf.add("今天的记忆")
        result = mf.timeline()
        assert "today" in result
        assert "yesterday" in result
        assert "this_week" in result
        assert "this_month" in result
        assert "earlier" in result
        assert len(result["today"]) >= 1

    def test_timeline_empty(self, mf):
        """空数据库时间线全为空"""
        result = mf.timeline()
        assert result["today"] == []
        assert result["yesterday"] == []
        assert result["earlier"] == []

    def test_timeline_with_category_filter(self, mf):
        """按分类筛选时间线"""
        mf.add("工作记忆", category="work")
        mf.add("生活记忆", category="life")
        result = mf.timeline(category="work")
        assert len(result["today"]) == 1
        assert result["today"][0].category == "work"

    def test_timeline_total_count(self, mf):
        """时间线总数等于记忆数"""
        for i in range(5):
            mf.add(f"记忆{i}")
        result = mf.timeline()
        total = sum(len(v) for v in result.values())
        assert total == 5


# ===== 搜索建议 =====

class TestSearchSuggestions:
    def test_suggestions_tags(self, mf):
        """标签建议"""
        mf.add("A", tags=["python", "coding"])
        mf.add("B", tags=["python", "ai"])
        mf.add("C", tags=["java", "coding"])

        result = mf.search_suggestions("py")
        assert "python" in result["tags"]

    def test_suggestions_categories(self, mf):
        """分类建议"""
        mf.add("A", category="work")
        mf.add("B", category="workout")
        mf.add("C", category="life")

        result = mf.search_suggestions("wor")
        assert "work" in result["categories"]
        assert "workout" in result["categories"]

    def test_suggestions_empty_prefix(self, mf):
        """空前缀返回空"""
        mf.add("A", tags=["python"])
        result = mf.search_suggestions("")
        assert result["tags"] == []
        assert result["categories"] == []

    def test_suggestions_none_prefix(self, mf):
        """None 前缀返回空"""
        result = mf.search_suggestions(None)
        assert result["tags"] == []
        assert result["categories"] == []

    def test_suggestions_no_match(self, mf):
        """无匹配返回空列表"""
        mf.add("A", tags=["python"])
        result = mf.search_suggestions("zzz")
        assert result["tags"] == []
        assert result["categories"] == []

    def test_suggestions_limit(self, mf):
        """限制返回数量"""
        for i in range(15):
            mf.add(f"记忆{i}", tags=[f"tag{i}"])
        result = mf.search_suggestions("tag", limit=5)
        assert len(result["tags"]) <= 5


# ===== 添加前去重检测 =====

class TestCheckDuplicates:
    def test_exact_duplicate(self, mf):
        """完全相同内容检测"""
        mf.add("这是一条测试记忆内容用于去重检测")
        result = mf.check_duplicates("这是一条测试记忆内容用于去重检测")
        assert len(result) >= 1
        assert result[0]["match_type"] == "exact"
        assert result[0]["similarity"] == 1.0

    def test_high_similarity(self, mf):
        """高度相似内容检测"""
        mf.add("Python 是一种非常流行的编程语言，广泛用于数据分析和人工智能")
        result = mf.check_duplicates(
            "Python 是一种非常流行的编程语言，广泛用于数据分析",
            similarity_threshold=0.7
        )
        assert len(result) >= 1

    def test_no_duplicate(self, mf):
        """不相似内容返回空"""
        mf.add("今天天气真好，适合出去散步")
        result = mf.check_duplicates("量子力学中的薛定谔方程")
        assert result == []

    def test_empty_content(self, mf):
        """空内容返回空"""
        mf.add("测试")
        assert mf.check_duplicates("") == []
        assert mf.check_duplicates(None) == []

    def test_category_filter(self, mf):
        """按分类筛选去重"""
        mf.add("重复内容测试", category="work")
        mf.add("重复内容测试", category="life")
        result = mf.check_duplicates("重复内容测试", category="work")
        assert len(result) == 1
        assert result[0]["entry"].category == "work"

    def test_threshold(self, mf):
        """阈值控制"""
        mf.add("完全不同的内容一")
        # 低阈值应该能匹配到部分相似
        result_low = mf.check_duplicates("完全不同的内容", similarity_threshold=0.5)
        # 高阈值可能匹配不到
        result_high = mf.check_duplicates("完全不同的内容", similarity_threshold=0.99)
        assert len(result_low) >= len(result_high)


# ===== 按 ID 批量增删标签 =====

class TestBatchTagOperations:
    def test_add_tags_basic(self, mf):
        """基本批量添加标签"""
        e1 = mf.add("A", tags=["existing"])
        e2 = mf.add("B")
        count = mf.add_tags_to_memories([e1.id, e2.id], ["new-tag"])
        assert count == 2
        assert "new-tag" in mf.get(e1.id).tags
        assert "new-tag" in mf.get(e2.id).tags

    def test_add_tags_no_duplicate(self, mf):
        """添加已有标签不重复"""
        e = mf.add("A", tags=["mytag"])
        count = mf.add_tags_to_memories([e.id], ["mytag"])
        assert count == 0  # 无变化
        assert mf.get(e.id).tags.count("mytag") == 1

    def test_add_tags_empty(self, mf):
        """空参数返回 0"""
        assert mf.add_tags_to_memories([], ["tag"]) == 0
        e = mf.add("A")
        assert mf.add_tags_to_memories([e.id], []) == 0

    def test_add_tags_nonexistent(self, mf):
        """不存在的 ID 跳过"""
        e = mf.add("A")
        count = mf.add_tags_to_memories([e.id, "fake-id"], ["tag"])
        assert count == 1

    def test_remove_tags_basic(self, mf):
        """基本批量移除标签"""
        e1 = mf.add("A", tags=["tag1", "tag2"])
        e2 = mf.add("B", tags=["tag1", "tag3"])
        count = mf.remove_tags_from_memories([e1.id, e2.id], ["tag1"])
        assert count == 2
        assert "tag1" not in mf.get(e1.id).tags
        assert "tag1" not in mf.get(e2.id).tags
        assert "tag2" in mf.get(e1.id).tags

    def test_remove_tags_case_insensitive(self, mf):
        """移除标签大小写不敏感"""
        e = mf.add("A", tags=["MyTag", "other"])
        count = mf.remove_tags_from_memories([e.id], ["mytag"])
        assert count == 1
        tags = mf.get(e.id).tags
        assert "MyTag" not in tags
        assert "other" in tags

    def test_remove_tags_empty(self, mf):
        """空参数返回 0"""
        assert mf.remove_tags_from_memories([], ["tag"]) == 0


# ===== Bug 修复测试 =====

class TestFuzzySearchFix:
    def test_fuzzy_search_none_query(self, mf):
        """fuzzy_search None 查询不崩溃"""
        mf.add("测试内容")
        result = mf.fuzzy_search(None)
        assert result == []

    def test_fuzzy_search_empty_query(self, mf):
        """fuzzy_search 空字符串不崩溃"""
        mf.add("测试内容")
        result = mf.fuzzy_search("")
        assert result == []

    def test_fuzzy_search_whitespace_query(self, mf):
        """fuzzy_search 纯空白不崩溃"""
        mf.add("测试内容")
        result = mf.fuzzy_search("   ")
        assert result == []

    def test_fuzzy_search_normal(self, mf):
        """fuzzy_search 正常工作"""
        mf.add("Python 编程教程")
        result = mf.fuzzy_search("python", limit=10)
        assert len(result) >= 1


class TestRenameTagFix:
    def test_rename_tag_case_insensitive(self, mf):
        """重命名标签大小写不敏感"""
        e = mf.add("测试", tags=["MyTag", "other"])
        count = mf.rename_tag("mytag", "RenamedTag")
        assert count == 1
        tags = mf.get(e.id).tags
        assert "RenamedTag" in tags
        assert "MyTag" not in tags

    def test_rename_tag_empty_old(self, mf):
        """空旧标签名返回 0"""
        mf.add("测试", tags=["tag"])
        assert mf.rename_tag("", "new") == 0

    def test_rename_tag_none(self, mf):
        """None 参数返回 0"""
        assert mf.rename_tag(None, "new") == 0
        assert mf.rename_tag("old", None) == 0

    def test_rename_tag_dedup_after_rename(self, mf):
        """重命名后自动去重"""
        e = mf.add("测试", tags=["TagA", "taga", "other"])
        count = mf.rename_tag("taga", "TagA")
        assert count == 1
        tags = mf.get(e.id).tags
        assert tags.count("TagA") == 1


class TestBatchAddFix:
    def test_batch_add_with_pinned(self, mf):
        """batch_add 支持 pinned"""
        count = mf.batch_add([
            {"content": "普通记忆", "category": "test"},
            {"content": "置顶记忆", "category": "test", "pinned": True},
        ])
        assert count == 2
        pinned = mf.list_pinned()
        assert len(pinned) == 1

    def test_batch_add_with_expires_at(self, mf):
        """batch_add 支持 expires_at"""
        future = time.time() + 3600
        count = mf.batch_add([
            {"content": "有过期时间", "expires_at": future},
            {"content": "永不过期"},
        ])
        assert count == 2
        # 第一条还未过期，应能获取到
        all_memories = mf.list(limit=10)
        assert len(all_memories) == 2

    def test_batch_add_with_metadata(self, mf):
        """batch_add 支持 metadata"""
        count = mf.batch_add([
            {"content": "带元数据", "metadata": {"source": "test", "priority": 1}},
        ])
        assert count == 1
        entry = mf.list(limit=1)[0]
        assert entry.metadata.get("source") == "test"


class TestStatsPinnedCount:
    def test_stats_includes_pinned_count(self, mf):
        """stats() 包含 pinned_count"""
        mf.add("普通")
        mf.add("置顶", pinned=True)
        stats = mf.stats()
        assert "pinned_count" in stats
        assert stats["pinned_count"] == 1

    def test_stats_pinned_count_excludes_trash(self, mf):
        """pinned_count 排除回收站"""
        e = mf.add("置顶", pinned=True)
        mf.delete(e.id, hard_delete=False)
        stats = mf.stats()
        assert stats["pinned_count"] == 0


# ===== v5.5.6 Bug 修复追加测试 =====

class TestCheckDuplicatesHtmlSanitize:
    """Bug 1: check_duplicates 未对输入做 HTML 消毒"""

    def test_html_content_matches_sanitized(self, mf):
        """含 HTML 标签的输入应匹配消毒后存储的内容"""
        mf.add("bold text")
        result = mf.check_duplicates("<b>bold</b> text")
        assert len(result) >= 1
        assert result[0]["similarity"] == 1.0

    def test_html_content_partial_match(self, mf):
        """HTML 消毒后部分匹配（降低阈值验证）"""
        mf.add("Python 编程教程")
        # 消毒后输入为 "Python 编程"，与存储内容高度相似
        result = mf.check_duplicates("<i>Python</i> 编程", similarity_threshold=0.5)
        assert len(result) >= 1

    def test_script_tag_sanitized(self, mf):
        """script 标签被消毒（标签移除，内容保留）"""
        mf.add("alert(1)安全内容")
        # 消毒后 <script>alert(1)</script>安全内容 → alert(1)安全内容
        result = mf.check_duplicates("<script>alert(1)</script>安全内容")
        assert len(result) >= 1
        assert result[0]["similarity"] == 1.0


class TestAddTagsCaseInsensitive:
    """Bug 2: add_tags_to_ids 大小写敏感"""

    def test_add_tags_case_insensitive_dedup(self, mf):
        """添加大小写不同的已有标签不应重复"""
        e = mf.add("测试", tags=["MyTag"])
        count = mf.add_tags_to_memories([e.id], ["mytag"])
        assert count == 0  # 已存在（大小写不敏感），无变化
        tags = mf.get(e.id).tags
        assert len(tags) == 1
        assert tags[0] == "MyTag"  # 保留原始大小写

    def test_add_tags_new_case_insensitive(self, mf):
        """添加新标签（大小写不同）正常工作"""
        e = mf.add("测试", tags=["MyTag"])
        count = mf.add_tags_to_memories([e.id], ["OTHER"])
        assert count == 1
        tags = mf.get(e.id).tags
        assert "MyTag" in tags
        assert "OTHER" in tags

    def test_add_tags_preserves_original_case(self, mf):
        """去重时保留已有标签的原始大小写"""
        e = mf.add("测试", tags=["Important"])
        mf.add_tags_to_memories([e.id], ["important"])
        tags = mf.get(e.id).tags
        assert "Important" in tags
        assert "important" not in tags


class TestBatchAddTagsOrderPreserving:
    """Bug 3: batch_add_tags 使用 set 去重不保留顺序"""

    def test_batch_add_tags_preserves_order(self, mf):
        """batch_add_tags 应保留标签顺序"""
        e = mf.add("测试", tags=["a", "b", "c"])
        # 通过 storage 层直接调用 batch_add_tags
        count = mf._storage.batch_add_tags([e.id], ["d", "e"])
        assert count == 1
        tags = mf.get(e.id).tags
        assert tags == ["a", "b", "c", "d", "e"]

    def test_batch_add_tags_no_duplicate(self, mf):
        """batch_add_tags 不重复添加已有标签"""
        e = mf.add("测试", tags=["a", "b"])
        count = mf._storage.batch_add_tags([e.id], ["b", "c"])
        assert count == 1
        tags = mf.get(e.id).tags
        assert tags == ["a", "b", "c"]

    def test_batch_add_tags_case_insensitive(self, mf):
        """batch_add_tags 大小写不敏感去重"""
        e = mf.add("测试", tags=["MyTag"])
        count = mf._storage.batch_add_tags([e.id], ["mytag", "new"])
        assert count == 1
        tags = mf.get(e.id).tags
        assert tags == ["MyTag", "new"]


class TestBulkUpdateAddTagsConsistency:
    """bulk_update_by_filter add_tags 大小写一致性"""

    def test_bulk_update_add_tags_case_insensitive(self, mf):
        """bulk_update add_tags 大小写不敏感"""
        mf.add("A", category="test", tags=["MyTag"])
        count = mf.bulk_update_by_filter(
            category="test",
            updates={"add_tags": ["mytag", "new"]}
        )
        assert count == 1
        entry = mf.list(category="test")[0]
        assert "MyTag" in entry.tags
        assert "new" in entry.tags
        assert "mytag" not in entry.tags


class TestAppendTagsOrderPreserving:
    """Bug 3 修正: append_tags 保序去重"""

    def test_append_tags_preserves_order(self, mf):
        """append_tags 应保留标签顺序"""
        e = mf.add("测试", tags=["a", "b", "c"])
        result = mf._storage.append_tags(e.id, ["d", "e"])
        assert result is True
        tags = mf.get(e.id).tags
        assert tags == ["a", "b", "c", "d", "e"]

    def test_append_tags_no_duplicate(self, mf):
        """append_tags 不重复添加已有标签"""
        e = mf.add("测试", tags=["a", "b"])
        result = mf._storage.append_tags(e.id, ["b", "c"])
        assert result is True
        tags = mf.get(e.id).tags
        assert tags == ["a", "b", "c"]

    def test_append_tags_case_insensitive(self, mf):
        """append_tags 大小写不敏感去重"""
        e = mf.add("测试", tags=["MyTag"])
        result = mf._storage.append_tags(e.id, ["mytag", "new"])
        assert result is True
        tags = mf.get(e.id).tags
        assert tags == ["MyTag", "new"]

    def test_append_tags_no_change_returns_false(self, mf):
        """标签无变化时返回 False"""
        e = mf.add("测试", tags=["a", "b"])
        result = mf._storage.append_tags(e.id, ["A", "B"])
        assert result is False

    def test_append_tags_nonexistent(self, mf):
        """不存在的记忆返回 False"""
        result = mf._storage.append_tags("fake-id", ["tag"])
        assert result is False


class TestTagOpsFtsSync:
    """P1 Bug: 标签操作后 FTS 索引不同步（contentless FTS5 幽灵标签问题）

    v5.5.5 错误地在 UPDATE 后调用 _refresh_fts，导致 DELETE 用新值无法匹配旧索引条目。
    v5.5.6 修复：所有标签/内容更新方法改为 UPDATE 前用旧值 DELETE FTS、UPDATE 后用新值 INSERT FTS。
    """

    def _fts_contains_tag(self, mf, memory_id: str, tag: str) -> bool:
        """通过 FTS MATCH 查询验证某记忆的 tags 是否包含指定标签"""
        conn = mf._storage._get_conn()
        row = conn.execute(
            "SELECT m.id FROM memory_fts JOIN memories m ON memory_fts.rowid = m.rowid "
            "WHERE memory_fts MATCH ? AND m.id = ?",
            (tag, memory_id)
        ).fetchone()
        return row is not None

    def _fts_contains_content(self, mf, memory_id: str, keyword: str) -> bool:
        """通过 FTS MATCH 查询验证某记忆的 content 是否包含指定关键词"""
        conn = mf._storage._get_conn()
        row = conn.execute(
            "SELECT m.id FROM memory_fts JOIN memories m ON memory_fts.rowid = m.rowid "
            "WHERE memory_fts MATCH ? AND m.id = ?",
            (keyword, memory_id)
        ).fetchone()
        return row is not None

    def test_add_tags_to_ids_syncs_fts(self, mf):
        """add_tags_to_ids 添加标签后 FTS 索引同步更新"""
        e = mf.add("测试记忆内容", tags=["initial"])
        assert not self._fts_contains_tag(mf, e.id, "newtag")
        count = mf.add_tags_to_memories([e.id], ["newtag"])
        assert count == 1
        assert self._fts_contains_tag(mf, e.id, "newtag")

    def test_remove_tags_from_ids_syncs_fts(self, mf):
        """remove_tags_from_ids 删除标签后 FTS 索引同步更新（无幽灵标签）"""
        e = mf.add("测试记忆内容", tags=["oldtag", "other"])
        assert self._fts_contains_tag(mf, e.id, "oldtag")
        count = mf.remove_tags_from_memories([e.id], ["oldtag"])
        assert count == 1
        assert not self._fts_contains_tag(mf, e.id, "oldtag")
        assert self._fts_contains_tag(mf, e.id, "other")

    def test_batch_add_tags_syncs_fts(self, mf):
        """batch_add_tags 添加标签后 FTS 索引同步更新"""
        e = mf.add("测试记忆内容", tags=["initial"])
        assert not self._fts_contains_tag(mf, e.id, "batchnew")
        count = mf._storage.batch_add_tags([e.id], ["batchnew"])
        assert count == 1
        assert self._fts_contains_tag(mf, e.id, "batchnew")

    def test_batch_remove_tags_syncs_fts(self, mf):
        """batch_remove_tags 删除标签后 FTS 索引同步更新（无幽灵标签）"""
        e = mf.add("测试记忆内容", tags=["removethis", "keepthis"])
        assert self._fts_contains_tag(mf, e.id, "removethis")
        count = mf._storage.batch_remove_tags([e.id], ["removethis"])
        assert count == 1
        assert not self._fts_contains_tag(mf, e.id, "removethis")
        assert self._fts_contains_tag(mf, e.id, "keepthis")

    def test_append_tags_syncs_fts(self, mf):
        """append_tags 添加标签后 FTS 索引同步更新"""
        e = mf.add("测试记忆内容", tags=["initial"])
        assert not self._fts_contains_tag(mf, e.id, "appendnew")
        result = mf._storage.append_tags(e.id, ["appendnew"])
        assert result is True
        assert self._fts_contains_tag(mf, e.id, "appendnew")

    def test_merge_tags_syncs_fts(self, mf):
        """merge_tags 合并标签后 FTS 索引同步更新（源标签消失，目标标签出现）"""
        e = mf.add("测试记忆内容", tags=["oldtag1", "oldtag2", "other"])
        assert self._fts_contains_tag(mf, e.id, "oldtag1")
        assert self._fts_contains_tag(mf, e.id, "oldtag2")
        assert not self._fts_contains_tag(mf, e.id, "mergedtag")
        count = mf._storage.merge_tags(["oldtag1", "oldtag2"], "mergedtag")
        assert count == 1
        # 源标签应从 FTS 中消失
        assert not self._fts_contains_tag(mf, e.id, "oldtag1")
        assert not self._fts_contains_tag(mf, e.id, "oldtag2")
        # 目标标签应出现在 FTS 中
        assert self._fts_contains_tag(mf, e.id, "mergedtag")
        # 其他标签不受影响
        assert self._fts_contains_tag(mf, e.id, "other")

    def test_bulk_update_add_tags_syncs_fts(self, mf):
        """bulk_update_by_filter add_tags 后 FTS 索引同步更新"""
        e = mf.add("测试记忆内容", category="testcat", tags=["initial"])
        assert not self._fts_contains_tag(mf, e.id, "bulkadd")
        count = mf.bulk_update_by_filter(
            category="testcat",
            updates={"add_tags": ["bulkadd"]}
        )
        assert count == 1
        assert self._fts_contains_tag(mf, e.id, "bulkadd")

    def test_bulk_update_remove_tags_syncs_fts(self, mf):
        """bulk_update_by_filter remove_tags 后 FTS 索引同步更新（无幽灵标签）"""
        e = mf.add("测试记忆内容", category="testcat", tags=["removeme", "keepme"])
        assert self._fts_contains_tag(mf, e.id, "removeme")
        count = mf.bulk_update_by_filter(
            category="testcat",
            updates={"remove_tags": ["removeme"]}
        )
        assert count == 1
        assert not self._fts_contains_tag(mf, e.id, "removeme")
        assert self._fts_contains_tag(mf, e.id, "keepme")

    def test_bulk_update_category_syncs_fts(self, mf):
        """bulk_update_by_filter 改 category 后 FTS 索引同步更新"""
        e = mf.add("测试记忆内容", category="oldcat", tags=["test"])
        assert self._fts_contains_content(mf, e.id, "测试记忆")
        count = mf.bulk_update_by_filter(
            category="oldcat",
            updates={"new_category": "newcat"}
        )
        assert count == 1
        # 内容仍然可搜索
        assert self._fts_contains_content(mf, e.id, "测试记忆")
        # category 已更新
        assert mf.get(e.id).category == "newcat"

    def test_merge_memories_syncs_fts(self, mf):
        """merge_memories 合并后 FTS 索引同步更新"""
        target = mf.add("目标记忆内容行A", tags=["tagA"])
        source = mf.add("源记忆内容行B", tags=["tagB"])
        # 合并前验证
        assert self._fts_contains_tag(mf, target.id, "tagA")
        assert not self._fts_contains_tag(mf, target.id, "tagB")
        assert self._fts_contains_content(mf, source.id, "源记忆")
        # 合并
        result = mf._storage.merge_memories(source.id, target.id)
        assert result is not None
        # 目标记忆的 FTS 应包含合并后的内容和标签
        assert self._fts_contains_content(mf, target.id, "目标记忆")
        assert self._fts_contains_content(mf, target.id, "源记忆")
        assert self._fts_contains_tag(mf, target.id, "tagA")
        assert self._fts_contains_tag(mf, target.id, "tagB")
        # 源记忆已移入回收站，category=trash
        conn = mf._storage._get_conn()
        src_row = conn.execute(
            "SELECT category FROM memories WHERE id = ?", (source.id,)
        ).fetchone()
        assert src_row is not None
        assert src_row[0] == "trash"


class TestMemoryRecallLayerCase:
    """P2 Bug: memory_recall layer 输出大写枚举名，与其他接口不一致"""

    def test_memory_recall_layer_is_lowercase(self, mf):
        """memory_recall 返回的 layer 应为小写 value 格式"""
        mf.add("测试回忆内容", tags=["回忆", "测试"], layer="short_term")
        result = mf._storage.memory_recall(
            agent_id="cli",
            query="测试回忆",
            top_k=5
        )
        assert "recalled" in result
        if result["recalled"]:
            for item in result["recalled"]:
                assert "layer" in item
                # layer 应为小写（short_term / long_term 等），不是大写枚举名
                assert item["layer"] == item["layer"].lower()
                assert "_" in item["layer"] or item["layer"] in ("sensory", "permanent")
                # 验证不是 SHORT_TERM / LONG_TERM 格式
                assert item["layer"] not in ("SENSORY", "SHORT_TERM", "LONG_TERM", "PERMANENT")


# ===== v5.5.6 第二轮修复验证 =====

class TestRerankMetadata:
    """P2: search_enhanced 非重排模式的 chunks 应包含 category/layer/tags"""

    def test_no_rerank_chunks_have_metadata(self, mf):
        """非重排模式下，chunks 应包含 category/layer/tags"""
        mf.add("测试重排元数据内容 hello world", category="test_cat", tags=["tag1", "tag2"], layer="short_term")
        result = mf.search_enhanced(
            query="测试重排元数据",
            max_results=5,
            expand=False,
            rerank=False
        )
        assert result.get("rerank") is False
        chunks = result.get("chunks", [])
        assert len(chunks) > 0
        for c in chunks:
            assert "category" in c, "chunks 缺 category"
            assert "layer" in c, "chunks 缺 layer"
            assert "tags" in c, "chunks 缺 tags"
            # layer 应为小写 value
            assert c["layer"] == c["layer"].lower(), f"layer 应为小写: {c['layer']}"


class TestAgentInsightLayerCase:
    """P3: agent_insight 的 layer/importance/privacy 分布 key 应为小写"""

    def test_insight_distribution_keys_lowercase(self, mf):
        """layer_distribution / importance_distribution / privacy_distribution 的 key 应为小写"""
        mf.add("测试洞察记忆1", source_agent="test_agent", layer="short_term", importance="high", privacy="internal")
        mf.add("测试洞察记忆2", source_agent="test_agent", layer="long_term", importance="medium", privacy="public")
        result = mf._storage.agent_insight(agent_id="test_agent", days=30)
        assert "layer_distribution" in result
        assert "importance_distribution" in result
        assert "privacy_distribution" in result
        # layer key 应为小写
        for k in result["layer_distribution"].keys():
            assert k == k.lower(), f"layer key 应为小写: {k}"
            assert k not in ("SENSORY", "SHORT_TERM", "LONG_TERM", "PERMANENT"), f"layer key 不能是大写枚举名: {k}"
        # importance key 应为小写
        for k in result["importance_distribution"].keys():
            assert k == k.lower(), f"importance key 应为小写: {k}"
        # privacy key 应为小写
        for k in result["privacy_distribution"].keys():
            assert k == k.lower(), f"privacy key 应为小写: {k}"
