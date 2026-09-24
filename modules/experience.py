# -*- coding: utf-8 -*-
"""
MindForge v5.8.0 程序性记忆 / 经验蒸馏引擎（Cases → Skills 自进化）
====================================
在 v5.3.9 SkillExtractor（记忆→技能模板抽取）基础上补齐闭环：

  1. 案例记录：Agent 把一次成功任务存为 ExperienceCase（任务/内容/结果/耗时/来源记忆），
     持久化到 experience_cases 表；
  2. 蒸馏落库：distill() 从案例批量提取技能模板（复用 SkillExtractor），
     UPSERT 到 experience_skills 表；
  3. 匹配复用：match_skills() 按新任务查询已存技能，render_skill() 用参数渲染
     可执行流程（复用 SkillTemplate.render）。

设计原则：全部零外部依赖、SQLite 单文件、显式调用（不自动写主流程）。
"""

import json
import re
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from modules.skill_extractor import SkillExtractor, SkillTemplate


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

class ExperienceCase:
    """一次可复用的任务经验（Case）"""

    __slots__ = ("id", "task", "content", "result", "outcome", "duration_seconds",
                 "tags", "source_memory_ids", "agent_id", "created_at")

    def __init__(self, id: str = "", task: str = "", content: str = "",
                 result: str = "", outcome: str = "success",
                 duration_seconds: float = 0.0, tags: Optional[List[str]] = None,
                 source_memory_ids: Optional[List[str]] = None,
                 agent_id: str = "", created_at: float = 0.0):
        self.id = id or str(uuid.uuid4())
        self.task = task
        self.content = content
        self.result = result
        self.outcome = outcome
        self.duration_seconds = duration_seconds
        self.tags = list(tags or [])
        self.source_memory_ids = list(source_memory_ids or [])
        self.agent_id = agent_id
        self.created_at = created_at or time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "task": self.task, "content": self.content,
            "result": self.result, "outcome": self.outcome,
            "duration_seconds": round(self.duration_seconds, 3),
            "tags": self.tags[:20], "source_memory_ids": self.source_memory_ids[:50],
            "agent_id": self.agent_id, "created_at": round(self.created_at, 3),
        }


# ---------------------------------------------------------------------------
# 持久化存储
# ---------------------------------------------------------------------------

class SkillStore:
    """案例 + 技能模板的持久化存储（SQLite 单文件）"""

    def __init__(self, storage=None):
        self.storage = storage
        self._skills: Dict[str, SkillTemplate] = {}
        if self.storage is not None:
            self._ensure_tables()
            self._load_skills()

    # -- schema -----------------------------------------------------------

    def _ensure_tables(self) -> None:
        try:
            conn = self.storage._get_conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS experience_cases (
                    id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    content TEXT,
                    result TEXT DEFAULT '',
                    outcome TEXT DEFAULT 'success',
                    duration_seconds REAL DEFAULT 0.0,
                    tags TEXT DEFAULT '[]',
                    source_memory_ids TEXT DEFAULT '[]',
                    agent_id TEXT DEFAULT '',
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS experience_skills (
                    name TEXT PRIMARY KEY,
                    description TEXT DEFAULT '',
                    triggers TEXT DEFAULT '[]',
                    slots TEXT DEFAULT '[]',
                    steps TEXT DEFAULT '[]',
                    examples TEXT DEFAULT '[]',
                    tags TEXT DEFAULT '[]',
                    source_memory_ids TEXT DEFAULT '[]',
                    confidence REAL DEFAULT 0.0,
                    cluster_size INTEGER DEFAULT 0,
                    updated_at REAL
                );
            """)
            conn.commit()
        except sqlite3.OperationalError as _err:
            import logging
            logging.getLogger(__name__).warning("创建经验表失败: %s", _err)

    def _load_skills(self) -> None:
        """重启后从 experience_skills 表恢复内存态"""
        try:
            conn = self.storage._get_conn()
            for row in conn.execute(
                "SELECT name, description, triggers, slots, steps, examples, tags, "
                "source_memory_ids, confidence, cluster_size, updated_at "
                "FROM experience_skills"
            ):
                (name, desc, triggers, slots, steps, examples, tags,
                 source_ids, confidence, cluster_size, _updated) = row
                from modules.skill_extractor import SkillSlot
                parsed_slots = []
                for s in json.loads(slots) if slots else []:
                    try:
                        parsed_slots.append(SkillSlot(
                            name=s.get("name", ""),
                            examples=list(s.get("examples", [])),
                            required=bool(s.get("required", False)),
                            default=s.get("default"),
                        ))
                    except Exception:
                        continue
                self._skills[name] = SkillTemplate(
                    name=name, description=desc or "",
                    triggers=json.loads(triggers) if triggers else [],
                    slots=parsed_slots,
                    steps=json.loads(steps) if steps else [],
                    examples=json.loads(examples) if examples else [],
                    tags=json.loads(tags) if tags else [],
                    source_memory_ids=json.loads(source_ids) if source_ids else [],
                    confidence=float(confidence or 0.0),
                    cluster_size=int(cluster_size or 0),
                )
        except sqlite3.OperationalError as _err:
            import logging
            logging.getLogger(__name__).warning("加载技能失败: %s", _err)

    # -- cases ------------------------------------------------------------

    def record_case(self, task: str, content: str = "", result: str = "",
                    outcome: str = "success", duration_seconds: float = 0.0,
                    tags: Optional[List[str]] = None,
                    source_memory_ids: Optional[List[str]] = None,
                    agent_id: str = "") -> ExperienceCase:
        """记录一次任务经验（成功案例）"""
        outcome = str(outcome or "success").lower()
        if outcome not in ("success", "partial", "failure"):
            raise ValueError(f"无效 outcome: {outcome}（可选 success/partial/failure）")
        case = ExperienceCase(
            task=task, content=content, result=result, outcome=outcome,
            duration_seconds=max(0.0, float(duration_seconds or 0.0)),
            tags=tags, source_memory_ids=source_memory_ids, agent_id=agent_id,
        )
        if self.storage is not None:
            conn = self.storage._get_conn()
            conn.execute(
                "INSERT INTO experience_cases "
                "(id, task, content, result, outcome, duration_seconds, tags, "
                " source_memory_ids, agent_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case.id, case.task, case.content, case.result, case.outcome,
                 case.duration_seconds,
                 json.dumps(case.tags, ensure_ascii=False),
                 json.dumps(case.source_memory_ids, ensure_ascii=False),
                 case.agent_id, case.created_at))
            conn.commit()
        return case

    def list_cases(self, limit: int = 100, outcome: str = "") -> List[Dict[str, Any]]:
        if self.storage is None:
            return []
        limit = max(1, min(10000, int(limit)))
        conn = self.storage._get_conn()
        if outcome:
            rows = conn.execute(
                "SELECT id, task, content, result, outcome, duration_seconds, tags, "
                "source_memory_ids, agent_id, created_at FROM experience_cases "
                "WHERE outcome = ? ORDER BY created_at DESC LIMIT ?",
                (str(outcome), limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, task, content, result, outcome, duration_seconds, tags, "
                "source_memory_ids, agent_id, created_at FROM experience_cases "
                "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r[0], "task": r[1], "content": r[2] or "", "result": r[3] or "",
                "outcome": r[4], "duration_seconds": round(r[5] or 0.0, 3),
                "tags": json.loads(r[6]) if r[6] else [],
                "source_memory_ids": json.loads(r[7]) if r[7] else [],
                "agent_id": r[8] or "", "created_at": round(r[9] or 0.0, 3),
            })
        return out

    def delete_case(self, case_id: str) -> bool:
        if self.storage is None:
            return False
        conn = self.storage._get_conn()
        cur = conn.execute("DELETE FROM experience_cases WHERE id = ?", (case_id,))
        conn.commit()
        return cur.rowcount > 0

    # -- skills -----------------------------------------------------------

    def save_skills(self, templates: List[SkillTemplate]) -> int:
        """UPSERT 技能模板（按 name 合并 source_memory_ids）"""
        saved = 0
        if self.storage is None:
            return 0
        conn = self.storage._get_conn()
        for t in templates:
            existing = self._skills.get(t.name)
            source_ids = list(t.source_memory_ids)
            if existing:
                for sid in existing.source_memory_ids:
                    if sid not in source_ids:
                        source_ids.append(sid)
            now = time.time()
            conn.execute(
                "INSERT INTO experience_skills (name, description, triggers, slots, "
                " steps, examples, tags, source_memory_ids, confidence, cluster_size, "
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                " description=excluded.description, triggers=excluded.triggers, "
                " slots=excluded.slots, steps=excluded.steps, examples=excluded.examples, "
                " tags=excluded.tags, source_memory_ids=excluded.source_memory_ids, "
                " confidence=excluded.confidence, cluster_size=excluded.cluster_size, "
                " updated_at=excluded.updated_at",
                (t.name, t.description,
                 json.dumps(t.triggers, ensure_ascii=False),
                 json.dumps([s.to_dict() for s in t.slots], ensure_ascii=False),
                 json.dumps(t.steps, ensure_ascii=False),
                 json.dumps(t.examples, ensure_ascii=False),
                 json.dumps(t.tags, ensure_ascii=False),
                 json.dumps(source_ids, ensure_ascii=False),
                 float(t.confidence), int(t.cluster_size), now))
            self._skills[t.name] = t
            saved += 1
        conn.commit()
        return saved

    def distill(self, min_cluster_size: int = 2, max_skills: int = 50,
                outcome: str = "success") -> Dict[str, Any]:
        """从成功案例批量蒸馏技能模板并落库"""
        cases = self.list_cases(limit=10000, outcome=outcome)
        mem_dicts = []
        for c in cases:
            content = c["content"] or c["task"] or ""
            tags = list(c["tags"]) or []
            if c["task"] and c["task"] != content:
                content = c["task"] + "：" + content
            if c["outcome"] == "success":
                tags = list(set(tags) | {"success"})
            mem_dicts.append({
                "id": c["id"], "content": content, "tags": tags,
                "category": "experience",
            })
        extractor = SkillExtractor(min_cluster_size=max(1, int(min_cluster_size)),
                                   max_skills=max_skills)
        templates = extractor.extract(mem_dicts)
        saved = self.save_skills(templates)
        return {
            "cases_processed": len(mem_dicts),
            "skills_found": len(templates),
            "skills_saved": saved,
            "skills": [t.to_dict() for t in templates[:50]],
        }

    def list_skills(self, limit: int = 50) -> List[Dict[str, Any]]:
        out = []
        for t in list(self._skills.values())[:max(1, min(1000, int(limit)))]:
            out.append(t.to_dict())
        out.sort(key=lambda s: (-s["confidence"], -s["cluster_size"]))
        return out

    def get_skill(self, name: str) -> Optional[SkillTemplate]:
        for key, t in self._skills.items():
            if key.lower() == name.lower():
                return t
        return None

    def match_skills(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """按新任务查询最匹配的已存技能（触发词/标签/bigram 三重评分）"""
        q = str(query or "").strip().lower()
        if not q:
            return []
        qn = self._ngrams(q)
        scored = []
        for t in self._skills.values():
            score = 0.0
            if any(tg.lower() in q or q in tg.lower() for tg in t.triggers):
                score += 0.6
            if any(tg.lower() in q for tg in t.tags):
                score += 0.3
            if qn:
                hay = t.description + " " + " ".join(t.triggers) + " " + t.name
                sn = self._ngrams(hay)
                score += (len(qn & sn) / max(1, len(qn))) * 0.4
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda x: (-x[0], -x[1].confidence))
        return [t.to_dict() for _, t in scored[:max(1, min(50, int(limit)))]]

    def render_skill(self, name: str, **kwargs) -> Optional[str]:
        t = self.get_skill(name)
        if t is None:
            return None
        return t.render(**kwargs)

    def stats(self) -> Dict[str, Any]:
        conn = self.storage._get_conn() if self.storage is not None else None
        case_count = 0
        skill_count = len(self._skills)
        if conn is not None:
            try:
                case_count = conn.execute(
                    "SELECT COUNT(*) FROM experience_cases").fetchone()[0]
            except sqlite3.OperationalError:
                case_count = 0
        outcome_counts: Dict[str, int] = {}
        if conn is not None:
            try:
                for row in conn.execute(
                        "SELECT outcome, COUNT(*) FROM experience_cases GROUP BY outcome"):
                    outcome_counts[row[0]] = row[1]
            except sqlite3.OperationalError:
                pass
        return {
            "total_cases": int(case_count),
            "total_skills": int(skill_count),
            "outcome_counts": outcome_counts,
        }

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _ngrams(text: str, n: int = 2) -> set:
        s = re.sub(r"[\W_]+", "", text)
        if len(s) < n:
            return {s} if s else set()
        return {s[i:i + n] for i in range(len(s) - n + 1)}


__all__ = ["ExperienceCase", "SkillStore"]
