# -*- coding: utf-8 -*-
"""评测后端（参赛者协议）。

任何记忆系统实现 BaseBackend 协议即为同等地位的参赛者：
add() 接收对话轮，search() 对问题返回 top-K 记忆片段。

内置：
- MindForgeBackend     — MindForge 本体（临时库，评测后即弃）
- ContextWindowBackend — 基线：无记忆检索，把全部轮次原样当片段
  返回（模拟「裸上下文窗口」，供对比记忆系统的增量价值）

远程系统（Mem0 / Zep 等 API 服务）实现同名方法即可接入；
CLI --backend 列表按注册表动态扩展。
"""
import os
import tempfile
from typing import Any, Dict, List, Optional, Protocol


class RetrievedChunk(Dict[str, Any]):
    """检索片段统一表示：content / category / score。"""


class BaseBackend(Protocol):
    """参赛者协议。实现方不继承也可（鸭子类型）。"""

    name: str

    def add(self, texts: List[str], category: str) -> int:
        """批量写入记忆，返回写入条数。"""

    def search(self, query: str, k: int) -> List[RetrievedChunk]:
        """检索 top-K，返回 [{content, category, score}]。"""

    def close(self) -> None:
        """释放资源（临时库/连接）。"""

    def reset(self) -> None:
        """清空当前记忆库（评测按会话分组，每组独立库）。"""


class MindForgeBackend:
    """MindForge 本体。每个评测对象一个临时库，评测后即弃。"""

    name = "mindforge"

    def __init__(self, encrypted: bool = False, seed: int = 42,
                 workdir: Optional[str] = None) -> None:
        from mindforge.core.mindforge import MindForge
        from mindforge.core.types import MemoryConfig
        self._dir = workdir or tempfile.mkdtemp(prefix="mf_bench_")
        self._enc = encrypted
        cfg = MemoryConfig(
            db_path=os.path.join(self._dir, "bench.db"),
            key_file=None, encrypted=encrypted)
        self._mf = MindForge(config=cfg)
        if encrypted:
            self._mf.init_with_password(f"bench-pw-{seed}")

    def add(self, texts: List[str], category: str) -> int:
        for t in texts:
            self._mf.add(t, category=category, source_agent="bench_pipeline")
        return len(texts)

    def search(self, query: str, k: int) -> List[RetrievedChunk]:
        hits = self._mf.search(query, max_results=k)
        return [RetrievedChunk(content=h.content,
                               category=getattr(h, "category", ""),
                               score=float(getattr(h, "score", 0.0) or 0.0))
                for h in hits.chunks[:k]]

    def reset(self) -> None:
        """清空当前库：关闭旧实例，重建一个全新临时库。"""
        self.close()
        self._dir = tempfile.mkdtemp(prefix="mf_bench_")
        from mindforge.core.mindforge import MindForge
        from mindforge.core.types import MemoryConfig
        cfg = MemoryConfig(
            db_path=os.path.join(self._dir, "bench.db"),
            key_file=None, encrypted=self._enc)
        self._mf = MindForge(config=cfg)
        if self._enc:
            self._mf.init_with_password("bench-pw-reset")

    def close(self) -> None:
        try:
            self._mf.close()
        except Exception:
            pass
        try:
            os.remove(os.path.join(self._dir, "bench.db"))
        except OSError:
            pass


class ContextWindowBackend:
    """裸上下文窗口基线：不做任何检索，把全部轮次直接当片段返回。

    用途：证明记忆系统的增量价值（对比「不用记忆系统」的下限）。
    检索延迟记为 0（无检索发生），Recall/MRR 仍按片段位置正常计算。
    """

    name = "context_window"

    def __init__(self, **kwargs: Any) -> None:
        self._turns: List[RetrievedChunk] = []

    def add(self, texts: List[str], category: str) -> int:
        self._turns = [RetrievedChunk(content=t, category=category, score=1.0)
                       for t in texts]
        return len(texts)

    def search(self, query: str, k: int) -> List[RetrievedChunk]:
        return self._turns[:k]

    def reset(self) -> None:
        self._turns = []

    def close(self) -> None:
        self._turns = []


BACKEND_REGISTRY: Dict[str, Any] = {
    "mindforge": MindForgeBackend,
    "context_window": ContextWindowBackend,
}
