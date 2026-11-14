"""
MindForge v5.0 知识图谱引擎
自动提取实体与关系，构建动态知识网络
"""

import re
import json
import uuid
import time
import sqlite3  # v5.4.7 修复 C-3：异常处理中引用 sqlite3.OperationalError 需要此导入
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import defaultdict, deque  # v5.4.7 修复 L-7：BFS 使用 deque


@dataclass
class KnowledgeEntity:
    """知识实体"""
    id: str
    name: str
    entity_type: str
    description: str = ""
    metadata: Dict = field(default_factory=dict)
    created_at: float = 0.0


@dataclass
class KnowledgeRelation:
    """知识关系"""
    id: str
    from_entity: str
    to_entity: str
    relation_type: str
    weight: float = 1.0
    memory_ids: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    created_at: float = 0.0


@dataclass
class GraphPath:
    """图路径"""
    entities: List[str]
    relations: List[str]
    total_weight: float


ENTITY_PATTERNS = {
    "technology": [
        r'\b(python|java|javascript|typescript|go|rust|c\+\+|ruby|php)\b',
        r'\b(react|vue|angular|django|flask|spring|fastapi)\b',
        r'\b(mysql|postgresql|mongodb|redis|elasticsearch|sqlite)\b',
        r'\b(docker|kubernetes|k8s|aws|gcp|azure)\b',
        # v5.7.9：中文技术栈词
        r'(python|java|mysql|redis|sqlite|docker|kubernetes|微信小程序|浏览器插件)',
    ],
    "person": [
        r'(?:Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.)\s+[A-Z][a-z]+',
    ],
    "organization": [
        r'(?:Inc|Ltd|LLC|Corp|Corporation|Company|University|Institute)\b',
        # v5.7.9：中文组织后缀（公司/集团/大学/研究院等）
        r'[\u4e00-\u9fff]{2,12}(?:公司|集团|大学|研究院|实验室|工作室|团队|中心)',
    ],
    "product": [
        # v5.7.9：常见产品/平台名
        r'\b(wechat|alipay|feishu|lark|dingtalk|douyin|chrome|vscode|github|gitlab|slack|notion)\b',
        r'(微信|支付宝|飞书|钉钉|抖音|浏览器|编辑器)',
    ],
}

RELATION_PATTERNS = [
    # v6.5.7：英文对话关系（LoCoMo 类语料；multi-hop 链式证据前置）
    (r'\b([A-Z][a-z]+)\s+(?:is\s+)?(?:friends?|colleagues?|roommates?|classmates?)\s+with\s+([A-Z][a-z]+)', "friend_of"),
    (r'\b([A-Z][a-z]+)\s+(?:works?|working)\s+(?:at|for|in)\s+([A-Z][A-Za-z ]{1,30})', "works_at"),
    (r'\b([A-Z][a-z]+)\s+(?:lives?|living|moved)\s+(?:in|to)\s+([A-Z][A-Za-z ]{1,30})', "lives_in"),
    (r'\b([A-Z][a-z]+)\s+(?:met|saw|visited|called|emailed|texted)\s+([A-Z][A-Za-z ]{1,30})', "interacted_with"),
    (r'\b([A-Z][a-z]+)\s+(?:is\s+)?(?:married|engaged)\s+to\s+([A-Z][a-z]+)', "spouse_of"),
    (r'\b([A-Z][a-z]+)\s+(?:studied|study)\s+(?:at|in)\s+([A-Z][A-Za-z ]{1,30})', "studied_at"),
    (r'([\u4e00-\u9fa5A-Za-z]{1,12})\s*(?:认识|结识)\s+([\u4e00-\u9fa5A-Za-z]{1,12})', "knows"),
    (r'(.+?)\s+(?:使用|利用|采用|运用)\s+(.+)', "uses"),
    (r'(.+?)\s+(?:是|属于|归类为)\s+(.+)', "is_a"),
    (r'(.+?)\s+(?:包含|包括|有)\s+(.+)', "contains"),
    (r'(.+?)\s+(?:相关于|关联|有关)\s+(.+)', "related_to"),
    (r'(.+?)\s+(?:优化|改进|提升)\s+(.+)', "improves"),
    (r'(.+?)\s+(?:基于|依赖|需要)\s+(.+)', "depends_on"),
]


class KnowledgeGraph:
    """知识图谱引擎"""

    def __init__(self, storage=None, auto_load=True):
        self.storage = storage
        self.entities: Dict[str, KnowledgeEntity] = {}
        self.relations: Dict[str, KnowledgeRelation] = {}
        self._adjacency: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)
        # v5.7.9：同进程内已抽取记忆 ID（防抖，避免全库增量重复建关系）
        self._processed_memory_ids: set = set()
        if self.storage is not None and auto_load:
            self._ensure_tables()
            self._load_from_db()

    # v6.5.7：非专名停用词（首字母大写的普通词，防人名误报）
    _NAME_STOPWORDS = {
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday", "january", "february", "march", "april",
        "may", "june", "july", "august", "september", "october",
        "november", "december", "i", "he", "she", "it", "we", "they",
        "the", "this", "that", "there", "here", "what", "when", "where",
        "who", "how", "why", "yes", "no", "ok", "oh", "hi", "hello",
        "thanks", "thank", "please", "sure", "well", "now", "then",
        "today", "tomorrow", "yesterday", "morning", "afternoon",
        "evening", "night", "week", "month", "year", "day", "time",
    }
    _PERSON_NAME_RE = re.compile(
        r"\b([A-Z][a-z]{1,20})(?:\s+([A-Z][a-z]{1,20}))?\b")
    _PREP_ENTITY_RE = re.compile(
        r"\b(?:in|at|to|from|near)\s+([A-Z][a-z]{2,20})\b")

    def _is_name_candidate(self, word: str) -> bool:
        w = word.lower()
        return (w not in self._NAME_STOPWORDS and len(w) >= 2)

    def extract_entities(self, text: str) -> List[Tuple[str, str]]:
        """从文本中提取实体

        v6.5.7 扩充：英文名/地名/专名规则（第 6 路图检索前置）。
        此前 person 只匹配 "Mr. Zhang" 头衔模式，LoCoMo 类对话
        （Melanie / Pamela / Daniel）全部漏抽 → 图路空转 →
        multi-hop 桶（acc 25.2%）无路可用。
        规则（纯正则零 LLM）：
        - person：首字母大写词对（First Last）或单大写词
          （停用词过滤；单大写词需全文出现 ≥2 次——对话人物会复现）
        - location/org：in/at/to/from/near + 大写词（Paris / Google）
        """
        entities = []

        for entity_type, patterns in ENTITY_PATTERNS.items():
            for pattern in patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0]
                    entities.append((match.strip().lower(), entity_type))

        # 英文人名：词对优先（First Last），单大写词出现 ≥2 次
        words_seen = {}
        for m in self._PERSON_NAME_RE.finditer(text):
            first, second = m.group(1), m.group(2)
            if not self._is_name_candidate(first):
                continue
            if second and self._is_name_candidate(second):
                full = f"{first} {second}".lower()
                entities.append((full, "person"))
                words_seen[first.lower()] = words_seen.get(first.lower(), 0) + 1
            else:
                words_seen[first.lower()] = words_seen.get(first.lower(), 0) + 1
        for w, cnt in words_seen.items():
            if cnt >= 2:
                entities.append((w, "person"))
        # 地名/机构：介词 + 大写词
        for m in self._PREP_ENTITY_RE.finditer(text):
            w = m.group(1)
            if self._is_name_candidate(w) and w.capitalize() == w:
                entities.append((w.lower(), "location"))
        # 单次出现的人名兜底：出现在关系动词模式中的大写词
        # （Melanie met Pamela——Pamela 仅出现一次也必须入图，
        # 否则关系链断裂、multi-hop 检索无路）
        for pat, _rt in RELATION_PATTERNS:
            for m in re.finditer(pat, text, re.IGNORECASE):
                for g in m.groups():
                    if not g:
                        continue
                    for tok in g.split()[:2]:
                        tok = tok.strip()
                        if (tok and tok[:1].isupper()
                                and self._is_name_candidate(tok)
                                and len(tok) >= 3):
                            entities.append((tok.lower(), "person"))

        seen = set()
        unique = []
        for name, etype in entities:
            key = (name, etype)
            if key not in seen:
                seen.add(key)
                unique.append((name, etype))

        return unique

    def extract_relations(self, text: str, entities: List[str]) -> List[Tuple[str, str, str]]:
        """提取实体间关系（简易版）

        v6.5.7 修复：原实现对全文 lower() 后再跑正则——英文关系模式
        里的 [A-Z] 在小写文本上永不匹配（英文关系全数落空）。改为
        原文匹配 + 模式自带 (?i) 语义的 IGNORECASE；中文模式不受
        大小写影响，行为不变。
        """
        relations = []
        text_lower = text.lower()

        for pattern, rel_type in RELATION_PATTERNS:
            # 英文模式含 [A-Z]（大写名锚定）：用原文 + IGNORECASE；
            # 中文模式无大小写语义，原文/小写均可——统一原文匹配
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match) >= 2:
                    subj = match[0].strip()
                    obj = match[1].strip()

                    subj_entity = self._find_matching_entity(subj, entities)
                    obj_entity = self._find_matching_entity(obj, entities)

                    if subj_entity and obj_entity and subj_entity != obj_entity:
                        relations.append((subj_entity, rel_type, obj_entity))

        return relations

    def _find_matching_entity(self, text: str, entities: List[str]) -> Optional[str]:
        text_lower = text.lower()
        for entity in entities:
            if entity.lower() in text_lower:
                return entity
        return None

    def _ensure_tables(self) -> None:
        """v5.7.9：确保图谱持久化表存在（旧库升级容错）"""
        try:
            conn = self.storage._get_conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_graph (
                    id TEXT PRIMARY KEY,
                    entity TEXT,
                    entity_type TEXT,
                    description TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS graph_relations (
                    id TEXT PRIMARY KEY,
                    from_entity TEXT,
                    to_entity TEXT,
                    relation_type TEXT,
                    weight REAL DEFAULT 1.0,
                    memory_ids TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}',
                    created_at REAL
                );
            """)
            conn.commit()
        except sqlite3.OperationalError as _kg_err:
            logger.warning("创建知识图谱表失败: %s", _kg_err)

    def _load_from_db(self) -> None:
        """v5.7.9：从持久化表重建内存态（重启后实体/关系/邻接可用）"""
        try:
            conn = self.storage._get_conn()
            for row in conn.execute(
                "SELECT id, entity, entity_type, description, metadata, created_at "
                "FROM knowledge_graph"
            ):
                eid, name, etype, desc_text, meta, created = row
                self.entities[eid] = KnowledgeEntity(
                    id=eid, name=name, entity_type=etype or "general",
                    description=desc_text or "",
                    metadata=json.loads(meta) if meta else {},
                    created_at=created or 0.0,
                )
            for row in conn.execute(
                "SELECT id, from_entity, to_entity, relation_type, weight, "
                "memory_ids, metadata, created_at FROM graph_relations"
            ):
                rid, fe, te, rtype, weight, mids, meta, created = row
                rel = KnowledgeRelation(
                    id=rid, from_entity=fe, to_entity=te, relation_type=rtype,
                    weight=weight or 1.0,
                    memory_ids=json.loads(mids) if mids else [],
                    metadata=json.loads(meta) if meta else {},
                    created_at=created or 0.0,
                )
                self.relations[rid] = rel
                self._adjacency[fe].append((te, rtype, rel.weight))
                self._adjacency[te].append((fe, rtype, rel.weight))
                for mid in rel.memory_ids:
                    self._processed_memory_ids.add(mid)
        except sqlite3.OperationalError as _kg_err:
            logger.warning("加载知识图谱失败: %s", _kg_err)

    def add_entity(self, name: str, entity_type: str = "general",
                   description: str = "", metadata: Optional[Dict] = None) -> KnowledgeEntity:
        """添加实体"""
        entity_id = str(uuid.uuid4())
        now = time.time()

        entity = KnowledgeEntity(
            id=entity_id,
            name=name,
            entity_type=entity_type,
            description=description,
            metadata=metadata or {},
            created_at=now,
        )

        self.entities[entity_id] = entity

        if self.storage:
            try:
                conn = self.storage._get_conn()
                conn.execute("""
                    INSERT OR IGNORE INTO knowledge_graph (id, entity, entity_type, description, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (entity_id, name, entity_type, description,
                      json.dumps(metadata or {}, ensure_ascii=False), now))
                conn.commit()
            except sqlite3.OperationalError as _kg_err:
                logger.warning("写入知识图谱实体失败: %s", _kg_err)

        return entity

    def add_relation(self, from_name: str, to_name: str, relation_type: str,
                     weight: float = 1.0, memory_id: str = "") -> KnowledgeRelation:
        """添加关系"""
        from_entity = self._get_or_create_entity(from_name)
        to_entity = self._get_or_create_entity(to_name)

        rel_id = str(uuid.uuid4())
        now = time.time()

        relation = KnowledgeRelation(
            id=rel_id,
            from_entity=from_entity.id,
            to_entity=to_entity.id,
            relation_type=relation_type,
            weight=weight,
            memory_ids=[memory_id] if memory_id else [],
            created_at=now,
        )

        self.relations[rel_id] = relation
        self._adjacency[from_entity.id].append((to_entity.id, relation_type, weight))
        self._adjacency[to_entity.id].append((from_entity.id, relation_type, weight))

        if self.storage:
            try:
                conn = self.storage._get_conn()
                conn.execute("""
                    INSERT INTO graph_relations (id, from_entity, to_entity, relation_type, weight, memory_ids, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (rel_id, from_entity.id, to_entity.id, relation_type,
                      weight, json.dumps([memory_id] if memory_id else []),
                      json.dumps({}), now))
                conn.commit()
            except sqlite3.OperationalError as _kg_err:
                logger.warning("写入知识图谱关系失败: %s", _kg_err)

        return relation

    def _get_or_create_entity(self, name: str) -> KnowledgeEntity:
        for entity in self.entities.values():
            if entity.name.lower() == name.lower():
                return entity
        return self.add_entity(name)

    def process_memory(self, memory_id: str, content: str) -> Tuple[List[KnowledgeEntity], List[KnowledgeRelation]]:
        """处理一条记忆，提取实体和关系"""
        # v5.7.9：同进程内已处理记忆直接跳过（防抖）
        if memory_id and memory_id in self._processed_memory_ids:
            return [], []
        entities_data = self.extract_entities(content)
        entity_names = [name for name, _ in entities_data]

        added_entities = []
        for name, etype in entities_data:
            entity = self._get_or_create_entity(name)
            if entity.entity_type == "general" and etype != "general":
                entity.entity_type = etype
            added_entities.append(entity)

        relations = self.extract_relations(content, entity_names)
        added_relations = []
        for subj, rel_type, obj in relations:
            relation = self.add_relation(subj, obj, rel_type, memory_id=memory_id)
            added_relations.append(relation)

        if memory_id:
            self._processed_memory_ids.add(memory_id)

        return added_entities, added_relations

    def get_related_entities(self, entity_name: str, depth: int = 2,
                             max_results: int = 20) -> List[Tuple[str, str, float]]:
        """获取相关实体（广度优先）"""
        entity = self._find_entity_by_name(entity_name)
        if not entity:
            return []

        visited = {entity.id}
        queue = deque([(entity.id, 0, 1.0)])  # v5.4.7 修复 L-7：使用 deque 替代 list.pop(0)
        results = []

        while queue:
            current_id, current_depth, current_weight = queue.popleft()
            if current_depth > depth:
                continue

            for neighbor_id, rel_type, weight in self._adjacency.get(current_id, []):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)

                total_weight = current_weight * weight
                neighbor = self.entities.get(neighbor_id)
                if neighbor:
                    results.append((neighbor.name, rel_type, total_weight))

                if current_depth < depth:
                    queue.append((neighbor_id, current_depth + 1, total_weight))

        results.sort(key=lambda x: x[2], reverse=True)
        return results[:max_results]

    def find_path(self, from_name: str, to_name: str, max_depth: int = 3) -> Optional[GraphPath]:
        """查找两个实体间的路径"""
        from_entity = self._find_entity_by_name(from_name)
        to_entity = self._find_entity_by_name(to_name)
        if not from_entity or not to_entity:
            return None

        from queue import deque
        queue = deque([(from_entity.id, [from_entity.id], [])])
        visited = {from_entity.id}

        while queue:
            current, path, rels = queue.popleft()
            if len(path) > max_depth + 1:
                continue

            if current == to_entity.id:
                entity_names = []
                for eid in path:
                    e = self.entities.get(eid)
                    entity_names.append(e.name if e else eid)
                total_w = 1.0
                return GraphPath(entities=entity_names, relations=rels, total_weight=total_w)

            for neighbor_id, rel_type, weight in self._adjacency.get(current, []):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                queue.append((neighbor_id, path + [neighbor_id], rels + [rel_type]))

        return None

    def _find_entity_by_name(self, name: str) -> Optional[KnowledgeEntity]:
        name_lower = name.lower()
        for entity in self.entities.values():
            if entity.name.lower() == name_lower:
                return entity
        return None

    def get_entity_stats(self) -> Dict:
        """获取图谱统计"""
        type_counts = defaultdict(int)
        for entity in self.entities.values():
            type_counts[entity.entity_type] += 1

        rel_counts = defaultdict(int)
        for relation in self.relations.values():
            rel_counts[relation.relation_type] += 1

        return {
            "total_entities": len(self.entities),
            "total_relations": len(self.relations),
            "entity_types": dict(type_counts),
            "relation_types": dict(rel_counts),
        }
