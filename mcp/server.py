"""MindForge MCP Server — Model Context Protocol (stdio transport).

Implements MCP 2024-11-05 over stdio JSON-RPC.

Launch options:
  python -m MindForge.mcp.server --db-path ./memory.db
  MindForge-mcp --db-path ./memory.db
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow running without editable install: add parent of mcp/ to sys.path
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

try:
    from MindForge import __version__
except ImportError:
    __version__ = "5.6.1"


def _import_mindforge():
    """Import MindForge package with fallback for non-editable / direct runs."""
    try:
        import MindForge  # noqa: F401
        return sys.modules["MindForge"]
    except ImportError:
        # Running as bare package (not installed as MindForge) —
        # bootstrap a MindForge alias using the top-level __init__.py.
        import importlib.util
        init_py = _PKG_ROOT / "__init__.py"
        if not init_py.exists():
            raise
        spec = importlib.util.spec_from_file_location(
            "MindForge", str(init_py), submodule_search_locations=[str(_PKG_ROOT)]
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["MindForge"] = mod
        spec.loader.exec_module(mod)
        # Eagerly import known submodules so `from MindForge import X` works
        for _sub in ("core", "core.types", "core.storage", "core.mindforge",
                     "core.encryption", "core.indexer", "core.query",
                     "modules", "modules.recall", "modules.integrator",
                     "modules.knowledge_graph", "modules.personality",
                     "modules.federated", "modules.privacy", "modules.multimodal",
                     "modules.evolution", "modules.categorizer",
                     "modules.intent_router", "modules.conflict_detector",
                     "modules.skill_extractor", "modules.hybrid_search",
                     "modules.session_focus",
                     "adapters", "cli", "cli.main", "mcp", "mcp.server"):
            try:
                __import__("MindForge." + _sub)
            except Exception:
                pass
        return mod


_import_mindforge()

# ---------------------------------------------------------------------------
# MCP transport — Content-Length framed JSON-RPC over stdio
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    """Debug output goes to stderr; never write to stdout."""
    sys.stderr.write(f"[mcp-mindforge] {msg}\n")
    sys.stderr.flush()


def _read_message() -> Dict[str, Any]:
    headers: Dict[str, str] = {}
    # v5.6.2 安全修复：消息体大小上限（与 REST API 一致，10MB）
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    while True:
        line = sys.stdin.buffer.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
        try:
            decoded = line.decode("ascii", errors="replace").strip()
        except Exception:
            continue
        if ":" in decoded:
            k, v = decoded.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError:
        length = 0
    # v5.6.2 安全修复：拒绝超大消息体，防止内存耗尽 DoS
    if length > MAX_CONTENT_LENGTH:
        raise ValueError(f"Content-Length {length} exceeds maximum {MAX_CONTENT_LENGTH}")
    if length <= 0:
        raw = sys.stdin.buffer.readline()
    else:
        raw = sys.stdin.buffer.read(length)
    if not raw:
        raise EOFError("stdin closed")
    return json.loads(raw.decode("utf-8"))


def _write_message(payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _respond(request: Dict[str, Any], result: Any) -> None:
    _write_message({"jsonrpc": "2.0", "id": request.get("id"), "result": result})


def _respond_error(request: Dict[str, Any], code: int, message: str, data: Any = None) -> None:
    body: Dict[str, Any] = {"jsonrpc": "2.0", "id": request.get("id"),
                            "error": {"code": code, "message": message}}
    if data is not None:
        body["error"]["data"] = data
    _write_message(body)


def _notify(method: str, params: Optional[Dict[str, Any]] = None) -> None:
    msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    _write_message(msg)


# ---------------------------------------------------------------------------
# Tool schemas — matches MCP tools/list
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "memory_add",
        "description": "Add a new memory entry to MindForge lifelong memory system.",
        "inputSchema": {
            "type": "object",
            "required": ["content"],
            "properties": {
                "content": {"type": "string", "description": "记忆正文内容"},
                "category": {"type": "string", "description": "分类，默认 general"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                "importance": {"type": "string", "enum": ["critical", "high", "medium", "low"],
                              "description": "重要性级别，默认 medium"},
                "layer": {"type": "string", "enum": ["sensory", "short_term", "long_term", "permanent"],
                         "description": "记忆层级，默认 short_term"},
                "agent_id": {"type": "string", "description": "关联 Agent ID（可选）"},
            },
        },
    },
    {
        "name": "memory_search",
        "description": "Hybrid search: vector recall + TF-IDF + fuzzy (v5.4.5). Set use_embedding=false to disable vector search.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
                "min_relevance": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0},
                "category": {"type": "string", "description": "按分类过滤（可选）"},
                "agent_id": {"type": "string", "description": "按 Agent ID 过滤（可选）"},
                "use_embedding": {"type": "boolean", "default": True, "description": "启用向量召回（v5.4.5，默认 true）"},
            },
        },
    },
    {
        "name": "memory_list",
        "description": "分页列出记忆条目，支持按分类/层级/重要性过滤和排序。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "layer": {"type": "string", "enum": ["sensory", "short_term", "long_term", "permanent"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "sort_by": {"type": "string", "enum": ["created_at", "updated_at", "importance"],
                           "default": "created_at"},
            },
        },
    },
    {
        "name": "memory_stats",
        "description": "记忆库统计：总数、分类、层级、重要性、标签数量等。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_importance",
        "description": "Agent 记忆重要度分析：分布、漂移、低估/高估与重评估建议。",
        "inputSchema": {
            "type": "object",
            "required": ["agent_id"],
            "properties": {"agent_id": {"type": "string"}},
        },
    },
    {
        "name": "memory_context",
        "description": "为 LLM Prompt 格式化上下文注入（Token 预算感知）。",
        "inputSchema": {
            "type": "object",
            "required": ["agent_id", "query"],
            "properties": {
                "agent_id": {"type": "string"},
                "query": {"type": "string"},
                "token_budget": {"type": "integer", "minimum": 256, "maximum": 128000, "default": 4000},
            },
        },
    },
    {
        "name": "agent_emotion",
        "description": "Agent 情感追踪：每日情感、转换序列、波动性评分。",
        "inputSchema": {
            "type": "object",
            "required": ["agent_id"],
            "properties": {
                "agent_id": {"type": "string"},
                "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
            },
        },
    },
    {
        "name": "char_relationship",
        "description": "短剧角色关系分析：六型分类（ally/rival/romance/family/mentor/stranger）+ 情感弧线 + 强度。需同时提供 char1_id 和 char2_id。",
        "inputSchema": {
            "type": "object",
            "required": ["drama_id"],
            "properties": {
                "drama_id": {"type": "string"},
                "char1_id": {"type": "string", "description": "角色 1 ID（可选，用于特定角色关系分析）"},
                "char2_id": {"type": "string", "description": "角色 2 ID（可选，用于特定角色关系分析）"},
            },
        },
    },
    {
        "name": "drama_genre_trend",
        "description": "短剧类型趋势：rising / declining / stable + 平均评分。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "drama_binge_score",
        "description": "短剧追剧粘性评分（节奏/张力/互动/经典/完成率）。",
        "inputSchema": {
            "type": "object",
            "required": ["drama_id"],
            "properties": {"drama_id": {"type": "string"}},
        },
    },
    # ===== v5.4.1 新增六大能力 =====
    {
        "name": "memory_reflection",
        "description": "记忆反思（元认知）：主题分布、情感基调、关键经验、焦点漂移与结构化反思报告。",
        "inputSchema": {
            "type": "object",
            "required": ["agent_id"],
            "properties": {
                "agent_id": {"type": "string"},
                "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
            },
        },
    },
    {
        "name": "memory_lineage",
        "description": "记忆血缘溯源：单条记忆的版本历史、关联链接、审计事件与生命周期时间线。",
        "inputSchema": {
            "type": "object",
            "required": ["memory_id"],
            "properties": {"memory_id": {"type": "string"}},
        },
    },
    {
        "name": "memory_reinforce",
        "description": "记忆强化候选：识别高价值但正在衰减的记忆，给出强化排序与推荐动作。",
        "inputSchema": {
            "type": "object",
            "required": ["agent_id"],
            "properties": {
                "agent_id": {"type": "string"},
                "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 90},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
        },
    },
    {
        "name": "drama_plot_thread",
        "description": "剧情伏笔线索追踪：识别伏笔埋设与回收，输出线索列表、未回收线索与回收率。",
        "inputSchema": {
            "type": "object",
            "required": ["drama_id"],
            "properties": {"drama_id": {"type": "string"}},
        },
    },
    {
        "name": "drama_episode_curve",
        "description": "分集张力曲线：按集聚合张力指标，输出全剧曲线、高潮集、波动率与形态分类。",
        "inputSchema": {
            "type": "object",
            "required": ["drama_id"],
            "properties": {"drama_id": {"type": "string"}},
        },
    },
    {
        "name": "drama_screen_time",
        "description": "角色戏份平衡：角色台词量/字数/出场占比 + 基尼系数 + 独角戏/双核/群像结构判定。",
        "inputSchema": {
            "type": "object",
            "required": ["drama_id"],
            "properties": {"drama_id": {"type": "string"}},
        },
    },
    # ===== v5.4.2 新增两大能力 =====
    {
        "name": "fed_acl_add",
        "description": "联邦记忆细粒度 ACL：添加规则（主体×资源×操作×allow/deny，支持优先级/信任阈值/过期）。",
        "inputSchema": {
            "type": "object",
            "required": ["principal", "resource"],
            "properties": {
                "principal": {"type": "string", "description": "peer ID 或 * 表示任意节点"},
                "resource": {"type": "string", "description": "all / memory:<id> / category:<名> / tag:<名>"},
                "operations": {"type": "string", "default": "read", "description": "read/write/reshare/*，逗号分隔"},
                "effect": {"type": "string", "enum": ["allow", "deny"], "default": "allow"},
                "priority": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 100},
                "trust_min": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
                "expires_hours": {"type": "number", "description": "规则有效时长（小时），缺省永久"},
                "note": {"type": "string"},
            },
        },
    },
    {
        "name": "fed_acl_remove",
        "description": "联邦记忆细粒度 ACL：删除规则。",
        "inputSchema": {
            "type": "object",
            "required": ["rule_id"],
            "properties": {"rule_id": {"type": "string"}},
        },
    },
    {
        "name": "fed_acl_list",
        "description": "联邦记忆细粒度 ACL：规则列表（可按主体/效果过滤）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "principal": {"type": "string"},
                "effect": {"type": "string", "enum": ["allow", "deny"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
            },
        },
    },
    {
        "name": "fed_acl_check",
        "description": "联邦记忆细粒度 ACL：访问评估（默认拒绝；返回 allowed/effect/reason/matched_rule）。",
        "inputSchema": {
            "type": "object",
            "required": ["peer_id", "memory_id"],
            "properties": {
                "peer_id": {"type": "string"},
                "memory_id": {"type": "string"},
                "operation": {"type": "string", "enum": ["read", "write", "reshare"], "default": "read"},
                "peer_trust": {"type": "number", "minimum": 0, "maximum": 1},
                "memory_category": {"type": "string"},
                "memory_tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "fed_acl_stats",
        "description": "联邦记忆细粒度 ACL：规则分布与拒绝审计统计。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "share_conflict_list",
        "description": "共享记忆冲突：冲突记录列表（可按状态过滤 open/resolved/dismissed）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "resolved", "dismissed"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
        },
    },
    {
        "name": "share_conflict_resolve",
        "description": "共享记忆冲突：按策略解决 open 冲突（lww=版本+时间戳决胜覆盖；keep_both=另存分支并关联）。",
        "inputSchema": {
            "type": "object",
            "required": ["conflict_id", "strategy"],
            "properties": {
                "conflict_id": {"type": "string"},
                "strategy": {"type": "string", "enum": ["lww", "keep_both"]},
                "actor": {"type": "string"},
            },
        },
    },
    {
        "name": "share_conflict_dismiss",
        "description": "共享记忆冲突：人工关闭 open 冲突（判定无需处理）。",
        "inputSchema": {
            "type": "object",
            "required": ["conflict_id"],
            "properties": {
                "conflict_id": {"type": "string"},
                "actor": {"type": "string"},
            },
        },
    },
    {
        "name": "share_conflict_stats",
        "description": "共享记忆冲突：冲突态势统计（按状态/类型/解决方式）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ===== v5.3.9 新增五大能力 =====
    {
        "name": "intent_router",
        "description": "意图分类路由（规则正则→关键词加权→LLM 兜底）。返回 intent / label / confidence / routing_target。",
        "inputSchema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "description": "要分类的文本"},
                "force": {"type": "string", "description": "强制指定意图 ID（debug 用）"},
            },
        },
    },
    {
        "name": "conflict_scan",
        "description": "记忆矛盾扫描：反义词对 / 属性值不一致 / 时间线冲突，并可自动衰减（降重要性 + 打标签）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "只扫描指定分类"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
                "apply_decay": {"type": "boolean", "default": False,
                                "description": "若 true，直接写入衰减动作"},
            },
        },
    },
    {
        "name": "skill_extract",
        "description": "从记忆中抽取可复用的技能模板（聚类→槽位→步骤→触发词）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 2000},
                "min_cluster_size": {"type": "integer", "minimum": 1, "default": 2},
            },
        },
    },
    {
        "name": "rerank_search",
        "description": "混合检索增强：同义词/上位词查询扩展 + 三路召回 + Cross-Encoder 重排。",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "expand": {"type": "boolean", "default": True},
                "rerank": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "session_focus",
        "description": "会话焦点分析：主题聚类、焦点词、漂移检测、可生成增强查询。",
        "inputSchema": {
            "type": "object",
            "required": ["messages"],
            "properties": {
                "messages": {
                    "type": "array",
                    "description": '每条为 {"id","role","content","timestamp?"} 或 {"role":"user","content":"..."}',
                    "items": {"type": "object"},
                },
                "window_size": {"type": "integer", "minimum": 5, "maximum": 400, "default": 40},
                "augment_query": {"type": "string", "description": "若提供，生成带焦点的增强查询"},
            },
        },
    },
    # v5.4.5 新增向量检索工具
    {
        "name": "rebuild_embeddings",
        "description": "重建所有记忆的嵌入向量（需安装 sentence-transformers）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_size": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
            },
        },
    },
    {
        "name": "embedding_status",
        "description": "查看嵌入向量引擎状态：是否可用、模型名、维度、向量数量。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_diff",
        "description": "对比两个记忆版本的差异（v5.5.8 新增）。返回内容、分类、标签和重要度的结构化差异报告。",
        "inputSchema": {
            "type": "object",
            "required": ["version_a", "version_b"],
            "properties": {
                "version_a": {"type": "string", "description": "版本 A 的 ID"},
                "version_b": {"type": "string", "description": "版本 B 的 ID"},
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Enum helpers
# ---------------------------------------------------------------------------

def _to_importance(s: Optional[str], default):
    from MindForge import Importance
    if not s:
        return default
    return Importance.from_string(s)


def _to_layer(s: Optional[str], default):
    from MindForge import MemoryLayer
    if not s:
        return default
    return MemoryLayer.from_string(s)


def _to_list_str(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [t.strip() for t in v.split(",") if t.strip()]
    return [str(x) for x in v]


# v5.5.8: 必需参数校验装饰器
def _require_args(args: Dict[str, Any], *required: str) -> Optional[Dict[str, Any]]:
    """检查必需参数是否存在，返回 error dict 或 None（校验通过）"""
    missing = [k for k in required if k not in args or args[k] is None or args[k] == ""]
    if missing:
        return {"ok": False, "error": f"Missing required parameter(s): {', '.join(missing)}"}
    return None


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _safe_int(v: Any, default: int = 0, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    """Safely coerce arbitrary input to int, with optional range clamping.

    Returns *default* for None / empty / non-numeric strings instead of raising.
    """
    if v is None or v == "":
        return default
    try:
        iv = int(v)
    except (ValueError, TypeError):
        return default
    if lo is not None and iv < lo:
        iv = lo
    if hi is not None and iv > hi:
        iv = hi
    return iv


def _safe_float(v: Any, default: float = 0.0, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    """Safely coerce arbitrary input to float, with optional range clamping.

    v5.6.2 安全修复：拒绝 inf/nan 等非法值，防止异常输入直达底层。
    """
    if v is None or v == "":
        return default
    try:
        fv = float(v)
    except (ValueError, TypeError):
        return default
    import math
    if math.isnan(fv) or math.isinf(fv):
        return default
    if lo is not None and fv < lo:
        fv = lo
    if hi is not None and fv > hi:
        fv = hi
    return fv


def _clean(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_clean(x) for x in v]
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    if hasattr(v, "value"):
        return str(v.value)
    return v


def _entry_to_dict(m) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "id": str(m.id),
        "content": str(m.content),
        "category": str(m.category),
        "tags": list(m.tags) if m.tags else [],
        "importance": _clean(m.importance),
        "layer": _clean(m.layer),
        "agent_id": str(getattr(m, "source_agent", "") or ""),
        "created_at": None,
        "updated_at": None,
        "starred": bool(getattr(m, "starred", False)),
        "pinned": bool(getattr(m, "pinned", False)),
    }
    ca = getattr(m, "created_at", None)
    ua = getattr(m, "updated_at", None)
    if ca is not None:
        try:
            d["created_at"] = ca.isoformat()
        except Exception:
            d["created_at"] = str(ca)
    if ua is not None:
        try:
            d["updated_at"] = ua.isoformat()
        except Exception:
            d["updated_at"] = str(ua)
    return d


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def h_memory_add(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    from MindForge import Importance, MemoryLayer, PrivacyLevel
    err = _require_args(args, "content")
    if err:
        return err
    try:
        entry = mf.add(
            content=str(args["content"]),
            category=args.get("category") or "general",
            tags=_to_list_str(args.get("tags")),
            importance=_to_importance(args.get("importance"), Importance.MEDIUM),
            layer=_to_layer(args.get("layer"), MemoryLayer.SHORT_TERM),
            privacy=PrivacyLevel.INTERNAL,
            source_agent=args.get("agent_id") or "",
        )
        return {"ok": True, "memory": _entry_to_dict(entry)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


def h_memory_search(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "query")
    if err:
        return err
    categories = [args["category"]] if args.get("category") else None
    use_emb = args.get("use_embedding", True)
    if not isinstance(use_emb, bool):
        use_emb = True
    res = mf.search(
        query=str(args["query"]),
        max_results=_safe_int(args.get("max_results", 5), 5, 1, 100),
        min_relevance=_safe_float(args.get("min_relevance", 0.0), 0.0, 0.0, 1.0),
        categories=categories,
        agent_id=args.get("agent_id") or "",
        use_embedding=use_emb,
    )
    items = []
    for c in res.chunks:
        items.append({
            "relevance_score": float(c.relevance_score),
            "memory_id": str(c.memory_id),
            "content": str(c.content),
            "category": str(c.category),
            "tags": list(getattr(c, "tags", None) or []),
            "layer": _clean(getattr(c, "layer", None)),
        })
    return {"count": len(items), "results": items}


def h_memory_list(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    entries = mf.list(
        category=args.get("category") or None,
        layer=_to_layer(args.get("layer"), None),
        limit=_safe_int(args.get("limit", 20), 20, 1, 10000),
        offset=_safe_int(args.get("offset", 0), 0, 0),
        sort_by=str(args.get("sort_by", "created_at")),
    )
    return {"count": len(entries), "results": [_entry_to_dict(e) for e in entries]}


def h_memory_stats(mf, _args: Dict[str, Any]) -> Dict[str, Any]:
    s = mf.stats()
    return _clean(s)


def h_memory_importance(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "agent_id")
    if err:
        return err
    agent_id = str(args["agent_id"])
    return {"agent_id": agent_id, "result": _clean(mf.storage.memory_importance(agent_id))}


def h_memory_context(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "agent_id", "query")
    if err:
        return err
    agent_id = str(args["agent_id"])
    query = str(args["query"])
    token_budget = _safe_int(args.get("token_budget", 4000), 4000, 256, 128000)
    return {
        "agent_id": agent_id,
        "query": query,
        "token_budget": token_budget,
        "result": _clean(mf.storage.memory_context(agent_id, query, max_tokens=token_budget)),
    }


def h_agent_emotion(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "agent_id")
    if err:
        return err
    agent_id = str(args["agent_id"])
    days = _safe_int(args.get("days", 30), 30, 1, 3650)
    return {"agent_id": agent_id, "days": days,
            "result": _clean(mf.storage.agent_emotion(agent_id, days=days))}


def h_char_relationship(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "drama_id")
    if err:
        return err
    drama_id = str(args["drama_id"])
    char1_id = str(args.get("char1_id") or "")
    char2_id = str(args.get("char2_id") or "")
    if not char1_id or not char2_id:
        return {
            "drama_id": drama_id,
            "error": "char_relationship 需要同时提供 char1_id 和 char2_id",
            "hint": "传 char1_id + char2_id 可分析特定角色关系；仅传 drama_id 不足以完成分析",
        }
    result = mf.storage.char_relationship(drama_id, char1_id, char2_id)
    return {"drama_id": drama_id, "char1_id": char1_id, "char2_id": char2_id,
            "result": _clean(result)}


def h_drama_genre_trend(mf, _args: Dict[str, Any]) -> Dict[str, Any]:
    return {"result": _clean(mf.storage.drama_genre_trend())}


def h_drama_binge_score(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "drama_id")
    if err:
        return err
    drama_id = str(args["drama_id"])
    return {"drama_id": drama_id, "result": _clean(mf.storage.drama_binge_score(drama_id))}


# ===== v5.4.1 六大能力 MCP handlers =====

def h_memory_reflection(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "agent_id")
    if err:
        return err
    agent_id = str(args["agent_id"])
    days = _safe_int(args.get("days", 30), 30, 1, 365)
    return {"agent_id": agent_id, "days": days,
            "result": _clean(mf.storage.memory_reflection(agent_id, days))}


def h_memory_lineage(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "memory_id")
    if err:
        return err
    memory_id = str(args["memory_id"])
    return {"memory_id": memory_id,
            "result": _clean(mf.storage.memory_lineage(memory_id))}


def h_memory_reinforce(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "agent_id")
    if err:
        return err
    agent_id = str(args["agent_id"])
    days = _safe_int(args.get("days", 90), 90, 1, 365)
    limit = _safe_int(args.get("limit", 10), 10, 1, 50)
    return {"agent_id": agent_id, "days": days, "limit": limit,
            "result": _clean(mf.storage.memory_reinforce(agent_id, days, limit))}


def h_drama_plot_thread(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "drama_id")
    if err:
        return err
    drama_id = str(args["drama_id"])
    return {"drama_id": drama_id, "result": _clean(mf.storage.drama_plot_thread(drama_id))}


def h_drama_episode_curve(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "drama_id")
    if err:
        return err
    drama_id = str(args["drama_id"])
    return {"drama_id": drama_id, "result": _clean(mf.storage.drama_episode_curve(drama_id))}


def h_drama_screen_time(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "drama_id")
    if err:
        return err
    drama_id = str(args["drama_id"])
    return {"drama_id": drama_id, "result": _clean(mf.storage.drama_screen_time(drama_id))}


# ===== v5.4.2 两大能力 MCP handlers =====

def h_fed_acl_add(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "principal", "resource")
    if err:
        return err
    expires_hours = args.get("expires_hours")
    try:
        expires_hours = float(expires_hours) if expires_hours is not None else None
    except (TypeError, ValueError):
        expires_hours = None
    result = mf.federated_acl.add_rule(
        principal=str(args["principal"]),
        resource=str(args["resource"]),
        operations=str(args.get("operations", "read")),
        effect=str(args.get("effect", "allow")),
        priority=_safe_int(args.get("priority", 100), 100, 0, 10000),
        trust_min=float(args.get("trust_min", 0.0) or 0.0),
        expires_hours=expires_hours,
        note=str(args.get("note", "")))
    return _clean(result)


def h_fed_acl_remove(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "rule_id")
    if err:
        return err
    return _clean(mf.federated_acl.remove_rule(str(args["rule_id"])))


def h_fed_acl_list(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    rules = mf.federated_acl.list_rules(
        principal=args.get("principal"),
        effect=args.get("effect"),
        limit=_safe_int(args.get("limit", 200), 200, 1, 1000))
    return {"count": len(rules), "rules": _clean(rules)}


def h_fed_acl_check(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "peer_id", "memory_id")
    if err:
        return err
    peer_trust = args.get("peer_trust")
    try:
        peer_trust = float(peer_trust) if peer_trust is not None else None
    except (TypeError, ValueError):
        peer_trust = None
    tags = args.get("memory_tags") or []
    if isinstance(tags, str):
        tags = [tags]
    result = mf.federated_acl.check_access(
        peer_id=str(args["peer_id"]),
        memory_id=str(args["memory_id"]),
        operation=str(args.get("operation", "read")),
        peer_trust=peer_trust,
        memory_category=args.get("memory_category"),
        memory_tags=[str(t) for t in tags])
    return _clean(result)


def h_fed_acl_stats(mf, _args: Dict[str, Any]) -> Dict[str, Any]:
    return _clean(mf.federated_acl.acl_stats())


def h_share_conflict_list(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    conflicts = mf.share_conflict.list_conflicts(
        status=args.get("status"),
        limit=_safe_int(args.get("limit", 50), 50, 1, 500))
    return {"count": len(conflicts), "conflicts": _clean(conflicts)}


def h_share_conflict_resolve(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "conflict_id", "strategy")
    if err:
        return err
    return _clean(mf.share_conflict.resolve(
        str(args["conflict_id"]), str(args["strategy"]),
        actor=str(args.get("actor", ""))))


def h_share_conflict_dismiss(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "conflict_id")
    if err:
        return err
    return _clean(mf.share_conflict.dismiss(
        str(args["conflict_id"]), actor=str(args.get("actor", ""))))


def h_share_conflict_stats(mf, _args: Dict[str, Any]) -> Dict[str, Any]:
    return _clean(mf.share_conflict.stats())


# ===== v5.3.9 五大能力 MCP handlers =====

def h_intent_router(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "text")
    if err:
        return err
    result = mf.classify_intent(str(args["text"]), force=args.get("force"))
    return _clean(result)


def h_conflict_scan(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    return _clean(mf.scan_conflicts(
        category=args.get("category"),
        limit=_safe_int(args.get("limit", 500), 500, 1, 5000),
        apply_decay=bool(args.get("apply_decay", False)),
    ))


def h_skill_extract(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    return _clean(mf.extract_skills(
        category=args.get("category"),
        limit=_safe_int(args.get("limit", 2000), 2000, 1, 50000),
        min_cluster_size=max(1, _safe_int(args.get("min_cluster_size", 2), 2, 1, 100)),
    ))


def h_rerank_search(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "query")
    if err:
        return err
    return _clean(mf.search_enhanced(
        query=str(args["query"]),
        max_results=min(50, max(1, _safe_int(args.get("max_results", 10), 10, 1, 50))),
        expand=bool(args.get("expand", True)),
        rerank=bool(args.get("rerank", True)),
    ))


def h_session_focus(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    raw_msgs = args.get("messages") or []
    msgs: List[Dict[str, Any]] = []
    for i, m in enumerate(raw_msgs, 1):
        if not isinstance(m, dict):
            continue
        msgs.append({
            "id": str(m.get("id") or f"m{i}"),
            "role": str(m.get("role") or "user").lower(),
            "content": str(m.get("content") or ""),
            "timestamp": float(m.get("timestamp") or i),
        })
    return _clean(mf.session_focus(
        msgs,
        window_size=max(5, _safe_int(args.get("window_size", 40), 40, 5, 500)),
        augment_query=args.get("augment_query"),
    ))


def h_rebuild_embeddings(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    batch_size = _safe_int(args.get("batch_size", 100), 100, 1, 1000)
    return _clean(mf.rebuild_embeddings(batch_size=batch_size))


def h_embedding_status(mf, _args: Dict[str, Any]) -> Dict[str, Any]:
    return _clean(mf.get_embedding_status())


def h_memory_diff(mf, args: Dict[str, Any]) -> Dict[str, Any]:
    err = _require_args(args, "version_a", "version_b")
    if err:
        return err
    return _clean(mf.memory_diff(
        str(args["version_a"]),
        str(args["version_b"]),
    ))


HANDLERS: Dict[str, Any] = {
    "memory_add": h_memory_add,
    "memory_search": h_memory_search,
    "memory_list": h_memory_list,
    "memory_stats": h_memory_stats,
    "memory_importance": h_memory_importance,
    "memory_context": h_memory_context,
    "agent_emotion": h_agent_emotion,
    "char_relationship": h_char_relationship,
    "drama_genre_trend": h_drama_genre_trend,
    "drama_binge_score": h_drama_binge_score,
    # v5.4.1 新增
    "memory_reflection": h_memory_reflection,
    "memory_lineage": h_memory_lineage,
    "memory_reinforce": h_memory_reinforce,
    "drama_plot_thread": h_drama_plot_thread,
    "drama_episode_curve": h_drama_episode_curve,
    "drama_screen_time": h_drama_screen_time,
    # v5.4.2 新增
    "fed_acl_add": h_fed_acl_add,
    "fed_acl_remove": h_fed_acl_remove,
    "fed_acl_list": h_fed_acl_list,
    "fed_acl_check": h_fed_acl_check,
    "fed_acl_stats": h_fed_acl_stats,
    "share_conflict_list": h_share_conflict_list,
    "share_conflict_resolve": h_share_conflict_resolve,
    "share_conflict_dismiss": h_share_conflict_dismiss,
    "share_conflict_stats": h_share_conflict_stats,
    # v5.3.9 新增
    "intent_router": h_intent_router,
    "conflict_scan": h_conflict_scan,
    "skill_extract": h_skill_extract,
    "rerank_search": h_rerank_search,
    "session_focus": h_session_focus,
    # v5.4.5 新增向量检索
    "rebuild_embeddings": h_rebuild_embeddings,
    "embedding_status": h_embedding_status,
    # v5.5.8 新增
    "memory_diff": h_memory_diff,
}


# ---------------------------------------------------------------------------
# MCP request dispatcher
# ---------------------------------------------------------------------------

def _handle_initialize(request: Dict[str, Any]) -> Dict[str, Any]:
    _notify("notifications/initialized")
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}, "logging": {}},
        "serverInfo": {"name": "mindforge", "version": __version__},
    }


def _handle_tools_list(_request: Dict[str, Any]) -> Dict[str, Any]:
    return {"tools": list(TOOL_SCHEMAS)}


def _handle_tools_call(mf, request: Dict[str, Any]) -> Dict[str, Any]:
    params = request.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("Invalid params: expected object")
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    if name not in HANDLERS:
        raise ValueError(f"Unknown tool: {name}")
    result = HANDLERS[name](mf, arguments)
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}]}


def _parse_args(argv: List[str]) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {"db_path": None, "key_file": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--db-path" and i + 1 < len(argv):
            cfg["db_path"] = argv[i + 1]; i += 2
        elif a.startswith("--db-path="):
            cfg["db_path"] = a.split("=", 1)[1]; i += 1
        elif a == "--key-file" and i + 1 < len(argv):
            cfg["key_file"] = argv[i + 1]; i += 2
        elif a.startswith("--key-file="):
            cfg["key_file"] = a.split("=", 1)[1]; i += 1
        else:
            i += 1
    return cfg


def serve_forever(db_path: Optional[str] = None, key_file: Optional[str] = None) -> int:
    from MindForge import MindForge
    if not db_path:
        default_root = os.path.join(os.path.expanduser("~"), ".MindForge", "data", "store")
        os.makedirs(default_root, exist_ok=True)
        db_path = os.path.join(default_root, "memory.db")
    _log(f"db: {db_path}")
    kwargs: Dict[str, Any] = {"db_path": db_path, "encrypted": False}
    if key_file:
        kwargs["key_file"] = key_file
        kwargs["encrypted"] = True
    mf = MindForge(**kwargs)

    while True:
        try:
            msg = _read_message()
        except EOFError:
            _log("stdin closed")
            return 0
        except Exception as e:
            _log(f"read error: {e}")
            continue

        method = msg.get("method", "")
        rid = msg.get("id")
        try:
            if method == "initialize":
                _respond(msg, _handle_initialize(msg))
            elif method == "notifications/initialized":
                pass
            elif method == "tools/list":
                _respond(msg, _handle_tools_list(msg))
            elif method == "tools/call":
                _respond(msg, _handle_tools_call(mf, msg))
            elif method == "ping":
                _respond(msg, {})
            elif method in ("shutdown", "exit"):
                if rid is not None:
                    _respond(msg, {})
                return 0
            else:
                if rid is None:
                    _log(f"ignored notification: {method}")
                else:
                    _respond_error(msg, code=-32601, message=f"Method not found: {method}")
        except Exception as e:
            tb = traceback.format_exc(limit=6)
            _log(f"handler error {method}: {e}\n{tb}")
            if rid is not None:
                # v5.6.2 安全修复：不向客户端返回堆栈跟踪，仅服务端日志记录
                _respond_error(msg, code=-32000, message="Internal server error")


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = _parse_args(argv)
    return serve_forever(db_path=cfg.get("db_path"), key_file=cfg.get("key_file"))


if __name__ == "__main__":
    sys.exit(main())
