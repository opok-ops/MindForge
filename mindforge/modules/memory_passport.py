"""
MindForge v6.0.0 功能 6：记忆护照（跨模型迁移）

花半年教会一个 AI 的记忆，一次授权、记忆跟你走、随时可撤销。

- export_passport(): 导出结构化记忆护照（schema + 记忆 + 偏好 + 技能）
- import_passport(): 导入护照（merge/replace），标记来源 passport_id
- revoke_passport(): 撤销导入——删除来源标记的记忆，可逆
- passport_status(): 当前护照导入来源状态

安全约束：
- 护照含 schema 标识 mindforge-memory-passport/1.0 与 passport_id
- 导入时校验格式；单条失败不中断
- revoke 只删除带该 passport_id 来源标记的记忆（不动用户已有数据）
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

PASSPORT_FORMAT = "mindforge-memory-passport"
FORMAT_VERSION = 1
MAX_MEMORIES = 10000
MAX_PREFERENCES = 500

# 写入 memories 时用于标记来源的 metadata key
_PASSPORT_KEY = "passport_id"


def _ensure_columns(conn) -> None:
    try:
        conn.execute("ALTER TABLE memories ADD COLUMN passport_id TEXT DEFAULT ''")
    except Exception:
        pass


def _collect_memories(mf, max_memories: int) -> List[Dict[str, Any]]:
    """收集记忆（不含 STRICT 隐私；不含明文之外的敏感字段）"""
    out = []
    try:
        conn = mf._storage._get_conn()
        rows = conn.execute(
            "SELECT id, content, category, tags, importance, layer, privacy, "
            "source_agent, source_session, created_at, valid_from, valid_to, "
            "metadata FROM memories WHERE privacy != 'STRICT' ORDER BY created_at "
            "LIMIT ?", (max(1, min(MAX_MEMORIES, int(max_memories))),)).fetchall()
    except Exception:
        rows = []
    for r in rows:
        tags = r[3]
        try:
            tags = json.loads(tags) if tags and tags.startswith("[") else []
        except Exception:
            tags = []
        out.append({
            "id": r[0], "content": r[1], "category": r[2] or "general",
            "tags": tags, "importance": r[4] or "MEDIUM", "layer": r[5] or "short_term",
            "privacy": r[6] or "INTERNAL", "source_agent": r[7] or "",
            "source_session": r[8] or "", "created_at": r[9] or 0.0,
            "valid_from": r[10] or 0.0, "valid_to": r[11] or 0.0,
        })
    return out


def _collect_preferences(mf) -> List[Dict[str, Any]]:
    """收集偏好类记忆（category=preference 或带 user_preference 标签）"""
    prefs = []
    for m in _collect_memories(mf, MAX_MEMORIES):
        if (m["category"] == "preference"
                or any("user_preference" in (t or "") for t in m["tags"])):
            prefs.append({
                "content": m["content"], "tags": m["tags"][:10],
                "importance": m["importance"], "created_at": m["created_at"],
            })
        if len(prefs) >= MAX_PREFERENCES:
            break
    return prefs


def export_passport(mf, include_skills: bool = True,
                    include_preferences: bool = True,
                    max_memories: int = 5000) -> Dict[str, Any]:
    """导出记忆护照"""
    passport_id = f"passport_{uuid.uuid4().hex[:12]}"
    memories = _collect_memories(mf, max_memories)
    passport = {
        "format": PASSPORT_FORMAT,
        "format_version": FORMAT_VERSION,
        "passport_id": passport_id,
        "exported_at": round(time.time(), 3),
        "counts": {"memories": len(memories)},
        "memories": memories,
    }
    if include_preferences:
        prefs = _collect_preferences(mf)
        passport["preferences"] = prefs
        passport["counts"]["preferences"] = len(prefs)
    if include_skills:
        try:
            skills = mf.skill_list(limit=200)
            passport["skills"] = skills
            passport["counts"]["skills"] = len(skills)
        except Exception:
            passport["skills"] = []
    return {"success": True, "passport_id": passport_id,
            "passport": passport, "counts": passport["counts"]}


def _validate(passport: Any) -> Dict[str, Any]:
    errors = []
    if not isinstance(passport, dict):
        return {"valid": False, "errors": ["护照必须是 JSON 对象"]}
    if passport.get("format") != PASSPORT_FORMAT:
        errors.append("格式标识错误")
    if passport.get("format_version") != FORMAT_VERSION:
        errors.append("版本不支持")
    if not passport.get("passport_id"):
        errors.append("缺少 passport_id")
    mems = passport.get("memories")
    if not isinstance(mems, list):
        errors.append("缺少 memories 列表")
    elif len(mems) > MAX_MEMORIES:
        errors.append(f"记忆数超过上限 {MAX_MEMORIES}")
    return {"valid": not errors, "errors": errors}


def import_passport(mf, passport: Any, mode: str = "merge",
                    actor: str = "", session_id: str = "") -> Dict[str, Any]:
    """导入记忆护照。mode: merge（合并）/ replace（先删旧来源再写）"""
    v = _validate(passport)
    if not v["valid"]:
        return {"success": False, "errors": v["errors"]}
    passport_id = passport["passport_id"]

    conn = mf._storage._get_conn()
    _ensure_columns(conn)

    if mode == "replace":
        # 先删除此前从该护照导入的记忆
        conn.execute("DELETE FROM memories WHERE passport_id = ?", (passport_id,))
        conn.commit()

    imported = 0
    skipped = 0
    for m in (passport.get("memories") or [])[:MAX_MEMORIES]:
        if not isinstance(m, dict):
            continue
        content = (m.get("content") or "").strip()
        if not content:
            skipped += 1
            continue
        try:
            meta = json.loads(m.get("metadata", "{}")) if isinstance(m.get("metadata"), str) else dict(m.get("metadata") or {})
        except Exception:
            meta = {}
        meta[_PASSPORT_KEY] = passport_id
        try:
            entry = mf.add(
                content=content,
                category=m.get("category") or "general",
                tags=list(m.get("tags", []) or [])[:20],
                importance=m.get("importance") or "MEDIUM",
                layer=m.get("layer") or "short_term",
                privacy=m.get("privacy") or "INTERNAL",
                source_agent=m.get("source_agent") or f"passport:{passport_id}",
                source_session=m.get("source_session") or "",
                metadata=meta,
            )
            try:
                conn.execute("UPDATE memories SET passport_id = ? WHERE id = ?",
                             (passport_id, entry.id))
                conn.commit()
            except Exception:
                pass
            imported += 1
        except Exception:
            skipped += 1
    # 偏好导入（合并为 preference 记忆）
    pref_imported = 0
    for p in (passport.get("preferences") or [])[:MAX_PREFERENCES]:
        if not isinstance(p, dict):
            continue
        try:
            entry = mf.add(
                content=(p.get("content") or "").strip() or "偏好",
                category="preference",
                tags=list(set((p.get("tags") or [])[:10]) | {"user_preference"}),
                importance=p.get("importance") or "HIGH",
                metadata={_PASSPORT_KEY: passport_id},
            )
            try:
                conn.execute("UPDATE memories SET passport_id = ? WHERE id = ?",
                             (passport_id, entry.id))
                conn.commit()
            except Exception:
                pass
            pref_imported += 1
        except Exception:
            pass
    mf._storage._add_audit(
        "passport_import", "", actor or "", session_id or "", "",
        {"passport_id": passport_id, "memories": imported, "prefs": pref_imported})
    return {"success": True, "passport_id": passport_id,
            "memories_imported": imported, "preferences_imported": pref_imported,
            "skipped": skipped}


def revoke_passport(mf, passport_id: str, actor: str = "",
                    session_id: str = "") -> Dict[str, Any]:
    """撤销护照导入：删除该护照来源的记忆与偏好，恢复原状。"""
    if not passport_id or len(passport_id) > 128:
        return {"success": False, "error": "passport_id 无效"}
    conn = mf._storage._get_conn()
    _ensure_columns(conn)
    cur = conn.execute("DELETE FROM memories WHERE passport_id = ?", (passport_id,))
    conn.commit()
    removed = cur.rowcount
    mf._storage._add_audit(
        "passport_revoke", "", actor or "", session_id or "", "",
        {"passport_id": passport_id, "removed": removed})
    return {"success": True, "passport_id": passport_id, "memories_removed": removed}


def passport_status(mf) -> Dict[str, Any]:
    """护照来源状态：列出已导入的护照及其记忆数"""
    conn = mf._storage._get_conn()
    _ensure_columns(conn)
    try:
        rows = conn.execute(
            "SELECT passport_id, COUNT(*) FROM memories "
            "WHERE passport_id != '' GROUP BY passport_id").fetchall()
    except Exception:
        rows = []
    sources = [{"passport_id": r[0], "memories": r[1]} for r in rows]
    return {"sources": sources, "total_sources": len(sources),
            "total_imported": sum(r[1] for r in rows)}
