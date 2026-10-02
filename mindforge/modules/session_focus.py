"""
MindForge 会话焦点增强引擎
================================
输入：一个会话消息序列（每条 {id, role, content, timestamp}）
输出：
  - 按时间滚动的主题簇（Topic Cluster）
  - 会话焦点摘要（当前 top-K 主题 + 代表词）
  - 焦点漂移检测（与上一窗口相比主题变化率）
  - 焦点增强查询（把当前主题关键词拼接到原始搜索 query，提升召回）

实现：纯文本特征，滑动窗口 + token / 2-gram 并集频率做简单 k-means(k 自动)
无外部依赖。
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# 停用词（中英常见功能词 / 代词）
# ---------------------------------------------------------------------------

STOPWORDS = set("""
的 了 和 是 就 都 而 及 与 或 一个 一些 这 那 这个 那个 这些 那些 我 你 他 她 它
我们 你们 他们 自己 什么 怎么 如何 为什么 可以 已经 将 要 会 能 有 在 于 对 从
到 给 把 被 让 向 比 又 还 只 也 很 更 最 不 没 没有 不是 吧 吗 呢 啊 哦 嗯
the a an and or but if then of in on at to for with from by is are was were be been
being have has had do does did i you he she it we they them my your our their this that
these those what which who how why when where so not no just only also can will would should
could may might about into out up down over under again further then once here there all any
each few more most other some such than too very
""".split())


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class TopicCluster:
    cluster_id: int
    keywords: List[Tuple[str, float]]       # [(词, 权重)]
    message_ids: List[str] = field(default_factory=list)
    representative: str = ""                # 代表消息的正文（最长、关键词密度最高）
    size: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "size": self.size,
            "top_keywords": self.keywords[:12],
            "message_ids": self.message_ids[:200],
            "representative": self.representative[:160],
        }


@dataclass
class FocusSummary:
    window_start_ts: float
    window_end_ts: float
    clusters: List[TopicCluster] = field(default_factory=list)
    drift_score: float = 0.0                 # 0.0 ~ 1.0
    drift_indicators: List[str] = field(default_factory=list)
    focus_keywords: List[Tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window": {
                "start": self.window_start_ts,
                "end": self.window_end_ts,
                "duration": round(self.window_end_ts - self.window_start_ts, 2),
            },
            "clusters": [c.to_dict() for c in self.clusters],
            "drift_score": round(self.drift_score, 3),
            "drift_indicators": self.drift_indicators,
            "focus_keywords": self.focus_keywords[:20],
        }

    def enhance_query(self, query: str, top_k: int = 5,
                      boost_mode: str = "append") -> str:
        """把焦点关键词拼接到查询。boost_mode: append / replace_empty / weighted_string"""
        if not query:
            return " ".join(w for w, _ in self.focus_keywords[:top_k])
        kws = [w for w, s in self.focus_keywords[:top_k] if w and w not in query.lower()]
        if not kws:
            return query
        if boost_mode == "replace_empty":
            return query
        # 默认 append：保留原 query 并在后面加「关键词」
        extra = " ".join(kws)
        return f"{query} {extra}"


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class SessionFocus:
    """无外部依赖的会话焦点聚类 + 漂移检测 + 查询增强"""

    def __init__(self,
                 max_messages_per_window: int = 50,
                 min_cluster_size: int = 2,
                 max_clusters: int = 8,
                 similarity_threshold: float = 0.25,
                 top_n_keywords: int = 10):
        self.max_w = max_messages_per_window
        self.min_cluster = min_cluster_size
        self.max_clusters = max_clusters
        self.sim_thresh = similarity_threshold
        self.top_n = top_n_keywords
        self._prev_keywords: Counter = Counter()

    # -- feature extraction ------------------------------------------------

    @staticmethod
    def _text(msg: Dict[str, Any]) -> str:
        role = str(msg.get("role", "user"))
        content = str(msg.get("content", ""))
        return f"{role}\n{content}"

    @classmethod
    def _tokens(cls, text: str) -> List[str]:
        cleaned = re.sub(r"[\W_]+", " ", text.lower()).split()
        bag: List[str] = []
        for t in cleaned:
            if not t or t in STOPWORDS or len(t) <= 1:
                continue
            bag.append(t)
        # 中文 2-gram（字符级）
        cn_seg = "".join(cleaned)
        if re.search(r"[\u4e00-\u9fa5]", cn_seg):
            for n in (2, 3):
                for i in range(len(cn_seg) - n + 1):
                    chunk = cn_seg[i:i + n]
                    if re.search(r"[\u4e00-\u9fa5]", chunk) and chunk not in STOPWORDS:
                        bag.append(chunk)
        return bag

    @classmethod
    def _tfidf_features(cls, texts: Sequence[str]) -> Tuple[List[Counter], Counter]:
        tfs: List[Counter] = []
        df: Counter = Counter()
        for txt in texts:
            tokens = cls._tokens(txt)
            tf = Counter(tokens)
            tfs.append(tf)
            for w in set(tf):
                df[w] += 1
        return tfs, df

    @staticmethod
    def _cosine(a: Counter, b: Counter) -> float:
        if not a or not b:
            return 0.0
        shared = set(a) & set(b)
        dot = sum(a[w] * b[w] for w in shared)
        if dot == 0:
            return 0.0
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / max(1e-9, na * nb)

    # -- simple greedy clustering -----------------------------------------

    def _cluster(self, tfs: Sequence[Counter],
                 ids: Sequence[str], texts: Sequence[str]) -> List[TopicCluster]:
        n = len(tfs)
        centers: List[int] = []       # 中心消息的索引
        center_to_cluster: Dict[int, int] = {}
        assignments = [-1] * n

        for i in range(n):
            best_idx, best_sim = -1, 0.0
            for c_idx, cent in enumerate(centers):
                sim = self._cosine(tfs[i], tfs[cent])
                if sim > best_sim:
                    best_sim, best_idx = sim, c_idx
            if best_idx >= 0 and best_sim >= self.sim_thresh:
                assignments[i] = best_idx
            elif len(centers) < self.max_clusters:
                assignments[i] = len(centers)
                center_to_cluster[i] = assignments[i]
                centers.append(i)
            else:
                # 兜底归给最相似的
                assignments[i] = best_idx if best_idx >= 0 else 0

        # 构建簇
        clusters_by_id: Dict[int, TopicCluster] = {}
        for i, cid in enumerate(assignments):
            if cid < 0:
                continue
            cl = clusters_by_id.setdefault(cid, TopicCluster(
                cluster_id=cid, keywords=[], message_ids=[], representative="", size=0,
            ))
            cl.message_ids.append(ids[i])
            cl.size += 1
            if len(texts[i]) > len(cl.representative):
                cl.representative = texts[i]

        # 计算每个簇的关键词（簇内tf-idf之和）
        _, df = self._tfidf_features(texts)
        N = max(1, len(texts))
        clusters: List[TopicCluster] = []
        for cid, cl in clusters_by_id.items():
            if cl.size < self.min_cluster:
                continue
            cluster_tf: Counter = Counter()
            for i in range(n):
                if assignments[i] == cid:
                    cluster_tf.update(tfs[i])
            scored = []
            for w, f in cluster_tf.items():
                idf = math.log((1 + N) / (1 + df.get(w, 0)) + 1.0)
                scored.append((w, float(f) * idf))
            scored.sort(key=lambda x: -x[1])
            cl.keywords = scored[: self.top_n]
            clusters.append(cl)
        clusters.sort(key=lambda c: -c.size)
        return clusters

    # -- drift detection ---------------------------------------------------

    def _detect_drift(self, current_keywords: Counter,
                      prev_keywords: Counter) -> Tuple[float, List[str]]:
        if not prev_keywords or not current_keywords:
            return 0.0, []
        top_curr = set(w for w, _ in current_keywords.most_common(15))
        top_prev = set(w for w, _ in prev_keywords.most_common(15))
        new_keys = top_curr - top_prev
        gone_keys = top_prev - top_curr
        shared = top_curr & top_prev
        union = top_curr | top_prev
        ratio = 1.0 - (len(shared) / max(1.0, len(union)))
        indicators: List[str] = []
        if new_keys:
            indicators.append("新增主题词: " + ", ".join(sorted(new_keys)[:6]))
        if gone_keys:
            indicators.append("淡出主题词: " + ", ".join(sorted(gone_keys)[:6]))
        return ratio, indicators

    # -- main API ----------------------------------------------------------

    def summarize(self, messages: Sequence[Dict[str, Any]],
                  window_size: Optional[int] = None) -> FocusSummary:
        window_size = window_size or self.max_w
        msgs = list(messages)[-window_size:]
        if not msgs:
            return FocusSummary(
                window_start_ts=0.0, window_end_ts=0.0,
                clusters=[], drift_score=0.0, drift_indicators=[], focus_keywords=[],
            )
        texts = [self._text(m) for m in msgs]
        ids = [str(m.get("id", i)) for i, m in enumerate(msgs)]

        def _ts(m: Dict[str, Any]) -> float:
            t = m.get("timestamp") or m.get("ts") or m.get("time")
            if isinstance(t, (int, float)):
                return float(t)
            return 0.0

        start_ts = min((_ts(m) for m in msgs), default=0.0)
        end_ts = max((_ts(m) for m in msgs), default=time.time())
        tfs, _ = self._tfidf_features(texts)
        clusters = self._cluster(tfs, ids, texts)

        # 当前窗口焦点关键词（tf-idf）
        global_tf = Counter()
        for tf in tfs:
            global_tf.update(tf)
        _, df_all = self._tfidf_features(texts)
        N = len(texts)
        scored_all = []
        for w, f in global_tf.items():
            idf = math.log((1 + N) / (1 + df_all.get(w, 0)) + 1.0)
            scored_all.append((w, float(f) * idf))
        scored_all.sort(key=lambda x: -x[1])
        focus_keywords = scored_all[: self.top_n]

        drift, indicators = self._detect_drift(global_tf, self._prev_keywords)
        summary = FocusSummary(
            window_start_ts=start_ts,
            window_end_ts=end_ts,
            clusters=clusters,
            drift_score=drift,
            drift_indicators=indicators,
            focus_keywords=focus_keywords,
        )
        # 保存供下次 drift 比较
        self._prev_keywords = global_tf
        return summary

    # -- streaming helpers -------------------------------------------------

    def reset(self) -> None:
        self._prev_keywords = Counter()


__all__ = ["SessionFocus", "TopicCluster", "FocusSummary"]
