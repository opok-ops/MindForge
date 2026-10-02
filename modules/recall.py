"""
MindForge v5.0 召回引擎
语义检索 + 知识图谱增强 + 上下文优化
"""

import copy
import time
from dataclasses import dataclass
from typing import List, Optional, Any

from core.storage import StorageEngine, MemoryEntry
from core.indexer import IndexEngine
from core.query import QueryEngine, MemoryChunk, RecallResult
from core.types import Importance, MemoryLayer


@dataclass
class RecallConfig:
    """召回配置"""
    max_results: int = 10
    min_relevance: float = 0.3
    include_categories: Optional[List[str]] = None
    exclude_categories: Optional[List[str]] = None
    include_layers: Optional[List[MemoryLayer]] = None
    min_importance: Optional[Any] = None
    max_tokens: int = 4000
    use_knowledge_graph: bool = True
    use_reranking: bool = True
    diversity_weight: float = 0.2
    temporal_bias: float = 0.1


class RecallEngine:
    """召回引擎"""

    def __init__(self, storage: StorageEngine, index: IndexEngine,
                 knowledge_graph=None):
        self.storage = storage
        self.index = index
        self.query_engine = QueryEngine(storage, index)
        self.knowledge_graph = knowledge_graph

    def recall(self,
               query: str,
               agent_id: str = "",
               session_id: str = "",
               config: Optional[RecallConfig] = None) -> RecallResult:
        """召回记忆"""
        start = time.time()
        config = config or RecallConfig()

        base_results = self.query_engine.search(
            query=query,
            max_results=config.max_results * 3,
            min_relevance=config.min_relevance * 0.5,
            categories=config.include_categories,
            layers=config.include_layers,
            agent_id=agent_id,
            session_id=session_id,
        )

        chunks = base_results.chunks

        if config.use_knowledge_graph and self.knowledge_graph:
            kg_chunks = self._kg_enhanced_search(query, config)
            chunks = self._merge_results(chunks, kg_chunks)

        if config.exclude_categories or config.min_importance is not None:
            chunks = self._apply_config_filters(chunks, config)

        if config.use_reranking:
            chunks = self._rerank(chunks, query, config)

        chunks = chunks[:config.max_results]

        token_estimate = sum(len(c.content) for c in chunks) // 4
        if config.max_tokens > 0 and token_estimate > config.max_tokens:
            chunks = self._optimize_context_window(chunks, config.max_tokens)

        elapsed = (time.time() - start) * 1000
        layers_used = list(set(c.layer.value for c in chunks))

        return RecallResult(
            chunks=chunks,
            total_found=len(chunks),
            query_time_ms=round(elapsed, 2),
            strategy_used="hybrid_semantic_kg",
            token_estimate=sum(len(c.content) for c in chunks) // 4,
            layers_used=layers_used,
        )

    def _apply_config_filters(self, chunks: List[MemoryChunk],
                              config: RecallConfig) -> List[MemoryChunk]:
        """应用 RecallConfig 中声明的排除分类和最低重要度。"""
        minimum = config.min_importance
        if minimum is not None:
            if isinstance(minimum, str):
                try:
                    minimum = Importance[minimum.strip().upper()]
                except KeyError as exc:
                    raise ValueError(
                        "min_importance must be LOW/MEDIUM/HIGH/CRITICAL"
                    ) from exc
            elif not isinstance(minimum, Importance):
                if (isinstance(minimum, int) and not isinstance(minimum, bool)
                        and 0 <= minimum <= 3):
                    minimum = minimum
                else:
                    raise ValueError(
                        "min_importance must be an Importance, valid name, or rank 0-3"
                    )

        kept = []
        excluded = set(config.exclude_categories or [])
        for chunk in chunks:
            entry = self.storage.get_memory(chunk.memory_id)
            if entry is None or entry.category in excluded:
                continue
            if minimum is not None:
                rank = minimum if isinstance(minimum, int) else minimum.to_int()
                if entry.importance.to_int() < rank:
                    continue
            kept.append(chunk)
        return kept

    def _kg_enhanced_search(self, query: str, config: RecallConfig) -> List[MemoryChunk]:
        """知识图谱增强搜索"""
        chunks = []
        entities = self.knowledge_graph.extract_entities(query)

        for entity_name, _ in entities:
            related = self.knowledge_graph.get_related_entities(
                entity_name, depth=1, max_results=10
            )

            for related_name, rel_type, weight in related:
                expanded_query = f"{query} {related_name}"
                result = self.query_engine.search(
                    query=expanded_query,
                    max_results=3,
                    min_relevance=config.min_relevance,
                    categories=config.include_categories,
                )
                for chunk in result.chunks:
                    chunk.relevance_score *= weight * 0.8
                    chunks.append(chunk)

        return chunks

    def _merge_results(self, base: List[MemoryChunk],
                       enhanced: List[MemoryChunk]) -> List[MemoryChunk]:
        """合并搜索结果"""
        seen = set()
        merged = []

        for chunk in base:
            if chunk.memory_id not in seen:
                seen.add(chunk.memory_id)
                merged.append(chunk)

        for chunk in enhanced:
            if chunk.memory_id not in seen:
                seen.add(chunk.memory_id)
                merged.append(chunk)
            else:
                for existing in merged:
                    if existing.memory_id == chunk.memory_id:
                        existing.relevance_score = max(
                            existing.relevance_score, chunk.relevance_score
                        )
                        break

        merged.sort(key=lambda x: x.relevance_score, reverse=True)
        return merged

    def _rerank(self, chunks: List[MemoryChunk], query: str,
                config: RecallConfig) -> List[MemoryChunk]:
        """重排序"""
        if not chunks:
            return chunks

        max_score = max(c.relevance_score for c in chunks) if chunks else 1.0

        # v5.4.7 修复 M-6：批量获取记忆，避免 N+1 查询
        memory_ids = [c.memory_id for c in chunks]
        entries_map = {}
        for mid in memory_ids:
            entry = self.storage.get_memory(mid)
            if entry:
                entries_map[mid] = entry

        for chunk in chunks:
            score = chunk.relevance_score / max_score if max_score > 0 else 0

            entry = entries_map.get(chunk.memory_id)
            if entry:
                recency = 1.0
                if entry.last_accessed_at:
                    import time as _time
                    elapsed_hours = (_time.time() - entry.last_accessed_at) / 3600
                    recency = 1.0 / (1.0 + elapsed_hours * 0.01)

                importance_bonus = entry.importance.to_int() / 3.0 * 0.2

                final_score = (
                    score * 0.6
                    + recency * config.temporal_bias
                    + importance_bonus
                )

                chunk.relevance_score = final_score

        chunks.sort(key=lambda x: x.relevance_score, reverse=True)
        return chunks

    def _optimize_context_window(self, chunks: List[MemoryChunk],
                                  max_tokens: int) -> List[MemoryChunk]:
        """优化上下文窗口"""
        selected = []
        total_tokens = 0

        for chunk in chunks:
            chunk_tokens = len(chunk.content) // 4
            if total_tokens + chunk_tokens <= max_tokens:
                selected.append(chunk)
                total_tokens += chunk_tokens
            else:
                remaining = max_tokens - total_tokens
                if remaining > 50:
                    # v5.5.8 修复：原写法 `truncated = chunk` 只是别名而非拷贝，
                    # 随后的赋值会原地修改调用方持有的 MemoryChunk，
                    # 导致记忆内容被永久截断（缓存/复用场景尤其危险）。
                    truncated = copy.copy(chunk)
                    truncated.content = chunk.content[:remaining * 4]
                    selected.append(truncated)
                break

        return selected

    def get_session_relevant(self, session_id: str,
                             current_query: str,
                             limit: int = 10) -> List[MemoryEntry]:
        """获取会话相关记忆"""
        session_memories = self.query_engine.get_session_context(session_id, limit * 2)

        result = self.recall(
            query=current_query,
            session_id=session_id,
            config=RecallConfig(max_results=limit),
        )

        # v5.4.7 修复 M-6：避免重复查询同一条记忆
        entries = []
        seen_ids = set()
        for c in result.chunks:
            if c.memory_id not in seen_ids:
                entry = self.storage.get_memory(c.memory_id)
                if entry and entry.source_session in ("", session_id):
                    entries.append(entry)
                    seen_ids.add(c.memory_id)
        # 会话上下文即使未命中当前查询，也应保留在会话召回结果中；
        # 不把其他会话的记忆混入该上下文。
        for entry in session_memories:
            if entry.id not in seen_ids:
                entries.append(entry)
                seen_ids.add(entry.id)
            if len(entries) >= limit:
                break
        return entries[:limit]
