"""薄壳：所有打包配置均在 pyproject.toml。

保留此文件仅为兼容仍会调用 `python setup.py` 的旧工具/CI 脚本；
实际元数据（版本、依赖、入口点、classifiers）全部由 pyproject.toml 声明，
版本号经 dynamic version 从 mindforge/core/version.py 动态读取。
"""

from setuptools import setup

setup()
