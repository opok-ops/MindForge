"""
MindForge 意图分类路由引擎
================================
三层路由：规则正则 → 关键词加权 → （可选）LLM 兜底
支持 10+ 种业务意图，带置信度与降级路径。
"""
from __future__ import annotations

import re
import time
import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 内置意图定义
# ---------------------------------------------------------------------------

INTENT_DEFS: Dict[str, Dict[str, Any]] = {
    "memory_store": {
        "label": "记忆存储",
        "description": "用户要求记住、保存、记录某事",
        "examples": ["记住这件事", "帮我保存", "记录一下", "别忘了这个", "写入记忆"],
        "keywords_pos": ["记住", "保存", "记录", "别忘了", "写入", "记下", "存起来", "备忘"],
        "keywords_neg": [],
        "patterns": [r"(记|保存|记录|忘?记)住|存(起来|储)?|备?忘"],
        "priority": 85,
    },
    "memory_recall": {
        "label": "记忆检索",
        "description": "用户查询历史、搜索记忆、回忆某事",
        "examples": ["我以前说过什么", "帮我找一下", "搜索记忆", "上次提到", "回忆一下"],
        "keywords_pos": ["搜索", "查找", "找一下", "回忆", "以前", "上次", "历史", "检索", "提过"],
        "keywords_neg": ["怎么", "为什么", "如何", "教程", "方案"],
        "patterns": [r"(搜索|查找|找|回忆|检索)(记忆|记录|历史|一下)?"],
        "priority": 80,
    },
    "question_answer": {
        "label": "知识问答",
        "description": "用户提出事实类/方法类问题",
        "examples": ["这是什么意思", "怎么实现", "为什么会这样", "解释一下", "原理是什么"],
        "keywords_pos": ["为什么", "怎么", "如何", "什么", "解释", "原理", "意思", "能否"],
        "keywords_neg": [],
        "patterns": [r"(为什么|怎么|如何|是?什么|解释|原理)(样|呢|吧|啊|吗|一下)?$"],
        "priority": 70,
    },
    "task_planning": {
        "label": "任务规划",
        "description": "制定计划、拆解步骤、排期安排",
        "examples": ["我该怎么开始", "制定计划", "分几步", "排期", "下一步做什么"],
        "keywords_pos": ["计划", "步骤", "排期", "安排", "下一步", "拆解", "流程", "方案", "todo", "待办"],
        "keywords_neg": [],
        "patterns": [r"(计划|步骤|排期|安排|todo|待办|下一步|怎么(做|开始|办))"],
        "priority": 75,
    },
    "drama_creation": {
        "label": "短剧创作",
        "description": "短剧/剧本/角色/台词创作相关",
        "examples": ["写个短剧", "生成剧本", "加一段台词", "设计角色", "剧情怎么走"],
        "keywords_pos": ["短剧", "剧本", "台词", "角色", "剧情", "剧集", "场景", "写戏", "拍剧"],
        "keywords_neg": [],
        "patterns": [r"(短剧|剧本|台词|角色|剧情|剧集|场景)(创作|设计|生成|写|加|分析)?"],
        "priority": 90,
    },
    "agent_control": {
        "label": "Agent 控制",
        "description": "控制 Agent 行为、切换人格、记忆管理",
        "examples": ["切换模式", "换个人格", "清空记忆", "导出数据", "停止分析"],
        "keywords_pos": ["切换", "人格", "模式", "清空", "删除", "导出", "导入", "备份", "停止", "重启"],
        "keywords_neg": [],
        "patterns": [r"(切换|清空|删除|导出|导入|备份|重启|停止|设置|改)(模式|人格|记忆|数据|配置)?"],
        "priority": 88,
    },
    "emotion_support": {
        "label": "情感支持",
        "description": "用户表达情绪、倾诉、寻求安慰",
        "examples": ["我好难过", "太烦了", "压力大", "不开心", "求安慰"],
        "keywords_pos": ["难过", "烦", "压力", "不开心", "伤心", "生气", "累", "焦虑", "安慰", "鼓励"],
        "keywords_neg": ["怎么", "为什么", "解决"],
        "patterns": [r"(好?(难|烦|累|伤)?(过|心|人)|(焦|担)虑|(求|需?要)?安慰|鼓励|委屈|崩溃|抑?郁)"],
        "priority": 82,
    },
    "code_development": {
        "label": "代码开发",
        "description": "写代码、调试、重构、review",
        "examples": ["写一个Python脚本", "帮我debug", "优化这段代码", "解释这个函数", "重构一下"],
        "keywords_pos": ["代码", "函数", "类", "debug", "重构", "优化", "bug", "报错", "python", "java", "script"],
        "keywords_neg": [],
        "patterns": [r"(写|生成|优?化?|重构|debug|修|解释|review).*(代码|函数|类|脚本|bug|报错|方法)"],
        "priority": 78,
    },
    "information_extract": {
        "label": "信息抽取",
        "description": "从文本中抽取实体、关系、结构",
        "examples": ["从这段提取关键词", "总结要点", "列出人物", "抽取参数"],
        "keywords_pos": ["抽取", "提取", "总结", "要点", "实体", "关键词", "标签", "结构化", "摘要"],
        "keywords_neg": [],
        "patterns": [r"(抽取|提取|总结|要点|实体|关键词|标签|结构化|摘要|列(出|取)?)"],
        "priority": 76,
    },
    "creative_writing": {
        "label": "创意写作",
        "description": "文案、故事、诗、营销稿创作",
        "examples": ["写首诗", "帮我写个文案", "写个故事", "润色一下", "slogan"],
        "keywords_pos": ["文案", "诗", "故事", "润色", "slogan", "标题", "软文", "广告语", "小说"],
        "keywords_neg": [],
        "patterns": [r"(写|创作|润色|生成|构思|起).*(文案|诗|故事|slogan|标题|软文|广告|小说|散文)"],
        "priority": 72,
    },
    "chit_chat": {
        "label": "闲聊对话",
        "description": "日常寒暄、问候、无明确任务意图",
        "examples": ["你好", "在吗", "哈哈", "天气真好", "谢谢"],
        "keywords_pos": ["你好", "在吗", "嗨", "哈哈", "谢谢", "再见", "早", "晚安", "天气"],
        "keywords_neg": [],
        "patterns": [r"^(你好|嗨|hi|hello|在?吗|哈哈|嘿嘿|谢谢|感谢|再见|晚安|早|好啊)$"],
        "priority": 50,
    },
}

DEFAULT_INTENT = "chit_chat"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class IntentResult:
    intent: str
    label: str
    confidence: float                 # 0.0 ~ 1.0
    routing: str                      # 建议的路由目标
    matched_rules: List[str] = field(default_factory=list)
    keyword_hits: Dict[str, int] = field(default_factory=dict)
    candidates: List[Tuple[str, float]] = field(default_factory=list)
    latency_ms: float = 0.0
    fallback: bool = False            # 是否走了 LLM/默认兜底

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "routing": self.routing,
            # 路由层级：0=规则正则  1=关键词加权  2=LLM 路由  3=兜底
            # v5.5.8 修复：原表达式 `2 if self.fallback else 2` 两支相同，
            # 导致兜底与正常 LLM 路由无法区分，level 分级失效。
            "level": 0 if self.matched_rules else (1 if self.keyword_hits else (3 if self.fallback else 2)),
            "matched_rules": self.matched_rules,
            "keyword_hits": self.keyword_hits,
            "top_keywords_hits": [(k, w) for k, w in list(self.keyword_hits.items())[:5]],
            "top_candidates": self.candidates[:5],
            "latency_ms": round(self.latency_ms, 2),
            "fallback": self.fallback,
        }


# ---------------------------------------------------------------------------
# 路由引擎
# ---------------------------------------------------------------------------

class IntentRouter:
    """三层意图路由引擎"""

    def __init__(self,
                 llm_classifier: Optional[Callable[[str], List[Tuple[str, float]]]] = None,
                 custom_intents: Optional[Dict[str, Dict[str, Any]]] = None,
                 min_confidence: float = 0.35,
                 cache_size: int = 4096):
        self.intents = dict(INTENT_DEFS)
        if custom_intents:
            self.intents.update(custom_intents)
        self.llm_classifier = llm_classifier
        self.min_confidence = min_confidence
        self._cache: Dict[str, Tuple[str, float, List[Tuple[str, float]]]] = {}
        self._cache_size = cache_size
        self._compiled_patterns: Dict[str, List[re.Pattern]] = {
            name: [re.compile(p, re.IGNORECASE) for p in d.get("patterns", [])]
            for name, d in self.intents.items()
        }

    # -- scoring primitives -------------------------------------------------

    def _keyword_score(self, text: str, intent: str) -> Tuple[float, Dict[str, int]]:
        d = self.intents[intent]
        hits: Dict[str, int] = {}
        pos = d.get("keywords_pos", [])
        neg = d.get("keywords_neg", [])
        score = 0.0
        for kw in pos:
            if kw and kw in text:
                cnt = text.count(kw)
                hits[kw] = cnt
                score += cnt * 0.12
        for kw in neg:
            if kw and kw in text:
                score -= 0.18
        return min(score, 0.95), hits

    def _pattern_score(self, text: str, intent: str) -> Tuple[float, List[str]]:
        matched: List[str] = []
        score = 0.0
        for p in self._compiled_patterns.get(intent, []):
            m = p.search(text)
            if m:
                matched.append(m.group(0))
                score += 0.22
        return min(score, 0.95), matched

    def _length_and_shape_bonus(self, text: str, intent: str) -> float:
        """简单的形状启发式：问题多含问号、命令较短、闲聊很短。"""
        t = text.strip()
        if not t:
            return 0.0
        bonus = 0.0
        if intent == "question_answer" and ("?" in t or "？" in t):
            bonus += 0.15
        if intent == "chit_chat" and len(t) <= 8:
            bonus += 0.2
        if intent == "agent_control" and t.endswith(("一下", "吧", "啊")):
            bonus += 0.05
        if intent == "creative_writing" and len(t) >= 6:
            bonus += 0.05
        return bonus

    # -- classify -----------------------------------------------------------

    def classify(self, text: str, force_override: Optional[str] = None) -> IntentResult:
        t0 = time.perf_counter()
        src = (text or "").strip()

        if force_override and force_override in self.intents:
            d = self.intents[force_override]
            return IntentResult(
                intent=force_override, label=d["label"],
                confidence=0.99, routing=force_override,
                matched_rules=["force_override"], latency_ms=0.1, fallback=False,
            )

        # cache key (ignore whitespace)
        cache_key = hashlib.sha256(re.sub(r"\s+", "", src).encode("utf-8")).hexdigest()  # P1-007: MD5→SHA-256
        if cache_key in self._cache:
            intent, conf, cands = self._cache[cache_key]
            return IntentResult(
                intent=intent, label=self.intents[intent]["label"],
                confidence=conf, routing=intent, candidates=cands,
                latency_ms=(time.perf_counter() - t0) * 1000,
                matched_rules=["cache"], fallback=False,
            )

        scores: List[Tuple[str, float, Dict[str, int], List[str]]] = []
        for name in self.intents:
            kw_s, kw_hits = self._keyword_score(src, name)
            pat_s, pat_matched = self._pattern_score(src, name)
            shape_bonus = self._length_and_shape_bonus(src, name)
            priority = self.intents[name].get("priority", 50) / 1000.0
            total = kw_s + pat_s + shape_bonus + priority
            scores.append((name, total, kw_hits, pat_matched))

        scores.sort(key=lambda x: x[1], reverse=True)
        top_name, top_score, top_kw, top_pat = scores[0]
        candidates = [(n, round(s, 4)) for n, s, _, _ in scores[:5]]
        fallback = False

        # LLM 兜底（若配置且置信度低于阈值或与第二名差距小）
        # v5.4.8 P2-003 修复：添加最低 LLM 置信度阈值，防止低质量 LLM 结果被接受
        llm_accepted = False
        min_llm_confidence = 0.5  # LLM 结果必须达到此阈值才被接受

        if top_score < self.min_confidence and self.llm_classifier is not None:
            try:
                llm_scores = self.llm_classifier(src) or []
                if llm_scores:
                    llm_scores.sort(key=lambda x: x[1], reverse=True)
                    first = llm_scores[0]
                    # 验证：意图存在 + 分数高于规则 + 分数达到最低阈值
                    if (first[0] in self.intents and
                        first[1] > top_score and
                        first[1] >= min_llm_confidence):
                        top_name, top_score = first[0], first[1]
                        candidates = [(n, round(s, 4)) for n, s in llm_scores[:5]]
                        fallback = True
                        llm_accepted = True
            except Exception as _llm_err:
                logger.warning("LLM 意图识别失败，回退启发式路由: %s", _llm_err)

        # 二次兜底：默认意图（仅在 LLM 未接受时）
        if top_score < self.min_confidence and not llm_accepted:
            top_name = DEFAULT_INTENT
            top_score = max(top_score, 0.25)
            fallback = True

        latency = (time.perf_counter() - t0) * 1000
        result = IntentResult(
            intent=top_name,
            label=self.intents[top_name]["label"],
            confidence=min(max(top_score, 0.0), 1.0),
            routing=top_name,
            matched_rules=list(dict.fromkeys(top_pat))[:6],
            keyword_hits=top_kw,
            candidates=candidates,
            latency_ms=latency,
            fallback=fallback,
        )

        # cache 写入（简单 LRU：超出就清空一半）
        if len(self._cache) >= self._cache_size:
            keys = list(self._cache.keys())[:self._cache_size // 2]
            for k in keys:
                self._cache.pop(k, None)
        self._cache[cache_key] = (top_name, result.confidence, candidates)
        return result

    # v5.4.7 修复：添加 classify_intent 别名，与 README 文档保持一致
    def classify_intent(self, text: str, force_override: Optional[str] = None) -> IntentResult:
        """classify() 的别名方法，与 README 文档中的 API 名称保持一致。"""
        return self.classify(text, force_override)

    # -- batch --------------------------------------------------------------

    def classify_batch(self, texts: List[str]) -> List[IntentResult]:
        return [self.classify(t) for t in texts]

    def add_intent(self, name: str, definition: Dict[str, Any]) -> None:
        if not isinstance(definition, dict):
            raise ValueError("definition must be dict")
        self.intents[name] = definition
        self._compiled_patterns[name] = [
            re.compile(p, re.IGNORECASE) for p in definition.get("patterns", [])
        ]

    def routing_target(self, intent: str) -> str:
        """给出该意图对应的下一步路由目标（handler 名 / API endpoint）。"""
        mapping = {
            "memory_store": "memory.add",
            "memory_recall": "memory.search",
            "question_answer": "llm.answer",
            "task_planning": "agent.plan",
            "drama_creation": "drama.compose",
            "agent_control": "agent.control",
            "emotion_support": "agent.empathy",
            "code_development": "llm.code",
            "information_extract": "ie.extract",
            "creative_writing": "llm.write",
            "chit_chat": "agent.chat",
        }
        return mapping.get(intent, f"handler.{intent}")


__all__ = ["IntentRouter", "IntentResult", "INTENT_DEFS", "DEFAULT_INTENT"]
