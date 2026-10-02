# -*- coding: utf-8 -*-
"""
MindForge - AI Agent 终身记忆系统
=======================================
四层记忆架构 · 知识图谱引擎 · 多模态支持 · 人格化记忆 · 联邦网络 · AI短剧记忆

兼容入口（自 v5.8.13 起）：全部源码已迁入 `mindforge.*` 命名空间，
本文件仅为向后兼容保留，使 `from MindForge import MindForge` 在 pip install
后仍然可用。新代码请统一使用 `from mindforge import MindForge`。
"""

from mindforge import *  # noqa: F401,F403
from mindforge import (  # noqa: F401
    __version__,
    VERSION_INFO,
    NEXT_MAJOR_TARGET,
)

__author__ = "MindForge Project"
__license__ = "MIT"
