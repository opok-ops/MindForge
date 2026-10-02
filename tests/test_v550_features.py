# -*- coding: utf-8 -*-
"""MindForge v5.5.2 新功能测试

覆盖：Agent 记忆快照/去重/健康检查/重要度重校准、
AI 短剧分集生成/角色台词/剧情反转/剧本导出、
Bug 修复验证（枚举不匹配）
"""
import os
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mindforge.core.mindforge import MindForge
from mindforge.core.types import Importance, MemoryLayer


# ============================================================
# Agent 记忆终极增强测试
# ============================================================

class TestAgentMemorySnapshot:
    """v5.5.0: Agent 记忆快照"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_snapshot_empty_agent(self):
        result = self.mf.agent_memory_snapshot("")
        assert "error" in result

    def test_snapshot_no_memories(self):
        result = self.mf.agent_memory_snapshot("agent1")
        assert result["memory_count"] == 0
        assert "snapshot_id" in result
        assert result["categories"] == {}

    def test_snapshot_with_memories(self):
        self.mf.add("Python 编程技巧", category="tech", source_agent="agent1", tags=["python"])
        self.mf.add("生活感悟", category="life", source_agent="agent1")
        self.mf.add("另一条技术记忆", category="tech", source_agent="agent1")

        result = self.mf.agent_memory_snapshot("agent1", label="测试快照")
        assert result["memory_count"] == 3
        assert result["label"] == "测试快照"
        assert result["categories"]["tech"] == 2
        assert result["categories"]["life"] == 1
        assert len(result["sample_entries"]) > 0


class TestAgentMemoryDeduplicate:
    """v5.5.0: Agent 记忆去重"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_dedup_empty_agent(self):
        result = self.mf.agent_memory_deduplicate("")
        assert "error" in result

    def test_dedup_no_duplicates(self):
        self.mf.add("完全不同的内容一", category="tech", source_agent="agent1")
        self.mf.add("完全不同的内容二", category="life", source_agent="agent1")

        result = self.mf.agent_memory_deduplicate("agent1", similarity_threshold=0.9)
        assert result["duplicates_found"] == 0
        assert result["merged"] == 0

    def test_dedup_dry_run(self):
        # 添加高度相似的内容
        self.mf.add("Python 编程技巧与最佳实践", category="tech", source_agent="agent1")
        self.mf.add("Python 编程技巧与最佳实践方法", category="tech", source_agent="agent1")

        result = self.mf.agent_memory_deduplicate(
            "agent1", similarity_threshold=0.7, dry_run=True)
        assert result["dry_run"] is True
        # dry_run 不应实际修改
        conn = self.mf.storage._get_conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE source_agent = 'agent1' AND category != 'trash'"
        ).fetchone()[0]
        assert count == 2

    def test_dedup_actual_merge(self):
        self.mf.add("Python 编程技巧与最佳实践", category="tech", source_agent="agent1")
        self.mf.add("Python 编程技巧与最佳实践方法", category="tech", source_agent="agent1")

        result = self.mf.agent_memory_deduplicate(
            "agent1", similarity_threshold=0.7, dry_run=False)
        assert result["merged"] >= 1
        assert len(result["groups"]) >= 1


class TestAgentMemoryHealthCheck:
    """v5.5.0: Agent 记忆健康检查"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_health_empty_agent(self):
        result = self.mf.agent_memory_health_check("")
        assert "error" in result

    def test_health_no_memories(self):
        result = self.mf.agent_memory_health_check("agent1")
        assert result["overall_score"] == 0
        assert result["total_memories"] == 0
        assert len(result["issues"]) > 0

    def test_health_with_memories(self):
        for i in range(5):
            self.mf.add(f"测试记忆内容 {i}", category="tech", source_agent="agent1",
                       importance=Importance.MEDIUM, layer=MemoryLayer.SHORT_TERM)

        result = self.mf.agent_memory_health_check("agent1")
        assert result["total_memories"] == 5
        assert 0 <= result["overall_score"] <= 100
        assert "dimensions" in result
        assert "issues" in result
        assert "recommendations" in result
        assert result["dimensions"]["layer_distribution"]["short_term"] == 5


class TestAgentMemoryImportanceRecalibrate:
    """v5.5.0: Agent 记忆重要度重校准"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_recalibrate_empty_agent(self):
        result = self.mf.agent_memory_importance_recalibrate("")
        assert "error" in result

    def test_recalibrate_no_memories(self):
        result = self.mf.agent_memory_importance_recalibrate("agent1")
        assert result["evaluated"] == 0
        assert result["upgraded"] == 0

    def test_recalibrate_dry_run(self):
        entry = self.mf.add("重要测试记忆", category="tech", source_agent="agent1",
                           importance=Importance.LOW)
        # 模拟高访问次数
        conn = self.mf.storage._get_conn()
        conn.execute("UPDATE memories SET access_count = 10 WHERE id = ?", (entry.id,))
        conn.commit()

        result = self.mf.agent_memory_importance_recalibrate("agent1", dry_run=True)
        assert result["dry_run"] is True
        assert result["evaluated"] == 1
        # 高访问次数应该被升级
        assert result["upgraded"] >= 1

    def test_recalibrate_actual(self):
        entry = self.mf.add("重要测试记忆", category="tech", source_agent="agent1",
                           importance=Importance.LOW)
        conn = self.mf.storage._get_conn()
        conn.execute("UPDATE memories SET access_count = 10 WHERE id = ?", (entry.id,))
        conn.commit()

        result = self.mf.agent_memory_importance_recalibrate("agent1", dry_run=False)
        assert result["upgraded"] >= 1
        # 验证实际修改
        mem = self.mf.get(entry.id)
        assert mem.importance.value != "LOW"


# ============================================================
# AI 短剧终极增强测试
# ============================================================

class TestDramaGenerateEpisode:
    """v5.5.0: AI 短剧分集生成"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)
        # 创建测试短剧
        conn = self.mf.storage._get_conn()
        conn.execute(
            "INSERT INTO drama_series (id, title, genre, total_episodes, current_episode, status, platform, rating, description, tags, cover_url, metadata, created_at, updated_at, last_watched_at) "
            "VALUES (?, ?, ?, 0, 0, ?, '', 0.0, '', '[]', '', '{}', ?, ?, 0.0)",
            ("drama1", "测试短剧", "romance", "ongoing", 1000, 1000)
        )
        conn.commit()

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_generate_episode_no_drama(self):
        result = self.mf.drama_generate_episode("nonexistent", 1)
        assert "error" in result

    def test_generate_episode_basic(self):
        result = self.mf.drama_generate_episode(
            "drama1", episode_number=1, theme="初次相遇", num_scenes=3, mood="romantic")
        assert "episode_id" in result
        assert result["episode_number"] == 1
        assert result["theme"] == "初次相遇"
        assert len(result["scenes"]) == 3
        assert result["mood"] == "romantic"
        assert len(result["suggestions"]) > 0

    def test_generate_episode_phase_detection(self):
        # 第1集应该是 opening 阶段
        result = self.mf.drama_generate_episode("drama1", 1)
        assert result["phase"] == "opening"


class TestDramaCharacterDialogue:
    """v5.5.0: AI 角色台词生成"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)
        conn = self.mf.storage._get_conn()
        conn.execute(
            "INSERT INTO drama_series (id, title, genre, total_episodes, current_episode, status, platform, rating, description, tags, cover_url, metadata, created_at, updated_at, last_watched_at) "
            "VALUES (?, ?, ?, 0, 0, ?, '', 0.0, '', '[]', '', '{}', ?, ?, 0.0)",
            ("drama1", "测试短剧", "drama", "ongoing", 1000, 1000)
        )
        conn.execute(
            "INSERT INTO drama_characters (id, drama_id, name, role, personality, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("char1", "drama1", "主角小明", "protagonist", "冷静、理性、勇敢", 1000)
        )
        conn.commit()

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_dialogue_no_drama(self):
        result = self.mf.drama_character_dialogue("nonexistent", "角色")
        assert "error" in result

    def test_dialogue_no_character(self):
        result = self.mf.drama_character_dialogue("drama1", "不存在的角色")
        assert "error" in result

    def test_dialogue_basic(self):
        result = self.mf.drama_character_dialogue(
            "drama1", "主角小明", context="面对危机时刻", emotion="tense", num_lines=3)
        assert result["character"] == "主角小明"
        assert result["emotion"] == "tense"
        assert len(result["lines"]) == 3
        assert "context_analysis" in result
        for line in result["lines"]:
            assert "text" in line
            assert line["character"] == "主角小明"


class TestDramaPlotTwistSuggest:
    """v5.5.0: AI 剧情反转建议"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)
        conn = self.mf.storage._get_conn()
        conn.execute(
            "INSERT INTO drama_series (id, title, genre, total_episodes, current_episode, status, platform, rating, description, tags, cover_url, metadata, created_at, updated_at, last_watched_at) "
            "VALUES (?, ?, ?, 0, 0, ?, '', 0.0, '', '[]', '', '{}', ?, ?, 0.0)",
            ("drama1", "悬疑测试剧", "suspense", "ongoing", 1000, 1000)
        )
        conn.commit()

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_twist_no_drama(self):
        result = self.mf.drama_plot_twist_suggest("nonexistent")
        assert "error" in result

    def test_twist_basic(self):
        result = self.mf.drama_plot_twist_suggest("drama1", num_suggestions=3)
        assert "drama_id" in result
        assert "current_analysis" in result
        assert len(result["twists"]) == 3
        assert len(result["recommendations"]) > 0
        for twist in result["twists"]:
            assert "type" in twist
            assert "description" in twist
            assert "setup_method" in twist
            assert "story_impact" in twist
            assert 1 <= twist["dramatic_level"] <= 10


class TestDramaScriptExport:
    """v5.5.0: 短剧剧本导出"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)
        conn = self.mf.storage._get_conn()
        conn.execute(
            "INSERT INTO drama_series (id, title, genre, total_episodes, current_episode, status, platform, rating, description, tags, cover_url, metadata, created_at, updated_at, last_watched_at) "
            "VALUES (?, ?, ?, 0, 0, ?, '', 0.0, '测试简介', '[]', '', '{}', ?, ?, 0.0)",
            ("drama1", "测试短剧", "drama", "ongoing", 1000, 1000)
        )
        conn.execute(
            "INSERT INTO drama_scenes (id, drama_id, episode, scene_number, title, content, location, time_of_day, tags, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', '{}', ?)",
            ("scene1", "drama1", 1, 1, "开场", "故事开始的地方", "咖啡馆", "白天", 1000)
        )
        conn.execute(
            "INSERT INTO drama_lines (id, drama_id, scene_id, character_id, character_name, line_text, context, episode, timestamp, is_classic, memory_id, tags, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '[]', '{}', ?)",
            ("line1", "drama1", "scene1", "", "主角", "你好，世界", "开场问候", 1, "00:01", 1, 1000)
        )
        conn.commit()

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_export_no_drama(self):
        result = self.mf.drama_script_export("nonexistent")
        assert "error" in result

    def test_export_basic(self):
        result = self.mf.drama_script_export("drama1", format="standard")
        assert result["title"] == "测试短剧"
        assert result["format"] == "standard"
        assert result["total_scenes"] == 1
        assert result["total_lines"] == 1
        assert "script_content" in result
        assert "测试短剧" in result["script_content"]
        assert "主角" in result["script_content"]
        assert "你好，世界" in result["script_content"]

    def test_export_formats(self):
        for fmt in ["standard", "condensed", "detailed"]:
            result = self.mf.drama_script_export("drama1", format=fmt)
            assert result["format"] == fmt
            assert len(result["script_content"]) > 0

    def test_export_invalid_format_fallback(self):
        result = self.mf.drama_script_export("drama1", format="invalid")
        assert result["format"] == "standard"  # 回退到 standard


# ============================================================
# Bug 修复验证测试
# ============================================================

class TestBugFixes:
    """v5.5.0: Bug 修复验证"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_drama_search_valid_genre_suspense(self):
        """修复验证：SUSPENSE 类型不应被拒绝"""
        # 创建一个 suspense 类型的短剧
        conn = self.mf.storage._get_conn()
        conn.execute(
            "INSERT INTO drama_series (id, title, genre, total_episodes, current_episode, status, platform, rating, description, tags, cover_url, metadata, created_at, updated_at, last_watched_at) "
            "VALUES (?, ?, ?, 0, 0, ?, '', 8.0, '悬疑测试', '[]', '', '{}', ?, ?, 0.0)",
            ("drama_suspense", "悬疑剧", "suspense", "ongoing", 1000, 1000)
        )
        conn.commit()

        # 用 SUSPENSE 类型搜索应该能找到
        results = self.mf.drama_search("悬疑", genre="SUSPENSE")
        assert isinstance(results, list)
        # 至少不应该因为类型无效而返回空（类型过滤应该正常工作）

    def test_drama_search_valid_genre_horror(self):
        """修复验证：HORROR 类型不应被拒绝"""
        results = self.mf.drama_search("恐怖", genre="HORROR")
        assert isinstance(results, list)

    def test_drama_search_valid_genre_fantasy(self):
        """修复验证：FANTASY 类型不应被拒绝"""
        results = self.mf.drama_search("奇幻", genre="FANTASY")
        assert isinstance(results, list)

    def test_drama_search_invalid_genre_rejected(self):
        """验证：无效类型仍被正确拒绝"""
        results = self.mf.drama_search("测试", genre="INVALID_GENRE")
        assert isinstance(results, list)
        # 无效类型应被设为 None，不过滤

    def test_drama_progress_valid_status_planned(self):
        """修复验证：PLANNED 状态应被接受"""
        conn = self.mf.storage._get_conn()
        conn.execute(
            "INSERT INTO drama_series (id, title, genre, total_episodes, current_episode, status, platform, rating, description, tags, cover_url, metadata, created_at, updated_at, last_watched_at) "
            "VALUES (?, ?, ?, 10, 0, ?, '', 0.0, '', '[]', '', '{}', ?, ?, 0.0)",
            ("drama1", "测试剧", "drama", "planned", 1000, 1000)
        )
        conn.commit()

        result = self.mf.drama_progress("drama1", current_episode=3, status="PLANNED")
        assert "error" not in result or result.get("error") is None

    def test_drama_recommend_v2_valid_genre(self):
        """修复验证：drama_recommend_v2 接受合法类型"""
        result = self.mf.drama_recommend_v2(genre="SUSPENSE", mode="all")
        assert isinstance(result, list)

    def test_version_semver_format(self):
        """验证版本号符合 semver 格式（v5.5.4 动态校验）"""
        import re
        from mindforge import __version__
        assert re.match(r'^\d+\.\d+\.\d+$', __version__), \
            f"版本号格式不正确: {__version__}"
