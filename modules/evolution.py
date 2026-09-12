"""
MindForge v5.0 记忆演化引擎
艾宾浩斯遗忘曲线 · 记忆巩固 · 记忆重组
"""

import math
import time
from dataclasses import dataclass, field
from typing import List, Dict, Tuple

from core.types import MemoryLayer, Importance
from core.storage import StorageEngine, MemoryEntry


@dataclass
class ForgettingCurve:
    """艾宾浩斯遗忘曲线参数"""
    initial_strength: float = 1.0
    decay_rate: float = 0.1
    review_boost: float = 0.3
    importance_multiplier: Dict[str, float] = field(default_factory=lambda: {
        "LOW": 0.7,
        "MEDIUM": 1.0,
        "HIGH": 1.5,
        "CRITICAL": 2.0,
    })

    def calculate_strength(self, initial_strength: float,
                           elapsed_hours: float,
                           importance: Importance = Importance.MEDIUM) -> float:
        """计算记忆强度（基于艾宾浩斯曲线）"""
        multiplier = self.importance_multiplier.get(importance.value, 1.0)
        adjusted_decay = self.decay_rate / multiplier
        strength = initial_strength * math.exp(-adjusted_decay * elapsed_hours)
        return max(0.01, min(1.0, strength))

    def next_review_time(self, current_strength: float,
                         target_strength: float = 0.6,
                         importance: Importance = Importance.MEDIUM) -> float:
        """计算下次复习时间（小时）"""
        if current_strength <= target_strength:
            return 0
        multiplier = self.importance_multiplier.get(importance.value, 1.0)
        adjusted_decay = self.decay_rate / multiplier
        hours = -math.log(target_strength / current_strength) / adjusted_decay
        return max(0, hours)


class MemoryEvolution:
    """记忆演化引擎"""

    def __init__(self, storage: StorageEngine):
        self.storage = storage
        self.forgetting_curve = ForgettingCurve()
        self._consolidation_threshold = 0.7
        self._permanent_threshold = 0.9
        self._short_term_capacity = 100

    def update_forgetting_scores(self):
        """更新所有记忆的遗忘分数

        v5.6.1 性能优化：从逐条 update_memory 改为 bulk_update_memory_fields，
        减少事务开销和不必要的 FTS/版本历史/审计处理。
        """
        all_memories = self.storage.list_memories(limit=10000)
        now = time.time()

        bulk_updates = []
        for entry in all_memories:
            elapsed_hours = (now - entry.last_accessed_at) / 3600
            if elapsed_hours < 1:
                continue

            new_strength = self.forgetting_curve.calculate_strength(
                entry.strength,
                elapsed_hours,
                entry.importance,
            )

            forgetting_score = 1.0 - new_strength

            new_metadata = {
                **entry.metadata,
                "current_strength": new_strength,
                "forgetting_score": forgetting_score,
            }
            bulk_updates.append({
                "id": entry.id,
                "strength": new_strength,
                "forgetting_score": forgetting_score,
                "metadata": new_metadata,
            })

        if bulk_updates:
            self.storage.bulk_update_memory_fields(
                bulk_updates,
                ["strength", "forgetting_score", "metadata"],
            )

    def consolidate(self, agent_id: str = "", session_id: str = "") -> Dict:
        """记忆巩固：将短期记忆转化为长期记忆"""
        short_term = self.storage.list_memories(
            layer=MemoryLayer.SHORT_TERM,
            limit=self._short_term_capacity,
        )

        consolidated = []
        promoted = 0
        demoted = 0

        now = time.time()

        for entry in short_term:
            elapsed_hours = (now - entry.created_at) / 3600
            strength = self.forgetting_curve.calculate_strength(
                entry.strength, elapsed_hours, entry.importance
            )

            access_factor = min(entry.access_count / 5.0, 1.0)
            importance_factor = entry.importance.to_int() / 3.0
            consolidation_score = (strength + access_factor + importance_factor) / 3.0

            if consolidation_score >= self._consolidation_threshold:
                # P1 修复：合并为单次 update_memory，避免双次非原子操作
                # （中途崩溃会导致 layer 已升级但 consolidation_count 未 +1）
                new_count = entry.consolidation_count + 1
                self.storage.update_memory(
                    entry_id=entry.id,
                    layer=MemoryLayer.LONG_TERM,
                    consolidation_count=new_count,
                    metadata={
                        **entry.metadata,
                        "consolidated_at": now,
                        "consolidation_score": consolidation_score,
                        "consolidation_count": new_count,
                    },
                    actor=agent_id,
                    session_id=session_id,
                )
                entry.consolidation_count = new_count
                promoted += 1
                consolidated.append(entry.id)
            elif strength < 0.2 and entry.importance == Importance.LOW:
                self.storage.update_memory(
                    entry_id=entry.id,
                    layer=MemoryLayer.SENSORY,
                    actor=agent_id,
                    session_id=session_id,
                )
                demoted += 1

        return {
            "processed": len(short_term),
            "promoted_to_long_term": promoted,
            "demoted_to_sensory": demoted,
            "consolidated_ids": consolidated,
        }

    def promote_to_permanent(self, memory_id: str,
                             agent_id: str = "", session_id: str = "") -> bool:
        """提升为永久记忆"""
        entry = self.storage.get_memory(memory_id, agent_id, session_id)
        if not entry:
            return False

        if entry.layer != MemoryLayer.LONG_TERM:
            return False

        if entry.consolidation_count < 3:
            return False

        self.storage.update_memory(
            entry_id=memory_id,
            layer=MemoryLayer.PERMANENT,
            metadata={**entry.metadata, "permanent_at": time.time()},
            actor=agent_id,
            session_id=session_id,
        )
        return True

    def review_schedule(self, limit: int = 20) -> List[Tuple[MemoryEntry, float]]:
        """生成复习计划"""
        all_memories = self.storage.list_memories(limit=1000)
        now = time.time()
        schedule = []

        for entry in all_memories:
            if entry.layer in (MemoryLayer.SENSORY, MemoryLayer.PERMANENT):
                continue

            elapsed_hours = (now - entry.last_accessed_at) / 3600
            current_strength = self.forgetting_curve.calculate_strength(
                entry.strength, elapsed_hours, entry.importance
            )

            next_review = self.forgetting_curve.next_review_time(
                current_strength, 0.6, entry.importance
            )

            urgency = 1.0 - current_strength
            if urgency > 0.3:
                schedule.append((entry, urgency))

        schedule.sort(key=lambda x: x[1], reverse=True)
        return schedule[:limit]

    def reactivate(self, memory_id: str, agent_id: str = "", session_id: str = "") -> bool:
        """重新激活记忆（复习后增强）"""
        entry = self.storage.get_memory(memory_id, agent_id, session_id)
        if not entry:
            return False

        boost = self.forgetting_curve.review_boost
        new_strength = min(1.0, entry.strength + boost)

        self.storage.update_memory(
            entry_id=memory_id,
            metadata={
                **entry.metadata,
                "strength": new_strength,
                "last_review_at": time.time(),
                "review_count": entry.metadata.get("review_count", 0) + 1,
            },
            actor=agent_id,
            session_id=session_id,
        )
        return True

    def get_evolution_stats(self) -> Dict:
        """获取演化统计"""
        stats = self.storage.get_stats()
        by_layer = stats.get("by_layer", {})

        return {
            "sensory": by_layer.get("sensory", 0),
            "short_term": by_layer.get("short_term", 0),
            "long_term": by_layer.get("long_term", 0),
            "permanent": by_layer.get("permanent", 0),
            "consolidation_rate": self._calc_consolidation_rate(),
        }

    def _calc_consolidation_rate(self) -> float:
        short_term = self.storage.count_memories(layer=MemoryLayer.SHORT_TERM)
        long_term = self.storage.count_memories(layer=MemoryLayer.LONG_TERM)
        total = short_term + long_term
        if total == 0:
            return 0.0
        return long_term / total
