"""
MindForge 默认文件系统路径（v5.6.5 P2 #18）

统一 CLI 与 MCP 两个入口的默认数据库位置，消除此前 CLI 用 cwd 相对的
``./data/memory.db``、MCP 用 ``~/.MindForge/data/store/memory.db`` 导致的
“同一份数据在不同入口看不到”的问题。

解析优先级：
1. 环境变量 ``MINDFORGE_DB_PATH``（显式指定，最高优先级）；
2. 跨 cwd 稳定的用户级目录 ``~/.MindForge/data/store/memory.db``。

注意：``core.storage.StorageEngine`` / ``core.types.MemoryConfig`` 的库级默认
仍是 ``./data/memory.db``（文档化的库用法，不在本次入口统一范围内改动）；
本模块只统一**应用入口**（CLI / MCP）的默认值。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 当入口未显式指定数据库、也未设置环境变量时使用的用户级默认目录（相对 home）。
_DEFAULT_DB_REL = (".MindForge", "data", "store")
_DEFAULT_DB_NAME = "memory.db"


def default_data_dir() -> Path:
    """返回用户级默认数据目录（必要时创建）。"""
    root = Path(os.path.expanduser("~")).joinpath(*_DEFAULT_DB_REL)
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_default_db_path() -> str:
    """返回入口层默认数据库路径。

    优先采用 ``MINDFORGE_DB_PATH`` 环境变量；否则返回
    ``~/.MindForge/data/store/memory.db``（绝对路径，与当前工作目录无关）。
    """
    env = os.environ.get("MINDFORGE_DB_PATH", "").strip()
    if env:
        return env
    return str(default_data_dir() / _DEFAULT_DB_NAME)
