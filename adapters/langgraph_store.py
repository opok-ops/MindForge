# -*- coding: utf-8 -*-
"""LangGraph Store 适配器（v5.8.4 新增）

把 MindForge 暴露为 LangGraph 生态的跨线程长期记忆 Store（BaseStore 协议），
让用户在 LangGraph Checkpointer 之外获得可插拔的长期记忆后端：

    from adapters.langgraph_store import MindForgeStore

    store = MindForgeStore(mindforge)          # mindforge 实例
    # LangGraph: builder.compile(store=store)
    #   store.put(("users", "u1"), "prefs", {"theme": "dark"})
    #   store.search(("users", "u1"), query="theme")

设计要点：
- 零硬依赖：未安装 langgraph 时类仍可导入（基类回退 object）；
  安装 langgraph 后自动继承 langgraph.store.base.BaseStore，通过协议检查。
- namespace tuple → MindForge category（("users","u1") -> "users/u1"）；
  key 存入 tags（"mf_key:{key}"），value 以 JSON 作为记忆正文。
- 读取统一走 mf.search().chunks（唯一返回解密明文的 facade 路径）；
  delete 走 search_by_tag 定位 id（不需明文）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

try:
    from langgraph.store.base import BaseStore as _BaseStore  # type: ignore
    _LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - 无 langgraph 环境回退
    _BaseStore = object  # type: ignore
    _LANGGRAPH_AVAILABLE = False

KeyTagPrefix = "mf_key:"
NS_SEP = "/"


def _ns_str(namespace: Tuple[str, ...]) -> str:
    if isinstance(namespace, str):
        return namespace
    return NS_SEP.join(str(x) for x in namespace)


class MindForgeStore(_BaseStore):
    """LangGraph BaseStore 协议的 MindForge 后端（v5.8.4）。"""

    def __init__(self, mindforge, default_category: str = "langgraph"):
        self.mf = mindforge
        self.default_category = default_category

    def _key_tag(self, key: str) -> str:
        return KeyTagPrefix + str(key)

    def _resolve_id(self, namespace: Tuple[str, ...], key: str) -> Optional[str]:
        """按 namespace+tag 定位 memory_id（不解密）。"""
        category = _ns_str(namespace) or self.default_category
        try:
            hits = self.mf.search_by_tag(self._key_tag(key),
                                         category=category, limit=5)
        except AttributeError:
            return None
        return hits[0].id if hits else None

    # ---------- BaseStore 协议 ----------
    def put(self, namespace: Tuple[str, ...], key: str,
            value: Dict[str, Any], index: Optional[Dict] = None) -> Any:
        """写入一条记忆（幂等：同 namespace+key 先删旧再写新）。"""
        category = _ns_str(namespace) or self.default_category
        old_id = self._resolve_id(namespace, key)
        if old_id:
            self.mf.delete(old_id)
        content = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             default=str)
        return self.mf.add(
            content,
            category=category,
            tags=[self._key_tag(key)],
            source_session=str(namespace) if namespace else "",
        )

    def get(self, namespace: Tuple[str, ...], key: str) -> Optional[Dict[str, Any]]:
        """按 namespace+key 精确读取（返回解密后的 value dict）。

        v5.8.8 加固：先用 search_by_tag 精确定位 memory_id，再在 search().chunks
        里按 memory_id 过滤，避免同 namespace 下 key 前缀相似时误命中
        （如 mf_key:prefs 串到 mf_key:prefs_backup）。
        """
        category = _ns_str(namespace) or self.default_category
        mid = self._resolve_id(namespace, key)
        rr = self.mf.search(self._key_tag(key), max_results=5,
                            categories=[category])
        for c in (rr.chunks or []):
            cid = getattr(c, "memory_id", "") or ""
            if mid and cid and cid != mid:
                continue
            if getattr(c, "content", ""):
                try:
                    obj = json.loads(c.content)
                    if isinstance(obj, dict):
                        return {"namespace": list(namespace) if namespace else [],
                                "key": key, "value": obj}
                except (ValueError, TypeError):
                    return {"namespace": list(namespace) if namespace else [],
                            "key": key, "value": c.content}
        return None

    def search(self, namespace_prefix: Tuple[str, ...],
               query: Optional[str] = None,
               filter: Optional[Dict[str, Any]] = None,
               limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
        """在命名空间前缀下搜索（query 为空则列该 category）。"""
        category = _ns_str(namespace_prefix) if namespace_prefix else None
        if query:
            rr = self.mf.search(query, max_results=limit,
                                categories=[category] if category else None)
            chunks = list(rr.chunks or [])
        else:
            # list() 返回的 MemoryEntry.content 在加密模式下为空，
            # 需从 tags 反解 mf_key:{key}，再走 get() 拿解密 value。
            try:
                rows = self.mf.list(category=category, limit=limit) or []
            except AttributeError:
                rows = []
            chunks = []
            for r in rows:
                key = ""
                for t in (getattr(r, "tags", []) or []):
                    if str(t).startswith(KeyTagPrefix):
                        key = str(t)[len(KeyTagPrefix):]
                        break
                chunks.append({"content": "", "key": key,
                               "memory_id": getattr(r, "id", "")})
        out: List[Dict[str, Any]] = []
        for c in chunks:
            key = c.get("key", "") if isinstance(c, dict) else ""
            if key:
                item = self.get(namespace_prefix, key)
                if item is not None:
                    c["content"] = json.dumps(item["value"], ensure_ascii=False)
            content = c.get("content", "") if isinstance(c, dict) else getattr(c, "content", "")
            try:
                value = json.loads(content) if content else {}
                if not isinstance(value, dict):
                    value = {"value": content}
            except (ValueError, TypeError):
                value = {"value": content} if content else {}
            out.append({
                "namespace": list(namespace_prefix) if namespace_prefix else [],
                "key": key,
                "value": value,
                "memory_id": c.get("memory_id", "") if isinstance(c, dict) else getattr(c, "memory_id", ""),
            })
        return out[offset:offset + limit]

    def delete(self, namespace: Tuple[str, ...], key: str) -> None:
        mid = self._resolve_id(namespace, key)
        if mid:
            self.mf.delete(mid)

    def list(self, prefix: Tuple[str, ...] = (),
             limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
        return self.search(prefix, query=None, limit=limit, offset=offset)


__all__ = ["MindForgeStore", "_LANGGRAPH_AVAILABLE"]
