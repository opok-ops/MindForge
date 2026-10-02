# -*- coding: utf-8 -*-
"""MindForge v5.4.8 新功能测试

覆盖：Agent 记忆强化、跨 Agent 共享、知识领域分析、AI 短剧场景生成、情感时间线
"""
import os
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mindforge.core.mindforge import MindForge
from mindforge.core.types import Importance


class TestAgentMemoryReinforce:
    """v5.4.8: Agent 记忆强化"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_reinforce_no_agent(self):
        result = self.mf.agent_memory_reinforce("")
        assert "error" in result

    def test_reinforce_no_high_access(self):
        self.mf.add("测试记忆", category="test", source_agent="agent1")
        result = self.mf.agent_memory_reinforce("agent1", min_access_count=10)
        assert result["evaluated"] == 0
        assert result["reinforced"] == 0

    def test_reinforce_dry_run(self):
        # 添加记忆并模拟访问
        entry = self.mf.add("重要记忆", category="test", source_agent="agent1",
                           importance=Importance.LOW)
        # 手动增加访问次数
        conn = self.mf.storage._get_conn()
        conn.execute("UPDATE memories SET access_count = 5 WHERE id = ?", (entry.id,))
        conn.commit()

        result = self.mf.agent_memory_reinforce("agent1", min_access_count=3, dry_run=True)
        assert result["evaluated"] == 1
        assert result["reinforced"] == 1
        assert result["dry_run"] is True
        # dry_run 不应该实际修改
        mem = self.mf.get(entry.id)
        assert mem.importance.value == "LOW"

    def test_reinforce_actual(self):
        entry = self.mf.add("重要记忆", category="test", source_agent="agent1",
                           importance=Importance.LOW)
        conn = self.mf.storage._get_conn()
        conn.execute("UPDATE memories SET access_count = 5 WHERE id = ?", (entry.id,))
        conn.commit()

        result = self.mf.agent_memory_reinforce("agent1", min_access_count=3, dry_run=False)
        assert result["reinforced"] == 1
        assert result["details"][0]["from"] == "LOW"
        assert result["details"][0]["to"] == "MEDIUM"

        # 验证实际修改
        mem = self.mf.get(entry.id)
        assert mem.importance.value == "MEDIUM"


class TestAgentSharedMemories:
    """v5.4.8: 跨 Agent 记忆共享"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_share_empty_agent(self):
        result = self.mf.agent_shared_memories("agent1", "agent2")
        assert result["shared_count"] == 0

    def test_share_self_error(self):
        result = self.mf.agent_shared_memories("agent1", "agent1")
        assert "error" in result

    def test_share_basic(self):
        self.mf.add("共享知识", category="tech", source_agent="agent1")
        self.mf.add("私有记忆", category="personal", source_agent="agent1")

        result = self.mf.agent_shared_memories("agent1", "agent2")
        assert result["shared_count"] == 2

        # agent2 应该能搜到
        conn = self.mf.storage._get_conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE source_agent = 'agent2'"
        ).fetchone()[0]
        assert count == 2

    def test_share_with_category_filter(self):
        self.mf.add("技术知识", category="tech", source_agent="agent1")
        self.mf.add("生活经验", category="life", source_agent="agent1")

        result = self.mf.agent_shared_memories("agent1", "agent2", categories=["tech"])
        assert result["shared_count"] == 1
        assert result["details"][0]["category"] == "tech"

    def test_share_dedup(self):
        self.mf.add("相同内容", category="tech", source_agent="agent1")
        self.mf.add("相同内容", category="tech", source_agent="agent2")

        result = self.mf.agent_shared_memories("agent1", "agent2")
        assert result["shared_count"] == 0  # 已存在，不重复共享

    def test_share_dry_run(self):
        self.mf.add("测试记忆", category="tech", source_agent="agent1")

        result = self.mf.agent_shared_memories("agent1", "agent2", dry_run=True)
        assert result["shared_count"] == 1
        assert result["dry_run"] is True

        # dry_run 不应实际创建
        conn = self.mf.storage._get_conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE source_agent = 'agent2'"
        ).fetchone()[0]
        assert count == 0


class TestAgentKnowledgeDomains:
    """v5.4.8: Agent 知识领域分析"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_domains_empty(self):
        result = self.mf.agent_knowledge_domains("agent1")
        assert result["total_memories"] == 0
        assert result["domains"] == []

    def test_domains_basic(self):
        self.mf.add("Python 技巧", category="tech", source_agent="agent1", tags=["python"])
        self.mf.add("更多 Python", category="tech", source_agent="agent1", tags=["python", "code"])
        self.mf.add("生活经验", category="life", source_agent="agent1", tags=["daily"])

        result = self.mf.agent_knowledge_domains("agent1")
        assert result["total_memories"] == 3
        assert len(result["domains"]) == 2
        # tech 应该有 2 条
        tech_domain = next(d for d in result["domains"] if d["name"] == "tech")
        assert tech_domain["count"] == 2
        assert "python" in tech_domain["top_tags"]


class TestDramaGenerateScene:
    """v5.4.8: AI 短剧场景生成"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_generate_scene_no_drama(self):
        result = self.mf.drama_generate_scene("nonexistent", "场景1")
        assert "error" in result

    def test_generate_scene_basic(self):
        # 创建短剧
        conn = self.mf.storage._get_conn()
        conn.execute(
            "INSERT INTO drama_series (id, title, genre, total_episodes, current_episode, status, platform, rating, description, tags, cover_url, metadata, created_at, updated_at, last_watched_at) "
            "VALUES (?, ?, ?, 0, 0, ?, '', 0.0, '', '[]', '', '{}', ?, ?, 0.0)",
            ("drama1", "测试短剧", "romance", "ongoing", 1000, 1000)
        )
        conn.commit()

        result = self.mf.drama_generate_scene(
            "drama1", "初次相遇",
            characters=[],
            mood="romantic",
            setting="咖啡馆"
        )

        assert "scene_id" in result
        assert result["title"] == "初次相遇"
        assert result["mood"] == "romantic"
        assert len(result["suggestions"]) > 0
        assert result["scene_order"] == 1

    def test_generate_scene_with_characters(self):
        conn = self.mf.storage._get_conn()
        conn.execute(
            "INSERT INTO drama_series (id, title, genre, total_episodes, current_episode, status, platform, rating, description, tags, cover_url, metadata, created_at, updated_at, last_watched_at) "
            "VALUES (?, ?, ?, 0, 0, ?, '', 0.0, '', '[]', '', '{}', ?, ?, 0.0)",
            ("drama1", "测试短剧", "suspense", "ongoing", 1000, 1000)
        )
        conn.execute(
            "INSERT INTO drama_characters (id, drama_id, name, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("char1", "drama1", "侦探", "protagonist", 1000)
        )
        conn.commit()

        result = self.mf.drama_generate_scene(
            "drama1", "案发现场",
            characters=["侦探"],
            mood="tense"
        )

        assert len(result["characters"]) == 1
        assert result["characters"][0]["name"] == "侦探"


class TestDramaEmotionTimeline:
    """v5.4.8: 短剧情感时间线"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.mf = MindForge(db_path=self.db_path, encrypted=False)

    def teardown_method(self):
        self.mf.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_emotion_timeline_no_drama(self):
        result = self.mf.drama_emotion_timeline("nonexistent")
        assert "error" in result

    def test_emotion_timeline_empty(self):
        conn = self.mf.storage._get_conn()
        conn.execute(
            "INSERT INTO drama_series (id, title, genre, total_episodes, current_episode, status, platform, rating, description, tags, cover_url, metadata, created_at, updated_at, last_watched_at) "
            "VALUES (?, ?, ?, 0, 0, ?, '', 0.0, '', '[]', '', '{}', ?, ?, 0.0)",
            ("drama1", "测试短剧", "drama", "ongoing", 1000, 1000)
        )
        conn.commit()

        result = self.mf.drama_emotion_timeline("drama1")
        assert len(result["emotion_points"]) == 0
        assert result["trend"] == "no_data"

    def test_emotion_timeline_with_scenes(self):
        conn = self.mf.storage._get_conn()
        conn.execute(
            "INSERT INTO drama_series (id, title, genre, total_episodes, current_episode, status, platform, rating, description, tags, cover_url, metadata, created_at, updated_at, last_watched_at) "
            "VALUES (?, ?, ?, 0, 0, ?, '', 0.0, '', '[]', '', '{}', ?, ?, 0.0)",
            ("drama1", "测试短剧", "drama", "ongoing", 1000, 1000)
        )
        # 添加不同情感的场景
        scenes = [
            ("s1", " happy ending", "开心 成功 幸福", 1),
            ("s2", "悲伤离别", "悲伤 痛苦 绝望", 2),
            ("s3", "紧张对峙", "危险 冲突 危机", 3),
        ]
        for sid, title, desc, order in scenes:
            conn.execute(
                "INSERT INTO drama_scenes (id, drama_id, title, content, scene_number, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, "drama1", title, desc, order, 1000)
            )
        conn.commit()

        result = self.mf.drama_emotion_timeline("drama1")
        assert result["total_scenes"] == 3
        assert len(result["emotion_points"]) == 3
        assert result["trend"] != "no_data"
        assert len(result["summary"]) > 0
