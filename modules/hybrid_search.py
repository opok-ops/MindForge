"""
MindForge v5.3.9 混合检索增强管线
================================
补上原检索缺的两块：
  A. 查询扩展 Query Expansion：
     - 同义词扩展（内置中文常见技术/生活同义词 + 用户自定义词典）
     - 上位词泛化（如「MySQL 报错」→「数据库报错」）
     - 缩写还原（如「k8s」→「Kubernetes」）
     - 纠错回退（编辑距离 1 的常见错拼）

  B. Cross-Encoder 风格重排（Reranking）：
     - 纯 CPU 的多特征打分（query-token overlap / 关键短语命中 / n-gram overlap
       / 词距惩罚 / 类别与重要性先验），模拟 cross-encoder 的相关性判断
     - 与原 relevance_score 做加权融合，最终输出新排序
"""
from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


# ---------------------------------------------------------------------------
# 内置查询扩展词典（可通过 add_synonym / add_abbr 扩充）
# ---------------------------------------------------------------------------

DEFAULT_SYNONYMS: Dict[str, List[str]] = {
    "部署": ["上线", "发布", "发版"],
    "报错": ["错误", "异常", "失败", "崩溃"],
    "数据库": ["mysql", "postgresql", "pgsql", "db", "sqlite", "redis", "mongo", "mongodb"],
    "mysql": ["数据库", "mysql", "mysqldb"],
    "k8s": ["kubernetes", "kubectl", "容器", "容器化"],
    "容器": ["docker", "k8s", "kubernetes", "镜像"],
    "配置": ["conf", "config", "配置文件", "settings", "参数"],
    "启动": ["运行", "拉起", "start", "开机", "执行"],
    "停止": ["shutdown", "stop", "停掉", "终止", "结束"],
    "搜索": ["查找", "查询", "检索", "search", "find"],
    "缓存": ["cache", "redis", "memcached"],
    "接口": ["api", "接口调用", "端点", "endpoint"],
    "用户": ["user", "客户", "账号"],
    "密码": ["password", "口令", "pwd", "秘钥"],
    "日志": ["log", "日志文件", "访问日志"],
    "性能": ["速度", "慢", "耗时", "吞吐", "qps"],
}

DEFAULT_ABBR: Dict[str, str] = {
    "k8s": "Kubernetes",
    "db": "数据库",
    "api": "接口",
    "ai": "人工智能",
    "llm": "大语言模型",
    "kg": "知识图谱",
    "qa": "问答",
    "ui": "界面",
    "ux": "用户体验",
    "ci": "持续集成",
    "cd": "持续部署",
    "devops": "开发运维",
    "sdk": "开发工具包",
    "sql": "结构化查询语言",
    "cpu": "中央处理器",
    "gpu": "图形处理器",
    "nlp": "自然语言处理",
}

DEFAULT_HYPONYMS: Dict[str, List[str]] = {  # 具体 → 泛化
    "mysql": ["数据库", "关系型数据库"],
    "postgresql": ["数据库"],
    "redis": ["数据库", "缓存"],
    "mongodb": ["数据库", "文档数据库"],
    "k8s": ["容器编排", "云原生"],
    "docker": ["容器", "虚拟化"],
    "kubernetes": ["容器编排", "云原生"],
    "nginx": ["反向代理", "web服务器"],
    "fastapi": ["api框架", "web框架"],
    "flask": ["api框架", "web框架"],
    "django": ["api框架", "web框架"],
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class ExpansionResult:
    original: str
    expanded_terms: List[str] = field(default_factory=list)     # 扩展出的并列词
    rewrites: List[str] = field(default_factory=list)          # 重写后的查询（可直接拿去多路检索）
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"original": self.original,
                "expanded_terms": self.expanded_terms[:40],
                "rewrites": self.rewrites[:10],
                "note": self.note}


@dataclass
class RerankResult:
    memory_id: str
    original_score: float
    rerank_score: float
    fused_score: float
    rank_before: int
    rank_after: int
    features: Dict[str, float] = field(default_factory=dict)
    content: str = ""
    # v5.5.6 fix(P2-2): 保留候选元数据，供下游过滤/去重/展示
    importance: str = ""
    category: str = ""
    layer: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "original_score": round(self.original_score, 4),
            "rerank_score": round(self.rerank_score, 4),
            "fused_score": round(self.fused_score, 4),
            "rank_before": self.rank_before,
            "rank_after": self.rank_after,
            "delta_rank": self.rank_before - self.rank_after,
            "features": {k: round(v, 4) for k, v in self.features.items()},
            "content": self.content[:120],
            "importance": self.importance,
            "category": self.category,
            "layer": self.layer,
            "tags": self.tags,
        }


# ---------------------------------------------------------------------------
# Query Expansion
# ---------------------------------------------------------------------------

class QueryExpander:
    """同义词 + 缩写 + 上位词 + 可选编辑距离纠错"""

    def __init__(self,
                 synonyms: Optional[Dict[str, List[str]]] = None,
                 abbr: Optional[Dict[str, str]] = None,
                 hyponyms: Optional[Dict[str, List[str]]] = None,
                 max_rewrites: int = 6,
                 enable_typo: bool = True):
        self.synonyms: Dict[str, List[str]] = {}
        for k, vs in (synonyms or DEFAULT_SYNONYMS).items():
            self._add_multi(self.synonyms, k, vs)
        self.abbr = dict(abbr or DEFAULT_ABBR)
        self.hyponyms = dict(hyponyms or DEFAULT_HYPONYMS)
        self.max_rewrites = max_rewrites
        self.enable_typo = enable_typo

    @staticmethod
    def _add_multi(d: Dict[str, List[str]], k: str, vs: Iterable[str]) -> None:
        lc = k.lower()
        bucket = d.setdefault(lc, [])
        for v in vs:
            v_lc = v.lower()
            if v_lc not in bucket and v_lc != lc:
                bucket.append(v_lc)
        # 双向：把每个同义词也作为 key，反向扩展回原词
        for v in vs:
            v_lc = v.lower()
            reverse = d.setdefault(v_lc, [])
            if lc not in reverse and lc != v_lc:
                reverse.append(lc)

    def add_synonym(self, term: str, synonyms: Sequence[str]) -> None:
        self._add_multi(self.synonyms, term, synonyms)

    def add_abbr(self, abbr: str, full: str) -> None:
        self.abbr[abbr.lower()] = full

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        raw = re.sub(r"[\W_]+", " ", text.lower()).split()
        # 中文逐字不做，这里仅按空白+符号分词；中文再切 2-gram
        bag: List[str] = []
        for token in raw:
            if not token:
                continue
            bag.append(token)
            if re.search(r"[\u4e00-\u9fa5]", token) and len(token) >= 2:
                for n in (2, 3):
                    for i in range(len(token) - n + 1):
                        bag.append(token[i:i + n])
        return bag

    # -- typo tolerance (edit distance 1) ----------------------------------

    @staticmethod
    def _edit_dist1(a: str, b: str) -> bool:
        """Check if two strings have edit distance <= 1. O(min(len(a), len(b)))"""
        la, lb = len(a), len(b)
        if abs(la - lb) > 1:
            return False
        # Ensure a is the shorter one
        if la > lb:
            a, b = b, a
            la, lb = lb, la
        # Same length: check for 0 or 1 substitution
        if la == lb:
            diff = 0
            for ca, cb in zip(a, b):
                if ca != cb:
                    diff += 1
                    if diff > 1:
                        return False
            return True
        # Length diff 1: check for 1 insertion/deletion
        i = j = 0
        diff = 0
        while i < la and j < lb:
            if a[i] == b[j]:
                i += 1
                j += 1
            else:
                diff += 1
                if diff > 1:
                    return False
                j += 1  # skip one char in longer string
        return True

    def _typo_candidates(self, tokens: Sequence[str], max_tokens: int = 5) -> Dict[str, List[str]]:
        """把已知同义词/缩写词典作为 vocabulary，寻找编辑距离 1 的替换。

        v5.4.8 P1-001 修复：改用反向查找（遍历 vocab 检查编辑距离），
        而不是生成所有 edits（O(L*A) per word）。
        添加 max_tokens 限制防止长查询导致性能崩溃。
        """
        vocab: Set[str] = set(self.synonyms) | set(self.abbr) | set(self.hyponyms)
        if not vocab:
            return {}

        out: Dict[str, List[str]] = {}
        checked = 0
        for t in tokens:
            if checked >= max_tokens:
                break
            if len(t) < 3 or not re.fullmatch(r"[a-z0-9_]+", t):
                continue
            if t in vocab:
                continue
            # 反向查找：遍历 vocab 检查编辑距离
            candidates = [v for v in vocab if self._edit_dist1(t, v)]
            if candidates:
                out[t] = candidates[:3]
            checked += 1
        return out

    # -- main expand -------------------------------------------------------

    def expand(self, query: str, hyponym: bool = True,
               typo: Optional[bool] = None) -> ExpansionResult:
        q = (query or "").strip()
        tokens = self._tokenize(q)
        seen_terms: Set[str] = set(tokens)
        expanded: List[str] = []

        def _add(t: str) -> None:
            if not t:
                return
            if t not in seen_terms:
                seen_terms.add(t)
                expanded.append(t)

        # 1) 缩写还原
        for t in tokens:
            if t in self.abbr:
                _add(self.abbr[t].lower())

        # 2) 同义词
        for t in tokens:
            if t in self.synonyms:
                for s in self.synonyms[t]:
                    _add(s)

        # 3) 上位词泛化
        if hyponym:
            for t in tokens:
                if t in self.hyponyms:
                    for g in self.hyponyms[t]:
                        _add(g.lower())

        # 4) 纠错（把 query 中可能的 typo token 指向候选词）
        typo_note = ""
        if (typo if typo is not None else self.enable_typo):
            suggestions = self._typo_candidates(tokens)
            if suggestions:
                for bad, goods in suggestions.items():
                    for g in goods:
                        _add(g)
                typo_parts = [f"{b}→{'/'.join(g)}" for b, g in suggestions.items()]
                typo_note = "typo 候选: " + ", ".join(typo_parts)

        # 生成 rewrite：原句 + 同义词替换版本（逐词替换一次）
        rewrites: List[str] = [q]
        lowered_tokens = re.split(r"(\s+)", q)
        for idx, chunk in enumerate(lowered_tokens):
            lc = chunk.lower()
            if lc in self.synonyms:
                for s in self.synonyms[lc][:2]:
                    new_parts = lowered_tokens.copy()
                    # v5.6.8 修复：原写法 `s if chunk.isupper() else s` 两个分支相同，
                    # 大小写保留逻辑形同虚设（全大写原词会被替换成小写同义词）。
                    new_parts[idx] = s.upper() if chunk.isupper() else s
                    rewrites.append("".join(new_parts))
            if lc in self.abbr:
                new_parts = lowered_tokens.copy()
                new_parts[idx] = self.abbr[lc]
                rewrites.append("".join(new_parts))
        # 去重保序 + 截断
        dedup: List[str] = []
        for r in rewrites:
            if r not in dedup:
                dedup.append(r)
            if len(dedup) >= self.max_rewrites:
                break
        return ExpansionResult(
            original=q,
            expanded_terms=expanded,
            rewrites=dedup,
            note=typo_note,
        )


# ---------------------------------------------------------------------------
# Cross-Encoder Style Reranker (纯 CPU, 多特征融合)
# ---------------------------------------------------------------------------

@dataclass
class RerankWeights:
    original: float = 0.35
    token_overlap: float = 0.22
    phrase_hit: float = 0.20
    ngram_overlap: float = 0.13
    proximity: float = 0.05
    importance_bonus: float = 0.03
    category_bonus: float = 0.02
    layer_bonus: float = 0.01
    length_norm: float = 0.10

    def total(self) -> float:
        return (self.original + self.token_overlap + self.phrase_hit +
                self.ngram_overlap + self.proximity +
                self.importance_bonus + self.category_bonus +
                self.layer_bonus)


class CrossEncoderReranker:
    """无外部依赖的相关性重排：融合 query 感知的多特征打分"""

    def __init__(self, weights: Optional[RerankWeights] = None):
        self.weights = weights or RerankWeights()

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"[\W_]+", " ", (text or "").lower())

    @staticmethod
    def _tokens(text: str) -> List[str]:
        s = re.sub(r"[\W_]+", " ", text.lower())
        return [t for t in s.split() if t]

    @staticmethod
    def _char_ngrams(text: str, n: int = 2) -> List[str]:
        s = re.sub(r"[\s]+", "", text.lower())
        if len(s) < n:
            return [s] if s else []
        return [s[i:i + n] for i in range(len(s) - n + 1)]

    def _score_token_overlap(self, q_tokens: Sequence[str], d_tokens: Sequence[str]) -> float:
        if not q_tokens or not d_tokens:
            return 0.0
        qs, ds = set(q_tokens), set(d_tokens)
        inter = len(qs & ds)
        union = len(qs | ds)
        jaccard = inter / max(1, union)
        # recall 部分：query 词命中比例
        recall = sum(1 for t in q_tokens if t in ds) / max(1, len(q_tokens))
        return 0.6 * recall + 0.4 * jaccard

    def _score_phrase_hit(self, query: str, doc: str) -> float:
        if not query or not doc:
            return 0.0
        q = self._norm(query)
        d = self._norm(doc)
        if not q:
            return 0.0
        # 3+ 长度的 token：若在文档里出现算一个强命中
        long_tokens = [t for t in self._tokens(q) if len(t) >= 3]
        hits = 0
        for t in long_tokens:
            if t in d:
                hits += 1
        # 完整子串命中（2-gram以上）
        substring_hits = 0
        for n in (3, 4):
            for i in range(len(q) - n + 1):
                chunk = q[i:i + n]
                if chunk.isspace():
                    continue
                if chunk in d:
                    substring_hits += 1
        ratio1 = hits / max(1, len(long_tokens))
        ratio2 = min(1.0, substring_hits / max(1, len(q) / 3.0))
        return 0.5 * ratio1 + 0.5 * ratio2

    def _score_ngram_overlap(self, query: str, doc: str) -> float:
        q = set(self._char_ngrams(query, 2)) | set(self._char_ngrams(query, 3))
        d = set(self._char_ngrams(doc, 2)) | set(self._char_ngrams(doc, 3))
        if not q or not d:
            return 0.0
        return len(q & d) / max(1, len(q | d))

    def _score_proximity(self, q_tokens: Sequence[str], doc: str) -> float:
        """query 词在文档中首次出现位置的跨度越小分越高。"""
        if not q_tokens or not doc:
            return 0.0
        d_lower = doc.lower()
        positions: List[int] = []
        for t in q_tokens:
            idx = d_lower.find(t)
            if idx >= 0:
                positions.append(idx)
        if len(positions) < 2:
            return 0.0 if len(positions) == 0 else 0.5
        span = max(positions) - min(positions)
        # span 越小越好；按 doc 长度归一
        norm = 1.0 - min(1.0, span / max(1.0, len(doc)))
        return norm

    def _score_metadata(self, doc_meta: Dict[str, Any], query_meta: Dict[str, Any]) -> Tuple[float, float, float]:
        """(importance_bonus, category_bonus, layer_bonus)"""
        importance_score = 0.0
        imp = str(doc_meta.get("importance", "")).upper()
        if imp == "CRITICAL":
            importance_score = 1.0
        elif imp == "HIGH":
            importance_score = 0.75
        elif imp == "MEDIUM":
            importance_score = 0.45
        elif imp == "LOW":
            importance_score = 0.15
        category_score = 0.0
        cat = str(doc_meta.get("category", "")).lower()
        if cat and cat == str(query_meta.get("category", "")).lower():
            category_score = 1.0
        layer_score = 0.0
        layer = str(doc_meta.get("layer", "")).lower()
        if layer == "permanent":
            layer_score = 1.0
        elif layer == "long_term":
            layer_score = 0.8
        elif layer == "short_term":
            layer_score = 0.4
        return importance_score, category_score, layer_score

    # -- main rerank -------------------------------------------------------

    def rerank(self,
               query: str,
               candidates: Sequence,  # v5.4.7 修复 H-5：接受 dict 或 dataclass
               query_category: Optional[str] = None,
               top_k: int = 20) -> List[RerankResult]:
        """candidates 每项需含: memory_id, content, original_score；
        可含 importance/category/layer 元数据。
        v5.4.7 修复 H-5：支持 dict 和 dataclass (MemoryChunk) 两种输入。"""
        q_norm = self._norm(query)
        q_tokens = self._tokens(query)

        def _get_field(c, field, default=""):
            """v5.4.7 修复 H-5：兼容 dict 和 dataclass 访问"""
            if isinstance(c, dict):
                return c.get(field, default)
            return getattr(c, field, default)

        scored: List[RerankResult] = []
        for rank_before, c in enumerate(candidates, 1):
            content = str(_get_field(c, "content", ""))
            d_tokens = self._tokens(content)
            meta = {
                "importance": _get_field(c, "importance"),
                "category": _get_field(c, "category"),
                "layer": _get_field(c, "layer"),
            }
            q_meta = {"category": query_category or ""}

            f_token = self._score_token_overlap(q_tokens, d_tokens)
            f_phrase = self._score_phrase_hit(q_norm, self._norm(content))
            f_ngram = self._score_ngram_overlap(q_norm, content)
            f_prox = self._score_proximity(q_tokens, content)
            f_imp, f_cat, f_layer = self._score_metadata(meta, q_meta)

            original_score = float(_get_field(c, "original_score", _get_field(c, "score", 0.0)))
            # 归一化 original_score 到 0-1（若原分 >1 先压缩）
            orig_norm = 1.0 - math.exp(-max(0.0, original_score))
            rerank_raw = (
                self.weights.token_overlap * f_token +
                self.weights.phrase_hit * f_phrase +
                self.weights.ngram_overlap * f_ngram +
                self.weights.proximity * f_prox +
                self.weights.importance_bonus * f_imp +
                self.weights.category_bonus * f_cat +
                self.weights.layer_bonus * f_layer
            )
            rerank_denom = (
                self.weights.token_overlap + self.weights.phrase_hit +
                self.weights.ngram_overlap + self.weights.proximity +
                self.weights.importance_bonus + self.weights.category_bonus +
                self.weights.layer_bonus
            ) or 1.0
            rerank_score = rerank_raw / rerank_denom
            # 长度惩罚：内容过短（<10 字）给 0.9 折扣
            length_penalty = 1.0
            if len(content.strip()) < 10:
                length_penalty = 0.9
            fused_score = (
                self.weights.original * orig_norm +
                (1.0 - self.weights.original) * rerank_score
            ) * length_penalty

            scored.append(RerankResult(
                memory_id=str(c.get("memory_id", c.get("id", ""))),
                original_score=original_score,
                rerank_score=rerank_score,
                fused_score=fused_score,
                rank_before=rank_before,
                rank_after=0,
                features={
                    "token_overlap": f_token,
                    "phrase_hit": f_phrase,
                    "ngram_overlap": f_ngram,
                    "proximity": f_prox,
                    "importance": f_imp,
                    "category_match": f_cat,
                    "layer_bonus": f_layer,
                    "length_penalty": length_penalty,
                },
                content=content,
                # v5.5.6 fix(P2-2): 透传候选元数据
                importance=str(_get_field(c, "importance", "") or ""),
                category=str(_get_field(c, "category", "") or ""),
                layer=str(_get_field(c, "layer", "") or ""),
                tags=list(_get_field(c, "tags", []) or []),
            ))
        # 按 fused_score 排序
        scored.sort(key=lambda x: -x.fused_score)
        for rank_after, r in enumerate(scored, 1):
            r.rank_after = rank_after
        return scored[: max(1, int(top_k))]


__all__ = [
    "QueryExpander", "ExpansionResult",
    "CrossEncoderReranker", "RerankResult", "RerankWeights",
]
