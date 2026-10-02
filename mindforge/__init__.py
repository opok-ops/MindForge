# -*- coding: utf-8 -*-
"""
MindForge — AI Agent 终身记忆系统

四层记忆架构 · 知识图谱 · 多模态支持 · 端到端加密

命名空间包入口：所有子模块均在 mindforge.* 下，不再污染 site-packages 顶层。
"""

__author__ = "MindForge Project"
__license__ = "MIT"

from .core import (
    MindForge,
    MemoryEntry,
    MemoryLayer,
    Importance,
    PrivacyLevel,
    StorageEngine,
    QueryEngine,
)

from .modules import (
    RecallEngine,
    RecallConfig,
    KnowledgeGraph,
    PersonalityEngine,
    EventBus,
    MemoryEvolution,
    ExperienceEngine,
)

from .core.version import __version__

__all__ = [
    "MindForge",
    "MemoryEntry",
    "MemoryLayer",
    "Importance",
    "PrivacyLevel",
    "StorageEngine",
    "QueryEngine",
    "RecallEngine",
    "RecallConfig",
    "KnowledgeGraph",
    "PersonalityEngine",
    "EventBus",
    "MemoryEvolution",
    "ExperienceEngine",
    "__version__",
]
