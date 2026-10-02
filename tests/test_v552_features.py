"""
MindForge v5.5.2 功能测试
覆盖：TTL过期机制、多关键词高亮、按分类/标签批量删除、FTS5溢出修复、版本校验
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


class TestVersion:
    """版本校验（动态：semver 格式 + pyproject.toml 一致性）

    v5.5.4 修复：不再硬编码版本号，改为校验格式和一致性。
    """

    def test_version_semver_format(self):
        """版本号符合 semver 格式 (X.Y.Z)"""
        import re
        assert re.match(r'^\d+\.\d+\.\d+$', __version__), \
            f"版本号格式不正确: {__version__}"

    def test_pyproject_version_matches(self):
        """pyproject.toml 动态版本指向 core/version.py 的 __version__"""
        from pathlib import Path
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        try:
            import tomllib
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            assert "version" in data["project"].get("dynamic", []), \
                "pyproject.toml 应声明 dynamic version"
            attr = data.get("tool", {}).get("setuptools", {}).get("dynamic", {}).get("version", {}).get("attr")
            assert attr == "mindforge.core.version.__version__", \
                f"dynamic version attr 应为 mindforge.core.version.__version__，实际: {attr}"
        except ModuleNotFoundError:
            # Python < 3.11: tomllib not available, fallback to regex
            import re
            text = pyproject.read_text(encoding="utf-8")
            m = re.search(r'attr\s*=\s*"mindforge\.core\.version\.__version__"', text)
            assert m is not None, "pyproject.toml 未找到 dynamic version attr"
        from mindforge.core.version import __version__ as _truth
        assert _truth == __version__


class TestTTLExpiration:
    """v5.5.2: Memory TTL 过期机制"""

    def test_add_memory_with_expires_at(self, mf):
        """添加带过期时间的记忆"""
        future = time.time() + 3600
        entry = mf.add("临时记忆内容", expires_at=future)
        assert entry.id is not None
        assert entry.expires_at == future

    def test_add_memory_no_expiration(self, mf):
        """默认永不过期"""
        entry = mf.add("永久记忆")
        assert entry.expires_at == 0.0

    def test_set_ttl(self, mf):
        """设置 TTL"""
        entry = mf.add("测试记忆")
        result = mf.set_ttl(entry.id, 3600)
        assert result is True
        # 验证过期时间已设置
        fetched = mf._storage.get_memory(entry.id)
        assert fetched is not None
        assert fetched.expires_at > 0

    def test_set_ttl_cancel(self, mf):
        """取消 TTL（设为 <= 0）"""
        entry = mf.add("测试记忆")
        mf.set_ttl(entry.id, 3600)
        result = mf.set_ttl(entry.id, 0)
        assert result is True
        fetched = mf._storage.get_memory(entry.id)
        assert fetched is not None
        assert fetched.expires_at == 0.0

    def test_set_ttl_nonexistent(self, mf):
        """设置不存在记忆的 TTL"""
        result = mf.set_ttl("nonexistent-id", 3600)
        assert result is False

    def test_auto_expire_on_get(self, mf):
        """获取已过期记忆时自动移入回收站"""
        past = time.time() - 10
        entry = mf.add("即将过期", expires_at=past)
        # 直接获取应该返回 None（已自动过期）
        result = mf.get(entry.id)
        assert result is None
        # 验证已移入回收站
        trashed = mf._storage.list_memories(category="trash", limit=10)
        trashed_ids = [t.id for t in trashed]
        assert entry.id in trashed_ids

    def test_list_expired(self, mf):
        """列出已过期记忆"""
        past = time.time() - 10
        future = time.time() + 3600
        mf.add("已过期1", expires_at=past)
        mf.add("已过期2", expires_at=past)
        mf.add("未过期", expires_at=future)
        expired = mf.list_expired()
        assert len(expired) == 2
        contents = [e.content for e in expired]
        assert "已过期1" in contents
        assert "已过期2" in contents

    def test_list_expired_empty(self, mf):
        """无过期记忆时返回空列表"""
        mf.add("正常记忆")
        expired = mf.list_expired()
        assert len(expired) == 0

    def test_purge_expired(self, mf):
        """清理已过期记忆"""
        past = time.time() - 10
        mf.add("过期1", expires_at=past)
        mf.add("过期2", expires_at=past)
        mf.add("正常")
        count = mf.purge_expired()
        assert count == 2
        # 验证已全部移入回收站
        remaining = mf.list(limit=100)
        normal_ids = [m.id for m in remaining if m.category != "trash"]
        assert len(normal_ids) == 1

    def test_purge_expired_none(self, mf):
        """无过期记忆时清理返回 0"""
        mf.add("正常记忆")
        count = mf.purge_expired()
        assert count == 0

    def test_expired_not_in_search(self, mf):
        """已过期记忆不应出现在正常搜索结果中"""
        past = time.time() - 10
        mf.add("过期的特殊关键词xyz", expires_at=past)
        mf.add("正常的特殊关键词xyz")
        # 触发过期清理
        mf.purge_expired()
        results = mf.search("特殊关键词xyz", max_results=10)
        contents = [c.content for c in results.chunks]
        assert "正常的特殊关键词xyz" in contents
        assert "过期的特殊关键词xyz" not in contents


class TestMultiKeywordHighlight:
    """v5.5.2: 多关键词搜索高亮"""

    def test_single_keyword_highlight(self, mf):
        """单关键词高亮"""
        result = mf.highlight("hello world", "hello")
        assert "<mark>hello</mark>" in result

    def test_multi_keyword_highlight(self, mf):
        """多关键词高亮（空格分隔）"""
        result = mf.highlight("hello world foo", "hello foo")
        assert "<mark>hello</mark>" in result
        assert "<mark>foo</mark>" in result

    def test_chinese_highlight(self, mf):
        """中文关键词高亮"""
        result = mf.highlight("今天天气很好", "天气")
        assert "<mark>天气</mark>" in result

    def test_multi_chinese_keyword(self, mf):
        """多中文关键词高亮"""
        result = mf.highlight("人工智能和机器学习", "人工智能 机器学习")
        assert "<mark>人工智能</mark>" in result
        assert "<mark>机器学习</mark>" in result

    def test_case_insensitive(self, mf):
        """大小写不敏感"""
        result = mf.highlight("Hello World", "hello")
        assert "<mark>Hello</mark>" in result

    def test_empty_query(self, mf):
        """空查询返回原文"""
        result = mf.highlight("hello", "")
        assert result == "hello"

    def test_empty_text(self, mf):
        """空文本返回空"""
        result = mf.highlight("", "hello")
        assert result == ""

    def test_custom_tags(self, mf):
        """自定义高亮标签"""
        result = mf.highlight("hello world", "hello", before_tag="<b>", after_tag="</b>")
        assert "<b>hello</b>" in result

    def test_no_nested_replacement(self, mf):
        """长关键词优先，避免短关键词破坏长关键词"""
        result = mf.highlight("人工智能", "人工 人工智能")
        # 完整的 "人工智能" 应该被整体高亮，而不是被 "人工" 部分替换
        assert "<mark>人工智能</mark>" in result


class TestBatchDelete:
    """v5.5.2: 按分类/标签批量删除"""

    def test_batch_delete_by_category_soft(self, mf):
        """按分类软删除（移入回收站）"""
        mf.add("临时1", category="temp")
        mf.add("临时2", category="temp")
        mf.add("永久", category="work")
        count = mf.batch_delete_by_category("temp")
        assert count == 2
        remaining = mf.list(limit=100)
        work_items = [m for m in remaining if m.category == "work"]
        assert len(work_items) == 1

    def test_batch_delete_by_category_permanent(self, mf):
        """按分类永久删除"""
        mf.add("临时1", category="temp")
        mf.add("临时2", category="temp")
        count = mf.batch_delete_by_category("temp", permanent=True)
        assert count == 2
        # 永久删除后回收站也找不到
        all_items = mf.list(limit=100)
        assert len(all_items) == 0

    def test_batch_delete_by_category_empty(self, mf):
        """删除不存在的分类返回 0"""
        mf.add("正常", category="work")
        count = mf.batch_delete_by_category("nonexistent")
        assert count == 0

    def test_batch_delete_by_tag_soft(self, mf):
        """按标签软删除"""
        mf.add("带标签1", tags=["expire-me"])
        mf.add("带标签2", tags=["expire-me", "other"])
        mf.add("无标签", tags=["keep"])
        count = mf.batch_delete_by_tag("expire-me")
        assert count == 2
        remaining = mf.list(limit=100)
        kept = [m for m in remaining if m.category != "trash"]
        assert len(kept) == 1
        assert kept[0].content == "无标签"

    def test_batch_delete_by_tag_permanent(self, mf):
        """按标签永久删除"""
        mf.add("带标签", tags=["delete"])
        count = mf.batch_delete_by_tag("delete", permanent=True)
        assert count == 1
        all_items = mf.list(limit=100)
        assert len(all_items) == 0

    def test_batch_delete_by_tag_no_match(self, mf):
        """删除不存在的标签返回 0"""
        mf.add("正常", tags=["keep"])
        count = mf.batch_delete_by_tag("nonexistent")
        assert count == 0

    def test_batch_delete_by_tag_partial_match_no_false_positive(self, mf):
        """标签 LIKE 匹配不会误报（精确验证）"""
        mf.add("标签abc", tags=["abc"])
        mf.add("标签abcd", tags=["abcd"])
        # 删除 "abc" 不应匹配 "abcd"
        count = mf.batch_delete_by_tag("abc")
        assert count == 1
        remaining = mf.list(limit=100)
        kept = [m for m in remaining if m.category != "trash"]
        assert len(kept) == 1
        assert kept[0].content == "标签abcd"


class TestFTS5ScoreFix:
    """v5.5.2: FTS5 bm25 分数溢出修复"""

    def test_fts_search_no_crash(self, mf):
        """FTS 搜索不应因异常分数崩溃"""
        mf.add("测试内容用于搜索")
        # 正常搜索不应抛出异常
        result = mf.search("测试", max_results=5)
        assert isinstance(result.chunks, list)

    def test_indexer_fts_search_handles_none_score(self, mf):
        """索引引擎 FTS 搜索处理 None 分数"""
        from mindforge.core.indexer import IndexEngine
        idx = IndexEngine()
        # 直接调用 fts_search，传入空连接应优雅处理
        import sqlite3
        conn = sqlite3.connect(":memory:")
        try:
            results = idx.fts_search(conn, "test", top_k=5)
            assert results == []  # 无 FTS 表时返回空
        finally:
            conn.close()


class TestQueryEngineCache:
    """v5.5.2: 查询引擎 entry cache 性能优化"""

    def test_search_with_category_filter(self, mf):
        """带分类过滤的搜索正常工作"""
        mf.add("工作记忆内容", category="work")
        mf.add("个人记忆内容", category="personal")
        result = mf.search("记忆内容", max_results=10, categories=["work"])
        assert len(result.chunks) >= 1
        for chunk in result.chunks:
            assert chunk.category == "work"

    def test_search_with_layer_filter(self, mf):
        """带层级过滤的搜索正常工作"""
        from mindforge import MemoryLayer
        mf.add("长期记忆", layer=MemoryLayer.LONG_TERM)
        mf.add("短期记忆", layer=MemoryLayer.SHORT_TERM)
        result = mf.search("记忆", max_results=10, layers=[MemoryLayer.LONG_TERM])
        for chunk in result.chunks:
            assert chunk.layer == MemoryLayer.LONG_TERM
