"""
MindForge v5.5.2 查询引擎
语义检索 + 知识图谱查询 + 上下文优化
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from .storage import StorageEngine, MemoryEntry
from .indexer import IndexEngine
from .types import MemoryLayer

logger = logging.getLogger(__name__)

# v5.4.8 P2-002/P3-003 修复：向量降级只警告一次
_vector_degradation_warned = False

# v5.5.5 P0 修复：向量最低分数阈值，低于此值的向量结果不参与融合
# 防止低质量向量结果干扰文本检索
VECTOR_MIN_SCORE_THRESHOLD = 0.40

# v5.5.5 P0 修复：文本+向量加权融合权重
# 文本检索更精准，权重更高；向量检索补充语义，权重较低
TEXT_WEIGHT = 0.6
VECTOR_WEIGHT = 0.4


@dataclass
class MemoryChunk:
    """记忆片段"""
    memory_id: str
    content: str
    category: str = "general"
    relevance_score: float = 0.0
    layer: MemoryLayer = MemoryLayer.SHORT_TERM
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecallResult:
    """召回结果"""
    chunks: List[MemoryChunk]
    total_found: int
    query_time_ms: float
    strategy_used: str
    token_estimate: int
    layers_used: List[str] = field(default_factory=list)


# v5.5.5 P1 新增：中文技术同义词词典（50+ 组）
# 用于查询扩展，提升中文检索召回率
_CHINESE_SYNONYMS: Dict[str, List[str]] = {
    # 性能/速度
    "慢": ["延迟", "卡顿", "性能差", "速度慢", "响应慢"],
    "延迟": ["慢", "卡顿", "响应时间", "等待时间", "耗时"],
    "卡顿": ["慢", "延迟", "不流畅", "掉帧", "响应慢"],
    "性能": ["速度", "效率", "吞吐量", "响应速度", "并发能力"],
    "速度慢": ["性能差", "慢", "卡顿", "延迟高", "效率低"],
    "性能差": ["速度慢", "慢", "低效", "吞吐低", "响应慢"],

    # 数据库/存储
    "数据库": ["db", "mysql", "postgresql", "sql server", "存储"],
    "慢查询": ["查询慢", "sql 慢", "数据库慢", "查询性能差"],
    "索引": ["index", "检索优化", "查询加速", "数据库索引"],
    "缓存": ["cache", "redis", "memcached", "缓冲"],
    "备份": ["backup", "快照", "容灾", "数据备份"],
    "恢复": ["restore", "回滚", "数据恢复", "灾备恢复"],

    # 安全
    "安全": ["security", "防护", "安全防护", "信息安全"],
    "加密": ["encryption", "密文", "数据加密", "加密保护"],
    "权限": ["permission", "access control", "访问控制", "授权"],
    "漏洞": ["vulnerability", "bug", "安全漏洞", "缺陷"],
    "攻击": ["attack", "入侵", "黑客", "网络攻击"],
    "防护": ["protection", "防御", "安全防护", "防护措施"],

    # 监控/运维
    "监控": ["monitor", "监控系统", "观测", "告警"],
    "告警": ["alert", "报警", "通知", "预警"],
    "日志": ["log", "logging", "系统日志", "运行日志"],
    "运维": ["ops", "devops", "系统运维", "运维管理"],
    "部署": ["deploy", "发布", "上线", "部署上线"],
    "扩容": ["scaling", "扩展", "弹性伸缩", "加机器"],

    # 错误/异常
    "错误": ["error", "bug", "异常", "问题"],
    "异常": ["exception", "错误", "不正常", "出问题"],
    "崩溃": ["crash", "挂了", "宕机", "退出"],
    "失败": ["fail", "失败了", "不成功", "出错"],
    "超时": ["timeout", "超时了", "连接超时", "请求超时"],

    # 网络
    "网络": ["network", "网络连接", "联网", "通信"],
    "连接": ["connection", "链接", "连接失败", "连接超时"],
    "带宽": ["bandwidth", "网速", "网络速度", "网络带宽"],
    "延迟高": ["网络慢", "高延迟", "ping 高", "网络延迟"],

    # 代码/开发
    "代码": ["code", "源码", "程序", "脚本"],
    "bug": ["错误", "缺陷", "问题", "漏洞"],
    "重构": ["refactor", "代码重构", "重写", "优化代码"],
    "测试": ["test", "测试用例", "单元测试", "自动化测试"],
    "调试": ["debug", "排错", "调试代码", "问题定位"],

    # 服务器/基础设施
    "服务器": ["server", "主机", "机器", "服务端"],
    "容器": ["container", "docker", "k8s", "容器化"],
    "集群": ["cluster", "集群部署", "分布式集群", "节点集群"],
    "负载均衡": ["load balancer", "lb", "nginx", "流量分发"],

    # 接口/API
    "接口": ["api", "接口调用", "服务接口", "rest api"],
    "请求": ["request", "调用", "http 请求", "api 请求"],
    "响应": ["response", "返回", "返回结果", "响应结果"],

    # AI/大模型
    "大模型": ["llm", "ai 模型", "语言模型", "gpt"],
    "向量": ["vector", "embedding", "嵌入向量", "向量化"],
    "提示词": ["prompt", "提示", "指令", "system prompt"],
    "token": ["令牌", "词元", "tokens", "token 数"],
}


class QueryEngine:
    """查询引擎"""

    def __init__(self, storage: StorageEngine, index: IndexEngine):
        self.storage = storage
        self.index = index

    @staticmethod
    def expand_query(query: str, max_expansions: int = 3) -> List[str]:
        """查询扩展：基于中文技术同义词词典扩展查询词

        v5.5.5 P1 新增。提升中文检索召回率。

        Args:
            query: 原始查询词
            max_expansions: 每个词最多扩展的同义词数量

        Returns:
            扩展后的查询词列表（含原始查询词）
        """
        if not query or not query.strip():
            return [query] if query else []

        # v5.6.2 安全修复：超长输入截断，防止 DoS
        _MAX_QUERY_LEN = 1000
        if len(query) > _MAX_QUERY_LEN:
            query = query[:_MAX_QUERY_LEN]

        # 简单分词：按空格、标点分割，保留中文词
        import re
        tokens = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9_-]+', query.lower())
        if not tokens:
            return [query]

        expanded_terms = set()
        expanded_terms.add(query)  # 保留原始查询

        for token in tokens:
            if token in _CHINESE_SYNONYMS:
                synonyms = _CHINESE_SYNONYMS[token][:max_expansions]
                for syn in synonyms:
                    expanded_terms.add(syn)
                    # 同时添加 "原词 + 同义词" 的组合
                    if token != syn:
                        expanded_terms.add(f"{token} {syn}")

        return list(expanded_terms)

    def search(self,
               query: str,
               max_results: int = 10,
               min_relevance: float = 0.3,
               categories: Optional[List[str]] = None,
               layers: Optional[List[MemoryLayer]] = None,
               agent_id: str = "",
               session_id: str = "",
               use_embedding: bool = True) -> RecallResult:
        """搜索记忆

        v5.4.5 新增向量检索两阶段搜索：
        1. 向量召回：通过嵌入向量余弦相似度召回语义相近的记忆
        2. 多路融合：TF-IDF + FTS5 + Fuzzy + 向量，按 id 合并取最高分
        3. 精排：按综合分数排序，取 top_k

        当 use_embedding=False 或 EmbeddingEngine 不可用时，
        自动降级为 TF-IDF + Fuzzy 两路搜索（v5.4.5 之前的行为）。
        """
        import time
        start = time.time()

        # v5.2.8 修复：CLI 等短生命周期进程中，内存 TF-IDF 索引为空，
        # 导致跨进程搜索永远返回 0 结果。搜索前先从持久层水合索引。
        if self.index.needs_hydration:
            self.index.hydrate(self.storage.get_indexable_documents())

        # ===== 第一阶段：多路召回 =====
        # score_map: {memory_id: score}，多路结果按 id 合并取最高分
        score_map = {}

        # 路 1：TF-IDF
        tfidf_results = self.index.search(query, top_k=max_results * 3)
        for doc_id, s in tfidf_results:
            if s >= score_map.get(doc_id, 0.0):
                score_map[doc_id] = s

        # 路 2：Fuzzy（TF-IDF 召回不足时补充）
        positive = sum(1 for _, s in tfidf_results if s >= min_relevance)
        if positive < max_results:
            supplements = self.storage.fuzzy_search(
                query, limit=max_results * 2, threshold=0.1)
            for item in supplements:
                entry = item["entry"]
                s = min(0.95, float(item["score"]))
                if s >= score_map.get(entry.id, 0.0):
                    score_map[entry.id] = s

        # 路 3：FTS5 全文检索
        fts5_used = False
        # v5.6.2 安全修复：初始化默认值，防止异常路径下 UnboundLocalError
        strategy_used = "tfidf+fuzzy"
        try:
            conn = self.storage._get_conn()
            fts_results = self.index.fts_search(conn, query, top_k=max_results * 3)
            for doc_id, s in fts_results:
                if s >= score_map.get(doc_id, 0.0):
                    score_map[doc_id] = s
            if fts_results:
                fts5_used = True
                strategy_used = "tfidf+fuzzy+fts5"
            else:
                strategy_used = "tfidf+fuzzy"
        except Exception:
            strategy_used = "tfidf+fuzzy"

        # 路 4：向量召回（v5.4.5 新增，v5.4.8 P2-002/P3-003 修复）
        global _vector_degradation_warned
        if use_embedding:
            # 先检查 engine 是否可用，避免无谓的异常
            engine = self.storage.embedding_engine
            engine_available = engine is not None and getattr(engine, 'is_available', False)

            if engine_available:
                try:
                    vector_results = self.storage.vector_search(
                        query, top_k=max_results * 3,
                        categories=categories, layers=layers)
                    if vector_results:
                        strategy_used = "vector+tfidf+fuzzy" + ("+fts5" if fts5_used else "")
                        for item in vector_results:
                            entry = item["entry"]
                            s = float(item["score"])
                            # v5.5.5 P0 修复：低于阈值的向量结果不参与融合
                            if s < VECTOR_MIN_SCORE_THRESHOLD:
                                continue
                            existing = score_map.get(entry.id, 0.0)
                            if existing > 0:
                                # 文本 + 向量均命中 → 加权融合
                                fused = existing * TEXT_WEIGHT + s * VECTOR_WEIGHT
                                # 融合后分数应高于文本单独分数（体现向量补充价值）
                                score_map[entry.id] = max(fused, existing)
                            else:
                                # 只有向量命中 → 直接使用向量分数
                                score_map[entry.id] = s
                except Exception as e:
                    # 向量搜索异常（非 engine 不可用情况）
                    if not _vector_degradation_warned:
                        _vector_degradation_warned = True
                        logger.warning(
                            "向量搜索异常，降级为纯文本检索: %s。"
                            "此警告仅显示一次。", e
                        )
            else:
                # engine 不可用，只警告一次
                if not _vector_degradation_warned:
                    _vector_degradation_warned = True
                    logger.warning(
                        "向量引擎不可用（可能未安装 sentence-transformers），"
                        "降级为 TF-IDF + FTS5 + Fuzzy 三路检索。"
                        "语义相似但用词不同的记忆可能无法召回。"
                        "安装命令: pip install sentence-transformers"
                    )

        # v5.5.2 perf: entry cache to avoid double-fetch in pre-filter + result build
        # Note: intentionally NOT passing agent_id/session_id to get_memory here,
        # to avoid updating access stats for memories that are filtered out.
        _entry_cache: Dict[str, Any] = {}

        def _cached_get(mid: str) -> Optional[Any]:
            if mid not in _entry_cache:
                _entry_cache[mid] = self.storage.get_memory(mid)
            return _entry_cache[mid]

        # 预过滤：按 categories/layers 筛选 score_map，避免非匹配记忆占据排序位
        if categories or layers:
            filtered_map = {}
            for doc_id, score in list(score_map.items()):
                entry = _cached_get(doc_id)
                if not entry:
                    continue
                if categories and entry.category not in categories:
                    continue
                if layers and entry.layer not in layers:
                    continue
                filtered_map[doc_id] = score
            score_map = filtered_map

        # 合并排序
        raw_results = sorted(score_map.items(), key=lambda x: x[1], reverse=True)

        # ===== 第二阶段：构建结果 =====
        chunks = []
        for doc_id, score in raw_results:
            if score < min_relevance:
                break

            entry = _cached_get(doc_id)
            if not entry:
                continue

            if categories and entry.category not in categories:
                continue
            if layers and entry.layer not in layers:
                continue

            content_text = entry.content
            if entry.encrypted:
                # v5.6.2 安全修复：单条解密失败不中断整个搜索，跳过坏数据
                try:
                    content_text = self.storage.decrypt_content(entry)
                except Exception:
                    logger.warning("记忆 %s 解密失败，已跳过", entry.id)
                    continue

            chunk = MemoryChunk(
                memory_id=entry.id,
                content=content_text,
                category=entry.category,
                relevance_score=score,
                layer=entry.layer,
                tags=entry.tags,
                metadata=entry.metadata,
            )
            chunks.append(chunk)
            # v5.5.2 fix: update access stats for returned results.
            # The cached fetch intentionally skips access updates, so we
            # update them here for memories that actually make it to results.
            self.storage._update_access(entry, agent_id, session_id)

            if len(chunks) >= max_results:
                break

        elapsed = (time.time() - start) * 1000
        token_estimate = sum(len(c.content) for c in chunks) // 4

        used_layers = list(set(c.layer.value for c in chunks))

        return RecallResult(
            chunks=chunks,
            total_found=len(chunks),
            query_time_ms=round(elapsed, 2),
            strategy_used=strategy_used,
            token_estimate=token_estimate,
            layers_used=used_layers,
        )

    def get_session_context(self,
                            session_id: str,
                            max_items: int = 20) -> List[MemoryEntry]:
        """获取会话上下文记忆"""
        all_memories = self.storage.list_memories(limit=max_items * 2)
        session_memories = [
            m for m in all_memories
            if m.source_session == session_id
        ]
        return session_memories[:max_items]

    def get_by_layer(self, layer: MemoryLayer, limit: int = 50) -> List[MemoryEntry]:
        """按层级获取记忆"""
        return self.storage.list_memories(layer=layer, limit=limit)
