"""
MindForge v5.0 索引引擎
支持 TF-IDF、向量索引、FTS5 全文检索
"""

import math
import re
import sqlite3
from collections import Counter, defaultdict
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
            return [(doc_id, 0.0) for doc_id in list(self.vectors.keys())[:top_k]]

        # 倒排链累加点积：只访问与查询共享非零维度的候选文档
        dots: Dict[str, float] = {}
        for idx, qval in q.items():
            for doc_id, dval in self._postings.get(idx, {}).items():
                dots[doc_id] = dots.get(doc_id, 0.0) + qval * dval

        scores = [(doc_id, dot / (query_norm * self._norms[doc_id]))
                  for doc_id, dot in dots.items()
                  if self._norms.get(doc_id, 0.0) > 0]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

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
    """索引引擎"""

    def __init__(self, db_path: str = "./data/memory.db"):
        self.db_path = db_path
        self.vectorizer = TFIDFVectorizer()
        self.vector_index = VectorIndex()
        self._fitted = False
        self._hydrated = False
        self._doc_texts: Dict[str, str] = {}

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
        # 强制下次搜索时重新 fit，确保词表覆盖全部历史文档
        self._fitted = False
        self._hydrated = True
        return len(documents)

    def index_memory(self, doc_id: str, text: str, metadata: Optional[Dict] = None):
        """索引记忆"""
        self._doc_texts[doc_id] = text

        if not self._fitted and len(self._doc_texts) >= 5:
            self._fit_vectorizer()

        if self._fitted:
            # v5.6.3 性能修复：直接存稀疏向量（{dim: weight}），不再展开为
            # 词表长度的稠密 list
            vector_dict = self.vectorizer.transform(text)
            self.vector_index.add(doc_id, vector_dict, metadata)

    def remove_memory(self, doc_id: str):
        """移除索引"""
        self._doc_texts.pop(doc_id, None)
        self.vector_index.remove(doc_id)

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """搜索"""
        if not self._fitted and self._doc_texts:
            self._fit_vectorizer()

        if not self._fitted:
            return self._keyword_search(query, top_k)

        # v5.6.3 性能修复：查询向量同样保持稀疏，走倒排链检索
        query_vec_dict = self.vectorizer.transform(query)
        return self.vector_index.search(query_vec_dict, top_k)

    def _keyword_search(self, query: str, top_k: int) -> List[Tuple[str, float]]:
        """关键词搜索（降级方案）"""
        query_lower = query.lower()
        scores = []

        for doc_id, text in self._doc_texts.items():
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
        """转义 FTS5 查询特殊字符（v5.4.7 新增）

        FTS5 MATCH 语法中 + - * ( ) " : 等是特殊字符，
        直接传入会导致 OperationalError。用双引号包裹为短语查询，
        同时转义内部的双引号。
        """
        # 去除首尾空白
        q = query.strip()
        if not q:
            return q
        # 转义内部双引号
        q = q.replace('"', '""')
        # 用双引号包裹为短语查询
        return f'"{q}"'

    def fts_search(self, conn: sqlite3.Connection, query: str,
                   top_k: int = 10) -> List[Tuple[str, float]]:
        """FTS5 全文搜索（v5.4.7 修复：特殊字符转义）"""
        try:
            escaped = self._escape_fts5_query(query)
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
