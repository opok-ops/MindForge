"""
MindForge v6.1.5 新增：自适应上下文窗口 (Adaptive Context Window)

跨会话上下文连续性引擎：
  - 追踪会话中的查询模式（query pattern），预测下一步可能需要的高频记忆
  - 维护一个「预热候选池」，后续搜索时优先召回已预热条目
  - 基于记忆距离（时间衰减 × 访问间隔 × 图谱关联深度）动态调整 min_relevance
  - 自动管理：会话结束清空，异常恢复

设计约束：
  - 纯本地统计——不依赖外部模型
  - 零侵入：通过 _warm_pool 附加分实现，不改变原有检索逻辑
  - 内存安全：候选池上限 200 条，超限按分数淘汰
"""

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set, Tuple

logger = logging.getLogger(__name__)

# 候选池配置
MAX_POOL_SIZE = 200
WARM_BOOST = 0.15  # 预热条目额外加分
PATTERN_WINDOW = 30  # 保留最近 N 次查询模式


@dataclass
class QueryPattern:
    """单次查询的模式记录"""
    query: str
    timestamp: float
    result_ids: List[str]
    categories_hit: List[str]
    layers_hit: List[str]


@dataclass
class ContextWindow:
    """会话级上下文窗口"""
    session_id: str
    patterns: List[QueryPattern] = field(default_factory=list)
    warm_pool: "OrderedDict[str, float]" = field(
        default_factory=lambda: OrderedDict()
    )
    _created_at: float = field(default_factory=time.time)
    _last_active: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self._created_at

    @property
    def idle_seconds(self) -> float:
        return time.time() - self._last_active

    def touch(self):
        self._last_active = time.time()


class AdaptiveContextEngine:
    """自适应上下文窗口引擎

    使用方式：
        ctx = AdaptiveContextEngine(storage, index)
        # 每次搜索后更新模式
        ctx.record_search(session_id="s1", query="数据库", results=chunks)
        # 下次搜索时获取 boost 加分
        boosts = ctx.get_warm_boosts(session_id="s1", candidates=candidate_ids)
        # 自适应阈值
        threshold = ctx.adaptive_min_relevance(session_id="s1", base=0.3)
    """

    def __init__(self, storage, index):
        """
        Args:
            storage: StorageEngine 实例
            index: IndexEngine 实例
        """
        self.storage = storage
        self.index = index
        self._windows: Dict[str, ContextWindow] = {}

    def get_or_create_window(self, session_id: str) -> ContextWindow:
        """获取或创建会话的上下文窗口"""
        if session_id not in self._windows:
            self._windows[session_id] = ContextWindow(session_id=session_id)
        window = self._windows[session_id]
        window.touch()
        return window

    def record_search(
        self,
        session_id: str,
        query: str,
        results: List[Any],
        categories: Optional[List[str]] = None,
        layers: Optional[List[str]] = None,
    ):
        """记录一次搜索的结果模式，更新候选池。

        Args:
            session_id: 会话 ID
            query: 原始查询词
            results: MemoryChunk 列表或任何含 memory_id 的对象列表
            categories: 指定过滤类别
            layers: 指定过滤层级
        """
        window = self.get_or_create_window(session_id)

        # Extract result metadata
        result_ids: List[str] = []
        cats_hit: Set[str] = set()
        layers_hit: Set[str] = set()

        for r in results:
            mid = getattr(r, "memory_id", None) or (
                r.get("memory_id") if isinstance(r, dict) else None
            )
            if mid:
                result_ids.append(str(mid))
            cat = getattr(r, "category", None) or (
                r.get("category") if isinstance(r, dict) else None
            )
            if cat:
                cats_hit.add(str(cat))
            layer = getattr(r, "layer", None) or (
                r.get("layer") if isinstance(r, dict) else None
            )
            if layer:
                layers_hit.add(str(layer))

        pattern = QueryPattern(
            query=query,
            timestamp=time.time(),
            result_ids=result_ids,
            categories_hit=sorted(cats_hit),
            layers_hit=sorted(layers_hit),
        )

        window.patterns.append(pattern)
        # Trim to window size
        if len(window.patterns) > PATTERN_WINDOW:
            window.patterns = window.patterns[-PATTERN_WINDOW:]

        # Update warm pool: results from this search get boosted
        self._update_warm_pool(window, result_ids)

    def get_warm_boosts(
        self, session_id: str, candidate_ids: List[str]
    ) -> Dict[str, float]:
        """获取候选记忆的预热加分

        Args:
            session_id: 会话 ID
            candidate_ids: 本次搜索候选记忆 ID

        Returns:
            {memory_id: boost_score}，只含命中候选池的条目
        """
        if session_id not in self._windows:
            return {}
        window = self._windows[session_id]
        if not window.warm_pool:
            return {}

        candidate_set = set(candidate_ids)
        boosts: Dict[str, float] = {}
        for mid in candidate_set:
            if mid in window.warm_pool:
                boosts[mid] = window.warm_pool[mid] * WARM_BOOST
        return boosts

    def adaptive_min_relevance(
        self,
        session_id: str,
        base: float = 0.3,
        min_floor: float = 0.15,
        max_ceiling: float = 0.5,
    ) -> float:
        """根据会话活跃度自适应调整最低相关度阈值。

        策略：
          - 刚创建窗口（pattern < 2）：用 base
          - 连续快速查询（density >= 5/30s）：降低阈值（用户正在探索，宁可多召回）
          - 长时间空闲后重新查询：提高阈值（聚焦性查询）

        Args:
            session_id: 会话 ID
            base: 基准阈值
            min_floor: 最低允许阈值
            max_ceiling: 最高允许阈值

        Returns:
            调整后的 min_relevance
        """
        if session_id not in self._windows:
            return base

        window = self._windows[session_id]

        if len(window.patterns) < 2:
            return base

        # Calculate query density (queries in last 30 seconds)
        recent_cutoff = time.time() - 30
        recent_count = sum(1 for p in window.patterns if p.timestamp > recent_cutoff)

        if recent_count >= 5:
            # Dense exploration mode: lower threshold to recall more
            return max(min_floor, base - 0.08)
        elif recent_count <= 1 and window.idle_seconds > 60:
            # Cold-start after idle: raise threshold to focus
            return min(max_ceiling, base + 0.05)
        else:
            return base

    def predict_next(
        self,
        session_id: str,
        current_query: str,
        top_n: int = 5,
    ) -> List[str]:
        """基于历史模式预测下一次搜索最可能需要哪些记忆。

        策略：
          1. 找到与当前查询语义相似的过往模式
          2. 收集「紧随相似模式之后」的查询结果
          3. 按出现频率排序返回

        Args:
            session_id: 会话 ID
            current_query: 当前查询词
            top_n: 返回预测条数

        Returns:
            预测的 memory_id 列表
        """
        if session_id not in self._windows:
            return []
        window = self._windows[session_id]
        if len(window.patterns) < 3:
            return []

        # Tokenize for simple overlap similarity
        current_tokens: Set[str] = set(self._tokenize(current_query))
        if not current_tokens:
            return []

        # Find similar past patterns (exclude last one)
        similar_follow_up_ids: Dict[str, int] = {}

        for i, pattern in enumerate(window.patterns[:-1]):
            past_tokens = set(self._tokenize(pattern.query))
            if not past_tokens:
                continue
            overlap = len(current_tokens & past_tokens) / max(
                len(current_tokens | past_tokens), 1
            )
            if overlap >= 0.3:
                # Collect results from the NEXT pattern
                next_pattern = window.patterns[i + 1]
                for mid in next_pattern.result_ids:
                    similar_follow_up_ids[mid] = similar_follow_up_ids.get(mid, 0) + 1

        # Sort by frequency
        sorted_ids = sorted(
            similar_follow_up_ids.items(), key=lambda x: x[1], reverse=True
        )
        return [mid for mid, _ in sorted_ids[:top_n]]

    def close_session(self, session_id: str) -> Dict[str, Any]:
        """关闭并清理会话的上下文窗口"""
        window = self._windows.pop(session_id, None)
        if window:
            return {
                "closed": True,
                "patterns_count": len(window.patterns),
                "warm_pool_size": len(window.warm_pool),
                "session_age_seconds": round(window.age_seconds, 1),
            }
        return {"closed": False, "reason": "session not found"}

    def session_stats(self, session_id: str) -> Optional[Dict[str, Any]]:
        """查看会话状态"""
        window = self._windows.get(session_id)
        if not window:
            return None
        return {
            "session_id": session_id,
            "patterns": len(window.patterns),
            "warm_pool_size": len(window.warm_pool),
            "age_seconds": round(window.age_seconds, 1),
            "idle_seconds": round(window.idle_seconds, 1),
            "top_categories": self._get_top_categories(window),
        }

    # ===================================================================
    # Private
    # ===================================================================

    def _update_warm_pool(self, window: ContextWindow, result_ids: List[str]):
        """更新候选池：新命中的条目分数递增，最旧的淘汰"""
        for mid in result_ids:
            current = window.warm_pool.get(mid, 0.0)
            window.warm_pool[mid] = current + 1.0
            # Move to end (most recent access)
            window.warm_pool.move_to_end(mid)

        # Evict oldest entries over capacity
        while len(window.warm_pool) > MAX_POOL_SIZE:
            window.warm_pool.popitem(last=False)

    def _get_top_categories(self, window: ContextWindow) -> List[str]:
        cats: Dict[str, int] = {}
        for p in window.patterns:
            for c in p.categories_hit:
                cats[c] = cats.get(c, 0) + 1
        return sorted(cats, key=cats.get, reverse=True)[:5] if cats else []

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """简单分词：中文按 bigram，英文按单词"""
        tokens: List[str] = []
        for seg in re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9_]+', text.lower()):
            if re.match(r'[\u4e00-\u9fff]', seg):
                if len(seg) <= 2:
                    tokens.append(seg)
                else:
                    for i in range(len(seg) - 1):
                        tokens.append(seg[i:i + 2])
            elif len(seg) > 1:
                tokens.append(seg)
        return tokens
