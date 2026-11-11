"""
MindForge v5.0 索引引擎
支持 TF-IDF、向量索引、FTS5 全文检索
"""

import heapq
import math
import os
import re
import sqlite3
from collections import Counter, defaultdict
from itertools import islice
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass


@dataclass
class IndexedDocument:
    """索引文档"""
    doc_id: str
    text: str
    metadata: Dict
    vector: List[float]


class TFIDFVectorizer:
    """TF-IDF 向量化器"""

    def __init__(self):
        self.vocab: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self._df: Dict[str, int] = {}  # v6.4.8: 增量 fit 的精确 df 延续
        self.doc_count: int = 0

    def _tokenize(self, text: str) -> List[str]:
        text = text.lower()
        tokens = []
        for segment in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+', text):
            if re.match(r'[\u4e00-\u9fff]', segment):
                if len(segment) <= 2:
                    tokens.append(segment)
                else:
                    for i in range(len(segment) - 1):
                        tokens.append(segment[i:i+2])
            elif len(segment) > 1:
                tokens.append(segment)
        return tokens

    def fit(self, documents: List[str]):
        self.doc_count = len(documents)
        df = defaultdict(int)

        all_tokens = set()
        for doc in documents:
            tokens = set(self._tokenize(doc))
            for token in tokens:
                df[token] += 1
                all_tokens.add(token)

        self.vocab = {word: idx for idx, word in enumerate(sorted(all_tokens))}

        for word, idx in self.vocab.items():
            self.idf[word] = math.log((self.doc_count + 1) / (df[word] + 1)) + 1
        # v6.4.8: df 表留存，供 incremental_fit 延续精确计数
        self._df = dict(df)

    def incremental_fit(self, text: str) -> None:
        """v6.4.8 新增：单文档增量词表扩展。

        修复「fit 后新增文档失索引」：新 token 按当前 doc_count+1 估
        IDF 补进词表；已知 token 的 df 计数 +1（IDF 漂移不回算旧向量，
        见 index_memory 注释）。维度号从当前词表长度起追加，与既有
        向量维度兼容（旧文档在新词上恒为零分量）。
        """
        if not self._df:
            # 未 fit 过（不应发生——调用方保证 fitted），防御性直通
            return
        self.doc_count += 1
        next_idx = len(self.vocab)
        for token in set(self._tokenize(text)):
            if token in self.vocab:
                self._df[token] = self._df.get(token, 0) + 1
                self.idf[token] = math.log(
                    (self.doc_count + 1) / (self._df[token] + 1)) + 1
            else:
                self._df[token] = 1
                self.vocab[token] = next_idx
                next_idx += 1
                self.idf[token] = math.log(
                    (self.doc_count + 1) / 2) + 1

    def transform(self, text: str) -> Dict[int, float]:
        tokens = self._tokenize(text)
        if not tokens:
            return {}

        tf = Counter(tokens)
        max_tf = max(tf.values())

        vector = {}
        for token, count in tf.items():
            if token in self.vocab:
                idx = self.vocab[token]
                tf_norm = 0.5 + 0.5 * count / max_tf
                vector[idx] = tf_norm * self.idf.get(token, 0)

        norm = math.sqrt(sum(v * v for v in vector.values()))
        if norm > 0:
            vector = {k: v / norm for k, v in vector.items()}

        return vector

    def cosine_similarity(self, v1: Dict[int, float], v2: Dict[int, float]) -> float:
        common = set(v1.keys()) & set(v2.keys())
        dot = sum(v1[k] * v2[k] for k in common)
        return dot


class VectorIndex:
    """向量索引

    v5.6.3 性能修复：向量以稀疏字典存储。TF-IDF 中文 bigram 词表可达上万维，
    旧实现把每篇文档展开成词表长度的稠密 list，单次搜索退化为 O(N*V) 全表
    浮点点积（N=文档数、V=词表大小，二者随语料同时增长），3k 条记忆的混合
    搜索因此达到 ~0.9s。现在：
      - 文档/查询向量一律稀疏化（只保留非零维，单条中文记忆通常仅几十个
        非零 bigram 权重）；
      - 维护维度 -> {doc_id: 权重} 的倒排链 _postings，查询只累计共享非零
        维的候选文档，点积复杂度降到 O(候选数 * 查询非零维)；
      - add/remove 同步维护倒排链，覆盖写入先清旧链。
    为兼容外部直接使用本类，仍接受稠密 list/tuple 输入。
    """

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.vectors: Dict[str, Dict[int, float]] = {}
        self.metadata: Dict[str, Dict] = {}
        self._norms: Dict[str, float] = {}  # 预计算 L2 范数缓存
        self._postings: Dict[int, Dict[str, float]] = {}  # 倒排链
        # v6.4.8: 上次 search 实际遍历的倒排链条目数（性能契约可观测）
        self._last_traversed = 0
        # v6.4.8: 上次 search 是否走了病态回退（全库词查询信号）
        self.last_fallback = False

    @staticmethod
    def _as_sparse(vector) -> Dict[int, float]:
        """稀疏 dict（dict 输入）或稠密 list/tuple 统一转成 {dim: weight}"""
        if isinstance(vector, dict):
            return {int(k): float(v) for k, v in vector.items() if v != 0.0}
        return {i: float(v) for i, v in enumerate(vector) if v != 0.0}

    def _remove_from_postings(self, doc_id: str, sparse: Dict[int, float]):
        for idx in sparse:
            chain = self._postings.get(idx)
            if chain is not None:
                chain.pop(doc_id, None)
                if not chain:
                    del self._postings[idx]

    def add(self, doc_id: str, vector, metadata: Optional[Dict] = None):
        sparse = self._as_sparse(vector)
        if doc_id in self.vectors:
            # 覆盖旧向量：先清理旧倒排链，避免过期权重残留
            self._remove_from_postings(doc_id, self.vectors[doc_id])
        self.vectors[doc_id] = sparse
        if metadata:
            self.metadata[doc_id] = metadata
        self._norms[doc_id] = math.sqrt(sum(v * v for v in sparse.values()))
        for idx, val in sparse.items():
            self._postings.setdefault(idx, {})[doc_id] = val

    def remove(self, doc_id: str):
        sparse = self.vectors.pop(doc_id, None)
        self.metadata.pop(doc_id, None)
        self._norms.pop(doc_id, None)
        if sparse is not None:
            self._remove_from_postings(doc_id, sparse)

    def search(self, query_vector, top_k: int = 10) -> List[Tuple[str, float]]:
        if not self.vectors:
            return []

        q = self._as_sparse(query_vector)
        query_norm = math.sqrt(sum(v * v for v in q.values()))
        if query_norm == 0:
            # v6.4.8: 空查询（无 token/全 vocab miss）≠ 全库词回退。
            # 显式复位 last_fallback，防止继承上一查询的 True 导致
            # query.py 误跳过 fuzzy 拼写兜底（空查询正是 fuzzy 的
            # 设计场景：用户词打错 → 全 miss → fuzzy 纠错召回）。
            self.last_fallback = False
            self._last_traversed = 0
            return [(doc_id, 0.0) for doc_id in list(self.vectors.keys())[:top_k]]

        # v6.4.6 perf: 低区分度查询维剪枝。100k 规模压测发现中文长查询
        # p50 421ms，profile 定位到常见词（如「条目/陈述性/记忆」）的倒排
        # 链全量遍历。两层剪枝：eps 相对阈值 + max_df 停用词。
        # v6.4.8 perf: max_df 从 0.5×N 收紧到 0.05×N。0.5 时半库词链
        # （数十万级遍历）仍进 active，100k 实测 mixed_realistic p50
        # 134ms。常见词的点积贡献近似常数（30k profile 实证 eps 剪不掉
        # 全库词，v6.4.6 注释已论证 top-K 排序不变），链长超库容 5% 的
        # 维一律剪——100k 库下 active 链长上界 5k，遍历毫秒级。
        _EPS_REL = 0.02
        _max_df_len = 0.05 * len(self.vectors)
        active = [(idx, qval) for idx, qval in q.items()
                  if qval > _EPS_REL * query_norm
                  and len(self._postings.get(idx, ())) <= _max_df_len]
        self._last_traversed = 0
        # v6.4.8: 全库词回退信号（供 query.py 跳过 fuzzy/FTS5 兜底）
        self.last_fallback = False
        if not active:
            self.last_fallback = True
            # v6.4.8: 回退不再遍历全部倒排链（旧 O(N×dims) 是 100k
            # 全库词查询 209ms 的根因）。全库词点积贡献是常数，top-K
            # 只由相对区分度决定：取最短 _FALLBACK_DIMS 维（区分度最高
            # 的信号），单链截断 _TRAVERSE_CAP 项（平分数下截断不改
            # top-K 语义）。
            _FALLBACK_DIMS = 3
            _TRAVERSE_CAP = 5000
            ranked = sorted(
                q.items(),
                key=lambda kv: len(self._postings.get(kv[0], ())))
            active = ranked[:_FALLBACK_DIMS]
            # islice 免建截断副本（dict(list(x)[:cap]) 的 15k 次 hash
            # 插入在实测里占 ~3ms，30k 病态复现 14→11ms）
            dots: Dict[str, float] = {}
            for idx, qval in active:
                chain = self._postings.get(idx, {})
                pairs = (chain.items() if len(chain) <= _TRAVERSE_CAP
                         else islice(chain.items(), _TRAVERSE_CAP))
                for doc_id, dval in pairs:
                    dots[doc_id] = dots.get(doc_id, 0.0) + qval * dval
                    self._last_traversed += 1
            return self._topk_from_dots(dots, query_norm, top_k)

        # 倒排链累加点积：只访问与查询共享非零维度的候选文档
        # （active 链长上界 _max_df_len，遍历量与库容解耦）
        dots: Dict[str, float] = {}
        for idx, qval in active:
            for doc_id, dval in self._postings.get(idx, {}).items():
                dots[doc_id] = dots.get(doc_id, 0.0) + qval * dval
                self._last_traversed += 1

        # v6.4.6 perf: heapq.nlargest 替代全量 sort——候选集大时
        # O(n·log k) 优于 O(n·log n)，且免建全量列表排序的中间开销。
        return self._topk_from_dots(dots, query_norm, top_k)

    def _topk_from_dots(self, dots: Dict[str, float], query_norm: float,
                        top_k: int) -> List[Tuple[str, float]]:
        """点积累积结果归一化 + top-K 选取（v6.4.8 抽出，回退/正常路径共用）"""
        return heapq.nlargest(
            top_k,
            ((doc_id, dot / (query_norm * self._norms[doc_id]))
             for doc_id, dot in dots.items()
             if self._norms.get(doc_id, 0.0) > 0),
            key=lambda x: x[1])

    def _cosine(self, v1, v2) -> float:
        """保留原始接口（向后兼容），稀疏/稠密输入均支持"""
        s1 = self._as_sparse(v1)
        s2 = self._as_sparse(v2)
        n1 = math.sqrt(sum(a * a for a in s1.values()))
        n2 = math.sqrt(sum(a * a for a in s2.values()))
        if n1 == 0 or n2 == 0:
            return 0.0
        dot = sum(val * s2.get(k, 0.0) for k, val in s1.items())
        return dot / (n1 * n2)


class IndexEngine:
    """索引引擎

    v5.6.5 P1 #5：内存结构（``_doc_texts`` / 稀疏向量 / 倒排链）设置
    LRU 上限，默认 ``DEFAULT_MAX_DOCS`` 条，可由构造参数或环境变量
    ``MINDFORGE_INDEX_MAX_DOCS`` 覆盖（设为 0/负数表示不限制）。超限时
    最久未写入的文档从该进程内加速器淘汰。被淘汰文档不会丢失可检索性：
    QueryEngine 多路召回同时走 SQLite FTS5 与 fuzzy（直接扫持久层），
    按 id 取最高分合并，因此 TF-IDF 加速器有界不影响最终召回。
    """

    DEFAULT_MAX_DOCS = 100_000

    def __init__(self, db_path: str = "./data/memory.db",
                 max_docs: Optional[int] = None):
        self.db_path = db_path
        self.vectorizer = TFIDFVectorizer()
        self.vector_index = VectorIndex()
        # v6.4.8: 病态回退信号门面透传（见 search()）
        self.last_fallback = False
        # v6.4.9: FTS5 trigram df 进程内缓存（见 _prune_fts_query）
        self._fts_vocab_cache: Optional[Dict[str, int]] = None
        self._fts_vocab_rowid: int = -1
        self._fitted = False
        self._hydrated = False
        self._doc_texts: Dict[str, str] = {}
        if max_docs is None:
            try:
                max_docs = int(os.environ.get("MINDFORGE_INDEX_MAX_DOCS",
                                             self.DEFAULT_MAX_DOCS))
            except (TypeError, ValueError):
                max_docs = self.DEFAULT_MAX_DOCS
        # None 语义保留：显式 <=0 表示不限制
        self.max_docs = max_docs if max_docs and max_docs > 0 else None
        self.evicted_count = 0

    @property
    def needs_hydration(self) -> bool:
        """是否需要从持久层水合（v5.2.8 新增）

        TF-IDF 向量索引是进程内存结构，CLI 等短生命周期进程启动时为空，
        必须先水合才能搜索到历史记忆。
        """
        return not self._hydrated

    def hydrate(self, documents: Dict[str, str]) -> int:
        """从持久层水合文档（v5.2.8 新增：修复跨进程搜索失效）

        Args:
            documents: {memory_id: content} 映射（通常来自
                StorageEngine.get_indexable_documents()）

        Returns:
            水合的文档数量
        """
        if self._hydrated:
            return 0
        self._doc_texts.update(documents)
        self._evict_if_needed()
        # 强制下次搜索时重新 fit，确保词表覆盖保留文档（被淘汰文档仍可经
        # FTS5/fuzzy 召回，见类文档说明）
        self._fitted = False
        self._hydrated = True
        return len(documents)

    def _evict_if_needed(self) -> None:
        """超过内存上限时，按插入顺序（近似 LRU）淘汰最旧文档，
        同步从 _doc_texts、稀疏向量、倒排链三处移除，保证三者一致。"""
        if self.max_docs is None:
            return
        while len(self._doc_texts) > self.max_docs:
            oldest = next(iter(self._doc_texts))
            del self._doc_texts[oldest]
            self.vector_index.remove(oldest)
            self.evicted_count += 1

    def index_memory(self, doc_id: str, text: str, metadata: Optional[Dict] = None):
        """索引记忆"""
        self._doc_texts[doc_id] = text

        # v5.6.5 P1 #5: 渐进 fit 触发点（LRU 守卫契约：add 即索引）。
        # v6.4.8 fix: fit 后的增量 add 先 incremental_fit 扩词表再
        # transform——旧逻辑词表锁死在前 5 条文档，此后增量 add 的
        # transform 全 vocab miss（空向量入索引，TF-IDF 路半瘫痪）。
        if not self._fitted and len(self._doc_texts) >= 5:
            self._fit_vectorizer()
        elif self._fitted:
            self.vectorizer.incremental_fit(text)

        if self._fitted:
            vector_dict = self.vectorizer.transform(text)
            self.vector_index.add(doc_id, vector_dict, metadata)

        # v5.6.5 P1 #5: 写入后 LRU 淘汰
        self._evict_if_needed()

    def remove_memory(self, doc_id: str):
        """移除索引"""
        self._doc_texts.pop(doc_id, None)
        self.vector_index.remove(doc_id)

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """搜索"""
        if not self._fitted and self._doc_texts:
            self._fit_vectorizer()

        if not self._fitted:
            # v6.4.8: 降级路径同样透传病态回退信号（保持门面契约一致）
            self.last_fallback = False
            return self._keyword_search(query, top_k)

        # v5.6.3 性能修复：查询向量同样保持稀疏，走倒排链检索
        query_vec_dict = self.vectorizer.transform(query)
        results = self.vector_index.search(query_vec_dict, top_k)
        # v6.4.8: 把 VectorIndex 的病态回退信号透传到门面层
        # （query.py 据此跳过 fuzzy 兜底路）
        self.last_fallback = self.vector_index.last_fallback
        return results

    def _keyword_search(self, query: str, top_k: int) -> List[Tuple[str, float]]:
        """关键词搜索（降级方案）"""
        query_lower = query.lower()
        scores = []

        for doc_id, text in self._doc_texts.items():
            if not text:
                continue  # v6.1.5 Bug-2 fix: 空文本除零守卫
            text_lower = text.lower()
            score = 0
            for word in query_lower.split():
                if word in text_lower:
                    score += text_lower.count(word)
            if score > 0:
                scores.append((doc_id, score / len(text) * 1000))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _fit_vectorizer(self):
        texts = list(self._doc_texts.values())
        self.vectorizer.fit(texts)

        for doc_id, text in self._doc_texts.items():
            vector_dict = self.vectorizer.transform(text)
            self.vector_index.add(doc_id, vector_dict)

        self._fitted = True

    @staticmethod
    def _escape_fts5_query(query: str) -> str:
        """转义 FTS5 查询特殊字符（v5.4.7 新增，v5.7.1 优化多词召回）

        FTS5 MATCH 语法中 + - * ( ) " : 等是特殊字符，
        直接传入会导致 OperationalError。用双引号包裹为短语查询，
        同时转义内部的双引号。

        v5.7.1：按空白分词后用 OR 连接各短语。英文/数字多词查询
        （如 "search bug"）可同时召回含任一短语的文档；中文无空格
        仍为单短语查询，行为不变。
        """
        q = query.strip()
        if not q:
            return q
        tokens = [t for t in q.split() if t]
        # 单 token（含中文无空格）保持原短语行为
        if len(tokens) <= 1:
            return '"' + q.replace('"', '""') + '"'
        # 多 token：各自转义为短语后 OR 连接
        escaped = ['"' + t.replace('"', '""') + '"' for t in tokens]
        return " OR ".join(escaped)

    def fts_search(self, conn: sqlite3.Connection, query: str,
                   top_k: int = 10) -> List[Tuple[str, float]]:
        """FTS5 全文搜索（v5.4.7 修复：特殊字符转义）

        v6.4.9: 多词查询先经 _prune_fts_query 剔除高频短语——
        trigram 索引下全库短语的 bm25 评分是 100k 延迟的大头。
        """
        try:
            escaped = self._escape_fts5_query(query)
            if not escaped:
                return []
            escaped = self._prune_fts_query(conn, query, escaped)
            if not escaped:
                return []
            rows = conn.execute("""
                SELECT m.id, bm25(memory_fts) as score
                FROM memory_fts
                JOIN memories m ON memory_fts.rowid = m.rowid
                WHERE memory_fts MATCH ?
                  AND m.category != 'trash'
                ORDER BY score
                LIMIT ?
            """, (escaped, top_k)).fetchall()
            # v5.5.2 fix: clamp bm25 score to prevent math.exp overflow.
            # bm25 returns negative values (lower = more relevant); a large
            # positive value is anomalous but would cause OverflowError.
            results = []
            for row in rows:
                raw = row[1]
                if raw is None:
                    continue
                clamped = max(-50.0, min(50.0, float(raw)))
                score = 1.0 / (1.0 + math.exp(clamped))
                results.append((row[0], score))
            return results
        except sqlite3.OperationalError:
            return []

    # v6.4.9: 高频短语剪枝比例（与 TF-IDF max_df 0.05 对齐）
    _FTS_MAX_DF_RATIO = 0.05

    def _prune_fts_query(self, conn: sqlite3.Connection, query: str,
                         escaped: str) -> str:
        """剔除 MATCH 里必然无区分度的高频短语。

        短语 df 上界 = min(该词全部 trigram 的 df)（短语匹配行 ⊆
        每个 trigram 行集的交集）。上界超库容 5% 的词剔除；全被剔除
        时留上界最小的那个，防空 MATCH。
        df 来自 fts5vocab 的进程内缓存（首查全表读一次，此后 O(1)
        点查；以 memories.max_rowid 为失效标记，覆盖 add/update；
        硬删导致的 df 缓存偏大只会让剪枝偏保守，5% 阈值级近似）。
        """
        tokens = [t for t in query.split() if t]
        if len(tokens) <= 1:
            return escaped
        try:
            row = conn.execute(
                "SELECT max(rowid) FROM memories").fetchone()
            total = (row[0] or 0) if row else 0
            if total < 200:
                return escaped  # 小库 df 语义不稳
            if (self._fts_vocab_cache is None
                    or total != self._fts_vocab_rowid):
                # v6.4.9: vocab 是流式虚拟表，逐 term 点查/IN 批查
                # 都触发全表扫（combined 100k 45ms 回退的根因）——
                # 全表读一次进 dict，之后纯内存点查。
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts_v "
                    "USING fts5vocab('memory_fts', 'row')")
                self._fts_vocab_cache = dict(
                    conn.execute("SELECT term, doc FROM memory_fts_v"))
                self._fts_vocab_rowid = total
            df_map = self._fts_vocab_cache
            budget = self._FTS_MAX_DF_RATIO * total
            kept = []
            best, best_ub = None, None
            for tok in tokens:
                if len(tok) < 3:
                    ub = 0  # trigram 查不了短词，保留
                else:
                    ub = min(df_map.get(tok[i:i + 3], 0)
                             for i in range(len(tok) - 2))
                if ub <= budget:
                    kept.append(tok)
                if best_ub is None or ub < best_ub:
                    best, best_ub = tok, ub
            if not kept:
                kept = [best] if best else []
                return " OR ".join(
                    '"' + t.replace('"', '""') + '"' for t in kept)
            if len(kept) == len(tokens):
                return escaped
            return " OR ".join(
                '"' + t.replace('"', '""') + '"' for t in kept)
        except sqlite3.Error:
            return escaped
