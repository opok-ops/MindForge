"""
MindForge v5.5.8 - AI Agent 终身记忆系统
=======================================
四层记忆架构 · 知识图谱引擎 · 多模态支持 · 人格化记忆 · 联邦网络 · AI短剧记忆
v5.5.8 修复：storage __version__ NameError · MCP 参数校验 · API body 校验 · 加密输入校验 · falsy 枚举值
v5.5.8 新增：memory_diff 版本对比 · MCP memory_diff 工具 · CLI memory-diff 命令
v5.5.7 安全加固：加密 fail-closed · 并发限制 · webhook 签名一致性 · banner 纯净 · WAL 降级
v5.5.6 新增：记忆置顶 · 批量获取 · 时间线视图 · 搜索建议 · 添加前去重 · 批量标签操作
v5.5.4 新增：记忆合并 · 最常访问 · 最近访问 · 批量更新 · 标签统计 · 索引一致性检查
v5.5.2 新增：Memory TTL过期机制 · 多关键词搜索高亮 · 按分类/标签批量删除 · FTS5溢出修复 · 查询引擎性能优化 · 全面BOM修复

便捷导入模块，使 `from MindForge import MindForge` 在 pip install 后可用。
底层实现仍在 core/ 和 modules/ 子包中。
"""

__version__ = "5.6.1"
__author__ = "MindForge Project"
__license__ = "MIT"

from core import (
    MindForge,
    MemoryEntry,
    MemoryLayer,
    PrivacyLevel,
    Importance,
    MemoryType,
    StorageEngine,
    EncryptionEngine,
    IndexEngine,
    QueryEngine,
)

from modules import (
    RecallEngine,
    RecallConfig,
    KnowledgeGraph,
    MemoryEvolution,
    PersonalityEngine,
    MultimodalMemory,
    FederatedMemory,
    TaxonomyManager,
    PrivacyEngine,
    MemoryIntegrator,
)

__all__ = [
    "MindForge",
    "MemoryEntry",
    "MemoryLayer",
    "PrivacyLevel",
    "Importance",
    "MemoryType",
    "StorageEngine",
    "EncryptionEngine",
    "IndexEngine",
    "QueryEngine",
    "RecallEngine",
    "RecallConfig",
    "KnowledgeGraph",
    "MemoryEvolution",
    "PersonalityEngine",
    "MultimodalMemory",
    "FederatedMemory",
    "TaxonomyManager",
    "PrivacyEngine",
    "MemoryIntegrator",
    "__version__",
]
