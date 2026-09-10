"""
MindForge v5.6.0 记忆衰减引擎

基于艾宾浩斯遗忘曲线的记忆生命周期管理：
  - 衰减评分：根据时间、重要性、访问频率计算记忆强度
  - 自动归档：强度低于阈值的记忆移入归档区
  - 过期清除：归档超过保留期的记忆永久删除
  - GC 周期：update → archive → purge 的完整编排

设计理念：
  真实记忆系统和"日志数据库"的分水岭就是会忘。
  长期不召回的记忆降权/归档，冲突记忆被新事实覆盖。
  旧上下文不污染新决策——Agent 长期运行的核心痛点。

和现有模块的关系：
  - evolution.py: ForgettingCurve 提供 Ebbinghaus 数学模型
  - storage.py: archived_memories 表 + archive_memories_by_ids
  - recall.py: 召回时更新 last_accessed_at，间接触发衰减重置
  - memory_decay.py: 编排层，将上述组件串联为 GC 周期
"""

import math
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

from core.types import MemoryLayer, Importance
from core.storage import StorageEngine, MemoryEntry


class DecayPolicy(Enum):
    """衰减策略"""
    CONSERVATIVE = "conservative"   # 少记多存，适合重要库
    BALANCED = "balanced"           # 默认平衡
    AGGRESSIVE = "aggressive"       # 快速遗忘，适合高频临时记忆


@dataclass
class DecayConfig:
    """衰减配置（v5.6.0 新增）

    三套预设策略 + 可微调参数。
    """
    policy: DecayPolicy = DecayPolicy.BALANCED

    # 衰减数学参数
    decay_rate: float = 0.05          # 每小时衰减率（越小越慢忘）
    review_boost: float = 0.3         # 每次召回的强度增益
    importance_multiplier: Dict[str, float] = field(default_factory=lambda: {
        "LOW": 0.7,
        "MEDIUM": 1.0,
        "HIGH": 1.5,
        "CRITICAL": 2.5,  # CRITICAL 几乎不衰减
    })

    # 归档阈值：强度低于此值 → 归档
    archive_threshold: float = 0.15

    # 永久删除：归档超过此天数 → 删除
    purge_after_days: int = 90

    # 保护规则
    protect_permanent: bool = True     # PERMANENT 层不衰减
    protect_critical: bool = True      # CRITICAL 重要性不衰减
    protect_recent_hours: float = 6.0  # 最近 N 小时内创建的不归档
    protect_starred: bool = True       # 收藏/starred 记忆不衰减
    protect_pinned: bool = True        # 置顶/pinned 记忆不衰减

    # 临时层额外清理
    sensory_max_age_hours: int = 48    # 感官层超过 N 小时 → 归档
    short_term_max_age_hours: int = 168  # 短期层超过 7 天 → 评估

    @classmethod
    def from_policy(cls, policy: DecayPolicy) -> "DecayConfig":
        """从预设策略创建配置"""
        if policy == DecayPolicy.CONSERVATIVE:
            return cls(
                policy=policy,
                decay_rate=0.02,
                review_boost=0.4,
                archive_threshold=0.08,
                purge_after_days=365,
                sensory_max_age_hours=168,
                short_term_max_age_hours=720,
            )
        elif policy == DecayPolicy.AGGRESSIVE:
            return cls(
                policy=policy,
                decay_rate=0.15,
                review_boost=0.2,
                archive_threshold=0.25,
                purge_after_days=30,
                sensory_max_age_hours=12,
                short_term_max_age_hours=48,
            )
        else:  # BALANCED
            return cls(policy=policy)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy": self.policy.value,
            "decay_rate": self.decay_rate,
            "review_boost": self.review_boost,
            "importance_multiplier": self.importance_multiplier,
            "archive_threshold": self.archive_threshold,
            "purge_after_days": self.purge_after_days,
            "protect_permanent": self.protect_permanent,
            "protect_critical": self.protect_critical,
            "protect_recent_hours": self.protect_recent_hours,
            "protect_starred": self.protect_starred,
            "protect_pinned": self.protect_pinned,
            "sensory_max_age_hours": self.sensory_max_age_hours,
            "short_term_max_age_hours": self.short_term_max_age_hours,
        }


class MemoryDecayEngine:
    """记忆衰减引擎

    编排完整的 GC 周期：
      1. compute_decay_scores() — 计算所有活跃记忆的当前强度
      2. archive_decayed() — 归档强度低于阈值的记忆
      3. purge_old_archives() — 永久删除过期的归档记忆
      4. cleanup_sensory() — 按时间清理超期感官/短期层
      5. run_gc() — 一键执行以上全部步骤
    """

    def __init__(self, storage: StorageEngine, config: Optional[DecayConfig] = None):
        self.storage = storage
        self.config = config or DecayConfig.from_policy(DecayPolicy.BALANCED)

    def compute_strength(self, entry: MemoryEntry) -> float:
        """计算单条记忆的当前强度

        基于 Ebbinghaus 指数衰减：
          strength = initial * exp(-adjusted_decay * elapsed_hours)

        修正因子：
          - 召回增强：每次召回提升 review_boost，访问频率越高衰减越慢
          - 重要性加权：CRITICAL 几乎不衰减，LOW 快速衰减
          - 巩固次数：每次巩固（层级提升）减慢后续衰减
        """
        now = time.time()
        elapsed_hours = max(0, (now - entry.last_accessed_at) / 3600)

        if elapsed_hours < 0.01:
            return entry.strength

        multiplier = self.config.importance_multiplier.get(
            entry.importance.value, 1.0
        )
        adjusted_decay = self.config.decay_rate / multiplier

        # 巩固系数：每巩固一次，衰减速率降 10%（最多降 50%）
        consolidation_factor = max(0.5, 1.0 - 0.1 * entry.consolidation_count)

        raw_strength = entry.strength * math.exp(
            -adjusted_decay * elapsed_hours * consolidation_factor
        )

        return max(0.01, min(1.0, raw_strength))

    def _is_protected(self, entry: MemoryEntry, now: float) -> bool:
        """判断记忆是否受保护（不参与归档）"""
        # PERMANENT 层保护
        if self.config.protect_permanent and entry.layer == MemoryLayer.PERMANENT:
            return True

        # CRITICAL 重要性保护
        if self.config.protect_critical and entry.importance == Importance.CRITICAL:
            return True

        # 收藏/置顶保护
        if self.config.protect_starred and getattr(entry, "starred", False):
            return True
        if self.config.protect_pinned and getattr(entry, "pinned", False):
            return True

        # 最近创建的记忆保护
        age_hours = (now - entry.created_at) / 3600
        if age_hours < self.config.protect_recent_hours:
            return True

        # 已在回收站的跳过
        if entry.category == "trash":
            return True

        return False

    def compute_decay_scores(self, limit: int = 10000) -> Dict[str, Any]:
        """扫描所有活跃记忆，计算衰减分数

        Returns:
            {
                "scanned": 总扫描数,
                "updated": 更新元数据数,
                "below_threshold": 低于归档阈值的记忆数,
                "by_layer": {sensory: N, short_term: N, ...},
                "avg_strength": 平均强度,
            }
        """
        now = time.time()
        entries = self.storage.list_memories(limit=limit)

        scanned = 0
        updated = 0
        below_threshold = 0
        total_strength = 0.0
        by_layer: Dict[str, int] = {}

        for entry in entries:
            scanned += 1
            layer_name = entry.layer.value if hasattr(entry.layer, 'value') else str(entry.layer)
            by_layer[layer_name] = by_layer.get(layer_name, 0) + 1

            strength = self.compute_strength(entry)
            total_strength += strength

            if strength < self.config.archive_threshold:
                below_threshold += 1

            # 更新元数据中的衰减信息
            if abs(strength - entry.strength) > 0.001:
                self.storage.update_memory(
                    entry_id=entry.id,
                    metadata={
                        **entry.metadata,
                        "current_strength": round(strength, 4),
                        "forgetting_score": round(1.0 - strength, 4),
                        "decay_updated_at": now,
                    }
                )
                updated += 1

        return {
            "scanned": scanned,
            "updated": updated,
            "below_threshold": below_threshold,
            "by_layer": by_layer,
            "avg_strength": round(total_strength / max(1, scanned), 4),
        }

    def archive_decayed(self, dry_run: bool = False,
                        actor: str = "gc") -> Dict[str, Any]:
        """归档强度低于阈值的记忆

        Args:
            dry_run: 只预览不执行
            actor: 操作者标识

        Returns:
            {
                "candidates": 候选归档数,
                "archived": 实际归档数,
                "protected": 受保护跳过数,
                "by_layer": {...},
                "sample_ids": [...],  # 前 10 个候选 ID
            }
        """
        now = time.time()
        entries = self.storage.list_memories(limit=10000)

        candidates: List[str] = []
        protected = 0
        by_layer: Dict[str, int] = {}

        for entry in entries:
            if self._is_protected(entry, now):
                protected += 1
                continue

            strength = self.compute_strength(entry)
            if strength < self.config.archive_threshold:
                candidates.append(entry.id)
                layer_name = entry.layer.value if hasattr(entry.layer, 'value') else str(entry.layer)
                by_layer[layer_name] = by_layer.get(layer_name, 0) + 1

        if dry_run:
            return {
                "candidates": len(candidates),
                "archived": 0,
                "protected": protected,
                "by_layer": by_layer,
                "sample_ids": candidates[:10],
                "dry_run": True,
            }

        archived = self.storage.archive_memories_by_ids(
            candidates, reason="decayed", actor=actor
        )

        return {
            "candidates": len(candidates),
            "archived": archived,
            "protected": protected,
            "by_layer": by_layer,
            "sample_ids": candidates[:10],
            "dry_run": False,
        }

    def purge_old_archives(self, dry_run: bool = False,
                           actor: str = "gc") -> Dict[str, Any]:
        """永久删除过期的归档记忆

        Args:
            dry_run: 只预览不执行
            actor: 操作者标识

        Returns:
            {"to_purge": 候选数, "purged": 实际删除数, "dry_run": bool}
        """
        if dry_run:
            archived = self.storage.list_archived(limit=10000)
            now = time.time()
            cutoff = now - (self.config.purge_after_days * 86400)
            count = sum(
                1 for a in archived
                if a.get("archived_at", 0) < cutoff
            )
            return {
                "to_purge": count,
                "purged": 0,
                "dry_run": True,
            }

        purged = self.storage.purge_archived(
            older_than_days=self.config.purge_after_days,
            actor=actor,
        )
        return {
            "to_purge": purged,
            "purged": purged,
            "dry_run": False,
        }

    def cleanup_temporary_layers(self, dry_run: bool = False,
                                  actor: str = "gc") -> Dict[str, Any]:
        """按时间清理超期的感官层/短期层记忆

        Args:
            dry_run: 只预览不执行
            actor: 操作者标识

        Returns:
            {"sensory_archived": N, "short_term_archived": N, "dry_run": bool}
        """
        results: Dict[str, Any] = {"dry_run": dry_run}

        for layer_name, max_hours in [
            ("sensory", self.config.sensory_max_age_hours),
            ("short_term", self.config.short_term_max_age_hours),
        ]:
            if dry_run:
                conn = self.storage._get_conn()
                cutoff = time.time() - (max_hours * 3600)
                rows = conn.execute(
                    "SELECT COUNT(*) as cnt FROM memories"
                    " WHERE layer = ? AND category != 'trash' AND created_at < ?",
                    (layer_name, cutoff)
                ).fetchone()
                count = rows["cnt"] if rows else 0
                results[f"{layer_name}_candidates"] = count
                results[f"{layer_name}_archived"] = 0
            else:
                result = self.storage.auto_archive(
                    max_age_hours=max_hours,
                    layer=layer_name,
                    actor=actor,
                )
                results[f"{layer_name}_archived"] = result.get("archived", 0)

        return results

    def run_gc(self, dry_run: bool = False,
               skip_purge: bool = False,
               actor: str = "gc") -> Dict[str, Any]:
        """执行完整 GC 周期

        顺序：
          1. compute_decay_scores — 更新衰减分数
          2. cleanup_temporary_layers — 按时间清理感官/短期层
          3. archive_decayed — 按强度归档
          4. purge_old_archives — 清除过期归档（可跳过）

        Args:
            dry_run: 只预览不执行
            skip_purge: 跳过永久删除步骤（保守模式）
            actor: 操作者标识

        Returns:
            完整 GC 报告
        """
        report: Dict[str, Any] = {
            "dry_run": dry_run,
            "started_at": time.time(),
            "policy": self.config.policy.value,
        }

        # Step 1: 衰减评分
        report["decay_scores"] = self.compute_decay_scores()

        # Step 2: 时间清理临时层
        report["temporary_cleanup"] = self.cleanup_temporary_layers(
            dry_run=dry_run, actor=actor
        )

        # Step 3: 强度归档
        report["archive_decayed"] = self.archive_decayed(
            dry_run=dry_run, actor=actor
        )

        # Step 4: 永久删除（可跳过）
        if skip_purge:
            report["purge_old_archives"] = {"skipped": True}
        else:
            report["purge_old_archives"] = self.purge_old_archives(
                dry_run=dry_run, actor=actor
            )

        report["finished_at"] = time.time()
        report["duration_ms"] = round(
            (report["finished_at"] - report["started_at"]) * 1000, 2
        )

        return report

    def gc_stats(self) -> Dict[str, Any]:
        """获取衰减/GC 统计概览"""
        stats = self.storage.get_stats()
        archived_count = len(self.storage.list_archived(limit=10000))

        # 计算当前衰减概况
        entries = self.storage.list_memories(limit=10000)
        now = time.time()

        strengths: List[float] = []
        below = 0
        protected = 0

        for entry in entries:
            if self._is_protected(entry, now):
                protected += 1
                continue
            s = self.compute_strength(entry)
            strengths.append(s)
            if s < self.config.archive_threshold:
                below += 1

        avg = sum(strengths) / max(1, len(strengths))

        return {
            "total_memories": stats.get("total", 0),
            "archived_memories": archived_count,
            "active_memories": len(strengths),
            "protected_memories": protected,
            "below_threshold": below,
            "avg_strength": round(avg, 4),
            "archive_threshold": self.config.archive_threshold,
            "purge_after_days": self.config.purge_after_days,
            "policy": self.config.policy.value,
            "by_layer": stats.get("by_layer", {}),
        }
