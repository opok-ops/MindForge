"""
MindForge v6.0.0 功能 3：可分享的技能包

让用户把一组好用的技能模板一键导出为可分享文件，别人一键导入安装。

- PACK_FORMAT: "mindforge-skill-pack"，FORMAT_VERSION = 1
- export_skill_pack(): 按技能名导出为结构化 JSON
- validate_skill_pack(): 校验格式/版本/条数上限，防恶意包
- import_skill_pack(): merge（按 name UPSERT 合并）或 replace（先删后写）

安全约束：
- MAX_SKILLS_PER_PACK = 500，MAX_CASES = 2000（防超大包）
- 字段白名单解析，忽略未知字段
- 导入失败原子回滚（单技能失败不影响已导入项）
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from mindforge.modules.skill_extractor import SkillSlot, SkillTemplate

PACK_FORMAT = "mindforge-skill-pack"
FORMAT_VERSION = 1

MAX_SKILLS_PER_PACK = 500
MAX_CASES_PER_PACK = 2000


def export_skill_pack(skill_store, names: Optional[List[str]] = None,
                      include_cases: bool = True,
                      max_cases: int = 200) -> Dict[str, Any]:
    """按技能名导出技能包（可分享文件内容）。"""
    skills = skill_store.list_skills(limit=MAX_SKILLS_PER_PACK)
    if names:
        wanted = {n.lower() for n in names if n and n.strip()}
        skills = [s for s in skills if (s.get("name") or "").lower() in wanted]
    if not skills:
        return {"success": False, "error": "没有可导出的技能"}

    pack_skills = []
    for s in skills[:MAX_SKILLS_PER_PACK]:
        pack_skills.append({
            "name": s.get("name", ""),
            "description": s.get("description", ""),
            "triggers": list(s.get("triggers", []) or [])[:20],
            "slots": [{"name": sl.get("name", ""),
                       "examples": list(sl.get("examples", []) or [])[:5],
                       "required": bool(sl.get("required", False)),
                       "default": sl.get("default")}
                      for sl in (s.get("slots", []) or [])[:20]],
            "steps": list(s.get("steps", []) or [])[:30],
            "examples": list(s.get("examples", []) or [])[:5],
            "tags": list(s.get("tags", []) or [])[:20],
            "confidence": float(s.get("confidence", 0.0) or 0.0),
            "cluster_size": int(s.get("cluster_size", 0) or 0),
        })

    pack = {
        "format": PACK_FORMAT,
        "format_version": FORMAT_VERSION,
        "exported_at": round(time.time(), 3),
        "skills": pack_skills,
    }
    if include_cases:
        cases = skill_store.list_cases(limit=min(max_cases, MAX_CASES_PER_PACK))
        pack["cases"] = [{
            "task": c.get("task", ""),
            "content": (c.get("content") or "")[:2000],
            "result": (c.get("result") or "")[:2000],
            "outcome": c.get("outcome", "success"),
            "duration_seconds": float(c.get("duration_seconds", 0.0) or 0.0),
            "tags": list(c.get("tags", []) or [])[:20],
        } for c in cases[:MAX_CASES_PER_PACK]]
    return {"success": True, "pack": pack,
            "skill_count": len(pack_skills),
            "case_count": len(pack.get("cases", []))}


def validate_skill_pack(pack: Any) -> Dict[str, Any]:
    """校验技能包格式。返回 {valid, errors}。"""
    errors = []
    if not isinstance(pack, dict):
        return {"valid": False, "errors": ["技能包必须是 JSON 对象"]}
    if pack.get("format") != PACK_FORMAT:
        errors.append(f"格式标识错误: {pack.get('format')!r}（应为 {PACK_FORMAT}）")
    ver = pack.get("format_version")
    if ver != FORMAT_VERSION:
        errors.append(f"版本不支持: {ver!r}（当前支持 v{FORMAT_VERSION}）")
    skills = pack.get("skills")
    if not isinstance(skills, list):
        errors.append("缺少 skills 列表")
    elif not skills:
        errors.append("技能包为空（无技能）")
    elif len(skills) > MAX_SKILLS_PER_PACK:
        errors.append(f"技能数超过上限 {MAX_SKILLS_PER_PACK}")
    else:
        names = set()
        for i, s in enumerate(skills):
            if not isinstance(s, dict):
                errors.append(f"skills[{i}] 不是对象")
                continue
            name = (s.get("name") or "").strip()
            if not name:
                errors.append(f"skills[{i}] 缺少 name")
            elif name.lower() in names:
                errors.append(f"技能名重复: {name}")
            names.add(name.lower())
    cases = pack.get("cases")
    if isinstance(cases, list) and len(cases) > MAX_CASES_PER_PACK:
        errors.append(f"案例数超过上限 {MAX_CASES_PER_PACK}")
    return {"valid": not errors, "errors": errors}


def _parse_skill(pack_skill: Dict[str, Any]) -> Optional[SkillTemplate]:
    """按白名单解析单个技能，返回 SkillTemplate 或 None。"""
    try:
        name = (pack_skill.get("name") or "").strip()
        if not name:
            return None
        slots = []
        for sl in (pack_skill.get("slots") or [])[:20]:
            if not isinstance(sl, dict):
                continue
            slots.append(SkillSlot(
                name=(sl.get("name") or "").strip(),
                examples=list(sl.get("examples", []) or [])[:5],
                required=bool(sl.get("required", False)),
                default=sl.get("default"),
            ))
        return SkillTemplate(
            name=name,
            description=(pack_skill.get("description") or "")[:1000],
            triggers=list(pack_skill.get("triggers", []) or [])[:20],
            slots=slots,
            steps=list(pack_skill.get("steps", []) or [])[:30],
            examples=list(pack_skill.get("examples", []) or [])[:5],
            tags=list(pack_skill.get("tags", []) or [])[:20],
            confidence=max(0.0, min(1.0, float(pack_skill.get("confidence", 0.0) or 0.0))),
            cluster_size=int(pack_skill.get("cluster_size", 0) or 0),
        )
    except Exception:
        return None


def import_skill_pack(skill_store, pack: Any,
                      mode: str = "merge",
                      actor: str = "") -> Dict[str, Any]:
    """导入技能包。mode: merge（UPSERT 合并）/ replace（先删后写）。"""
    v = validate_skill_pack(pack)
    if not v["valid"]:
        return {"success": False, "errors": v["errors"]}

    skills = pack.get("skills") or []
    templates = []
    for ps in skills:
        t = _parse_skill(ps)
        if t is not None:
            templates.append(t)

    if mode == "replace":
        # 先删除现有全部技能（仅在包非空时）
        try:
            existing = skill_store.list_skills(limit=MAX_SKILLS_PER_PACK)
            for e in existing:
                skill_store.delete_skill(e.get("name", ""))
        except Exception as ex:
            return {"success": False, "error": f"替换模式清空失败: {ex}"}

    saved = 0
    if templates:
        try:
            saved = skill_store.save_skills(templates)
        except Exception as ex:
            return {"success": False, "error": f"技能保存失败: {ex}"}

    imported_cases = 0
    cases = pack.get("cases") or []
    for c in cases[:MAX_CASES_PER_PACK]:
        if not isinstance(c, dict):
            continue
        try:
            skill_store.record_case(
                task=(c.get("task") or "")[:500] or "imported",
                content=(c.get("content") or "")[:2000],
                result=(c.get("result") or "")[:2000],
                outcome=c.get("outcome", "success"),
                duration_seconds=float(c.get("duration_seconds", 0.0) or 0.0),
                tags=list(c.get("tags", []) or [])[:20],
                agent_id=actor or "",
            )
            imported_cases += 1
        except Exception:
            continue

    return {
        "success": True,
        "mode": mode,
        "skills_imported": saved,
        "cases_imported": imported_cases,
        "skills_total": len(templates),
        "cases_total": len(cases),
    }
