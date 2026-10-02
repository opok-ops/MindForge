"""
MindForge 记忆 → 技能转化引擎
====================================
核心想法：一组「解决某问题」的记忆，可以被抽象成可复用的技能模板：
    Skill = {name, description, triggers, slots, steps, examples, tags}

抽取流程：
  1. 候选聚类：按标签 + 关键词 overlap 把记忆分桶（同一技能 ≈ 主题簇）
  2. 槽位识别：在每条记忆里找出 {{参数}} / <参数> / 变量名模式
  3. 步骤提炼：按「先/然后/最后/接下来/步骤 1~n」等连接词 + 序号模式抽取步骤序列
  4. 触发词归纳：出现频次 ≥ min_hits 的关键词/标签 即为触发词
  5. 示例采样：从簇中挑最多 k 条作为 skill 的 examples
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 模式定义
# ---------------------------------------------------------------------------

_SLOT_PATTERNS = [
    re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]{0,29})\s*\}\}"),
    re.compile(r"<\s*([A-Za-z_][A-Za-z0-9_]{0,29})\s*>"),
    re.compile(r"(\$[A-Z_][A-Z0-9_]{0,19})"),
    re.compile(r"[「『](变量|参数|占位|入参)?\s*([A-Za-z_][A-Za-z0-9_\u4e00-\u9fa5]{1,15})\s*[」』]"),
]

_STEP_PATTERNS = [
    (re.compile(r"(?:步骤|第)\s*([0-9一二三四五六七八九十]{1,4})\s*[、步：:\.]\s*([^\n，,。；;]{2,120})"), "numbered"),
    (re.compile(r"(首先|第一步|先|一开始)[:：\s]*([^\n，,。；;]{2,120})"), "first"),
    (re.compile(r"(然后|接着|接下来|下一步|第二步)[:：\s]*([^\n，,。；;]{2,120})"), "next"),
    (re.compile(r"(最后|最终|完成后|收尾|第三步)[:：\s]*([^\n，,。；;]{2,120})"), "last"),
    (re.compile(r"(注意|提示|警告|重要)[:：\s]*([^\n，,。；;]{2,120})"), "note"),
]

_CMD_VERBS = ["安装", "配置", "启动", "停止", "运行", "执行", "调用", "发送", "请求",
              "打开", "访问", "下载", "上传", "生成", "创建", "删除", "修改", "更新",
              "导出", "导入", "备份", "恢复", "部署", "测试", "验证", "构建", "打包"]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class SkillSlot:
    name: str
    examples: List[str] = field(default_factory=list)
    required: bool = False
    default: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "examples": self.examples[:5],
                "required": self.required, "default": self.default}


@dataclass
class SkillTemplate:
    name: str
    description: str
    triggers: List[str] = field(default_factory=list)
    slots: List[SkillSlot] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    source_memory_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    cluster_size: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers[:20],
            "slots": [s.to_dict() for s in self.slots],
            "steps": self.steps[:30],
            "examples": self.examples[:5],
            "tags": self.tags[:20],
            "source_memory_ids": self.source_memory_ids[:100],
            "confidence": round(self.confidence, 3),
            "cluster_size": self.cluster_size,
        }

    def render(self, **kwargs) -> str:
        """把技能模板用用户提供的 slot 值填回去，得到可执行流程文本。"""
        mapping = {s.name: kwargs.get(s.name, s.default or f"{{{{{s.name}}}}}")
                   for s in self.slots}
        lines = [f"# 技能：{self.name}"]
        if self.description:
            lines.append(f"> {self.description}")
        lines.append("")
        if self.steps:
            lines.append("## 步骤")
            for i, step in enumerate(self.steps, 1):
                action = str(step.get("action", ""))
                for k, v in mapping.items():
                    action = re.sub(rf"(\{{{{\s*{re.escape(k)}\s*\}}}}|<\s*{re.escape(k)}\s*>)", str(v), action)
                lines.append(f"{i}. {action}")
        if self.examples:
            lines.append("")
            lines.append("## 示例")
            for ex in self.examples:
                lines.append(f"- {ex}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class SkillExtractor:
    """无外部依赖的记忆→技能抽取引擎。"""

    def __init__(self,
                 min_cluster_size: int = 2,
                 max_skills: int = 50,
                 overlap_threshold: float = 0.35,  # v5.4.8 P3-002: 从 0.20 提高到 0.35
                 min_trigger_hits: int = 1,
                 max_step_per_skill: int = 25):
        self.min_cluster_size = max(1, int(min_cluster_size))
        self.max_skills = max_skills
        self.base_threshold = overlap_threshold
        self.overlap_threshold = overlap_threshold
        self.min_trigger_hits = min_trigger_hits
        self.max_step = max_step_per_skill

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _ngrams(text: str, n: int = 2) -> Set[str]:
        s = re.sub(r"[\W_]+", "", text)
        if len(s) < n:
            return {s} if s else set()
        return {s[i:i + n] for i in range(len(s) - n + 1)}

    @staticmethod
    def _tags_of(mem: Dict[str, Any]) -> List[str]:
        raw = mem.get("tags") or []
        if isinstance(raw, str):
            return [t.strip() for t in re.split(r"[,，#\s]+", raw) if t.strip()]
        return [str(t).strip() for t in raw if str(t).strip()]

    def _cluster(self, memories: List[Dict[str, Any]]) -> List[List[int]]:
        n = len(memories)
        parent = list(range(n))

        # v5.4.8 P3-002 修复：自适应阈值，防止大数据集下的链式合并
        # 数据集越大，阈值越高，避免不相关的记忆通过中间节点连接
        import math
        adaptive_threshold = min(
            self.base_threshold + 0.05 * math.log(max(n, 1)),
            0.60  # 上限 0.60
        )
        threshold = max(self.base_threshold, adaptive_threshold)

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        feats: List[Tuple[Set[str], Set[str]]] = []
        for m in memories:
            content = str(m.get("content", ""))
            feats.append((self._ngrams(content, 2), set(self._tags_of(m))))

        for i in range(n):
            ai, ti = feats[i]
            for j in range(i + 1, n):
                aj, tj = feats[j]
                overlap_n = 0.0
                if ai and aj:
                    overlap_n = len(ai & aj) / max(1, min(len(ai), len(aj)))
                overlap_t = 0.0
                if ti and tj:
                    overlap_t = len(ti & tj) / max(1, min(len(ti), len(tj)))
                score = max(overlap_n, overlap_t)
                # 若标签完全相同直接合并
                if ti and tj and ti == tj:
                    score = max(score, 0.85)
                if score >= threshold:
                    union(i, j)

        buckets: Dict[int, List[int]] = {}
        for i in range(n):
            buckets.setdefault(find(i), []).append(i)
        return [b for b in buckets.values() if len(b) >= self.min_cluster_size]

    # -- extractors --------------------------------------------------------

    def _extract_slots(self, texts: List[str]) -> List[SkillSlot]:
        slot_hits: Dict[str, Set[str]] = {}
        for t in texts:
            for pat in _SLOT_PATTERNS:
                for m in pat.finditer(t):
                    name = m.group(1) if pat.groups == 1 else m.group(m.lastindex or 1)
                    # group count 不同，统一兜底
                    try:
                        if m.lastindex and m.lastindex >= 1:
                            name = m.group(m.lastindex)
                    except Exception:
                        pass
                    if not name or len(name) < 1:
                        continue
                    slot_hits.setdefault(name, set()).add(t[:60])
        out = []
        for name, exs in slot_hits.items():
            out.append(SkillSlot(name=name, examples=list(exs), required=False))
        out.sort(key=lambda s: -len(s.examples))
        return out

    def _extract_steps(self, texts: List[str]) -> List[Dict[str, Any]]:
        steps: List[Tuple[int, str, str]] = []  # (order_key, action, kind)
        for t in texts:
            for pat, kind in _STEP_PATTERNS:
                for m in pat.finditer(t):
                    raw_order = m.group(1)
                    action = m.group(2).strip(" 　\t")
                    order_key = 50
                    if kind == "first":
                        order_key = 10
                    elif kind == "next":
                        order_key = 30
                    elif kind == "last":
                        order_key = 70
                    elif kind == "note":
                        order_key = 90
                    elif kind == "numbered":
                        # 中文数字/阿拉伯数字 转 int，超出范围给个中值
                        try:
                            if raw_order.isdigit():
                                order_key = 10 + int(raw_order) * 2
                            else:
                                cn = "一二三四五六七八九十"
                                order_key = 10 + (cn.index(raw_order) + 1) * 2
                        except Exception:
                            order_key = 20
                    steps.append((order_key, action, kind))
        # 去重 + 排序 + 截断
        seen = set()
        out = []
        for key, action, kind in sorted(steps, key=lambda x: x[0]):
            sig = action[:40]
            if sig in seen:
                continue
            seen.add(sig)
            out.append({"order": key, "action": action, "kind": kind})
            if len(out) >= self.max_step:
                break
        return out

    def _collect_triggers(self, memories_in_cluster: List[Dict[str, Any]],
                          texts: List[str]) -> List[str]:
        freq: Dict[str, int] = {}
        # 标签
        for m in memories_in_cluster:
            for t in self._tags_of(m):
                freq[t] = freq.get(t, 0) + 1
        # 命令动词
        joined = "\n".join(texts)
        for v in _CMD_VERBS:
            c = joined.count(v)
            if c > 0:
                freq[v] = freq.get(v, 0) + c
        # 2-gram 中高并集频（简化：取频次高的前若干）
        items = [(k, v) for k, v in freq.items() if v >= self.min_trigger_hits]
        items.sort(key=lambda kv: (-kv[1], kv[0]))
        return [k for k, _ in items[:20]]

    @staticmethod
    def _summarize_name(memories: List[Dict[str, Any]]) -> Tuple[str, str]:
        tags: Set[str] = set()
        first_lines: List[str] = []
        for m in memories:
            for t in SkillExtractor._tags_of(m):
                tags.add(t)
            c = str(m.get("content", ""))
            first_line = re.split(r"[\n，,。；;]", c, maxsplit=1)[0][:40].strip()
            if first_line:
                first_lines.append(first_line)
        tags_list = sorted(tags)
        name_prefix = "_".join(tags_list[:2]) if tags_list else "skill"
        name = f"{name_prefix}_{len(memories)}mem".replace(" ", "_")
        desc_parts = first_lines[:2]
        description = "；".join(desc_parts) if desc_parts else f"从 {len(memories)} 条记忆归纳的通用流程"
        return name, description

    # -- main API ----------------------------------------------------------

    def extract(self, memories: List[Dict[str, Any]]) -> List[SkillTemplate]:
        """从一组记忆中批量抽取技能。"""
        clusters = self._cluster(memories)
        # 优先大簇
        clusters.sort(key=lambda c: -len(c))
        skills: List[SkillTemplate] = []
        for cl in clusters[: self.max_skills]:
            cl_mems = [memories[i] for i in cl]
            texts = [str(m.get("content", "")) for m in cl_mems]
            ids = [str(m.get("id", i)) for i, m in zip(cl, cl_mems)]
            name, desc = self._summarize_name(cl_mems)
            slots = self._extract_slots(texts)
            steps = self._extract_steps(texts)
            triggers = self._collect_triggers(cl_mems, texts)
            confidence = 0.0
            if triggers:
                confidence += min(len(triggers) * 0.04, 0.35)
            if slots:
                confidence += min(len(slots) * 0.06, 0.25)
            if steps:
                confidence += min(len(steps) * 0.04, 0.30)
            if len(cl) >= 4:
                confidence += 0.10
            examples = [t for t in texts if len(t) >= 8][:5]
            # tags 汇总
            tag_set: Set[str] = set()
            for m in cl_mems:
                for t in self._tags_of(m):
                    tag_set.add(t)
            skills.append(SkillTemplate(
                name=name, description=desc,
                triggers=triggers, slots=slots, steps=steps,
                examples=examples, tags=sorted(tag_set),
                source_memory_ids=ids,
                confidence=min(confidence, 1.0),
                cluster_size=len(cl),
            ))
        skills.sort(key=lambda s: (-s.confidence, -s.cluster_size))
        return skills


__all__ = ["SkillExtractor", "SkillTemplate", "SkillSlot"]
