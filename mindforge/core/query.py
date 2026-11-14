"""
MindForge 查询引擎
语义检索 + 知识图谱查询 + 上下文优化
"""

import logging
import sqlite3
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
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

# v6.3.0 explain：explain=False 时的占位 breakdown（写入被丢弃，零逻辑分叉）
_NO_BREAKDOWN: Dict[str, Any] = {}


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
    # v6.3.0 新增：检索可解释性——各召回路的原始分数与最终胜出路径。
    # 仅在 search(explain=True) 时填充，其余路径恒为 None（序列化兼容）。
    # 结构: {"tfidf": 0.42, "fts5": 0.31, "fuzzy": 0.0, "vector": 0.0,
    #        "final": 0.42, "winner": "tfidf",
    #        "fusion": {"text":…, "vector":…, "fused":…}（仅融合时存在）}
    score_breakdown: Optional[Dict[str, Any]] = None
    # v6.5.2：证据上下文窗口扩展（AdaptiveContextEngine 雏形）。
    # search(expand_context=N>0) 且命中条目带坐标 tags
    # （ctxgrp:<组id> + ctxseq:<序号>）时填充：[(seq, content),...]
    # 升序含命中轮自身；其余恒为 None（序列化兼容）。
    context_expansion: Optional[List[Tuple[int, str]]] = None


@dataclass
class RecallResult:
    """召回结果"""
    chunks: List[MemoryChunk]
    total_found: int
    query_time_ms: float
    strategy_used: str
    token_estimate: int
    layers_used: List[str] = field(default_factory=list)
    # v5.7.0 P1：加密兜底启用时为 True，表示部分结果来自全量解密扫描
    approximate: bool = False


# v6.5.5：相对时间词归一化表（窗口扩展时就地换算为绝对日期）。
# 锚定来源：轮次 tags 携带 "ctxdate:YYYY-MM-DD"（可选协议位）。
# 缺 ctxdate 时原样返回（降级安全）。纯规则零 LLM。
_TEMPORAL_RULES = [
    ("today", 0), ("tonight", 0), ("this morning", 0),
    ("this afternoon", 0), ("this evening", 0), ("right now", 0),
    ("yesterday", -1), ("yesterday morning", -1), ("last night", -1),
    ("tomorrow", 1), ("tomorrow morning", 1),
    ("last week", -7), ("next week", 7), ("last weekend", -7),
    ("next weekend", 7), ("last month", -30), ("next month", 30),
    ("last year", -365), ("next year", 365),
]
# v6.5.7：中文相对时间词（第 5 路查询解析用；与 _TEMPORAL_RULES
# 英文表分开，避免 "上个月" 被 N-数量模式抢先误配）
_CN_TEMPORAL_RULES = [
    ("今天", 0), ("今晚", 0), ("今天早上", 0), ("今天下午", 0),
    ("昨天", -1), ("昨晚", -1), ("前天", -2),
    ("明天", 1), ("明晚", 1), ("后天", 2),
    ("上周", -7), ("上星期", -7), ("下周", 7), ("下星期", 7),
    ("上个月", -30), ("上个月份", -30), ("上月", -30),
    ("下个月", 30), ("下月", 30),
    ("去年", -365), ("明年", 365),
]

_N_REL = re.compile(
    r"(\b\d+\s+(?:day|week|month|year)s?\s+ago\b"
    r"|\bin\s+\d+\s+(?:day|week|month|year)s?\b"
    r"|[两二三四五六七八九十0-9]+\s*(?:天|周|月|年)(?:之)?\s*[前后以])", re.IGNORECASE)
_CN_NUM = {"两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}
_REL_DUR = {"day": 1, "week": 7, "month": 30, "year": 365,
            "天": 1, "周": 7, "月": 30, "年": 365}


_ABS_DATE_PATTERNS = [
    # ISO 日期 2023-05-06 / 2023-5-6
    re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"),
    # 中文 2023年5月6日 / 2023年5月
    re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?"),
    # M月D日 / M月（年份取锚年，无锚则跳过）
    re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日(?![\d年])"),
]


def _parse_query_window(query: str,
                        time_anchor: Optional[str] = None
                        ) -> Optional[Tuple[str, str, str]]:
    """v6.5.7 第 5 路：从 query 解析时间窗口（第 5 路召回的前置）。

    Returns:
        (start_iso, end_iso, label) —— ISO 日期闭区间；无法解析返回 None。
        - query 携带绝对日期：直接成窗（单日窗口）
        - query 携带相对时间词：需 time_anchor（ISO 日期）换算；
          无锚时降级返回 None（v6.5.5 同款降级安全语义）
        label 用于 explain（如 "yesterday@2023-05-06"）。
    """
    q = (query or "").strip()
    if not q:
        return None
    today = None
    if time_anchor:
        try:
            from datetime import date as _d
            parts = str(time_anchor).strip().split("-")
            today = _d(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError, TypeError):
            today = None

    # 1) 绝对日期优先
    for pat in _ABS_DATE_PATTERNS:
        m = pat.search(q)
        if not m:
            continue
        try:
            groups = m.groups()
            if len(groups) == 3 and groups[0] and len(groups[0]) == 4:
                y, mo, dy = int(groups[0]), int(groups[1]), int(groups[2])
            elif len(groups) == 3 and groups[2] is None:
                y, mo, dy = int(groups[0]), int(groups[1]), 1
            else:
                # M月D日：年份取锚
                if today is None:
                    continue
                y, mo, dy = today.year, int(groups[0]), int(groups[1])
            from datetime import date as _d
            the_day = _d(y, mo, dy)
        except (ValueError, TypeError):
            continue
        iso = the_day.isoformat()
        return iso, iso, iso

    # 2) 相对词 → 锚换算
    if today is None:
        return None
    from datetime import timedelta as _td
    ql = q.lower()
    offset = None
    # 中文相对词优先（"上个月"含"个月"会被 _N_REL 数量模式误吸，
    # 必须先于英文表与数量模式匹配）
    for phrase, off in _CN_TEMPORAL_RULES:
        if phrase in q:
            offset = off
            label = phrase
            break
    if offset is None:
        for phrase, off in _TEMPORAL_RULES:
            if phrase in ql:
                offset = off
                label = phrase
                break
    if offset is None:
        m = _N_REL.search(q)
        if m:
            token = m.group(1)
            # 数字 + 单位 + 前/后
            import re as _re2
            num_m = _re2.search(r"(\d+|[两二三四五六七八九十]+)", token)
            unit_m = _re2.search(r"(day|week|month|year|天|周|月|年)", token,
                                 _re2.IGNORECASE)
            if num_m and unit_m:
                raw = num_m.group(1)
                n = (_CN_NUM.get(raw) if raw in _CN_NUM
                     else int(raw) if raw.isdigit() else None)
                if n is not None:
                    mult = _REL_DUR.get(unit_m.group(1).lower(), 1)
                    offset = (-n if ("ago" in token or "前" in token)
                              else n if ("in " in token or "后" in token
                                         or "以" in token) else -n)
                    label = token
    if offset is None:
        return None
    # 相对词窗口 ±3 天容差：对话时间表达天然模糊（"上周"可能指
    # 上周任何一天），单日窗口召回率过低（v6.5.7 199 问实测修正）
    d_start = today + _td(days=offset - 3)
    d_end = today + _td(days=offset + 3)
    return d_start.isoformat(), d_end.isoformat(), f"{label}@{today.isoformat()}"


def _normalize_temporal(text: str, anchor: Optional[str]) -> str:
    """把 text 中的相对时间词换算为 [绝对日期±N] 标注（v6.5.5）

    anchor 为该轮所属日期（YYYY-MM-DD）；None 时原样返回。
    """
    if not anchor or not text:
        return text
    try:
        from datetime import datetime, timedelta
        base = datetime.strptime(anchor, "%Y-%m-%d")
    except Exception:
        return text

    def fmt(d):
        return d.strftime("%Y-%m-%d")

    out = text
    # 显式「N 天/周/月 前（后）」
    def rel_repl(m):
        s = m.group(0).lower()
        import re as _r
        cn = _r.search(r"([两二三四五六七八九十]+)", s)
        if cn:
            # 中文数字（十以内单字；十X=10+X、X十=X*10 简化处理）
            t = cn.group(1)
            if len(t) == 1:
                n = _CN_NUM.get(t, 1)
            else:
                n = 10 + _CN_NUM.get(t[1], 0) if t[0] == "十" else _CN_NUM.get(t[0], 1) * 10 + _CN_NUM.get(t[1], 0)
        else:
            num = _r.search(r"(\d+)", s)
            if not num:
                return m.group(0)
            n = int(num.group(1))
        unit = None
        for k in _REL_DUR:
            if k in s:
                unit = k
                break
        if unit is None:
            return m.group(0)
        delta = n * _REL_DUR[unit]
        if "前" in s or "ago" in s:
            d = base - timedelta(days=delta)
        else:
            d = base + timedelta(days=delta)
        return f"[{fmt(d)}]"

    out = _N_REL.sub(rel_repl, out)
    # 固定短语表（倒序替换避免子串误伤：先长后短）
    for phrase, delta_days in sorted(_TEMPORAL_RULES, key=lambda x: -len(x[0])):
        pat = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
        d = base + timedelta(days=delta_days)
        out = pat.sub(f"[{fmt(d)}]", out)
    return out


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

    def _temporal_candidates(self, query: str,
                             time_anchor: Optional[str],
                             max_candidates: int = 30
                             ) -> List[Tuple[str, float, str]]:
        """v6.5.7 第 5 路：时间锚定召回。

        动机（LoCoMo 全量数据）：temporal 桶 acc 9.6% 为最大失血桶——
        问「上周做了什么」时相关记忆内容带绝对日期（2023-05-06），
        query 的「上周」与内容日期在四路（词面/语义）上均不匹配，
        证据根本进不了候选集。本路：
        1. 解析 query 时间窗（_parse_query_window，绝对日期或锚定相对词）
        2. 明文库 SQL 月粒度 LIKE 召回 content/tags 命中窗口日期的记忆
        3. 窗口内日期精确命中加权 0.85，月粒度命中 0.65

        加密库 content 为密文无法 SQL 匹配——自动跳过本路（warn 一次）。
        Returns:
            [(memory_id, score, label), ...]
        """
        win = _parse_query_window(query, time_anchor)
        if not win:
            return []
        start_iso, end_iso, label = win
        try:
            from datetime import date as _d
            d0, d1 = _d.fromisoformat(start_iso), _d.fromisoformat(end_iso)
        except ValueError:
            return []
        # 生成窗口覆盖的年月 pattern（最多 3 个，防超宽窗口全表扫）
        months = []
        y, mo = d0.year, d0.month
        while True:
            months.append(f"{y:04d}-{mo:02d}")
            if (y, mo) == (d1.year, d1.month) or len(months) >= 3:
                break
            mo += 1
            if mo > 12:
                y, mo = y + 1, 1
        conn = self.storage._get_conn()
        ids: Dict[str, float] = {}
        try:
            for ym in months:
                pat = f"%{ym}-%"
                rows = conn.execute(
                    "SELECT id, content, tags FROM memories "
                    "WHERE encrypted=0 AND category != 'trash' "
                    "AND (content LIKE ? OR tags LIKE ?) LIMIT ?",
                    (pat, pat, max_candidates)).fetchall()
                for r in rows:
                    score = 0.5   # 月粒度命中（v6.5.7 小样校准：补盲分
                    # 必须低于四路典型命中 0.5-0.7，否则挤掉正确证据）
                    text = (r[1] or "") + " " + (r[2] or "")
                    # 窗口内日期精确命中 → 加权
                    import re as _re3
                    for dm in _re3.finditer(r"20\d{2}-\d{2}-\d{2}", text):
                        try:
                            dd = _d.fromisoformat(dm.group(0)[:10])
                        except ValueError:
                            continue
                        if d0 <= dd <= d1:
                            score = 0.6
                            break
                    if r[0] not in ids or score > ids[r[0]]:
                        ids[r[0]] = score
        except sqlite3.OperationalError as e:
            logger.warning("temporal 路召回失败（降级跳过）: %s", e)
            return []
        return [(mid, s, label) for mid, s in ids.items()]

    def _graph_candidates(self, query: str,
                          max_entities: int = 8,
                          max_hops: int = 2,
                          ) -> List[Tuple[str, float, str]]:
        """v6.5.7 第 6 路：知识图谱邻接召回（两跳内）。

        动机（LoCoMo 全量数据）：multi-hop 桶 acc 25.2%——两跳问题
        需要「A 关联 B、B 关联 C」的链式证据，词面/语义检索对间接
        关联天然盲。本路：
        1. query 与 KG 实体匹配（双向 LIKE，限 max_entities）
        2. 沿 graph_relations 展开 1-2 跳邻接实体
        3. 收集沿途 relation.memory_ids 指回的记忆作为候选
        分数：1 跳 0.55、2 跳 0.45，× relation weight（cap 0.9）。
        （v6.5.7 小样校准：0.8/0.5 会把四路正确证据挤出 top-k，
        cat1 multi-hop 实测 -3.1pt——补盲分必须低于四路典型命中。）

        KG 为空（未跑 graph_extract）时本路空转返回 []——零成本降级。
        Returns:
            [(memory_id, score, label), ...]
        """
        conn = self.storage._get_conn()
        try:
            ent_rows = conn.execute(
                "SELECT id, entity FROM knowledge_graph LIMIT 500").fetchall()
        except sqlite3.OperationalError as e:
            logger.warning("graph 路召回失败（KG 表缺失，降级跳过）: %s", e)
            return []
        if not ent_rows:
            return []

        ql = (query or "").lower()
        if not ql:
            return []
        # 双向包含匹配：query 含实体名 或 实体名含 query 词
        # （实体名普遍较短，双向匹配召回率远高于单向）
        matched = []
        for eid, name in ent_rows:
            nm = (name or "").strip().lower()
            if not nm or len(nm) < 2:
                continue
            if nm in ql or ql in nm:
                matched.append((eid, name))
            if len(matched) >= max_entities:
                break
        if not matched:
            return []

        import json as _json
        cand: Dict[str, float] = {}
        start_ids = {eid for eid, _ in matched}
        frontier = {eid: (0.55, 1) for eid in start_ids}
        visited = set(start_ids)
        collected_rel_ids: set = set()
        for _hop in range(max_hops):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"SELECT from_entity, to_entity, weight, memory_ids "
                f"FROM graph_relations "
                f"WHERE from_entity IN ({placeholders}) "
                f"OR to_entity IN ({placeholders})",
                list(frontier.keys()) + list(frontier.keys())).fetchall()
            nxt = {}
            for fe, te, w, mids in rows:
                score0, hop = frontier.get(fe) or frontier.get(te) or (0.45, 2)
                other = te if fe in frontier else fe
                rel_score = min(0.9, score0 * (w or 1.0))
                if mids:
                    try:
                        for mid in _json.loads(mids):
                            if mid:
                                s = rel_score
                                if mid not in cand or s > cand[mid]:
                                    cand[mid] = s
                    except (ValueError, TypeError):
                        pass
                if other not in visited:
                    visited.add(other)
                    nxt[other] = (min(0.9, score0 * 0.6), hop + 1)
            frontier = nxt
        label = ",".join(name for _, name in matched[:3])
        return [(mid, s, f"kg:{label}") for mid, s in cand.items()]

    def search(self,
               query: str,
               max_results: int = 10,
               min_relevance: float = 0.3,
               categories: Optional[List[str]] = None,
               layers: Optional[List[MemoryLayer]] = None,
               agent_id: str = "",
               session_id: str = "",
               use_embedding: bool = True,
               explain: bool = False,
               expand_context: int = 0,
               time_anchor: Optional[str] = None,
               *, _update_access: bool = True,
               _record_search: bool = True) -> RecallResult:
        """搜索记忆

        v5.4.5 新增向量检索两阶段搜索：
        1. 向量召回：通过嵌入向量余弦相似度召回语义相近的记忆
        2. 多路融合：TF-IDF + FTS5 + Fuzzy + 向量，按 id 合并取最高分
        3. 精排：按综合分数排序，取 top_k

        当 use_embedding=False 或 EmbeddingEngine 不可用时，
        自动降级为 TF-IDF + Fuzzy 两路搜索（v5.4.5 之前的行为）。

        v6.3.0 新增 explain 参数（检索可解释性）：True 时每个
        MemoryChunk.score_breakdown 携带各召回路原始分数、融合
        明细与最终胜出路径，用于 CLI `search --explain` 与检索
        质量调试。默认 False，热路径零额外开销。
        """
        import time
        start = time.time()
        if (isinstance(max_results, bool) or not isinstance(max_results, int)
                or max_results < 0):
            raise ValueError("max_results must be a non-negative integer")
        if max_results == 0:
            if _record_search:
                try:
                    self.storage.record_search(query)
                except Exception:
                    pass
            return RecallResult(
                chunks=[], total_found=0,
                query_time_ms=round((time.time() - start) * 1000, 2),
                strategy_used="none", token_estimate=0, layers_used=[],
            )

        # v5.2.8 修复：CLI 等短生命周期进程中，内存 TF-IDF 索引为空，
        # 导致跨进程搜索永远返回 0 结果。搜索前先从持久层水合索引。
        if self.index.needs_hydration:
            self.index.hydrate(self.storage.get_indexable_documents())

        # ===== 第一阶段：多路召回 =====
        # score_map: {memory_id: score}，多路结果按 id 合并取最高分
        score_map = {}
        # v6.3.0 explain：各路原始分数与胜出路径（仅 explain=True 时填充）
        breakdown_map: Dict[str, Dict[str, Any]] = {}

        def _bd(mid: str) -> Dict[str, Any]:
            if not explain:
                return _NO_BREAKDOWN
            bd = breakdown_map.setdefault(mid, {})
            bd.setdefault("tfidf", 0.0)
            bd.setdefault("fts5", 0.0)
            bd.setdefault("fuzzy", 0.0)
            bd.setdefault("vector", 0.0)
            return bd

        # 路 1：TF-IDF
        tfidf_results = self.index.search(query, top_k=max_results * 3)
        for doc_id, s in tfidf_results:
            if explain:
                _bd(doc_id)["tfidf"] = max(s, _bd(doc_id)["tfidf"])
            if s >= score_map.get(doc_id, 0.0):
                score_map[doc_id] = s
                if explain:
                    _bd(doc_id)["winner"] = "tfidf"
                    _bd(doc_id)["final"] = s

        # 路 2：Fuzzy（拼写纠错兜底；v5.7.1：加密模式下总是跑供加密
        # 兜底复用，避免二次全表扫描）
        # v6.4.8: 触发收紧为「TF-IDF 零有效命中」。fuzzy 的价值在
        # 「查询词全不在库」（打错字场景），此时 TF-IDF 必空。有命中
        # （含低分）时 fuzzy 只补凑数条目还白扫候选集（30k 实测
        # 140ms/查询）。全库词回退（tfidf_fallback）同理跳过。
        # 注意按非零分判定：空查询走零分 top-K 兼容分支，
        # tfidf_results 恒非空，不看分会死锁拼写兜底。
        tfidf_fallback = getattr(self.index, "last_fallback", False)
        tfidf_effective = any(s > 0.0 for _, s in tfidf_results)
        fuzzy_hits = []
        if (self.storage.encrypted
                or (not tfidf_effective and not tfidf_fallback)):
            fuzzy_hits = self.storage.fuzzy_search(
                query, limit=max_results * 2, threshold=0.1)
            for item in fuzzy_hits:
                entry = item["entry"]
                s = min(0.95, float(item["score"]))
                if explain:
                    _bd(entry.id)["fuzzy"] = max(s, _bd(entry.id)["fuzzy"])
                if s >= score_map.get(entry.id, 0.0):
                    score_map[entry.id] = s
                    if explain:
                        _bd(entry.id)["winner"] = "fuzzy"
                        _bd(entry.id)["final"] = s

        # 路 3：FTS5 全文检索
        # v6.4.9: 全库词回退时跳过（FTS5 对高频短语的 bm25 全匹配行
        # 评分是病态查询的主要剩余成本，且无区分度信号）。正常查询
        # 走 fts_search 的 df 剪枝。
        fts5_used = False
        # v5.6.2: 防异常路径下 UnboundLocalError
        strategy_used = "tfidf+fuzzy"
        try:
            if not tfidf_fallback:
                conn = self.storage._get_conn()
                fts_results = self.index.fts_search(conn, query, top_k=max_results * 3)
                for doc_id, s in fts_results:
                    if explain:
                        _bd(doc_id)["fts5"] = max(s, _bd(doc_id)["fts5"])
                    if s >= score_map.get(doc_id, 0.0):
                        score_map[doc_id] = s
                        if explain:
                            _bd(doc_id)["winner"] = "fts5"
                            _bd(doc_id)["final"] = s
                if fts_results:
                    fts5_used = True
                    strategy_used = "tfidf+fuzzy+fts5"
                else:
                    strategy_used = "tfidf+fuzzy"
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
                                if explain:
                                    bd = _bd(entry.id)
                                    bd["vector"] = max(s, bd["vector"])
                                    bd["fusion"] = {
                                        "text": round(existing, 4),
                                        "vector": round(s, 4),
                                        "text_weight": TEXT_WEIGHT,
                                        "vector_weight": VECTOR_WEIGHT,
                                        "fused": round(fused, 4),
                                    }
                                # 融合后分数应高于文本单独分数（体现向量补充价值）
                                score_map[entry.id] = max(fused, existing)
                                if explain:
                                    _bd(entry.id)["winner"] = "text+vector"
                                    _bd(entry.id)["final"] = score_map[entry.id]
                            else:
                                # 只有向量命中 → 直接使用向量分数
                                if explain:
                                    _bd(entry.id)["vector"] = s
                                score_map[entry.id] = s
                                if explain:
                                    _bd(entry.id)["winner"] = "vector"
                                    _bd(entry.id)["final"] = s
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

        # v5.7.0 P1（v5.5.7 遗留）：加密模式兜底标记。
        # v5.7.1 重构：加密模式下路 2 fuzzy_search 必然跑过（见上），其结果
        # 已对加密条目内存解密打分并合并进 score_map。这里只需判断本次召回
        # 是否含加密条目来设置 approximate 标记，不再二次全表扫描。
        approximate = False
        if self.storage.encrypted and fuzzy_hits:
            if any(h["entry"].encrypted for h in fuzzy_hits):
                approximate = True

        # v5.5.2 perf: entry cache to avoid double-fetch in pre-filter + result build
        # Note: intentionally NOT passing agent_id/session_id to get_memory here,
        # to avoid updating access stats for memories that are filtered out.
        _entry_cache: Dict[str, Any] = {}

        def _cached_get(mid: str) -> Optional[Any]:
            if mid not in _entry_cache:
                # v6.4.2 perf: 批量检索上下文——首访访问计数走挂账，
                # 由本 search 尾部的 flush_all_access 统一落库。
                _entry_cache[mid] = self.storage.get_memory(
                    mid, _batch_access=True)
            return _entry_cache[mid]

        # ===== v6.5.7 第 5/6 路：temporal + graph 召回 =====
        # v6.5.10 分路施策（conv0 三轮小样 + 全量 1986 问校准）：
        # - **temporal 混排**（v6.5.7 语义保留）：窗口日期命中的证据
        #   与 query 时间语义强相关，0.6/0.85 分进主排序——conv0 上
        #   混排版 temporal +5.4pt / open-domain +7.7pt 的增益来源。
        # - **graph 尾部补位**：2 跳邻接天然宽泛，0.55/0.45 混进四路
        #   0.6-0.8 区间占 top-k 名额，把四路正确证据挤出（全量
        #   cat1 multi-hop 25.2%→10.3%，-14.9pt 的元凶）——只在四路
        #   不足 max_results 时追加。
        # 降级矩阵不变：加密库 temporal 跳过、KG 空空转、无锚降级。
        fill_candidates: Dict[str, Tuple[float, str]] = {}
        temporal_mixed = False
        if not self.storage.encrypted and query:
            for mid, s, lbl in self._temporal_candidates(query, time_anchor):
                if explain:
                    _bd(mid)["temporal"] = max(s, _bd(mid).get("temporal", 0.0))
                if s >= score_map.get(mid, 0.0):
                    is_new = mid not in score_map
                    score_map[mid] = s
                    if is_new:
                        temporal_mixed = True
                    if explain and is_new:
                        _bd(mid)["winner"] = "temporal"
                        _bd(mid)["final"] = s
        if query:
            for mid, s, lbl in self._graph_candidates(query):
                if explain:
                    _bd(mid)["graph"] = max(s, _bd(mid).get("graph", 0.0))
                if mid not in score_map and s >= fill_candidates.get(mid, (0.0, ""))[0]:
                    fill_candidates[mid] = (s, "graph")

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
        seen_contents: set = set()
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
            # v5.7.1：fuzzy_search 已对加密条目回填明文（entry.content 非空），
            # 直接用；仅未解密（content 为空串，来自 get_memory）时才解密。
            if entry.encrypted and not content_text:
                # v5.6.2 安全修复：单条解密失败不中断整个搜索，跳过坏数据
                try:
                    content_text = self.storage.decrypt_content(entry)
                except Exception:
                    logger.warning("记忆 %s 解密失败，已跳过", entry.id)
                    continue

            # v5.7.2：内容去重——不同 ID 但 content 完全相同的记忆只保留
            # 分数最高的一条（raw_results 已按分数降序），避免用户重复 add
            # 相同内容时搜索结果出现重复卡片。
            content_key = hash(content_text)
            if content_key in seen_contents:
                continue
            seen_contents.add(content_key)

            # v6.3.5：已被压缩合并的源记忆不再独立参与召回（由其摘要代表）。
            # 摘要本身无 compressed_into 标记，不受此过滤影响；解压后标记
            # 移除，源恢复独立召回。过滤位于 chunk 构建点，对 query_engine
            # 直调 / recall / CLI / REST / MCP 全部检索路径生效。
            if (entry.metadata or {}).get("compressed_into"):
                continue

            chunk = MemoryChunk(
                memory_id=entry.id,
                content=content_text,
                category=entry.category,
                relevance_score=score,
                layer=entry.layer,
                tags=entry.tags,
                metadata=entry.metadata,
                # v6.3.0 explain：仅 explain=True 时携带得分分解
                score_breakdown=breakdown_map.get(entry.id) if explain else None,
            )
            chunks.append(chunk)
            # v5.5.2 fix: update access stats for returned results.
            # The cached fetch intentionally skips access updates, so we
            # update them here for memories that actually make it to results.
            if _update_access:
                self.storage._update_access(entry, agent_id, session_id)

            if len(chunks) >= max_results:
                break

                # ===== v6.5.9 第 5/6 路尾部补位 =====
        # 四路结果不足 max_results 时，按分降序追加补盲候选（temporal
        # 优先于 graph 同分）。绝不挤四路既有命中——LoCoMo 全量校准。
        fill_used: set = set()
        if fill_candidates and len(chunks) < max_results:
            for mid, (s, src_path) in sorted(
                    fill_candidates.items(), key=lambda kv: -kv[1][0]):
                if len(chunks) >= max_results:
                    break
                entry = _cached_get(mid)
                if not entry:
                    continue
                if categories and entry.category not in categories:
                    continue
                if layers and entry.layer not in layers:
                    continue
                content_text = entry.content
                if entry.encrypted and not content_text:
                    try:
                        content_text = self.storage.decrypt_content(entry)
                    except Exception:
                        continue
                if content_text in seen_contents:
                    continue
                seen_contents.add(content_text)
                fill_used.add(src_path)
                chunks.append(MemoryChunk(
                    memory_id=entry.id,
                    content=content_text,
                    category=entry.category,
                    relevance_score=s,
                    layer=entry.layer,
                    tags=entry.tags,
                    metadata=entry.metadata,
                    score_breakdown=breakdown_map.get(entry.id) if explain else None,
                ))
                if _update_access:
                    self.storage._update_access(entry, agent_id, session_id)

        # v6.5.10：temporal 混排 + graph 补位——标记两类贡献
        # （temporal 看 breakdown 命中；graph 看 fill 实际追加）
        _six_used = set(fill_used)
        if explain and any("temporal" in (v or {}) for v in breakdown_map.values()):
            _six_used.add("temporal")
        if not explain:
            if fill_candidates:
                _six_used.add("graph")
            if temporal_mixed:
                _six_used.add("temporal")
        if _six_used:
            strategy_used = strategy_used + "+" + "+".join(sorted(_six_used))

        # v5.7.0 P2：记录搜索历史（审计写入失败不阻断主路径）
        if _record_search:
            try:
                self.storage.record_search(query)
            except Exception:
                pass

        # v6.4.2 perf: 本轮 search 命中的首访访问计数 + 挂账的 search 审计
        # 在此统一批量落库（executemany + 单次 commit，含审计 INSERT 合并）。
        # 挂账期上限 = 单次 search 时长，recently_accessed 排序在 search
        # 返回后即正确；同条记忆 60s 节流窗口内重复命中仍免落库（v5.6.3 契约）。
        try:
            self.storage.flush_access()
        except Exception:
            pass

        # v6.5.2：证据上下文窗口扩展（AdaptiveContextEngine 雏形）。
        # 命中 chunk 带 ctxgrp:/ctxseq: 坐标 tags 时，按组拉邻居轮填充
        # context_expansion。expand_context=0（默认）零开销直通，行为与
        # 旧版完全一致（1273 项既有测试契约不变）。
        if expand_context and expand_context > 0:
            self._expand_chunk_context(chunks, expand_context)

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
            approximate=approximate,
        )


    def _expand_chunk_context(self, chunks: List[MemoryChunk],
                              radius: int) -> None:
        """按 ctxgrp/ctxseq 坐标 tags 填充 context_expansion（v6.5.2）

        坐标协议：入库 tags 携带 "ctxgrp:<组id>" 与 "ctxseq:<序号>"；
        v6.5.5 起可选 "ctxdate:YYYY-MM-DD"（轮次日期锚）——携带时邻居
        文本中的相对时间词（yesterday/两周前…）就地归一化为绝对日期，
        temporal 类问题的证据自包含时间戳。
        无坐标或组内邻居缺失时该 chunk 字段保持 None（降级安全）。
        窗口 = [seq-radius, seq+radius]（含命中轮），升序输出。
        """
        import json as _json
        for chunk in chunks:
            if chunk.context_expansion is not None:
                continue
            grp = seq = None
            for t in (chunk.tags or []):
                if t.startswith("ctxgrp:"):
                    grp = t[7:]
                elif t.startswith("ctxseq:"):
                    try:
                        seq = int(t[7:])
                    except ValueError:
                        pass
            if grp is None or seq is None:
                continue
            try:
                group_entries = self.storage.search_by_tag(
                    f"ctxgrp:{grp}", limit=4096)
            except Exception:
                continue
            neighbours = []
            for e in group_entries:
                e_seq = None
                for t in (e.tags or []):
                    if t.startswith("ctxseq:"):
                        try:
                            e_seq = int(t[7:])
                        except ValueError:
                            pass
                        break
                if e_seq is None:
                    continue
                if abs(e_seq - seq) <= radius:
                    # v6.5.5：邻居轮时间归一化（ctxdate 存在时）
                    e_date = None
                    for t in (e.tags or []):
                        if t.startswith("ctxdate:"):
                            e_date = t[8:]
                            break
                    content = (_normalize_temporal(e.content, e_date)
                               if e_date else e.content)
                    neighbours.append((e_seq, content))
            if neighbours:
                neighbours.sort(key=lambda x: x[0])
                chunk.context_expansion = neighbours

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
