"""
MindForge OpenClaw 适配器
"""

from typing import List, Dict, Optional

# v5.6.8 修复：适配器上报的版本号此前硬编码为 5.0.1（早已过期）。改为引用唯一真值。
from core.version import __version__ as _MF_VERSION


class OpenClawAdapter:
    """OpenClaw 记忆适配器"""

    def __init__(self, MindForge):
        self.cm = MindForge
        self.session_id = "openclaw"
        self.agent_id = "openclaw"

    def store(self, content: str, metadata: Optional[Dict] = None) -> str:
        """存储记忆"""
        metadata = metadata or {}
        entry = self.cm.add(
            content=content,
            category=metadata.get("category", "general"),
            tags=metadata.get("tags", []),
            source_session=self.session_id,
            source_agent=self.agent_id,
            metadata=metadata,
        )
        return entry.id

    def recall(self, query: str, limit: int = 10) -> List[Dict]:
        """召回记忆"""
        result = self.cm.search(
            query=query,
            max_results=limit,
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        return [
            {
                "id": chunk.memory_id,
                "content": chunk.content,
                "category": chunk.category,
                "relevance": chunk.relevance_score,
                "layer": chunk.layer.value,
                "tags": chunk.tags,
            }
            for chunk in result.chunks
        ]

    def forget(self, memory_id: str) -> bool:
        """删除记忆"""
        return self.cm.delete(memory_id, self.agent_id, self.session_id)

    def get_stats(self) -> Dict:
        """获取统计"""
        return self.cm.stats()

    def set_session(self, session_id: str):
        """设置会话"""
        self.session_id = session_id

    def set_agent(self, agent_id: str):
        """设置 Agent"""
        self.agent_id = agent_id

    def consolidate(self):
        """触发记忆巩固"""
        from modules.evolution import MemoryEvolution
        evo = MemoryEvolution(self.cm.storage)
        return evo.consolidate(self.agent_id, self.session_id)

    def get_personality(self, user_id: str = "default") -> Dict:
        """获取用户画像"""
        from modules.personality import PersonalityEngine
        pe = PersonalityEngine(self.cm.storage)
        return pe.get_recommended_style(user_id)

    def to_config_dict(self) -> Dict:
        """输出配置字典格式"""
        return {
            "adapter": "MindForge",
            "version": _MF_VERSION,
            "features": [
                "four_layer_memory",
                "knowledge_graph",
                "multimodal",
                "personality",
                "federated",
            ],
        }
