# -*- coding: utf-8 -*-
"""CrewAI Memory 适配器（v5.8.4 新增）

把 MindForge 包装为 CrewAI 生态的长期记忆后端（Memory 接口）：

    from adapters.crewai_memory import CrewAIMemory

    memory = CrewAIMemory(mindforge, namespace="crew_travel")
    memory.save("用户偏好靠窗座位", metadata={"trip": "tokyo"})
    memory.search("座位偏好", limit=3)

设计要点：
- 零硬依赖：未安装 crewai 时类仍可导入（基类回退 object）。
- 写：save() -> MindForge add()，namespace 编码进 category，metadata 扁平为 tags；
- 读：search() -> MindForge 混合检索（mf.search().chunks 解密明文路径）；
- reset() 仅清空本 namespace 条目（list 定位 id 后逐条 delete），不影响其他应用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from crewai.memory.memory import Memory as _CrewMemory  # type: ignore
    _CREWAI_AVAILABLE = True
except Exception:  # pragma: no cover - 无 crewai 环境回退
    _CrewMemory = object  # type: ignore
    _CREWAI_AVAILABLE = False


class CrewAIMemory(_CrewMemory):
    """CrewAI Memory 协议的 MindForge 后端（v5.8.4）。"""

    def __init__(self, mindforge, namespace: str = "crewai"):
        self.mf = mindforge
        self.namespace = namespace

    def save(self, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        """保存一条记忆。value 序列化为文本，metadata 扁平为 tags。"""
        metadata = metadata or {}
        tags = ["%s:%s" % (k, v) for k, v in metadata.items()
                if isinstance(v, (str, int, float, bool))]
        self.mf.add(
            str(value),
            category=self.namespace,
            tags=tags,
            source_agent="crewai",
        )

    def search(self, query: str, limit: int = 3,
               filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """搜索记忆，返回 CrewAI 风格 list[dict]（context 为解密明文）。"""
        rr = self.mf.search(query, max_results=limit,
                            categories=[self.namespace])
        results: List[Dict[str, Any]] = []
        for c in (rr.chunks or []):
            results.append({
                "context": getattr(c, "content", "") or "",
                "metadata": {
                    "id": getattr(c, "memory_id", ""),
                    "category": getattr(c, "category", self.namespace),
                    "tags": getattr(c, "tags", []),
                },
            })
        return results

    def reset(self) -> None:
        """清空本 namespace 的全部记忆（不影响其他应用）。循环删直到空。"""
        for _ in range(100):  # 安全上限，防意外死循环
            rows = self.mf.list(category=self.namespace, limit=500) or []
            if not rows:
                return
            for h in rows:
                self.mf.delete(getattr(h, "id", ""))


__all__ = ["CrewAIMemory", "_CREWAI_AVAILABLE"]
