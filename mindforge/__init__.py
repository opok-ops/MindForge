# -*- coding: utf-8 -*-
"""
MindForge — AI Agent 终身记忆系统

四层记忆架构 · 知识图谱引擎 · 多模态支持 · 人格化记忆 · 联邦网络 · AI短剧记忆

包入口（mindforge.* 命名空间）：自 v5.8.13 起全部源码统一位于 mindforge/
子包下，不再以 core/modules/mcp/api/cli/adapters 等顶层名称安装进
site-packages，避免与其它库冲突。旧式 `from MindForge import ...` 仍可通过
仓库根目录的 MindForge.py 兼容模块使用（不推荐新代码使用）。
"""

from .core import (
    MindForge,
    MemoryEntry,
    MemoryLayer,
    PrivacyLevel,
    Importance,
    MemoryType,
    MemoryConfig,
    StorageEngine,
    EncryptionEngine,
    IndexEngine,
    QueryEngine,
    AuditRecord,
    MemoryChunk,
    RecallResult,
    VectorIndex,
    init_engine,
    get_engine,
)
from .modules import (
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
    # v6.0.0 扩展端口预留（默认不启用，仅暴露发现/注册入口）
    V6_TARGET_VERSION,
    V6Capability,
    V6Status,
    V6Port,
    MultiAgentCollaborationPort,
    MultiAgentAdapterPort,
    MemoryPreviewPort,
    create_multi_agent_adapter,
    register_v6_port,
    get_v6_port,
    list_v6_ports,
    v6_status,
)
from .modules.event_bus import EventBus
from .modules.experience import ExperienceCase, SkillStore
from .core.version import __version__, VERSION_INFO, NEXT_MAJOR_TARGET

__author__ = "MindForge Project"
__license__ = "MIT"

__all__ = [
    "MindForge",
    "MemoryEntry",
    "MemoryLayer",
    "PrivacyLevel",
    "Importance",
    "MemoryType",
    "MemoryConfig",
    "StorageEngine",
    "EncryptionEngine",
    "IndexEngine",
    "QueryEngine",
    "AuditRecord",
    "MemoryChunk",
    "RecallResult",
    "VectorIndex",
    "init_engine",
    "get_engine",
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
    "EventBus",
    "ExperienceCase",
    "SkillStore",
    "__version__",
    "VERSION_INFO",
    "NEXT_MAJOR_TARGET",
    # v6.0.0 端口预留
    "V6_TARGET_VERSION",
    "V6Capability",
    "V6Status",
    "V6Port",
    "MultiAgentCollaborationPort",
    "MultiAgentAdapterPort",
    "MemoryPreviewPort",
    "create_multi_agent_adapter",
    "register_v6_port",
    "get_v6_port",
    "list_v6_ports",
    "v6_status",
]
