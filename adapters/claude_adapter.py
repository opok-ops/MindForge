"""
MindForge v5.0 Claude Code 适配器
"""

import os
from typing import List, Dict, Optional


class ClaudeCodeAdapter:
    """Claude Code 记忆适配器"""

    def __init__(self, MindForge):
        self.cm = MindForge
        self.session_id = os.environ.get("CLAUDE_SESSION_ID", "claude")
        self.agent_id = "claude_code"

    @classmethod
    def from_env(cls):
        """从环境变量创建"""
        # v5.6.8 修复：`core` 已是顶层包，`..core` 为越界相对导入，调用时必抛
        # ImportError。改为与 modules/*、core/* 一致的绝对导入。
        from core import MindForge, MemoryConfig

        db_path = os.environ.get("MindForge_DB_PATH", "./data/memory.db")
        key_file = os.environ.get("MindForge_KEY_FILE", "./data/.key")
        encrypted = os.environ.get("MindForge_ENCRYPTED", "true").lower() == "true"

        config = MemoryConfig(
            db_path=db_path,
            key_file=key_file,
            encrypted=encrypted,
        )
        cm = MindForge(config=config)
        return cls(cm)

    def remember(self, content: str, tags: Optional[List[str]] = None) -> str:
        """记住某件事"""
        entry = self.cm.add(
            content=content,
            tags=tags or [],
            source_agent=self.agent_id,
            source_session=self.session_id,
        )
        return entry.id

    def recall(self, query: str, limit: int = 5) -> List[str]:
        """回忆相关内容"""
        result = self.cm.search(
            query=query,
            max_results=limit,
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        return [chunk.content for chunk in result.chunks]

    def forget(self, memory_id: str) -> bool:
        """忘记某件事"""
        return self.cm.delete(memory_id, self.agent_id, self.session_id)

    def get_context(self, current_task: str, limit: int = 10) -> str:
        """获取相关上下文"""
        result = self.cm.search(
            query=current_task,
            max_results=limit,
            agent_id=self.agent_id,
            session_id=self.session_id,
        )

        context_parts = ["# Relevant Memory\n"]
        for i, chunk in enumerate(result.chunks, 1):
            context_parts.append(f"## Memory {i} ({chunk.category})\n")
            context_parts.append(f"{chunk.content}\n")

        return "\n".join(context_parts)

    def learn_user_preference(self, user_message: str, response: str):
        """学习用户偏好"""
        from modules.personality import PersonalityEngine
        pe = PersonalityEngine(self.cm.storage)
        pe.learn_from_interaction("default", user_message, response)

    def get_user_style(self) -> Dict:
        """获取用户风格偏好"""
        from modules.personality import PersonalityEngine
        pe = PersonalityEngine(self.cm.storage)
        return pe.get_recommended_style()
