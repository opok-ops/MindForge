"""
MindForge v5.0 人格化引擎
学习用户偏好、语言风格、思维模式
"""

import json
import time
import re
import sqlite3  # v5.4.7 修复 C-4：异常处理中引用 sqlite3.OperationalError 需要此导入
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

from core.storage import StorageEngine


@dataclass
class UserProfile:
    """用户画像"""
    user_id: str = "default"
    preferences: Dict[str, float] = field(default_factory=dict)
    language_style: Dict[str, Any] = field(default_factory=dict)
    topics_of_interest: Dict[str, float] = field(default_factory=dict)
    technical_level: Dict[str, float] = field(default_factory=dict)
    communication_patterns: Dict[str, Any] = field(default_factory=dict)
    learning_style: Dict[str, float] = field(default_factory=dict)
    interaction_history: List[Dict] = field(default_factory=list)
    last_updated: float = 0.0
    total_interactions: int = 0

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "preferences": self.preferences,
            "language_style": self.language_style,
            "topics_of_interest": self.topics_of_interest,
            "technical_level": self.technical_level,
            "communication_patterns": self.communication_patterns,
            "learning_style": self.learning_style,
            "total_interactions": self.total_interactions,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile":
        return cls(
            user_id=data.get("user_id", "default"),
            preferences=data.get("preferences", {}),
            language_style=data.get("language_style", {}),
            topics_of_interest=data.get("topics_of_interest", {}),
            technical_level=data.get("technical_level", {}),
            communication_patterns=data.get("communication_patterns", {}),
            learning_style=data.get("learning_style", {}),
            total_interactions=data.get("total_interactions", 0),
            last_updated=data.get("last_updated", 0),
        )


class PersonalityEngine:
    """人格化引擎"""

    def __init__(self, storage: StorageEngine):
        self.storage = storage
        self.profiles: Dict[str, UserProfile] = {}
        self._load_profiles()

    def _load_profiles(self):
        """加载用户画像"""
        try:
            all_memories = self.storage.list_memories(category="user_profile", limit=100)
            for entry in all_memories:
                try:
                    # P3 #29 修复：使用安全 JSON 解析，防深度嵌套栈溢出
                    data = self.storage._safe_json_loads(entry.content)
                    if data is None:
                        continue
                    profile = UserProfile.from_dict(data)
                    self.profiles[profile.user_id] = profile
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
        except sqlite3.OperationalError:
            pass

    def get_profile(self, user_id: str = "default") -> UserProfile:
        """获取用户画像"""
        if user_id not in self.profiles:
            self.profiles[user_id] = UserProfile(user_id=user_id)
        return self.profiles[user_id]

    def learn_from_interaction(self, user_id: str,
                               user_message: str,
                               agent_response: str,
                               metadata: Optional[Dict] = None):
        """从交互中学习"""
        profile = self.get_profile(user_id)
        profile.total_interactions += 1
        profile.last_updated = time.time()

        self._analyze_language_style(profile, user_message)
        self._analyze_topics(profile, user_message)
        self._analyze_technical_level(profile, user_message)
        self._analyze_communication_patterns(profile, user_message)

        profile.interaction_history.append({
            "timestamp": time.time(),
            "message_length": len(user_message),
            "response_length": len(agent_response),
            "metadata": metadata or {},
        })

        if len(profile.interaction_history) > 1000:
            profile.interaction_history = profile.interaction_history[-1000:]

        self._save_profile(profile)

    def _analyze_language_style(self, profile: UserProfile, text: str):
        """分析语言风格"""
        style = profile.language_style

        style["avg_message_length"] = (
            style.get("avg_message_length", 0) * 0.9 + len(text) * 0.1
        )

        formality_score = self._calculate_formality(text)
        style["formality"] = (
            style.get("formality", 0.5) * 0.95 + formality_score * 0.05
        )

        emoji_count = sum(1 for ch in text if ord(ch) > 0x1F000)
        style["emoji_usage"] = (
            style.get("emoji_usage", 0) * 0.9 + min(emoji_count / 5, 1.0) * 0.1
        )

        exclamation_count = text.count('!')
        style["excitement"] = (
            style.get("excitement", 0.5) * 0.95 + min(exclamation_count / 3, 1.0) * 0.05
        )

        question_count = text.count('?')
        style["curiosity"] = (
            style.get("curiosity", 0.5) * 0.95 + min(question_count / 2, 1.0) * 0.05
        )

    def _calculate_formality(self, text: str) -> float:
        """计算正式程度"""
        formal_words = ['请', '您', '谢谢', '感谢', '请问', '您好', '尊敬', '此致', '敬礼']
        casual_words = ['哈哈', '哈哈哈哈', 'emmm', 'emm', '啊这', 'yyds', '绝了', '哇']

        formal_score = sum(1 for w in formal_words if w in text)
        casual_score = sum(1 for w in casual_words if w in text)

        total = formal_score + casual_score + 1
        return 0.3 + 0.4 * (formal_score / total)

    def _analyze_topics(self, profile: UserProfile, text: str):
        """分析兴趣主题"""
        topics = profile.topics_of_interest
        text_lower = text.lower()

        topic_keywords = {
            "编程开发": ['代码', '编程', '开发', '函数', '类', '接口', 'api', 'bug', 'debug'],
            "人工智能": ['ai', '人工智能', '大模型', 'llm', 'gpt', 'agent', '机器学习', '深度学习'],
            "数据库": ['数据库', 'sql', 'mysql', 'postgresql', 'redis', 'mongodb', '索引', '查询'],
            "前端开发": ['前端', 'html', 'css', 'javascript', 'react', 'vue', '组件', 'ui'],
            "生活日常": ['吃饭', '睡觉', '周末', '假期', '旅游', '电影', '音乐', '游戏'],
            "学习成长": ['学习', '研究', '阅读', '书籍', '课程', '教程', '成长', '进步'],
        }

        for topic, keywords in topic_keywords.items():
            count = sum(1 for kw in keywords if kw in text_lower)
            if count > 0:
                current = topics.get(topic, 0)
                topics[topic] = current + count * 0.1
                if topics[topic] > 1.0:
                    topics[topic] = 1.0

    def _analyze_technical_level(self, profile: UserProfile, text: str):
        """分析技术水平"""
        tech_level = profile.technical_level
        text_lower = text.lower()

        beginner_words = ['怎么用', '如何', '入门', '新手', '基础', '教程', '简单']
        advanced_words = ['优化', '架构', '性能', '并发', '分布式', '微服务', '底层', '原理']

        beginner_count = sum(1 for w in beginner_words if w in text_lower)
        advanced_count = sum(1 for w in advanced_words if w in text_lower)

        current = tech_level.get("overall", 0.5)
        if advanced_count > beginner_count:
            new_level = min(1.0, current + 0.02)
        elif beginner_count > advanced_count:
            new_level = max(0.0, current - 0.01)
        else:
            new_level = current

        tech_level["overall"] = new_level

    def _analyze_communication_patterns(self, profile: UserProfile, text: str):
        """分析沟通模式"""
        patterns = profile.communication_patterns

        sentence_count = len(re.split(r'[。！？.!?]', text))
        patterns["avg_sentences_per_message"] = (
            patterns.get("avg_sentences_per_message", 3) * 0.9 + sentence_count * 0.1
        )

        detail_length = len(text) / max(sentence_count, 1)
        patterns["detail_oriented"] = (
            patterns.get("detail_oriented", 0.5) * 0.95
            + min(detail_length / 100, 1.0) * 0.05
        )

    def get_recommended_style(self, user_id: str = "default") -> Dict:
        """获取推荐的交流风格"""
        profile = self.get_profile(user_id)
        style = profile.language_style

        formality = style.get("formality", 0.5)
        emoji_usage = style.get("emoji_usage", 0)
        detail = style.get("detail_oriented", 0.5)

        return {
            "formality_level": "正式" if formality > 0.7 else "中性" if formality > 0.4 else "轻松",
            "use_emoji": emoji_usage > 0.3,
            "detail_level": "详细" if detail > 0.7 else "适中" if detail > 0.4 else "简洁",
            "response_length": "详细" if detail > 0.6 else "标准",
            "technical_depth": profile.technical_level.get("overall", 0.5),
        }

    def get_top_interests(self, user_id: str = "default", top_n: int = 5) -> List[Tuple[str, float]]:
        """获取最感兴趣的主题"""
        profile = self.get_profile(user_id)
        interests = sorted(profile.topics_of_interest.items(), key=lambda x: x[1], reverse=True)
        return interests[:top_n]

    def _save_profile(self, profile: UserProfile):
        """保存用户画像"""
        try:
            existing = self.storage.list_memories(
                category="user_profile", limit=10
            )

            profile_entry = None
            for entry in existing:
                try:
                    # P3 #29 修复：使用安全 JSON 解析
                    data = self.storage._safe_json_loads(entry.content)
                    if data is None:
                        continue
                    if data.get("user_id") == profile.user_id:
                        profile_entry = entry
                        break
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            content = json.dumps(profile.to_dict(), ensure_ascii=False)

            if profile_entry:
                self.storage.update_memory(
                    entry_id=profile_entry.id,
                    content=content,
                )
            else:
                self.storage.add_memory(
                    content=content,
                    category="user_profile",
                    tags=["profile", profile.user_id],
                    source_agent="personality_engine",
                )
        except (sqlite3.OperationalError, ValueError):
            pass
