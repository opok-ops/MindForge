from setuptools import setup, find_packages
from pathlib import Path
import re

here = Path(__file__).parent
long_description = (here / "README.md").read_text(encoding="utf-8")

# 从 core/version.py 读取版本号（唯一真值）。
# 正则兼容带类型注解的写法：`__version__: str = "5.6.1"`。
_VERSION_RE = re.compile(
    r'^__version__\s*(?::\s*[^=]+)?=\s*["\']([^"\']+)["\']', re.M
)


def _read_version() -> str:
    """按优先级读取版本号：core/version.py → MindForge.py → 0.0.0。

    v5.6.2 起顶层 MindForge.py 不再自带 `__version__` 字面量（改为从
    core.version 再导出），因此这里把读取源切到 core/version.py，
    并保留对旧布局的兼容回退，避免打包元数据版本变成 0.0.0。

    Returns:
        形如 "5.6.1" 的版本字符串。
    """
    for rel in ("core/version.py", "MindForge.py"):
        path = here / rel
        if not path.exists():
            continue
        match = _VERSION_RE.search(path.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    return "0.0.0"


version = _read_version()

setup(
    name="MindForge",
    version=version,
    description="AI Agent 终身记忆系统 - 四层记忆架构 · 知识图谱 · 多模态支持",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/opok-ops/MindForge",
    author="MindForge Project",
    license="MIT",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    keywords="ai agent memory knowledge-graph llm",
    packages=find_packages(
        exclude=["tests", "tests.*", "website", "examples", "data"]
    ),
    python_requires=">=3.9",
    install_requires=["cryptography>=50.0.1"],
    extras_require={
        "dev": [
            "pytest",
            "pytest-cov",
        ],
        "embedding": ["sentence-transformers>=2.2.0"],
    },
    entry_points={
        "console_scripts": [
            "MindForge=cli.main:main",
            "MindForge-mcp=mcp.server:main",
        ],
    },
    project_urls={
        "Bug Reports": "https://github.com/opok-ops/MindForge/issues",
        "Source": "https://github.com/opok-ops/MindForge",
        "Homepage": "https://opok-ops.github.io/MindForge/",
    },
)
