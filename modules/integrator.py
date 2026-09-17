"""
MindForge v5.0 记忆整合器
记忆摘要、时间线、关联整合
"""

import logging
import time
from dataclasses import dataclass
from typing import List, Dict, Optional
from collections import defaultdict

from core.storage import StorageEngine, MemoryEntry

logger = logging.getLogger(__name__)


@dataclass
class MemorySummary:
    """记忆摘要"""
    summary: str
    key_points: List[str]
    categories: Dict[str, int]
    time_span: str
    total_memories: int


@dataclass
class MemoryTimeline:
    """记忆时间线"""
    entries: List[MemoryEntry]
    time_periods: Dict[str, List[MemoryEntry]]


class MemoryIntegrator:
    """记忆整合器"""

    def __init__(self, storage: StorageEngine):
        self.storage = storage

    def generate_summary(self,
                          category: Optional[str] = None,
                          days: int = 30,
                          max_points: int = 10) -> MemorySummary:
        """生成记忆摘要"""
        entries = self.storage.list_memories(category=category, limit=500)

        now = time.time()
        cutoff = now - days * 86400

        recent = [e for e in entries if e.created_at >= cutoff]

        categories = defaultdict(int)
        for e in recent:
            categories[e.category] += 1

        sorted_entries = sorted(recent, key=lambda x: x.importance.to_int(), reverse=True)

        key_points = []
        for entry in sorted_entries[:max_points]:
            content = entry.content
            if entry.encrypted:
                try:
                    content = self.storage.decrypt_content(entry)
                except (ValueError, TypeError) as _dec_err:
                    logger.warning("整合记忆 %s 解密失败，跳过该条: %s",
                                   entry.id, _dec_err)
            preview = content[:80] + "..." if len(content) > 80 else content
            key_points.append(preview)

        if recent:
            oldest = min(e.created_at for e in recent)
            newest = max(e.created_at for e in recent)
            time_span = f"{self._format_date(oldest)} - {self._format_date(newest)}"
        else:
            time_span = "无数据"

        summary = f"过去 {days} 天共产生 {len(recent)} 条记忆，" \
                  f"涵盖 {len(categories)} 个分类。"

        if categories:
            top_cat = max(categories.items(), key=lambda x: x[1])
            summary += f"其中 {top_cat[0]} 类最多，有 {top_cat[1]} 条。"

        return MemorySummary(
            summary=summary,
            key_points=key_points,
            categories=dict(categories),
            time_span=time_span,
            total_memories=len(recent),
        )

    def generate_timeline(self,
                          category: Optional[str] = None,
                          limit: int = 100) -> MemoryTimeline:
        """生成时间线"""
        entries = self.storage.list_memories(category=category, limit=limit)
        entries.sort(key=lambda x: x.created_at, reverse=True)

        periods = defaultdict(list)
        for entry in entries:
            date_str = self._format_date(entry.created_at)
            periods[date_str].append(entry)

        return MemoryTimeline(
            entries=entries,
            time_periods=dict(periods),
        )

    def find_related_memories(self, memory_id: str,
                               max_related: int = 5) -> List[MemoryEntry]:
        """查找相关记忆"""
        target = self.storage.get_memory(memory_id)
        if not target:
            return []

        target_content = target.content
        if target.encrypted:
            try:
                target_content = self.storage.decrypt_content(target)
            except (ValueError, TypeError) as _dec_err:
                logger.warning("整合目标记忆 %s 解密失败，跳过: %s",
                               target.id, _dec_err)

        target_words = set(target_content.lower().split())

        all_entries = self.storage.list_memories(limit=200)
        scored = []

        for entry in all_entries:
            if entry.id == memory_id:
                continue

            content = entry.content
            if entry.encrypted:
                try:
                    content = self.storage.decrypt_content(entry)
                except (ValueError, TypeError) as _dec_err:
                    logger.warning("整合记忆 %s 解密失败，跳过该条: %s",
                                   entry.id, _dec_err)

            entry_words = set(content.lower().split())
            common = target_words & entry_words
            score = len(common) / max(len(target_words), 1)

            if entry.category == target.category:
                score += 0.2

            if score > 0.05:
                scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [e for e, _ in scored[:max_related]]

    def consolidate_daily(self, date_str: Optional[str] = None) -> Dict:
        """每日记忆整合"""
        if not date_str:
            date_str = self._format_date(time.time())

        entries = self.storage.list_memories(limit=500)
        day_entries = [
            e for e in entries
            if self._format_date(e.created_at) == date_str
        ]

        categories = defaultdict(int)
        tags = defaultdict(int)
        importance_dist = defaultdict(int)

        for entry in day_entries:
            categories[entry.category] += 1
            for tag in entry.tags:
                tags[tag] += 1
            importance_dist[entry.importance.value] += 1

        return {
            "date": date_str,
            "total_memories": len(day_entries),
            "by_category": dict(categories),
            "top_tags": sorted(tags.items(), key=lambda x: x[1], reverse=True)[:10],
            "by_importance": dict(importance_dist),
        }

    def get_memory_clusters(self, n_clusters: int = 5) -> Dict[str, List[str]]:
        """获取记忆聚类（简易版）"""
        entries = self.storage.list_memories(limit=200)
        clusters: Dict[str, List[str]] = defaultdict(list)

        for entry in entries:
            clusters[entry.category].append(entry.id)

        return dict(clusters)

    def _format_date(self, timestamp: float) -> str:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
