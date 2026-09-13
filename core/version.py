"""
MindForge 版本号单一来源
========================

历史问题：`__version__` 的真值散落在 5 处（顶层包、core/storage.py、
core/mindforge.py、cli/main.py、mcp/server.py），每处都带一份硬编码兜底。
发版时要改 5 次，漏改就会出现「顶层 5.6.1 / storage 5.6.0」这类漂移。

本模块把版本号收敛为唯一真值：

- 运行时代码一律从这里 import，不再自带硬编码兜底；
- `pyproject.toml` 与 `setup.py` 仍然各自声明/读取同一份字符串
  （打包元数据无法在导入期依赖本模块），发版时同步这两处即可。

发版清单：改 `core/version.py` 的 `__version__` + `pyproject.toml` 的
`project.version`，其余位置自动跟随。
"""

from typing import Tuple

#: 当前发布版本（唯一真值）
__version__: str = "5.6.3"

#: 结构化版本元组，便于比较（如 `VERSION_INFO >= (6, 0, 0)`）
VERSION_INFO: Tuple[int, int, int] = (5, 6, 3)

#: 下一个大版本的目标版本号（供 v6 端口预留引用）
NEXT_MAJOR_TARGET: str = "6.0.0"


def get_version() -> str:
    """返回当前版本号字符串（等价于 `__version__`）。

    Returns:
        形如 "5.6.1" 的 semver 字符串。
    """
    return __version__


def get_version_info() -> Tuple[int, int, int]:
    """返回结构化版本元组。

    Returns:
        形如 (5, 6, 1) 的三元整数元组。
    """
    return VERSION_INFO


__all__ = [
    "__version__",
    "VERSION_INFO",
    "NEXT_MAJOR_TARGET",
    "get_version",
    "get_version_info",
]
