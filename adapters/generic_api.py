"""
MindForge v5.0 通用 API 适配器
REST API / SDK 风格接口
"""

from typing import List, Dict, Optional, Callable, Any


class GenericAPIAdapter:
    """通用 API 适配器"""

    def __init__(self, MindForge):
        self.cm = MindForge
        self._handlers: Dict[str, Callable] = {}
        self._register_default_handlers()

    def _register_default_handlers(self):
        self._handlers = {
            "memory.add": self._handle_add,
            "memory.get": self._handle_get,
            "memory.search": self._handle_search,
            "memory.list": self._handle_list,
            "memory.update": self._handle_update,
            "memory.delete": self._handle_delete,
            "stats.get": self._handle_stats,
            "graph.stats": self._handle_graph_stats,
            "graph.related": self._handle_graph_related,
            "personality.profile": self._handle_personality,
            "evolution.consolidate": self._handle_consolidate,
        }

    def handle_request(self, request: Dict) -> Dict:
        """处理 API 请求

        P2 #19 修复：
        - 校验 request 结构（action 必选且为字符串）
        - 异常信息脱敏，不暴露内部调用栈细节
        """
        if not isinstance(request, dict):
            return {"success": False, "error": "Invalid request format"}

        action = request.get("action", "")
        params = request.get("params", {})

        if not action or not isinstance(action, str):
            return {"success": False, "error": "Missing required field: action"}
        if not isinstance(params, dict):
            return {"success": False, "error": "Invalid params format"}

        handler = self._handlers.get(action)
        if not handler:
            return {
                "success": False,
                "error": f"Unknown action: {action}",
            }

        try:
            result = handler(params)
            return {
                "success": True,
                "data": result,
            }
        except (ValueError, TypeError) as e:
            # 预期内的错误：返回消息（用户输入类）
            return {
                "success": False,
                "error": str(e),
            }
        except Exception as e:
            # 未预期的错误：脱敏返回，防止内部细节泄露
            import logging as _logging
            _logging.getLogger(__name__).exception("API handler error: %s", action)
            return {
                "success": False,
                "error": "Internal error",
            }

    @staticmethod
    def _require(params: Dict, field: str, error_msg: str = "") -> Any:
        """校验必需字段，缺失时返回友好错误"""
        if field not in params:
            raise ValueError(error_msg or f"Missing required field: {field}")
        return params[field]

    def _handle_add(self, params: Dict) -> Dict:
        entry = self.cm.add(
            content=params["content"],
            category=params.get("category", "general"),
            tags=params.get("tags", []),
            source_agent=params.get("agent", "api"),
            source_session=params.get("session", "api"),
        )
        return entry.to_dict()

    def _handle_get(self, params: Dict) -> Optional[Dict]:
        entry = self.cm.get(
            params["id"],
            actor=params.get("actor", "api"),
            session_id=params.get("session_id", ""),
        )
        return entry.to_dict() if entry else None

    def _handle_search(self, params: Dict) -> Dict:
        result = self.cm.search(
            query=params["query"],
            max_results=params.get("limit", 10),
            categories=params.get("categories"),
            actor=params.get("actor", "api"),
            session_id=params.get("session_id", ""),
        )
        return {
            "total": result.total_found,
            "time_ms": result.query_time_ms,
            "results": [
                {
                    "id": c.memory_id,
                    "content": c.content,
                    "category": c.category,
                    "relevance": c.relevance_score,
                }
                for c in result.chunks
            ],
        }

    def _handle_list(self, params: Dict) -> List[Dict]:
        entries = self.cm.list(
            category=params.get("category"),
            limit=params.get("limit", 50),
            offset=params.get("offset", 0),
        )
        return [e.to_dict() for e in entries]

    def _handle_update(self, params: Dict) -> bool:
        return self.cm.update(
            memory_id=params["id"],
            content=params.get("content"),
            category=params.get("category"),
            tags=params.get("tags"),
            actor=params.get("actor", "api"),
            session_id=params.get("session_id", ""),
        )

    def _handle_delete(self, params: Dict) -> bool:
        return self.cm.delete(
            params["id"],
            actor=params.get("actor", "api"),
            session_id=params.get("session_id", ""),
        )

    def _handle_stats(self, params: Dict) -> Dict:
        return self.cm.stats()

    def _handle_graph_stats(self, params: Dict) -> Dict:
        from ..modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph(storage=self.cm.storage)
        return kg.get_entity_stats()

    def _handle_graph_related(self, params: Dict) -> List:
        from ..modules.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph(storage=self.cm.storage)
        related = kg.get_related_entities(
            params["entity"],
            depth=params.get("depth", 2),
        )
        return [{"name": n, "relation": r, "weight": w} for n, r, w in related]

    def _handle_personality(self, params: Dict) -> Dict:
        from ..modules.personality import PersonalityEngine
        pe = PersonalityEngine(self.cm.storage)
        return pe.get_recommended_style(params.get("user_id", "default"))

    def _handle_consolidate(self, params: Dict) -> Dict:
        from ..modules.evolution import MemoryEvolution
        evo = MemoryEvolution(self.cm.storage)
        return evo.consolidate()

    def register_handler(self, action: str, handler: Callable):
        """注册自定义处理器"""
        self._handlers[action] = handler
