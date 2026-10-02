# -*- coding: utf-8 -*-
"""LangGraph Store 适配器（v5.8.4 新增）。

将 MindForge 暴露为 LangGraph BaseStore 风格的跨线程长期记忆 Store，且不
引入强制 LangGraph 依赖。返回对象同时支持 LangGraph 的属性访问和原有映射
访问（例如 ``item.value`` 与 ``item["value"]``）。
"""
from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


def _as_namespace(namespace: Any) -> Tuple[str, ...]:
    if namespace is None:
        return ()
    if isinstance(namespace, str):
        return (namespace,) if namespace else ()
    return tuple(str(part) for part in namespace)


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.fromtimestamp(0, tz=timezone.utc)


class _StoreItem(dict):
    """符合 Item/SearchItem 属性协议，同时兼容原有 dict 风格调用。"""

    def __init__(self, namespace, key, value, created_at, updated_at,
                 memory_id=None, score=None):
        super().__init__(
            namespace=tuple(namespace),
            key=str(key),
            value=value,
            created_at=_as_datetime(created_at),
            updated_at=_as_datetime(updated_at),
        )
        if memory_id:
            self["memory_id"] = memory_id
        if score is not None:
            self["score"] = score

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def dict(self):
        return dict(self)


class MindForgeStore(_BaseStore):
    """LangGraph BaseStore 协议的 MindForge 后端。"""

    _LIST_PAGE_SIZE = 500

    def __init__(self, mindforge, default_category: str = "langgraph"):
        self.mf = mindforge
        self.default_category = default_category

    def _key_tag(self, key: str) -> str:
        return KeyTagPrefix + str(key)

    def _category(self, namespace: Tuple[str, ...]) -> str:
        return _ns_str(namespace) or self.default_category

    def _resolve_ids(self, namespace: Tuple[str, ...], key: str) -> List[str]:
        """按完整 namespace+tag 定位所有重复的 memory_id，不读取明文。"""
        try:
            hits = self.mf.search_by_tag(
                self._key_tag(key), category=self._category(namespace), limit=100
            )
        except AttributeError:
            return []
        ids = list(dict.fromkeys(
            str(getattr(hit, "id", "")) for hit in hits if getattr(hit, "id", None)
        ))
        requested = _as_namespace(namespace)
        resolved = []
        for memory_id in ids:
            entry = self._entry_for_id(memory_id)
            if entry is not None and self._namespace_for_entry(entry) == requested:
                resolved.append(memory_id)
        return resolved

    def _resolve_id(self, namespace: Tuple[str, ...], key: str) -> Optional[str]:
        ids = self._resolve_ids(namespace, key)
        return ids[0] if ids else None

    def _entry_for_id(self, memory_id: str):
        try:
            return self.mf._storage.get_memory(memory_id)
        except (AttributeError, TypeError):
            return None

    def _namespace_for_entry(self, entry) -> Tuple[str, ...]:
        """优先从适配器写入的 source_session 还原 tuple，兼容旧分类行。"""
        raw = getattr(entry, "source_session", "") or ""
        if raw:
            try:
                value = ast.literal_eval(raw)
                if isinstance(value, (tuple, list)) and all(
                        isinstance(part, str) for part in value):
                    return tuple(value)
            except (ValueError, SyntaxError, TypeError):
                pass
        category = str(getattr(entry, "category", "") or "")
        if category == self.default_category and not raw:
            return ()
        return tuple(category.split(NS_SEP)) if category else ()

    @staticmethod
    def _key_for_entry(entry) -> str:
        for tag in (getattr(entry, "tags", []) or []):
            tag = str(tag)
            if tag.startswith(KeyTagPrefix):
                return tag[len(KeyTagPrefix):]
        return ""

    @staticmethod
    def _decode_value(content: str) -> Dict[str, Any]:
        if not content:
            return {}
        try:
            value = json.loads(content)
        except (ValueError, TypeError):
            return {"value": content}
        return value if isinstance(value, dict) else {"value": value}

    def _make_item(self, namespace, key, value, entry=None,
                   memory_id=None, score=None):
        created_at = getattr(entry, "created_at", 0) if entry is not None else 0
        updated_at = getattr(entry, "updated_at", created_at) if entry is not None else created_at
        return _StoreItem(
            namespace=namespace,
            key=key,
            value=value,
            created_at=created_at,
            updated_at=updated_at,
            memory_id=memory_id or getattr(entry, "id", None),
            score=score,
        )

    def _iter_entries(self) -> Iterable[Any]:
        offset = 0
        while True:
            try:
                page = self.mf.list(
                    category=None, limit=self._LIST_PAGE_SIZE, offset=offset
                ) or []
            except AttributeError:
                return
            if not page:
                return
            yield from page
            if len(page) < self._LIST_PAGE_SIZE:
                return
            offset += len(page)

    @staticmethod
    def _namespace_matches(namespace, prefix) -> bool:
        prefix = _as_namespace(prefix)
        return len(namespace) >= len(prefix) and namespace[:len(prefix)] == prefix

    @staticmethod
    def _matches_filter(value: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> bool:
        if not filters:
            return True
        operators = {
            "$eq": lambda actual, expected: actual == expected,
            "$ne": lambda actual, expected: actual != expected,
            "$gt": lambda actual, expected: actual is not None and actual > expected,
            "$gte": lambda actual, expected: actual is not None and actual >= expected,
            "$lt": lambda actual, expected: actual is not None and actual < expected,
            "$lte": lambda actual, expected: actual is not None and actual <= expected,
        }
        for dotted_key, expected in filters.items():
            actual = value
            for part in str(dotted_key).split("."):
                actual = actual.get(part) if isinstance(actual, dict) else None
            if isinstance(expected, dict):
                for operator, operand in expected.items():
                    compare = operators.get(operator)
                    if compare is None:
                        return False
                    try:
                        if not compare(actual, operand):
                            return False
                    except (TypeError, ValueError):
                        return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _validate_page(limit: int, offset: int) -> None:
        if (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
                or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0):
            raise ValueError("limit and offset must be non-negative integers")

    # ---------- BaseStore 协议 ----------
    def put(self, namespace: Tuple[str, ...], key: str,
            value: Optional[Dict[str, Any]], index=None, *, ttl=None) -> None:
        """写入或删除一条记忆（同 namespace+key 保持幂等）。"""
        namespace = _as_namespace(namespace)
        if value is None:
            self.delete(namespace, key)
            return None
        category = self._category(namespace)
        for old_id in self._resolve_ids(namespace, key):
            self.mf.delete(old_id)
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        self.mf.add(
            content,
            category=category,
            tags=[self._key_tag(key)],
            source_session=str(namespace) if namespace else "",
        )
        return None

    def get(self, namespace: Tuple[str, ...], key: str, *, refresh_ttl=None):
        """按 namespace+key 精确读取，并返回 Item 兼容对象。"""
        namespace = _as_namespace(namespace)
        category = self._category(namespace)
        memory_id = self._resolve_id(namespace, key)
        if not memory_id:
            return None
        result = self.mf.search(
            self._key_tag(key), max_results=10, categories=[category]
        )
        for chunk in (result.chunks or []):
            chunk_id = getattr(chunk, "memory_id", "") or ""
            if chunk_id != memory_id:
                continue
            value = self._decode_value(getattr(chunk, "content", ""))
            entry = self._entry_for_id(memory_id)
            return self._make_item(namespace, key, value, entry, memory_id)
        return None

    def search(self, namespace_prefix: Tuple[str, ...], query: Optional[str] = None,
               filter: Optional[Dict[str, Any]] = None, limit: int = 10,
               offset: int = 0, *, refresh_ttl=None) -> List[_StoreItem]:
        """在 namespace 前缀下搜索，并在过滤后执行分页。"""
        self._validate_page(limit, offset)
        if limit == 0:
            return []
        prefix = _as_namespace(namespace_prefix)
        needed = offset + limit
        items: List[_StoreItem] = []

        if query:
            candidate_limit = max(10, needed)
            while True:
                result = self.mf.search(query, max_results=candidate_limit)
                items = []
                for chunk in (result.chunks or []):
                    memory_id = getattr(chunk, "memory_id", "") or ""
                    entry = self._entry_for_id(memory_id) if memory_id else None
                    namespace = self._namespace_for_entry(entry) if entry else tuple(
                        str(getattr(chunk, "category", "") or "").split(NS_SEP)
                    )
                    if not self._namespace_matches(namespace, prefix):
                        continue
                    key = self._key_for_entry(entry) if entry else ""
                    if not key:
                        continue
                    value = self._decode_value(getattr(chunk, "content", ""))
                    if not self._matches_filter(value, filter):
                        continue
                    items.append(self._make_item(
                        namespace, key, value, entry, memory_id,
                        score=getattr(chunk, "relevance_score", None),
                    ))
                if len(items) >= needed or len(result.chunks or []) < candidate_limit:
                    break
                candidate_limit *= 2
        else:
            for entry in self._iter_entries():
                namespace = self._namespace_for_entry(entry)
                if not self._namespace_matches(namespace, prefix):
                    continue
                key = self._key_for_entry(entry)
                if not key:
                    continue
                item = self.get(namespace, key)
                if item is None or not self._matches_filter(item.value, filter):
                    continue
                items.append(item)
                if len(items) >= needed:
                    break

        return items[offset:offset + limit]

    def delete(self, namespace: Tuple[str, ...], key: str) -> None:
        for memory_id in self._resolve_ids(_as_namespace(namespace), key):
            self.mf.delete(memory_id)

    def list_namespaces(self, *, prefix=None, suffix=None, max_depth=None,
                        limit: int = 100, offset: int = 0) -> List[Tuple[str, ...]]:
        """列出实际存有记忆的 namespace，支持前缀、后缀和分页。"""
        self._validate_page(limit, offset)
        prefix = _as_namespace(prefix)
        suffix = _as_namespace(suffix)
        if max_depth is not None:
            if (isinstance(max_depth, bool) or not isinstance(max_depth, int)
                    or max_depth < 0):
                raise ValueError("max_depth must be a non-negative integer")
            if len(prefix) > max_depth:
                return []
        if limit == 0:
            return []
        found = set()
        for entry in self._iter_entries():
            if not self._key_for_entry(entry):
                continue
            namespace = self._namespace_for_entry(entry)
            if prefix and not self._namespace_matches(namespace, prefix):
                continue
            if suffix and (len(namespace) < len(suffix)
                           or namespace[-len(suffix):] != suffix):
                continue
            if max_depth is not None:
                namespace = namespace[:max_depth]
            found.add(namespace)
        ordered = sorted(found)
        return ordered[offset:offset + limit]

    def list(self, prefix: Tuple[str, ...] = (),
             limit: int = 10, offset: int = 0) -> List[_StoreItem]:
        return self.search(prefix, query=None, limit=limit, offset=offset)

    def _list_namespaces_op(self, op) -> List[Tuple[str, ...]]:
        prefix = suffix = None
        for condition in (getattr(op, "match_conditions", None) or ()):
            match_type = getattr(condition, "match_type", "")
            match_type = getattr(match_type, "value", match_type)
            path = _as_namespace(getattr(condition, "path", ()))
            if match_type == "prefix":
                prefix = path
            elif match_type == "suffix":
                suffix = path
            else:
                raise ValueError("unsupported namespace match condition")
        return self.list_namespaces(
            prefix=prefix,
            suffix=suffix,
            max_depth=getattr(op, "max_depth", None),
            limit=getattr(op, "limit", 100),
            offset=getattr(op, "offset", 0),
        )

    # ---------- batch / async ----------
    @staticmethod
    def _unpack_item(item: Any):
        """兼容 (namespace,key,value) / 带 index 的 tuple / dict。"""
        if isinstance(item, dict):
            namespace = _as_namespace(item.get("namespace"))
            return namespace, str(item["key"]), item.get("value"), item.get("index")
        namespace, key, value = item[0], item[1], item[2]
        index = item[3] if len(item) > 3 else None
        return _as_namespace(namespace), str(key), value, index

    @staticmethod
    def _op_name(op: Any) -> str:
        return type(op).__name__

    def batch(self, items: Iterable[Any]) -> List[Any]:
        """按 LangGraph Op 分发；PutOp(value=None) 表示删除。"""
        out: List[Any] = []
        for item in items:
            op = self._op_name(item)
            if op == "GetOp":
                out.append(self.get(item.namespace, item.key,
                                    refresh_ttl=getattr(item, "refresh_ttl", None)))
            elif op == "SearchOp":
                out.append(self.search(
                    item.namespace_prefix,
                    query=getattr(item, "query", None),
                    filter=getattr(item, "filter", None),
                    limit=getattr(item, "limit", 10),
                    offset=getattr(item, "offset", 0),
                    refresh_ttl=getattr(item, "refresh_ttl", None),
                ))
            elif op == "ListNamespacesOp":
                out.append(self._list_namespaces_op(item))
            elif op == "PutOp":
                namespace, key, value = item.namespace, item.key, item.value
                if value is None:
                    self.delete(namespace, key)
                    out.append(None)
                else:
                    out.append(self.put(namespace, key, value,
                                        index=getattr(item, "index", None),
                                        ttl=getattr(item, "ttl", None)))
            elif isinstance(item, (tuple, list, dict)):
                namespace, key, value, index = self._unpack_item(item)
                out.append(self.put(namespace, key, value, index=index))
            else:
                out.append(None)
        return out

    async def abatch(self, items: Iterable[Any]) -> List[Any]:
        return await asyncio.to_thread(self.batch, list(items))

    async def aget(self, namespace, key, *, refresh_ttl=None):
        return await asyncio.to_thread(self.get, namespace, key,
                                       refresh_ttl=refresh_ttl)

    async def aput(self, namespace, key, value, index=None, *, ttl=None):
        return await asyncio.to_thread(self.put, namespace, key, value, index,
                                       ttl=ttl)

    async def asearch(self, namespace_prefix, query=None, filter=None,
                      limit=10, offset=0, *, refresh_ttl=None):
        return await asyncio.to_thread(
            self.search, namespace_prefix, query, filter, limit, offset,
            refresh_ttl=refresh_ttl,
        )

    async def adelete(self, namespace, key):
        return await asyncio.to_thread(self.delete, namespace, key)

    async def alist(self, prefix=(), limit=10, offset=0):
        return await asyncio.to_thread(self.list, prefix, limit, offset)

    async def alist_namespaces(self, *, prefix=None, suffix=None, max_depth=None,
                               limit=100, offset=0):
        return await asyncio.to_thread(
            self.list_namespaces,
            prefix=prefix,
            suffix=suffix,
            max_depth=max_depth,
            limit=limit,
            offset=offset,
        )


__all__ = ["MindForgeStore", "_LANGGRAPH_AVAILABLE"]
