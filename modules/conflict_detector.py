"""
MindForge 矛盾检测 + 自动衰减引擎
========================================
三类检测：
  A. 直接反义对（词级别，如 启用 / 禁用）
  B. 属性值冲突（"价格是 X" vs "价格是 Y, X≠Y"）
  C. 时间线冲突（"已于周一完成" vs "预计周三完成"）

自动衰减策略：
  - 同主体冲突 → 老记忆重要性衰减、tag 标记 conflict
  - 显式 resolve 后，胜出记忆 +0.1 重要性，失败 -0.2
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 反义词典（内置常见技术/生活反义对）
# ---------------------------------------------------------------------------

ANTONYM_PAIRS: List[Tuple[str, str]] = [
    ("启用", "禁用"), ("开启", "关闭"), ("打开", "关闭"), ("启动", "停止"),
    ("允许", "禁止"), ("允许", "拒绝"), ("通过", "驳回"), ("成功", "失败"),
    ("新增", "删除"), ("添加", "移除"), ("增加", "减少"), ("上升", "下降"),
    ("提高", "降低"), ("支持", "反对"), ("兼容", "不兼容"), ("安全", "危险"),
    ("公开", "私有"), ("加密", "明文"), ("同步", "异步"), ("阻塞", "非阻塞"),
    ("真", "假"), ("是", "否"), ("对", "错"), ("正确", "错误"),
    ("稳定", "不稳定"), ("可用", "不可用"), ("正常", "异常"), ("存在", "不存在"),
    ("完成", "未完成"), ("已", "未"), ("可以", "不能"), ("应该", "不该"),
    # v5.4.7 修复：添加常见技术偏好反义对
    ("vim", "vscode"), ("vim", "vs code"), ("emacs", "vim"),
    ("python", "java"), ("javascript", "typescript"), ("react", "vue"),
    ("mac", "windows"), ("linux", "windows"), ("ios", "android"),
    ("git", "svn"), ("docker", "vm"), ("kubernetes", "docker swarm"),
    ("rest", "graphql"), ("sql", "nosql"), ("mongodb", "postgresql"),
]

# 属性抽取正则：<属性名> 是/为/=/: <值>
_ATTR_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9_\+\-\.\*#]{0,19})"
    r"\s*(?:是|为|=|等于|：|:)\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9_\-\+\.\*#/%@\$]{1,40})"
)

# v5.4.7 修复：添加偏好抽取正则，检测 "喜欢/偏好 X" 模式
_PREFERENCE_RE = re.compile(
    r"(?:喜欢|偏好|爱用|常用|主要用|习惯用)\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9_\-\+\.\*#/%@\$]{1,40})"
)

# 时间词
_TIME_WORDS = {
    "周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5, "周六": 6, "周日": 7, "周天": 7,
    "星期一": 1, "星期二": 2, "星期三": 3, "星期四": 4, "星期五": 5, "星期六": 6, "星期日": 7,
    "一月": 1, "二月": 2, "三月": 3, "四月": 4, "五月": 5, "六月": 6, "七月": 7,
    "八月": 8, "九月": 9, "十月": 10, "十一月": 11, "十二月": 12,
    "今天": 0, "明天": 1, "后天": 2, "昨天": -1, "前天": -2,
}
_STATUS_PATTERNS = [
    (re.compile(r"(已|已经|早就|完成了|做完了|搞定了)\s*[^\s，,。；;]{0,10}(完成|做完|上线|发布|提交|合并)"), "done"),
    (re.compile(r"(预计|计划|准备|将于|要|还没|尚未|未完成|待办|准备)\s*[^\s，,。；;]{0,10}(完成|上线|发布|提交|合并|做|弄)"), "pending"),
]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class ConflictPair:
    memory_id_a: str
    memory_id_b: str
    conflict_type: str       # antonym / attribute / timeline
    severity: float          # 0.0 ~ 1.0
    evidence: Dict[str, Any] = field(default_factory=dict)
    suggestion: str = ""
    resolution: str = "unresolved"   # unresolved / a_wins / b_wins / both_deprecated

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id_a": self.memory_id_a,
            "memory_id_b": self.memory_id_b,
            "conflict_type": self.conflict_type,
            "severity": round(self.severity, 3),
            "evidence": self.evidence,
            "suggestion": self.suggestion,
            "resolution": self.resolution,
        }


@dataclass
class DecayAction:
    memory_id: str
    delta_importance: float        # 例如 -0.15
    added_tags: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "delta_importance": round(self.delta_importance, 3),
            "added_tags": self.added_tags,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class ConflictDetector:
    """纯 CPU 实现的矛盾检测 + 自动衰减。无外部依赖。"""

    def __init__(self,
                 storage_or_antonym_pairs=None,
                 antonym_threshold: int = 1,
                 attr_match_min_length: int = 2,
                 auto_decay_old: float = 0.12,
                 auto_decay_resolve_loser: float = 0.20,
                 auto_boost_resolve_winner: float = 0.08):
        # v5.4.7 修复：兼容 README 示例中的 ConflictDetector(storage) 用法
        # 如果第一个参数是 StorageEngine 实例，则忽略它（当前版本不需要）
        # 如果是 list/tuple，则作为反义词对列表
        if storage_or_antonym_pairs is None:
            antonym_pairs = None
        elif isinstance(storage_or_antonym_pairs, (list, tuple)):
            antonym_pairs = storage_or_antonym_pairs
        else:
            # 假设是 storage 或其他对象，忽略
            antonym_pairs = None

        self.antonyms = list(antonym_pairs or ANTONYM_PAIRS)
        self.antonym_threshold = antonym_threshold
        self.attr_min_len = attr_match_min_length
        self.auto_decay_old = auto_decay_old
        self.auto_decay_loser = auto_decay_resolve_loser
        self.auto_boost_winner = auto_boost_resolve_winner

    # -- detector primitives -----------------------------------------------

    @staticmethod
    def _tokens(text: str) -> set:
        # 简单中文切词：按标点/空白 + 2-gram 组合 + 中文 2~4 字片段
        raw = re.sub(r"[\s，,。；;：:!！？?、()（）\[\]【】\"'《》\-—/\\]+", " ", text)
        parts = [p for p in raw.split() if p]
        bag = set(parts)
        for p in parts:
            if len(p) >= 2:
                for n in (2, 3):
                    for i in range(0, len(p) - n + 1):
                        bag.add(p[i:i + n])
        return bag

    def _antonym_hit(self, a: str, b: str) -> Tuple[int, List[str]]:
        count = 0
        pairs_hit: List[str] = []
        ta, tb = self._tokens(a), self._tokens(b)
        for x, y in self.antonyms:
            if (x in ta and y in tb) or (y in ta and x in tb):
                count += 1
                pairs_hit.append(f"{x}↔{y}")
        return count, pairs_hit

    def _extract_attrs(self, text: str) -> Dict[str, str]:
        attrs: Dict[str, str] = {}
        for m in _ATTR_RE.finditer(text):
            name, value = m.group(1).strip(), m.group(2).strip()
            if len(name) >= self.attr_min_len and len(value) >= 1:
                attrs[name] = value
        return attrs

    def _extract_preferences(self, text: str) -> List[str]:
        """v5.4.7 修复：抽取偏好项，如 '喜欢 Vim' -> ['vim']"""
        prefs = []
        for m in _PREFERENCE_RE.finditer(text):
            pref = m.group(1).strip().lower()
            if len(pref) >= 2:
                prefs.append(pref)
        return prefs

    def _timeline_status(self, text: str) -> Optional[str]:
        for pattern, status in _STATUS_PATTERNS:
            if pattern.search(text):
                return status
        return None

    # -- public API --------------------------------------------------------

    def detect_antonym(self, text_a: str, text_b: str,
                       id_a: str, id_b: str) -> Optional[ConflictPair]:
        cnt, pairs = self._antonym_hit(text_a, text_b)
        if cnt < self.antonym_threshold:
            return None
        severity = min(0.35 + cnt * 0.20, 1.0)
        return ConflictPair(
            memory_id_a=id_a, memory_id_b=id_b,
            conflict_type="antonym", severity=severity,
            evidence={"antonym_pairs": pairs, "hit_count": cnt},
            suggestion="反义词同现，建议人工核验以较新记忆或上下文一致者为准",
        )

    def detect_preference(self, text_a: str, text_b: str,
                          id_a: str, id_b: str) -> Optional[ConflictPair]:
        """v5.4.7 修复：检测偏好冲突，如 '喜欢 Vim' vs '喜欢 VS Code'"""
        prefs_a = self._extract_preferences(text_a)
        prefs_b = self._extract_preferences(text_b)
        if not prefs_a or not prefs_b:
            return None
        # 检查是否有不同的偏好项
        set_a, set_b = set(prefs_a), set(prefs_b)
        if set_a == set_b:
            return None  # 相同偏好，不冲突
        # 检查是否有交集（同一类别但不同选择）
        # 例如：都提到编辑器但选择不同
        conflicts = []
        for pa in set_a:
            for pb in set_b:
                if pa != pb:
                    # v5.4.8 P3-001 修复：收紧长度差阈值，添加额外启发式
                    # 1. 长度差 <= 2（更严格）
                    # 2. 首字母相同（同类事物的常见模式）
                    # 3. 一个包含另一个（如 vim/vimrc）
                    len_diff_ok = abs(len(pa) - len(pb)) <= 2
                    same_first_letter = pa[0] == pb[0] if pa and pb else False
                    substring_rel = pa in pb or pb in pa

                    if len_diff_ok or same_first_letter or substring_rel:
                        conflicts.append((pa, pb))
        if not conflicts:
            return None
        severity = min(0.40 + 0.10 * len(conflicts), 0.90)
        return ConflictPair(
            memory_id_a=id_a, memory_id_b=id_b,
            conflict_type="preference", severity=severity,
            evidence={"preference_conflicts": conflicts, "count": len(conflicts)},
            suggestion=f"偏好不一致：{', '.join(f'{a} vs {b}' for a, b in conflicts[:3])}",
        )

    def detect_attribute(self, text_a: str, text_b: str,
                         id_a: str, id_b: str) -> Optional[ConflictPair]:
        a_attrs = self._extract_attrs(text_a)
        b_attrs = self._extract_attrs(text_b)
        shared = [k for k in a_attrs if k in b_attrs and a_attrs[k] != b_attrs[k]]
        if not shared:
            return None
        conflicts = {k: (a_attrs[k], b_attrs[k]) for k in shared}
        severity = min(0.30 + 0.12 * len(shared), 0.95)
        return ConflictPair(
            memory_id_a=id_a, memory_id_b=id_b,
            conflict_type="attribute", severity=severity,
            evidence={"attribute_conflicts": conflicts, "count": len(shared)},
            suggestion=f"以下 {len(shared)} 个属性取值不一致：{', '.join(shared)}",
        )

    def detect_timeline(self, text_a: str, text_b: str,
                        id_a: str, id_b: str) -> Optional[ConflictPair]:
        sa, sb = self._timeline_status(text_a), self._timeline_status(text_b)
        if not sa or not sb or sa == sb:
            return None
        if {sa, sb} == {"done", "pending"}:
            severity = 0.65
            return ConflictPair(
                memory_id_a=id_a, memory_id_b=id_b,
                conflict_type="timeline", severity=severity,
                evidence={"status_a": sa, "status_b": sb},
                suggestion="同一件事出现『已完成』vs『待完成』矛盾，请以最近时间戳或显式更新者为准",
            )
        return None

    # -- batch scan --------------------------------------------------------

    def scan_memories(self,
                      memories: List[Dict[str, Any]],
                      max_pairs: int = 1000) -> List[ConflictPair]:
        """扫描一批记忆并返回所有冲突对。
        memories: 每一项必须至少含 id, content 字段，可选 created_at。
        """
        results: List[ConflictPair] = []
        n = len(memories)
        # 两两比较，O(n^2)，对大样本先按标签/关键词分桶以减少比较
        checked = 0
        for i in range(n):
            mi = memories[i]
            for j in range(i + 1, n):
                mj = memories[j]
                checked += 1
                if checked > max_pairs:
                    return results
                ta, tb = str(mi.get("content", "")), str(mj.get("content", ""))
                id_a, id_b = str(mi.get("id", i)), str(mj.get("id", j))
                for detector in (self.detect_antonym, self.detect_attribute, self.detect_timeline, self.detect_preference):
                    pair = detector(ta, tb, id_a, id_b)
                    if pair is not None:
                        results.append(pair)
        # 按严重度排序
        results.sort(key=lambda p: p.severity, reverse=True)
        return results

    # -- auto decay --------------------------------------------------------

    def plan_decay(self, conflicts: List[ConflictPair],
                   memories_by_id: Dict[str, Dict[str, Any]]) -> List[DecayAction]:
        """基于冲突结果生成衰减动作。未 resolve 的双方，老的那条减去 auto_decay_old，并打上 conflict 标签。"""
        actions: Dict[str, DecayAction] = {}

        def _get(mid: str) -> Dict[str, Any]:
            return memories_by_id.get(mid, {"id": mid, "created_at": None})

        for c in conflicts:
            a, b = c.memory_id_a, c.memory_id_b
            if c.resolution == "unresolved":
                # 谁老谁减
                ca_time = _get(a).get("created_at") or 0
                cb_time = _get(b).get("created_at") or 0
                if ca_time and cb_time:
                    older = a if ca_time < cb_time else b
                else:
                    older = a if len(a) < len(b) else b
                self._merge_action(actions, older, -self.auto_decay_old, ["conflict"],
                                   reason=f"与{('b' if older==a else 'a')}冲突类型={c.conflict_type}")
            elif c.resolution == "a_wins":
                self._merge_action(actions, a, +self.auto_boost_winner, [], reason=f"冲突胜出: {c.conflict_type}")
                self._merge_action(actions, b, -self.auto_decay_loser, ["conflict-deprecated"],
                                   reason=f"冲突失败: {c.conflict_type}")
            elif c.resolution == "b_wins":
                self._merge_action(actions, b, +self.auto_boost_winner, [], reason=f"冲突胜出: {c.conflict_type}")
                self._merge_action(actions, a, -self.auto_decay_loser, ["conflict-deprecated"],
                                   reason=f"冲突失败: {c.conflict_type}")
            elif c.resolution == "both_deprecated":
                self._merge_action(actions, a, -self.auto_decay_loser, ["conflict-deprecated"],
                                   reason=f"双方均弃用: {c.conflict_type}")
                self._merge_action(actions, b, -self.auto_decay_loser, ["conflict-deprecated"],
                                   reason=f"双方均弃用: {c.conflict_type}")
        return list(actions.values())

    @staticmethod
    def _merge_action(bucket: Dict[str, DecayAction], mid: str,
                      delta: float, tags: List[str], reason: str):
        if mid not in bucket:
            bucket[mid] = DecayAction(memory_id=mid, delta_importance=0.0, added_tags=[], reason="")
        a = bucket[mid]
        a.delta_importance += delta
        for t in tags:
            if t not in a.added_tags:
                a.added_tags.append(t)
        if a.reason:
            a.reason += " | " + reason
        else:
            a.reason = reason


__all__ = ["ConflictDetector", "ConflictPair", "DecayAction", "ANTONYM_PAIRS"]
