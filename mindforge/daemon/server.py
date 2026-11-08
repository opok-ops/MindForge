"""MindForge 常驻守护进程（daemon）——毫秒级调用路径。

背景（2026-11-08 延迟实测）：引擎直连 2-3ms，但 CLI 子进程路径 698ms——
差距全部来自 Python 解释器启动与导入开销。daemon 把引擎实例常驻在
一个长生命周期进程里，宿主（dsh 插件等）通过 stdin/stdout 行式 JSON
协议调用，消除每次调用的进程启动税。

协议（v6.4.0）：
    请求（stdin，每行一个 JSON）:
        {"id": <int|str>, "cmd": "<command>", "args": {<kwargs>}}
    响应（stdout，每行一个 JSON，立即可 flush）:
        {"id": ..., "ok": true, "data": {...}}        成功
        {"id": ..., "ok": false, "error": "<msg>"}    失败（进程不退出）
    生命周期:
        "ping"     → {"pong": true, "pid": ..., "uptime_ms": ...}
        "shutdown" → 优雅退出（退出码 0）
        EOF        → 退出
    stderr: 启动横幅与日志（stdout 保持纯 JSON 行，严禁污染）

命令集（v1，输出契约对齐 CLI --json）:
    add / search / memory-recall / memory-context / stats /
    compress / compress-list / decompress / adaptive-search

设计原则:
    - 单线程顺序处理：v1 目标是正确与低延迟，agent 场景以顺序调用为主
    - 每请求独立 try/except：单条请求异常不杀进程
    - 引擎实例只建一次、永不 close（daemon 生命周期 = 引擎生命周期）
"""
from __future__ import annotations

import json
import os
import sys
import time

from mindforge.core.types import (
    Importance,
    MemoryLayer,
    MemoryType,
    PrivacyLevel,
)

__all__ = ["run_daemon"]


def _make_serializer():
    """引擎对象 → CLI --json 兼容 dict 的转换集合（对齐 cli/main.py 契约）。"""

    def add(entry):
        return {
            "status": "ok",
            "id": entry.id,
            "content": entry.content,
            "category": entry.category,
            "tags": entry.tags,
            "layer": entry.layer.value,
            "importance": entry.importance.value
            if getattr(entry.importance, "value", None)
            else str(entry.importance),
        }

    def search(result):
        return {
            "chunks": [
                {
                    "memory_id": c.memory_id,
                    "content": c.content,
                    "category": c.category,
                    "layer": c.layer.value,
                    "relevance_score": c.relevance_score,
                    "tags": c.tags,
                    "score_breakdown": c.score_breakdown,
                }
                for c in result.chunks
            ],
            "total_found": result.total_found,
            "query_time_ms": result.query_time_ms,
            "strategy_used": result.strategy_used,
            "token_estimate": result.token_estimate,
            "layers_used": result.layers_used,
            "approximate": getattr(result, "approximate", False),
        }

    return add, search


def run_daemon(db_path: str, key_file: str | None = None, encrypted: bool = False) -> int:
    """启动常驻守护进程主循环。返回退出码。"""
    from mindforge.core.types import MemoryConfig
    from mindforge.core.mindforge import MindForge

    t_start = time.time()
    cm = MindForge(
        config=MemoryConfig(db_path=db_path, key_file=key_file, encrypted=encrypted)
    )
    add_ser, search_ser = _make_serializer()
    taxonomy = None  # lazy：add 缺省分类时才建

    print(
        json.dumps(
            {
                "event": "ready",
                "pid": os.getpid(),
                "db_path": db_path,
                "startup_ms": int((time.time() - t_start) * 1000),
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
        flush=True,
    )

    def cmd_add(args):
        nonlocal taxonomy
        content = args["content"]
        category = args.get("category")
        tags = args.get("tags")
        if not category or not tags:
            if taxonomy is None:
                from mindforge.modules.categorizer import TaxonomyManager

                taxonomy = TaxonomyManager()
            if not category:
                category = taxonomy.suggest_category(content)
            if not tags:
                tags = taxonomy.suggest_tags(content)
        entry = cm.add(
            content=content,
            category=category,
            tags=tags,
            privacy=PrivacyLevel.from_string(args.get("privacy", "INTERNAL")),
            importance=Importance.from_string(args.get("importance", "MEDIUM")),
            memory_type=MemoryType.from_string(args.get("type", "text")),
            layer=MemoryLayer.from_string(args.get("layer", "short_term")),
            source_session=args.get("session", ""),
            source_agent=args.get("agent", ""),
        )
        return add_ser(entry)

    def cmd_search(args):
        result = cm.search(
            query=args["query"],
            max_results=args.get("limit", 10),
            min_relevance=0.2,
            agent_id=args.get("agent", ""),
            session_id=args.get("session", ""),
            use_embedding=args.get("use_embedding", True),
            explain=args.get("explain", False),
        )
        return search_ser(result)

    def cmd_memory_recall(args):
        return cm.memory_recall(
            args["agent"], args["query"],
            args.get("top_k", 10), args.get("days", 30),
        )

    def cmd_memory_context(args):
        return cm.memory_context(
            args["agent"], args["query"], args.get("max_tokens", 4000)
        )

    def cmd_stats(args):
        return cm.detailed_stats()

    def cmd_compress(args):
        return cm.compress_memories(
            threshold=args.get("threshold", 0.5),
            dry_run=args.get("dry_run", False),
            categories=[args["category"]] if args.get("category") else None,
            min_cluster_size=args.get("min_cluster_size", 2),
        )

    def cmd_compress_list(args):
        items = cm.list_compressions()
        return {"total": len(items), "clusters": items}

    def cmd_decompress(args):
        return cm.decompress_cluster(args["cluster_id"])

    def cmd_adaptive_search(args):
        return cm.adaptive_search(
            query=args["query"],
            session_id=args.get("session", "cli"),
            max_results=args.get("limit", 10),
        )

    handlers = {
        "add": cmd_add,
        "search": cmd_search,
        "memory-recall": cmd_memory_recall,
        "memory-context": cmd_memory_context,
        "stats": cmd_stats,
        "compress": cmd_compress,
        "compress-list": cmd_compress_list,
        "decompress": cmd_decompress,
        "adaptive-search": cmd_adaptive_search,
    }

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            rid = req.get("id")
            cmd = req.get("cmd", "")
            args = req.get("args", {}) or {}
        except json.JSONDecodeError as e:
            sys.stdout.write(
                json.dumps({"id": None, "ok": False, "error": f"bad json: {e}"}) + "\n"
            )
            sys.stdout.flush()
            continue

        if cmd == "ping":
            sys.stdout.write(
                json.dumps(
                    {
                        "id": rid,
                        "ok": True,
                        "data": {
                            "pong": True,
                            "pid": os.getpid(),
                            "uptime_ms": int((time.time() - t_start) * 1000),
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            sys.stdout.flush()
            continue
        if cmd == "shutdown":
            sys.stdout.write(
                json.dumps({"id": rid, "ok": True, "data": {"bye": True}}) + "\n"
            )
            sys.stdout.flush()
            break

        handler = handlers.get(cmd)
        if handler is None:
            sys.stdout.write(
                json.dumps(
                    {
                        "id": rid,
                        "ok": False,
                        "error": f"unknown cmd: {cmd}（支持: {sorted(handlers) + ['ping', 'shutdown']}）",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            sys.stdout.flush()
            continue

        try:
            data = handler(args)
            sys.stdout.write(
                json.dumps({"id": rid, "ok": True, "data": data}, ensure_ascii=False)
                + "\n"
            )
        except Exception as e:  # 单请求失败不杀进程
            sys.stdout.write(
                json.dumps(
                    {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"},
                    ensure_ascii=False,
                )
                + "\n"
            )
        sys.stdout.flush()

    try:
        cm.close()
    except Exception:
        pass
    return 0
