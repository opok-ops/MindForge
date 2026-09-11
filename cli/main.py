#!/usr/bin/env python3
"""
MindForge v5.5.8 CLI - 命令行工具
=================================

Usage:
    python cli/main.py <command> [options]

Commands:
    init                初始化 MindForge（生成加密密钥）
    add <content>       添加记忆
    search <query>      搜索记忆
    list                列出所有记忆
    get <id>            获取单条记忆（v5.1.1 补全）
    update <id>         更新记忆（v5.1.1 补全）
    delete <id>         删除记忆（v5.1.1 补全）
    stats               统计信息
    audit               审计日志（v5.1.1 补全）
    recent              最近添加的记忆（v5.1.1 新增）
    trash               查看回收站（v5.1.1 新增）
    restore <id>        从回收站恢复记忆（v5.1.1 新增）
    backup              备份数据
    export <file>       导出记忆
    import <file>       导入记忆
    privacy-scan <text> 隐私扫描
    compliance          合规报告
    consolidate         记忆巩固（短期→长期）
    evolution           记忆演化统计
    graph               知识图谱操作
    personality         人格化引擎
    multimodal          多模态记忆
    federated           联邦记忆
    serve               启动 Web UI
    --- v5.3.6+ ---
    memory-link         记忆关联推理
    memory-recall       智能记忆召回
    drama-pacing        剧集节奏分析
    char-interaction    角色互动分析
    --- v5.3.7+ ---
    memory-importance   记忆重要度分析
    memory-context      上下文记忆注入
    agent-emotion       Agent 情感追踪
    drama-genre-trend   短剧类型趋势分析
    drama-binge-score   追剧粘性评分
    char-relationship   角色关系深度分析
    --- v5.4.1+ ---
    memory-reflection   记忆反思（元认知报告）
    rebuild-embeddings  重建所有记忆的嵌入向量
    embedding-status    查看嵌入向量状态
    memory-lineage      记忆血缘溯源
    memory-reinforce    记忆强化候选
    drama-plot-thread   剧情伏笔线索追踪
    drama-episode-curve 分集张力曲线
    drama-screen-time   角色戏份平衡
    --- v5.4.2+ ---
    fed-acl-add         联邦 ACL：添加规则
    fed-acl-remove      联邦 ACL：删除规则
    fed-acl-list        联邦 ACL：规则列表
    fed-acl-check       联邦 ACL：访问评估
    fed-acl-stats       联邦 ACL：统计
    share-conflicts     共享冲突：列表
    share-conflict-resolve 共享冲突：解决（lww/keep_both）
    share-conflict-dismiss 共享冲突：关闭
    share-conflict-stats   共享冲突：统计
"""

import sys
import json
import sqlite3
import argparse
import getpass
import html
import time
import socket
import ipaddress
from typing import Any, Dict, List
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import (
    MindForge,
    MemoryConfig,
    PrivacyLevel,
    Importance,
    MemoryType,
    MemoryLayer,
)

try:
    from MindForge import __version__
except ImportError:
    __version__ = "5.6.1"

# 懒加载 modules：仅在对应命令执行时才导入，大幅加速 CLI 启动
_modules_cache = {}

def _lazy_import(name):
    if name not in _modules_cache:
        if name == "TaxonomyManager":
            from modules.categorizer import TaxonomyManager
            _modules_cache[name] = TaxonomyManager
        elif name == "RecallConfig":
            from modules.recall import RecallConfig
            _modules_cache[name] = RecallConfig
        elif name == "KnowledgeGraph":
            from modules.knowledge_graph import KnowledgeGraph
            _modules_cache[name] = KnowledgeGraph
        elif name == "MemoryEvolution":
            from modules.evolution import MemoryEvolution
            _modules_cache[name] = MemoryEvolution
        elif name == "PersonalityEngine":
            from modules.personality import PersonalityEngine
            _modules_cache[name] = PersonalityEngine
        elif name == "MultimodalMemory":
            from modules.multimodal import MultimodalMemory
            _modules_cache[name] = MultimodalMemory
        elif name == "FederatedMemory":
            from modules.federated import FederatedMemory
            _modules_cache[name] = FederatedMemory
        elif name == "PrivacyEngine":
            from modules.privacy import PrivacyEngine
            _modules_cache[name] = PrivacyEngine
        elif name == "MemoryIntegrator":
            from modules.integrator import MemoryIntegrator
            _modules_cache[name] = MemoryIntegrator
    return _modules_cache[name]

COLORS = {
    "cyan": "\033[96m",
    "purple": "\033[95m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "pink": "\033[95m",
    "reset": "\033[0m",
    "bold": "\033[1m",
}

_json_mode = False


def c(text: str, color: str) -> str:
    return f"{COLORS.get(color, '')}{text}{COLORS['reset']}"


def format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _split_tags(tags):
    """展开逗号分隔的标签（v5.2.8 新增）

    add/update/find 等命令的 --tags 使用 nargs="+"（空格分隔），
    而 batch-add-tags 等命令约定逗号分隔。两种习惯混用时，
    `--tags a,b` 会被错误存为单个标签 "a,b"。
    此函数在 parse_args 后统一扁平化：空格分隔与逗号分隔均可混用。

    Args:
        tags: nargs="+" 解析出的原始列表（可能含 "a,b" 形式的元素）

    Returns:
        展开去空后的标签列表；无有效标签时返回 None
    """
    if not tags:
        return None
    result = []
    for item in tags:
        for part in str(item).split(","):
            part = part.strip()
            if part:
                result.append(part)
    return result or None


def format_size(bytes_val: int) -> str:
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    else:
        return f"{bytes_val / 1024 / 1024:.2f} MB"


# ===== 路径安全校验（v5.2.7 新增：防止路径遍历攻击）=====
# 允许的工作根目录：当前工作目录 + 用户主目录下的 data
_ALLOWED_BASE_DIRS = [
    Path.cwd().resolve(),
    Path.home().resolve() / "data",
    Path.home().resolve() / ".mindforge",
]

# 允许的导入/导出文件扩展名
_ALLOWED_EXPORT_EXTS = {".md", ".html", ".xml", ".json", ".xlsx", ".csv", ".txt"}
_ALLOWED_IMPORT_EXTS = {".md", ".xml", ".json", ".xlsx", ".csv", ".txt"}


def _validate_path(path_str, base_dir=None, must_exist=False,
                   allow_symlinks=False, max_size=None, allowed_exts=None):
    """校验文件路径安全性，防止路径遍历攻击（v5.2.7 新增）

    Args:
        path_str: 用户输入的路径
        base_dir: 允许的基础目录，默认使用 _ALLOWED_BASE_DIRS
        must_exist: 是否要求文件必须存在（用于读取）
        allow_symlinks: 是否允许符号链接（默认禁止）
        max_size: 文件大小上限（字节，仅 must_exist=True 时检查）
        allowed_exts: 允许的文件扩展名集合，None 表示不限制

    Returns:
        Path: 校验通过后的绝对路径

    Raises:
        ValueError: 路径不安全或校验失败
    """
    if not path_str or not isinstance(path_str, str):
        raise ValueError("路径不能为空")
    if len(path_str) > 4096:
        raise ValueError("路径过长（上限 4096 字符）")

    target = Path(path_str)
    # 相对路径基于当前工作目录解析
    if not target.is_absolute():
        target = Path.cwd() / target

    # resolve 解析所有 .. 和符号链接
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError) as e:
        raise ValueError(f"路径解析失败: {e}")

    # 校验在允许的基础目录内（防止路径遍历越界）
    base_dirs = [Path(b).resolve() for b in ([base_dir] if base_dir else _ALLOWED_BASE_DIRS)]
    in_allowed = False
    for b in base_dirs:
        try:
            resolved.relative_to(b)
            in_allowed = True
            break
        except ValueError:
            continue
    if not in_allowed:
        raise ValueError(f"路径越界：只允许在 {', '.join(str(b) for b in base_dirs)} 之下")

    # 符号链接检查
    if not allow_symlinks:
        # 检查目标本身或父路径中是否存在符号链接
        check_path = resolved
        while check_path != check_path.parent:
            if check_path.is_symlink():
                raise ValueError(f"不允许操作符号链接: {check_path}")
            check_path = check_path.parent

    # 扩展名检查
    if allowed_exts is not None:
        ext = resolved.suffix.lower()
        if ext not in allowed_exts:
            raise ValueError(f"不支持的文件类型: {ext}（允许: {', '.join(sorted(allowed_exts))}）")

    # 存在性检查
    if must_exist and not resolved.exists():
        raise ValueError(f"文件不存在: {resolved}")

    # 大小检查
    if max_size is not None and resolved.exists() and resolved.is_file():
        size = resolved.stat().st_size
        if size > max_size:
            raise ValueError(f"文件过大: {size} 字节（上限 {max_size}）")

    return resolved


def _safe_export_path(output_path, default_name, ext):
    """安全处理导出路径（v5.2.7 新增）

    Args:
        output_path: 用户指定的输出路径（可能为空）
        default_name: 默认文件名
        ext: 允许的扩展名（如 ".md"）

    Returns:
        校验后的 Path 对象
    """
    if not output_path:
        # 默认输出到当前目录
        out = Path.cwd() / default_name
    else:
        out = output_path

    return _validate_path(out, allowed_exts={ext})


def _safe_import_path(input_path, max_size=100 * 1024 * 1024):
    """安全处理导入路径（v5.2.7 新增）

    Args:
        input_path: 用户指定的输入路径
        max_size: 文件大小上限，默认 100MB

    Returns:
        校验后的 Path 对象
    """
    return _validate_path(
        input_path,
        must_exist=True,
        allow_symlinks=False,
        max_size=max_size,
        allowed_exts=_ALLOWED_IMPORT_EXTS,
    )



def print_banner():
    if _json_mode:
        return
    banner = f"""
{COLORS['cyan']}╔══════════════════════════════════════════════════════╗
║        {COLORS['bold']}MindForge v{__version__} - AI Agent 终身记忆系统{COLORS['reset']}{COLORS['cyan']}        ║
║      四层记忆架构 · 知识图谱 · 多模态 · 人格化      ║
╚══════════════════════════════════════════════════════╝{COLORS['reset']}
"""
    print(banner)


def cmd_init(args):
    """初始化 MindForge"""
    print_banner()
    print(c(f"MindForge v{__version__} 初始化向导", "bold"))
    print("=" * 50)

    # 非加密模式（CI/自动化场景）
    if getattr(args, 'no_encrypt', False):
        try:
            config = MemoryConfig(
                db_path=args.db_path,
                key_file=args.key_file,
                encrypted=False,
            )
            cm = MindForge(config=config)
            cm.init_with_password("")

            print(c("\n✅ 初始化成功（无加密）！", "green"))
            print(f"   数据库路径: {args.db_path}")
            print(c("\n   ⚠️  警告：未启用加密，记忆数据明文存储", "yellow"))
            return 0
        except (ValueError, TypeError, OSError) as e:
            print(c(f"❌ 初始化失败：{e}", "red"))
            return 1

    # 非交互式密码（--password 参数）
    if getattr(args, 'password', None):
        password = args.password
        if len(password) < 8:
            print(c("⚠️  警告：密码建议至少 8 位", "yellow"))
    else:
        # 交互式密码输入
        try:
            password = getpass.getpass("请设置加密密码（用于保护记忆）：")
            if not password:
                print(c("❌ 密码不能为空", "red"))
                return 1

            password2 = getpass.getpass("请再次输入密码确认：")
            if password != password2:
                print(c("❌ 两次密码不一致", "red"))
                return 1
        except (EOFError, OSError):
            print(c("❌ 无法读取密码：非交互式环境请使用 --password 或 --no-encrypt", "red"))
            return 1

        if len(password) < 8:
            print(c("⚠️  警告：密码建议至少 8 位", "yellow"))

    print("\n正在生成加密密钥（PBKDF2-SHA256，100000 次迭代）...")

    try:
        config = MemoryConfig(
            db_path=args.db_path,
            key_file=args.key_file,
            encrypted=True,
        )
        cm = MindForge(config=config)
        cm.init_with_password(password)

        print(c("\n✅ 初始化成功！", "green"))
        print(f"   数据库路径: {args.db_path}")
        print(f"   密钥文件: {args.key_file}")
        print(c("\n   提示：请妥善保管密码，丢失后无法恢复记忆数据", "yellow"))
        return 0
    except (ValueError, TypeError, OSError) as e:
        print(c(f"❌ 初始化失败：{e}", "red"))
        return 1


def _get_memory(args) -> MindForge:
    # P0-001: key_file 单独存在时也启用加密
    key_file_path = Path(args.key_file)
    encrypted = key_file_path.exists()

    config = MemoryConfig(
        db_path=args.db_path,
        key_file=args.key_file,
        encrypted=encrypted,
    )
    mf = MindForge(config=config)

    # 加密模式下需要密码初始化
    if encrypted:
        import os
        password = os.environ.get("MINDFORGE_PASSWORD", "")
        if not password and sys.stdin.isatty():
            try:
                password = getpass.getpass("请输入加密密码：")
            except (EOFError, OSError):
                password = ""
        if password:
            mf.init_with_password(password)
        else:
            # P2 修复：非交互且无密码时给出清晰错误，而非裸 AttributeError
            # v5.5.7 fix: --json 模式下输出合法 JSON 错误到 stdout，
            # 非 JSON 模式下错误走 stderr（不污染 stdout，bridge parseOutput 不会挂）
            error_msg = (
                "加密数据库需要密码才能操作。请通过以下方式之一提供密码："
                " 1. 设置环境变量 MINDFORGE_PASSWORD；"
                " 2. 在交互式终端中运行（会提示输入密码）；"
                " 3. 如使用 dsh-mindforge bridge，在宿主环境中 export MINDFORGE_PASSWORD。"
            )
            if _json_mode:
                print(json.dumps({"error": error_msg}, ensure_ascii=False))
            else:
                print(c("\n❌ 加密数据库需要密码才能操作", "red"), file=sys.stderr)
                print(c("   请通过以下方式之一提供密码：", "yellow"), file=sys.stderr)
                print(c("   1. 设置环境变量：export MINDFORGE_PASSWORD=\"你的密码\"", "yellow"), file=sys.stderr)
                print(c("   2. 在交互式终端中运行（会提示输入密码）", "yellow"), file=sys.stderr)
                print(c("   3. 如使用 dsh-mindforge bridge，在宿主环境中 export MINDFORGE_PASSWORD", "yellow"), file=sys.stderr)
            if hasattr(mf, 'close'):
                mf.close()
            sys.exit(1)

    return mf


def cmd_add(args):
    """添加记忆"""
    cm = _get_memory(args)
    taxonomy = _lazy_import("TaxonomyManager")()

    content = args.content
    category = args.category
    if not category:
        category = taxonomy.suggest_category(content)
        if not _json_mode:
            print(f"  建议分类：{c(category, 'cyan')}")

    tags = args.tags
    if not tags:
        tags = taxonomy.suggest_tags(content)
        if not _json_mode:
            print(f"  建议标签：{', '.join(tags) if tags else '无'}")

    privacy = PrivacyLevel.from_string(args.privacy)
    layer = MemoryLayer.from_string(args.layer)
    memory_type = MemoryType.from_string(args.type)

    entry = cm.add(
        content=content,
        category=category,
        tags=tags,
        privacy=privacy,
        importance=Importance.from_string(args.importance),
        memory_type=memory_type,
        layer=layer,
        source_session=args.session,
        source_agent=args.agent,
        starred=getattr(args, "star", False),
    )

    if getattr(args, 'json_output', False):
        _json_out({"status": "ok", "id": entry.id, "content": entry.content,
                   "category": entry.category, "tags": entry.tags,
                   "layer": entry.layer.value, "importance": args.importance})
        cm.close()
        return 0
    print(c("\n✅ 记忆已保存", "green"))
    print(f"   ID: {entry.id}")
    print(f"   分类: {entry.category}")
    print(f"   标签: {', '.join(entry.tags) if entry.tags else '无'}")
    print(f"   层级: {c(entry.layer.value, 'purple')}")
    print(f"   隐私: {entry.privacy.value}")
    print(f"   重要性: {args.importance}")
    if entry.starred:
        print("   ⭐ 已收藏")
    cm.close()
    return 0


def cmd_search(args):
    """搜索记忆"""
    cm = _get_memory(args)

    use_embedding = not getattr(args, "no_embedding", False)
    result = cm.search(
        query=args.query,
        max_results=args.limit,
        min_relevance=0.2,
        categories=[args.category] if args.category else None,
        agent_id=args.agent,
        session_id=args.session,
        use_embedding=use_embedding,
    )

    if getattr(args, 'json_output', False):
        _json_out({
            "chunks": [{"memory_id": c.memory_id, "content": c.content,
                        "category": c.category, "layer": c.layer.value,
                        "relevance_score": c.relevance_score, "tags": c.tags}
                       for c in result.chunks],
            "total_found": result.total_found,
            "query_time_ms": result.query_time_ms,
            "strategy_used": result.strategy_used,
            "token_estimate": result.token_estimate,
            "layers_used": result.layers_used,
        })
        cm.close()
        return 0

    print(f"\n找到 {c(result.total_found, 'cyan')} 条相关记忆"
          f"（耗时 {result.query_time_ms}ms）")
    print(f"策略：{result.strategy_used} | 预估 tokens：{result.token_estimate}")
    print(f"涉及层级：{', '.join(result.layers_used)}")

    for i, chunk in enumerate(result.chunks, 1):
        print(f"\n--- 结果 {i} [{chunk.category}] "
              f"相关度:{c(f'{chunk.relevance_score:.3f}', 'green')} "
              f"[{chunk.layer.value}] ---")
        content = chunk.content[:300]
        print(content)
        if len(chunk.content) > 300:
            print("...")

    cm.close()
    return 0


def cmd_get(args):
    """获取单条记忆（v5.1.1 补全）"""
    cm = _get_memory(args)
    entry = cm.get(args.id, actor=args.agent, session_id=args.session)

    if not entry:
        print(c("❌ 记忆不存在或无权访问", "red"))
        return 1

    privacy_color = {
        "PUBLIC": "green",
        "INTERNAL": "yellow",
        "PRIVATE": "red",
        "STRICT": "pink",
    }.get(entry.privacy.value, "reset")

    layer_color = {
        "sensory": "cyan",
        "short_term": "cyan",
        "long_term": "purple",
        "permanent": "green",
    }.get(entry.layer.value, "reset")

    star_mark = "⭐ " if entry.starred else ""

    print(c("\n📄 记忆详情", "bold"))
    print("=" * 50)
    print(f"ID:       {entry.id}")
    print(f"分类:     [{entry.category}]")
    print(f"隐私:     {c(entry.privacy.value, privacy_color)}")
    print(f"层级:     {c(entry.layer.value, layer_color)}")
    print(f"重要性:   {entry.importance.value}")
    print(f"类型:     {entry.memory_type.value}")
    print(f"标签:     {', '.join(entry.tags) if entry.tags else '无'}")
    print(f"创建:     {format_time(entry.created_at)}")
    print(f"更新:     {format_time(entry.updated_at)}")
    print(f"访问:     {entry.access_count} 次")
    print(f"状态:     {star_mark + '已收藏' if entry.starred else '未收藏'}")
    print(f"\n内容:\n{entry.content}")

    if entry.metadata:
        print(f"\n元数据: {json.dumps(entry.metadata, ensure_ascii=False, indent=2)}")

    cm.close()
    return 0


def cmd_update(args):
    """更新记忆（v5.1.1 补全）"""
    cm = _get_memory(args)

    privacy = PrivacyLevel.from_string(args.privacy) if args.privacy else None
    layer = MemoryLayer.from_string(args.layer) if args.layer else None
    importance = Importance.from_string(args.importance) if args.importance else None

    starred = None
    if args.star:
        starred = True
    elif args.unstar:
        starred = False

    # 至少要更新一个字段
    if not any([args.content, args.category, args.tags, args.privacy,
                args.importance, args.layer, starred is not None]):
        print(c("⚠️  请至少指定一个要更新的字段", "yellow"))
        return 1

    success = cm.update(
        memory_id=args.id,
        content=args.content,
        category=args.category,
        tags=args.tags,
        privacy=privacy,
        importance=importance,
        layer=layer,
        starred=starred,
        actor=args.agent,
        session_id=args.session,
    )

    if success:
        print(c("\n✅ 记忆已更新", "green"))
    else:
        print(c("\n❌ 更新失败：记忆不存在", "red"))

    cm.close()
    return 0 if success else 1


def cmd_delete(args):
    """删除记忆（v5.1.1 补全）"""
    cm = _get_memory(args)

    entry = cm.get(args.id, actor=args.agent, session_id=args.session)
    if not entry:
        if not _json_mode:
            print(c("❌ 记忆不存在", "red"))
        return 1

    if not args.force:
        if not _json_mode:
            print(c("\n⚠️  将删除记忆：", "yellow"))
            print(f"   [{entry.category}] {entry.preview[:60]}...")
            print(c("\n确认删除？加 --force 执行（软删除，可在回收站恢复）", "yellow"))
            print(c("彻底删除请加 --hard", "yellow"))
        return 1

    success = cm.delete(
        args.id,
        actor=args.agent,
        session_id=args.session,
        hard_delete=args.hard,
    )

    if success:
        if getattr(args, 'json_output', False):
            # v5.5.6 fix: 参数名为 id 而非 memory_id（此前 AttributeError）
            _json_out({"status": "ok", "id": args.id,
                       "action": "hard_delete" if args.hard else "soft_delete"})
            cm.close()
            return 0
        action = "彻底删除" if args.hard else "移到回收站"
        print(c(f"\n🗑️  已{action}", "green"))
    else:
        print(c("\n❌ 删除失败", "red"))

    cm.close()
    return 0 if success else 1


def cmd_audit(args):
    """审计日志（v5.1.1 补全）"""
    cm = _get_memory(args)

    logs = cm.audit_log(
        memory_id=args.id,
        actor=args.actor,
        limit=args.limit,
    )

    print(c("\n📋 审计日志", "bold"))
    print("=" * 50)
    print(f"共 {c(str(len(logs)), 'cyan')} 条记录")

    for log in logs:
        ts = format_time(log.timestamp)
        action = log.action or "unknown"
        mid = log.memory_id or "-"
        actor = log.actor or "-"
        detail = ""
        if log.details:
            if isinstance(log.details, dict):
                detail = log.details.get("message", "")
                if not detail:
                    detail = json.dumps(log.details, ensure_ascii=False)
            else:
                detail = str(log.details)
        print(f"\n[{ts}] {c(action.upper(), 'purple')}")
        print(f"   记忆ID: {mid[:16]}... | 操作者: {actor}")
        if detail:
            print(f"   详情: {detail}")

    cm.close()
    return 0


def cmd_recent(args):
    """最近添加的记忆（v5.1.1 新增）"""
    cm = _get_memory(args)

    now = datetime.now().timestamp()
    since = now - args.hours * 3600

    entries = cm.list(
        created_after=since,
        limit=args.limit,
        offset=args.offset,
    )

    print(f"\n最近 {c(str(args.hours), 'cyan')} 小时内添加的记忆"
          f"（共 {c(str(len(entries)), 'cyan')} 条）")

    for entry in entries:
        star_mark = "⭐ " if entry.starred else ""
        print(f"\n--- [{entry.category}] {star_mark}{entry.preview[:60]} ---")
        print(f"  层级: {entry.layer.value} | 重要性: {entry.importance.value}")
        print(f"  标签: {', '.join(entry.tags) if entry.tags else '无'}")
        print(f"  创建: {format_time(entry.created_at)}")

    cm.close()
    return 0


def cmd_trash(args):
    """查看回收站（v5.1.1 新增）"""
    cm = _get_memory(args)
    entries = cm.list(category="trash", limit=args.limit, offset=args.offset)

    print(f"\n🗑️  回收站（共 {c(str(len(entries)), 'cyan')} 条）")

    for entry in entries:
        original = entry.metadata.get("_original_category", "unknown")
        print(f"\n--- 原分类: [{original}] {entry.preview[:60]} ---")
        print(f"  ID: {entry.id}")
        print(f"  删除时间: {format_time(entry.updated_at)}")
        print(f"  标签: {', '.join(entry.tags) if entry.tags else '无'}")

    if not entries:
        print(c("回收站为空", "yellow"))

    cm.close()
    return 0


def cmd_restore(args):
    """从回收站恢复记忆（v5.1.1 新增）"""
    cm = _get_memory(args)

    success = cm.restore(
        args.id,
        actor=args.agent,
        session_id=args.session,
    )

    if success:
        print(c("\n♻️  记忆已恢复", "green"))
    else:
        print(c("\n❌ 恢复失败：记忆不存在或不在回收站", "red"))

    cm.close()
    return 0 if success else 1


def cmd_list(args):
    """列出记忆"""
    cm = _get_memory(args)

    starred = None
    if getattr(args, "starred", False):
        starred = True
    if getattr(args, "unstarred", False):
        starred = False

    created_after = None
    created_before = None
    if args.after:
        from datetime import datetime
        created_after = datetime.fromisoformat(args.after).timestamp()
    if args.before:
        from datetime import datetime
        created_before = datetime.fromisoformat(args.before).timestamp()

    entries = cm.list(
        category=args.category,
        layer=MemoryLayer.from_string(args.layer) if args.layer else None,
        starred=starred,
        created_after=created_after,
        created_before=created_before,
        limit=args.limit,
        offset=args.offset,
        sort_by=args.sort,
        sort_order=args.order,
    )

    # v5.5.8: 修复 cmd_list 显示全局总数而非筛选后的总数
    if args.category or args.layer or starred is not None or args.after or args.before:
        total = len(entries)
    else:
        total = cm.stats()["total"]
    filter_desc = ""
    if starred is not None:
        filter_desc += " [⭐ 收藏]" if starred else " [未收藏]"
    if args.after or args.before:
        filter_desc += " [时间筛选]"
    print(f"\n记忆列表（共 {total} 条，显示 {len(entries)} 条{filter_desc}）")

    for entry in entries:
        privacy_color = {
            "PUBLIC": "green",
            "INTERNAL": "yellow",
            "PRIVATE": "red",
            "STRICT": "pink",
        }.get(entry.privacy.value, "reset")

        layer_color = {
            "sensory": "cyan",
            "short_term": "cyan",
            "long_term": "purple",
            "permanent": "green",
        }.get(entry.layer.value, "reset")

        star_mark = "⭐ " if entry.starred else ""

        print(f"\n{'='*50}")
        print(f"ID:       {entry.id[:16]}...")
        print(f"分类:     [{entry.category}]  "
              f"隐私: {c(entry.privacy.value, privacy_color)}  "
              f"层级: {c(entry.layer.value, layer_color)}  "
              f"重要性: {entry.importance.value}")
        if entry.starred:
            print("状态:     ⭐ 已收藏")
        print(f"标签:     {', '.join(entry.tags) if entry.tags else '无'}")
        print(f"类型:     {entry.memory_type.value}")
        print(f"创建:     {format_time(entry.created_at)}  "
              f"访问: {entry.access_count} 次")
        print(f"预览: {star_mark}{entry.preview[:80]}...")

    if total > args.limit + args.offset:
        remaining = total - args.limit - args.offset
        print(f"\n... 还有 {remaining} 条"
              f"（使用 --offset {args.offset + args.limit} 查看更多）")

    cm.close()
    return 0


def cmd_stats(args):
    """统计信息"""
    cm = _get_memory(args)

    if args.detailed:
        stats = cm.detailed_stats()
        if getattr(args, 'json_output', False):
            _json_out(stats)
            cm.close()
            return 0
        print_banner()
        print(c("MindForge 详细统计报告", "bold"))
        print("=" * 50)
        print(f"总记忆数：        {c(str(stats.get('total', 0)), 'cyan')}")
        print(f"回收站：          {c(str(stats.get('trash', 0)), 'yellow')}")
        print(f"⭐ 收藏数：       {c(str(stats.get('starred', 0)), 'yellow')}")
        print(f"加密记忆数：      {c(str(stats.get('encrypted', 0)), 'purple')}")

        if stats.get("first_created"):
            print("\n创建时间范围：")
            print(f"  最早： {format_time(stats['first_created'])}")
            print(f"  最近： {format_time(stats['last_created'])}")

        print("\n📊 平均指标：")
        print(f"  平均访问次数：   {stats.get('avg_access_count', 0)}")
        print(f"  平均记忆强度：   {stats.get('avg_strength', 0)}")
        print(f"  平均遗忘分数：   {stats.get('avg_forgetting_score', 0)}")

        print("\n🔝 极值指标：")
        print(f"  最高访问次数：   {stats.get('max_access_count', 0)}")
        print(f"  最低记忆强度：   {stats.get('min_strength', 0)}")

        print("\n📂 按分类：")
        for cat, count in stats.get("by_category", {}).items():
            print(f"  {cat}: {count}")

        print("\n🧠 按层级：")
        for lay, count in stats.get("by_layer", {}).items():
            print(f"  {lay}: {count}")

        print("\n🔐 按隐私：")
        for pri, count in stats.get("by_privacy", {}).items():
            print(f"  {pri}: {count}")

        print("\n⭐ 按重要性：")
        for imp, count in stats.get("by_importance", {}).items():
            print(f"  {imp}: {count}")

        print(f"\n📋 审计记录数：   {stats.get('audit_records', 0)}")
    else:
        stats = cm.stats()
        # v5.5.6 fix: 非 detailed 模式也支持 --json 输出（此前无 JSON 分支）
        if getattr(args, 'json_output', False):
            # 附加数据库路径信息（便于插件集成）
            stats["db_path"] = args.db_path or ""
            _json_out(stats)
            cm.close()
            return 0
        print_banner()
        print(c("MindForge 统计报告", "bold"))
        print("=" * 50)
        print(f"总记忆数：  {c(str(stats['total']), 'cyan')}")
        print(f"⭐ 收藏数： {c(str(stats.get('starred_count', 0)), 'yellow')}")
        print(f"数据库大小：{format_size(stats['db_size_bytes'])}")
        # v5.5.6 fix: stats() 不返回 db_path，改为显示数据库文件大小即可
        print(f"数据库路径：{args.db_path or '(默认)'}")

        print("\n按隐私分级：")
        for level, count in stats.get("by_privacy", {}).items():
            print(f"  {level}: {count}")

        print("\n按记忆层级：")
        for layer, count in stats.get("by_layer", {}).items():
            print(f"  {layer}: {count}")

        print("\n按重要性：")
        for level, count in stats.get("by_importance", {}).items():
            print(f"  {level}: {count}")

        print("\n分类统计（前10）：")
        for cat, count in list(stats.get("top_categories", {}).items())[:10]:
            print(f"  {cat}: {count}")

        top_tags = stats.get("top_tags", {})
        if top_tags:
            print("\n标签统计（前10）：")
            for tag, count in list(top_tags.items())[:10]:
                print(f"  #{tag}: {count}")

    cm.close()
    return 0


def cmd_batch_delete(args):
    """批量删除记忆"""
    cm = _get_memory(args)

    created_after = None
    created_before = None
    if args.after:
        from datetime import datetime
        created_after = datetime.fromisoformat(args.after).timestamp()
    if args.before:
        from datetime import datetime
        created_before = datetime.fromisoformat(args.before).timestamp()

    starred = None
    if getattr(args, "starred", False):
        starred = True
    if getattr(args, "unstarred", False):
        starred = False

    if not args.force:
        preview = cm.list(
            category=args.category,
            layer=MemoryLayer.from_string(args.layer) if args.layer else None,
            starred=starred,
            created_after=created_after,
            created_before=created_before,
            limit=10,
        )
        print(c(f"\n⚠️  将删除 {len(preview)} 条记忆（预览前10条）：", "yellow"))
        for entry in preview:
            print(f"   - [{entry.category}] {entry.preview[:60]}...")
        print(c("\n确认删除？加 --force 执行", "yellow"))
        return 1

    count = cm.batch_delete(
        category=args.category,
        layer=MemoryLayer.from_string(args.layer) if args.layer else None,
        starred=starred,
        created_after=created_after,
        created_before=created_before,
        hard_delete=args.hard,
        actor=args.agent,
        session_id=args.session,
    )

    action = "彻底删除" if args.hard else "移到回收站"
    print(c(f"\n🗑️  已{action} {count} 条记忆", "green"))
    cm.close()
    return 0


def cmd_tag_search(args):
    """按标签搜索记忆"""
    cm = _get_memory(args)
    entries = cm.search_by_tag(
        tag=args.tag,
        category=args.category,
        layer=MemoryLayer.from_string(args.layer) if args.layer else None,
        limit=args.limit,
        offset=args.offset,
    )

    print(f"\n找到 {c(str(len(entries)), 'cyan')} 条带标签 #{args.tag} 的记忆")

    for entry in entries:
        star_mark = "⭐ " if entry.starred else ""
        print(f"\n--- [{entry.category}] "
              f"{star_mark}{entry.preview[:60]} ---")
        print(f"  标签: {', '.join(entry.tags)}")
        print(f"  层级: {entry.layer.value} | 重要性: {entry.importance.value}")
        print(f"  创建: {format_time(entry.created_at)}")

    cm.close()
    return 0


def cmd_deduplicate(args):
    """记忆去重（v5.0.4 新增）"""
    cm = _get_memory(args)

    actually_delete = args.execute

    print(c("正在扫描重复记忆...", "cyan"))
    if not actually_delete:
        result = cm.deduplicate(
            category=args.category,
            similarity_threshold=args.threshold,
            dry_run=True,
            actor=args.agent,
            session_id=args.session,
        )
        print(c("\n🔍 试运行结果（未实际删除）：", "yellow"))
    else:
        result = cm.deduplicate(
            category=args.category,
            similarity_threshold=args.threshold,
            dry_run=False,
            actor=args.agent,
            session_id=args.session,
        )
        print(c("\n🗑️  去重完成：", "green"))

    print(f"   发现重复组：{c(str(result['duplicates_found']), 'cyan')}")
    if not actually_delete:
        print(f"   将删除：    {c(str(result['would_remove']), 'yellow')} 条")
    else:
        print(f"   实际删除：  {c(str(result['removed']), 'green')} 条")

    if result["details"] and args.verbose:
        print(c("\n--- 详情 ---", "purple"))
        for i, d in enumerate(result["details"], 1):
            print(f"\n[{i}] 分类: {d['category']} | 相似度: {d['similarity']}")
            print(f"    保留: {d['keeper_preview'][:60]}")
            print(f"    删除: {d['loser_preview'][:60]}")

    if not actually_delete and result["would_remove"] > 0:
        print(c("\n确认无误后，加 --execute 执行实际删除", "yellow"))

    cm.close()
    return 0


def cmd_export_md(args):
    """导出为 Markdown（v5.0.4 新增）"""
    cm = _get_memory(args)

    try:
        output = _safe_export_path(args.output, "memory_export.md", ".md")
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1

    path = cm.export_as_markdown(
        output_path=str(output),
        category=args.category,
        layer=MemoryLayer.from_string(args.layer) if args.layer else None,
        starred_only=args.starred,
    )

    size = path.stat().st_size
    print(c("\n✅ Markdown 导出成功", "green"))
    print(f"   文件路径: {path}")
    print(f"   文件大小: {format_size(size)}")
    cm.close()
    return 0


def _generate_dashboard_html(dashboard: dict) -> str:
    """生成健康仪表盘 HTML 报告（v5.4.6 新增）"""
    growth_rows = ""
    for point in dashboard.get("growth_curve", [])[-15:]:
        growth_rows += f"<tr><td>{point['date']}</td><td>{point['daily']}</td><td>{point['cumulative']}</td></tr>\n"

    cat_rows = ""
    for cat in dashboard.get("category_distribution", [])[:10]:
        cat_rows += f"<tr><td>{cat['category']}</td><td>{cat['count']}</td></tr>\n"

    decay_rows = ""
    for item in dashboard.get("decay_warnings", [])[:10]:
        decay_rows += (f"<tr><td>{item['content'][:60]}...</td><td>{item['category']}</td>"
                       f"<td>{item['forgetting_score']}</td><td>{item['access_count']}</td></tr>\n")

    access_rows = ""
    for item in dashboard.get("top_access_low_importance", []):
        access_rows += (f"<tr><td>{item['content'][:60]}...</td><td>{item['category']}</td>"
                        f"<td>{item['access_count']}</td><td>{item['importance']}</td></tr>\n")

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>MindForge Health Dashboard</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 20px; background: #f5f5f5; }}
h1 {{ color: #333; }}
h2 {{ color: #555; border-bottom: 2px solid #ddd; padding-bottom: 5px; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; background: white; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #4CAF50; color: white; }}
tr:nth-child(even) {{ background: #f9f9f9; }}
.summary {{ display: flex; gap: 20px; margin: 20px 0; }}
.card {{ background: white; padding: 15px 25px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.card .num {{ font-size: 28px; font-weight: bold; color: #4CAF50; }}
.card .label {{ color: #777; font-size: 13px; }}
</style>
</head>
<body>
<h1>MindForge Health Dashboard</h1>
<p>Generated: {dashboard.get('generated_at', '')}</p>

<div class="summary">
  <div class="card"><div class="num">{dashboard.get('total_memories', 0)}</div><div class="label">Total Memories</div></div>
  <div class="card"><div class="num">{dashboard.get('summary', {}).get('categories', 0)}</div><div class="label">Categories</div></div>
  <div class="card"><div class="num">{dashboard.get('summary', {}).get('decay_warning_count', 0)}</div><div class="label">Decay Warnings</div></div>
  <div class="card"><div class="num">{dashboard.get('summary', {}).get('high_access_low_importance_count', 0)}</div><div class="label">High Access / Low Importance</div></div>
</div>

<h2>Memory Growth Curve (Recent 15 Days)</h2>
<table><tr><th>Date</th><th>Daily</th><th>Cumulative</th></tr>
{growth_rows}</table>

<h2>Category Distribution (Top 10)</h2>
<table><tr><th>Category</th><th>Count</th></tr>
{cat_rows}</table>

<h2>Decay Warnings (Top 10)</h2>
<table><tr><th>Content</th><th>Category</th><th>Forgetting Score</th><th>Access Count</th></tr>
{decay_rows}</table>

<h2>Top 10 High Access / Low Importance</h2>
<table><tr><th>Content</th><th>Category</th><th>Access Count</th><th>Importance</th></tr>
{access_rows}</table>
</body>
</html>"""


def cmd_health(args):
    """数据库健康检查（v5.0.5 新增，v5.4.6 新增 --dashboard）"""
    cm = _get_memory(args)

    # v5.4.6 仪表盘模式
    if getattr(args, 'dashboard', False):
        print_banner()
        print(c("📊 MindForge 记忆健康仪表盘", "bold"))
        print("=" * 50)

        dashboard = cm.health_dashboard()

        if getattr(args, 'html', False):
            # 输出 HTML 报告
            html_report = _generate_dashboard_html(dashboard)
            output_file = Path("./data/health_dashboard.html")
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(html_report, encoding="utf-8")
            print(c(f"\n✅ HTML 报告已生成: {output_file}", "green"))
        else:
            # 输出 JSON
            print(json.dumps(dashboard, ensure_ascii=False, indent=2))

        cm.close()
        return 0

    result = cm.health_check()

    if getattr(args, 'json_output', False):
        _json_out(result)
        cm.close()
        return 0 if result["status"] == "healthy" else (1 if result["status"] == "warning" else 2)

    print_banner()
    print(c("🩺 MindForge 健康检查", "bold"))
    print("=" * 50)

    status = result["status"]
    status_color = {
        "healthy": "green",
        "warning": "yellow",
        "critical": "red",
    }.get(status, "reset")
    status_icon = {"healthy": "✅", "warning": "⚠️", "critical": "🚨"}.get(status, "❓")

    print(f"\n总体状态：{status_icon} {c(status.upper(), status_color)}")
    print(f"完整性检查：{c(result['integrity_check'], 'green' if result['integrity_check'] == 'ok' else 'red')}")
    print(f"总记忆数：  {c(str(result['total_memories']), 'cyan')}")
    print(f"数据库大小：{format_size(result['db_size_bytes'])}")

    idx = result["indexes"]
    print("\n📊 索引状态：")
    print(f"   预期 {idx['expected']} 个，找到 {c(str(idx['found']), 'green' if idx['found'] == idx['expected'] else 'red')} 个")
    if idx["missing"]:
        print(f"   缺失：{', '.join(idx['missing'])}")

    print("\n📋 数据一致性：")
    print(f"   孤立 FTS 记录：    {c(str(result['fts_orphans']), 'yellow' if result['fts_orphans'] else 'green')}")
    print(f"   孤立审计日志：    {c(str(result['audit_orphans']), 'yellow' if result['audit_orphans'] else 'green')}")
    print(f"   加密不一致条目：  {c(str(result['encrypted_inconsistent']), 'red' if result['encrypted_inconsistent'] else 'green')}")

    print("\n💡 建议：")
    for rec in result["recommendations"]:
        print(f"   • {rec}")

    return 0 if status == "healthy" else (1 if status == "warning" else 2)


def cmd_summarize(args):
    """记忆摘要（v5.0.5 新增）"""
    cm = _get_memory(args)
    print_banner()
    print(c("📊 MindForge 记忆摘要", "bold"))
    print("=" * 50)

    result = cm.summarize(
        category=args.category,
        group_by=args.group_by,
    )

    print(f"\n总记忆数：{c(str(result['total']), 'cyan')}")

    print("\n📅 近期活动：")
    print(f"   最近 7 天： {c(str(result['recent_activity']['last_7d']), 'cyan')} 条")
    print(f"   最近 30 天：{c(str(result['recent_activity']['last_30d']), 'cyan')} 条")

    print(f"\n📂 按 {c(args.group_by, 'purple')} 分组：")
    for key, info in result["grouped"].items():
        print(f"\n  ▸ {c(key, 'cyan')} ({info['count']} 条)")
        print(f"    时间范围: {info['oldest'] or '-'} ~ {info['latest'] or '-'}")
        if info["samples"]:
            print("    样例：")
            for s in info["samples"][:2]:
                print(f"      • {s[:60]}{'...' if len(s) >= 60 else ''}")

    if result["top_tags"]:
        print("\n🏷️  热门标签：")
        for tag, count in result["top_tags"][:5]:
            print(f"   #{tag}: {count}")

    cm.close()
    return 0


def cmd_vacuum(args):
    """重建 FTS 索引 + 数据库优化（v5.0.6 新增）"""
    cm = _get_memory(args)
    print_banner()
    print(c("🧹 MindForge FTS 索引重建", "bold"))
    print("=" * 50)

    # 重建前健康状态
    before = cm.health_check()
    print(f"\n重建前：孤立 FTS 记录 = {c(str(before['fts_orphans']), 'yellow' if before['fts_orphans'] else 'green')}")

    print(c("\n正在重建 FTS 索引...", "cyan"))
    result = cm.rebuild_fts()

    # 执行 VACUUM 回收空间（需在非事务模式下执行）
    try:
        conn = cm.storage._get_conn()
        conn.commit()
        conn.isolation_level = None
        conn.execute("VACUUM")
        conn.isolation_level = ""
        vacuum_ok = True
    except sqlite3.OperationalError:
        vacuum_ok = False

    # 重建后健康状态
    after = cm.health_check()
    print(c("\n✅ 重建完成", "green"))
    print(f"   已索引条目：{c(str(result['indexed']), 'cyan')}")
    print(f"   耗时：{result['duration_ms']} ms")
    print(f"   VACUUM：{'✅ 已执行' if vacuum_ok else '⚠️ 跳过'}")
    print(f"\n重建后：孤立 FTS 记录 = {c(str(after['fts_orphans']), 'green' if after['fts_orphans'] == 0 else 'yellow')}")
    print(f"总体状态：{after['status']}")
    cm.close()
    return 0


def cmd_purge_trash(args):
    """清空回收站（v5.0.6 新增）"""
    cm = _get_memory(args)

    # 先统计回收站数量
    trash_items = cm.list(category="trash", limit=100000)

    if not trash_items:
        print(c("回收站为空，无需清理", "yellow"))
        cm.close()
        return 0

    if not args.force:
        print(c(f"\n⚠️  回收站中有 {len(trash_items)} 条记忆将被永久删除", "yellow"))
        print(c("预览（前 10 条）：", "cyan"))
        for entry in trash_items[:10]:
            print(f"   - [{entry.category}] {entry.preview[:60]}")
        print(c("\n确认永久删除？加 --force 执行（此操作不可恢复）", "yellow"))
        cm.close()
        return 1

    count = cm.purge_trash(actor=args.agent, session_id=args.session)
    print(c(f"\n🗑️  已永久删除 {count} 条回收站记忆", "green"))
    cm.close()
    return 0


def cmd_analyze(args):
    """记忆深度分析（v5.0.8 新增）"""
    cm = _get_memory(args)
    print_banner()
    print(c("📊 MindForge 深度分析报告", "bold"))
    print("=" * 50)

    stats = cm.stats()

    print("\n📈 总体概览：")
    print(f"   总记忆数：        {c(str(stats['total']), 'cyan')}")
    print(f"   ⭐ 收藏数：       {c(str(stats.get('starred_count', 0)), 'yellow')}")
    print(f"   数据库大小：      {format_size(stats['db_size_bytes'])}")

    print("\n📅 创建时间分布（最近 7 天）：")
    recent_counts = {}
    now = datetime.now().timestamp()
    day = 24 * 3600
    for i in range(7):
        start = now - (i + 1) * day
        end = now - i * day
        cnt = len(cm.list(created_after=start, created_before=end, limit=99999))
        date_str = datetime.fromtimestamp(end).strftime("%m-%d")
        recent_counts[date_str] = cnt

    max_count = max(recent_counts.values()) if recent_counts else 1
    for date_str in reversed(list(recent_counts.keys())):
        cnt = recent_counts[date_str]
        bar = "█" * int(cnt / max_count * 20) if max_count > 0 else ""
        print(f"   {date_str}: {bar} {c(str(cnt), 'cyan')} 条")

    print("\n🔥 活跃度分析：")
    total_access = sum(e.access_count for e in cm.list(limit=99999))
    avg_access = total_access / stats['total'] if stats['total'] > 0 else 0
    print(f"   总访问次数：      {c(str(total_access), 'purple')}")
    print(f"   平均访问次数：    {avg_access:.2f}")

    hot_entries = sorted(cm.list(limit=100), key=lambda e: e.access_count, reverse=True)[:5]
    if hot_entries:
        print("\n   🔥 热门记忆 TOP5：")
        for i, e in enumerate(hot_entries, 1):
            print(f"      {i}. [{e.category}] {e.preview[:40]}... ({e.access_count} 次访问)")

    print("\n🏷️ 标签分析：")
    top_tags = stats.get("top_tags", {})
    if top_tags:
        tag_list = sorted(top_tags.items(), key=lambda x: x[1], reverse=True)[:10]
        for tag, count in tag_list:
            print(f"   #{tag}: {c(str(count), 'pink')}")

    cm.close()
    return 0


def cmd_import_md(args):
    """从 Markdown 导入记忆（v5.0.8 新增）"""
    cm = _get_memory(args)

    try:
        input_path = _safe_import_path(args.input)
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        return 1

    if not input_path.exists():
        print(c(f"❌ 文件不存在：{input_path}", "red"))
        return 1

    try:
        content = input_path.read_text(encoding='utf-8')
    except (OSError, IOError) as e:
        print(c(f"❌ 读取文件失败：{e}", "red"))
        return 1

    import re
    entries = []
    current_category = "default"
    current_tags = []

    sections = re.split(r'(#{1,2}\s+.+)', content)
    for i in range(0, len(sections), 2):
        text = sections[i].strip()
        if text:
            entries.append({
                'content': text,
                'category': current_category,
                'tags': current_tags,
            })
        if i + 1 < len(sections):
            header = sections[i + 1].strip()
            title = header.lstrip('#').strip()
            current_category = title
            current_tags = re.findall(r'(?:^|\s)#([a-zA-Z]\w*)', title)

    if not entries:
        print(c("⚠️  未找到可导入的内容", "yellow"))
        return 0

    if not args.force:
        print(c(f"\n🔍 将导入 {len(entries)} 条记忆：", "cyan"))
        for e in entries[:5]:
            print(f"   - [{e['category']}] {e['content'][:60]}...")
        if len(entries) > 5:
            print(f"   ... 还有 {len(entries) - 5} 条")
        print(c("\n确认导入？加 --force 执行", "yellow"))
        return 1

    imported = 0
    skipped = 0
    for entry in entries:
        try:
            cm.add(
                content=entry['content'],
                category=entry['category'],
                tags=entry['tags'],
                layer=MemoryLayer.from_string(args.layer) if args.layer else MemoryLayer.short_term,
            )
            imported += 1
        except (ValueError, TypeError):
            skipped += 1

    print(c("\n✅ Markdown 导入完成", "green"))
    print(f"   成功导入：{c(str(imported), 'green')} 条")
    print(f"   导入失败：{c(str(skipped), 'yellow')} 条")
    cm.close()
    return 0


def cmd_migrate(args):
    """数据库迁移（v5.0.8 新增）"""
    cm = _get_memory(args)
    print_banner()
    print(c("🔄 MindForge 数据库迁移", "bold"))
    print("=" * 50)

    current_version = cm.storage.get_db_version()
    latest_version = cm.storage.get_latest_db_version()

    print(f"\n当前版本：{c(str(current_version), 'cyan')}")
    print(f"最新版本：{c(str(latest_version), 'green')}")

    if current_version >= latest_version:
        print(c("\n✅ 数据库已是最新版本，无需迁移", "green"))
        return 0

    if not args.force:
        print(c(f"\n⚠️  将执行从 v{current_version} 到 v{latest_version} 的迁移", "yellow"))
        print(c("确认迁移？加 --force 执行", "yellow"))
        return 1

    try:
        result = cm.storage.migrate_to_latest()
        print(c("\n✅ 迁移完成！", "green"))
        print(f"   迁移脚本：{result['scripts_applied']} 个")
        print(f"   耗时：{result['duration_ms']} ms")
        print(f"   当前版本：{result['final_version']}")
    except (sqlite3.OperationalError, ValueError) as e:
        print(c(f"\n❌ 迁移失败：{e}", "red"))
        return 1

    cm.close()
    return 0


def cmd_export_html(args):
    """导出记忆为 HTML（v5.0.9 新增）"""
    cm = _get_memory(args)
    entries = cm.list(limit=99999)

    if not entries:
        print(c("⚠️  没有可导出的记忆", "yellow"))
        return 0

    html_template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MindForge 导出 - {count} 条记忆</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%); min-height: 100vh; padding: 20px; }}
        .container {{ max-width: 800px; margin: 0 auto; }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        .header h1 {{ color: #00d4ff; font-size: 2rem; margin-bottom: 10px; }}
        .header p {{ color: #888; font-size: 0.9rem; }}
        .memory-card {{ background: rgba(255,255,255,0.05); border-radius: 12px; padding: 20px; margin-bottom: 16px; border: 1px solid rgba(0,212,255,0.1); }}
        .memory-card:hover {{ border-color: rgba(0,212,255,0.3); }}
        .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
        .category {{ background: linear-gradient(135deg, #00d4ff, #7b2cbf); color: white; padding: 4px 12px; border-radius: 20px; font-size: 0.8rem; }}
        .layer {{ font-size: 0.7rem; color: #aaa; }}
        .card-content {{ color: #e0e0e0; line-height: 1.6; margin-bottom: 10px; }}
        .card-footer {{ display: flex; justify-content: space-between; align-items: center; font-size: 0.75rem; color: #666; }}
        .tags {{ display: flex; gap: 6px; }}
        .tag {{ background: rgba(123,44,191,0.3); color: #c792ea; padding: 2px 8px; border-radius: 10px; }}
        .footer {{ text-align: center; margin-top: 40px; color: #555; font-size: 0.8rem; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🧠 MindForge 记忆导出</h1>
            <p>共 {count} 条记忆 · 导出时间：{export_time}</p>
        </div>
        {cards}
        <div class="footer">
            <p>Generated by MindForge v{__version__}</p>
        </div>
    </div>
</body>
</html>"""

    cards_html = ""
    for entry in entries:
        tags_html = "".join(f'<span class="tag">#{html.escape(str(t))}</span>' for t in entry.tags) if entry.tags else ""
        cards_html += f"""
<div class="memory-card">
    <div class="card-header">
        <span class="category">{html.escape(str(entry.category))}</span>
        <span class="layer">{html.escape(entry.layer.value)}</span>
    </div>
    <div class="card-content">{html.escape(entry.content)}</div>
    <div class="card-footer">
        <div class="tags">{tags_html}</div>
        <span>{html.escape(str(format_time(entry.created_at)))}</span>
    </div>
</div>"""

    export_time = html.escape(str(format_time(time.time())))
    html_content = html_template.format(count=len(entries), export_time=export_time, cards=cards_html, __version__=__version__)

    try:
        output_path = _safe_export_path(args.output, "memory_export.html", ".html")
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1
    output_path.write_text(html_content, encoding='utf-8')

    print(c("✅ HTML 导出完成！", "green"))
    print(f"   文件：{output_path}")
    print(f"   记忆数：{len(entries)}")
    cm.close()
    return 0


def cmd_star(args):
    """收藏记忆"""
    cm = _get_memory(args)
    success = cm.star(args.id, actor=args.agent, session_id=args.session)
    if success:
        print(c("\n⭐ 已收藏", "green"))
    else:
        print(c("\n❌ 收藏失败：记忆不存在", "red"))
    cm.close()
    return 0 if success else 1


def cmd_unstar(args):
    """取消收藏"""
    cm = _get_memory(args)
    success = cm.unstar(args.id, actor=args.agent, session_id=args.session)
    if success:
        print(c("\n🗑️  已取消收藏", "yellow"))
    else:
        print(c("\n❌ 取消失败：记忆不存在", "red"))
    cm.close()
    return 0 if success else 1


def cmd_consolidate(args):
    """记忆巩固"""
    cm = _get_memory(args)
    evolution = _lazy_import("MemoryEvolution")(cm.storage)

    print(c("正在执行记忆巩固...", "cyan"))
    result = evolution.consolidate(agent_id=args.agent, session_id=args.session)

    print(c("\n✅ 记忆巩固完成", "green"))
    print(f"   处理: {result['processed']} 条")
    print(f"   提升到长期记忆: {c(str(result['promoted_to_long_term']), 'green')} 条")
    print(f"   降级到感官记忆: {c(str(result['demoted_to_sensory']), 'yellow')} 条")

    stats = evolution.get_evolution_stats()
    print("\n记忆层级分布：")
    print(f"  感官记忆: {stats['sensory']}")
    print(f"  短期记忆: {stats['short_term']}")
    print(f"  长期记忆: {stats['long_term']}")
    print(f"  永久记忆: {stats['permanent']}")
    print(f"  巩固率: {stats['consolidation_rate']:.1%}")

    cm.close()
    return 0


def cmd_graph(args):
    """知识图谱操作"""
    cm = _get_memory(args)
    kg = _lazy_import("KnowledgeGraph")(storage=cm.storage)

    if args.graph_action == "stats":
        stats = kg.get_entity_stats()
        if getattr(args, 'json_output', False):
            _json_out(stats)
            cm.close()
            return 0
        print(c("知识图谱统计", "bold"))
        print(f"  实体总数: {stats['total_entities']}")
        print(f"  关系总数: {stats['total_relations']}")
        print("\n  实体类型:")
        for etype, count in stats['entity_types'].items():
            print(f"    {etype}: {count}")
        print("\n  关系类型:")
        for rtype, count in stats['relation_types'].items():
            print(f"    {rtype}: {count}")

    elif args.graph_action == "related":
        entity = args.entity
        related = kg.get_related_entities(entity, depth=args.depth)
        print(f"\n与 '{c(entity, 'cyan')}' 相关的实体：")
        for name, rel_type, weight in related:
            print(f"  - {name}  [{rel_type}]  (权重: {weight:.2f})")

    elif args.graph_action == "extract":
        text = args.text
        entities = kg.extract_entities(text)
        print(f"\n提取到 {len(entities)} 个实体：")
        for name, etype in entities:
            print(f"  - {name} ({etype})")

    cm.close()
    return 0


def cmd_personality(args):
    """人格化引擎"""
    cm = _get_memory(args)
    pe = _lazy_import("PersonalityEngine")(cm.storage)

    if args.personality_action == "profile":
        profile = pe.get_profile(args.user_id)
        print(c("用户画像", "bold"))
        print(f"  用户ID: {profile.user_id}")
        print(f"  交互次数: {profile.total_interactions}")
        print(f"  最后更新: {format_time(profile.last_updated)}")

        interests = pe.get_top_interests(args.user_id, 5)
        print("\n  兴趣主题 TOP5:")
        for topic, score in interests:
            print(f"    - {topic}: {score:.2f}")

        style = pe.get_recommended_style(args.user_id)
        print("\n  推荐交流风格:")
        for key, value in style.items():
            print(f"    - {key}: {value}")

    elif args.personality_action == "interests":
        interests = pe.get_top_interests(args.user_id, args.limit)
        print(f"\n用户 {args.user_id} 的兴趣 TOP {args.limit}：")
        for topic, score in interests:
            bar = "█" * int(score * 20)
            print(f"  {topic:<20} {bar} {score:.2f}")

    cm.close()
    return 0


def cmd_backup(args):
    """备份"""
    cm = _get_memory(args)
    try:
        backup_dir = _validate_path(args.output or "./data/backup", allow_symlinks=False)
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1
    backup_path = cm.backup(str(backup_dir))
    print(c(f"✅ 备份已创建：{backup_path}", "green"))
    print(f"   大小：{format_size(backup_path.stat().st_size)}")
    cm.close()
    return 0


def cmd_export(args):
    """导出记忆"""
    cm = _get_memory(args)
    layer = MemoryLayer.from_string(args.layer) if args.layer else None

    # 路径安全校验（v5.2.7 新增）
    _fmt_ext_map = {"json": ".json", "csv": ".csv"}
    _fmt_default_map = {"json": "memory_export.json", "csv": "memory_export.csv"}
    ext = _fmt_ext_map.get(args.format)
    if ext:
        try:
            output = _safe_export_path(args.output, _fmt_default_map[args.format], ext)
        except ValueError as e:
            print(c(f"❌ 路径校验失败: {e}", "red"))
            cm.close()
            return 1
    else:
        output = args.output

    if args.format == "json":
        count = cm.export_json(
            output_path=str(output),
            category=args.category,
            layer=layer,
            include_private=args.include_private,
        )
    elif args.format == "csv":
        count = cm.export_csv(
            output_path=str(output),
            category=args.category,
            include_private=args.include_private,
        )
    else:
        print(c(f"❌ 不支持的格式：{args.format}", "red"))
        cm.close()
        return 1

    print(c(f"✅ 导出成功！共 {count} 条记忆", "green"))
    print(f"   文件：{output}")
    print(f"   格式：{args.format.upper()}")
    cm.close()
    return 0


def cmd_import(args):
    """导入记忆"""
    cm = _get_memory(args)
    target_layer = MemoryLayer.from_string(args.target_layer) if args.target_layer else None

    try:
        input_path = _safe_import_path(args.input)
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1

    stats = cm.import_json(
        input_path=str(input_path),
        skip_duplicates=not args.force,
        target_layer=target_layer,
    )

    print(c("📥 导入完成", "bold"))
    print("=" * 40)
    print(f"  成功导入：{c(str(stats['imported']), 'green')} 条")
    print(f"  跳过重复：{c(str(stats['skipped']), 'yellow')} 条")
    print(f"  导入失败：{c(str(stats['failed']), 'red')} 条")
    cm.close()
    return 0


def cmd_compliance(args):
    """合规报告"""
    cm = _get_memory(args)
    privacy_engine = _lazy_import("PrivacyEngine")(cm.storage)
    report = privacy_engine.generate_compliance_report()

    print(c("隐私合规报告", "bold"))
    print("=" * 50)
    print(f"报告时间：{format_time(report['report_time'])}")
    print(f"总记忆数：{report['total_memories']}")
    print(f"私密记忆：{report['private_memories']}")
    print(f"严格隔离：{report['strict_memories']}")
    print(f"活跃授权：{report['active_grants']}")
    print(f"合规状态：{c(report['compliance_status'], 'green' if report['compliance_status'] == 'PASS' else 'yellow')}")
    print("\n按隐私分级统计：")
    for level, count in report["by_privacy"].items():
        print(f"  {level}: {count}")

    cm.close()
    return 0


def cmd_serve(args):
    """启动 Web UI 或 REST API"""
    if getattr(args, 'api', False):
        # v5.4.6 REST API 模式
        print(c("启动 MindForge REST API...", "cyan"))
        cm = _get_memory(args)
        try:
            from api.server import start_api_server
            start_api_server(cm, host=args.host, port=args.port,
                             ssl_certfile=args.ssl_cert,
                             ssl_keyfile=args.ssl_key)
        except KeyboardInterrupt:
            print("\n服务已停止")
        except (OSError, ValueError) as e:
            print(c(f"启动失败：{e}", "red"))
            return 1
        finally:
            cm.close()
        return 0

    # Web UI 模式（原有行为）
    print(c("启动 MindForge Web UI...", "cyan"))
    print(f"  地址: http://{args.host}:{args.port}")
    print(f"  数据库: {args.db_path}")
    print(c("\n  按 Ctrl+C 停止服务", "yellow"))

    try:
        import http.server
        import socketserver

        web_dir = Path(__file__).parent.parent / "website"
        if web_dir.exists():
            import os
            os.chdir(web_dir)

        Handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer((args.host, args.port), Handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    except (OSError, ValueError) as e:
        print(c(f"启动失败：{e}", "red"))
        return 1

    return 0


# ===== 记忆关联命令（v5.2.5 新增）=====

def cmd_link(args):
    """创建记忆关联（双向）"""
    cm = _get_memory(args)

    # 输入校验
    link_type = (args.type or "related").strip()
    note = (args.note or "").strip()

    result = cm.link_memories(
        source_id=args.source_id,
        target_id=args.target_id,
        link_type=link_type,
        note=note,
    )

    if result.get("success"):
        print(c("\n🔗 记忆关联创建成功", "green"))
        print(f"   关联 ID: {c(result['link_id'], 'cyan')}")
        print(f"   源记忆: {result['source_id']}")
        print(f"   目标记忆: {result['target_id']}")
        print(f"   关联类型: {link_type}")
        if note:
            print(f"   备注: {note}")
    else:
        print(c(f"\n❌ 关联失败: {result.get('error', '未知错误')}", "red"))

    cm.close()
    return 0 if result.get("success") else 1


def cmd_links(args):
    """列出记忆的所有关联（双向）"""
    cm = _get_memory(args)
    links = cm.list_links(args.memory_id)

    if not links:
        print(c("\n📭 该记忆暂无关联", "yellow"))
        cm.close()
        return 0

    print(c(f"\n🔗 记忆 {args.memory_id} 的关联（共 {len(links)} 条）", "cyan"))
    print("-" * 60)

    for i, link in enumerate(links, 1):
        type_color = {
            "related": "cyan",
            "depends_on": "yellow",
            "extends": "green",
            "contradicts": "red",
        }.get(link["link_type"], "white")

        content_preview = (link.get("linked_content") or "")[:60]
        if len(link.get("linked_content") or "") > 60:
            content_preview += "..."

        print(f"{i}. {c('[' + link['link_type'] + ']', type_color)} "
              f"→ {link['linked_id']}")
        print(f"   内容: {content_preview}")
        print(f"   分类: {link.get('linked_category', '-')}")
        if link.get("note"):
            print(f"   备注: {link['note']}")
        print(f"   关联 ID: {c(link['link_id'], 'cyan')}")
        print()

    cm.close()
    return 0


def cmd_unlink(args):
    """删除记忆关联"""
    cm = _get_memory(args)
    success = cm.unlink_memories(args.link_id)

    if success:
        print(c(f"\n✅ 已删除关联: {args.link_id}", "green"))
    else:
        print(c(f"\n❌ 删除失败: 关联不存在 {args.link_id}", "red"))

    cm.close()
    return 0 if success else 1


# ===== 置顶命令（v5.2.5 新增）=====

def cmd_pin(args):
    """置顶记忆"""
    cm = _get_memory(args)
    success = cm.pin(args.memory_id)

    if success:
        print(c("\n📌 已置顶", "green"))
        print(f"   记忆 ID: {args.memory_id}")
        print(c("   该记忆将在 list/search 中优先展示", "cyan"))
    else:
        print(c("\n❌ 置顶失败: 记忆不存在", "red"))

    cm.close()
    return 0 if success else 1


def cmd_unpin(args):
    """取消置顶"""
    cm = _get_memory(args)
    success = cm.unpin(args.memory_id)

    if success:
        print(c("\n📍 已取消置顶", "yellow"))
        print(f"   记忆 ID: {args.memory_id}")
    else:
        print(c("\n❌ 取消失败: 记忆不存在", "red"))

    cm.close()
    return 0 if success else 1


def cmd_pinned(args):
    """列出所有置顶记忆"""
    cm = _get_memory(args)
    entries = cm.list_pinned(limit=args.limit)

    if not entries:
        print(c("\n📭 暂无置顶记忆", "yellow"))
        cm.close()
        return 0

    print(c(f"\n📌 置顶记忆（共 {len(entries)} 条）", "cyan"))
    print("=" * 70)

    for i, entry in enumerate(entries, 1):
        content_preview = entry.content[:80]
        if len(entry.content) > 80:
            content_preview += "..."

        tags_str = ", ".join(entry.tags) if entry.tags else "-"
        print(f"{i}. {c(entry.id, 'cyan')}  [{entry.category}]")
        print(f"   {content_preview}")
        print(f"   标签: {tags_str} | 重要度: {entry.importance.value} | 层级: {entry.layer.value}")
        print()

    cm.close()
    return 0


# ===== 记忆版本历史命令（v5.2.7 新增）=====

def cmd_history(args):
    """查看记忆的修改历史"""
    cm = _get_memory(args)
    versions = cm.list_versions(args.memory_id, limit=args.limit)

    if not versions:
        print(c("\n📭 该记忆暂无修改历史", "yellow"))
        cm.close()
        return 0

    print(c(f"\n📜 记忆 {args.memory_id} 的修改历史（共 {len(versions)} 个版本）", "cyan"))
    print("=" * 70)

    for i, v in enumerate(versions, 1):
        print(f"{i}. 版本 v{v['version_number']}  {c(v['version_id'], 'cyan')}")
        print(f"   内容: {v['content_preview']}")
        print(f"   分类: {v.get('category', '-')} | 重要度: {v.get('importance', '-')}")
        print(f"   修改者: {v.get('actor', '-')} | 时间: {format_time(v['changed_at'])}")
        print()

    cm.close()
    return 0

def cmd_rollback(args):
    """回滚记忆到指定历史版本"""
    cm = _get_memory(args)

    # 先获取版本信息展示给用户
    version = cm.get_version(args.version_id)
    if not version:
        print(c(f"\n❌ 版本不存在: {args.version_id}", "red"))
        cm.close()
        return 1

    print(c(f"\n📜 目标版本 v{version['version_number']}", "cyan"))
    print(f"   内容预览: {version['content'][:80]}...")
    print(c("   ⚠️ 当前内容将被保存为新版本后回滚", "yellow"))

    result = cm.rollback_to_version(args.version_id, actor="cli")

    if result.get("success"):
        print(c("\n✅ 回滚成功", "green"))
        print(f"   记忆 ID: {result['memory_id']}")
        print(f"   已保存当前版本为 v{result.get('saved_current_as_version', '?')}")
    else:
        print(c(f"\n❌ 回滚失败: {result.get('error', '未知错误')}", "red"))

    cm.close()
    return 0 if result.get("success") else 1


# ===== v5.2.8 新增命令 =====

def cmd_export_csv(args):
    """导出记忆为 CSV（v5.2.8 新增）"""
    cm = _get_memory(args)

    try:
        output = _safe_export_path(args.output, "memory_export.csv", ".csv")
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1

    count = cm.export_csv(
        output_path=str(output),
        category=args.category,
        include_private=args.include_private,
    )

    size = output.stat().st_size
    print(c("\n✅ CSV 导出成功", "green"))
    print(f"   文件路径: {output}")
    print(f"   导出条数: {count}")
    print(f"   文件大小: {format_size(size)}")
    if not args.include_private:
        print("   隐私过滤: 已排除 PRIVATE/STRICT 记忆")
    cm.close()
    return 0


def cmd_diff(args):
    """对比记忆版本差异（v5.2.8 新增）"""
    import difflib

    cm = _get_memory(args)
    entry = cm.get(args.memory_id)
    if not entry:
        print(c(f"\n❌ 记忆不存在: {args.memory_id}", "red"))
        cm.close()
        return 1

    # 解析对比双方：默认 最新历史版本 vs 当前内容
    if args.version_id:
        version_a = cm.get_version(args.version_id)
        if not version_a:
            print(c(f"\n❌ 版本不存在: {args.version_id}", "red"))
            cm.close()
            return 1
    else:
        versions = cm.list_versions(args.memory_id, limit=1)
        if not versions:
            print(c("\n📭 该记忆暂无历史版本，无法对比", "yellow"))
            cm.close()
            return 0
        version_a = cm.get_version(versions[0]["version_id"])

    if args.against:
        version_b = cm.get_version(args.against)
        if not version_b:
            print(c(f"\n❌ 版本不存在: {args.against}", "red"))
            cm.close()
            return 1
        content_b = version_b["content"]
        label_b = f"v{version_b['version_number']} ({args.against})"
    else:
        content_b = entry.content
        label_b = "当前内容"

    label_a = f"v{version_a['version_number']} ({version_a['version_id']})"

    if version_a["content"] == content_b:
        print(c("\n✅ 两份内容完全一致，无差异", "green"))
        cm.close()
        return 0

    diff = difflib.unified_diff(
        version_a["content"].splitlines(),
        content_b.splitlines(),
        fromfile=label_a,
        tofile=label_b,
        lineterm="",
    )

    print(c("\n🔀 记忆版本差异对比", "cyan"))
    print("=" * 70)
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            print(c(line, "green"))
        elif line.startswith("-") and not line.startswith("---"):
            print(c(line, "red"))
        elif line.startswith("@@"):
            print(c(line, "purple"))
        else:
            print(line)

    cm.close()
    return 0


# ===== 多 Agent 记忆空间命令（v5.2.8 实验性 — v6.0.0 全量推送预览）=====

def _print_experimental_banner():
    if _json_mode:
        return
    print(c("🧪 实验性功能：多 Agent 记忆空间（v6.0.0 全量推送预览，API 可能变化）", "yellow"))


def cmd_space_create(args):
    """创建记忆空间（实验性）"""
    _print_experimental_banner()
    cm = _get_memory(args)
    result = cm.multi_agent.create_space(
        name=args.name,
        owner_agent=args.agent,
        description=args.desc,
        policy=args.policy,
    )
    if result.get("success"):
        print(c("\n✅ 记忆空间已创建", "green"))
        print(f"   空间 ID: {result['space_id']}")
        print(f"   名称: {result['name']}")
        print(f"   Owner: {result['owner_agent']}（已自动加入为 owner）")
        print(f"   策略: {result['policy']}")
    else:
        print(c(f"\n❌ 创建失败: {result.get('error')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_space_list(args):
    """列出记忆空间（实验性）"""
    _print_experimental_banner()
    cm = _get_memory(args)
    spaces = cm.multi_agent.list_spaces(agent_id=args.agent if args.mine else "")

    if not spaces:
        print(c("\n📭 暂无记忆空间", "yellow"))
        cm.close()
        return 0

    print(c(f"\n🌐 记忆空间列表（共 {len(spaces)} 个）", "cyan"))
    print("=" * 70)
    for s in spaces:
        role_info = f" | 我的角色: {s['my_role']}" if s.get("my_role") else ""
        print(f"📦 {c(s['name'], 'bold')} [{s['space_id']}]")
        print(f"   Owner: {s['owner_agent']} | 策略: {s['policy']} | "
              f"成员: {s['member_count']} | 共享记忆: {s['item_count']}{role_info}")
        if s.get("description"):
            print(f"   描述: {s['description']}")
        print(f"   创建: {format_time(s['created_at'])}")
        print()
    cm.close()
    return 0


def cmd_space_join(args):
    """加入记忆空间（实验性）"""
    _print_experimental_banner()
    cm = _get_memory(args)
    result = cm.multi_agent.add_member(
        space_ref=args.space,
        agent_id=args.agent,
        role="reader",
        actor=args.agent,
    )
    if result.get("success"):
        print(c("\n✅ 已加入空间", "green"))
        print(f"   Agent: {result['agent_id']}（角色: {result['role']}）")
        print("   提示: 需要 editor 角色共享记忆时，请联系空间 owner 调整")
    else:
        print(c(f"\n❌ 加入失败: {result.get('error')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_space_add_member(args):
    """添加空间成员（实验性，仅 owner）"""
    _print_experimental_banner()
    cm = _get_memory(args)
    result = cm.multi_agent.add_member(
        space_ref=args.space,
        agent_id=args.member_agent,
        role=args.role,
        actor=args.agent,
    )
    if result.get("success"):
        action = "角色已更新" if result.get("updated") else "成员已添加"
        print(c(f"\n✅ {action}", "green"))
        print(f"   Agent: {result['agent_id']} | 角色: {result['role']}")
    else:
        print(c(f"\n❌ 操作失败: {result.get('error')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_space_share(args):
    """共享记忆到空间（实验性）"""
    _print_experimental_banner()
    cm = _get_memory(args)
    result = cm.multi_agent.share_memory(
        space_ref=args.space,
        memory_id=args.memory_id,
        actor=args.agent,
    )
    if result.get("success"):
        print(c("\n✅ 记忆已共享到空间", "green"))
        print(f"   记忆 ID: {result['memory_id']}")
        print(f"   条目版本: v{result['version']}")
        if result.get("conflict_resolved"):
            print(c(f"   冲突解决: {result['conflict_resolved']}（版本号已递增）", "yellow"))
    else:
        print(c(f"\n❌ 共享失败: {result.get('error')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_space_memories(args):
    """列出空间中的记忆（实验性）"""
    _print_experimental_banner()
    cm = _get_memory(args)
    result = cm.multi_agent.list_space_memories(
        space_ref=args.space,
        actor=args.agent,
        limit=args.limit,
    )
    if not result.get("success"):
        print(c(f"\n❌ {result.get('error')}", "red"))
        cm.close()
        return 1

    items = result["items"]
    if not items:
        print(c(f"\n📭 空间 [{result['space_name']}] 中暂无共享记忆", "yellow"))
        cm.close()
        return 0

    print(c(f"\n🌐 空间 [{result['space_name']}] 的共享记忆（{result['count']} 条）", "cyan"))
    print("=" * 70)
    for i, item in enumerate(items, 1):
        print(f"{i}. [{item['category']}] {item['content_preview']}")
        print(f"   ID: {item['memory_id']} | 版本: v{item['version']} | "
              f"来源: {item['source_agent'] or '-'} | 共享者: {item['added_by']}")
        print(f"   标签: {', '.join(item['tags']) if item['tags'] else '无'} | "
              f"隐私: {item['privacy']} | 时间: {format_time(item['added_at'])}")
        print()
    cm.close()
    return 0


def cmd_space_stats(args):
    """记忆空间统计（实验性）"""
    _print_experimental_banner()
    cm = _get_memory(args)
    result = cm.multi_agent.space_stats(space_ref=args.space or "")
    if not result.get("success"):
        print(c(f"\n❌ {result.get('error')}", "red"))
        cm.close()
        return 1

    if args.space:
        space = result["space"]
        print(c(f"\n📊 空间统计: {space['name']}", "cyan"))
        print("=" * 50)
        print(f"   成员数: {space['member_count']} | 共享记忆: {space['item_count']}")
        print(f"   策略: {space['policy']} | Owner: {space['owner_agent']}")
        print("   角色分布: " + ", ".join(
            f"{role}×{cnt}" for role, cnt in result["members_by_role"].items()))
        if result["top_contributors"]:
            print("   贡献榜:")
            for t in result["top_contributors"]:
                print(f"     {t['agent_id']}: 共享 {t['shared']} 条")
    else:
        print(c("\n📊 多 Agent 记忆空间全局统计", "cyan"))
        print("=" * 50)
        print(f"   空间总数: {result['total_spaces']}")
        print(f"   参与 Agent 数: {result['participating_agents']}")
        print(f"   成员关系总数: {result['total_memberships']}")
        print(f"   共享记忆总数: {result['total_shared_items']}")
        print(f"   目标正式版本: v{result['target_version']}")
    cm.close()
    return 0


def _install_shell_completion(shell: str):
    """安装 Shell 自动补全（v5.4.6 新增）

    生成并安装 bash/zsh/fish 自动补全脚本。
    """
    # 收集所有子命令名称
    commands = [
        "init", "add", "search", "list", "get", "update", "delete",
        "stats", "star", "unstar", "batch-delete", "tag-search",
        "deduplicate", "export-md", "health", "summarize", "vacuum",
        "purge-trash", "analyze", "import-md", "migrate",
        "export-html", "export-xml", "import-xml",
        "export-json", "import-json", "import-csv",
        "merge", "remind", "tags", "cats", "timeline", "top",
        "random", "rename-tag", "rename-cat", "config", "doctor",
        "find", "audit", "recent", "trash", "restore",
        "consolidate", "graph", "personality",
        "agent-stats", "agent-list", "evolve",
        "agent-transfer", "agent-clean", "agent-list-memories",
        "agent-rank", "agent-forget", "agent-profile",
        "agent-merge", "agent-export", "agent-search", "agent-compare",
        "drama-search", "char-ranking", "agent-diff", "agent-purge",
        "drama-progress-update", "drama-rec2",
        "agent-timeline", "agent-heatmap", "drama-binge",
        "char-network", "agent-sentiment", "memory-decay",
        "drama-compare", "char-arc", "memory-cluster",
        "agent-insight", "drama-summary", "scene-tension",
        "memory-link", "memory-recall", "drama-pacing",
        "char-interaction", "quality", "similar",
        "backup", "export", "import", "compliance", "serve",
        "cleanup", "archive", "archived-list", "archived-restore",
        "archived-purge",
        "batch-add", "import-url",
        "export-excel", "import-excel", "copy", "move",
        "fuzzy-search", "search-history",
        "batch-add-tags", "batch-remove-tags", "merge-tags",
        "db-backup", "db-backups", "db-restore", "db-clean-backups",
        "drama-add", "drama-list", "drama-get",
        "memory-reflection", "rebuild-embeddings", "embedding-status",
        "memory-lineage", "memory-reinforce",
        "drama-plot-thread", "drama-episode-curve", "drama-screen-time",
        "fed-acl-add", "fed-acl-remove", "fed-acl-list",
        "fed-acl-check", "fed-acl-stats",
        "share-conflicts", "share-conflict-resolve",
        "share-conflict-dismiss", "share-conflict-stats",
        "export-obsidian",
    ]

    cmds_str = " ".join(commands)

    if shell == "bash":
        script = f"""# MindForge CLI auto-completion (bash)
_mindforge_completions() {{
    local cur prev opts
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    opts="{cmds_str}"
    COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
}}
complete -F _mindforge_completions mindforge
complete -F _mindforge_completions MindForge
"""
        target = Path.home() / ".bash_completion.d" / "mindforge"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script, encoding="utf-8")
        # Also append to .bashrc if not already there
        bashrc = Path.home() / ".bashrc"
        source_line = f"[ -f {target} ] && source {target}"
        if bashrc.exists():
            content = bashrc.read_text(encoding="utf-8", errors="ignore")
            if "mindforge" not in content:
                with open(bashrc, "a", encoding="utf-8") as f:
                    f.write(f"\n{source_line}\n")
        print(c(f"✅ Bash 补全已安装到 {target}", "green"))
        print(c("   重新打开终端或执行 source ~/.bashrc 生效", "yellow"))

    elif shell == "zsh":
        script = f"""#compdef mindforge MindForge
# MindForge CLI auto-completion (zsh)
_mindforge() {{
    local -a commands
    commands=({' '.join(repr(c) for c in commands)})
    _describe 'command' commands
}}
compdef _mindforge mindforge MindForge
"""
        # zsh completion directory
        fpath = Path.home() / ".zsh" / "completions"
        fpath.mkdir(parents=True, exist_ok=True)
        target = fpath / "_mindforge"
        target.write_text(script, encoding="utf-8")
        zshrc = Path.home() / ".zshrc"
        fpath_line = f"fpath=({fpath} $fpath)"
        if zshrc.exists():
            content = zshrc.read_text(encoding="utf-8", errors="ignore")
            if str(fpath) not in content:
                with open(zshrc, "a", encoding="utf-8") as f:
                    f.write(f"\n{fpath_line}\nautoload -Uz compinit && compinit\n")
        print(c(f"✅ Zsh 补全已安装到 {target}", "green"))
        print(c("   重新打开终端或执行 source ~/.zshrc 生效", "yellow"))

    elif shell == "fish":
        script = f"""# MindForge CLI auto-completion (fish)
complete -c mindforge -f -a '{cmds_str}'
complete -c MindForge -f -a '{cmds_str}'
"""
        target = Path.home() / ".config" / "fish" / "completions" / "mindforge.fish"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script, encoding="utf-8")
        print(c(f"✅ Fish 补全已安装到 {target}", "green"))
        print(c("   重新打开终端生效", "yellow"))

import json as _json

def _json_out(data, indent=2):
    """JSON 格式输出（--json 模式）"""
    print(_json.dumps(data, ensure_ascii=False, indent=indent, default=str))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mindforge",
        description=f"MindForge v{__version__} - AI Agent 终身记忆系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", "-v", action="version", version=f"MindForge v{__version__}")
    parser.add_argument("--install-completion", dest="install_completion",
                        choices=["bash", "zsh", "fish"],
                        help="安装 Shell 自动补全（v5.4.6 新增，支持 bash/zsh/fish）")

    parser.add_argument("--db-path", default="./data/memory.db", help="数据库路径")
    parser.add_argument("--key-file", default="./data/.key", help="密钥文件路径")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON 格式输出（供插件/脚本集成使用）")

    sub = parser.add_subparsers(dest="command", required=False)

    # 共享 --json 参数（统一 dest=json_output，所有子命令复用）
    # default=SUPPRESS：子 parser 未显式指定 --json 时不覆盖主 parser 已解析的值
    # （Python 3.13+ subparser 解析会重置 namespace，必须用 SUPPRESS 才能保留前置值）
    json_parser = argparse.ArgumentParser(add_help=False)
    json_parser.add_argument("--json", action="store_true", dest="json_output",
                             default=argparse.SUPPRESS,
                             help="JSON 格式输出（供插件/脚本集成使用）")

    p_init = sub.add_parser("init", help="初始化 MindForge")
    p_init.add_argument("--no-encrypt", action="store_true", help="不启用加密（CI/自动化场景）")
    p_init.add_argument("--password", default=None, help="直接指定密码（非交互式，CI/脚本场景）")


    p_add = sub.add_parser("add", help="添加记忆", parents=[json_parser])
    p_add.add_argument("content", help="记忆内容")
    p_add.add_argument("--category", "-c", help="分类")
    p_add.add_argument("--tags", "-t", nargs="+", help="标签")
    p_add.add_argument("--privacy", "-p", default="INTERNAL",
                       choices=["PUBLIC", "INTERNAL", "PRIVATE", "STRICT"], help="隐私等级")
    p_add.add_argument("--importance", "-i", default="MEDIUM",
                       choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"], help="重要性")
    p_add.add_argument("--layer", "-l", default="short_term",
                       choices=["sensory", "short_term", "long_term", "permanent"], help="记忆层级")
    p_add.add_argument("--type", default="text",
                       choices=["text", "image", "audio", "code", "structured"], help="记忆类型")
    p_add.add_argument("--session", default="cli", help="会话 ID")
    p_add.add_argument("--agent", default="cli", help="Agent ID")
    p_add.add_argument("--star", action="store_true", help="添加后直接收藏")

    p_search = sub.add_parser("search", help="搜索记忆", parents=[json_parser])
    p_search.add_argument("query", help="搜索查询")
    p_search.add_argument("--limit", type=int, default=10, help="最大结果数")
    p_search.add_argument("--category", "-c", help="分类筛选")
    p_search.add_argument("--agent", default="", help="Agent ID")
    p_search.add_argument("--session", default="", help="会话 ID")
    p_search.add_argument("--no-embedding", action="store_true",
                           help="禁用向量检索，降级为 TF-IDF + Fuzzy 搜索（v5.4.5）")

    p_list = sub.add_parser("list", help="列出记忆")
    p_list.add_argument("--category", "-c", help="分类筛选")
    p_list.add_argument("--layer", "-l", help="层级筛选")
    p_list.add_argument("--starred", action="store_true", default=None,
                        help="只显示已收藏")
    p_list.add_argument("--unstarred", action="store_true", default=None,
                        help="只显示未收藏")
    p_list.add_argument("--after", help="创建时间之后 (YYYY-MM-DD 或 ISO 格式)")
    p_list.add_argument("--before", help="创建时间之前 (YYYY-MM-DD 或 ISO 格式)")
    p_list.add_argument("--limit", type=int, default=50, help="数量限制")
    p_list.add_argument("--offset", type=int, default=0, help="偏移量")
    p_list.add_argument("--sort", "-s", default="created_at",
                        choices=["created_at", "updated_at", "last_accessed_at", "access_count", "strength", "forgetting_score"],
                        help="排序字段（v5.1.4 新增）")
    p_list.add_argument("--order", "-o", default="desc",
                        choices=["asc", "desc"],
                        help="排序顺序（v5.1.4 新增）")

    p_get = sub.add_parser("get", help="获取单条记忆（v5.1.1 补全）")
    p_get.add_argument("id", help="记忆 ID")
    p_get.add_argument("--agent", default="cli", help="Agent ID")
    p_get.add_argument("--session", default="cli", help="会话 ID")

    p_update = sub.add_parser("update", help="更新记忆（v5.1.1 补全）")
    p_update.add_argument("id", help="记忆 ID")
    p_update.add_argument("--content", help="新的记忆内容")
    p_update.add_argument("--category", "-c", help="新的分类")
    p_update.add_argument("--tags", "-t", nargs="+", help="新的标签")
    p_update.add_argument("--privacy", "-p",
                          choices=["PUBLIC", "INTERNAL", "PRIVATE", "STRICT"], help="隐私等级")
    p_update.add_argument("--importance", "-i",
                          choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"], help="重要性")
    p_update.add_argument("--layer", "-l",
                          choices=["sensory", "short_term", "long_term", "permanent"],
                          help="记忆层级")
    p_update.add_argument("--star", action="store_true", help="设为收藏")
    p_update.add_argument("--unstar", action="store_true", help="取消收藏")
    p_update.add_argument("--agent", default="cli", help="Agent ID")
    p_update.add_argument("--session", default="cli", help="会话 ID")

    p_delete = sub.add_parser("delete", help="删除记忆（v5.1.1 补全）", parents=[json_parser])
    p_delete.add_argument("id", help="记忆 ID")
    p_delete.add_argument("--hard", action="store_true", help="彻底删除（不可恢复）")
    p_delete.add_argument("--force", action="store_true", help="确认删除")
    p_delete.add_argument("--agent", default="cli", help="Agent ID")
    p_delete.add_argument("--session", default="cli", help="会话 ID")

    p_audit = sub.add_parser("audit", help="查看审计日志（v5.1.1 补全）")
    p_audit.add_argument("--id", help="限定记忆 ID")
    p_audit.add_argument("--actor", help="限定操作者")
    p_audit.add_argument("--limit", type=int, default=100, help="数量限制")

    p_recent = sub.add_parser("recent", help="最近添加的记忆（v5.1.1 新增）")
    p_recent.add_argument("--hours", type=int, default=24, help="最近 N 小时，默认 24")
    p_recent.add_argument("--limit", type=int, default=20, help="数量限制")
    p_recent.add_argument("--offset", type=int, default=0, help="偏移量")

    p_trash = sub.add_parser("trash", help="查看回收站（v5.1.1 新增）")
    p_trash.add_argument("--limit", type=int, default=50, help="数量限制")
    p_trash.add_argument("--offset", type=int, default=0, help="偏移量")

    p_restore = sub.add_parser("restore", help="从回收站恢复记忆（v5.1.1 新增）")
    p_restore.add_argument("id", help="记忆 ID")
    p_restore.add_argument("--agent", default="cli", help="Agent ID")
    p_restore.add_argument("--session", default="cli", help="会话 ID")

    p_stats = sub.add_parser("stats", help="统计信息", parents=[json_parser])
    p_stats.add_argument("--detailed", action="store_true", help="显示详细统计（v5.1.4 新增）")

    p_star = sub.add_parser("star", help="收藏记忆")
    p_star.add_argument("id", help="记忆 ID")
    p_star.add_argument("--agent", default="cli", help="Agent ID")
    p_star.add_argument("--session", default="cli", help="会话 ID")

    p_unstar = sub.add_parser("unstar", help="取消收藏")
    p_unstar.add_argument("id", help="记忆 ID")
    p_unstar.add_argument("--agent", default="cli", help="Agent ID")
    p_unstar.add_argument("--session", default="cli", help="会话 ID")

    p_batch_delete = sub.add_parser("batch-delete", help="批量删除记忆")
    p_batch_delete.add_argument("--category", "-c", help="按分类删除")
    p_batch_delete.add_argument("--layer", "-l", help="按层级删除")
    p_batch_delete.add_argument("--starred", action="store_true", default=None,
                                help="只删除已收藏的")
    p_batch_delete.add_argument("--unstarred", action="store_true", default=None,
                                help="只删除未收藏的")
    p_batch_delete.add_argument("--after", help="删除此时间之后的")
    p_batch_delete.add_argument("--before", help="删除此时间之前的")
    p_batch_delete.add_argument("--hard", action="store_true", help="彻底删除（不可恢复）")
    p_batch_delete.add_argument("--force", action="store_true", help="确认删除（不加则只预览）")
    p_batch_delete.add_argument("--agent", default="cli", help="Agent ID")
    p_batch_delete.add_argument("--session", default="cli", help="会话 ID")

    p_tag_search = sub.add_parser("tag-search", help="按标签搜索记忆")
    p_tag_search.add_argument("tag", help="标签名称")
    p_tag_search.add_argument("--category", "-c", help="分类筛选")
    p_tag_search.add_argument("--layer", "-l", help="层级筛选")
    p_tag_search.add_argument("--limit", type=int, default=50, help="数量限制")
    p_tag_search.add_argument("--offset", type=int, default=0, help="偏移量")

    p_dedup = sub.add_parser("deduplicate", help="记忆去重（v5.0.4 新增）")
    p_dedup.add_argument("--category", "-c", help="限定分类")
    p_dedup.add_argument("--threshold", type=float, default=0.95,
                         help="相似度阈值 (0-1)，默认 0.95")
    p_dedup.add_argument("--execute", action="store_true",
                         help="实际执行删除（不加则默认试运行）")
    p_dedup.add_argument("--verbose", "-v", action="store_true",
                         help="显示详细信息")
    p_dedup.add_argument("--agent", default="cli", help="Agent ID")
    p_dedup.add_argument("--session", default="cli", help="会话 ID")

    p_export_md = sub.add_parser("export-md", help="导出为 Markdown（v5.0.4 新增）")
    p_export_md.add_argument("--output", "-o", default="./data/memory.md",
                             help="输出文件路径")
    p_export_md.add_argument("--category", "-c", help="按分类筛选")
    p_export_md.add_argument("--layer", "-l", help="按层级筛选")
    p_export_md.add_argument("--starred", action="store_true",
                             help="仅导出收藏的记忆")

    p_health = sub.add_parser("health", help="数据库健康检查（v5.0.5 新增）", parents=[json_parser])
    p_health.add_argument("--dashboard", action="store_true",
                           help="输出记忆健康仪表盘 JSON 报告（v5.4.6 新增）")
    p_health.add_argument("--html", action="store_true",
                           help="仪表盘输出为 HTML 格式（配合 --dashboard 使用）")

    p_summarize = sub.add_parser("summarize", help="记忆摘要（v5.0.5 新增）")
    p_summarize.add_argument("--category", "-c", help="限定分类")
    p_summarize.add_argument("--group-by", "-g", default="category",
                             choices=["category", "layer", "importance", "privacy"],
                             help="分组维度（默认 category）")

    p_vacuum = sub.add_parser("vacuum", help="重建 FTS 索引 + 数据库优化（v5.0.6 新增）")

    p_rekey = sub.add_parser(
        "rekey",
        help="更换加密密钥 / 升级 KDF 参数（v5.6.0 新增）",
        description="安全更换加密密码或升级 PBKDF2 迭代次数（60k→600k）。所有加密记忆会被重加密，操作在事务中执行，失败自动回滚。",
    )
    p_rekey.add_argument("--old-password", help="旧密码（不传则交互输入）")
    p_rekey.add_argument("--new-password", help="新密码（不传则交互输入）")
    p_rekey.add_argument("--iterations", type=int, default=None,
                         help="新的 PBKDF2 迭代次数（默认使用当前推荐值 600,000）")
    p_rekey.add_argument("--upgrade-only", action="store_true",
                         help="仅升级 KDF 参数，密码不变（new-password 可省略）")
    p_rekey.add_argument("--yes", action="store_true",
                         help="跳过确认提示（脚本模式用）")

    p_backup = sub.add_parser(
        "backup",
        help="创建记忆库完整备份（v5.6.0 新增）",
        description="打包数据库、密钥文件和配置为单个 ZIP 归档，用于灾备恢复或跨机器迁移。与 export-md 不同，备份是二进制级完整快照。",
        parents=[json_parser],
    )
    p_backup.add_argument("--output", "-o", help="输出 ZIP 路径（默认在 data/ 目录生成带时间戳的文件名）")

    p_backup_restore = sub.add_parser(
        "backup-restore",
        help="从备份文件恢复（v5.6.0 新增）",
        description="从备份 ZIP 恢复数据库和密钥文件。注意：会覆盖目标位置的现有文件。",
        parents=[json_parser],
    )
    p_backup_restore.add_argument("backup_file", help="备份 ZIP 文件路径")
    p_backup_restore.add_argument("--db-path", help="恢复后的数据库路径（默认使用当前配置）")
    p_backup_restore.add_argument("--key-file", help="恢复后的密钥文件路径（加密模式必需）")
    p_backup_restore.add_argument("--force", "-f", action="store_true",
                                  help="覆盖已存在的文件（危险操作）")
    p_backup_restore.add_argument("--yes", action="store_true",
                                  help="跳过确认提示（脚本模式用）")

    p_purge_trash = sub.add_parser("purge-trash", help="清空回收站（v5.0.6 新增）")
    p_purge_trash.add_argument("--force", action="store_true",
                               help="确认永久删除（不加则只预览）")
    p_purge_trash.add_argument("--agent", default="cli", help="Agent ID")
    p_purge_trash.add_argument("--session", default="cli", help="会话 ID")

    p_gc = sub.add_parser(
        "gc",
        help="记忆衰减/垃圾回收（v5.6.0 新增）",
        description="执行记忆生命周期管理：计算衰减分数 → 归档低强度记忆 → 清除过期归档。"
                    "真实记忆系统会忘——长期不召回的记忆降权/归档，防止旧上下文污染新决策。",
        parents=[json_parser],
    )
    p_gc.add_argument("--dry-run", action="store_true",
                      help="只预览不执行（显示将被归档/删除的记忆数）")
    p_gc.add_argument("--status", action="store_true",
                      help="仅查看当前衰减统计，不执行 GC")
    p_gc.add_argument("--skip-purge", action="store_true",
                      help="跳过永久删除步骤（保守模式，只归档不删除）")
    p_gc.add_argument("--policy", choices=["conservative", "balanced", "aggressive"],
                      default="balanced", help="衰减策略（默认 balanced）")
    p_gc.add_argument("--archive-threshold", type=float,
                      help="归档强度阈值（覆盖策略默认值，0.0-1.0）")
    p_gc.add_argument("--purge-after-days", type=int,
                      help="归档保留天数（覆盖策略默认值）")

    p_analyze = sub.add_parser("analyze", help="记忆深度分析（v5.0.8 新增）")

    p_import_md = sub.add_parser("import-md", help="从 Markdown 导入记忆（v5.0.8 新增）")
    p_import_md.add_argument("input", help="Markdown 文件路径")
    p_import_md.add_argument("--layer", "-l", default="short_term",
                             choices=["sensory", "short_term", "long_term", "permanent"],
                             help="目标记忆层级")
    p_import_md.add_argument("--force", action="store_true",
                             help="确认导入（不加则只预览）")

    p_migrate = sub.add_parser("migrate", help="数据库迁移（v5.0.8 新增）")
    p_migrate.add_argument("--force", action="store_true",
                           help="确认迁移（不加则只预览）")

    p_export_html = sub.add_parser("export-html", help="导出记忆为 HTML（v5.0.9 新增）")
    p_export_html.add_argument("--output", "-o", default="memory_export.html",
                               help="输出文件路径")

    p_export_xml = sub.add_parser("export-xml", help="导出记忆为 XML（v5.1.4 新增）")
    p_export_xml.add_argument("--output", "-o", default="./data/memory_export.xml",
                              help="输出文件路径")

    p_import_xml = sub.add_parser("import-xml", help="从 XML 导入记忆（v5.1.4 新增）")
    p_import_xml.add_argument("input", help="XML 文件路径")
    p_import_xml.add_argument("--force", action="store_true", help="强制导入（覆盖重复）")

    p_export_json = sub.add_parser("export-json", help="导出记忆为 JSON（v5.1.5 新增）")
    p_export_json.add_argument("--output", "-o", default="./data/memory_export.json",
                               help="输出文件路径")
    p_export_json.add_argument("--pretty", action="store_true", help="格式化输出")

    p_import_json = sub.add_parser("import-json", help="从 JSON 导入记忆（v5.1.5 新增）")
    p_import_json.add_argument("input", help="JSON 文件路径")
    p_import_json.add_argument("--force", action="store_true", help="强制导入（覆盖重复）")
    p_import_json.add_argument("--dedup-threshold", type=float, default=0.0,
                               help="智能去重阈值 0-1（v5.4.6，0=禁用，0.85=推荐）")

    # v5.4.6 新增
    p_import_csv = sub.add_parser("import-csv", help="从 CSV 导入记忆（v5.4.6 新增）")
    p_import_csv.add_argument("input", help="CSV 文件路径")
    p_import_csv.add_argument("--force", action="store_true", help="强制导入（覆盖重复）")
    p_import_csv.add_argument("--dedup-threshold", type=float, default=0.0,
                               help="智能去重阈值 0-1（0=禁用，0.85=推荐）")
    p_import_csv.add_argument("--layer", choices=["sensory", "short_term", "long_term", "permanent"],
                               help="导入到指定层级")

    p_merge = sub.add_parser("merge", help="合并重复记忆（v5.1.5 新增）")
    p_merge.add_argument("--threshold", type=float, default=0.8, help="相似度阈值")
    p_merge.add_argument("--dry-run", action="store_true", help="仅预览，不执行")

    p_remind = sub.add_parser("remind", help="遗忘提醒（v5.1.5 新增）")
    p_remind.add_argument("--count", type=int, default=10, help="提醒数量")
    p_remind.add_argument("--threshold", type=float, default=0.5, help="遗忘分数阈值")

    p_tags = sub.add_parser("tags", help="标签管理（v5.1.6 新增）")
    p_tags.add_argument("--list", action="store_true", help="列出所有标签")
    p_tags.add_argument("--top", type=int, default=20, help="显示前 N 个标签")

    p_cats = sub.add_parser("cats", help="分类统计（v5.1.6 新增）")
    p_cats.add_argument("--top", type=int, default=20, help="显示前 N 个分类")

    p_timeline = sub.add_parser("timeline", help="时间线视图（v5.1.6 新增）")
    p_timeline.add_argument("--days", type=int, default=30, help="查看最近 N 天")
    p_timeline.add_argument("--category", help="按分类筛选")

    p_top = sub.add_parser("top", help="热门记忆（v5.1.6 新增）")
    p_top.add_argument("--count", type=int, default=10, help="显示数量")
    p_top.add_argument("--by", default="access_count", choices=["access_count", "strength", "created_at"],
                       help="排序依据")

    p_random = sub.add_parser("random", help="随机闪卡复习（v5.1.7 新增）")
    p_random.add_argument("--count", "-n", type=int, default=1, help="随机记忆数量")
    p_random.add_argument("--category", "-c", help="按分类筛选")

    p_rename_tag = sub.add_parser("rename-tag", help="重命名标签（v5.1.7 新增）")
    p_rename_tag.add_argument("old", help="旧标签名")
    p_rename_tag.add_argument("new", help="新标签名")
    p_rename_tag.add_argument("--force", "-f", action="store_true", help="强制确认")

    p_rename_cat = sub.add_parser("rename-cat", help="重命名分类（v5.1.7 新增）")
    p_rename_cat.add_argument("old", help="旧分类名")
    p_rename_cat.add_argument("new", help="新分类名")
    p_rename_cat.add_argument("--force", "-f", action="store_true", help="强制确认")

    p_config = sub.add_parser("config", help="查看配置（v5.1.7 新增）")

    p_doctor = sub.add_parser("doctor", help="全面诊断数据库（v5.1.8 新增）")
    p_doctor.add_argument("--fix", action="store_true", help="自动修复可修复的问题")

    p_find = sub.add_parser("find", help="高级查找记忆（v5.1.8 新增）")
    p_find.add_argument("--category", "-c", help="按分类筛选")
    p_find.add_argument("--layer", "-l", help="按层级筛选")
    p_find.add_argument("--tags", nargs="+", help="按标签筛选（支持多个）")
    p_find.add_argument("--keyword", "-k", help="关键词搜索")
    p_find.add_argument("--starred", action="store_true", help="仅收藏")
    p_find.add_argument("--after", type=float, help="起始时间戳")
    p_find.add_argument("--before", type=float, help="截止时间戳")
    p_find.add_argument("--limit", "-n", type=int, help="结果数量限制")

    p_export_excel = sub.add_parser("export-excel", help="导出记忆为 Excel（v5.1.9 新增）")
    p_export_excel.add_argument("--output", "-o", default="./data/memory_export.xlsx",
                                help="输出文件路径")
    p_export_excel.add_argument("--category", "-c", help="按分类筛选")
    p_export_excel.add_argument("--layer", "-l", help="按层级筛选")
    p_export_excel.add_argument("--starred", action="store_true", help="仅导出收藏的记忆")

    p_import_excel = sub.add_parser("import-excel", help="从 Excel 导入记忆（v5.1.9 新增）")
    p_import_excel.add_argument("input", help="Excel 文件路径")
    p_import_excel.add_argument("--category", "-c", help="目标分类（覆盖文件中的分类）")
    p_import_excel.add_argument("--layer", "-l", default="short_term",
                                choices=["sensory", "short_term", "long_term", "permanent"],
                                help="目标记忆层级")
    p_import_excel.add_argument("--force", action="store_true", help="强制导入（覆盖重复）")

    p_copy = sub.add_parser("copy", help="复制记忆到新分类（v5.1.9 新增）")
    p_copy.add_argument("id", help="记忆 ID")
    p_copy.add_argument("category", help="目标分类")
    p_copy.add_argument("--agent", default="cli", help="Agent ID")
    p_copy.add_argument("--session", default="cli", help="会话 ID")

    p_move = sub.add_parser("move", help="移动记忆到新分类（v5.1.9 新增）")
    p_move.add_argument("id", help="记忆 ID")
    p_move.add_argument("category", help="目标分类")
    p_move.add_argument("--agent", default="cli", help="Agent ID")
    p_move.add_argument("--session", default="cli", help="会话 ID")

    # ===== v5.2.0 新增命令 =====

    p_fuzzy_search = sub.add_parser("fuzzy-search", help="模糊搜索记忆（v5.2.0 新增）")
    p_fuzzy_search.add_argument("query", help="搜索关键词")
    p_fuzzy_search.add_argument("--category", "-c", help="按分类筛选")
    p_fuzzy_search.add_argument("--layer", "-l", help="按层级筛选")
    p_fuzzy_search.add_argument("--limit", "-n", type=int, default=20, help="结果数量限制")
    p_fuzzy_search.add_argument("--threshold", "-t", type=float, default=0.3, help="相似度阈值")
    p_fuzzy_search.add_argument("--highlight", action="store_true", help="高亮显示匹配词")

    p_search_history = sub.add_parser("search-history", help="查看搜索历史（v5.2.0 新增）")
    p_search_history.add_argument("--limit", "-n", type=int, default=20, help="显示数量")

    p_batch_add_tags = sub.add_parser("batch-add-tags", help="批量添加标签（v5.2.0 新增）")
    p_batch_add_tags.add_argument("--ids", help="记忆 ID 列表，逗号分隔")
    p_batch_add_tags.add_argument("--tags", required=True, help="标签列表，逗号分隔")
    p_batch_add_tags.add_argument("--category", "-c", help="按分类批量添加（替代 --ids）")
    p_batch_add_tags.add_argument("--agent", default="cli", help="Agent ID")
    p_batch_add_tags.add_argument("--session", default="cli", help="会话 ID")

    p_batch_remove_tags = sub.add_parser("batch-remove-tags", help="批量移除标签（v5.2.0 新增）")
    p_batch_remove_tags.add_argument("--ids", help="记忆 ID 列表，逗号分隔")
    p_batch_remove_tags.add_argument("--tags", required=True, help="标签列表，逗号分隔")
    p_batch_remove_tags.add_argument("--agent", default="cli", help="Agent ID")
    p_batch_remove_tags.add_argument("--session", default="cli", help="会话 ID")

    p_merge_tags = sub.add_parser("merge-tags", help="合并标签（v5.2.0 新增）")
    p_merge_tags.add_argument("--source", required=True, help="源标签列表，逗号分隔")
    p_merge_tags.add_argument("--target", required=True, help="目标标签名")
    p_merge_tags.add_argument("--force", action="store_true", help="跳过确认")
    p_merge_tags.add_argument("--agent", default="cli", help="Agent ID")
    p_merge_tags.add_argument("--session", default="cli", help="会话 ID")

    p_db_backup = sub.add_parser("db-backup", help="创建数据库备份（v5.2.0 新增）")
    p_db_backup.add_argument("--dir", default="./data/backups", help="备份目录")

    p_db_backups = sub.add_parser("db-backups", help="列出备份文件（v5.2.0 新增）")
    p_db_backups.add_argument("--dir", default="./data/backups", help="备份目录")

    p_db_restore = sub.add_parser("db-restore", help="从备份恢复（v5.2.0 新增）")
    p_db_restore.add_argument("backup", help="备份文件路径")
    p_db_restore.add_argument("--no-pre-backup", action="store_true", help="恢复前不先备份当前数据")
    p_db_restore.add_argument("--force", action="store_true", help="跳过确认")

    p_db_clean_backups = sub.add_parser("db-clean-backups", help="清理旧备份（v5.2.0 新增）")
    p_db_clean_backups.add_argument("--dir", default="./data/backups", help="备份目录")
    p_db_clean_backups.add_argument("--keep", type=int, default=10, help="保留数量")
    p_db_clean_backups.add_argument("--force", action="store_true", help="跳过确认")

    # ===== AI 短剧记忆模块（v5.2.1 新增）=====

    p_drama_add = sub.add_parser("drama-add", help="添加短剧（v5.2.1 新增）")
    p_drama_add.add_argument("title", help="短剧标题")
    p_drama_add.add_argument("--genre", "-g", default="other",
                             choices=["romance", "suspense", "comedy", "action", "horror", "scifi", "fantasy", "drama", "other"],
                             help="短剧类型")
    p_drama_add.add_argument("--episodes", "-e", type=int, default=0, help="总集数")
    p_drama_add.add_argument("--status", "-s", default="planned",
                             choices=["watching", "completed", "planned", "dropped"],
                             help="观看状态")
    p_drama_add.add_argument("--platform", default="", help="播放平台")
    p_drama_add.add_argument("--rating", type=float, default=0.0, help="评分 (0-10)")
    p_drama_add.add_argument("--description", "-d", default="", help="简介")
    p_drama_add.add_argument("--tags", "-t", nargs="+", help="标签")
    p_drama_add.add_argument("--cover", default="", help="封面 URL")

    p_drama_list = sub.add_parser("drama-list", help="列出短剧（v5.2.1 新增）")
    p_drama_list.add_argument("--genre", "-g", help="按类型筛选")
    p_drama_list.add_argument("--status", "-s", help="按状态筛选")
    p_drama_list.add_argument("--platform", help="按平台筛选")
    p_drama_list.add_argument("--min-rating", type=float, default=0.0, help="最低评分")
    p_drama_list.add_argument("--limit", type=int, default=50, help="数量限制")
    p_drama_list.add_argument("--offset", type=int, default=0, help="偏移量")
    p_drama_list.add_argument("--sort", default="updated_at",
                              choices=["created_at", "updated_at", "rating", "last_watched_at", "title"],
                              help="排序字段")
    p_drama_list.add_argument("--order", default="desc", choices=["asc", "desc"], help="排序顺序")

    p_drama_get = sub.add_parser("drama-get", help="获取短剧详情（v5.2.1 新增）")
    p_drama_get.add_argument("id", help="短剧 ID")

    p_drama_update = sub.add_parser("drama-update", help="更新短剧（v5.2.1 新增）")
    p_drama_update.add_argument("id", help="短剧 ID")
    p_drama_update.add_argument("--title", help="新标题")
    p_drama_update.add_argument("--genre", "-g",
                                choices=["romance", "suspense", "comedy", "action", "horror", "scifi", "fantasy", "drama", "other"],
                                help="新类型")
    p_drama_update.add_argument("--episodes", "-e", type=int, help="总集数")
    p_drama_update.add_argument("--current", type=int, help="当前看到第几集")
    p_drama_update.add_argument("--status", "-s",
                                choices=["watching", "completed", "planned", "dropped"],
                                help="观看状态")
    p_drama_update.add_argument("--platform", help="播放平台")
    p_drama_update.add_argument("--rating", type=float, help="评分")
    p_drama_update.add_argument("--description", "-d", help="简介")
    p_drama_update.add_argument("--tags", "-t", nargs="+", help="标签")
    p_drama_update.add_argument("--cover", help="封面 URL")
    p_drama_update.add_argument("--watched", action="store_true", help="标记为刚看过（更新观看时间）")

    p_drama_delete = sub.add_parser("drama-delete", help="删除短剧（v5.2.1 新增）")
    p_drama_delete.add_argument("id", help="短剧 ID")
    p_drama_delete.add_argument("--force", action="store_true", help="确认删除")

    p_drama_stats = sub.add_parser("drama-stats", help="短剧统计（v5.2.1 新增）")

    # v5.2.2 新增命令
    p_drama_recommend = sub.add_parser("drama-recommend", help="AI 智能推荐短剧（v5.2.2 新增）")
    p_drama_recommend.add_argument("--genre", "-g",
                                    choices=["romance", "suspense", "comedy", "action", "horror", "scifi", "fantasy", "drama", "other"],
                                    help="按类型筛选")
    p_drama_recommend.add_argument("--min-rating", type=float, default=7.0, help="最低评分")
    p_drama_recommend.add_argument("--limit", "-n", type=int, default=5, help="推荐数量")
    p_drama_recommend.add_argument("--exclude", nargs="+", help="排除的短剧 ID")

    p_drama_progress = sub.add_parser("drama-progress", help="观看进度统计（v5.2.2 新增）")

    p_drama_export = sub.add_parser("drama-export", help="导出短剧数据（v5.2.2 新增）")
    p_drama_export.add_argument("--output", "-o", default="./data/drama_export.json", help="输出文件")
    p_drama_export.add_argument("--ids", nargs="+", help="指定导出的短剧 ID 列表（默认全部）")

    # ===== v5.2.9 AI 短剧增强 =====

    p_drama_import = sub.add_parser("drama-import", help="从 JSON 批量导入短剧（v5.2.9 新增）")
    p_drama_import.add_argument("input", help="JSON 文件路径（与 drama-export 导出结构相同）")
    p_drama_import.add_argument("--overwrite", action="store_true",
                                 help="不跳过已存在的短剧（按 title 匹配，默认跳过）")

    p_drama_stars = sub.add_parser("drama-stars", help="高分短剧排行榜（v5.2.9 新增）")
    p_drama_stars.add_argument("--genre", "-g", default=None, help="类型过滤（ROMANCE/ACTION/...）")
    p_drama_stars.add_argument("--min-rating", "-r", type=float, default=0.0,
                                help="最低评分（0-10，默认 0）")
    p_drama_stars.add_argument("--limit", "-n", type=int, default=50, help="返回数量（默认 50）")

    p_scene_list_lines = sub.add_parser("scene-list-lines", help="按场次列出台词（v5.2.9 新增）")
    p_scene_list_lines.add_argument("scene_id", help="场次 ID")
    p_scene_list_lines.add_argument("--limit", "-n", type=int, default=500, help="数量限制（默认 500）")
    p_scene_list_lines.add_argument("--offset", "-o", type=int, default=0, help="偏移量")

    p_char_list_lines = sub.add_parser("char-list-lines", help="按角色列出台词（v5.2.9 新增）")
    p_char_list_lines.add_argument("char_id", help="角色 ID")
    p_char_list_lines.add_argument("--drama-id", default=None, help="可选：仅列出此短剧下的台词")
    p_char_list_lines.add_argument("--limit", "-n", type=int, default=500, help="数量限制（默认 500）")
    p_char_list_lines.add_argument("--offset", "-o", type=int, default=0, help="偏移量")

    # ===== v5.3.0 AI 短剧增强 =====

    p_drama_info = sub.add_parser("drama-info", help="短剧深度统计（v5.3.0 新增）")
    p_drama_info.add_argument("drama_id", help="短剧 ID")

    p_line_random = sub.add_parser("line-random", help="随机抽取台词（v5.3.0 新增）")
    p_line_random.add_argument("--drama-id", "-d", default=None, help="限定短剧 ID")
    p_line_random.add_argument("--char-id", "-c", default=None, help="限定角色 ID")
    p_line_random.add_argument("--classic", action="store_true", help="仅经典台词")
    p_line_random.add_argument("--count", "-n", type=int, default=1, help="抽取数量（默认 1，上限 100）")

    p_char_profile = sub.add_parser("char-profile", help="角色画像分析（v5.3.0 新增）")
    p_char_profile.add_argument("char_id", help="角色 ID")
    p_char_profile.add_argument("--drama-id", "-d", default=None, help="限定短剧 ID")

    # 台词相关
    p_line_add = sub.add_parser("line-add", help="添加短剧台词（v5.2.1 新增）")
    p_line_add.add_argument("drama_id", help="短剧 ID")
    p_line_add.add_argument("line", help="台词内容")
    p_line_add.add_argument("--character", "-c", default="", help="角色名")
    p_line_add.add_argument("--char-id", default="", help="角色 ID")
    p_line_add.add_argument("--scene-id", default="", help="场次 ID")
    p_line_add.add_argument("--context", default="", help="上下文")
    p_line_add.add_argument("--episode", "-e", type=int, default=0, help="集数")
    p_line_add.add_argument("--timestamp", default="", help="时间戳")
    p_line_add.add_argument("--classic", action="store_true", help="标记为经典台词")
    p_line_add.add_argument("--tags", "-t", nargs="+", help="标签")

    p_line_list = sub.add_parser("line-list", help="列出短剧台词（v5.2.1 新增）")
    p_line_list.add_argument("--drama-id", help="短剧 ID")
    p_line_list.add_argument("--scene-id", help="场次 ID")
    p_line_list.add_argument("--char-id", help="角色 ID")
    p_line_list.add_argument("--classic", action="store_true", default=None, help="仅经典台词")
    p_line_list.add_argument("--episode", type=int, help="集数")
    p_line_list.add_argument("--limit", type=int, default=100, help="数量限制")
    p_line_list.add_argument("--offset", type=int, default=0, help="偏移量")

    p_line_search = sub.add_parser("line-search", help="搜索台词（v5.2.1 新增）")
    p_line_search.add_argument("query", help="搜索关键词")
    p_line_search.add_argument("--drama-id", help="限定短剧 ID")
    p_line_search.add_argument("--classic-only", action="store_true", help="仅搜索经典台词")
    p_line_search.add_argument("--limit", type=int, default=20, help="数量限制")

    p_line_classic = sub.add_parser("line-classic", help="经典台词（v5.2.1 新增）")
    p_line_classic.add_argument("--drama-id", help="限定短剧 ID")
    p_line_classic.add_argument("--limit", type=int, default=20, help="数量限制")

    p_line_update = sub.add_parser("line-update", help="更新台词（v5.2.1 新增）")
    p_line_update.add_argument("id", help="台词 ID")
    p_line_update.add_argument("--line", help="新台词内容")
    p_line_update.add_argument("--character", help="新角色名")
    p_line_update.add_argument("--context", help="新上下文")
    p_line_update.add_argument("--classic", action="store_true", default=None, help="标记为经典")
    p_line_update.add_argument("--tags", "-t", nargs="+", help="新标签")

    p_line_delete = sub.add_parser("line-delete", help="删除台词（v5.2.1 新增）")
    p_line_delete.add_argument("id", help="台词 ID")
    p_line_delete.add_argument("--force", action="store_true", help="确认删除")

    # 角色相关
    p_char_add = sub.add_parser("char-add", help="添加短剧角色（v5.2.1 新增）")
    p_char_add.add_argument("drama_id", help="短剧 ID")
    p_char_add.add_argument("name", help="角色名")
    p_char_add.add_argument("--role", default="supporting",
                            choices=["lead", "supporting", "guest", "villain", "mentor"],
                            help="角色定位")
    p_char_add.add_argument("--actor", default="", help="演员名")
    p_char_add.add_argument("--description", "-d", default="", help="角色描述")
    p_char_add.add_argument("--personality", "-p", default="", help="性格特点")
    p_char_add.add_argument("--avatar", default="", help="头像 URL")
    p_char_add.add_argument("--tags", "-t", nargs="+", help="标签")

    p_char_list = sub.add_parser("char-list", help="列出短剧角色（v5.2.1 新增）")
    p_char_list.add_argument("--drama-id", help="短剧 ID")
    p_char_list.add_argument("--role", help="按角色定位筛选")
    p_char_list.add_argument("--limit", type=int, default=100, help="数量限制")
    p_char_list.add_argument("--offset", type=int, default=0, help="偏移量")

    p_char_get = sub.add_parser("char-get", help="获取角色详情（v5.2.1 新增）")
    p_char_get.add_argument("id", help="角色 ID")

    p_char_update = sub.add_parser("char-update", help="更新角色（v5.2.1 新增）")
    p_char_update.add_argument("id", help="角色 ID")
    p_char_update.add_argument("--name", help="新角色名")
    p_char_update.add_argument("--role",
                               choices=["lead", "supporting", "guest", "villain", "mentor"],
                               help="新角色定位")
    p_char_update.add_argument("--actor", help="新演员名")
    p_char_update.add_argument("--description", "-d", help="新描述")
    p_char_update.add_argument("--personality", "-p", help="新性格")
    p_char_update.add_argument("--avatar", help="新头像 URL")
    p_char_update.add_argument("--tags", "-t", nargs="+", help="新标签")

    p_char_delete = sub.add_parser("char-delete", help="删除角色（v5.2.1 新增）")
    p_char_delete.add_argument("id", help="角色 ID")
    p_char_delete.add_argument("--force", action="store_true", help="确认删除")

    # 场次相关
    p_scene_add = sub.add_parser("scene-add", help="添加短剧场次（v5.2.1 新增）")
    p_scene_add.add_argument("drama_id", help="短剧 ID")
    p_scene_add.add_argument("episode", type=int, help="集数")
    p_scene_add.add_argument("scene_number", type=int, help="场次号")
    p_scene_add.add_argument("title", help="场次标题")
    p_scene_add.add_argument("--content", "-c", default="", help="场次内容")
    p_scene_add.add_argument("--location", "-l", default="", help="地点")
    p_scene_add.add_argument("--time", default="", help="时间（日/夜）")
    p_scene_add.add_argument("--tags", "-t", nargs="+", help="标签")

    p_scene_list = sub.add_parser("scene-list", help="列出短剧场次（v5.2.1 新增）")
    p_scene_list.add_argument("--drama-id", help="短剧 ID")
    p_scene_list.add_argument("--episode", type=int, help="按集数筛选")
    p_scene_list.add_argument("--limit", type=int, default=100, help="数量限制")
    p_scene_list.add_argument("--offset", type=int, default=0, help="偏移量")

    p_scene_get = sub.add_parser("scene-get", help="获取场次详情（v5.2.1 新增）")
    p_scene_get.add_argument("id", help="场次 ID")

    p_scene_update = sub.add_parser("scene-update", help="更新场次（v5.2.1 新增）")
    p_scene_update.add_argument("id", help="场次 ID")
    p_scene_update.add_argument("--title", help="新标题")
    p_scene_update.add_argument("--content", "-c", help="新内容")
    p_scene_update.add_argument("--location", "-l", help="新地点")
    p_scene_update.add_argument("--time", help="新时间")
    p_scene_update.add_argument("--tags", "-t", nargs="+", help="新标签")

    p_scene_delete = sub.add_parser("scene-delete", help="删除场次（v5.2.1 新增）")
    p_scene_delete.add_argument("id", help="场次 ID")
    p_scene_delete.add_argument("--force", action="store_true", help="确认删除")

    p_consolidate = sub.add_parser("consolidate", help="记忆巩固")
    p_consolidate.add_argument("--agent", default="cli", help="Agent ID")
    p_consolidate.add_argument("--session", default="cli", help="会话 ID")

    p_graph = sub.add_parser("graph", help="知识图谱", parents=[json_parser])
    p_graph.add_argument("graph_action", choices=["stats", "related", "extract"], help="操作")
    p_graph.add_argument("--entity", help="实体名称")
    p_graph.add_argument("--depth", type=int, default=2, help="深度")
    p_graph.add_argument("--text", help="要提取的文本")

    p_personality = sub.add_parser("personality", help="人格化")
    p_personality.add_argument("personality_action", choices=["profile", "interests"], help="操作")
    p_personality.add_argument("--user-id", default="default", help="用户 ID")
    p_personality.add_argument("--limit", type=int, default=10, help="数量限制")

    # Agent 记忆统计（v5.2.2 新增）
    p_agent_stats = sub.add_parser("agent-stats", help="Agent 记忆统计（v5.2.2 新增）")
    p_agent_stats.add_argument("--agent", "-a", help="指定 Agent ID（默认统计全部）")

    p_agent_list = sub.add_parser("agent-list", help="列出 Agent 的记忆（v5.2.2 新增）")
    p_agent_list.add_argument("agent", help="Agent ID")
    p_agent_list.add_argument("--limit", "-n", type=int, default=50, help="数量限制")
    p_agent_list.add_argument("--offset", type=int, default=0, help="偏移量")

    p_evolve = sub.add_parser("evolve", help="记忆演化（v5.2.2 新增）")
    p_evolve.add_argument("--dry-run", action="store_true", help="仅统计不执行")

    p_agent_transfer = sub.add_parser("agent-transfer", help="迁移 Agent 记忆（v5.2.2 新增）")
    p_agent_transfer.add_argument("from_agent", help="源 Agent ID")
    p_agent_transfer.add_argument("to_agent", help="目标 Agent ID")
    p_agent_transfer.add_argument("--category", "-c", help="仅迁移指定分类")

    p_agent_clean = sub.add_parser("agent-clean", help="清理 Agent 旧记忆（v5.2.2 新增）")
    p_agent_clean.add_argument("agent", help="Agent ID")
    p_agent_clean.add_argument("--days", "-d", type=int, default=90, help="清理超过多少天的记忆（默认 90 天）")
    p_agent_clean.add_argument("--max-importance", "-m", default=None, help="最高清理的重要级别（LOW/MEDIUM/HIGH/CRITICAL）")
    p_agent_clean.add_argument("--dry-run", action="store_true", help="仅统计不执行")

    # ===== v5.2.9 Agent 记忆增强 =====

    p_agent_memories = sub.add_parser("agent-list-memories", help="列出 Agent 记忆（v5.2.9 新增）")
    p_agent_memories.add_argument("agent", help="Agent ID")
    p_agent_memories.add_argument("--limit", "-n", type=int, default=50, help="返回数量（默认 50）")
    p_agent_memories.add_argument("--offset", "-o", type=int, default=0, help="偏移量")
    p_agent_memories.add_argument("--format", "-f", choices=["table", "json"], default="table", help="输出格式")

    p_agent_rank = sub.add_parser("agent-rank", help="Agent 记忆排行榜（v5.2.9 新增）")
    p_agent_rank.add_argument("--by", "-b", choices=["count", "last_active", "avg_importance", "starred"],
                               default="count", help="排序维度（默认 count）")
    p_agent_rank.add_argument("--limit", "-n", type=int, default=20, help="返回数量（默认 20）")

    p_agent_forget = sub.add_parser("agent-forget", help="遗忘 Agent 低质量旧记忆（v5.2.9 新增）")
    p_agent_forget.add_argument("agent", help="Agent ID")
    p_agent_forget.add_argument("--min-score", "-s", type=int, default=30,
                                 help="质量分数阈值，低于此分数会被遗忘（默认 30，范围 0-100）")
    p_agent_forget.add_argument("--days", "-d", type=int, default=30,
                                 help="只遗忘超过多少天未更新的记忆（默认 30 天）")
    p_agent_forget.add_argument("--dry-run", action="store_true", help="仅预览不执行")

    # ===== v5.3.0 Agent 记忆增强 =====

    p_agent_profile = sub.add_parser("agent-profile", help="Agent 记忆画像（v5.3.0 新增）")
    p_agent_profile.add_argument("agent", help="Agent ID")

    p_agent_merge = sub.add_parser("agent-merge", help="合并两个 Agent 的记忆（v5.3.0 新增）")
    p_agent_merge.add_argument("from_agent", help="源 Agent ID")
    p_agent_merge.add_argument("to_agent", help="目标 Agent ID")
    p_agent_merge.add_argument("--dedup", "-d", choices=["exact", "none"], default="exact",
                                help="去重模式（exact=内容完全相同则跳过，none=不去重，默认 exact）")
    p_agent_merge.add_argument("--dry-run", action="store_true", help="仅预览不执行")

    p_agent_export = sub.add_parser("agent-export", help="导出 Agent 记忆为 JSON 包（v5.3.0 新增）")
    p_agent_export.add_argument("agent", help="Agent ID")
    p_agent_export.add_argument("--output", "-o", default="./data/agent_export.json", help="输出文件路径")
    p_agent_export.add_argument("--include-audit", action="store_true", help="包含审计日志")

    # ===== v5.3.1 新增 Agent 记忆命令 =====
    p_agent_search = sub.add_parser("agent-search", help="在指定 Agent 的记忆中搜索关键词（v5.3.1 新增）")
    p_agent_search.add_argument("agent", help="Agent ID")
    p_agent_search.add_argument("keyword", help="搜索关键词")
    p_agent_search.add_argument("--limit", "-n", type=int, default=50, help="数量限制（1-500）")
    p_agent_search.add_argument("--offset", type=int, default=0, help="偏移量")
    p_agent_search.add_argument("--format", "-f", choices=["table", "json"], default="table", help="输出格式")

    p_agent_compare = sub.add_parser("agent-compare", help="对比两个 Agent 的记忆差异（v5.3.1 新增）")
    p_agent_compare.add_argument("agent_a", help="Agent A ID")
    p_agent_compare.add_argument("agent_b", help="Agent B ID")

    # ===== v5.3.1 新增 AI 短剧命令 =====
    p_drama_search = sub.add_parser("drama-search", help="按关键词搜索短剧（v5.3.1 新增）")
    p_drama_search.add_argument("keyword", help="搜索关键词")
    p_drama_search.add_argument("--genre", "-g", help="类型过滤（ROMANCE/ACTION/COMEDY/THRILLER/SCIFI 等）")
    p_drama_search.add_argument("--min-rating", "-r", type=float, default=0.0, help="最低评分（0-10）")
    p_drama_search.add_argument("--limit", "-n", type=int, default=50, help="数量限制（1-500）")
    p_drama_search.add_argument("--offset", type=int, default=0, help="偏移量")

    p_char_ranking = sub.add_parser("char-ranking", help="角色台词排行榜（v5.3.1 新增）")
    p_char_ranking.add_argument("--drama-id", help="限定短剧 ID（不指定则全局排行）")
    p_char_ranking.add_argument("--sort-by", "-s", default="lines",
                                choices=["lines", "classic", "scenes"], help="排序维度（lines=总台词数/classic=经典台词数/scenes=出场场次数）")
    p_char_ranking.add_argument("--limit", "-n", type=int, default=20, help="数量限制（1-100）")

    # ===== v5.3.2 新增 Agent 记忆命令 =====
    p_agent_diff = sub.add_parser("agent-diff", help="对比同一 Agent 在不同时间段的记忆差异（v5.3.2 新增）")
    p_agent_diff.add_argument("agent", help="Agent ID")
    p_agent_diff.add_argument("--days-a", "-a", type=int, default=7, help="时间段 A 回溯天数（较早）")
    p_agent_diff.add_argument("--days-b", "-b", type=int, default=1, help="时间段 B 回溯天数（较近）")

    p_agent_purge = sub.add_parser("agent-purge", help="清空指定 Agent 的全部记忆（v5.3.2 新增，高危）")
    p_agent_purge.add_argument("agent", help="目标 Agent ID")
    p_agent_purge.add_argument("--force", "-f", action="store_true", help="实际执行（不加为 dry-run 预览）")

    # ===== v5.3.2 新增 AI 短剧命令 =====
    p_drama_progress_upd = sub.add_parser("drama-progress-update", help="更新短剧观看进度（v5.3.2 新增）")
    p_drama_progress_upd.add_argument("drama", help="短剧 ID")
    p_drama_progress_upd.add_argument("episode", type=int, help="当前集数（≥1）")
    p_drama_progress_upd.add_argument("--status", "-s",
                                      choices=["WATCHING", "COMPLETED", "DROPPED", "PLANNING"],
                                      help="观看状态")
    p_drama_progress_upd.add_argument("--rating", "-r", type=float, help="用户评分（0-10）")

    p_drama_rec2 = sub.add_parser("drama-rec2", help="短剧智能推荐 v2（v5.3.2 新增）")
    p_drama_rec2.add_argument("--genre", "-g", help="类型过滤（ROMANCE/ACTION 等）")
    p_drama_rec2.add_argument("--min-rating", "-r", type=float, default=0.0, help="最低评分（0-10）")
    p_drama_rec2.add_argument("--mode", "-m", default="unwatched",
                              choices=["unwatched", "watching", "dropped", "all"],
                              help="推荐模式（默认 unwatched=优先未看）")
    p_drama_rec2.add_argument("--limit", "-n", type=int, default=20, help="数量限制（1-200）")

    # ===== v5.3.3 新增命令 =====

    p_agent_timeline = sub.add_parser("agent-timeline", help="Agent 记忆时间线分析（v5.3.3 新增）")
    p_agent_timeline.add_argument("agent_id", help="Agent ID")
    p_agent_timeline.add_argument("--days", "-d", type=int, default=30, help="回溯天数（1-365）")

    p_agent_heatmap = sub.add_parser("agent-heatmap", help="Agent 记忆热力图（v5.3.3 新增）")
    p_agent_heatmap.add_argument("agent_id", help="Agent ID")
    p_agent_heatmap.add_argument("--days", "-d", type=int, default=30, help="回溯天数（1-365）")

    p_drama_binge = sub.add_parser("drama-binge", help="追剧统计（v5.3.3 新增）")
    p_drama_binge.add_argument("--drama-id", help="指定短剧 ID（不指定则统计全部）")

    p_char_network = sub.add_parser("char-network", help="角色关系网络（v5.3.3 新增）")
    p_char_network.add_argument("drama_id", help="短剧 ID")

    # ===== v5.3.4 新增 Agent 记忆命令 =====
    p_agent_sentiment = sub.add_parser("agent-sentiment", help="Agent 记忆情感分析（v5.3.4 新增）")
    p_agent_sentiment.add_argument("agent", help="Agent ID")
    p_agent_sentiment.add_argument("--days", "-d", type=int, default=30, help="回溯天数（1-365）")

    p_memory_decay = sub.add_parser("memory-decay", help="记忆衰减评分（v5.3.4 新增）")
    p_memory_decay.add_argument("agent", help="Agent ID")
    p_memory_decay.add_argument("--days", "-d", type=int, default=30, help="回溯天数（1-365）")

    # ===== v5.3.4 新增 AI 短剧命令 =====
    p_drama_compare = sub.add_parser("drama-compare", help="短剧对比分析（v5.3.4 新增）")
    p_drama_compare.add_argument("dramas", nargs="+", help="短剧 ID 列表（2-5 部）")

    p_char_arc = sub.add_parser("char-arc", help="角色成长弧线分析（v5.3.4 新增）")
    p_char_arc.add_argument("drama_id", help="短剧 ID")
    p_char_arc.add_argument("character_id", help="角色 ID")

    # ===== v5.3.5 新增 Agent 记忆命令 =====
    p_mem_cluster = sub.add_parser("memory-cluster", help="记忆主题聚类（v5.3.5 新增）")
    p_mem_cluster.add_argument("agent", help="Agent ID")
    p_mem_cluster.add_argument("--days", "-d", type=int, default=30, help="回溯天数（1-365）")
    p_mem_cluster.add_argument("--max-clusters", "-k", type=int, default=10, help="最大聚类数（1-50）")

    p_agent_insight = sub.add_parser("agent-insight", help="Agent 行为洞察（v5.3.5 新增）", parents=[json_parser])
    p_agent_insight.add_argument("agent", help="Agent ID")
    p_agent_insight.add_argument("--days", "-d", type=int, default=30, help="回溯天数（1-365）")

    # ===== v5.3.5 新增 AI 短剧命令 =====
    p_drama_summary = sub.add_parser("drama-summary", help="短剧剧情摘要（v5.3.5 新增）")
    p_drama_summary.add_argument("drama_id", help="短剧 ID")
    p_drama_summary.add_argument("--max-length", "-l", type=int, default=500, help="摘要最大字符数（100-2000）")

    p_scene_tension = sub.add_parser("scene-tension", help="场景张力分析（v5.3.5 新增）")
    p_scene_tension.add_argument("drama_id", help="短剧 ID")
    p_scene_tension.add_argument("--top-k", "-k", type=int, default=10, help="返回 Top-K 高张力场景（1-50）")

    # ===== v5.3.6 新增 Agent 记忆命令 =====
    p_mem_link = sub.add_parser("memory-link", help="记忆关联推理（v5.3.6 新增）")
    p_mem_link.add_argument("agent", help="Agent ID")
    p_mem_link.add_argument("memory_id", help="目标记忆 ID")
    p_mem_link.add_argument("--top-k", "-k", type=int, default=10, help="返回 Top-K 关联记忆（1-50）")
    p_mem_link.add_argument("--days", "-d", type=int, default=90, help="回溯窗口天数（1-365）")

    p_mem_recall = sub.add_parser("memory-recall", help="智能记忆召回（v5.3.6 新增）", parents=[json_parser])
    p_mem_recall.add_argument("agent", help="Agent ID")
    p_mem_recall.add_argument("query", help="查询文本")
    p_mem_recall.add_argument("--top-k", "-k", type=int, default=10, help="返回 Top-K 召回记忆（1-50）")
    p_mem_recall.add_argument("--days", "-d", type=int, default=180, help="回溯窗口天数（1-365）")

    # ===== v5.3.6 新增 AI 短剧命令 =====
    p_drama_pacing = sub.add_parser("drama-pacing", help="剧集节奏分析（v5.3.6 新增）")
    p_drama_pacing.add_argument("drama_id", help="短剧 ID")
    p_drama_pacing.add_argument("--window", "-w", type=int, default=3, help="滑动窗口大小（场景数 1-10）")

    p_char_inter = sub.add_parser("char-interaction", help="角色互动分析（v5.3.6 新增）")
    p_char_inter.add_argument("drama_id", help="短剧 ID")
    p_char_inter.add_argument("--top-k", "-k", type=int, default=15, help="返回 Top-K 互动关系（1-50）")

    p_quality = sub.add_parser("quality", help="记忆质量评分（v5.2.2 新增）")
    p_quality.add_argument("memory_id", nargs="?", help="记忆 ID（不指定则批量评分）")
    p_quality.add_argument("--category", "-c", help="批量评分时按分类过滤")
    p_quality.add_argument("--limit", "-n", type=int, default=100, help="批量评分数量限制")

    p_similar = sub.add_parser("similar", help="相似度分析（v5.2.2 新增）")
    p_similar.add_argument("memory_id", help="目标记忆 ID")
    p_similar.add_argument("--limit", "-n", type=int, default=10, help="返回数量")
    p_similar.add_argument("--min-similarity", "-m", type=float, default=0.3, help="最低相似度阈值（0-1）")

    # ===== v5.2.4 新增命令 =====

    p_note_add = sub.add_parser("note-add", help="添加记忆笔记（v5.2.4 新增）")
    p_note_add.add_argument("memory_id", help="记忆 ID")
    p_note_add.add_argument("content", help="笔记内容")
    p_note_add.add_argument("--author", "-a", default="cli", help="作者")
    p_note_add.add_argument("--tags", "-t", nargs="+", help="笔记标签")

    p_note_list = sub.add_parser("note-list", help="列出记忆笔记（v5.2.4 新增）")
    p_note_list.add_argument("memory_id", help="记忆 ID")
    p_note_list.add_argument("--limit", "-n", type=int, default=50, help="数量限制")

    p_note_delete = sub.add_parser("note-delete", help="删除笔记（v5.2.4 新增）")
    p_note_delete.add_argument("note_id", help="笔记 ID")
    p_note_delete.add_argument("--force", action="store_true", help="确认删除")

    p_template_add = sub.add_parser("template-add", help="添加记忆模板（v5.2.4 新增）")
    p_template_add.add_argument("name", help="模板名称")
    p_template_add.add_argument("content", help="模板内容（支持 {变量} 占位符）")
    p_template_add.add_argument("--category", "-c", default="general", help="默认分类")
    p_template_add.add_argument("--tags", "-t", nargs="+", help="默认标签")
    p_template_add.add_argument("--importance", "-i", default="MEDIUM",
                                choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"], help="默认重要性")
    p_template_add.add_argument("--layer", "-l", default="short_term",
                                choices=["sensory", "short_term", "long_term", "permanent"], help="默认层级")
    p_template_add.add_argument("--description", "-d", default="", help="模板描述")

    p_template_list = sub.add_parser("template-list", help="列出记忆模板（v5.2.4 新增）")
    p_template_list.add_argument("--category", "-c", help="按分类筛选")
    p_template_list.add_argument("--limit", "-n", type=int, default=50, help="数量限制")

    p_template_use = sub.add_parser("template-use", help="使用模板创建记忆（v5.2.4 新增）")
    p_template_use.add_argument("template_id", help="模板 ID")
    p_template_use.add_argument("--var", nargs="+", help="变量替换（格式：key=value）")
    p_template_use.add_argument("--agent", default="cli", help="Agent ID")
    p_template_use.add_argument("--session", default="cli", help="会话 ID")

    p_template_delete = sub.add_parser("template-delete", help="删除模板（v5.2.4 新增）")
    p_template_delete.add_argument("template_id", help="模板 ID")
    p_template_delete.add_argument("--force", action="store_true", help="确认删除")

    p_batch_update = sub.add_parser("batch-update", help="批量更新记忆（v5.2.4 新增）")
    p_batch_update.add_argument("--ids", required=True, help="记忆 ID 列表，逗号分隔")
    p_batch_update.add_argument("--category", "-c", help="新分类")
    p_batch_update.add_argument("--tags", "-t", nargs="+", help="新标签")
    p_batch_update.add_argument("--importance", "-i",
                                choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"], help="新重要性")
    p_batch_update.add_argument("--layer", "-l",
                                choices=["sensory", "short_term", "long_term", "permanent"], help="新层级")
    p_batch_update.add_argument("--star", action="store_true", default=None, help="设为收藏")
    p_batch_update.add_argument("--unstar", action="store_true", default=None, help="取消收藏")
    p_batch_update.add_argument("--force", action="store_true", help="跳过确认")
    p_batch_update.add_argument("--agent", default="cli", help="Agent ID")
    p_batch_update.add_argument("--session", default="cli", help="会话 ID")

    p_schedule = sub.add_parser("schedule", help="复习计划管理（v5.2.4 新增）")
    p_schedule.add_argument("schedule_action", choices=["create", "list", "review", "stats"],
                            help="操作：create=创建计划, list=到期列表, review=完成复习, stats=统计")
    p_schedule.add_argument("--memory-id", "-m", help="记忆 ID（create 时必填）")
    p_schedule.add_argument("--schedule-id", "-s", help="复习计划 ID（review 时必填）")
    p_schedule.add_argument("--interval", type=float, default=1.0, help="复习间隔天数（create 时使用）")
    p_schedule.add_argument("--limit", "-n", type=int, default=20, help="数量限制")

    p_export = sub.add_parser("export", help="导出记忆")
    p_export.add_argument("--output", "-o", default="./data/export.json", help="输出文件")
    p_export.add_argument("--format", "-f", default="json", choices=["json", "csv"], help="导出格式")
    p_export.add_argument("--category", "-c", help="按分类筛选")
    p_export.add_argument("--layer", "-l", help="按层级筛选")
    p_export.add_argument("--include-private", action="store_true", help="包含私密记忆")

    p_import = sub.add_parser("import", help="导入记忆")
    p_import.add_argument("input", help="导入文件路径")
    p_import.add_argument("--target-layer", help="目标记忆层级")
    p_import.add_argument("--force", action="store_true", help="强制导入（覆盖重复）")

    p_compliance = sub.add_parser("compliance", help="合规报告")

    p_serve = sub.add_parser("serve", help="启动 Web UI 或 REST API")
    p_serve.add_argument("--port", type=int, default=8080, help="端口")
    p_serve.add_argument("--api", action="store_true",
                          help="启动 REST API 模式（v5.4.6 新增）")
    p_serve.add_argument("--host", default="127.0.0.1",
                          help="绑定地址（默认 127.0.0.1，0.0.0.0 允许外部访问）")
    # v5.6.1: TLS 参数
    p_serve.add_argument("--ssl-cert", default="",
                          help="TLS 证书文件路径（启用 HTTPS，API 模式）")
    p_serve.add_argument("--ssl-key", default="",
                          help="TLS 私钥文件路径（默认与证书同文件）")

    p_cleanup = sub.add_parser("cleanup", help="清理过期记忆（v5.1.3 新增）")
    p_cleanup.add_argument("--hours", type=int, default=24, help="超过 N 小时的记忆将被清理，默认 24")
    p_cleanup.add_argument("--layer", "-l", default="sensory",
                           choices=["sensory", "short_term", "long_term", "permanent"],
                           help="要清理的记忆层级，默认 sensory")

    # v5.4.6 新增：归档命令
    p_archive = sub.add_parser("archive", help="自动归档过期记忆（v5.4.6 新增）")
    p_archive.add_argument("--hours", type=int, default=24, help="超过 N 小时的记忆将被归档")
    p_archive.add_argument("--layer", "-l", default="sensory",
                           choices=["sensory", "short_term"],
                           help="要归档的记忆层级")

    p_archived_list = sub.add_parser("archived-list", help="列出归档记忆（v5.4.6 新增）")
    p_archived_list.add_argument("--layer", "-l", help="按层级过滤")
    p_archived_list.add_argument("--category", "-c", help="按分类过滤")
    p_archived_list.add_argument("--limit", "-n", type=int, default=20, help="返回数量")

    p_archived_restore = sub.add_parser("archived-restore", help="从归档恢复记忆（v5.4.6 新增）")
    p_archived_restore.add_argument("archive_id", help="归档记录 ID")

    p_archived_purge = sub.add_parser("archived-purge", help="永久删除过期归档记忆（v5.4.6 新增）")
    p_archived_purge.add_argument("--older-than-days", type=int, default=90, help="归档超过 N 天的永久删除")

    # v5.4.6 新增：Obsidian 导出
    p_export_obsidian = sub.add_parser("export-obsidian",
                                        help="导出为 Obsidian Vault 格式（v5.4.6 新增）")
    p_export_obsidian.add_argument("output_dir", help="输出目录（Obsidian vault 根目录）")
    p_export_obsidian.add_argument("--category", "-c", help="按分类筛选")
    p_export_obsidian.add_argument("--layer", "-l",
                                    choices=["sensory", "short_term", "long_term", "permanent"],
                                    help="按层级筛选")
    p_export_obsidian.add_argument("--starred", action="store_true", help="仅导出收藏记忆")

    p_batch_add = sub.add_parser("batch-add", help="从文件批量添加记忆（v5.1.3 新增）")
    p_batch_add.add_argument("input", help="输入 JSON 文件路径")

    p_import_url = sub.add_parser("import-url", help="从 URL 导入网页内容（v5.1.3 新增）")
    p_import_url.add_argument("url", help="要导入的网页 URL")
    p_import_url.add_argument("--category", "-c", default="web", help="分类")
    p_import_url.add_argument("--tags", "-t", nargs="+", help="标签")
    p_import_url.add_argument("--layer", "-l", default="short_term",
                              choices=["sensory", "short_term", "long_term", "permanent"],
                              help="记忆层级")

    # ===== 记忆关联命令（v5.2.5 新增）=====
    p_link = sub.add_parser("link", help="创建记忆关联（双向，v5.2.5 新增）")
    p_link.add_argument("source_id", help="源记忆 ID")
    p_link.add_argument("target_id", help="目标记忆 ID")
    p_link.add_argument("--type", "-t", default="related",
                        choices=["related", "depends_on", "extends", "contradicts"],
                        help="关联类型（related/depends_on/extends/contradicts，默认 related）")
    p_link.add_argument("--note", "-n", default="", help="关联备注（最多 500 字）")

    p_links = sub.add_parser("links", help="列出记忆的所有关联（v5.2.5 新增）")
    p_links.add_argument("memory_id", help="记忆 ID")

    p_unlink = sub.add_parser("unlink", help="删除记忆关联（v5.2.5 新增）")
    p_unlink.add_argument("link_id", help="关联 ID")

    # ===== 置顶命令（v5.2.5 新增）=====
    p_pin = sub.add_parser("pin", help="置顶记忆（v5.2.5 新增）")
    p_pin.add_argument("memory_id", help="记忆 ID")

    p_unpin = sub.add_parser("unpin", help="取消置顶（v5.2.5 新增）")
    p_unpin.add_argument("memory_id", help="记忆 ID")

    p_pinned = sub.add_parser("pinned", help="列出所有置顶记忆（v5.2.5 新增）")
    p_pinned.add_argument("--limit", "-l", type=int, default=50,
                          help="数量限制（默认 50）")

    # ===== 记忆版本历史命令（v5.2.7 新增）=====
    p_history = sub.add_parser("history", help="查看记忆的修改历史（v5.2.7 新增）")
    p_history.add_argument("memory_id", help="记忆 ID")
    p_history.add_argument("--limit", "-l", type=int, default=50, help="返回版本数量上限")

    p_rollback = sub.add_parser("rollback", help="回滚记忆到指定历史版本（v5.2.7 新增）")
    p_rollback.add_argument("version_id", help="目标版本 ID")

    # ===== v5.2.8 新增命令 =====
    p_export_csv = sub.add_parser("export-csv", help="导出记忆为 CSV（v5.2.8 新增）")
    p_export_csv.add_argument("--output", "-o", default="./data/memory_export.csv",
                              help="输出文件路径")
    p_export_csv.add_argument("--category", "-c", help="限定分类")
    p_export_csv.add_argument("--include-private", action="store_true",
                              help="包含 PRIVATE/STRICT 记忆（默认排除）")

    p_diff = sub.add_parser("diff", help="对比记忆版本差异（v5.2.8 新增）")
    p_diff.add_argument("memory_id", help="记忆 ID")
    p_diff.add_argument("--version-id", "-v", dest="version_id", default="",
                        help="历史版本 ID（默认：最新历史版本 vs 当前内容）")
    p_diff.add_argument("--against", default="",
                        help="第二个版本 ID（两个历史版本互相比较）")

    # ===== 多 Agent 记忆空间命令（v5.2.8 实验性 — v6.0.0 全量推送预览）=====
    p_space_create = sub.add_parser("space-create",
                                    help="创建多 Agent 记忆空间（实验性 v6.0.0 预览）")
    p_space_create.add_argument("name", help="空间名称（唯一）")
    p_space_create.add_argument("--desc", default="", help="空间描述")
    p_space_create.add_argument("--policy", default="shared",
                                choices=["shared", "broadcast"],
                                help="空间策略：shared=成员协作 / broadcast=仅 owner 可写")
    p_space_create.add_argument("--agent", default="cli", help="Owner Agent ID")

    p_space_list = sub.add_parser("space-list",
                                  help="列出记忆空间（实验性 v6.0.0 预览）")
    p_space_list.add_argument("--mine", action="store_true",
                              help="仅列出我（--agent）加入的空间")
    p_space_list.add_argument("--agent", default="cli", help="Agent ID")

    p_space_join = sub.add_parser("space-join",
                                  help="以 reader 身份加入记忆空间（实验性 v6.0.0 预览）")
    p_space_join.add_argument("space", help="空间 ID 或名称")
    p_space_join.add_argument("--agent", default="cli", help="Agent ID")

    p_space_add_member = sub.add_parser("space-add-member",
                                        help="添加空间成员（实验性 v6.0.0 预览，仅 owner）")
    p_space_add_member.add_argument("space", help="空间 ID 或名称")
    p_space_add_member.add_argument("member_agent", help="要添加的 Agent ID")
    p_space_add_member.add_argument("--role", default="reader",
                                    choices=["editor", "reader"],
                                    help="成员角色（owner 角色暂不支持转移）")
    p_space_add_member.add_argument("--agent", default="cli", help="操作者 Agent ID（须为 owner）")

    p_space_share = sub.add_parser("space-share",
                                   help="共享记忆到空间（实验性 v6.0.0 预览）")
    p_space_share.add_argument("space", help="空间 ID 或名称")
    p_space_share.add_argument("memory_id", help="要共享的记忆 ID")
    p_space_share.add_argument("--agent", default="cli", help="操作者 Agent ID")

    p_space_memories = sub.add_parser("space-memories",
                                      help="列出空间中的共享记忆（实验性 v6.0.0 预览）")
    p_space_memories.add_argument("space", help="空间 ID 或名称")
    p_space_memories.add_argument("--agent", default="cli", help="Agent ID")
    p_space_memories.add_argument("--limit", "-n", type=int, default=50, help="数量限制")

    p_space_stats = sub.add_parser("space-stats",
                                   help="记忆空间统计（实验性 v6.0.0 预览）")
    p_space_stats.add_argument("space", nargs="?", default="",
                               help="空间 ID 或名称（省略则全局统计）")
    p_space_stats.add_argument("--agent", default="cli", help="Agent ID")

    # ===== v5.3.7 新增 Agent 记忆命令 =====
    p_mem_importance = sub.add_parser("memory-importance", help="记忆重要度分析（v5.3.7 新增）")
    p_mem_importance.add_argument("agent", help="Agent ID")
    p_mem_importance.add_argument("--days", "-d", type=int, default=30, help="回溯窗口天数（1-365）")

    p_mem_context = sub.add_parser("memory-context", help="上下文记忆注入（v5.3.7 新增）", parents=[json_parser])
    p_mem_context.add_argument("agent", help="Agent ID")
    p_mem_context.add_argument("query", help="查询文本")
    p_mem_context.add_argument("--max-tokens", "-t", type=int, default=4000, help="token 预算上限（500-32000）")

    p_agent_emotion = sub.add_parser("agent-emotion", help="Agent 情感追踪（v5.3.7 新增）")
    p_agent_emotion.add_argument("agent", help="Agent ID")
    p_agent_emotion.add_argument("--days", "-d", type=int, default=30, help="回溯天数（1-365）")

    # ===== v5.3.7 新增 AI 短剧命令 =====
    p_genre_trend = sub.add_parser("drama-genre-trend", help="短剧类型趋势分析（v5.3.7 新增）")
    p_genre_trend.add_argument("--days", "-d", type=int, default=90, help="回溯窗口天数（1-365）")

    p_binge_score = sub.add_parser("drama-binge-score", help="追剧粘性评分（v5.3.7 新增）")
    p_binge_score.add_argument("drama_id", help="短剧 ID")

    p_char_rel = sub.add_parser("char-relationship", help="角色关系深度分析（v5.3.7 新增）")
    p_char_rel.add_argument("drama_id", help="短剧 ID")
    p_char_rel.add_argument("char1", help="角色 1 ID")
    p_char_rel.add_argument("char2", help="角色 2 ID")

    # ===== v5.4.1 新增 Agent 记忆命令 =====
    p_mem_reflection = sub.add_parser("memory-reflection", help="记忆反思（v5.4.1 新增）", parents=[json_parser])
    p_mem_reflection.add_argument("agent", help="Agent ID")
    p_mem_reflection.add_argument("--days", "-d", type=int, default=30, help="回溯窗口天数（1-365）")

    p_mem_lineage = sub.add_parser("memory-lineage", help="记忆血缘溯源（v5.4.1 新增）", parents=[json_parser])
    p_mem_lineage.add_argument("memory_id", help="记忆 ID")

    p_mem_reinforce = sub.add_parser("memory-reinforce", help="记忆强化候选（v5.4.1 新增）", parents=[json_parser])
    p_mem_reinforce.add_argument("agent", help="Agent ID")
    p_mem_reinforce.add_argument("--days", "-d", type=int, default=90, help="回溯窗口天数（1-365）")
    p_mem_reinforce.add_argument("--limit", "-l", type=int, default=10, help="返回候选上限（1-50）")

    # ===== v5.5.8 新增 memory-diff 命令 =====
    p_mem_diff = sub.add_parser("memory-diff", help="对比两个记忆版本的差异（v5.5.8 新增）", parents=[json_parser])
    p_mem_diff.add_argument("version_a", help="版本 A 的 ID")
    p_mem_diff.add_argument("version_b", help="版本 B 的 ID")

    # ===== v5.4.1 新增 AI 短剧命令 =====
    p_plot_thread = sub.add_parser("drama-plot-thread", help="剧情伏笔线索追踪（v5.4.1 新增）")
    p_plot_thread.add_argument("drama_id", help="短剧 ID")

    p_episode_curve = sub.add_parser("drama-episode-curve", help="分集张力曲线（v5.4.1 新增）")
    p_episode_curve.add_argument("drama_id", help="短剧 ID")

    p_screen_time = sub.add_parser("drama-screen-time", help="角色戏份平衡分析（v5.4.1 新增）")
    p_screen_time.add_argument("drama_id", help="短剧 ID")

    # ===== v5.4.2 新增联邦 ACL 命令 =====
    p_acl_add = sub.add_parser("fed-acl-add", help="联邦 ACL：添加规则（v5.4.2 新增）")
    p_acl_add.add_argument("--principal", "-p", required=True, help="主体（peer ID 或 * 表示任意节点）")
    p_acl_add.add_argument("--resource", "-r", required=True,
                           help="资源表达式：all / memory:<id> / category:<名> / tag:<名>")
    p_acl_add.add_argument("--operations", "-o", default="read",
                           help="操作：read/write/reshare/*（逗号分隔，默认 read）")
    p_acl_add.add_argument("--effect", "-e", default="allow", choices=["allow", "deny"],
                           help="效果（默认 allow）")
    p_acl_add.add_argument("--priority", type=int, default=100, help="优先级（越大越先评估，默认 100）")
    p_acl_add.add_argument("--trust-min", type=float, default=0.0,
                           help="peer 信任阈值（0-1，默认 0 不校验）")
    p_acl_add.add_argument("--expires-hours", type=float, default=None, help="规则有效时长（小时）")
    p_acl_add.add_argument("--note", "-n", default="", help="备注")

    p_acl_remove = sub.add_parser("fed-acl-remove", help="联邦 ACL：删除规则（v5.4.2 新增）")
    p_acl_remove.add_argument("rule_id", help="规则 ID")

    p_acl_list = sub.add_parser("fed-acl-list", help="联邦 ACL：规则列表（v5.4.2 新增）")
    p_acl_list.add_argument("--principal", "-p", default=None, help="按主体过滤（含通配规则）")
    p_acl_list.add_argument("--effect", "-e", default=None, choices=["allow", "deny"], help="按效果过滤")
    p_acl_list.add_argument("--limit", "-l", type=int, default=200, help="返回上限（默认 200）")

    p_acl_check = sub.add_parser("fed-acl-check", help="联邦 ACL：访问评估（v5.4.2 新增）")
    p_acl_check.add_argument("peer", help="Peer ID")
    p_acl_check.add_argument("memory_id", help="记忆 ID")
    p_acl_check.add_argument("--operation", "-o", default="read",
                             choices=["read", "write", "reshare"], help="操作（默认 read）")
    p_acl_check.add_argument("--trust", type=float, default=None, help="peer 信任度（0-1）")
    p_acl_check.add_argument("--category", default=None, help="记忆分类（参与 category 规则匹配）")
    p_acl_check.add_argument("--tags", nargs="+", default=None, help="记忆标签（参与 tag 规则匹配）")

    p_acl_stats = sub.add_parser("fed-acl-stats", help="联邦 ACL：统计（v5.4.2 新增）")

    # ===== v5.4.2 新增共享冲突命令 =====
    p_cfl_list = sub.add_parser("share-conflicts", help="共享冲突：列表（v5.4.2 新增）")
    p_cfl_list.add_argument("--status", "-s", default=None,
                            choices=["open", "resolved", "dismissed"], help="按状态过滤")
    p_cfl_list.add_argument("--limit", "-l", type=int, default=50, help="返回上限（默认 50）")

    p_cfl_resolve = sub.add_parser("share-conflict-resolve", help="共享冲突：解决（v5.4.2 新增）")
    p_cfl_resolve.add_argument("conflict_id", help="冲突 ID")
    p_cfl_resolve.add_argument("--strategy", "-s", required=True,
                               choices=["lww", "keep_both"], help="解决策略")
    p_cfl_resolve.add_argument("--actor", "-a", default="", help="操作者")

    p_cfl_dismiss = sub.add_parser("share-conflict-dismiss", help="共享冲突：关闭（v5.4.2 新增）")
    p_cfl_dismiss.add_argument("conflict_id", help="冲突 ID")
    p_cfl_dismiss.add_argument("--actor", "-a", default="", help="操作者")

    p_cfl_stats = sub.add_parser("share-conflict-stats", help="共享冲突：统计（v5.4.2 新增）")

    # ===== v5.4.3 新增 Agent 记忆命令 =====
    p_influence_map = sub.add_parser("agent-influence", help="Agent 记忆影响力图谱（v5.4.3 新增）")
    p_influence_map.add_argument("agent", help="Agent ID")
    p_influence_map.add_argument("--days", "-d", type=int, default=30, help="回溯天数（1-365）")

    p_memory_overlap = sub.add_parser("memory-overlap", help="记忆重叠分析（v5.4.3 新增）")
    p_memory_overlap.add_argument("agent_a", help="Agent A ID")
    p_memory_overlap.add_argument("agent_b", help="Agent B ID")
    p_memory_overlap.add_argument("--days", "-d", type=int, default=30, help="回溯天数（1-365）")

    p_conflict_graph = sub.add_parser("conflict-graph", help="记忆冲突检测图（v5.4.3 新增）")
    p_conflict_graph.add_argument("agent", help="Agent ID")
    p_conflict_graph.add_argument("--days", "-d", type=int, default=30, help="回溯天数（1-365）")

    # ===== v5.4.3 新增 AI 短剧命令 =====
    p_quote_map = sub.add_parser("drama-quote-map", help="经典台词地图（v5.4.3 新增）")
    p_quote_map.add_argument("drama_id", help="短剧 ID")

    p_char_growth = sub.add_parser("char-growth", help="角色成长深度分析（v5.4.3 新增）")
    p_char_growth.add_argument("drama_id", help="短剧 ID")
    p_char_growth.add_argument("character_id", help="角色 ID")

    p_scene_rhythm = sub.add_parser("scene-rhythm", help="场景节奏分析（v5.4.3 新增）")
    p_scene_rhythm.add_argument("drama_id", help="短剧 ID")

    # ===== v5.3.9 新增五大能力 CLI =====
    p_intent_router = sub.add_parser("intent-router", help="意图分类路由（v5.3.9 新增）", parents=[json_parser])
    p_intent_router.add_argument("text", help="要分类的文本（用引号包裹）")
    p_intent_router.add_argument("--force", help="强制指定意图 ID 用于 debug")

    p_conflict_scan = sub.add_parser("conflict-scan", help="矛盾扫描 + 自动衰减（v5.3.9 新增）", parents=[json_parser])
    p_conflict_scan.add_argument("--category", help="只扫描指定类别")
    p_conflict_scan.add_argument("--limit", type=int, default=500, help="扫描最多多少条记忆")
    p_conflict_scan.add_argument("--apply-decay", action="store_true",
                                 help="应用衰减动作（降低重要性 + 打 conflict 标签）")

    p_skill_extract = sub.add_parser("skill-extract", help="从记忆抽取可复用技能模板（v5.3.9 新增）", parents=[json_parser])
    p_skill_extract.add_argument("--category", help="只抽取指定类别")
    p_skill_extract.add_argument("--limit", type=int, default=2000, help="处理最多多少条记忆")
    p_skill_extract.add_argument("--min-cluster", type=int, default=2, help="最小聚类规模（2+）")

    p_rerank_search = sub.add_parser("rerank-search", help="混合检索（查询扩展 + Cross-Encoder 重排）（v5.3.9 新增）", parents=[json_parser])
    p_rerank_search.add_argument("query", help="查询文本")
    p_rerank_search.add_argument("--top", type=int, default=10, help="返回 Top N 条（默认 10）")
    p_rerank_search.add_argument("--no-expand", action="store_true", help="关闭查询扩展")
    p_rerank_search.add_argument("--no-rerank", action="store_true", help="关闭 Cross-Encoder 重排")

    p_session_focus = sub.add_parser("session-focus", help="会话焦点聚类 + 漂移检测（v5.3.9 新增）", parents=[json_parser])
    p_session_focus.add_argument("--messages", "-m", action="append", required=True,
                                  help='消息条目 "role:内容"，可多次传入')
    p_session_focus.add_argument("--window", type=int, default=40, help="滑动窗口大小（默认 40）")
    p_session_focus.add_argument("--augment", help="若指定，输出针对该 query 的增强查询")

    # ===== v5.4.5 新增向量检索命令 =====
    p_rebuild_emb = sub.add_parser("rebuild-embeddings",
                                   help="重建/增量构建嵌入向量（v5.4.5 新增，v5.4.6 增量模式）")
    p_rebuild_emb.add_argument("--batch-size", "-b", type=int, default=100,
                               help="批量编码大小（默认 100）")
    p_rebuild_emb.add_argument("--full", action="store_true",
                               help="全量重建（默认仅处理缺失项）")

    p_emb_status = sub.add_parser("embedding-status",
                                  help="查看嵌入向量状态（v5.4.5 新增）")

    args = parser.parse_args(argv)

    global _json_mode
    _json_mode = getattr(args, 'json_output', False)

    # v5.4.6 Shell 自动补全安装
    if getattr(args, 'install_completion', None):
        _install_shell_completion(args.install_completion)
        return

    # 无子命令时打印帮助（v5.4.6 修复：required=False 后需手动处理）
    if not getattr(args, "command", None):
        parser.print_help()
        return

    # v5.2.8 修复：统一展开逗号分隔的标签（覆盖全部 nargs="+" 的 --tags 命令）
    if isinstance(getattr(args, "tags", None), list):
        args.tags = _split_tags(args.tags)

    _main_dispatch(args, parser)


def cmd_memory_importance(args):
    """记忆重要度分析（v5.3.7 新增）"""
    cm = _get_memory(args)
    print(c("\n📊 记忆重要度分析（v5.3.7）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  回溯天数:    {args.days}")

    try:
        result = cm.memory_importance(args.agent, args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    total = result["total_memories"]
    if total == 0:
        print(c(f"\n⚠️  该 Agent 在 {args.days} 天内无记忆", "yellow"))
        cm.close()
        return 0

    print(f"  总记忆数:    {total}")

    # 重要度分布
    dist = result.get("importance_distribution", {})
    print(c("\n📈 重要度分布", "cyan"))
    imp_colors = {"LOW": "yellow", "MEDIUM": "cyan", "HIGH": "green", "CRITICAL": "red"}
    for imp in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        cnt = dist.get(imp, 0)
        pct = cnt / total * 100 if total > 0 else 0
        bar = "█" * int(pct / 5)
        print(f"  {imp:<10} {cnt:>5}  ({pct:>5.1f}%)  {c(bar, imp_colors.get(imp, 'cyan'))}")

    # 漂移分析
    drift = result.get("drift_analysis", {})
    if drift:
        print(c("\n📉 重要度漂移分析", "cyan"))
        for imp in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            d = drift.get(imp, {})
            dir_label = {"increasing": "↑ 上升", "decreasing": "↓ 下降", "stable": "→ 稳定"}
            print(f"  {imp:<10} {dir_label.get(d.get('direction', 'stable'), '?'):<10} "
                  f"前半段 {d.get('first_half_ratio', 0):.1%} → 后半段 {d.get('second_half_ratio', 0):.1%}")

    # 低估记忆
    underrated = result.get("underrated", [])
    if underrated:
        print(c(f"\n⬇️ 被低估的记忆（高访问低重要度）共 {len(underrated)} 条", "yellow"))
        for u in underrated[:5]:
            print(f"  [{u['importance']}] 访问 {u['access_count']}x → 建议 {u['suggested_importance']}  "
                  f"{(u.get('content_preview') or '')[:40]}")

    # 高估记忆
    overrated = result.get("overrated", [])
    if overrated:
        print(c(f"\n⬆️ 被高估的记忆（高重要度低访问）共 {len(overrated)} 条", "red"))
        for o in overrated[:5]:
            print(f"  [{o['importance']}] 访问 {o['access_count']}x → 建议 {o['suggested_importance']}  "
                  f"{(o.get('content_preview') or '')[:40]}")

    # 建议
    suggestions = result.get("re-evaluation_suggestions", [])
    if suggestions:
        print(c("\n💡 重评估建议", "cyan"))
        for s in suggestions:
            print(f"  • {s}")

    cm.close()
    return 0


def cmd_memory_context(args):
    """上下文记忆注入（v5.3.7 新增）"""
    cm = _get_memory(args)
    try:
        result = cm.memory_context(args.agent, args.query, args.max_tokens)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    if getattr(args, 'json_output', False):
        _json_out(result)
        cm.close()
        return 0

    print(c("\n📥 上下文记忆注入（v5.3.7）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  查询:        {args.query}")
    print(f"  Token 上限:  {args.max_tokens}")
    print(f"  包含记忆:    {result['included_count']}")
    print(f"  排除记忆:    {result['excluded_count']}")
    print(f"  Token 估计:  {result['token_estimate']}")
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  查询:        {args.query}")
    print(f"  Token 上限:  {args.max_tokens}")

    

    context = result.get("context", "")
    if not context:
        print(c("\n⚠️  无匹配记忆可注入", "yellow"))
        cm.close()
        return 0

    print(c(f"\n{'─' * 60}", "cyan"))
    print(context)
    print(c(f"{'─' * 60}", "cyan"))

    cm.close()
    return 0


def cmd_agent_emotion(args):
    """Agent 情感追踪（v5.3.7 新增）"""
    cm = _get_memory(args)
    print(c("\n😊 Agent 情感追踪（v5.3.7）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  回溯天数:    {args.days}")

    try:
        result = cm.agent_emotion(args.agent, args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    total = result["total_memories"]
    if total == 0:
        print(c(f"\n⚠️  该 Agent 在 {args.days} 天内无记忆", "yellow"))
        cm.close()
        return 0

    print(f"  总记忆数:    {total}")

    # 情感分布
    dist = result.get("emotion_distribution", {})
    pct = result.get("emotion_percentages", {})
    print(c("\n📊 情感分布", "cyan"))
    emo_label = {"joy": "喜悦", "frustration": "挫败", "calm": "平静"}
    emo_color = {"joy": "green", "frustration": "red", "calm": "cyan"}
    for e in ("joy", "frustration", "calm"):
        cnt = dist.get(e, 0)
        p = pct.get(e, 0)
        bar = "█" * int(p * 20)
        print(f"  {emo_label.get(e, e):<10} {cnt:>5}  ({p:>5.1%})  {c(bar, emo_color.get(e, 'cyan'))}")

    # 主导情感
    dominant = result.get("dominant_emotion", "no_data")
    print(c(f"\n🎯 主导情感: {emo_label.get(dominant, dominant)}", "bold"))

    # 波动性
    vol = result.get("volatility_score", 0)
    vol_color = "green" if vol < 30 else ("yellow" if vol < 60 else "red")
    print(f"  情感波动性: {c(f'{vol:.1f} / 100', vol_color)}")

    # 时间线
    timeline = result.get("timeline", [])
    if timeline and len(timeline) <= 60:
        print(c("\n📉 情感时间线", "cyan"))
        for t in timeline:
            dom = t["dominant"]
            bar = "█" * (t.get("total", 1))
            print(f"  {t['date']}  {emo_label.get(dom, dom):<8} {c(bar, emo_color.get(dom, 'cyan'))}")

    # 转换
    transitions = result.get("transitions", [])
    if transitions:
        print(c(f"\n🔄 情感转换（共 {result.get('transition_count', 0)} 次）", "cyan"))
        for tr in transitions[:10]:
            print(f"  {tr}")
        if len(transitions) > 10:
            print(f"  ... 还有 {len(transitions) - 10} 次转换")

    cm.close()
    return 0


def cmd_drama_genre_trend(args):
    """短剧类型趋势分析（v5.3.7 新增）"""
    cm = _get_memory(args)
    print(c("\n🎭 短剧类型趋势分析（v5.3.7）", "bold"))
    print("=" * 60)
    print(f"  回溯天数:    {args.days}")

    try:
        result = cm.drama_genre_trend(args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    total = result["total_dramas"]
    if total == 0:
        print(c(f"\n⚠️  {args.days} 天内无短剧数据", "yellow"))
        cm.close()
        return 0

    print(f"  短剧总数:    {total}")

    # 类型分布
    dist = result.get("genre_distribution", {})
    trends = result.get("trends", {})
    print(c("\n📊 类型分布与趋势", "cyan"))
    print(f"{'类型':<16}{'数量':>6}{'占比':>8}{'趋势':>10}{'平均评分':>10}")
    print("-" * 60)
    for genre, cnt in sorted(dist.items(), key=lambda x: -x[1]):
        trend = trends.get(genre, {})
        share = trend.get("share", 0)
        direction = trend.get("trend", "stable")
        avg_r = trend.get("avg_rating", 0)
        dir_label = {"rising": "↑ 上升", "declining": "↓ 下降", "stable": "→ 稳定"}
        print(f"{genre:<16}{cnt:>6}{share:>7.1%}{dir_label.get(direction, '?'):>10}{avg_r:>10.1f}")

    # 热门类型
    top_genre = result.get("top_genre")
    if top_genre:
        print(c(f"\n🏆 热门类型: {top_genre}（{result.get('top_genre_count', 0)} 部）", "green"))

    cm.close()
    return 0


def cmd_drama_binge_score(args):
    """追剧粘性评分（v5.3.7 新增）"""
    cm = _get_memory(args)
    print(c("\n🔥 追剧粘性评分（v5.3.7）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")

    try:
        result = cm.drama_binge_score(args.drama_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"  剧名:       {result['title']}")

    score = result["binge_score"]
    rating = result["rating"]
    s_color = "red" if score >= 80 else ("green" if score >= 60 else ("yellow" if score >= 40 else "red"))
    print(f"  追剧粘性:   {c(f'{score:.1f} / 100', s_color)}")
    r_label = {"extreme": "极高", "high": "高", "medium": "中", "low": "低"}
    print(f"  评级:       {c(r_label.get(rating, rating), s_color)}")
    print(f"  推荐:       {result['recommendation']}")

    # 因子分解
    factors = result.get("factors", {})
    if factors:
        print(c("\n📊 因子分解", "cyan"))
        print(f"{'因子':<20}{'得分':>8}{'权重':>8}{'贡献':>8}")
        print("-" * 50)
        for fname, fdata in factors.items():
            label_map = {
                "pacing_health": "节奏健康度",
                "tension_avg": "平均张力",
                "interaction_density": "互动密度",
                "classic_ratio": "经典台词比",
                "completion_rate": "完成率",
            }
            print(f"{label_map.get(fname, fname):<20}{fdata['score']:>8.1f}{fdata['weight']:>8.0%}{fdata['contribution']:>8.1f}")

    print(c(f"\n总计: 场景 {result.get('total_scenes', 0)} | 角色 {result.get('total_characters', 0)} | "
          f"台词 {result.get('total_lines', 0)} | 经典 {result.get('classic_lines', 0)}", "cyan"))

    cm.close()
    return 0


def cmd_char_relationship(args):
    """角色关系深度分析（v5.3.7 新增）"""
    cm = _get_memory(args)
    print(c("\n👥 角色关系深度分析（v5.3.7）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")
    print(f"  角色 1:     {args.char1}")
    print(f"  角色 2:     {args.char2}")

    try:
        result = cm.char_relationship(args.drama_id, args.char1, args.char2)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"  剧名:       {result['title']}")
    print(f"  角色名称:   {result['name1']} ↔ {result['name2']}")

    rel_type = result.get("relationship_type", "stranger")
    rel_label = {
        "ally": "盟友", "rival": "对手", "romance": "恋情",
        "family": "家人", "mentor": "导师", "stranger": "陌生人",
    }
    print(c(f"\n🤝 关系类型: {rel_label.get(rel_type, rel_type)}", "bold"))
    print(f"  互动场景数: {result.get('interaction_count', 0)}")
    print(f"  台词交替数: {result.get('total_alternations', 0)}")
    print(f"  冲突水平:   {result.get('conflict_level', 'none')}")
    print(f"  关系强度:   {result.get('relationship_strength', 0):.2f}")
    print(f"  情感弧线:   {result.get('emotion_arc', 'stable')}")

    # 关键场景
    key_scenes = result.get("key_scenes", [])
    if key_scenes:
        print(c("\n🎬 关键场景（高互动/高冲突）", "cyan"))
        print(f"{'集':>4}{'场景':>6}{'交替':>6}{'冲突':>6}{'情感':>8}")
        print("-" * 40)
        for ks in key_scenes[:10]:
            print(f"{ks['episode']:>4}{ks['scene_number']:>6}{ks['alternations']:>6}"
                  f"{ks['conflict_hits']:>6}{ks['emotion']:>8}")
        if len(key_scenes) > 10:
            print(f"  ... 还有 {len(key_scenes) - 10} 个关键场景")

    # 情感发展
    progression = result.get("emotion_progression", [])
    if progression and len(progression) <= 30:
        print(c("\n📈 情感发展", "cyan"))
        for ep in progression:
            bar = "█" * ep.get("alternations", 1)
            print(f"  EP{ep['episode']} S{ep['scene']}  {ep['emotion']:<10} {bar}")

    cm.close()
    return 0


# ===== v5.4.1 新增能力 CLI 命令实现 =====

def cmd_memory_reflection(args):
    """记忆反思（v5.4.1 新增）"""
    cm = _get_memory(args)
    try:
        result = cm.memory_reflection(args.agent, args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    # v5.5.6 fix: 新增 --json 输出分支
    if getattr(args, 'json_output', False):
        _json_out(result)
        cm.close()
        return 0

    print(c("\n🪞 记忆反思（v5.4.1）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  回溯天数:    {args.days}")

    

    if result["total_memories"] == 0:
        print(c("\n窗口内暂无记忆可供反思。", "yellow"))
        cm.close()
        return 0

    print(f"  记忆总数:    {result['total_memories']}")
    tone = result.get("emotional_tone", {})
    print(f"  情感基调:    {tone.get('dominant', 'no_data')}")
    print(f"\n  反思摘要:\n    {result.get('reflection_summary', '')}")

    top_cats = result.get("top_categories", [])
    if top_cats:
        print(c("\n📂 Top 分类", "cyan"))
        for t in top_cats:
            print(f"  {t['category']:<16} {t['count']:>4} 条 ({t['share']*100:.1f}%)")

    themes = result.get("recurring_themes", [])
    if themes:
        print(c("\n🔁 反复出现的主题: ", "cyan") + "、".join(themes))

    lessons = result.get("key_lessons", [])
    if lessons:
        print(c(f"\n💡 关键经验 Top {len(lessons)}", "cyan"))
        for l in lessons:
            print(f"  [{l['importance']}] {l['content_preview']}")

    for s in result.get("suggestions", []):
        print(c(f"  ✦ {s}", "yellow"))

    cm.close()
    return 0


def cmd_memory_lineage(args):
    """记忆血缘溯源（v5.4.1 新增）"""
    cm = _get_memory(args)
    try:
        result = cm.memory_lineage(args.memory_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    # v5.5.6 fix: 新增 --json 输出分支
    if getattr(args, 'json_output', False):
        _json_out(result)
        cm.close()
        return 0

    print(c("\n🧬 记忆血缘溯源（v5.4.1）", "bold"))
    print("=" * 60)
    print(f"  记忆 ID:    {args.memory_id}")

    

    basic = result.get("basic", {})
    stats = result.get("stats", {})
    print(f"  内容预览:   {basic.get('content_preview', '')}")
    print(f"  分类:       {basic.get('category')}  |  重要度: {basic.get('importance')}")
    print(f"  层级:       {basic.get('layer')}  |  来源 Agent: {basic.get('source_agent') or '-'}")
    print(f"  存在天数:   {stats.get('age_days')} 天")
    print(f"  版本数:     {stats.get('version_count')}  |  出链: {stats.get('link_count_out')}  |  入链: {stats.get('link_count_in')}")
    print(f"  审计事件:   {stats.get('audit_event_count')} 条")

    timeline = result.get("lifecycle_timeline", [])
    if timeline:
        print(c("\n🕓 生命周期时间线", "cyan"))
        for ev in timeline[:20]:
            ts = ev.get("timestamp")
            ts_str = format_time(ts) if isinstance(ts, (int, float)) else str(ts)
            print(f"  {ts_str}  {ev.get('description', ev.get('event'))}")

    versions = result.get("versions", [])
    if versions:
        print(c(f"\n📜 版本历史（{len(versions)}）", "cyan"))
        for v in versions[:10]:
            print(f"  v{v['version_number']}  {v['content_preview']}")

    cm.close()
    return 0


def cmd_memory_diff(args):
    """对比两个记忆版本的差异（v5.5.8 新增）"""
    cm = _get_memory(args)
    try:
        result = cm.memory_diff(args.version_a, args.version_b)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    if getattr(args, 'json_output', False):
        _json_out(result)
        cm.close()
        return 0

    print(c("\n🔄 记忆版本差异对比（v5.5.8）", "bold"))
    print("=" * 60)
    ver_a = result["version_a"]
    ver_b = result["version_b"]
    print(f"  版本 A:  v{ver_a['version_number']}  (ID: {ver_a['version_id']})")
    print(f"  版本 B:  v{ver_b['version_number']}  (ID: {ver_b['version_id']})")

    changed = result.get("changed_fields", [])
    if not changed:
        print(c("\n  ✅ 两个版本完全相同，无差异。", "green"))
        cm.close()
        return 0

    print(c(f"\n  变更字段: {', '.join(changed)}", "yellow"))

    changes = result.get("changes", {})
    if changes.get("content", {}).get("changed"):
        diff = changes["content"].get("diff", "")
        print(c("\n  📝 内容差异:", "cyan"))
        for line in diff.splitlines():
            if line.startswith("+"):
                print(c(f"  {line}", "green"))
            elif line.startswith("-"):
                print(c(f"  {line}", "red"))
            elif line.startswith("@@"):
                print(c(f"  {line}", "purple"))
            else:
                print(f"  {line}")

    if changes.get("category", {}).get("changed"):
        cat = changes["category"]
        print(f"\n  📂 分类变更: {cat['from']} → {cat['to']}")

    if changes.get("tags", {}).get("changed"):
        tags = changes["tags"]
        if tags.get("added"):
            print(c(f"  🏷️  新增标签: {', '.join(tags['added'])}", "green"))
        if tags.get("removed"):
            print(c(f"  🏷️  移除标签: {', '.join(tags['removed'])}", "red"))

    if changes.get("importance", {}).get("changed"):
        imp = changes["importance"]
        print(f"  ⚡ 重要度变更: {imp['from']} → {imp['to']}")

    cm.close()
    return 0


def cmd_memory_reinforce(args):
    """记忆强化候选（v5.4.1 新增）"""
    cm = _get_memory(args)
    try:
        result = cm.memory_reinforce(args.agent, args.days, args.limit)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    # v5.5.6 fix: 新增 --json 输出分支
    if getattr(args, 'json_output', False):
        _json_out(result)
        cm.close()
        return 0

    print(c("\n💪 记忆强化候选（v5.4.1）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  回溯天数:    {args.days}  |  候选上限: {args.limit}")

    

    if result["total_scanned"] == 0:
        print(c("\n窗口内暂无记忆。", "yellow"))
        cm.close()
        return 0

    print(f"\n  {result.get('summary', '')}")

    candidates = result.get("candidates", [])
    if candidates:
        print(c(f"\n🎯 强化候选 Top {len(candidates)}", "cyan"))
        print(f"{'分数':>6}{'重要度':>10}{'闲置天':>8}  动作")
        print("-" * 60)
        for cd in candidates:
            print(f"{cd['reinforce_score']:>6}{cd['importance']:>10}"
                  f"{cd['days_idle']:>8}  {cd['recommended_action']}")
            print(f"      {cd['content_preview']}")
            if cd.get("reasons"):
                print(c(f"      ↳ {'; '.join(cd['reasons'])}", "cyan"))

    cm.close()
    return 0


def cmd_drama_plot_thread(args):
    """剧情伏笔线索追踪（v5.4.1 新增）"""
    cm = _get_memory(args)
    print(c("\n🧵 剧情伏笔线索追踪（v5.4.1）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")

    try:
        result = cm.drama_plot_thread(args.drama_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"  剧名:       {result['title']}")
    print(f"  场景总数:   {result['total_scenes']}")
    print(f"  线索总数:   {len(result.get('threads', []))}")
    print(f"  已回收:     {result.get('resolved_count', 0)}  |  未回收: {result.get('open_count', 0)}")
    print(f"  回收率:     {result.get('resolution_rate', 0)}%")

    threads = result.get("threads", [])
    if threads:
        print(c("\n🔗 线索明细", "cyan"))
        for t in threads[:20]:
            status = "✅已回收" if t["status"] == "resolved" else "⏳未回收"
            print(f"  {t['thread_id']:<4} EP{t['setup_episode']}  {status}  {t['name']}")
            if t["status"] == "resolved" and t.get("payoff_episode") is not None:
                print(c(f"        ↳ 回收于 EP{t['payoff_episode']}", "cyan"))

    for s in result.get("suggestions", []):
        print(c(f"  ✦ {s}", "yellow"))

    cm.close()
    return 0


def cmd_drama_episode_curve(args):
    """分集张力曲线（v5.4.1 新增）"""
    cm = _get_memory(args)
    print(c("\n📈 分集张力曲线（v5.4.1）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")

    try:
        result = cm.drama_episode_curve(args.drama_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"  剧名:       {result['title']}")
    print(f"  总集数:     {result.get('total_episodes', 0)}")
    print(f"  高潮集:     EP{result.get('climax_episode')} (张力 {result.get('climax_tension', 0)})")
    print(f"  平均张力:   {result.get('avg_tension', 0)}  |  波动率: {result.get('volatility', 0)}")
    print(f"  曲线形态:   {result.get('shape', 'steady')}")

    curve = result.get("curve", [])
    if curve:
        print(c("\n🎢 张力曲线", "cyan"))
        for p in curve:
            bar = "█" * max(1, int(p["tension"] / 5))
            marker = " ← 高潮" if p["episode"] == result.get("climax_episode") else ""
            print(f"  EP{p['episode']:<3} {p['tension']:>5}  {bar}{marker}")

    for s in result.get("suggestions", []):
        print(c(f"  ✦ {s}", "yellow"))

    cm.close()
    return 0


def cmd_drama_screen_time(args):
    """角色戏份平衡分析（v5.4.1 新增）"""
    cm = _get_memory(args)
    print(c("\n🎭 角色戏份平衡分析（v5.4.1）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")

    try:
        result = cm.drama_screen_time(args.drama_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"  剧名:       {result['title']}")
    print(f"  台词总数:   {result.get('total_lines', 0)}")

    balance = result.get("balance", {})
    if balance:
        print(f"  结构判定:   {balance.get('structure_label', '-')}")
        print(f"  Top 角色:   {balance.get('top_character', '-')} ({balance.get('top_share_pct', 0)}%)")
        print(f"  基尼系数:   {balance.get('gini_coefficient', 0)}")

    chars = result.get("characters", [])
    if chars:
        print(c("\n👤 角色戏份排行", "cyan"))
        print(f"{'#':>3}{'角色':<12}{'台词':>8}{'字数':>8}{'场景':>6}{'集数':>6}{'占比':>8}")
        print("-" * 60)
        for cs in chars[:20]:
            print(f"{cs['rank']:>3}{cs['name']:<12}{cs['line_count']:>8}"
                  f"{cs['word_count']:>8}{cs['scene_count']:>6}"
                  f"{cs['episode_count']:>6}{cs['share_pct']:>7}%")

    for s in result.get("suggestions", []):
        print(c(f"  ✦ {s}", "yellow"))

    cm.close()
    return 0


# ===== v5.4.2 新增能力 CLI 命令实现 =====

def cmd_fed_acl_add(args):
    """联邦 ACL：添加规则（v5.4.2 新增）"""
    cm = _get_memory(args)
    result = cm.federated_acl.add_rule(
        principal=args.principal, resource=args.resource,
        operations=args.operations, effect=args.effect,
        priority=args.priority, trust_min=args.trust_min,
        expires_hours=args.expires_hours, note=args.note)
    if result.get("success"):
        print(c("\n✅ ACL 规则已添加", "green"))
        print(f"   规则 ID:   {result['rule_id']}")
        print(f"   主体:      {result['principal']}")
        print(f"   资源:      {result['resource']}")
        print(f"   操作:      {result['operations']}")
        print(f"   效果:      {result['effect']} (priority={args.priority})")
    else:
        print(c(f"\n❌ {result.get('error', '添加失败')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_fed_acl_remove(args):
    """联邦 ACL：删除规则（v5.4.2 新增）"""
    cm = _get_memory(args)
    result = cm.federated_acl.remove_rule(args.rule_id)
    if result.get("success"):
        print(c(f"\n✅ ACL 规则已删除: {args.rule_id}", "green"))
    else:
        print(c(f"\n❌ {result.get('error', '删除失败')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_fed_acl_list(args):
    """联邦 ACL：规则列表（v5.4.2 新增）"""
    cm = _get_memory(args)
    rules = cm.federated_acl.list_rules(principal=args.principal,
                                        effect=args.effect, limit=args.limit)
    print(c(f"\n🛡️  联邦 ACL 规则（共 {len(rules)} 条）", "bold"))
    print("=" * 70)
    if not rules:
        print(c("  暂无规则（注意：无规则匹配时访问将被默认拒绝）", "yellow"))
        cm.close()
        return 0
    now = time.time()
    for r in rules:
        expired = bool(r["expires_at"] and r["expires_at"] < now)
        effect_str = c(r["effect"], "green" if r["effect"] == "allow" else "red")
        res = "all" if r["resource_type"] == "all" else f"{r['resource_type']}:{r['resource_value']}"
        extra = ""
        if r["trust_min"] > 0:
            extra += f" trust>={r['trust_min']}"
        if r["expires_at"]:
            extra += "（已过期）" if expired else f"（至 {format_time(r['expires_at'])}）"
        print(f"  {c(r['rule_id'][:12], 'cyan')} [{effect_str}] {r['principal']:<12} "
              f"{res:<28} ops={r['operations']} prio={r['priority']}{extra}")
        if r.get("note"):
            print(f"      ↳ {r['note']}")
    cm.close()
    return 0


def cmd_fed_acl_check(args):
    """联邦 ACL：访问评估（v5.4.2 新增）"""
    cm = _get_memory(args)
    result = cm.federated_acl.check_access(
        peer_id=args.peer, memory_id=args.memory_id,
        operation=args.operation, peer_trust=args.trust,
        memory_category=args.category, memory_tags=args.tags)
    if result["allowed"]:
        print(c("\n✅ 允许访问", "green"))
    else:
        print(c("\n❌ 拒绝访问", "red"))
    print(f"   Peer:      {args.peer}")
    print(f"   记忆:      {args.memory_id}")
    print(f"   操作:      {args.operation}")
    print(f"   判定:      {result['effect']}")
    print(f"   原因:      {result['reason']}")
    cm.close()
    return 0 if result["allowed"] else 2


def cmd_fed_acl_stats(args):
    """联邦 ACL：统计（v5.4.2 新增）"""
    cm = _get_memory(args)
    s = cm.federated_acl.acl_stats()
    print(c("\n🛡️  联邦 ACL 统计", "bold"))
    print("=" * 40)
    print(f"   规则总数:     {s['total_rules']}")
    for eff, cnt in s.get("by_effect", {}).items():
        print(f"   {eff} 规则:    {cnt}")
    for rt, cnt in s.get("by_resource_type", {}).items():
        print(f"   资源[{rt}]:  {cnt}")
    print(f"   已过期规则:   {s['expired_rules']}")
    print(c(f"   拒绝审计事件: {s['deny_audit_events']}",
            "yellow" if s["deny_audit_events"] else "green"))
    cm.close()
    return 0


def cmd_share_conflicts(args):
    """共享冲突：列表（v5.4.2 新增）"""
    cm = _get_memory(args)
    conflicts = cm.share_conflict.list_conflicts(status=args.status, limit=args.limit)
    print(c(f"\n⚔️  共享记忆冲突（共 {len(conflicts)} 条）", "bold"))
    print("=" * 70)
    if not conflicts:
        print(c("  暂无冲突记录", "green"))
        cm.close()
        return 0
    status_color = {"open": "red", "resolved": "green", "dismissed": "yellow"}
    for cf in conflicts:
        print(f"  {c(cf['conflict_id'][:12], 'cyan')} [{c(cf['status'], status_color.get(cf['status'], 'yellow'))}] "
              f"{cf['conflict_type']:<10} 本地记忆: {cf['local_memory_id'][:12]}…")
        local_prev = cf["local_snapshot"].get("content_preview", "")
        incoming_prev = cf["incoming_snapshot"].get("content_preview", "")
        peer = cf.get("incoming_peer") or "-"
        print(f"      本地:   {local_prev[:50]}")
        print(f"      传入:   {incoming_prev[:50]}  (from: {peer})")
        if cf["status"] == "resolved":
            print(c(f"      ↳ 已按 {cf['resolution']} 解决 → {cf['resolved_memory_id'] or '-'}", "green"))
    cm.close()
    return 0


def cmd_share_conflict_resolve(args):
    """共享冲突：解决（v5.4.2 新增）"""
    cm = _get_memory(args)
    result = cm.share_conflict.resolve(args.conflict_id, args.strategy, actor=args.actor)
    if result.get("success"):
        print(c("\n✅ 冲突已解决", "green"))
        print(f"   冲突 ID:   {args.conflict_id}")
        print(f"   策略:      {args.strategy}")
        print(f"   结果:      {result['resolution']}")
        print(f"   目标记忆:  {result.get('resolved_memory_id') or '-'}")
        if result.get("note"):
            print(c(f"   备注:      {result['note']}", "yellow"))
    else:
        print(c(f"\n❌ {result.get('error', '解决失败')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_share_conflict_dismiss(args):
    """共享冲突：关闭（v5.4.2 新增）"""
    cm = _get_memory(args)
    result = cm.share_conflict.dismiss(args.conflict_id, actor=args.actor)
    if result.get("success"):
        print(c(f"\n✅ 冲突已关闭: {args.conflict_id}", "green"))
    else:
        print(c(f"\n❌ {result.get('error', '关闭失败')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_share_conflict_stats(args):
    """共享冲突：统计（v5.4.2 新增）"""
    cm = _get_memory(args)
    s = cm.share_conflict.stats()
    print(c("\n⚔️  共享冲突统计", "bold"))
    print("=" * 40)
    print(f"   冲突总数:   {s['total_conflicts']}")
    print(c(f"   待处理:     {s['open']}", "red" if s["open"] else "green"))
    print(f"   已解决:     {s['resolved']}")
    print(f"   已关闭:     {s['dismissed']}")
    for ct, cnt in s.get("by_type", {}).items():
        print(f"   类型[{ct}]: {cnt}")
    for res, cnt in s.get("by_resolution", {}).items():
        if res:
            print(f"   解决[{res}]: {cnt}")
    cm.close()
    return 0


# ===== v5.3.9 新增五大能力 CLI 命令实现 =====

def cmd_intent_router(args):
    """意图分类路由 CLI 入口"""
    cm = _get_memory(args)
    print(c("\n🧭 意图分类路由（v5.3.9）", "bold"))
    print("=" * 60)
    try:
        result = cm.classify_intent(args.text, force=getattr(args, "force", None))
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1
    if getattr(args, "json_output", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        cm.close()
        return 0
    print(f"  输入文本:    {(args.text or '')[:60]}...")
    print(f"  意图 ID:     {c(result['intent'], 'cyan')}")
    print(f"  意图名:      {result['label']}")
    print(f"  置信度:      {result['confidence']:.3f}")
    print(f"  路由目标:    {result.get('routing_target', result.get('routing', '—'))}")
    matched = result.get("matched_rules") or []
    if matched:
        print(f"  命中规则:    {', '.join(matched[:5])}")
    top_keywords = result.get("top_keywords_hits") or []
    if not top_keywords:
        kw_hits = result.get("keyword_hits") or {}
        if kw_hits:
            top_keywords = [(k, kw_hits[k]) for k in list(kw_hits)[:5]]
    if top_keywords:
        print(f"  关键词命中:  {', '.join([f'{k}({w})' for k, w in top_keywords[:5]])}")
    print(f"  层级:        {'规则' if result['level'] == 0 else ('关键词' if result['level'] == 1 else 'LLM 兜底')}")
    cm.close()
    return 0


def cmd_conflict_scan(args):
    """矛盾扫描 + 自动衰减 CLI 入口"""
    cm = _get_memory(args)
    print(c("\n⚡ 矛盾扫描 + 自动衰减（v5.3.9）", "bold"))
    print("=" * 60)
    try:
        result = cm.scan_conflicts(
            category=getattr(args, "category", None),
            limit=max(1, int(getattr(args, "limit", 500))),
            apply_decay=bool(getattr(args, "apply_decay", False)),
        )
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1
    if getattr(args, "json_output", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        cm.close()
        return 0
    print(f"  扫描范围:    {getattr(args, 'category', '全部')}  (limit={getattr(args, 'limit', 500)})")
    print(f"  模式:        {'只读扫描' if not getattr(args, 'apply_decay', False) else c('已应用衰减！', 'red')}")
    print(f"  矛盾总数:    {c(str(result['conflicts_found']), 'red' if result['conflicts_found'] else 'green')}")
    if result["conflicts_found"] == 0:
        print(c("\n✅ 未发现明显矛盾", "green"))
        cm.close()
        return 0
    type_cnt: Dict[str, int] = {}
    for p in result.get("conflicts", []):
        t = p.get("conflict_type", "unknown")
        type_cnt[t] = type_cnt.get(t, 0) + 1
    print(f"  矛盾类型:    {', '.join([f'{k}({v})' for k, v in type_cnt.items()])}")
    print(c("\n📋 前 8 条矛盾：", "cyan"))
    for i, p in enumerate(result.get("conflicts", [])[:8], 1):
        ids = (p.get("id_a") or "")[:6] + " ↔ " + (p.get("id_b") or "")[:6]
        print(f"  [{i}] {c(p['conflict_type'], 'yellow')}  sev={p['severity']:.2f}  ids={ids}")
        print(f"       {p.get('suggestion', '')[:80]}")
    plan = result.get("decay_plan") or result.get("decay_planned") or []
    if plan:
        print(c(f"\n📉 衰减计划（共 {len(plan)} 条，前 5 条）：", "cyan"))
        for a in plan[:5]:
            tags = ", ".join(a.get("added_tags") or [])
            print(f"   • {a.get('memory_id', '')[:8]}  Δimp={a.get('delta_importance', 0):+.2f} "
                  f"tags=[{tags}]  reason={a.get('reason', '')[:30]}")
    if "decay_applied" in result:
        print(c(f"\n✅ 已应用衰减 {result['decay_applied']} 条", "green"))
    cm.close()
    return 0


def cmd_skill_extract(args):
    """记忆→技能模板抽取 CLI 入口"""
    cm = _get_memory(args)
    print(c("\n🧠 记忆 → 技能模板抽取（v5.3.9）", "bold"))
    print("=" * 60)
    try:
        result = cm.extract_skills(
            category=getattr(args, "category", None),
            limit=max(1, int(getattr(args, "limit", 2000))),
            min_cluster_size=max(1, int(getattr(args, "min_cluster", 2))),
        )
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1
    if getattr(args, "json_output", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        cm.close()
        return 0
    print(f"  处理记忆数:  {result['memories_processed']}")
    print(f"  抽取出技能:  {c(str(result['skills_found']), 'green' if result['skills_found'] else 'yellow')}")
    if not result["skills_found"]:
        print(c("\n⚠️  未抽取出可用技能（增加记忆量或降低 --min-cluster）", "yellow"))
        cm.close()
        return 0
    for i, s in enumerate(result["skills"][:8], 1):
        print(c(f"\n  [技能 {i}] {s['name']}  (cluster={s['cluster_size']})", "bold"))
        triggers = s.get("triggers") or []
        if triggers:
            print(f"       触发词: {', '.join(triggers[:8])}")
        slots = s.get("slots") or []
        if slots:
            print(f"       槽位:   {', '.join([sl.get('name','?') for sl in slots[:8]])}")
        steps = s.get("steps") or []
        if steps:
            print(f"       步骤 ({len(steps)}):")
            for j, st in enumerate(steps[:6], 1):
                action = st if isinstance(st, str) else st.get("action", str(st))
                print(f"         {j}. {action[:70]}{'…' if len(action) > 70 else ''}")
        samples = s.get("examples") or []
        if samples:
            print(f"       样例 ({len(samples)}):")
            for ex in samples[:2]:
                print(f"         - {(ex or '')[:60]}…")
    cm.close()
    return 0


def cmd_rerank_search(args):
    """混合检索增强（查询扩展 + Cross-Encoder 重排）CLI 入口"""
    cm = _get_memory(args)
    try:
        result = cm.search_enhanced(
            query=args.query,
            max_results=max(1, int(getattr(args, "top", 10))),
            expand=not bool(getattr(args, "no_expand", False)),
            rerank=not bool(getattr(args, "no_rerank", False)),
        )
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1
    if getattr(args, "json_output", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        cm.close()
        return 0
    print(f"  查询:        {args.query[:70]}")
    print(f"  查询扩展:    {'启用' if not getattr(args, 'no_expand', False) else '关闭'}")
    print(f"  Cross-Enc:   {'启用' if not getattr(args, 'no_rerank', False) else '关闭'}")
    exp = result.get("query_expansion") or {}
    if exp:
        print(c("\n✨ 查询扩展：", "cyan"))
        terms = exp.get("expanded_terms") or []
        rewrites = exp.get("rewrites") or []
        if terms:
            print(f"     扩展词:   {', '.join(terms[:12])}")
        if rewrites:
            for r in rewrites[:3]:
                print(f"     Rewrite:  {r}")
    if not result.get("reranked"):
        chunks = result.get("chunks") or []
        if not chunks:
            print(c("\n⚠️  无匹配结果", "yellow"))
        else:
            print(c(f"\n📄 召回 {len(chunks)} 条（不重排，前 8）：", "cyan"))
            for i, c_ in enumerate(chunks[:8], 1):
                print(f"  [{i}] score={c_.get('relevance_score', 0):.3f}  id={c_['memory_id'][:8]}")
                print(f"       {(c_.get('content') or '')[:90]}…")
        cm.close()
        return 0
    print(f"  初始召回:    {result.get('initial_count', 0)}")
    print(f"  重排后输出:  {result.get('reranked_count', 0)}")
    print(c("\n📄 重排结果（前 8）：", "cyan"))
    for i, rk in enumerate(result["reranked"][:8], 1):
        delta = rk.get("delta_rank") or 0
        mark = "↑" if delta < 0 else ("↓" if delta > 0 else "→")
        print(f"  [{i}] fused={rk.get('fused_score', 0):.3f}  orig={rk.get('original_score', 0):.3f}  "
              f"{mark}{abs(delta)}  id={rk['memory_id'][:8]}")
        print(f"       {(rk.get('content') or '')[:90]}…")
    cm.close()
    return 0


def cmd_session_focus(args):
    """会话焦点聚类 + 漂移检测 CLI 入口"""
    cm = _get_memory(args)
    print(c("\n🎯 会话焦点分析（v5.3.9）", "bold"))
    print("=" * 60)
    # 解析 --messages/-m role:content
    msgs: List[Dict[str, Any]] = []
    for idx, entry in enumerate(args.messages or [], 1):
        if ":" in entry:
            role, content = entry.split(":", 1)
        else:
            role, content = "user", entry
        msgs.append({
            "id": f"m{idx}",
            "role": role.strip().lower() or "user",
            "content": content.strip(),
            "timestamp": float(idx),
        })
    window = max(5, int(getattr(args, "window", 40)))
    augment = getattr(args, "augment", None)
    try:
        result = cm.session_focus(msgs, window_size=window, augment_query=augment)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1
    if getattr(args, "json_output", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        cm.close()
        return 0
    print(f"  消息数:      {len(msgs)}")
    print(f"  窗口大小:    {window}")
    print(f"  聚类数:      {len(result.get('clusters', []))}")
    drift = result.get("drift_score", 0.0)
    drift_color = "green" if drift < 0.3 else ("yellow" if drift < 0.6 else "red")
    print(f"  漂移得分:    {c(f'{drift:.3f}', drift_color)}  ({'稳定' if drift < 0.3 else ('轻微漂移' if drift < 0.6 else '严重漂移')})")
    kw = result.get("focus_keywords") or []
    if kw:
        print(f"  当前焦点:    {', '.join([f'{k}({w:.2f})' for k, w in kw[:10]])}")
    recent_shifts = result.get("recent_shifts") or []
    if recent_shifts:
        print(c("\n🔄 最近主题切换：", "cyan"))
        for sh in recent_shifts[:4]:
            print(f"     t={sh.get('window_index')}  top1={sh.get('top_keyword', '—')} "
                  f"jaccard={sh.get('jaccard', 0):.2f}")
    if augment:
        eq = result.get("enhanced_query")
        if eq:
            print(c("\n✨ 增强查询：", "cyan"))
            print(f"     原始: {augment}")
            print(f"     增强: {eq}")
    cm.close()
    return 0


def _main_dispatch(args, parser=None):
    """命令分发（v5.3.7 重构：将 commands dict 和 dispatch 逻辑独立）

    Args:
        args: argparse 解析结果
        parser: 主 ArgumentParser 实例，用于未知命令时打印帮助
                （v5.5.8 修复：此前引用了 main() 的局部 parser，触发 NameError）
    """
    commands = {
        "init": cmd_init,
        "add": cmd_add,
        "search": cmd_search,
        "list": cmd_list,
        "get": cmd_get,
        "update": cmd_update,
        "delete": cmd_delete,
        "stats": cmd_stats,
        "star": cmd_star,
        "unstar": cmd_unstar,
        "batch-delete": cmd_batch_delete,
        "tag-search": cmd_tag_search,
        "deduplicate": cmd_deduplicate,
        "export-md": cmd_export_md,
        "health": cmd_health,
        "summarize": cmd_summarize,
        "vacuum": cmd_vacuum,
        "purge-trash": cmd_purge_trash,
        "analyze": cmd_analyze,
        "import-md": cmd_import_md,
        "migrate": cmd_migrate,
        "export-html": cmd_export_html,
        "export-xml": cmd_export_xml,
        "import-xml": cmd_import_xml,
        "export-json": cmd_export_json,
        "import-json": cmd_import_json,
        "import-csv": cmd_import_csv,
        "merge": cmd_merge,
        "remind": cmd_remind,
        "tags": cmd_tags,
        "cats": cmd_cats,
        "timeline": cmd_timeline,
        "top": cmd_top,
        "random": cmd_random,
        "rename-tag": cmd_rename_tag,
        "rename-cat": cmd_rename_cat,
        "config": cmd_config,
        "rekey": cmd_rekey,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "doctor": cmd_doctor,
        "find": cmd_find,
        "audit": cmd_audit,
        "recent": cmd_recent,
        "trash": cmd_trash,
        "restore": cmd_restore,
        "consolidate": cmd_consolidate,
        "graph": cmd_graph,
        "personality": cmd_personality,
        "agent-stats": cmd_agent_stats,
        "agent-list": cmd_agent_list,
        "evolve": cmd_evolve,
        "agent-transfer": cmd_agent_transfer,
        "agent-clean": cmd_agent_clean,
        "agent-list-memories": cmd_agent_list_memories,
        "agent-rank": cmd_agent_rank,
        "agent-forget": cmd_agent_forget,
        "agent-profile": cmd_agent_profile,
        "agent-merge": cmd_agent_merge,
        "agent-export": cmd_agent_export,
        "agent-search": cmd_agent_search,
        "agent-compare": cmd_agent_compare,
        "drama-search": cmd_drama_search,
        "char-ranking": cmd_char_ranking,
        "agent-diff": cmd_agent_diff,
        "agent-purge": cmd_agent_purge,
        "drama-progress-update": cmd_drama_progress_update,
        "drama-rec2": cmd_drama_rec2,
        "agent-timeline": cmd_agent_timeline,
        "agent-heatmap": cmd_agent_heatmap,
        "drama-binge": cmd_drama_binge,
        "char-network": cmd_char_network,
        "agent-sentiment": cmd_agent_sentiment,
        "memory-decay": cmd_memory_decay,
        "drama-compare": cmd_drama_compare,
        "char-arc": cmd_char_arc,
        "memory-cluster": cmd_memory_cluster,
        "agent-insight": cmd_agent_insight,
        "drama-summary": cmd_drama_summary,
        "scene-tension": cmd_scene_tension,
        "memory-link": cmd_memory_link,
        "memory-recall": cmd_memory_recall,
        "drama-pacing": cmd_drama_pacing,
        "char-interaction": cmd_char_interaction,
        "quality": cmd_quality,
        "similar": cmd_similar,
        "backup": cmd_backup,
        "backup-restore": cmd_backup_restore,
        "gc": cmd_gc,
        "export": cmd_export,
        "import": cmd_import,
        "compliance": cmd_compliance,
        "serve": cmd_serve,
        "cleanup": cmd_cleanup,
        "archive": cmd_archive,
        "archived-list": cmd_archived_list,
        "archived-restore": cmd_archived_restore,
        "archived-purge": cmd_archived_purge,
        "export-obsidian": cmd_export_obsidian,
        "batch-add": cmd_batch_add,
        "import-url": cmd_import_url,
        "export-excel": cmd_export_excel,
        "import-excel": cmd_import_excel,
        "copy": cmd_copy,
        "move": cmd_move,
        "fuzzy-search": cmd_fuzzy_search,
        "search-history": cmd_search_history,
        "batch-add-tags": cmd_batch_add_tags,
        "batch-remove-tags": cmd_batch_remove_tags,
        "merge-tags": cmd_merge_tags,
        "db-backup": cmd_db_backup,
        "db-backups": cmd_db_backups,
        "db-restore": cmd_db_restore,
        "db-clean-backups": cmd_db_clean_backups,
        # AI 短剧记忆模块（v5.2.1 新增）
        "drama-add": cmd_drama_add,
        "drama-list": cmd_drama_list,
        "drama-get": cmd_drama_get,
        "drama-update": cmd_drama_update,
        "drama-delete": cmd_drama_delete,
        "drama-stats": cmd_drama_stats,
        "drama-recommend": cmd_drama_recommend,
        "drama-progress": cmd_drama_progress,
        "drama-export": cmd_drama_export,
        "drama-import": cmd_drama_import,
        "drama-stars": cmd_drama_stars,
        "scene-list-lines": cmd_scene_list_lines,
        "char-list-lines": cmd_char_list_lines,
        "drama-info": cmd_drama_info,
        "line-random": cmd_line_random,
        "char-profile": cmd_char_profile,
        "line-add": cmd_line_add,
        "line-list": cmd_line_list,
        "line-search": cmd_line_search,
        "line-classic": cmd_line_classic,
        "line-update": cmd_line_update,
        "line-delete": cmd_line_delete,
        "char-add": cmd_char_add,
        "char-list": cmd_char_list,
        "char-get": cmd_char_get,
        "char-update": cmd_char_update,
        "char-delete": cmd_char_delete,
        "scene-add": cmd_scene_add,
        "scene-list": cmd_scene_list,
        "scene-get": cmd_scene_get,
        "scene-update": cmd_scene_update,
        "scene-delete": cmd_scene_delete,
        # v5.2.4 新增
        "note-add": cmd_note_add,
        "note-list": cmd_note_list,
        "note-delete": cmd_note_delete,
        "template-add": cmd_template_add,
        "template-list": cmd_template_list,
        "template-use": cmd_template_use,
        "template-delete": cmd_template_delete,
        "batch-update": cmd_batch_update,
        "schedule": cmd_schedule,
        # v5.2.5 新增：记忆关联 + 置顶
        "link": cmd_link,
        "links": cmd_links,
        "unlink": cmd_unlink,
        "pin": cmd_pin,
        "unpin": cmd_unpin,
        "pinned": cmd_pinned,
        # v5.2.7 新增：记忆版本历史
        "history": cmd_history,
        "rollback": cmd_rollback,
        # v5.2.8 新增
        "export-csv": cmd_export_csv,
        "diff": cmd_diff,
        # v5.2.8 实验性：多 Agent 记忆空间（v6.0.0 全量推送预览）
        "space-create": cmd_space_create,
        "space-list": cmd_space_list,
        "space-join": cmd_space_join,
        "space-add-member": cmd_space_add_member,
        "space-share": cmd_space_share,
        "space-memories": cmd_space_memories,
        "space-stats": cmd_space_stats,
        # v5.3.7 新增
        "memory-importance": cmd_memory_importance,
        "memory-context": cmd_memory_context,
        "agent-emotion": cmd_agent_emotion,
        "drama-genre-trend": cmd_drama_genre_trend,
        "drama-binge-score": cmd_drama_binge_score,
        "char-relationship": cmd_char_relationship,
        # v5.4.1 新增
        "memory-reflection": cmd_memory_reflection,
        "memory-lineage": cmd_memory_lineage,
        "memory-reinforce": cmd_memory_reinforce,
        # v5.5.8 新增
        "memory-diff": cmd_memory_diff,
        "drama-plot-thread": cmd_drama_plot_thread,
        "drama-episode-curve": cmd_drama_episode_curve,
        "drama-screen-time": cmd_drama_screen_time,
        # v5.4.2 新增
        "fed-acl-add": cmd_fed_acl_add,
        "fed-acl-remove": cmd_fed_acl_remove,
        "fed-acl-list": cmd_fed_acl_list,
        "fed-acl-check": cmd_fed_acl_check,
        "fed-acl-stats": cmd_fed_acl_stats,
        "share-conflicts": cmd_share_conflicts,
        "share-conflict-resolve": cmd_share_conflict_resolve,
        "share-conflict-dismiss": cmd_share_conflict_dismiss,
        "share-conflict-stats": cmd_share_conflict_stats,
        # v5.4.3 新增
        "agent-influence": cmd_agent_influence,
        "memory-overlap": cmd_memory_overlap,
        "conflict-graph": cmd_conflict_graph,
        "drama-quote-map": cmd_drama_quote_map,
        "char-growth": cmd_char_growth,
        "scene-rhythm": cmd_scene_rhythm,
        # v5.3.9 新增五大能力
        "intent-router": cmd_intent_router,
        "conflict-scan": cmd_conflict_scan,
        "skill-extract": cmd_skill_extract,
        "rerank-search": cmd_rerank_search,
        "session-focus": cmd_session_focus,
        # v5.4.5 新增向量检索
        "rebuild-embeddings": cmd_rebuild_embeddings,
        "embedding-status": cmd_embedding_status,
    }

    cmd = commands.get(args.command)
    if cmd:
        sys.exit(cmd(args) or 0)
    else:
        if parser is not None:
            parser.print_help()
        else:
            print(f"未知命令: {args.command}")
        sys.exit(1)


def cmd_rebuild_embeddings(args):
    """重建/增量构建嵌入向量（v5.4.5 新增，v5.4.6 增量模式）"""
    cm = _get_memory(args)

    incremental = not getattr(args, 'full', False)
    mode_label = "增量构建" if incremental else "全量重建"
    print(c(f"\n🔧 {mode_label}嵌入向量（v5.4.6）", "bold"))
    print("=" * 60)

    try:
        result = cm.rebuild_embeddings(batch_size=args.batch_size, incremental=incremental)
    except Exception as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if not result.get("success"):
        print(c(f"\n❌ {result.get('error', '未知错误')}", "red"))
        print(c("   安装 sentence-transformers: pip install sentence-transformers", "yellow"))
        print(c("   或配置 OpenAI/Ollama 后端: MINDFORGE_EMBEDDING_BACKEND=openai", "yellow"))
        cm.close()
        return 1

    print(c(f"\n✅ {mode_label}完成", "green"))
    print(f"   模式:       {result.get('mode', mode_label)}")
    print(f"   待处理:     {result['total']}")
    print(f"   已生成向量: {result['embedded']}")
    print(f"   跳过（空）: {result['skipped']}")
    if result['errors']:
        print(c(f"   错误:       {result['errors']}", "yellow"))

    cm.close()
    return 0


def cmd_embedding_status(args):
    """查看嵌入向量状态（v5.4.5 新增）"""
    cm = _get_memory(args)

    print(c("\n📊 嵌入向量状态（v5.4.5）", "bold"))
    print("=" * 60)

    try:
        status = cm.get_embedding_status()
    except Exception as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if status["available"]:
        print(c("  状态:       ✅ 可用", "green"))
        print(f"  模型:       {status['model_name']}")
        print(f"  维度:       {status['dimension']}")
        print(f"  向量数量:   {status['embedding_count']}")
    else:
        print(c("  状态:       ❌ 不可用", "red"))
        print(c("  原因:       sentence-transformers 未安装", "yellow"))
        print(c("  安装命令:   pip install sentence-transformers", "cyan"))
        print("\n  注意: 向量检索不可用时，搜索自动降级为 TF-IDF + Fuzzy")

    cm.close()
    return 0


def cmd_cleanup(args):
    """清理过期记忆（v5.1.3 新增）"""
    cm = _get_memory(args)

    count = cm.cleanup(max_age_hours=args.hours, layer=args.layer)

    print(c("\n✅ 清理完成", "green"))
    print(f"   清理了 {count} 条过期记忆（层级: {args.layer}, 超过 {args.hours} 小时）")

    cm.close()
    return 0


def cmd_archive(args):
    """自动归档过期记忆（v5.4.6 新增）"""
    cm = _get_memory(args)
    result = cm.auto_archive(max_age_hours=args.hours, layer=args.layer)
    print(c("\n✅ 归档完成（v5.4.6）", "green"))
    print(f"   归档了 {result['archived']} 条记忆（层级: {result['layer']}, 超过 {result['max_age_hours']} 小时）")
    print(c("   使用 archived-list 查看归档记录，archived-restore 恢复", "cyan"))
    cm.close()
    return 0


def cmd_archived_list(args):
    """列出归档记忆（v5.4.6 新增）"""
    cm = _get_memory(args)
    entries = cm.list_archived(layer=args.layer, category=args.category, limit=args.limit)
    if not entries:
        print(c("⚠️  没有归档记忆", "yellow"))
        cm.close()
        return 0
    print(c(f"\n📦 归档记忆（{len(entries)} 条）", "bold"))
    print("=" * 60)
    for e in entries:
        content_preview = (e.get("content") or "")[:60]
        print(f"   [{e.get('archive_id', e.get('id', ''))[:8]}] "
              f"[{e.get('category', '')}] {content_preview}...")
        print(f"     层级: {e.get('layer', '')} | 归档时间: {format_time(e.get('archived_at', 0))}")
    cm.close()
    return 0


def cmd_archived_restore(args):
    """从归档恢复记忆（v5.4.6 新增）"""
    cm = _get_memory(args)
    result = cm.restore_archived(args.archive_id)
    if result.get("restored"):
        print(c("\n✅ 恢复成功", "green"))
        print(f"   记忆 ID: {result.get('memory_id', '')}")
    else:
        print(c(f"\n❌ 恢复失败: {result.get('error', '未知错误')}", "red"))
    cm.close()
    return 0 if result.get("restored") else 1


def cmd_archived_purge(args):
    """永久删除过期归档记忆（v5.4.6 新增）"""
    cm = _get_memory(args)
    count = cm.purge_archived(older_than_days=args.older_than_days)
    print(c("\n✅ 清理完成", "green"))
    print(f"   永久删除了 {count} 条归档记忆（超过 {args.older_than_days} 天）")
    cm.close()
    return 0


def cmd_export_obsidian(args):
    """导出为 Obsidian Vault 格式（v5.4.6 新增）"""
    cm = _get_memory(args)
    layer = MemoryLayer.from_string(args.layer) if args.layer else None
    result = cm.export_obsidian(
        output_dir=args.output_dir,
        category=args.category,
        layer=layer,
        starred_only=args.starred,
    )
    print(c("\n✅ Obsidian Vault 导出完成（v5.4.6）", "green"))
    print(f"   导出目录: {result['output_dir']}")
    print(f"   导出记忆: {result['exported']} 条")
    if result['errors']:
        print(c(f"   错误: {result['errors']} 条", "yellow"))
    print(c("   在 Obsidian 中打开该目录即可使用", "cyan"))
    cm.close()
    return 0


def cmd_batch_add(args):
    """从文件批量添加记忆（v5.1.3 新增）"""
    cm = _get_memory(args)
    input_path = Path(args.input)

    if not input_path.exists():
        print(c(f"\n❌ 文件不存在: {input_path}", "red"))
        return 1

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        print(c("\n❌ JSON 解析失败", "red"))
        return 1

    entries = data.get("memories", data)
    if not isinstance(entries, list):
        print(c("\n❌ 数据格式错误：必须是 memories 列表", "red"))
        return 1

    count = cm.batch_add(entries)
    print(c("\n✅ 批量添加完成", "green"))
    print(f"   成功添加 {count} 条记忆（共 {len(entries)} 条）")

    cm.close()
    return 0


def _is_private_ip(ip_str):
    """检查 IP 是否为内网/保留地址"""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        return False


def _validate_url_safe(url):
    """校验 URL 安全性，防止 SSRF

    v5.4.2 安全修复：返回解析到的安全 IP，调用方用 IP 直连绕过 DNS Rebinding TOCTOU。
    同时拦截 0.0.0.0、::、::ffff:* 等 IPv6 映射地址。
    """
    if len(url) > 2048:
        raise ValueError("URL 过长")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("只允许 http/https 协议")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("无效的 hostname")
    # 检查十进制/十六进制 IP 编码
    if hostname.isdigit():
        raise ValueError("不允许十进制 IP 编码")
    if hostname.lower().startswith("0x"):
        raise ValueError("不允许十六进制 IP 编码")
    # 拦截 0.0.0.0 和 IPv6 映射地址
    if hostname in ("0.0.0.0", "::", "::0"):
        raise ValueError(f"不允许的特殊地址: {hostname}")
    # DNS 解析并检查所有 IP
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise ValueError("DNS 解析失败")
    safe_ip = None
    for info in infos:
        ip = info[4][0]
        # 拦截 IPv6 映射的 IPv4 地址（如 ::ffff:127.0.0.1）
        if "::ffff:" in ip.lower():
            mapped = ip.split(":")[-1]
            if _is_private_ip(mapped):
                raise ValueError(f"hostname 解析到 IPv6 映射内网地址: {ip}")
        if _is_private_ip(ip):
            raise ValueError(f"hostname 解析到内网地址: {ip}")
        if safe_ip is None:
            safe_ip = ip
    # 返回安全 IP 供调用方直连，避免 DNS Rebinding TOCTOU
    return safe_ip


def cmd_import_url(args):
    """从 URL 导入网页内容（v5.1.3 新增）"""
    cm = _get_memory(args)

    try:
        import http.client
        import ssl
        import re
        from urllib.parse import urlparse

        # v5.4.2：校验后获取安全 IP，用 IP 直连绕过 DNS Rebinding
        safe_ip = _validate_url_safe(args.url)
        parsed = urlparse(args.url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        # v5.4.2 修复：HTTPS 时用自定义连接，连接 safe_ip 但 TLS 握手用原始域名校验
        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(
                host=safe_ip, port=port, timeout=10,
                context=ctx, server_hostname=parsed.hostname,
            )
        else:
            conn = http.client.HTTPConnection(host=safe_ip, port=port, timeout=10)

        conn.request("GET", path, headers={
            "Host": parsed.hostname,
            "User-Agent": "MindForge/5.4 URL Importer",
        })
        resp = conn.getresponse()
        content = resp.read(5 * 1024 * 1024).decode("utf-8", errors="ignore")
        conn.close()

        title_match = re.search(r"<title>([^<]+)</title>", content, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else "网页内容"

        text_content = re.sub(r"<[^>]+>", "\n", content)
        text_content = re.sub(r"\s+", " ", text_content).strip()[:5000]

        entry = cm.add(
            content=text_content,
            category=args.category or "web",
            tags=args.tags or ["url", "imported"],
            layer=MemoryLayer.from_string(args.layer),
        )

        print(c("\n✅ URL 导入完成", "green"))
        print(f"   标题: {title}")
        print(f"   内容: {text_content[:100]}...")
        print(f"   ID: {entry.id[:16]}...")

    except (ValueError, TypeError, OSError) as e:
        print(c(f"\n❌ 导入失败: {e}", "red"))
        cm.close()
        return 1

    cm.close()
    return 0


def cmd_export_xml(args):
    """导出记忆为 XML（v5.1.4 新增）"""
    cm = _get_memory(args)
    entries = cm.list(limit=99999)

    if not entries:
        print(c("⚠️  没有可导出的记忆", "yellow"))
        return 0

    try:
        output_path = _safe_export_path(args.output, "memory_export.xml", ".xml")
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)

    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<mindforge>
    <version>{version}</version>
    <export_time>{export_time}</export_time>
    <total>{total}</total>
    <memories>
{memories}
    </memories>
</mindforge>"""

    memories_xml = ""
    for entry in entries:
        tags_xml = "".join(f"<tag>{xml_escape(str(t))}</tag>" for t in entry.tags) if entry.tags else ""
        memories_xml += f"""        <memory>
            <id>{xml_escape(str(entry.id))}</id>
            <content>{xml_escape(entry.content)}</content>
            <category>{xml_escape(str(entry.category))}</category>
            <tags>{tags_xml}</tags>
            <privacy>{xml_escape(str(entry.privacy.value))}</privacy>
            <importance>{xml_escape(str(entry.importance.value))}</importance>
            <memory_type>{xml_escape(str(entry.memory_type.value))}</memory_type>
            <layer>{xml_escape(str(entry.layer.value))}</layer>
            <access_count>{xml_escape(str(entry.access_count))}</access_count>
            <created_at>{xml_escape(str(entry.created_at))}</created_at>
            <updated_at>{xml_escape(str(entry.updated_at))}</updated_at>
            <starred>{'true' if entry.starred else 'false'}</starred>
        </memory>
"""

    export_time = xml_escape(str(format_time(time.time())))
    final_xml = xml_content.format(version=__version__, export_time=export_time, total=len(entries), memories=memories_xml)

    output_path.write_text(final_xml, encoding='utf-8')

    print(c("\n✅ XML 导出完成！", "green"))
    print(f"   文件：{output_path}")
    print(f"   记忆数：{len(entries)}")
    cm.close()
    return 0


def cmd_import_xml(args):
    """从 XML 导入记忆（v5.1.4 新增）"""
    cm = _get_memory(args)

    try:
        input_path = _safe_import_path(args.input)
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1

    if not input_path.exists():
        print(c(f"\n❌ 文件不存在: {input_path}", "red"))
        return 1

    try:
        content = input_path.read_text(encoding='utf-8')
    except (OSError, IOError) as e:
        print(c(f"\n❌ 读取文件失败: {e}", "red"))
        return 1

    try:
        import xml.etree.ElementTree as ET

        # 安全加固：记忆 XML 为无 DTD 的简单格式，
        # 直接拒绝 DOCTYPE/ENTITY 声明，防 XXE 与实体膨胀攻击（billion laughs）
        upper = content[:4096].upper()
        if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
            print(c("\n❌ XML 包含 DOCTYPE/ENTITY 声明，已阻止解析（防 XXE 攻击）", "red"))
            cm.close()
            return 1
        root = ET.fromstring(content)
    except (ValueError, TypeError, ET.ParseError) as e:
        print(c(f"\n❌ XML 解析失败: {e}", "red"))
        return 1

    memories = root.findall('memories/memory')
    if not memories:
        print(c("⚠️  未找到可导入的记忆", "yellow"))
        cm.close()
        return 0

    if not args.force:
        print(c(f"\n🔍 将导入 {len(memories)} 条记忆：", "cyan"))
        for mem in memories[:5]:
            content_elem = mem.find('content')
            content_preview = (content_elem.text or "")[:60] if content_elem is not None else ""
            cat_elem = mem.find('category')
            category = cat_elem.text or "general" if cat_elem is not None else "general"
            print(f"   - [{category}] {content_preview}...")
        if len(memories) > 5:
            print(f"   ... 还有 {len(memories) - 5} 条")
        print(c("\n确认导入？加 --force 执行", "yellow"))
        cm.close()
        return 1

    imported = 0
    skipped = 0
    for mem in memories:
        try:
            content_elem = mem.find('content')
            content_text = (content_elem.text or "") if content_elem is not None else ""
            cat_elem = mem.find('category')
            category = (cat_elem.text or "general") if cat_elem is not None else "general"

            tags = []
            tag_elements = mem.findall('tags/tag')
            for tag_elem in tag_elements:
                if tag_elem.text:
                    tags.append(tag_elem.text)

            privacy_str = mem.find('privacy').text if mem.find('privacy') is not None else "internal"
            importance_str = mem.find('importance').text if mem.find('importance') is not None else "medium"
            layer_str = mem.find('layer').text if mem.find('layer') is not None else "short_term"

            cm.add(
                content=content_text,
                category=category,
                tags=tags,
                privacy=PrivacyLevel.from_string(privacy_str),
                importance=Importance.from_string(importance_str),
                layer=MemoryLayer.from_string(layer_str),
            )
            imported += 1
        except (ValueError, TypeError):
            skipped += 1

    print(c("\n✅ XML 导入完成", "green"))
    print(f"   成功导入：{c(str(imported), 'green')} 条")
    print(f"   导入失败：{c(str(skipped), 'yellow')} 条")
    cm.close()
    return 0


def cmd_export_json(args):
    """导出记忆为 JSON（v5.1.5 新增）"""
    cm = _get_memory(args)
    entries = cm.list(limit=99999)

    if not entries:
        print(c("⚠️  没有可导出的记忆", "yellow"))
        return 0

    try:
        output_path = _safe_export_path(args.output, "memory_export.json", ".json")
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)

    export_data = {
        "version": "5.1.5",
        "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(entries),
        "memories": []
    }

    for entry in entries:
        mem_dict = {
            "id": entry.id,
            "content": entry.content,
            "category": entry.category,
            "tags": entry.tags,
            "privacy": entry.privacy.value,
            "importance": entry.importance.value,
            "memory_type": entry.memory_type.value,
            "layer": entry.layer.value,
            "access_count": entry.access_count,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
            "starred": entry.starred,
            "strength": entry.strength,
            "forgetting_score": entry.forgetting_score,
        }
        export_data["memories"].append(mem_dict)

    indent = 2 if args.pretty else None
    output_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=indent), encoding='utf-8')

    print(c("\n✅ JSON 导出完成！", "green"))
    print(f"   文件：{output_path}")
    print(f"   记忆数：{len(entries)}")
    cm.close()
    return 0


def cmd_import_json(args):
    """从 JSON 导入记忆（v5.1.5 新增）"""
    cm = _get_memory(args)

    try:
        input_path = _safe_import_path(args.input)
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1

    if not input_path.exists():
        print(c(f"\n❌ 文件不存在: {input_path}", "red"))
        return 1

    try:
        content = input_path.read_text(encoding='utf-8')
        data = json.loads(content)
    except (json.JSONDecodeError, OSError, IOError) as e:
        print(c(f"\n❌ JSON 解析失败: {e}", "red"))
        return 1

    memories = data.get("memories", [])
    if not memories:
        print(c("⚠️  未找到可导入的记忆", "yellow"))
        cm.close()
        return 0

    if not args.force:
        print(c(f"\n🔍 将导入 {len(memories)} 条记忆：", "cyan"))
        for mem in memories[:5]:
            content_preview = (mem.get("content") or "")[:60]
            category = mem.get("category") or "general"
            print(f"   - [{category}] {content_preview}...")
        if len(memories) > 5:
            print(f"   ... 还有 {len(memories) - 5} 条")
        if args.dedup_threshold > 0:
            print(c(f"   智能去重已启用（阈值={args.dedup_threshold}）", "purple"))
        print(c("\n确认导入？加 --force 执行", "yellow"))
        cm.close()
        return 1

    # v5.4.6 智能去重
    dedup_threshold = getattr(args, 'dedup_threshold', 0.0) or 0.0

    if dedup_threshold > 0:
        stats = cm.import_json(
            str(input_path),
            skip_duplicates=not args.force,
            dedup_threshold=dedup_threshold,
        )
        print(c("\n✅ JSON 导入完成（智能去重）", "green"))
        print(f"   成功导入：{c(str(stats['imported']), 'green')} 条")
        print(f"   去重跳过：{c(str(stats.get('deduped', 0)), 'purple')} 条")
        print(f"   ID 重复：{c(str(stats['skipped']), 'yellow')} 条")
        print(f"   导入失败：{c(str(stats['failed']), 'red')} 条")
    else:
        imported = 0
        skipped = 0
        for mem in memories:
            try:
                cm.add(
                    content=mem.get("content", ""),
                    category=mem.get("category", "general"),
                    tags=mem.get("tags", []),
                    privacy=PrivacyLevel.from_string(mem.get("privacy", "internal")),
                    importance=Importance.from_string(mem.get("importance", "medium")),
                    layer=MemoryLayer.from_string(mem.get("layer", "short_term")),
                )
                imported += 1
            except (ValueError, TypeError):
                skipped += 1

        print(c("\n✅ JSON 导入完成", "green"))
        print(f"   成功导入：{c(str(imported), 'green')} 条")
        print(f"   导入失败：{c(str(skipped), 'yellow')} 条")
    cm.close()
    return 0


def cmd_import_csv(args):
    """从 CSV 导入记忆（v5.4.6 新增，支持智能去重）"""
    cm = _get_memory(args)

    try:
        input_path = _safe_import_path(args.input)
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1

    if not input_path.exists():
        print(c(f"\n❌ 文件不存在: {input_path}", "red"))
        return 1

    target_layer = None
    if hasattr(args, 'layer') and args.layer:
        target_layer = MemoryLayer.from_string(args.layer)

    dedup_threshold = getattr(args, 'dedup_threshold', 0.0) or 0.0

    if not args.force:
        # 预览 CSV 行数
        try:
            import csv as _csv
            with open(input_path, 'r', encoding='utf-8-sig', newline='') as f:
                row_count = sum(1 for _ in _csv.DictReader(f))
        except Exception:
            row_count = 0
        print(c(f"\n🔍 将导入 {row_count} 条记忆（CSV）", "cyan"))
        if dedup_threshold > 0:
            print(c(f"   智能去重已启用（阈值={dedup_threshold}）", "purple"))
        print(c("\n确认导入？加 --force 执行", "yellow"))
        cm.close()
        return 1

    stats = cm.import_csv(
        str(input_path),
        skip_duplicates=not args.force,
        target_layer=target_layer,
        dedup_threshold=dedup_threshold,
    )

    print(c("\n✅ CSV 导入完成", "green"))
    print(f"   成功导入：{c(str(stats['imported']), 'green')} 条")
    if stats.get('deduped', 0) > 0:
        print(f"   去重跳过：{c(str(stats['deduped']), 'purple')} 条")
    if stats.get('skipped', 0) > 0:
        print(f"   ID 重复：{c(str(stats['skipped']), 'yellow')} 条")
    print(f"   导入失败：{c(str(stats['failed']), 'red')} 条")
    cm.close()
    return 0


def cmd_merge(args):
    """合并重复记忆（v5.1.5 新增）"""
    cm = _get_memory(args)
    entries = cm.list(limit=99999)

    if len(entries) < 2:
        print(c("⚠️  记忆数量不足，无法合并", "yellow"))
        cm.close()
        return 0

    from difflib import SequenceMatcher

    duplicates = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            ratio = SequenceMatcher(None, entries[i].content, entries[j].content).ratio()
            if ratio >= args.threshold:
                duplicates.append({
                    "source": entries[i],
                    "target": entries[j],
                    "similarity": round(ratio, 2)
                })

    if not duplicates:
        print(c("✅ 未找到重复记忆", "green"))
        cm.close()
        return 0

    print(c(f"\n🔍 找到 {len(duplicates)} 组重复记忆（相似度 >= {args.threshold}）:", "cyan"))
    for idx, dup in enumerate(duplicates, 1):
        print(f"\n{idx}. 相似度: {dup['similarity']}")
        print(f"   源记忆: {dup['source'].content[:80]}...")
        print(f"   目标:   {dup['target'].content[:80]}...")

    if args.dry_run:
        print(c("\n⚠️  预览模式，未执行合并", "yellow"))
        cm.close()
        return 0

    merged = 0
    skipped = 0
    for dup in duplicates:
        try:
            cm.delete(dup["target"].id)
            merged += 1
        except (ValueError, TypeError):
            skipped += 1

    print(c("\n✅ 合并完成", "green"))
    print(f"   已合并：{c(str(merged), 'green')} 组")
    print(f"   合并失败：{c(str(skipped), 'yellow')} 组")
    cm.close()
    return 0


def cmd_remind(args):
    """遗忘提醒（v5.1.5 新增）"""
    cm = _get_memory(args)
    entries = cm.list(limit=99999)

    if not entries:
        print(c("⚠️  没有记忆", "yellow"))
        cm.close()
        return 0

    need_remind = sorted(
        [e for e in entries if e.forgetting_score >= args.threshold],
        key=lambda x: x.forgetting_score,
        reverse=True
    )[:args.count]

    if not need_remind:
        print(c("✅ 所有记忆状态良好，无需提醒", "green"))
        cm.close()
        return 0

    print(c(f"\n📢 需要复习的记忆（遗忘分数 >= {args.threshold}）:", "yellow"))
    for idx, entry in enumerate(need_remind, 1):
        print(f"\n{idx}. [{entry.category}] 遗忘分数: {c(f'{entry.forgetting_score:.2f}', 'red')}")
        print(f"   访问次数: {entry.access_count} | 强度: {entry.strength:.2f}")
        print(f"   内容: {entry.content[:120]}...")

    cm.close()
    return 0


def cmd_tags(args):
    """标签管理（v5.1.6 新增）"""
    cm = _get_memory(args)
    entries = cm.list(limit=99999)

    if not entries:
        print(c("⚠️  没有记忆", "yellow"))
        cm.close()
        return 0

    tag_counts = {}
    for entry in entries:
        for tag in entry.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    if not tag_counts:
        print(c("⚠️  没有找到标签", "yellow"))
        cm.close()
        return 0

    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:args.top]

    print(c(f"\n🏷️  标签统计（共 {len(tag_counts)} 个，显示前 {len(sorted_tags)}）:", "cyan"))
    max_len = max(len(t) for t, _ in sorted_tags) if sorted_tags else 0
    for idx, (tag, count) in enumerate(sorted_tags, 1):
        bar = "█" * min(count, 20)
        print(f"{idx:2}. #{tag:<{max_len}} {c(str(count), 'yellow'):>4} {bar}")

    cm.close()
    return 0


def cmd_cats(args):
    """分类统计（v5.1.6 新增）"""
    cm = _get_memory(args)
    entries = cm.list(limit=99999)

    if not entries:
        print(c("⚠️  没有记忆", "yellow"))
        cm.close()
        return 0

    cat_counts = {}
    for entry in entries:
        cat_counts[entry.category] = cat_counts.get(entry.category, 0) + 1

    sorted_cats = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)[:args.top]

    print(c(f"\n📂 分类统计（共 {len(cat_counts)} 个，显示前 {len(sorted_cats)}）:", "cyan"))
    max_len = max(len(cn) for cn, _ in sorted_cats) if sorted_cats else 0
    for idx, (cat, count) in enumerate(sorted_cats, 1):
        bar = "█" * min(count, 20)
        print(f"{idx:2}. {cat:<{max_len}} {c(str(count), 'yellow'):>4} {bar}")

    cm.close()
    return 0


def cmd_timeline(args):
    """时间线视图（v5.1.6 新增）"""
    cm = _get_memory(args)
    entries = cm.list(limit=99999)

    if not entries:
        print(c("⚠️  没有记忆", "yellow"))
        cm.close()
        return 0

    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=args.days)
    cutoff_ts = cutoff.timestamp()

    filtered = [e for e in entries if e.created_at >= cutoff_ts]
    if args.category:
        filtered = [e for e in filtered if e.category == args.category]

    if not filtered:
        print(c(f"⚠️  最近 {args.days} 天没有记忆", "yellow"))
        cm.close()
        return 0

    by_date = {}
    for entry in filtered:
        date_str = datetime.fromtimestamp(entry.created_at).strftime("%Y-%m-%d")
        if date_str not in by_date:
            by_date[date_str] = []
        by_date[date_str].append(entry)

    print(c(f"\n📅 时间线视图（最近 {args.days} 天，共 {len(filtered)} 条）:", "cyan"))
    if args.category:
        print(f"   分类筛选: {args.category}")

    for date_str in sorted(by_date.keys(), reverse=True):
        entries_day = by_date[date_str]
        print(f"\n{date_str} ({len(entries_day)} 条)")
        for entry in entries_day:
            prefix = "⭐" if entry.starred else "  "
            print(f"  {prefix} [{entry.category}] {entry.content[:80]}...")

    cm.close()
    return 0


def cmd_top(args):
    """热门记忆（v5.1.6 新增）"""
    cm = _get_memory(args)
    entries = cm.list(limit=99999)

    if not entries:
        print(c("⚠️  没有记忆", "yellow"))
        cm.close()
        return 0

    sort_key = {
        "access_count": lambda x: x.access_count,
        "strength": lambda x: x.strength,
        "created_at": lambda x: x.created_at,
    }.get(args.by, lambda x: x.access_count)

    top_entries = sorted(entries, key=sort_key, reverse=True)[:args.count]

    by_label = {"access_count": "访问次数", "strength": "记忆强度", "created_at": "创建时间"}
    label = by_label.get(args.by, "访问次数")

    print(c(f"\n🔥 热门记忆（按 {label} 排序，前 {args.count} 条）:", "cyan"))
    for idx, entry in enumerate(top_entries, 1):
        val = sort_key(entry)
        if args.by == "created_at":
            from datetime import datetime
            val_str = datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M")
        else:
            val_str = f"{val:.2f}" if isinstance(val, float) else str(val)
        prefix = "⭐" if entry.starred else "  "
        print(f"\n{idx}. {prefix} [{entry.category}] {label}: {c(val_str, 'yellow')}")
        print(f"   {entry.content[:100]}...")

    cm.close()
    return 0


def cmd_random(args):
    """随机闪卡复习（v5.1.7 新增）"""
    cm = _get_memory(args)
    entries = cm.random(count=args.count, category=getattr(args, 'category', None))

    if not entries:
        print(c("⚠️  没有找到记忆", "yellow"))
        cm.close()
        return 0

    print(c(f"\n🎲 随机记忆闪卡（共 {len(entries)} 张）:", "cyan"))
    for idx, entry in enumerate(entries, 1):
        from datetime import datetime
        created = datetime.fromtimestamp(entry.created_at).strftime("%Y-%m-%d")
        starred = "⭐" if entry.starred else "  "
        print(f"\n{'='*50}")
        print(f"  第 {idx} 张  {starred}  [{entry.category}] 强度: {entry.strength:.2f}")
        print(f"  创建: {created}  访问: {entry.access_count}次")
        print(f"{'='*50}")
        print(f"\n  {entry.content}")
        if entry.tags:
            print(f"\n  🏷️  标签: {', '.join(entry.tags)}")

    cm.close()
    return 0


def cmd_rename_tag(args):
    """重命名标签（v5.1.7 新增）"""
    cm = _get_memory(args)

    if not args.force:
        print(c(f"\n将标签 '{args.old}' 重命名为 '{args.new}'", "yellow"))
        print(c("此操作将更新所有包含该标签的记忆。", "yellow"))
        confirm = input("\n确认继续？(y/N): ").strip().lower()
        if confirm != 'y':
            print("已取消")
            cm.close()
            return 0

    count = cm.rename_tag(args.old, args.new)
    print(c(f"\n✅ 标签重命名成功，影响 {count} 条记忆", "green"))

    cm.close()
    return 0


def cmd_rename_cat(args):
    """重命名分类（v5.1.7 新增）"""
    cm = _get_memory(args)

    if not args.force:
        print(c(f"\n将分类 '{args.old}' 重命名为 '{args.new}'", "yellow"))
        print(c("此操作将移动所有该分类下的记忆。", "yellow"))
        confirm = input("\n确认继续？(y/N): ").strip().lower()
        if confirm != 'y':
            print("已取消")
            cm.close()
            return 0

    count = cm.rename_category(args.old, args.new)
    print(c(f"\n✅ 分类重命名成功，影响 {count} 条记忆", "green"))

    cm.close()
    return 0


def cmd_config(args):
    """查看配置（v5.1.7 新增）"""
    cm = _get_memory(args)
    cfg = cm.config_summary()

    print(c("\n⚙️  MindForge 配置信息:", "cyan"))
    print(f"  数据库路径: {cfg['db_path']}")
    print(f"  加密状态: {'开启 🔐' if cfg['encrypted'] else '未加密'}")
    print(f"  数据库大小: {cfg['db_size_mb']} MB")
    print(f"  版本: v{__version__}")

    stats = cm.stats()
    print("\n📊 记忆统计:")
    print(f"  总记忆数: {stats['total']}")
    print(f"  分类数: {len(stats.get('top_categories', {}))}")
    print(f"  收藏数: {stats.get('starred_count', 0)}")

    cm.close()
    return 0


def cmd_rekey(args):
    """更换加密密钥 / 升级 KDF 参数（v5.6.0 新增）"""
    import getpass

    # 检查是否启用加密
    from ..core.encryption import get_key_params, PBKDF2_ITERATIONS_CURRENT

    # 先拿到配置判断加密状态
    config = _build_config(args)
    if not config.encrypted:
        print(c("❌ 加密未启用，无法执行 rekey", "red"))
        return 1

    # 检查密钥文件是否存在
    key_params = get_key_params(config.key_file)
    if key_params is None:
        print(c("❌ 密钥文件不存在，请先初始化 MindForge", "red"))
        return 1

    # 显示当前状态
    print(c(f"\n{COLORS['bold']}🔐 加密密钥更换 / KDF 参数升级{COLORS['reset']}", "cyan"))
    print("=" * 55)
    print(f"  当前 KDF 算法:    {key_params.algorithm}")
    print(f"  当前迭代次数:    {key_params.iterations:,}")
    is_current = key_params.iterations == PBKDF2_ITERATIONS_CURRENT
    status = c("✅ 已是最新", "green") if is_current else c("⚠️  低于推荐值", "yellow")
    print(f"  状态:            {status}")
    print(f"  推荐迭代次数:    {PBKDF2_ITERATIONS_CURRENT:,} (OWASP 2023)")
    print()

    # 获取旧密码
    old_password = args.old_password
    if not old_password:
        old_password = getpass.getpass("请输入旧密码: ")
    if not old_password:
        print(c("❌ 旧密码不能为空", "red"))
        return 1

    # 获取新密码
    if args.upgrade_only:
        new_password = old_password
        print(c("ℹ️  --upgrade-only 模式：密码不变，仅升级 KDF 参数", "cyan"))
    else:
        new_password = args.new_password
        if not new_password:
            new_password = getpass.getpass("请输入新密码: ")
            confirm = getpass.getpass("请再次输入新密码: ")
            if new_password != confirm:
                print(c("❌ 两次输入的新密码不一致", "red"))
                return 1
        if not new_password:
            print(c("❌ 新密码不能为空", "red"))
            return 1

    # 确认提示
    if not args.yes:
        target_iters = args.iterations or PBKDF2_ITERATIONS_CURRENT
        print()
        print(c("⚠️  此操作将：", "yellow"))
        print(f"  1. 生成新的加密密钥（{target_iters:,} 次迭代）")
        print(f"  2. 重加密所有加密记忆（事务保障，失败自动回滚）")
        print(f"  3. 备份旧密钥文件到 .key.bak")
        print()
        print(c(f"{COLORS['bold']}建议操作前先备份数据库！{COLORS['reset']}", "yellow"))
        try:
            answer = input("确认继续? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1
        if answer not in ("y", "yes"):
            print("已取消")
            return 0

    # 执行 rekey
    print()
    print(c("🔄 正在验证旧密码...", "cyan"))

    # 通过环境变量传递密码给 _get_memory
    import os
    os.environ["MINDFORGE_PASSWORD"] = old_password

    try:
        cm = _get_memory(args)
    except SystemExit:
        print(c("❌ 旧密码验证失败", "red"))
        return 1
    except Exception as e:
        print(c(f"❌ 旧密码验证失败：{e}", "red"))
        return 1

    try:
        print(c("🔄 正在生成新密钥并重加密数据...", "cyan"))
        result = cm.rekey(
            old_password=old_password,
            new_password=new_password,
            new_iterations=args.iterations,
        )
        cm.close()
    except Exception as e:
        print(c(f"❌ rekey 失败：{e}", "red"))
        print(c("密钥文件已从 .key.bak 恢复，数据库回滚到旧密文，数据未受影响", "yellow"))
        return 1

    # 成功报告
    print()
    print(c(f"{COLORS['bold']}✅ rekey 完成！{COLORS['reset']}", "green"))
    print("=" * 55)
    print(f"  重加密记忆数:    {result['rekeyed_count']} 条")
    print(f"  旧迭代次数:      {result['old_iterations']:,}")
    print(f"  新迭代次数:      {result['new_iterations']:,}")
    improvement = (
        (result['new_iterations'] - result['old_iterations']) / result['old_iterations'] * 100
        if result['old_iterations'] > 0 else 0
    )
    print(f"  安全提升:        +{improvement:.0f}%")
    print(f"  密钥备份:        {result['backup_path']}")
    print()
    print(c("💡 建议：确认一切正常后可手动删除 .key.bak 备份文件", "cyan"))

    return 0


def cmd_backup(args):
    """创建记忆库完整备份（v5.6.0 新增）"""
    cm = _get_memory(args)

    print(c(f"\n💾 创建记忆库备份...", "cyan", ))

    try:
        result = cm.backup(output_path=args.output)
        cm.close()
    except Exception as e:
        print(c(f"❌ 备份失败：{e}", "red"))
        return 1

    if _json_mode:
        _json_out({
            "ok": True,
            "path": result["path"],
            "size_bytes": result["size_bytes"],
            "size_mb": result["size_mb"],
            "memory_count": result["memory_count"],
            "encrypted": result["encrypted"],
            "version": result["version"],
        })
        return 0

    print()
    print(c("✅ 备份完成！", "green"))
    print("=" * 50)
    print(f"  备份文件:    {result['path']}")
    print(f"  文件大小:    {result['size_mb']} MB ({result['size_bytes']:,} bytes)")
    print(f"  记忆数量:    {result['memory_count']} 条")
    print(f"  加密状态:    {'开启 🔐' if result['encrypted'] else '未加密'}")
    print(f"  程序版本:    v{result['version']}")
    print()

    if result["encrypted"]:
        print(c(f"{COLORS['bold']}⚠️  此备份包含密钥文件！{COLORS['reset']}", "red"))
        print(c("任何拿到此 ZIP 的人都能读取你的全部加密记忆。", "yellow"))
        print(c("请像保管密码一样保管此备份文件。", "yellow"))
        print()
    print(c("💡 建议：将备份文件存储到离线/异地位置，确保灾备安全", "cyan"))

    return 0


def cmd_backup_restore(args):
    """从备份文件恢复（v5.6.0 新增）"""
    from ..core.mindforge import MindForge
    from ..core.types import MemoryConfig

    backup_file = Path(args.backup_file)
    if not backup_file.exists():
        print(c(f"❌ 备份文件不存在: {backup_file}", "red"))
        return 1

    # 确定目标路径（与 _get_memory 保持一致的默认值逻辑）
    key_file_path = Path(args.key_file) if args.key_file else Path(args.db_path).parent / ".key"
    db_path = args.db_path
    key_file = args.key_file or str(key_file_path)

    # 先读取备份信息用于显示
    import zipfile
    try:
        with zipfile.ZipFile(backup_file, "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except Exception as e:
        print(c(f"❌ 无法读取备份文件：{e}", "red"))
        return 1

    # 显示备份信息
    print(c("\n📦 备份文件信息", "cyan"))
    print("=" * 50)
    print(f"  文件路径:    {backup_file}")
    print(f"  记忆数量:    {manifest.get('memory_count', '?')} 条")
    print(f"  加密状态:    {'开启 🔐' if manifest.get('encrypted') else '未加密'}")
    print(f"  程序版本:    v{manifest.get('mindforge_version', 'unknown')}")
    print(f"  备份时间:    {manifest.get('created_at_iso', 'unknown')}")
    print()
    print(c("恢复目标：", "cyan"))
    print(f"  数据库:      {db_path}")
    if manifest.get("encrypted"):
        print(f"  密钥文件:    {key_file}")
    print()

    # 警告：覆盖
    import os as _os
    target_db = Path(db_path)
    target_key = Path(key_file) if manifest.get("encrypted") else None

    if target_db.exists():
        print(c("⚠️  目标数据库文件已存在，将被覆盖！", "yellow"))
    if target_key and target_key.exists():
        print(c("⚠️  目标密钥文件已存在，将被覆盖！", "yellow"))
    if target_db.exists() or (target_key and target_key.exists()):
        print()

    # 确认提示
    if not args.yes:
        print(c("此操作将覆盖目标位置的现有数据，不可撤销！", "yellow"))
        try:
            answer = input("确认恢复? [输入 YES 继续]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1
        if answer != "YES":
            print("已取消")
            return 0

    # 执行恢复
    print()
    print(c("🔄 正在恢复...", "cyan"))

    try:
        result = MindForge.restore_backup(
            str(backup_file),
            db_path=db_path,
            key_file=key_file if manifest.get("encrypted") else None,
            force=True,
        )
    except Exception as e:
        print(c(f"❌ 恢复失败：{e}", "red"))
        return 1

    if _json_mode:
        _json_out({
            "ok": True,
            "restored_db": result["restored_db"],
            "restored_key": result["restored_key"],
            "memory_count": result["memory_count"],
            "encrypted": result["encrypted"],
            "backup_version": result["backup_version"],
            "mindforge_version": result["mindforge_version"],
        })
        return 0

    print()
    print(c("✅ 恢复完成！", "green"))
    print("=" * 50)
    print(f"  数据库路径:    {result['restored_db']}")
    if result["restored_key"]:
        print(f"  密钥文件:      {result['restored_key']}")
    print(f"  记忆数量:      {result['memory_count']} 条")
    print(f"  加密状态:      {'开启 🔐' if result['encrypted'] else '未加密'}")
    print(f"  备份版本:      v{result['mindforge_version']}")
    print()
    print(c("💡 建议：使用 'MindForge config' 或 'MindForge list' 验证恢复结果", "cyan"))

    return 0


def cmd_doctor(args):
    """全面诊断数据库（v5.1.8 新增）"""
    cm = _get_memory(args)
    print_banner()
    print(c("🔧 MindForge 全面诊断", "bold"))
    print("=" * 50)

    issues = []
    fixes_applied = 0

    # 1. 完整性检查
    print(c("\n[1/5] 数据库完整性...", "cyan"))
    health = cm.health_check()
    if health["integrity_check"] == "ok":
        print("  ✅ 完整性校验通过")
    else:
        print(f"  🚨 完整性校验失败: {health['integrity_check']}")
        issues.append("数据库完整性校验失败，可能存在损坏")

    # 2. FTS 孤立记录
    print(c("\n[2/5] FTS 索引一致性...", "cyan"))
    if health["fts_orphans"] == 0:
        print("  ✅ FTS 索引无孤立记录")
    else:
        print(f"  ⚠️  发现 {health['fts_orphans']} 条孤立 FTS 记录")
        if args.fix:
            cm.rebuild_fts()
            print("  ✅ 已重建 FTS 索引")
            fixes_applied += 1
        else:
            issues.append("存在孤立 FTS 记录，建议运行 vacuum 命令")

    # 3. 索引检查
    print(c("\n[3/5] 数据库索引...", "cyan"))
    idx = health["indexes"]
    if idx["found"] == idx["expected"]:
        print(f"  ✅ 索引完整 ({idx['found']}/{idx['expected']})")
    else:
        print(f"  ⚠️  索引缺失: {idx['found']}/{idx['expected']}")
        if idx["missing"]:
            print(f"     缺失: {', '.join(idx['missing'])}")
        issues.append("存在缺失索引，可能影响查询性能")

    # 4. 加密一致性
    print(c("\n[4/5] 加密一致性...", "cyan"))
    if health["encrypted_inconsistent"] == 0:
        print("  ✅ 加密状态一致")
    else:
        print(f"  🚨 {health['encrypted_inconsistent']} 条记忆加密状态异常")
        issues.append("存在加密不一致的记忆")

    # 5. 数据库版本
    print(c("\n[5/5] 数据库版本...", "cyan"))
    try:
        current_ver = cm.storage.get_db_version()
        latest_ver = cm.storage.get_latest_db_version()
        if current_ver >= latest_ver:
            print(f"  ✅ 版本最新 (v{current_ver})")
        else:
            print(f"  ⚠️  版本落后: v{current_ver} -> v{latest_ver}")
            if args.fix:
                cm.storage.migrate_to_latest()
                print("  ✅ 已迁移到最新版本")
                fixes_applied += 1
            else:
                issues.append("数据库版本落后，建议运行 migrate --force")
    except (sqlite3.OperationalError, ValueError) as e:
        print(f"  ⚠️  无法获取版本: {e}")

    # 汇总
    print(c("\n" + "=" * 50, "reset"))
    if not issues:
        print(c("🎉 诊断完成，未发现问题！", "green"))
    else:
        print(c(f"⚠️  发现 {len(issues)} 个问题:", "yellow"))
        for i, issue in enumerate(issues, 1):
            print(f"   {i}. {issue}")
        if args.fix:
            print(c(f"\n✅ 已自动修复 {fixes_applied} 个问题", "green"))
        else:
            print(c("\n💡 加 --fix 参数自动修复可修复的问题", "cyan"))

    cm.close()
    return 0 if not issues else 1


def cmd_find(args):
    """高级查找记忆（v5.1.8 新增）
    支持按分类、标签、层级、收藏、时间范围组合筛选
    """
    cm = _get_memory(args)

    entries = cm.list(limit=99999)
    results = []

    for entry in entries:
        if args.category and entry.category != args.category:
            continue
        if args.layer and entry.layer.value != args.layer:
            continue
        if args.starred and not entry.starred:
            continue
        if args.tags:
            entry_tags = set(entry.tags) if entry.tags else set()
            if not set(args.tags).issubset(entry_tags):
                continue
        if args.keyword and args.keyword.lower() not in entry.content.lower():
            continue
        if args.after and entry.created_at < args.after:
            continue
        if args.before and entry.created_at > args.before:
            continue
        results.append(entry)

    results.sort(key=lambda e: e.created_at, reverse=True)
    total = len(results)
    if args.limit:
        results = results[:args.limit]

    print(c("\n🔍 高级查找结果", "bold"))
    print(f"   匹配 {c(str(total), 'cyan')} 条记忆" + (f"（显示前 {len(results)} 条）" if args.limit and total > args.limit else ""))

    for entry in results:
        star = "⭐ " if entry.starred else ""
        print(f"\n{star}[{c(entry.id[:12], 'dim')}] {c(entry.category, 'green')} "
              f"[{entry.layer.value}]")
        print(f"   {entry.content[:200]}")
        if entry.tags:
            print(f"   标签: {', '.join('#' + t for t in entry.tags)}")
        print(f"   创建: {format_time(entry.created_at)} | 访问: {entry.access_count}")

    cm.close()
    return 0


def cmd_gc(args):
    """记忆衰减/垃圾回收（v5.6.0 新增）"""
    from modules.memory_decay import MemoryDecayEngine, DecayConfig, DecayPolicy

    cm = _get_memory(args)

    policy_map = {
        "conservative": DecayPolicy.CONSERVATIVE,
        "balanced": DecayPolicy.BALANCED,
        "aggressive": DecayPolicy.AGGRESSIVE,
    }
    policy = policy_map.get(args.policy, DecayPolicy.BALANCED)
    config = DecayConfig.from_policy(policy)

    # CLI 参数覆盖策略默认值
    if args.archive_threshold is not None:
        config.archive_threshold = args.archive_threshold
    if args.purge_after_days is not None:
        config.purge_after_days = args.purge_after_days

    engine = MemoryDecayEngine(storage=cm._storage, config=config)

    # --status: 只看统计
    if args.status:
        stats = engine.gc_stats()

        if _json_mode:
            _json_out(stats)
            cm.close()
            return 0

        print(c("\n📊 记忆衰减统计", "cyan"))
        print("=" * 50)
        print(f"  策略:          {stats['policy']}")
        print(f"  总记忆数:      {stats['total_memories']}")
        print(f"  活跃记忆:      {stats['active_memories']}")
        print(f"  受保护记忆:    {stats['protected_memories']}")
        print(f"  归档记忆:      {stats['archived_memories']}")
        print(f"  低于阈值:      {stats['below_threshold']} 条")
        print(f"  平均强度:      {stats['avg_strength']}")
        print(f"  归档阈值:      {config.archive_threshold}")
        print(f"  保留天数:      {config.purge_after_days}")
        print()
        print(c("层级分布:", "yellow"))
        for layer, count in stats.get("by_layer", {}).items():
            print(f"  {layer}: {count}")

        cm.close()
        return 0

    # 执行 GC
    dry = args.dry_run
    skip_purge = args.skip_purge

    if dry:
        print(c("\n🔍 预览 GC 结果（dry-run 模式，不会实际执行）", "cyan"))
    else:
        print(c("\n🗑️  执行记忆垃圾回收...", "cyan"))

    report = engine.run_gc(dry_run=dry, skip_purge=skip_purge, actor="cli")

    if _json_mode:
        _json_out(report)
        cm.close()
        return 0

    print("=" * 50)
    print(f"  策略:          {report['policy']}")
    print(f"  耗时:          {report['duration_ms']}ms")
    print()

    ds = report["decay_scores"]
    print(c("[1/4] 衰减评分", "yellow"))
    print(f"  扫描记忆:      {ds['scanned']}")
    print(f"  更新元数据:    {ds['updated']}")
    print(f"  低于阈值:      {ds['below_threshold']} 条")
    print(f"  平均强度:      {ds['avg_strength']}")
    print()

    tc = report["temporary_cleanup"]
    print(c("[2/4] 临时层时间清理", "yellow"))
    if dry:
        print(f"  感官层候选:    {tc.get('sensory_candidates', 0)}")
        print(f"  短期层候选:    {tc.get('short_term_candidates', 0)}")
    else:
        print(f"  感官层归档:    {tc.get('sensory_archived', 0)}")
        print(f"  短期层归档:    {tc.get('short_term_archived', 0)}")
    print()

    ad = report["archive_decayed"]
    print(c("[3/4] 强度归档", "yellow"))
    print(f"  归档候选:      {ad['candidates']}")
    print(f"  受保护跳过:    {ad['protected']}")
    if dry:
        print(f"  实际归档:      0 (dry-run)")
    else:
        print(f"  实际归档:      {ad['archived']}")
    print()

    pa = report["purge_old_archives"]
    print(c("[4/4] 过期归档清除", "yellow"))
    if pa.get("skipped"):
        print(f"  状态:          已跳过（--skip-purge）")
    elif dry:
        print(f"  待清除:        {pa.get('to_purge', 0)} 条")
    else:
        print(f"  已删除:        {pa.get('purged', 0)} 条")

    print()
    if dry:
        print(c("💡 这是预览结果。去掉 --dry-run 执行实际 GC。", "cyan"))
    else:
        total_archived = ad["archived"] + tc.get("sensory_archived", 0) + tc.get("short_term_archived", 0)
        total_purged = pa.get("purged", 0)
        if total_archived == 0 and total_purged == 0:
            print(c("✅ GC 完成，无需归档或删除的记忆。", "green"))
        else:
            print(c(f"✅ GC 完成！归档 {total_archived} 条，永久删除 {total_purged} 条。", "green"))
            if not skip_purge and total_purged > 0:
                print(c("💡 永久删除的记忆不可恢复。如需保守模式，使用 --skip-purge。", "cyan"))

    cm.close()
    return 0


def cmd_export_excel(args):
    """导出记忆为 Excel（v5.1.9 新增）"""
    cm = _get_memory(args)

    layer = MemoryLayer.from_string(args.layer) if args.layer else None

    try:
        output = _safe_export_path(args.output, "memory_export.xlsx", ".xlsx")
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1

    path = cm.export_excel(
        output_path=str(output),
        category=args.category,
        layer=layer,
        starred_only=getattr(args, 'starred', False),
    )

    size = path.stat().st_size
    print(c("\n✅ Excel 导出成功", "green"))
    print(f"   文件路径: {path}")
    print(f"   文件大小: {format_size(size)}")
    cm.close()
    return 0


def cmd_import_excel(args):
    """从 Excel 导入记忆（v5.1.9 新增）"""
    cm = _get_memory(args)

    try:
        input_path = _safe_import_path(args.input)
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1

    if not input_path.exists():
        print(c(f"\n❌ 文件不存在: {input_path}", "red"))
        return 1

    layer = MemoryLayer.from_string(args.layer) if args.layer else None

    stats = cm.import_excel(
        input_path=str(input_path),
        target_category=args.category,
        target_layer=layer,
    )

    print(c("\n✅ Excel 导入完成", "green"))
    print(f"   成功导入：{c(str(stats['imported']), 'green')} 条")
    print(f"   跳过重复：{c(str(stats['skipped']), 'yellow')} 条")
    print(f"   导入失败：{c(str(stats['failed']), 'red')} 条")
    cm.close()
    return 0


def cmd_copy(args):
    """复制记忆到新分类（v5.1.9 新增）"""
    cm = _get_memory(args)

    entry = cm.get(args.id)
    if not entry:
        print(c("❌ 记忆不存在", "red"))
        return 1

    print(c(f"\n将记忆复制到新分类 '{args.category}'", "yellow"))
    print(f"   原记忆: [{entry.category}] {entry.preview[:60]}...")

    success = cm.copy(
        memory_id=args.id,
        new_category=args.category,
        actor=args.agent,
        session_id=args.session,
    )

    if success:
        print(c("\n✅ 复制成功", "green"))
    else:
        print(c("\n❌ 复制失败", "red"))

    cm.close()
    return 0 if success else 1


def cmd_move(args):
    """移动记忆到新分类（v5.1.9 新增）"""
    cm = _get_memory(args)

    entry = cm.get(args.id)
    if not entry:
        print(c("❌ 记忆不存在", "red"))
        return 1

    if entry.category == "trash":
        print(c("❌ 无法移动回收站中的记忆，请先恢复", "red"))
        return 1

    print(c(f"\n将记忆从 '{entry.category}' 移动到 '{args.category}'", "yellow"))
    print(f"   记忆: {entry.preview[:60]}...")

    success = cm.move(
        memory_id=args.id,
        new_category=args.category,
        actor=args.agent,
        session_id=args.session,
    )

    if success:
        print(c("\n✅ 移动成功", "green"))
    else:
        print(c("\n❌ 移动失败", "red"))

    cm.close()
    return 0 if success else 1


def cmd_fuzzy_search(args):
    """模糊搜索记忆（v5.2.0 新增）"""
    cm = _get_memory(args)

    layer = MemoryLayer.from_string(args.layer) if args.layer else None

    results = cm.fuzzy_search(
        query=args.query,
        category=args.category,
        layer=layer,
        limit=args.limit,
        threshold=args.threshold,
    )

    print(c(f"\n🔍 模糊搜索: \"{args.query}\"", "bold"))
    print(c(f"   找到 {len(results)} 条结果（阈值 {args.threshold}）\n", "dim"))

    if not results:
        print(c("   没有找到匹配的记忆", "yellow"))
        cm.close()
        return 0

    for i, result in enumerate(results, 1):
        entry = result["entry"]
        score = result["score"]

        content = entry.preview
        if args.highlight:
            content = cm.highlight(content, args.query, c("", "yellow"), c("", "reset"))

        print(f" {i}. [{entry.id[:12]}] {content[:80]}...")
        print(f"    分类: {entry.category} | 标签: {', '.join(entry.tags) if entry.tags else '无'}")
        print(f"    相关度: {score:.2f} | 访问: {entry.access_count}次 | 强度: {entry.strength:.2f}")
        print()

    cm.close()
    return 0


def cmd_search_history(args):
    """查看搜索历史（v5.2.0 新增）"""
    cm = _get_memory(args)

    history = cm.search_history(limit=args.limit)

    print(c(f"\n📜 搜索历史（最近 {len(history)} 条）\n", "bold"))

    if not history:
        print(c("   暂无搜索历史", "yellow"))
        cm.close()
        return 0

    for i, item in enumerate(history, 1):
        from datetime import datetime
        last_used = datetime.fromtimestamp(item["last_used"]).strftime("%Y-%m-%d %H:%M")
        print(f" {i}. \"{item['query']}\"  ({item['count']}次, 最近: {last_used})")

    print()
    cm.close()
    return 0


def cmd_batch_add_tags(args):
    """批量添加标签（v5.2.0 新增）"""
    cm = _get_memory(args)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    if args.category:
        count = cm.add_tags_by_category(
            category=args.category,
            tags=tags,
            actor=args.agent,
            session_id=args.session,
        )
        print(c(f"\n✅ 已为分类 '{args.category}' 的 {count} 条记忆添加标签: {', '.join(tags)}", "green"))
    elif args.ids:
        entry_ids = [i.strip() for i in args.ids.split(",") if i.strip()]
        count = cm.batch_add_tags(
            entry_ids=entry_ids,
            tags=tags,
            actor=args.agent,
            session_id=args.session,
        )
        print(c(f"\n✅ 已为 {count} 条记忆添加标签: {', '.join(tags)}", "green"))
    else:
        print(c("❌ 请指定 --ids 或 --category 参数", "red"))
        cm.close()
        return 1

    cm.close()
    return 0


def cmd_batch_remove_tags(args):
    """批量移除标签（v5.2.0 新增）"""
    cm = _get_memory(args)

    if not args.ids:
        print(c("❌ 请指定 --ids 参数（记忆 ID 列表，逗号分隔）", "red"))
        cm.close()
        return 1

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    entry_ids = [i.strip() for i in args.ids.split(",") if i.strip()]

    count = cm.batch_remove_tags(
        entry_ids=entry_ids,
        tags=tags,
        actor=args.agent,
        session_id=args.session,
    )

    print(c(f"\n✅ 已从 {count} 条记忆中移除标签: {', '.join(tags)}", "green"))
    cm.close()
    return 0


def cmd_merge_tags(args):
    """合并标签（v5.2.0 新增）"""
    cm = _get_memory(args)

    source_tags = [t.strip() for t in args.source.split(",") if t.strip()]

    print(c("\n合并标签确认:", "yellow"))
    print(f"   源标签: {', '.join(source_tags)}")
    print(f"   目标标签: {args.target}")

    if not args.force:
        answer = input("\n确认合并？(y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            print(c("已取消", "yellow"))
            cm.close()
            return 0

    count = cm.merge_tags(
        source_tags=source_tags,
        target_tag=args.target,
        actor=args.agent,
        session_id=args.session,
    )

    print(c(f"\n✅ 合并完成，{count} 条记忆受影响", "green"))
    cm.close()
    return 0


def cmd_db_backup(args):
    """创建数据库备份（v5.2.0 新增）"""
    cm = _get_memory(args)

    print(c("\n💾 创建数据库备份...", "yellow"))

    try:
        backup_dir = _validate_path(args.dir or "./data/backups", allow_symlinks=False)
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1

    result = cm.create_backup(backup_dir=str(backup_dir))

    if result["success"]:
        print(c("\n✅ 备份成功", "green"))
        print(f"   文件: {result['filename']}")
        print(f"   路径: {result['path']}")
        print(f"   大小: {result['size_mb']} MB")
        print(f"   时间: {result['timestamp']}")
    else:
        print(c(f"\n❌ 备份失败: {result.get('error', '未知错误')}", "red"))
        cm.close()
        return 1

    cm.close()
    return 0


def cmd_db_backups(args):
    """列出备份文件（v5.2.0 新增）"""
    cm = _get_memory(args)

    backups = cm.list_backups(backup_dir=args.dir)

    print(c(f"\n📦 备份列表（共 {len(backups)} 个）\n", "bold"))

    if not backups:
        print(c("   暂无备份", "yellow"))
        cm.close()
        return 0

    for i, backup in enumerate(backups, 1):
        from datetime import datetime
        created = datetime.fromtimestamp(backup["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        print(f" {i}. {backup['filename']}")
        print(f"    大小: {backup['size_mb']} MB | 创建时间: {created}")

    print()
    cm.close()
    return 0


def cmd_db_restore(args):
    """从备份恢复（v5.2.0 新增）"""
    cm = _get_memory(args)

    # 路径安全校验（v5.2.7 新增：防止路径遍历 + 符号链接攻击）
    try:
        backup_file = _validate_path(args.backup, must_exist=True, allow_symlinks=False)
    except ValueError as e:
        print(c(f"❌ 路径校验失败: {e}", "red"))
        cm.close()
        return 1

    # SQLite 文件签名校验（v5.2.7 新增：防止恢复非数据库文件导致损坏）
    try:
        with open(backup_file, 'rb') as f:
            header = f.read(16)
    except (OSError, IOError) as e:
        print(c(f"❌ 读取备份文件失败: {e}", "red"))
        cm.close()
        return 1
    if not header.startswith(b'SQLite format 3'):
        print(c("❌ 文件不是有效的 SQLite 数据库", "red"))
        cm.close()
        return 1

    print(c("\n⚠️  恢复备份警告:", "yellow"))
    print(f"   备份文件: {backup_file}")
    print(f"   恢复前自动备份: {'否' if args.no_pre_backup else '是'}")
    print(c("\n   此操作将覆盖当前数据库！", "red"))

    if not args.force:
        answer = input("\n确认恢复？(y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            print(c("已取消", "yellow"))
            cm.close()
            return 0

    result = cm.restore_backup(
        backup_path=str(backup_file),
        create_backup_before=not args.no_pre_backup,
    )

    if result["success"]:
        print(c("\n✅ 恢复成功", "green"))
        if result.get("backup_created"):
            print(f"   恢复前备份: {result['backup_created']}")
    else:
        print(c(f"\n❌ 恢复失败: {result.get('error', '未知错误')}", "red"))
        cm.close()
        return 1

    cm.close()
    return 0


def cmd_db_clean_backups(args):
    """清理旧备份（v5.2.0 新增）"""
    cm = _get_memory(args)

    backups = cm.list_backups(backup_dir=args.dir)
    will_delete = max(0, len(backups) - args.keep)

    print(c("\n🧹 清理旧备份", "yellow"))
    print(f"   当前备份数: {len(backups)}")
    print(f"   保留数量: {args.keep}")
    print(f"   将删除: {will_delete} 个")

    if not args.force and will_delete > 0:
        answer = input("\n确认删除？(y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            print(c("已取消", "yellow"))
            cm.close()
            return 0

    deleted = cm.delete_old_backups(
        backup_dir=args.dir,
        keep_count=args.keep,
    )

    print(c(f"\n✅ 已删除 {deleted} 个旧备份", "green"))
    cm.close()
    return 0


# ===== AI 短剧记忆模块（v5.2.1 新增）=====

def cmd_drama_add(args):
    """添加短剧（v5.2.1 新增）"""
    cm = _get_memory(args)
    drama = cm.add_drama(
        title=args.title,
        genre=args.genre,
        total_episodes=args.episodes,
        status=args.status,
        platform=args.platform,
        rating=args.rating,
        description=args.description,
        tags=args.tags,
        cover_url=args.cover,
    )
    print(c("\n✅ 短剧已添加", "green"))
    print(f"   ID: {drama.id}")
    print(f"   标题: {drama.title}")
    print(f"   类型: {drama.genre.value}")
    print(f"   集数: {drama.total_episodes}")
    print(f"   状态: {drama.status.value}")
    cm.close()
    return 0


def cmd_drama_list(args):
    """列出短剧（v5.2.1 新增）"""
    cm = _get_memory(args)
    dramas = cm.list_dramas(
        genre=args.genre,
        status=args.status,
        platform=args.platform,
        min_rating=args.min_rating,
        limit=args.limit,
        offset=args.offset,
        sort_by=args.sort,
        sort_order=args.order,
    )
    print(f"\n找到 {c(str(len(dramas)), 'cyan')} 部短剧")
    for i, d in enumerate(dramas, 1):
        status_color = {
            "watching": "green", "completed": "cyan",
            "planned": "yellow", "dropped": "red"
        }.get(d.status.value, "reset")
        progress = f"{d.current_episode}/{d.total_episodes}" if d.total_episodes else f"{d.current_episode}/?"
        star = "⭐" if d.rating >= 8 else ""
        print(f"\n{i}. {star} {c(d.title, 'bold')} [{c(d.status.value, status_color)}]")
        print(f"   类型: {d.genre.value} | 进度: {progress} | 评分: {d.rating}")
        if d.platform:
            print(f"   平台: {d.platform}")
        if d.description:
            print(f"   简介: {d.description[:80]}...")
    cm.close()
    return 0


def cmd_drama_get(args):
    """获取短剧详情（v5.2.1 新增）"""
    cm = _get_memory(args)
    drama = cm.get_drama(args.id)
    if not drama:
        print(c("❌ 短剧不存在", "red"))
        cm.close()
        return 1

    print(c("\n📺 短剧详情", "bold"))
    print("=" * 50)
    print(f"标题:   {drama.title}")
    print(f"ID:     {drama.id}")
    print(f"类型:   {drama.genre.value}")
    print(f"状态:   {drama.status.value}")
    print(f"集数:   {drama.current_episode}/{drama.total_episodes}")
    print(f"评分:   {drama.rating}")
    if drama.platform:
        print(f"平台:   {drama.platform}")
    if drama.tags:
        print(f"标签:   {', '.join(drama.tags)}")
    if drama.description:
        print(f"简介:   {drama.description}")
    if drama.last_watched_at:
        from datetime import datetime
        print(f"上次观看: {datetime.fromtimestamp(drama.last_watched_at).strftime('%Y-%m-%d %H:%M')}")

    scenes = cm.list_scenes(drama_id=drama.id, limit=5)
    if scenes:
        print(f"\n场次:   共 {len(scenes)} 场（显示前5场）")
        for s in scenes[:5]:
            print(f"   EP{s.episode}-{s.scene_number}: {s.title}")

    chars = cm.list_characters(drama_id=drama.id, limit=5)
    if chars:
        print(f"\n角色:   共 {len(chars)} 个（显示前5个）")
        for ch in chars[:5]:
            print(f"   {ch.name} ({ch.role})")

    lines = cm.classic_lines(drama_id=drama.id, limit=3)
    if lines:
        print("\n经典台词:")
        for l in lines[:3]:
            char = l.character_name or "未知"
            print(f'   "{l.line_text[:80]}" — {char}')

    cm.close()
    return 0


def cmd_drama_update(args):
    """更新短剧（v5.2.1 新增）"""
    cm = _get_memory(args)
    success = cm.update_drama(
        drama_id=args.id,
        title=args.title,
        genre=args.genre,
        total_episodes=args.episodes,
        current_episode=args.current,
        status=args.status,
        platform=args.platform,
        rating=args.rating,
        description=args.description,
        tags=args.tags,
        cover_url=args.cover,
        mark_watched=args.watched,
    )
    if success:
        print(c("\n✅ 短剧已更新", "green"))
    else:
        print(c("\n❌ 更新失败", "red"))
    cm.close()
    return 0 if success else 1


def cmd_drama_delete(args):
    """删除短剧（v5.2.1 新增）"""
    cm = _get_memory(args)
    drama = cm.get_drama(args.id)
    if not drama:
        print(c("❌ 短剧不存在", "red"))
        cm.close()
        return 1

    if not args.force:
        print(c(f"\n⚠️  将删除短剧：{drama.title}", "yellow"))
        print(c("   同时删除所有场次、角色、台词", "yellow"))
        answer = input("\n确认删除？(y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            print(c("已取消", "yellow"))
            cm.close()
            return 0

    success = cm.delete_drama(args.id)
    if success:
        print(c("\n🗑️  短剧已删除", "green"))
    else:
        print(c("\n❌ 删除失败", "red"))
    cm.close()
    return 0 if success else 1


def cmd_drama_stats(args):
    """短剧统计（v5.2.1 新增）"""
    cm = _get_memory(args)
    stats = cm.drama_stats()
    print(c("\n📊 短剧统计", "bold"))
    print("=" * 40)
    print(f"短剧总数:   {stats['total']}")
    print(f"在看:       {stats['watching']}")
    print(f"已看完:     {stats['completed']}")
    print(f"台词总数:   {stats['total_lines']}")
    print(f"经典台词:   {stats['classic_lines']}")
    if stats['by_genre']:
        print("\n按类型:")
        for genre, cnt in stats['by_genre'].items():
            print(f"  {genre}: {cnt}")
    if stats['by_status']:
        print("\n按状态:")
        for status, cnt in stats['by_status'].items():
            print(f"  {status}: {cnt}")
    cm.close()
    return 0


def cmd_drama_recommend(args):
    """AI 智能推荐短剧（v5.2.2 新增）"""
    cm = _get_memory(args)
    recs = cm.recommend_dramas(
        genre=args.genre,
        min_rating=args.min_rating,
        exclude_ids=args.exclude,
        limit=args.limit,
    )
    print(c(f"\n🤖 AI 智能推荐（{len(recs)} 部）", "cyan"))
    print("=" * 50)
    if not recs:
        print(c("   没有符合条件的短剧", "yellow"))
        print("   提示：可以降低评分阈值或添加更多短剧")
    else:
        for i, d in enumerate(recs, 1):
            star = "⭐" if d.rating >= 8 else ""
            print(f"\n{i}. {star} {c(d.title, 'bold')} [{d.genre.value}]")
            print(f"   评分: {d.rating} | 类型: {d.genre.value}")
            if d.platform:
                print(f"   平台: {d.platform}")
            if d.tags:
                print(f"   标签: {', '.join(d.tags[:5])}")
            if d.description:
                print(f"   简介: {d.description[:60]}...")
    cm.close()
    return 0


def cmd_drama_progress(args):
    """观看进度统计（v5.2.2 新增）"""
    cm = _get_memory(args)
    progress = cm.drama_watching_progress()
    print(c("\n📈 观看进度统计", "bold"))
    print("=" * 50)
    print(f"短剧总数:   {progress['total_dramas']}")
    print(f"规划总集数: {progress['total_planned_episodes']}")
    print(f"已看总集数: {progress['total_watched_episodes']}")
    rate = progress['completion_rate']
    rate_color = "green" if rate >= 80 else "yellow" if rate >= 50 else "red"
    print(f"完成度:     {c(f'{rate}%', rate_color)}")

    if progress['by_genre']:
        print("\n按类型分布:")
        for genre, data in progress['by_genre'].items():
            genre_rate = 0.0
            if data['total_planned'] > 0:
                genre_rate = data['total_watched'] / data['total_planned'] * 100
            print(f"  {genre}: {data['count']} 部 | 进度 {data['total_watched']}/{data['total_planned']} ({genre_rate:.1f}%)")
    cm.close()
    return 0


def cmd_drama_export(args):
    """导出短剧数据（v5.2.2 新增）"""
    cm = _get_memory(args)
    try:
        count = cm.export_dramas(
            output_path=args.output,
            drama_ids=args.ids,
        )
        print(c("\n✅ 短剧数据已导出", "green"))
        print(f"   文件: {args.output}")
        print(f"   数量: {count} 部")
    except (OSError, ValueError) as e:
        print(c(f"\n❌ 导出失败: {e}", "red"))
        cm.close()
        return 1
    cm.close()
    return 0


# ===== v5.2.9 AI 短剧增强命令 =====

def cmd_drama_import(args):
    """从 JSON 批量导入短剧（v5.2.9 新增）"""
    cm = _get_memory(args)
    print(c("\n📥 批量导入短剧", "bold"))
    print("=" * 60)
    print(f"  输入文件:    {args.input}")
    print(f"  已存在处理:  {'覆盖（不跳过）' if args.overwrite else '跳过'}")

    try:
        # v5.2.9 安全加固：导入前做路径白名单校验
        _validate_path(args.input, must_exist=True, allow_symlinks=False,
                       max_size=500 * 1024 * 1024, allowed_exts={".json"})
        stats = cm.import_dramas(
            input_path=args.input,
            skip_existing=not args.overwrite,
        )
    except (OSError, ValueError) as e:
        print(c(f"\n❌ 导入失败: {e}", "red"))
        cm.close()
        return 1

    print(f"\n  📚 导入短剧:     {c(str(stats.get('dramas', 0)), 'green')} 部")
    print(f"  🎬 导入场次:     {c(str(stats.get('scenes', 0)), 'green')} 场")
    print(f"  🧑 导入角色:     {c(str(stats.get('characters', 0)), 'green')} 人")
    print(f"  💬 导入台词:     {c(str(stats.get('lines', 0)), 'green')} 条")
    if stats.get("skipped"):
        print(f"  ⏭️  跳过已存在:   {stats['skipped']} 部")
    if stats.get("failed"):
        print(f"  ❌ 导入失败:     {c(str(stats['failed']), 'yellow')} 项")
    cm.close()
    return 0


def cmd_drama_stars(args):
    """高分短剧排行榜（v5.2.9 新增，别名 drama-stars）"""
    cm = _get_memory(args)
    print(c("\n⭐ 高分短剧排行榜", "bold"))
    print("=" * 60)
    print(f"  类型过滤:   {args.genre or '全部'}")
    print(f"  最低评分:   ≥ {args.min_rating}")
    print(f"  Limit:      {args.limit}")

    dramas = cm.drama_stars(
        genre=args.genre,
        min_rating=args.min_rating,
        limit=args.limit,
    )

    if not dramas:
        print(c("\nℹ️  暂无符合条件的短剧", "yellow"))
        cm.close()
        return 0

    print(f"\n共 {c(str(len(dramas)), 'cyan')} 部：")
    for i, d in enumerate(dramas, 1):
        rating_color = "yellow"
        if d.rating >= 9:
            rating_color = "green"
        elif d.rating >= 7:
            rating_color = "cyan"
        title = (d.title or "(无标题)")[:50]
        status = d.status.value if hasattr(d.status, "value") else str(d.status)
        genre = d.genre.value if hasattr(d.genre, "value") else str(d.genre)
        print(f"  {i:>3}. [{c(str(round(d.rating, 1)) + '★', rating_color)}] "
              f"{genre:<10} {status:<10} {c(title, 'bold')}")

    cm.close()
    return 0


def cmd_scene_list_lines(args):
    """按场次列出台词（v5.2.9 新增）"""
    cm = _get_memory(args)
    print(c("\n🎬 场次台词", "bold"))
    print("=" * 60)
    print(f"  场次 ID:  {args.scene_id}")
    print(f"  Limit:    {args.limit}")
    print(f"  Offset:   {args.offset}")

    lines = cm.scene_lines(
        scene_id=args.scene_id,
        limit=args.limit,
        offset=args.offset,
    )

    if not lines:
        print(c("\nℹ️  该场次暂无台词", "yellow"))
        cm.close()
        return 0

    print(f"\n共 {c(str(len(lines)), 'cyan')} 条：")
    for i, l in enumerate(lines, 1):
        char = (l.character_name or "未知")[:12]
        classic = "⭐" if l.is_classic else "  "
        ep = f"EP{l.episode}" if l.episode else ""
        text = l.line_text.replace("\n", " ")[:90]
        print(f"  {i:>3}. {classic} {ep:<5} {char:<12}: {text}")

    cm.close()
    return 0


def cmd_char_list_lines(args):
    """按角色列出台词（v5.2.9 新增）"""
    cm = _get_memory(args)
    print(c("\n🧑 角色台词", "bold"))
    print("=" * 60)
    print(f"  角色 ID:    {args.char_id}")
    print(f"  限定短剧:   {args.drama_id or '全部'}")
    print(f"  Limit:      {args.limit}")
    print(f"  Offset:     {args.offset}")

    lines = cm.character_lines(
        character_id=args.char_id,
        drama_id=args.drama_id,
        limit=args.limit,
        offset=args.offset,
    )

    if not lines:
        print(c("\nℹ️  该角色暂无台词", "yellow"))
        cm.close()
        return 0

    print(f"\n共 {c(str(len(lines)), 'cyan')} 条：")
    for i, l in enumerate(lines, 1):
        char = (l.character_name or "未知")[:12]
        classic = "⭐" if l.is_classic else "  "
        ep = f"EP{l.episode}" if l.episode else ""
        drm = (l.drama_id or "")[:8]
        text = l.line_text.replace("\n", " ")[:80]
        print(f"  {i:>3}. {classic} {drm:<8} {ep:<5} {char:<12}: {text}")

    cm.close()
    return 0


# ===== v5.3.0 AI 短剧增强命令 =====

def cmd_drama_info(args):
    """短剧深度统计（v5.3.0 新增）"""
    cm = _get_memory(args)
    print(c("\n📊 短剧深度统计", "bold"))
    print("=" * 60)

    info = cm.drama_info(drama_id=args.drama_id)

    if not info:
        print(c(f"\n❌ 短剧不存在: {args.drama_id}", "red"))
        cm.close()
        return 1

    print(f"  标题:       {c(info['title'], 'bold')}")
    print(f"  类型:       {info['genre']}")
    print(f"  状态:       {info['status']}")
    print(f"  评分:       {c(str(round(info['rating'], 1)) + '★', 'yellow')}")
    print(f"  总集数:     {info['total_episodes']}  |  当前: {info['current_episode']}")
    print("\n  📋 内容统计:")
    print(f"     场次数:     {info['scene_count']}")
    print(f"     角色数:     {info['character_count']}")
    print(f"     台词数:     {c(str(info['line_count']), 'cyan')}")
    print(f"     经典台词:   {info['classic_line_count']} ({info['classic_ratio']}%)")
    print(f"     总字数:     {info['total_text_chars']}")
    print(f"     平均台词:   {info['avg_line_length']} 字/条")

    if info.get("episode_distribution"):
        print("\n  📺 每集台词分布:")
        for ep, cnt in info["episode_distribution"].items():
            bar = "█" * min(cnt, 40)
            print(f"     EP{ep}: {bar} {cnt}")

    if info.get("top_characters_by_lines"):
        print("\n  🧑 台词最多角色 Top-5:")
        for i, ch in enumerate(info["top_characters_by_lines"], 1):
            print(f"     {i}. {ch['name']}: {ch['line_count']} 条")

    cm.close()
    return 0


def cmd_line_random(args):
    """随机抽取台词（v5.3.0 新增）"""
    cm = _get_memory(args)
    print(c("\n🎲 随机台词", "bold"))
    print("=" * 60)
    if args.drama_id:
        print(f"  限定短剧:  {args.drama_id}")
    if args.char_id:
        print(f"  限定角色:  {args.char_id}")
    if args.classic:
        print("  仅经典:    是")
    print(f"  抽取数量:  {args.count}")

    lines = cm.random_lines(
        drama_id=args.drama_id,
        character_id=args.char_id,
        is_classic=args.classic if args.classic else None,
        count=args.count,
    )

    if not lines:
        print(c("\nℹ️  没有符合条件的台词", "yellow"))
        cm.close()
        return 0

    print(f"\n抽取到 {c(str(len(lines)), 'cyan')} 条：")
    for i, l in enumerate(lines, 1):
        char = l.character_name or "未知"
        classic = "⭐" if l.is_classic else "  "
        ep = f"EP{l.episode}" if l.episode else ""
        print(f"\n  {i}. {classic} {ep} {c(char, 'cyan')}:")
        print(f'     "{l.line_text}"')

    cm.close()
    return 0


def cmd_char_profile(args):
    """角色画像分析（v5.3.0 新增）"""
    cm = _get_memory(args)
    print(c("\n🧑 角色画像", "bold"))
    print("=" * 60)

    profile = cm.character_profile(
        character_id=args.char_id,
        drama_id=args.drama_id,
    )

    if not profile:
        print(c(f"\n❌ 角色不存在: {args.char_id}", "red"))
        cm.close()
        return 1

    print(f"  角色名:       {c(profile['name'], 'bold')}")
    print(f"  角色 ID:      {profile['character_id']}")
    if profile.get("drama_id"):
        print(f"  限定短剧:     {profile['drama_id']}")
    print("\n  📋 台词统计:")
    print(f"     总台词:     {c(str(profile['total_lines']), 'cyan')}")
    print(f"     经典台词:   {profile['classic_lines']} ({profile['classic_ratio']}%)")
    print(f"     总字数:     {profile['total_text_chars']}")
    print(f"     平均长度:   {profile['avg_line_length']} 字/条")
    print("\n  🎬 出场统计:")
    print(f"     场次:       {profile['scene_appearances']}")
    print(f"     短剧数:     {profile['drama_appearances']}")

    if profile.get("drama_ids"):
        print(f"     出场短剧:   {', '.join(profile['drama_ids'][:5])}")

    if profile.get("longest_line"):
        print("\n  💬 代表性台词（最长）:")
        print(f'     "{profile["longest_line"]}"')

    cm.close()
    return 0


def cmd_line_add(args):
    """添加短剧台词（v5.2.1 新增）"""
    cm = _get_memory(args)
    line = cm.add_line(
        drama_id=args.drama_id,
        line_text=args.line,
        scene_id=args.scene_id,
        character_id=args.char_id,
        character_name=args.character,
        context=args.context,
        episode=args.episode,
        timestamp=args.timestamp,
        is_classic=args.classic,
        tags=args.tags,
    )
    print(c("\n✅ 台词已添加", "green"))
    print(f"   ID: {line.id}")
    char = line.character_name or "未知"
    print(f'   "{line.line_text}" — {char}')
    if line.is_classic:
        print("   ⭐ 经典台词")
    cm.close()
    return 0


def cmd_line_list(args):
    """列出短剧台词（v5.2.1 新增）"""
    cm = _get_memory(args)
    lines = cm.list_lines(
        drama_id=args.drama_id,
        scene_id=args.scene_id,
        character_id=args.char_id,
        is_classic=args.classic,
        episode=args.episode,
        limit=args.limit,
        offset=args.offset,
    )
    label = "经典台词" if args.classic else "台词"
    print(f"\n找到 {c(str(len(lines)), 'cyan')} 条{label}")
    for i, l in enumerate(lines, 1):
        char = l.character_name or "未知"
        classic = "⭐" if l.is_classic else "  "
        ep = f"EP{l.episode}" if l.episode else ""
        print(f"\n{i}. {classic} {ep} {char}:")
        print(f'   "{l.line_text}"')
        if l.context:
            print(f"   背景: {l.context[:60]}")
    cm.close()
    return 0


def cmd_line_search(args):
    """搜索台词（v5.2.1 新增）"""
    cm = _get_memory(args)
    lines = cm.search_lines(
        query=args.query,
        drama_id=args.drama_id,
        is_classic_only=args.classic_only,
        limit=args.limit,
    )
    print(f"\n找到 {c(str(len(lines)), 'cyan')} 条匹配台词")
    for i, l in enumerate(lines, 1):
        char = l.character_name or "未知"
        classic = "⭐" if l.is_classic else "  "
        highlighted = cm.highlight(l.line_text, args.query)
        print(f"\n{i}. {classic} {char}:")
        print(f'   "{highlighted}"')
    cm.close()
    return 0


def cmd_line_classic(args):
    """经典台词（v5.2.1 新增）"""
    cm = _get_memory(args)
    lines = cm.classic_lines(
        drama_id=args.drama_id,
        limit=args.limit,
    )
    print(c(f"\n⭐ 经典台词（共 {len(lines)} 条）", "yellow"))
    for i, l in enumerate(lines, 1):
        char = l.character_name or "未知"
        print(f'\n{i}. "{l.line_text}"')
        print(f"   —— {char}")
    cm.close()
    return 0


def cmd_line_update(args):
    """更新台词（v5.2.1 新增）"""
    cm = _get_memory(args)
    success = cm.update_line(
        line_id=args.id,
        line_text=args.line,
        character_name=args.character,
        context=args.context,
        is_classic=args.classic,
        tags=args.tags,
    )
    if success:
        print(c("\n✅ 台词已更新", "green"))
    else:
        print(c("\n❌ 更新失败", "red"))
    cm.close()
    return 0 if success else 1


def cmd_line_delete(args):
    """删除台词（v5.2.1 新增）"""
    cm = _get_memory(args)
    line = cm.get_line(args.id)
    if not line:
        print(c("❌ 台词不存在", "red"))
        cm.close()
        return 1

    if not args.force:
        print(c(f'\n⚠️  将删除台词："{line.line_text[:50]}..."', "yellow"))
        answer = input("\n确认删除？(y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            print(c("已取消", "yellow"))
            cm.close()
            return 0

    success = cm.delete_line(args.id)
    if success:
        print(c("\n🗑️  台词已删除", "green"))
    else:
        print(c("\n❌ 删除失败", "red"))
    cm.close()
    return 0 if success else 1


def cmd_char_add(args):
    """添加短剧角色（v5.2.1 新增）"""
    cm = _get_memory(args)
    char = cm.add_character(
        drama_id=args.drama_id,
        name=args.name,
        role=args.role,
        actor=args.actor,
        description=args.description,
        personality=args.personality,
        avatar_url=args.avatar,
        tags=args.tags,
    )
    print(c("\n✅ 角色已添加", "green"))
    print(f"   ID: {char.id}")
    print(f"   姓名: {char.name}")
    print(f"   定位: {char.role}")
    if char.actor:
        print(f"   演员: {char.actor}")
    cm.close()
    return 0


def cmd_char_list(args):
    """列出短剧角色（v5.2.1 新增）"""
    cm = _get_memory(args)
    chars = cm.list_characters(
        drama_id=args.drama_id,
        role=args.role,
        limit=args.limit,
        offset=args.offset,
    )
    print(f"\n找到 {c(str(len(chars)), 'cyan')} 个角色")
    for i, ch in enumerate(chars, 1):
        role_color = {"lead": "yellow", "supporting": "cyan", "villain": "red"}.get(ch.role, "reset")
        actor = f" ({ch.actor})" if ch.actor else ""
        print(f"\n{i}. {ch.name} [{c(ch.role, role_color)}]{actor}")
        if ch.personality:
            print(f"   性格: {ch.personality[:60]}")
        if ch.description:
            print(f"   描述: {ch.description[:60]}")
    cm.close()
    return 0


def cmd_char_get(args):
    """获取角色详情（v5.2.1 新增）"""
    cm = _get_memory(args)
    char = cm.get_character(args.id)
    if not char:
        print(c("❌ 角色不存在", "red"))
        cm.close()
        return 1

    print(c("\n👤 角色详情", "bold"))
    print("=" * 40)
    print(f"姓名:   {char.name}")
    print(f"ID:     {char.id}")
    print(f"定位:   {char.role}")
    if char.actor:
        print(f"演员:   {char.actor}")
    if char.personality:
        print(f"性格:   {char.personality}")
    if char.description:
        print(f"描述:   {char.description}")
    if char.tags:
        print(f"标签:   {', '.join(char.tags)}")

    lines = cm.list_lines(character_id=char.id, limit=5)
    if lines:
        print("\n代表台词（最近5条）:")
        for l in lines[:5]:
            print(f'   "{l.line_text[:60]}"')

    cm.close()
    return 0


def cmd_char_update(args):
    """更新角色（v5.2.1 新增）"""
    cm = _get_memory(args)
    success = cm.update_character(
        char_id=args.id,
        name=args.name,
        role=args.role,
        actor=args.actor,
        description=args.description,
        personality=args.personality,
        avatar_url=args.avatar,
        tags=args.tags,
    )
    if success:
        print(c("\n✅ 角色已更新", "green"))
    else:
        print(c("\n❌ 更新失败", "red"))
    cm.close()
    return 0 if success else 1


def cmd_char_delete(args):
    """删除角色（v5.2.1 新增）"""
    cm = _get_memory(args)
    char = cm.get_character(args.id)
    if not char:
        print(c("❌ 角色不存在", "red"))
        cm.close()
        return 1

    if not args.force:
        print(c(f"\n⚠️  将删除角色：{char.name}", "yellow"))
        answer = input("\n确认删除？(y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            print(c("已取消", "yellow"))
            cm.close()
            return 0

    success = cm.delete_character(args.id)
    if success:
        print(c("\n🗑️  角色已删除", "green"))
    else:
        print(c("\n❌ 删除失败", "red"))
    cm.close()
    return 0 if success else 1


def cmd_scene_add(args):
    """添加短剧场次（v5.2.1 新增）"""
    cm = _get_memory(args)
    scene = cm.add_scene(
        drama_id=args.drama_id,
        episode=args.episode,
        scene_number=args.scene_number,
        title=args.title,
        content=args.content,
        location=args.location,
        time_of_day=args.time,
        tags=args.tags,
    )
    print(c("\n✅ 场次已添加", "green"))
    print(f"   ID: {scene.id}")
    print(f"   EP{scene.episode} - 场{scene.scene_number}: {scene.title}")
    if scene.location:
        print(f"   地点: {scene.location}")
    cm.close()
    return 0


def cmd_scene_list(args):
    """列出短剧场次（v5.2.1 新增）"""
    cm = _get_memory(args)
    scenes = cm.list_scenes(
        drama_id=args.drama_id,
        episode=args.episode,
        limit=args.limit,
        offset=args.offset,
    )
    print(f"\n找到 {c(str(len(scenes)), 'cyan')} 个场次")
    for i, s in enumerate(scenes, 1):
        location = f"[{s.location}]" if s.location else ""
        time_str = f" ({s.time_of_day})" if s.time_of_day else ""
        print(f"\n{i}. EP{s.episode}-{s.scene_number}: {s.title} {location}{time_str}")
        if s.content:
            print(f"   {s.content[:80]}...")
    cm.close()
    return 0


def cmd_scene_get(args):
    """获取场次详情（v5.2.1 新增）"""
    cm = _get_memory(args)
    scene = cm.get_scene(args.id)
    if not scene:
        print(c("❌ 场次不存在", "red"))
        cm.close()
        return 1

    print(c("\n🎬 场次详情", "bold"))
    print("=" * 40)
    print(f"标题:   EP{scene.episode} - 场{scene.scene_number}: {scene.title}")
    print(f"ID:     {scene.id}")
    if scene.location:
        print(f"地点:   {scene.location}")
    if scene.time_of_day:
        print(f"时间:   {scene.time_of_day}")
    if scene.tags:
        print(f"标签:   {', '.join(scene.tags)}")
    if scene.content:
        print(f"\n内容:\n{scene.content}")

    lines = cm.list_lines(scene_id=scene.id, limit=10)
    if lines:
        print(f"\n本场台词（{len(lines)} 条）:")
        for l in lines:
            char = l.character_name or "未知"
            print(f'   {char}: "{l.line_text[:60]}"')

    cm.close()
    return 0


def cmd_scene_update(args):
    """更新场次（v5.2.1 新增）"""
    cm = _get_memory(args)
    success = cm.update_scene(
        scene_id=args.id,
        title=args.title,
        content=args.content,
        location=args.location,
        time_of_day=args.time,
        tags=args.tags,
    )
    if success:
        print(c("\n✅ 场次已更新", "green"))
    else:
        print(c("\n❌ 更新失败", "red"))
    cm.close()
    return 0 if success else 1


def cmd_scene_delete(args):
    """删除场次（v5.2.1 新增）"""
    cm = _get_memory(args)
    scene = cm.get_scene(args.id)
    if not scene:
        print(c("❌ 场次不存在", "red"))
        cm.close()
        return 1

    if not args.force:
        print(c(f"\n⚠️  将删除场次：{scene.title}", "yellow"))
        print(c("   同时删除本场所有台词", "yellow"))
        answer = input("\n确认删除？(y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            print(c("已取消", "yellow"))
            cm.close()
            return 0

    success = cm.delete_scene(args.id)
    if success:
        print(c("\n🗑️  场次已删除", "green"))
    else:
        print(c("\n❌ 删除失败", "red"))
    cm.close()
    return 0 if success else 1


# ===== Agent 记忆优化（v5.2.2 新增）=====

def cmd_agent_stats(args):
    """Agent 记忆统计（v5.2.2 新增）"""
    cm = _get_memory(args)
    stats = cm.agent_stats(agent_id=args.agent)
    print(c("\n🤖 Agent 记忆统计", "bold"))
    print("=" * 50)

    if args.agent:
        # 单个 Agent 详情
        print(f"Agent ID:   {stats['agent_id']}")
        print(f"记忆总数:   {stats['total_memories']}")
        if stats['last_active']:
            from datetime import datetime
            print(f"最后活跃:   {datetime.fromtimestamp(stats['last_active']).strftime('%Y-%m-%d %H:%M')}")
        if stats['by_category']:
            print("\n按分类:")
            for cat, cnt in sorted(stats['by_category'].items(), key=lambda x: x[1], reverse=True)[:10]:
                print(f"  {cat}: {cnt}")
        if stats['by_layer']:
            print("\n按层级:")
            for layer, cnt in stats['by_layer'].items():
                print(f"  {layer}: {cnt}")
    else:
        # 全部 Agent 概览
        print(f"Agent 总数: {stats['total_agents']}")
        if stats['by_agent']:
            print("\n按 Agent 分布:")
            for agent, data in sorted(stats['by_agent'].items(), key=lambda x: x[1]['count'], reverse=True)[:15]:
                count = data['count']
                cats = ', '.join(data['top_categories'][:3]) if data['top_categories'] else '无'
                print(f"  {agent}: {count} 条 | 主要分类: {cats}")
    cm.close()
    return 0


def cmd_agent_list(args):
    """列出 Agent 的记忆（v5.2.2 新增）"""
    cm = _get_memory(args)
    entries = cm.list_by_agent(agent_id=args.agent, limit=args.limit, offset=args.offset)
    print(f"\nAgent [{args.agent}] 的记忆: {len(entries)} 条")
    for i, e in enumerate(entries, 1):
        star = "⭐" if e.starred else "  "
        preview = e.content[:60] + "..." if len(e.content) > 60 else e.content
        print(f"\n{i}. {star} [{e.category}] {c(preview, 'cyan')}")
        print(f"   ID: {e.id[:16]}... | 层级: {e.layer.value} | 隐私: {e.privacy.value}")
    cm.close()
    return 0


def cmd_evolve(args):
    """记忆演化（v5.2.2 新增）"""
    cm = _get_memory(args)
    print(c("\n🧠 记忆演化", "bold"))
    print("=" * 50)

    result = cm.evolve_memories(dry_run=args.dry_run)

    if args.dry_run:
        print(c("📊 模拟统计（未执行）", "yellow"))
    else:
        print(c("✅ 演化完成", "green"))

    print(f"  短期→长期候选: {result.get('short_to_long', 0)} 条")
    print(f"  长期→永久候选: {result.get('long_to_permanent', 0)} 条")
    print(f"  过期短期记忆:   {result.get('stale_short_term', 0)} 条")

    if not args.dry_run:
        print(f"\n  实际升级到长期: {result.get('upgraded_to_long', 0)} 条")
        print(f"  实际升级到永久: {result.get('upgraded_to_permanent', 0)} 条")

    if args.dry_run:
        print("\n💡 使用 evolve（不加 --dry-run）执行实际演化")
    cm.close()
    return 0


def cmd_agent_transfer(args):
    """迁移 Agent 记忆（v5.2.2 新增）"""
    cm = _get_memory(args)
    print(c("\n🔄 Agent 记忆迁移", "bold"))
    print("=" * 50)
    print(f"  源 Agent:   {args.from_agent}")
    print(f"  目标 Agent: {args.to_agent}")
    if args.category:
        print(f"  分类过滤:   {args.category}")

    result = cm.transfer_agent_memories(
        from_agent=args.from_agent,
        to_agent=args.to_agent,
        category=args.category,
    )

    print(f"\n  已迁移:     {c(str(result['transferred']) + ' 条', 'green')}")
    cm.close()
    return 0


def cmd_agent_clean(args):
    """清理 Agent 旧记忆（v5.2.2 新增）"""
    cm = _get_memory(args)
    print(c("\n🧹 Agent 记忆清理", "bold"))
    print("=" * 50)
    print(f"  Agent ID:           {args.agent}")
    print(f"  清理天数阈值:       {args.days} 天")
    print(f"  最高清理重要级别:   {args.max_importance or '全部'}")

    if args.dry_run:
        print(c("\n📊 模拟统计（未执行）", "yellow"))
    else:
        print(c("\n⚠️  执行清理（移入回收站）", "yellow"))

    result = cm.clean_agent_memories(
        agent_id=args.agent,
        older_than_days=args.days,
        max_importance=args.max_importance,
        dry_run=args.dry_run,
    )

    print(f"\n  待清理:       {result.get('to_clean', 0)} 条")
    if not args.dry_run:
        print(f"  已清理:       {c(str(result.get('cleaned', 0)) + ' 条', 'green')}")

    if args.dry_run:
        print("\n💡 使用 agent-clean（不加 --dry-run）执行实际清理")
    cm.close()
    return 0


# ===== v5.2.9 Agent 记忆增强命令 =====

def cmd_agent_list_memories(args):
    """列出 Agent 记忆（v5.2.9 新增，表格/JSON 双格式）"""
    cm = _get_memory(args)
    print(c("\n🧠 Agent 记忆列表", "bold"))
    print("=" * 60)
    print(f"  Agent ID:  {args.agent}")
    print(f"  Limit:     {args.limit}")
    print(f"  Offset:    {args.offset}")

    entries = cm.list_by_agent(agent_id=args.agent, limit=args.limit, offset=args.offset)

    if not entries:
        print(c("\nℹ️  暂无该 Agent 的记忆", "yellow"))
        cm.close()
        return 0

    if args.format == "json":
        # v5.2.9 安全加固：输出敏感数据时截断 ID/标签
        json_data = []
        for e in entries:
            d = e.to_dict() if hasattr(e, "to_dict") else vars(e)
            if isinstance(d.get("content"), str):
                d["content"] = d["content"][:500]
            json_data.append(d)
        import json as _json
        print(_json.dumps(json_data, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"\n共 {c(str(len(entries)), 'cyan')} 条：")
        from datetime import datetime
        for i, e in enumerate(entries, 1):
            content = (e.content or "").replace("\n", " ")[:70]
            created = datetime.fromtimestamp(e.created_at).strftime("%Y-%m-%d %H:%M") if e.created_at else "-"
            print(f"  {i:>3}. [{created}] ({e.category}/{e.layer}) {e.id[:10]}... {content}")

    cm.close()
    return 0


def cmd_agent_rank(args):
    """Agent 记忆排行榜（v5.2.9 新增）"""
    cm = _get_memory(args)
    print(c("\n🏆 Agent 记忆排行榜", "bold"))
    print("=" * 60)
    print(f"  排序维度: {args.by}")
    print(f"  Limit:    {args.limit}")

    rows = cm.rank_agents(by=args.by, limit=args.limit)

    if not rows:
        print(c("\nℹ️  暂无 Agent 数据", "yellow"))
        cm.close()
        return 0

    from datetime import datetime
    labels_map = {
        "count": "记忆数",
        "last_active": "最近活跃",
        "avg_importance": "平均重要度",
        "starred": "收藏数",
    }
    label = labels_map.get(args.by, args.by)
    print(f"\n  {'#':>3}  {'Agent ID':<28}  {'记忆数':>6}  {'收藏':>4}  {'平均重要':>6}  {label}")
    print("  " + "-" * 72)
    for i, r in enumerate(rows, 1):
        aid = (r["agent_id"] or "")[:26] + ("…" if len(r["agent_id"] or "") > 26 else "")
        la = datetime.fromtimestamp(r["last_active"]).strftime("%m-%d %H:%M") if r["last_active"] else "-"
        extra = {
            "count": str(r["count"]),
            "last_active": la,
            "avg_importance": str(r["avg_importance"]),
            "starred": str(r["starred_count"]),
        }[args.by]
        print(f"  {i:>3}. {aid:<28}  {r['count']:>6}  {r['starred_count']:>4}  "
              f"{r['avg_importance']:>6}  {c(extra, 'cyan')}")

    cm.close()
    return 0


def cmd_agent_forget(args):
    """遗忘 Agent 低质量旧记忆（v5.2.9 新增）"""
    cm = _get_memory(args)
    print(c("\n🗑️  Agent 智能遗忘", "bold"))
    print("=" * 60)
    print(f"  Agent ID:            {args.agent}")
    print(f"  质量分数阈值:        < {args.min_score}/100")
    print(f"  未更新超过:          {args.days} 天")

    if args.dry_run:
        print(c("\n📊 预览模式（未执行）", "yellow"))
    else:
        print(c("\n⚠️  执行模式（标记为 trash 回收站）", "yellow"))

    result = cm.forget_agent(
        agent_id=args.agent,
        min_quality_score=args.min_score,
        older_than_days=args.days,
        dry_run=args.dry_run,
    )

    err = result.get("error")
    if err:
        print(c(f"\n❌ {err}", "red"))
        cm.close()
        return 1

    print(f"\n  评估总数:   {result['evaluated']}")
    print(f"  选中遗忘:   {c(str(result['selected']) + ' 条', 'yellow')}")
    if not args.dry_run:
        print(f"  已执行:     {c(str(result['cleaned']) + ' 条', 'green')}")
    if result.get("selected_ids"):
        print(f"  前 20 ID:  {', '.join(result['selected_ids'])}")

    cm.close()
    return 0


# ===== v5.3.0 Agent 记忆增强命令 =====

def cmd_agent_profile(args):
    """Agent 记忆画像（v5.3.0 新增）"""
    cm = _get_memory(args)
    print(c("\n🧠 Agent 记忆画像", "bold"))
    print("=" * 60)

    profile = cm.agent_profile(agent_id=args.agent)

    if profile.get("error"):
        print(c(f"\n❌ {profile['error']}", "red"))
        cm.close()
        return 1

    if profile.get("total_memories", 0) == 0:
        print(c(f"\nℹ️  Agent '{args.agent}' 暂无记忆", "yellow"))
        cm.close()
        return 0

    from datetime import datetime
    print(f"  Agent ID:       {profile['agent_id']}")
    print(f"  记忆总数:       {c(str(profile['total_memories']), 'cyan')}")
    first = datetime.fromtimestamp(profile['first_active']).strftime("%Y-%m-%d %H:%M") if profile.get('first_active') else "-"
    last = datetime.fromtimestamp(profile['last_active']).strftime("%Y-%m-%d %H:%M") if profile.get('last_active') else "-"
    print(f"  首次活跃:       {first}")
    print(f"  最近活跃:       {c(last, 'green')}")

    if profile.get("by_layer"):
        print("\n  📊 层级分布:")
        for layer, cnt in profile["by_layer"].items():
            print(f"     {layer}: {cnt}")

    if profile.get("by_category"):
        print("\n  📁 分类分布 Top-10:")
        for cat, cnt in profile["by_category"].items():
            print(f"     {cat}: {cnt}")

    if profile.get("by_importance"):
        print("\n  ⚡ 重要度分布:")
        for imp, cnt in profile["by_importance"].items():
            print(f"     {imp}: {cnt}")

    print(f"\n  ⭐ 收藏: {profile.get('starred_count', 0)}  |  📌 置顶: {profile.get('pinned_count', 0)}")

    if profile.get("top_tags"):
        print("\n  🏷️  知识领域 Top-10:")
        for t in profile["top_tags"]:
            print(f"     {t['tag']}: {t['count']}")

    if profile.get("activity_timeline_30d"):
        print("\n  📈 近 30 天活跃:")
        for day, cnt in sorted(profile["activity_timeline_30d"].items())[-7:]:
            bar = "█" * min(cnt, 30)
            print(f"     {day}: {bar} {cnt}")

    if profile.get("quality_distribution_sample"):
        print(f"\n  📋 质量分布（采样 {profile.get('quality_sample_size', 0)} 条）:")
        qd = profile["quality_distribution_sample"]
        for grade in ["优秀", "良好", "中等", "及格", "需改进"]:
            cnt = qd.get(grade, 0)
            if cnt > 0:
                print(f"     {grade}: {cnt}")

    cm.close()
    return 0


def cmd_agent_merge(args):
    """合并两个 Agent 的记忆（v5.3.0 新增）"""
    cm = _get_memory(args)
    print(c("\n🔀 Agent 记忆合并", "bold"))
    print("=" * 60)
    print(f"  源 Agent:    {args.from_agent}")
    print(f"  目标 Agent:  {args.to_agent}")
    print(f"  去重模式:    {args.dedup}")

    if args.dry_run:
        print(c("\n📊 预览模式（未执行）", "yellow"))
    else:
        print(c("\n⚠️  执行模式（将迁移源 Agent 记忆到目标 Agent）", "yellow"))

    result = cm.merge_agents(
        from_agent=args.from_agent,
        to_agent=args.to_agent,
        dedup=args.dedup,
        dry_run=args.dry_run,
    )

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"\n  评估总数:       {result['evaluated']}")
    print(f"  迁移数量:       {c(str(result['migrated']) + ' 条', 'green')}")
    print(f"  跳过重复:       {result['skipped_duplicates']}")
    if result.get("failed"):
        print(f"  失败:           {c(str(result['failed']), 'red')}")

    cm.close()
    return 0


def cmd_agent_export(args):
    """导出 Agent 记忆为 JSON 包（v5.3.0 新增）"""
    cm = _get_memory(args)
    print(c("\n📦 导出 Agent 记忆", "bold"))
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  输出文件:    {args.output}")
    print(f"  含审计日志:  {'是' if args.include_audit else '否'}")

    try:
        result = cm.export_agent(
            agent_id=args.agent,
            output_path=args.output,
            include_audit=args.include_audit,
        )
    except (OSError, ValueError) as e:
        print(c(f"\n❌ 导出失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(c("\n✅ 导出成功", "green"))
    print(f"   文件: {result['file_path']}")
    print(f"   数量: {result['total']} 条记忆")

    cm.close()
    return 0


def cmd_agent_search(args):
    """在指定 Agent 的记忆中搜索关键词（v5.3.1 新增）"""
    cm = _get_memory(args)
    print(c("\n🔍 Agent 记忆搜索（v5.3.1）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:  {args.agent}")
    print(f"  关键词:    {args.keyword}")
    print(f"  限制:      {args.limit}（偏移 {args.offset}）")

    try:
        results = cm.agent_search(
            agent_id=args.agent,
            keyword=args.keyword,
            limit=args.limit,
            offset=args.offset,
        )
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 搜索失败: {e}", "red"))
        cm.close()
        return 1

    if not results:
        print(c("\n⚠️  未找到匹配的记忆", "yellow"))
        cm.close()
        return 0

    if args.format == "json":
        import json as _json
        out = []
        for r in results:
            imp = getattr(r, "importance", "")
            out.append({
                "id": getattr(r, "id", ""),
                "content": getattr(r, "content", ""),
                "category": getattr(r, "category", ""),
                "tags": getattr(r, "tags", []) if hasattr(r, "tags") else [],
                "importance": imp.value if hasattr(imp, "value") else str(imp),
                "created_at": getattr(r, "created_at", 0),
            })
        print(_json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        print(c(f"\n📋 共找到 {len(results)} 条匹配记忆", "green"))
        print("-" * 60)
        for i, r in enumerate(results, 1):
            content = getattr(r, "content", "")[:60]
            cat = getattr(r, "category", "general")
            imp = getattr(r, "importance", "")
            imp_str = imp.value if hasattr(imp, "value") else str(imp)
            print(f"  {i}. [{cat}][{imp_str}] {content}...")

    cm.close()
    return 0


def cmd_agent_compare(args):
    """对比两个 Agent 的记忆差异（v5.3.1 新增）"""
    cm = _get_memory(args)
    print(c("\n⚖️  Agent 记忆对比（v5.3.1）", "bold"))
    print("=" * 60)
    print(f"  Agent A:   {args.agent_a}")
    print(f"  Agent B:   {args.agent_b}")

    try:
        result = cm.agent_compare(args.agent_a, args.agent_b)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 对比失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(c("\n📊 记忆数量对比", "cyan"))
    print(f"  Agent A:   {result['count_a']} 条")
    print(f"  Agent B:   {result['count_b']} 条")
    print(f"  差值:      {result['count_a'] - result['count_b']:+d}")

    print(c("\n📈 平均重要度对比", "cyan"))
    print(f"  Agent A:   {result['avg_importance_a']}")
    print(f"  Agent B:   {result['avg_importance_b']}")

    print(c("\n📂 共有分类", "cyan"))
    if result["common_categories"]:
        for cat in result["common_categories"][:10]:
            print(f"  • {cat}")
        if len(result["common_categories"]) > 10:
            print(f"  ... 共 {len(result['common_categories'])} 个")
    else:
        print("  （无共有分类）")

    print(c(f"\n📂 A 独有分类（{len(result['only_a_categories'])} 个）", "yellow"))
    for cat in result["only_a_categories"][:5]:
        print(f"  • {cat}")

    print(c(f"\n📂 B 独有分类（{len(result['only_b_categories'])} 个）", "yellow"))
    for cat in result["only_b_categories"][:5]:
        print(f"  • {cat}")

    print(c("\n🏷️  共有标签", "cyan"))
    if result["common_tags"]:
        print(f"  {' '.join(result['common_tags'][:15])}")
    else:
        print("  （无共有标签）")

    print(f"\n  标签总数: A={result['tags_a_count']}  B={result['tags_b_count']}")

    cm.close()
    return 0


def cmd_drama_search(args):
    """按关键词搜索短剧（v5.3.1 新增）"""
    cm = _get_memory(args)
    print(c("\n🎬 短剧搜索（v5.3.1）", "bold"))
    print("=" * 60)
    print(f"  关键词:    {args.keyword}")
    if args.genre:
        print(f"  类型:      {args.genre}")
    if args.min_rating > 0:
        print(f"  最低评分:  {args.min_rating}")
    print(f"  限制:      {args.limit}（偏移 {args.offset}）")

    try:
        results = cm.drama_search(
            keyword=args.keyword,
            genre=args.genre,
            min_rating=args.min_rating,
            limit=args.limit,
            offset=args.offset,
        )
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 搜索失败: {e}", "red"))
        cm.close()
        return 1

    if not results:
        print(c("\n⚠️  未找到匹配的短剧", "yellow"))
        cm.close()
        return 0

    print(c(f"\n📋 共找到 {len(results)} 部短剧", "green"))
    print("-" * 60)
    for i, d in enumerate(results, 1):
        title = getattr(d, "title", "")
        genre = getattr(d, "genre", "")
        rating = getattr(d, "rating", 0.0)
        status = getattr(d, "status", "")
        desc = (getattr(d, "description", "") or "")[:40]
        print(f"  {i}. 《{title}》  [{genre}][{status}]  评分: {rating}")
        if desc:
            print(f"     {desc}...")

    cm.close()
    return 0


def cmd_char_ranking(args):
    """角色台词排行榜（v5.3.1 新增）"""
    cm = _get_memory(args)
    print(c("\n🏆 角色台词排行榜（v5.3.1）", "bold"))
    print("=" * 60)
    if args.drama_id:
        print(f"  短剧 ID:   {args.drama_id}")
    else:
        print("  范围:      全局")
    print(f"  排序维度:  {args.sort_by}")
    print(f"  Top 数:    {args.limit}")

    try:
        results = cm.character_ranking(
            drama_id=args.drama_id,
            sort_by=args.sort_by,
            limit=args.limit,
        )
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 排行榜生成失败: {e}", "red"))
        cm.close()
        return 1

    if not results:
        print(c("\n⚠️  暂无角色数据", "yellow"))
        cm.close()
        return 0

    print(c(f"\n🥇 角色排行 Top {len(results)}", "green"))
    print("-" * 70)
    print(f"{'排名':<5}{'角色名':<15}{'总台词':<8}{'经典':<8}{'经典率':<10}{'场次':<8}{'平均字数':<10}")
    for r in results:
        print(f"{r['rank']:<5}{r['name'][:12]:<15}{r['total_lines']:<8}"
              f"{r['classic_lines']:<8}{r['classic_ratio']}%{'':<5}{r['scene_count']:<8}{r['avg_line_length']:<10}")

    cm.close()
    return 0


def cmd_agent_diff(args):
    """对比同一 Agent 在不同时间段的记忆差异（v5.3.2 新增）"""
    cm = _get_memory(args)
    print(c("\n📈 Agent 记忆时间段对比（v5.3.2）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:   {args.agent}")
    print(f"  时间段 A:   {args.days_a} 天前 ~ {args.days_b} 天前")
    print(f"  时间段 B:   {args.days_b} 天前 ~ 现在")

    try:
        result = cm.agent_diff(args.agent, args.days_a, args.days_b)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    pa = result["period_a"]
    pb = result["period_b"]
    print(c("\n📊 时间段对比", "cyan"))
    print(f"  {pa['time_range']}: {pa['count']} 条")
    print(f"  {pb['time_range']}: {pb['count']} 条")
    print(f"  增量: {c(str(result['total_diff']), 'green' if result['total_diff'] >= 0 else 'yellow')}")

    print(c("\n📈 重要度分布（A 时间段）", "cyan"))
    for imp, cnt in pa["by_importance"].items():
        print(f"  {imp:<10} {cnt}")

    print(c("\n📈 重要度分布（B 时间段）", "cyan"))
    for imp, cnt in pb["by_importance"].items():
        print(f"  {imp:<10} {cnt}")

    if result["new_categories"]:
        print(c("\n✨ 新增分类（B 新增，A 没有）", "green"))
        for cat in result["new_categories"][:15]:
            cnt = pb["by_category"].get(cat, 0)
            print(f"  • {cat}  ({cnt} 条)")

    if result["dropped_categories"]:
        print(c("\n💨 消失分类（A 有，B 没有）", "yellow"))
        for cat in result["dropped_categories"][:15]:
            cnt = pa["by_category"].get(cat, 0)
            print(f"  • {cat}  ({cnt} 条)")

    cm.close()
    return 0


def cmd_agent_purge(args):
    """清空指定 Agent 的全部记忆（v5.3.2 新增，高危操作）"""
    cm = _get_memory(args)
    dry_run = not args.force
    print(c("\n⚠️  Agent 记忆清空（v5.3.2）", "bold" if not dry_run else "bold"))
    print("=" * 60)
    print(f"  Agent ID:   {args.agent}")
    print(f"  模式:       {'❌ 实际执行！会永久删除！' if not dry_run else '🔍 预览模式 (加 --force 实际执行)'}")

    if not dry_run:
        print(c("\n  高危操作：将永久删除该 Agent 的全部记忆！", "red"))

    try:
        result = cm.agent_purge(args.agent, dry_run=dry_run)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"\n  匹配记忆:   {result['total_found']} 条")

    if dry_run and result.get("by_category"):
        print(c("\n📋 分类明细（预览）", "cyan"))
        for cat, cnt in sorted(result["by_category"].items(), key=lambda x: -x[1]):
            print(f"  • {cat:<25} {cnt}")
        if result.get("note"):
            print(c(f"\n💡 {result['note']}", "yellow"))

    if not dry_run:
        print(c(f"\n✅ 已永久删除 {result['purged']} 条记忆", "green"))

    cm.close()
    return 0


def cmd_drama_progress_update(args):
    """更新短剧观看进度（v5.3.2 新增）"""
    cm = _get_memory(args)
    print(c("\n🎬 短剧观看进度更新（v5.3.2）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama}")
    print(f"  当前集数:   第 {args.episode} 集")
    if args.status:
        print(f"  观看状态:   {args.status}")
    if args.rating is not None:
        print(f"  用户评分:   {args.rating}")

    try:
        result = cm.drama_progress(
            args.drama, args.episode, args.status, args.rating)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(c("\n✅ 进度已更新", "green"))
    print(f"  短剧:       {result['drama_id']}")
    print(f"  集数:       {result['current_episode']}")
    if result.get("status"):
        print(f"  状态:       {result['status']}")
    if result.get("user_rating") is not None:
        print(f"  评分:       {result['user_rating']}")

    cm.close()
    return 0


def cmd_drama_rec2(args):
    """短剧智能推荐 v2（v5.3.2 新增）"""
    cm = _get_memory(args)
    print(c("\n🎬 短剧智能推荐 v2（v5.3.2）", "bold"))
    print("=" * 60)
    if args.genre:
        print(f"  类型:       {args.genre}")
    if args.min_rating > 0:
        print(f"  最低评分:   {args.min_rating}")
    print(f"  模式:       {args.mode}")
    print(f"  Top 数:     {args.limit}")

    try:
        results = cm.drama_recommend_v2(
            genre=args.genre,
            min_rating=args.min_rating,
            mode=args.mode,
            limit=args.limit,
        )
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if not results:
        print(c("\n⚠️  暂无匹配短剧，请尝试调整过滤条件", "yellow"))
        cm.close()
        return 0

    print(c(f"\n🏆 推荐 Top {len(results)}", "green"))
    print("-" * 75)
    print(f"{'#':<4}{'剧名':<20}{'类型':<10}{'官方分':<8}{'状态':<12}{'看到':<6}{'用户分':<6}")
    for i, d in enumerate(results, 1):
        ep_label = f"E{d['current_episode']}" if d['current_episode'] else "-"
        ur = f"{d['user_rating']}" if d['user_rating'] is not None else "-"
        ws = d['watch_status'] or "-"
        print(f"{i:<4}{d['title'][:18]:<20}{d['genre']:<10}{d['rating']:<8}"
              f"{ws:<12}{ep_label:<6}{ur:<6}")

    cm.close()
    return 0


def cmd_agent_timeline(args):
    """Agent 记忆时间线分析（v5.3.3 新增）"""
    cm = _get_memory(args)
    print(c("\n📊 Agent 记忆时间线分析（v5.3.3）", "bold"))
    print("=" * 60)
    print(f"  Agent:   {args.agent_id}")
    print(f"  天数:    {args.days}")

    try:
        result = cm.agent_timeline(args.agent_id, days=args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    if result["total_memories"] == 0:
        print(c(f"\n⚠️  该 Agent 在 {args.days} 天内无记忆记录", "yellow"))
        cm.close()
        return 0

    print(f"\n  总记忆数:    {result['total_memories']}")
    print(f"  日均记忆:    {result['avg_per_day']}")

    trend_label = {"rising": "📈 上升", "declining": "📉 下降",
                   "stable": "➡️ 稳定", "no_data": "无数据",
                   "insufficient_data": "数据不足"}.get(result["trend"], result["trend"])
    print(f"  趋势:        {trend_label}")

    if result.get("peak_day"):
        print(f"  最活跃日期:  {result['peak_day']['date']}（{result['peak_day']['count']} 条）")
    if result.get("peak_hour") is not None:
        print(f"  最活跃时段:  {result['peak_hour']['hour']:02d}:00（{result['peak_hour']['count']} 条）")

    if result.get("top_active_hours"):
        hours_str = ", ".join(f"{h:02d}:00" for h in result["top_active_hours"])
        print(f"  活跃时段Top3: {hours_str}")

    # 按天分布（最近 10 天）
    by_day = result.get("by_day", {})
    if by_day:
        print(c(f"\n📅 按天分布（最近 {min(len(by_day), 10)} 天）", "cyan"))
        sorted_days = sorted(by_day.keys(), reverse=True)[:10]
        for day in sorted_days:
            count = by_day[day]
            bar = "█" * min(count, 30)
            print(f"  {day} | {bar} {count}")

    cm.close()
    return 0


def cmd_agent_heatmap(args):
    """Agent 记忆热力图（v5.3.3 新增）"""
    cm = _get_memory(args)
    print(c("\n🔥 Agent 记忆热力图（v5.3.3）", "bold"))
    print("=" * 60)
    print(f"  Agent:   {args.agent_id}")
    print(f"  天数:    {args.days}")

    try:
        result = cm.agent_heatmap(args.agent_id, days=args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    if result["total_memories"] == 0:
        print(c(f"\n⚠️  该 Agent 在 {args.days} 天内无记忆记录", "yellow"))
        cm.close()
        return 0

    print(f"\n  总记忆数:  {result['total_memories']}")
    print(f"  分类数:    {len(result['categories'])}")

    if result.get("max_density_cell"):
        mc = result["max_density_cell"]
        print(f"  密度最高:  [{mc['category']} × {mc['importance']}] = {mc['count']}")

    # 矩阵表
    matrix = result.get("matrix", {})
    imp_levels = result.get("importance_levels", ["LOW", "MEDIUM", "HIGH", "CRITICAL"])

    print(c("\n📊 密度矩阵（分类 × 重要度）", "cyan"))
    print(f"  {'分类':<20}", end="")
    for lv in imp_levels:
        print(f"{lv:<10}", end="")
    print(f"{'总计':<8}")
    print("  " + "-" * 60)

    for cat in sorted(matrix.keys()):
        print(f"  {cat[:18]:<20}", end="")
        for lv in imp_levels:
            val = matrix[cat].get(lv, 0)
            cell = str(val) if val > 0 else "-"
            print(f"{cell:<10}", end="")
        print(f"{result['row_totals'].get(cat, 0):<8}")

    print("  " + "-" * 60)
    print(f"  {'总计':<20}", end="")
    for lv in imp_levels:
        print(f"{result['col_totals'].get(lv, 0):<10}", end="")
    print(f"{result['total_memories']:<8}")

    cm.close()
    return 0


def cmd_drama_binge(args):
    """追剧统计（v5.3.3 新增）"""
    cm = _get_memory(args)
    print(c("\n📺 追剧统计（v5.3.3）", "bold"))
    print("=" * 60)

    try:
        result = cm.drama_binge(drama_id=args.drama_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    if result["total_dramas"] == 0:
        print(c("\n⚠️  暂无短剧数据", "yellow"))
        cm.close()
        return 0

    print(f"\n  总短剧数:      {result['total_dramas']}")
    print(f"  追剧中:        {result['watching']}")
    print(f"  已完成:        {result['completed']}")
    print(f"  已弃剧:        {result['dropped']}")
    print(f"  计划中:        {result['planned']}")
    print(f"\n  已观看集数:    {result['total_episodes_watched']}")
    print(f"  计划总集数:    {result['total_episodes_planned']}")
    print(f"  完成率:        {result['completion_rate']}%")
    if result.get("average_rating"):
        print(f"  平均评分:      {result['average_rating']}（{result['rated_count']} 部已评分）")

    if result.get("recent_watched"):
        print(c("\n🕐 最近观看 Top-5", "cyan"))
        print("-" * 60)
        for i, w in enumerate(result["recent_watched"], 1):
            status_label = {"watching": "追剧中", "completed": "已完成",
                           "dropped": "已弃剧", "planned": "计划中"}.get(w["status"], w["status"])
            print(f"  {i}. {w['title'][:20]:<22} {status_label:<8} "
                  f"E{w['current_episode']}/{w['total_episodes']} "
                  f"评分:{w['rating']}")

    cm.close()
    return 0


def cmd_char_network(args):
    """角色关系网络（v5.3.3 新增）"""
    cm = _get_memory(args)
    print(c("\n🕸️  角色关系网络（v5.3.3）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID: {args.drama_id}")

    try:
        result = cm.char_network(args.drama_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    if result["total_characters"] == 0:
        print(c("\n⚠️  该短剧暂无角色数据", "yellow"))
        cm.close()
        return 0

    print(f"\n  角色总数:    {result['total_characters']}")
    print(f"  关系边数:    {result['total_edges']}")
    print(f"  分析场次数:  {result['total_scenes_analyzed']}")

    # 角色节点（按关联数排序）
    nodes = result.get("nodes", [])
    if nodes:
        print(c("\n👥 角色节点（按关联数排序）", "cyan"))
        print("-" * 60)
        print(f"  {'#':<4}{'角色名':<16}{'角色类型':<12}{'出场场次':<10}{'关联数':<8}")
        for i, n in enumerate(nodes[:15], 1):
            print(f"  {i:<4}{n['name'][:14]:<16}{n['role']:<12}"
                  f"{n['scene_count']:<10}{n['connections']:<8}")

    # 关系边（按权重排序，Top-10）
    edges = result.get("edges", [])
    if edges:
        print(c("\n🔗 角色关系 Top-10（按共同出场次数）", "cyan"))
        print("-" * 60)
        for i, e in enumerate(edges[:10], 1):
            print(f"  {i}. {e['source_name'][:12]} ↔ {e['target_name'][:12]}  "
                  f"共同出场: {e['weight']} 次")

    cm.close()
    return 0


def cmd_agent_sentiment(args):
    """Agent 记忆情感分析（v5.3.4 新增）"""
    cm = _get_memory(args)
    print(c("\n🧠 Agent 记忆情感分析（v5.3.4）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:   {args.agent}")
    print(f"  回溯天数:   {args.days}")

    try:
        result = cm.agent_sentiment(args.agent, args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    total = result["total_memories"]
    if total == 0:
        print(c(f"\n⚠️  该 Agent 在 {args.days} 天内无记忆", "yellow"))
        cm.close()
        return 0

    print(c(f"\n📊 情感分布（共 {total} 条记忆）", "cyan"))
    bar_pos = "█" * int(result["positive_ratio"] * 20)
    bar_neg = "█" * int(result["negative_ratio"] * 20)
    bar_neu = "█" * int(result["neutral_ratio"] * 20)
    print(f"  {c('正面', 'green')}: {result['positive']:>5} ({result['positive_ratio']:.1%}) {bar_pos}")
    print(f"  {c('负面', 'red')}: {result['negative']:>5} ({result['negative_ratio']:.1%}) {bar_neg}")
    print(f"  {c('中性', 'yellow')}: {result['neutral']:>5} ({result['neutral_ratio']:.1%}) {bar_neu}")

    dom = result["dominant_sentiment"]
    dom_label = {"positive": "正面主导 😊", "negative": "负面主导 😟", "neutral": "中性主导 😐"}.get(dom, dom)
    print(c(f"\n  主导情感: {dom_label}", "bold"))

    if result.get("by_importance"):
        print(c("\n📈 按重要度细分", "cyan"))
        for imp, vals in sorted(result["by_importance"].items()):
            print(f"  {imp:<10} 正:{vals['positive']}  负:{vals['negative']}  中:{vals['neutral']}")

    cm.close()
    return 0


def cmd_memory_decay(args):
    """记忆衰减评分（v5.3.4 新增）"""
    cm = _get_memory(args)
    print(c("\n🧠 记忆衰减评分（v5.3.4）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:   {args.agent}")
    print(f"  回溯天数:   {args.days}")

    try:
        result = cm.memory_decay(args.agent, args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    total = result["total_memories"]
    if total == 0:
        print(c(f"\n⚠️  该 Agent 在 {args.days} 天内无记忆", "yellow"))
        cm.close()
        return 0

    print(c(f"\n📊 衰减分布（共 {total} 条记忆）", "cyan"))
    dd = result["decay_distribution"]
    print(f"  {c('💪 强固', 'green')}:  {dd.get('strong', 0):>5}  (retention ≥ 70%)")
    print(f"  {c('✅ 稳定', 'cyan')}:  {dd.get('stable', 0):>5}  (40% ~ 70%)")
    print(f"  {c('⚠️  衰减', 'yellow')}:  {dd.get('fading', 0):>5}  (15% ~ 40%)")
    print(f"  {c('❗ 危急', 'red')}:  {dd.get('critical', 0):>5}  (< 15%)")

    avg_ret = f"{result['avg_retention']:.1%}"
    print(f"\n  平均保留率: {c(avg_ret, 'bold')}")
    print(f"  危急记忆数: {result['critical_decay']}")

    if result.get("critical_memories"):
        print(c(f"\n❗ 危急记忆 Top {len(result['critical_memories'])}", "red"))
        print("-" * 70)
        for m in result["critical_memories"][:10]:
            preview = m["content_preview"][:40] or "(空)"
            print(f"  [{m['importance']:<8}] 保留率:{m['retention']:.1%}  "
                  f"天数:{m['days_elapsed']:.0f}d  访问:{m['access_count']}  {preview}")

    cm.close()
    return 0


def cmd_drama_compare(args):
    """短剧对比分析（v5.3.4 新增）"""
    cm = _get_memory(args)
    print(c("\n🎬 短剧对比分析（v5.3.4）", "bold"))
    print("=" * 60)
    print(f"  对比数量:   {len(args.dramas)} 部")

    try:
        result = cm.drama_compare(args.dramas)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    dramas = result["dramas"]
    print(c(f"\n📊 对比明细（{result['total_compared']} 部有效）", "cyan"))
    print("-" * 85)
    print(f"{'#':<4}{'剧名':<16}{'类型':<8}{'集数':<6}{'角色':<6}{'台词':<6}{'经典':<6}{'评分':<6}{'状态':<10}")
    print("-" * 85)
    for i, d in enumerate(dramas, 1):
        if "error" in d:
            print(f"{i:<4}{d['id'][:14]:<16}{c('未找到', 'red')}")
            continue
        print(f"{i:<4}{d['title'][:14]:<16}{d['genre'][:6]:<8}{d['total_episodes']:<6}"
              f"{d['character_count']:<6}{d['total_lines']:<6}{d['classic_lines']:<6}"
              f"{d['rating']:<6}{d['status']:<10}")

    comp = result.get("comparison", {})
    if comp:
        print(c("\n🏆 各维度领先", "green"))
        if comp.get("best_rated"):
            print(f"  评分最高:   {comp['best_rated']}")
        if comp.get("most_episodes"):
            print(f"  集数最多:   {comp['most_episodes']}")
        if comp.get("most_characters"):
            print(f"  角色最多:   {comp['most_characters']}")
        if comp.get("most_classic_lines"):
            print(f"  经典最多:   {comp['most_classic_lines']}")

    cm.close()
    return 0


def cmd_char_arc(args):
    """角色成长弧线分析（v5.3.4 新增）"""
    cm = _get_memory(args)
    print(c("\n🎬 角色成长弧线分析（v5.3.4）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")
    print(f"  角色 ID:    {args.character_id}")

    try:
        result = cm.character_arc(args.drama_id, args.character_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"\n  角色名:     {result['character_name']}")
    print(f"  角色定位:   {result.get('character_role', '未知')}")
    print(f"  出场场景:   {result['total_scenes']}")
    print(f"  总台词数:   {result['total_lines']}")

    if result["total_scenes"] == 0:
        print(c("\n⚠️  该角色无台词记录", "yellow"))
        cm.close()
        return 0

    peak = result["peak_scene"]
    print(f"  峰值场景:   {peak['scene_id']}  ({peak['line_count']} 句)")

    stage = result["growth_stage"]
    stage_label = {
        "rising": "后期崛起 📈",
        "falling": "前期活跃 📉",
        "peak_middle": "中期高峰 ⛰️",
        "stable": "稳定出场 ➡️",
        "no_data": "数据不足",
    }.get(stage, stage)
    print(c(f"\n  成长阶段:   {stage_label}", "bold"))

    sd = result["stage_distribution"]
    print(c("\n📊 三段分布", "cyan"))
    total = sd["early"] + sd["mid"] + sd["late"]
    if total > 0:
        print(f"  前期:  {sd['early']:>4} 句  ({sd['early']/total:.1%})")
        print(f"  中期:  {sd['mid']:>4} 句  ({sd['mid']/total:.1%})")
        print(f"  后期:  {sd['late']:>4} 句  ({sd['late']/total:.1%})")

    cm.close()
    return 0


def cmd_memory_cluster(args):
    """记忆主题聚类（v5.3.5 新增）"""
    cm = _get_memory(args)
    print(c("\n🧠 记忆主题聚类（v5.3.5）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:   {args.agent}")
    print(f"  回溯天数:   {args.days}")
    print(f"  最大簇数:   {args.max_clusters}")

    try:
        result = cm.memory_cluster(args.agent, args.days, args.max_clusters)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    total = result["total_memories"]
    if total == 0:
        print(c(f"\n⚠️  该 Agent 在 {args.days} 天内无记忆", "yellow"))
        cm.close()
        return 0

    print(c("\n📊 总览", "cyan"))
    print(f"  总记忆数:    {total}")
    print(f"  已聚类:      {result['clustered_memories']}")
    print(f"  未聚类:      {result['unclustered']}")
    print(f"  主题簇数:    {len(result['clusters'])}")

    clusters = result.get("clusters", [])
    if not clusters:
        print(c("\n⚠️  未识别出明确主题簇", "yellow"))
        cm.close()
        return 0

    print(c("\n🏷️  主题簇列表（按规模排序）", "cyan"))
    print("-" * 75)
    print(f"{'#':<4}{'规模':<6}{'权重':<8}{'主题标签'}")
    print("-" * 75)
    for cl in clusters:
        print(f"{cl['cluster_id']:<4}{cl['size']:<6}{cl['total_weight']:<8}"
              f"{cl['label'][:55]}")

    # 最大簇的 Top 词
    largest = clusters[0]
    print(c(f"\n📌 最大簇 Top-8 关键词：{largest['label'][:30]}", "green"))
    print("  " + " · ".join(largest["top_words"][:8]))

    cm.close()
    return 0


def cmd_agent_insight(args):
    """Agent 行为洞察（v5.3.5 新增）"""
    cm = _get_memory(args)

    try:
        result = cm.agent_insight(args.agent, args.days)
    except (ValueError, TypeError) as e:
        if getattr(args, 'json_output', False):
            _json_out({"error": str(e)})
        else:
            print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if getattr(args, 'json_output', False):
        _json_out(result)
        cm.close()
        return 0

    print(c("\n🔍 混合检索增强（查询扩展 + Cross-Encoder 重排）（v5.3.9）", "bold"))
    print("=" * 60)
    

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(c("\n🧠 Agent 行为洞察（v5.3.5）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:   {args.agent}")
    print(f"  回溯天数:   {args.days}")

    total = result["total_memories"]
    if total == 0:
        print(c(f"\n⚠️  该 Agent 在 {args.days} 天内无记忆", "yellow"))
        cm.close()
        return 0

    act = result["activity"]
    print(c("\n📊 活跃度统计", "cyan"))
    print(f"  总记忆数:    {total}")
    print(f"  总访问次数:  {act['total_accesses']}")
    avg_access = f"{act['avg_access_per_memory']} 次/条"
    print(f"  平均访问:    {avg_access}")
    print(f"  平均内容长:  {act['avg_content_length']} 字")

    # 按周趋势
    wb = act.get("trend_by_week", {})
    if wb:
        print(c(f"\n📈 按周趋势（近 {args.days} 天）", "cyan"))
        sorted_w = sorted(wb.items(), key=lambda x: x[0])
        max_v = max(wb.values()) or 1
        for wk, cnt in sorted_w:
            bar = "█" * max(1, int(cnt / max_v * 20))
            print(f"  {wk:<8}  {cnt:>5} 条  {bar}")

    print(c("\n📦 记忆层分布", "cyan"))
    for layer, cnt in sorted(result["layer_distribution"].items(), key=lambda x: -x[1]):
        pct = cnt / total * 100
        bar = "█" * int(pct / 5)
        print(f"  {layer:<16} {cnt:>5}  ({pct:>5.1f}%)  {bar}")

    print(c("\n⭐ 重要度分布", "cyan"))
    for imp, cnt in sorted(result["importance_distribution"].items(), key=lambda x: -x[1]):
        pct = cnt / total * 100
        print(f"  {imp:<10} {cnt:>5}  ({pct:>5.1f}%)")

    tags = result.get("tag_preferences", [])
    if tags:
        print(c("\n🏷️  Top 标签偏好", "cyan"))
        for t in tags[:8]:
            pct_s = f"{t['ratio'] * 100:.1f}%"
            print(f"  {t['tag']:<20} {t['count']:>5} 次  ({pct_s:>6})")

    insights = result.get("insights", [])
    if insights:
        print(c("\n💡 智能洞察", "green"))
        for s in insights:
            print(f"  ✨ {s}")

    cm.close()
    return 0


def cmd_drama_summary(args):
    """短剧剧情摘要（v5.3.5 新增）"""
    cm = _get_memory(args)
    print(c("\n🎬 短剧剧情摘要（v5.3.5）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")
    print(f"  摘要长度:   {args.max_length} 字")

    try:
        result = cm.drama_summary(args.drama_id, args.max_length)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"\n  剧名:       {result['title']}")
    print(f"  类型:       {result['genre']}")
    print(f"  集数:       {result['current_episode']}/{result['episodes']}")
    print(f"  状态:       {result['status']}")
    rating_s = f"{result['rating']:.1f}" if result.get("rating") else "-"
    print(f"  评分:       {rating_s}")
    src = {"stored": "官方", "derived": "自动生成"}.get(result.get("summary_source"), "未知")
    print(f"  摘要来源:   {src}")
    print(f"  场景总数:   {result['total_scenes']}")
    print(f"  关键场景:   {result['key_scene_count']}")

    chars = result.get("characters", [])
    if chars:
        print(c(f"\n👥 核心角色（Top-{len(chars)}）", "cyan"))
        for ch in chars:
            print(f"  · {ch['name']:<14} 台词 {ch['lines']} 句")

    print(c("\n📖 剧情摘要", "cyan"))
    print("-" * 60)
    summary = result.get("summary", "(无)")
    # 格式化换行（每 60 字左右）
    wrapped = []
    for line in summary.split("\n"):
        cur = ""
        for ch in line:
            cur += ch
            if len(cur) >= 60:
                wrapped.append(cur)
                cur = ""
        if cur:
            wrapped.append(cur)
    for ln in wrapped:
        print(f"  {ln}")

    quotes = result.get("classic_quotes", [])
    if quotes:
        print(c("\n🎭 经典台词", "green"))
        for q in quotes:
            print(f"  💬 {q}")

    cm.close()
    return 0


def cmd_scene_tension(args):
    """场景张力分析（v5.3.5 新增）"""
    cm = _get_memory(args)
    print(c("\n🎬 场景张力分析（v5.3.5）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")
    print(f"  Top-K:      {args.top_k}")

    try:
        result = cm.scene_tension(args.drama_id, args.top_k)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    total = result["total_scenes"]
    if total == 0:
        print(c("\n⚠️  该短剧无场景数据", "yellow"))
        cm.close()
        return 0

    print(f"\n  剧名:       {result['title']}")
    print(f"  场景总数:   {total}")
    avg_s = f"{result['avg_tension']:.1f}"
    print(f"  平均张力:   {avg_s} / 100")

    # 主高潮
    mc = result.get("main_climax")
    if mc:
        print(c("\n🌋 主高潮段", "red"))
        peak_s = f"{mc['peak_tension']:.1f}"
        print(f"  集数:       第 {mc['episodes']} 集")
        print(f"  张力峰值:   {peak_s}")
        print(f"  场景范围:   {mc['description']}")

    # Top-K 高张力场景
    top = result.get("top_tension_scenes", [])
    if top:
        print(c(f"\n🔥 Top-{len(top)} 高张力场景", "red"))
        print("-" * 85)
        print(f"{'#':<4}{'集':<5}{'场景标题':<22}{'台词':<6}{'角色':<6}{'冲突':<6}{'强度':<6}{'张力':<8}{'关键'}")
        print("-" * 85)
        for i, sc in enumerate(top, 1):
            key_m = "★" if sc.get("is_key_scene") else ""
            title = (sc.get("scene_title") or "")[:20]
            ep = sc.get("episode") or "-"
            print(f"{i:<4}{str(ep):<5}{title:<22}{sc['line_count']:<6}"
                  f"{sc['character_count']:<6}{sc['conflict_hits']:<6}"
                  f"{sc['intensity_hits']:<6}{sc['tension']:<8.1f}{key_m}")

    # 张力曲线：ASCII 可视化（按顺序）
    curve = result.get("tension_curve", [])
    if curve and len(curve) <= 80:
        print(c("\n📉 张力曲线（按场景顺序）", "cyan"))
        # 每场景一行精简
        max_t = max((c["tension"] for c in curve), default=1) or 1
        for cp in curve:
            idx = cp.get("order") or "-"
            # 30 格柱状
            bar_len = int(cp["tension"] / max(max_t, 0.01) * 30)
            bar = "█" * bar_len
            color = "red" if cp["tension"] >= 60 else ("yellow" if cp["tension"] >= 35 else "cyan")
            print(f"  S{str(idx):<4} {c(bar, color)}  {cp['tension']:.0f}")

    cm.close()
    return 0


def cmd_memory_link(args):
    """记忆关联推理（v5.3.6 新增）"""
    cm = _get_memory(args)
    print(c("\n🔗 记忆关联推理（v5.3.6）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  目标记忆:    {args.memory_id}")
    print(f"  Top-K:       {args.top_k}")
    print(f"  回溯天数:    {args.days}")

    try:
        result = cm.memory_link(args.agent, args.memory_id, args.top_k, args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    total_cand = result["total_candidates"]
    if total_cand == 0:
        print(c(f"\n⚠️  该 Agent 在 {args.days} 天内无其他记忆", "yellow"))
        cm.close()
        return 0

    print(c("\n📊 总览", "cyan"))
    print(f"  候选记忆:    {total_cand}")
    print(f"  关联总数:    {result['total_links']}")
    print(f"  返回数量:    {result['returned']}")

    links = result.get("links", [])
    if not links:
        print(c("\n⚠️  未发现显著关联记忆", "yellow"))
        cm.close()
        return 0

    # 关联类型分布
    tdist = result.get("link_type_distribution", {})
    if tdist:
        print(c("\n🏷️  关联类型分布", "cyan"))
        type_label = {"keyword": "关键词", "tag": "标签", "temporal": "时间", "weak": "弱关联"}
        for t, cnt in sorted(tdist.items(), key=lambda x: -x[1]):
            print(f"  {type_label.get(t, t):<10} {cnt:>5} 条")

    # Top-K 关联记忆
    print(c(f"\n🔗 Top-{len(links)} 关联记忆", "cyan"))
    print("-" * 80)
    print(f"{'#':<4}{'强度':<8}{'类型':<14}{'时间差(天)':<12}{'预览'}")
    print("-" * 80)
    for i, lk in enumerate(links, 1):
        types_s = "/".join(lk["link_types"])[:12]
        td = f"{lk['time_diff_days']:.1f}"
        prev = (lk.get("content_preview") or "")[:38]
        print(f"{i:<4}{lk['strength']:<8.3f}{types_s:<14}{td:<12}{prev}")

    # 最强关联详情
    strongest = result.get("strongest_link")
    if strongest:
        print(c("\n💪 最强关联", "green"))
        print(f"  关联强度:    {strongest['strength']:.3f}")
        print(f"  关联类型:    {' / '.join(strongest['link_types'])}")
        sk = strongest.get("shared_keywords", [])
        if sk:
            print(f"  共享关键词:  {' · '.join(sk[:8])}")
        st = strongest.get("shared_tags", [])
        if st:
            print(f"  共享标签:    {' · '.join(st[:6])}")

    cm.close()
    return 0


def cmd_memory_recall(args):
    """智能记忆召回（v5.3.6 新增）"""
    cm = _get_memory(args)
    try:
        result = cm.memory_recall(args.agent, args.query, args.top_k, args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    if getattr(args, 'json_output', False):
        _json_out(result)
        cm.close()
        return 0

    print(c("\n🔎 智能记忆召回（v5.3.6）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  查询:        {args.query}")
    print(f"  Top-K:       {args.top_k}")
    print(f"  回溯天数:    {args.days}")

    

    qk = result.get("query_keywords", [])
    if qk:
        print(c(f"\n🔑 查询关键词: {' · '.join(qk[:12])}", "cyan"))

    scanned = result["total_scanned"]
    matched = result["total_matched"]
    print(f"\n  扫描记忆:    {scanned}")
    print(f"  匹配记忆:    {matched}")

    recalled = result.get("recalled", [])
    if not recalled:
        print(c("\n⚠️  无匹配记忆", "yellow"))
        cm.close()
        return 0

    avg_s = f"{result.get('avg_score', 0):.1f}"
    print(f"  平均召回分:  {avg_s}")

    print(c(f"\n🏆 Top-{len(recalled)} 召回记忆", "cyan"))
    print("-" * 90)
    print(f"{'#':<4}{'分数':<8}{'覆盖':<8}{'重要度':<10}{'访问':<6}{'年龄(天)':<10}{'预览'}")
    print("-" * 90)
    for i, rc in enumerate(recalled, 1):
        imp = rc["importance"][:8]
        cov = f"{rc['coverage']:.0%}"
        print(f"{i:<4}{rc['score']:<8.1f}{cov:<8}{imp:<10}{rc['access_count']:<6}"
              f"{rc['age_days']:<10.1f}{(rc.get('content_preview') or '')[:36]}")

    # Top 1 匹配关键词
    if recalled:
        top1 = recalled[0]
        mk = top1.get("matched_keywords", [])
        if mk:
            star = " ⭐" if top1.get("starred") else ""
            print(c(f"\n📌 Top-1 匹配关键词：{' · '.join(mk[:10])}{star}", "green"))

    cm.close()
    return 0


def cmd_drama_pacing(args):
    """剧集节奏分析（v5.3.6 新增）"""
    cm = _get_memory(args)
    print(c("\n🎬 剧集节奏分析（v5.3.6）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")
    print(f"  窗口大小:   {args.window}")

    try:
        result = cm.drama_pacing(args.drama_id, args.window)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    total = result["total_scenes"]
    if total == 0:
        print(c("\n⚠️  该短剧无场景数据", "yellow"))
        cm.close()
        return 0

    print(f"\n  剧名:       {result['title']}")
    print(f"  场景总数:   {total}")

    # 节奏健康度
    health = result["health_score"]
    hcolor = "green" if health >= 60 else ("yellow" if health >= 30 else "red")
    print(f"  节奏健康度: {c(f'{health:.1f} / 100', hcolor)}")

    # 节奏分布
    dist = result["pacing_distribution"]
    print(c("\n📊 节奏分布", "cyan"))
    pace_label = {"fast": "快节奏", "medium": "中节奏", "slow": "慢节奏"}
    pace_color = {"fast": "red", "medium": "green", "slow": "yellow"}
    for p in ["fast", "medium", "slow"]:
        cnt = dist.get(p, 0)
        pct = cnt / total * 100 if total > 0 else 0
        bar = "█" * int(pct / 5)
        print(f"  {pace_label[p]}  {cnt:>5}  ({pct:>5.1f}%)  {c(bar, pace_color[p])}")

    # 拖沓段
    slow_segs = result.get("slow_segments", [])
    if slow_segs:
        print(c("\n🐢 拖沓段（连续慢节奏）", "yellow"))
        for seg in slow_segs:
            print(f"  第 {seg['episodes']} 集  长度 {seg['length']} 场景  "
                  f"平均密度 {seg['avg_density']:.2f}")

    # 密集段
    fast_segs = result.get("fast_segments", [])
    if fast_segs:
        print(c("\n🔥 密集段（连续快节奏）", "red"))
        for seg in fast_segs:
            print(f"  第 {seg['episodes']} 集  长度 {seg['length']} 场景  "
                  f"平均密度 {seg['avg_density']:.2f}")

    # 节奏曲线（ASCII 可视化）
    curve = result.get("pacing_curve", [])
    if curve and len(curve) <= 80:
        print(c("\n📉 节奏曲线（按场景顺序）", "cyan"))
        for cp in curve:
            idx = cp.get("order") or "-"
            density = cp.get("avg_density", 0)
            bar_len = int(density * 30)
            bar = "█" * bar_len
            color = pace_color.get(cp["pace"], "cyan")
            print(f"  S{str(idx):<4} {c(bar, color)}  {density:.2f}  [{cp['pace']}]")

    # 洞察
    insights = result.get("insights", [])
    if insights:
        print(c("\n💡 节奏洞察", "cyan"))
        for ins in insights:
            print(f"  • {ins}")

    cm.close()
    return 0


def cmd_char_interaction(args):
    """角色互动分析（v5.3.6 新增）"""
    cm = _get_memory(args)
    print(c("\n🤝 角色互动分析（v5.3.6）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")
    print(f"  Top-K:      {args.top_k}")

    try:
        result = cm.char_interaction(args.drama_id, args.top_k)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    total_chars = result["total_characters"]
    if total_chars == 0:
        print(c("\n⚠️  该短剧无角色数据", "yellow"))
        cm.close()
        return 0

    print(f"\n  剧名:       {result['title']}")
    print(f"  角色总数:   {total_chars}")
    print(f"  互动对数:   {result['total_pairs']}")

    # 核心角色
    core = result.get("core_characters", [])
    if core:
        print(c("\n⭐ 核心角色（互动强度 Top-3）", "cyan"))
        for cc in core:
            print(f"  {cc['name']:<16} 总强度 {cc['total_strength']:.1f}")

    # Top-K 互动关系
    interactions = result.get("interactions", [])
    if not interactions:
        print(c("\n⚠️  无角色互动数据", "yellow"))
        cm.close()
        return 0

    rel_label = {
        "antagonist": "对抗", "close": "亲密",
        "frequent": "频繁", "casual": "偶发",
    }
    print(c(f"\n🔗 Top-{len(interactions)} 互动关系", "cyan"))
    print("-" * 85)
    print(f"{'#':<4}{'角色A':<14}{'角色B':<14}{'共现':<6}{'交替':<6}{'冲突':<6}{'强度':<8}{'关系'}")
    print("-" * 85)
    for i, it in enumerate(interactions, 1):
        na = (it.get("name_a") or "")[:12]
        nb = (it.get("name_b") or "")[:12]
        rel = rel_label.get(it["relation_type"], it["relation_type"])
        print(f"{i:<4}{na:<14}{nb:<14}{it['co_scenes']:<6}"
              f"{it['alternations']:<6}{it['conflict_hits']:<6}"
              f"{it['strength']:<8.1f}{rel}")

    cm.close()
    return 0


def cmd_quality(args):
    """记忆质量评分（v5.2.2 新增）"""
    cm = _get_memory(args)

    if args.memory_id:
        # 单个记忆评分
        result = cm.quality_score(args.memory_id)
        if not result:
            print(c(f"❌ 记忆不存在: {args.memory_id}", "red"))
            cm.close()
            return 1

        print(c("\n📊 记忆质量评分", "bold"))
        print("=" * 50)
        print(f"记忆 ID:   {result['memory_id'][:16]}...")
        print(f"总评分:     {c(str(result['total_score']) + '/100', 'green')}")
        print(f"等  级:     {c(result['grade'], 'cyan')}")

        print("\n各项得分:")
        for item, score in result['breakdown'].items():
            print(f"  {item}: {score}")

    else:
        # 批量评分
        print(c("\n📊 批量质量评分", "bold"))
        print("=" * 50)
        if args.category:
            print(f"分类过滤:   {args.category}")
        print(f"数量限制:   {args.limit}")

        result = cm.batch_quality_score(category=args.category, limit=args.limit)

        print("\n统计结果:")
        print(f"  总  数:   {result['total']}")
        print(f"  平均分:   {c(str(result['average_score']), 'green')}")

        print("\n等级分布:")
        for grade, count in result['grades'].items():
            if count > 0:
                print(f"  {grade}: {count} 条")

        if result.get('top_scores'):
            print("\n🏆 高分记忆 Top 5:")
            for i, s in enumerate(result['top_scores'][:5], 1):
                print(f"  {i}. {s['memory_id'][:16]}... - {s['total_score']}分 ({s['grade']})")

    cm.close()
    return 0


def cmd_similar(args):
    """相似度分析（v5.2.2 新增）"""
    cm = _get_memory(args)

    entry = cm.get(args.memory_id)
    if not entry:
        print(c(f"❌ 记忆不存在: {args.memory_id}", "red"))
        cm.close()
        return 1

    print(c("\n🔍 相似度分析", "bold"))
    print("=" * 50)
    print(f"目标记忆:   {args.memory_id[:16]}...")
    print(f"内容预览:   {entry.content[:60]}...")

    results = cm.analyze_similarity(
        memory_id=args.memory_id,
        limit=args.limit,
        min_similarity=args.min_similarity
    )

    if not results:
        print(f"\n未找到相似度 >= {args.min_similarity} 的记忆")
    else:
        print(f"\n找到 {len(results)} 条相似记忆:")
        for i, r in enumerate(results, 1):
            print(f"\n{i}. [{c(str(r['similarity']), 'cyan')}] {r['content_preview']}")
            print(f"   ID: {r['memory_id'][:16]}... | 分类: {r['category']} | 层级: {r['layer']}")

    cm.close()
    return 0


# ===== v5.2.4 新增命令处理函数 =====


def cmd_note_add(args):
    """添加记忆笔记（v5.2.4 新增）"""
    cm = _get_memory(args)
    result = cm.add_note(args.memory_id, args.content, author=args.author, tags=args.tags)
    if result.get("success"):
        print(c("\n✅ 笔记已添加", "green"))
        print(f"   笔记 ID: {result['note_id']}")
        print(f"   记忆 ID: {result['memory_id']}")
    else:
        print(c(f"\n❌ {result.get('error', '添加失败')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_note_list(args):
    """列出记忆笔记（v5.2.4 新增）"""
    cm = _get_memory(args)
    notes = cm.list_notes(args.memory_id, limit=args.limit)
    if not notes:
        print(c("\n📝 暂无笔记", "yellow"))
        cm.close()
        return 0

    print(c(f"\n📝 记忆笔记（共 {len(notes)} 条）", "bold"))
    print("=" * 60)
    for note in notes:
        print(f"  {c(note['id'][:8], 'cyan')} | {format_time(note['created_at'])}")
        print(f"    {note['content'][:80]}")
        if note.get("author"):
            print(f"    作者: {note['author']}")
        if note.get("tags"):
            print(f"    标签: {', '.join(note['tags'])}")
        print()
    cm.close()
    return 0


def cmd_note_delete(args):
    """删除笔记（v5.2.4 新增）"""
    cm = _get_memory(args)
    if not args.force:
        print(c(f"\n⚠️  确认删除笔记 {args.note_id[:8]}...？", "yellow"))
        print("   使用 --force 参数确认删除")
        cm.close()
        return 1

    result = cm.delete_note(args.note_id)
    if result.get("success"):
        print(c("\n✅ 笔记已删除", "green"))
    else:
        print(c(f"\n❌ {result.get('error', '删除失败')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_template_add(args):
    """添加记忆模板（v5.2.4 新增）"""
    cm = _get_memory(args)
    result = cm.add_template(
        name=args.name,
        content_template=args.content,
        category=args.category,
        tags=args.tags,
        importance=args.importance,
        layer=args.layer,
        description=args.description,
    )
    if result.get("success"):
        print(c("\n✅ 模板已创建", "green"))
        print(f"   模板 ID: {result['template_id']}")
        print(f"   名称: {result['name']}")
    else:
        print(c(f"\n❌ {result.get('error', '创建失败')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_template_list(args):
    """列出记忆模板（v5.2.4 新增）"""
    cm = _get_memory(args)
    templates = cm.list_templates(category=args.category, limit=args.limit)
    if not templates:
        print(c("\n📋 暂无模板", "yellow"))
        cm.close()
        return 0

    print(c(f"\n📋 记忆模板（共 {len(templates)} 个）", "bold"))
    print("=" * 60)
    for t in templates:
        print(f"  {c(t['id'][:8], 'cyan')} | {c(t['name'], 'bold')} | 使用 {t['use_count']} 次")
        print(f"    分类: {t['category']} | 重要性: {t['importance']} | 层级: {t['layer']}")
        print(f"    模板: {t['content_template'][:60]}...")
        if t.get("description"):
            print(f"    描述: {t['description']}")
        print()
    cm.close()
    return 0


def cmd_template_use(args):
    """使用模板创建记忆（v5.2.4 新增）"""
    cm = _get_memory(args)

    # 解析变量
    variables = {}
    if args.var:
        for v in args.var:
            if "=" in v:
                key, value = v.split("=", 1)
                variables[key] = value

    result = cm.use_template(args.template_id, variables=variables,
                             actor=args.agent, session_id=args.session)
    if result.get("success"):
        print(c("\n✅ 记忆已创建", "green"))
        print(f"   记忆 ID: {result['memory_id']}")
        print(f"   使用模板: {result['template_name']}")
        print(f"   内容: {result['content'][:80]}")
    else:
        print(c(f"\n❌ {result.get('error', '创建失败')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_template_delete(args):
    """删除模板（v5.2.4 新增）"""
    cm = _get_memory(args)
    if not args.force:
        print(c(f"\n⚠️  确认删除模板 {args.template_id[:8]}...？", "yellow"))
        print("   使用 --force 参数确认删除")
        cm.close()
        return 1

    result = cm.delete_template(args.template_id)
    if result.get("success"):
        print(c(f"\n✅ 模板已删除: {result.get('name', '')}", "green"))
    else:
        print(c(f"\n❌ {result.get('error', '删除失败')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_batch_update(args):
    """批量更新记忆（v5.2.4 新增）"""
    cm = _get_memory(args)

    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    if not ids:
        print(c("\n❌ 未指定有效的记忆 ID", "red"))
        cm.close()
        return 1

    starred = None
    if args.star:
        starred = True
    elif args.unstar:
        starred = False

    if not args.force:
        print(c(f"\n⚠️  即将批量更新 {len(ids)} 条记忆", "yellow"))
        changes = []
        if args.category:
            changes.append(f"分类 → {args.category}")
        if args.tags:
            changes.append(f"标签 → {', '.join(args.tags)}")
        if args.importance:
            changes.append(f"重要性 → {args.importance}")
        if args.layer:
            changes.append(f"层级 → {args.layer}")
        if starred is not None:
            changes.append(f"收藏 → {'是' if starred else '否'}")
        if changes:
            print(f"   变更: {'; '.join(changes)}")
        print("   使用 --force 参数确认执行")
        cm.close()
        return 1

    result = cm.batch_update(
        memory_ids=ids,
        category=args.category,
        tags=args.tags,
        importance=args.importance,
        layer=args.layer,
        starred=starred,
        actor=args.agent,
        session_id=args.session,
    )
    if result.get("success"):
        print(c("\n✅ 批量更新完成", "green"))
        print(f"   成功更新: {result['updated']}/{result['total']} 条")
        if result.get("errors"):
            print(c(f"   失败: {len(result['errors'])} 条（ID 不存在）", "yellow"))
    else:
        print(c(f"\n❌ {result.get('error', '更新失败')}", "red"))
    cm.close()
    return 0 if result.get("success") else 1


def cmd_schedule(args):
    """复习计划管理（v5.2.4 新增）"""
    cm = _get_memory(args)

    if args.schedule_action == "create":
        if not args.memory_id:
            print(c("\n❌ 创建复习计划需要 --memory-id 参数", "red"))
            cm.close()
            return 1
        result = cm.create_review_schedule(args.memory_id, interval_days=args.interval)
        if result.get("success"):
            print(c("\n✅ 复习计划已创建", "green"))
            print(f"   计划 ID: {result['schedule_id']}")
            print(f"   记忆 ID: {result['memory_id']}")
            print(f"   间隔: {result['interval_days']} 天")
            print(f"   下次复习: {format_time(result['scheduled_at'])}")
        else:
            print(c(f"\n❌ {result.get('error', '创建失败')}", "red"))
        cm.close()
        return 0 if result.get("success") else 1

    elif args.schedule_action == "list":
        reviews = cm.list_due_reviews(limit=args.limit)
        if not reviews:
            print(c("\n🎉 暂无到期复习", "green"))
            cm.close()
            return 0

        print(c(f"\n📅 到期复习（共 {len(reviews)} 条）", "bold"))
        print("=" * 60)
        for r in reviews:
            print(f"  {c(r['schedule_id'][:8], 'cyan')} | 已复习 {r['review_count']} 次 | 间隔 {r['interval_days']} 天")
            print(f"    记忆: {r['content'][:60]}")
            print(f"    分类: {r['category']} | 重要性: {r['importance']}")
            print(f"    计划时间: {format_time(r['scheduled_at'])}")
            print()
        cm.close()
        return 0

    elif args.schedule_action == "review":
        if not args.schedule_id:
            print(c("\n❌ 完成复习需要 --schedule-id 参数", "red"))
            cm.close()
            return 1
        result = cm.complete_review(args.schedule_id)
        if result.get("success"):
            print(c("\n✅ 复习完成！", "green"))
            print(f"   累计复习: {result['review_count']} 次")
            print(f"   下次间隔: {result['next_interval_days']} 天")
            print(f"   下次复习: {format_time(result['next_scheduled_at'])}")
        else:
            print(c(f"\n❌ {result.get('error', '操作失败')}", "red"))
        cm.close()
        return 0 if result.get("success") else 1

    elif args.schedule_action == "stats":
        stats = cm.get_review_stats()
        print(c("\n📊 复习计划统计", "bold"))
        print("=" * 40)
        print(f"   总计划数: {stats['total_schedules']}")
        print(f"   待复习: {stats['pending']}")
        print(c(f"   已到期: {stats['due_now']}", "yellow" if stats['due_now'] > 0 else "green"))
        print(f"   累计完成复习: {stats['total_reviews_completed']} 次")
        cm.close()
        return 0

    cm.close()
    return 1


def cmd_agent_influence(args):
    """Agent 记忆影响力图谱（v5.4.3 新增）"""
    cm = _get_memory(args)
    print(c("\n🕸️  Agent 记忆影响力图谱（v5.4.3）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  回溯天数:    {args.days}")

    try:
        result = cm.agent_influence_map(args.agent, args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"  总节点数:    {result['total_nodes']}")
    print(f"  总边数:      {result['total_edges']}")
    print(f"  自身入度:    {result['self_influence_in']}")
    print(f"  自身出度:    {result['self_influence_out']}")
    print(f"  影响力评分:  {result['influence_score']}")

    top = result.get("top_influencers", [])
    if top:
        print(c(f"\n🏆 核心影响力 Agent Top {len(top)}", "cyan"))
        print(f"{'Agent ID':<30}{'入度':>6}{'出度':>6}{'共享标签':>8}")
        print("-" * 60)
        for n in top:
            print(f"{n['agent_id'][:28]:<30}{n['influence_in']:>6}"
                  f"{n['influence_out']:>6}{n['shared_tags']:>8}")

    edges = result.get("edges", [])
    if edges:
        print(c("\n🔗 影响力边（前 20 条）", "cyan"))
        for e in edges[:20]:
            print(f"  {e['from'][:20]} → {e['to'][:20]}  [{e['type']}]")

    cm.close()
    return 0


def cmd_memory_overlap(args):
    """记忆重叠分析（v5.4.3 新增）"""
    cm = _get_memory(args)
    print(c("\n📊 记忆重叠分析（v5.4.3）", "bold"))
    print("=" * 60)
    print(f"  Agent A:     {args.agent_a}")
    print(f"  Agent B:     {args.agent_b}")
    print(f"  回溯天数:    {args.days}")

    try:
        result = cm.memory_overlap(args.agent_a, args.agent_b, args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"\n  综合相似度:  {result['overall_similarity']}  ({result['similarity_level']})")

    tags = result.get("tags", {})
    print(c("\n🏷️  标签重叠", "cyan"))
    print(f"  Jaccard:     {tags.get('jaccard', 0)}")
    print(f"  重叠率:      {tags.get('overlap_pct', 0)}%")
    print(f"  共享标签:    {', '.join(tags.get('shared', [])[:10]) or '无'}")
    print(f"  A 独有:      {', '.join(tags.get('unique_a', [])[:10]) or '无'}")
    print(f"  B 独有:      {', '.join(tags.get('unique_b', [])[:10]) or '无'}")

    cats = result.get("categories", {})
    print(c("\n📂 分类重叠", "cyan"))
    print(f"  Jaccard:     {cats.get('jaccard', 0)}")
    print(f"  共享分类:    {', '.join(cats.get('shared', [])) or '无'}")

    kw = result.get("keywords", {})
    print(c("\n🔑 关键词重叠", "cyan"))
    print(f"  Jaccard:     {kw.get('jaccard', 0)}")
    print(f"  共享词数:    {kw.get('shared_count', 0)}")
    print(f"  共享词:      {', '.join(kw.get('shared', [])[:10]) or '无'}")

    cm.close()
    return 0


def cmd_conflict_graph(args):
    """记忆冲突检测图（v5.4.3 新增）"""
    cm = _get_memory(args)
    print(c("\n⚔️  记忆冲突检测图（v5.4.3）", "bold"))
    print("=" * 60)
    print(f"  Agent ID:    {args.agent}")
    print(f"  回溯天数:    {args.days}")

    try:
        result = cm.conflict_graph(args.agent, args.days)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"  总记忆数:    {result['total_memories']}")
    print(f"  冲突数:      {result['conflict_count']}")
    print(f"  冲突密度:    {result['conflict_density']}")

    sev = result.get("severity_distribution", {})
    print(c("\n📋 严重度分布", "cyan"))
    print(f"  高:    {sev.get('high', 0)}")
    print(f"  中:    {sev.get('medium', 0)}")
    print(f"  低:    {sev.get('low', 0)}")

    conflicts = result.get("conflicts", [])
    if conflicts:
        print(c("\n⚠️  冲突详情（前 20 条）", "cyan"))
        for cf in conflicts[:20]:
            sev_label = {"high": "🔴高", "medium": "🟡中", "low": "🟢低"}.get(cf["severity"], "?")
            print(f"\n  {sev_label} [{cf['conflict_type']}] 重要度差: {cf['importance_diff']}")
            print(f"    A: [{cf['memory_a']['importance']}] {cf['memory_a']['content_preview']}")
            print(f"    B: [{cf['memory_b']['importance']}] {cf['memory_b']['content_preview']}")
            if cf.get("shared_tags"):
                print(c(f"    ↳ 共享标签: {', '.join(cf['shared_tags'])}", "cyan"))

    top_tags = result.get("top_conflict_tags", [])
    if top_tags:
        print(c("\n🏷️  冲突热点标签", "cyan"))
        print(f"  {', '.join(top_tags)}")

    cm.close()
    return 0


def cmd_drama_quote_map(args):
    """经典台词地图（v5.4.3 新增）"""
    cm = _get_memory(args)
    print(c("\n🗺️  经典台词地图（v5.4.3）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")

    try:
        result = cm.drama_quote_map(args.drama_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"  剧名:       {result['title']}")
    print(f"  总台词:     {result['total_lines']}")
    print(f"  经典台词:   {result['classic_count']}")
    print(f"  经典率:     {result['classic_ratio']}")
    print(f"  密度评级:   {result['density_rating']}")

    by_char = result.get("by_character", [])
    if by_char:
        print(c("\n🎭 角色经典台词贡献排行", "cyan"))
        print(f"{'角色':<16}{'总台词':>6}{'经典':>6}{'经典率':>8}")
        print("-" * 40)
        for ch in by_char:
            ratio = round(ch['classic'] / max(1, ch['total']) * 100, 1)
            print(f"{ch['name'][:14]:<16}{ch['total']:>6}{ch['classic']:>6}{ratio:>7}%")
            for cl in ch.get("classic_lines", [])[:2]:
                print(c(f"  \"{cl[:50]}\"", "cyan"))

    top_eps = result.get("top_episodes", [])
    if top_eps:
        print(c("\n📺 经典台词最多的集", "cyan"))
        for ep in top_eps:
            print(f"  EP{ep['episode']}:  {ep['classic_count']} 条经典台词")

    timeline = result.get("timeline", [])
    if timeline:
        print(c("\n📜 经典台词时间线（前 15 条）", "cyan"))
        for t in timeline[:15]:
            print(f"  EP{t['episode']}  [{t['character'][:10]}]  \"{t['text'][:40]}\"")

    cm.close()
    return 0


def cmd_char_growth(args):
    """角色成长深度分析（v5.4.3 新增）"""
    cm = _get_memory(args)
    print(c("\n🌱 角色成长深度分析（v5.4.3）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")
    print(f"  角色 ID:    {args.character_id}")

    try:
        result = cm.character_growth(args.drama_id, args.character_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"  角色名:     {result['character_name']}")
    print(f"  角色定位:   {result['character_role']}")
    print(f"  总台词:     {result['total_lines']}")
    print(f"  活跃集数:   {result['total_episodes_active']}")
    print(f"  活跃趋势:   {result['activity_trend']}")
    print(f"  成长评分:   {result['growth_score']}/100")

    emotion_arc = result.get("emotion_arc", [])
    if emotion_arc:
        print(c("\n💖 情感弧线", "cyan"))
        print(f"{'集':>4}{'台词数':>6}{'情感':>10}{'评分':>8}")
        print("-" * 32)
        for e in emotion_arc:
            print(f"{e['episode']:>4}{e['line_count']:>6}{e['emotion']:>10}{e['emotion_score']:>8}")

    complexity = result.get("complexity_curve", [])
    if complexity:
        print(c("\n🧠 对话复杂度曲线", "cyan"))
        print(f"{'集':>4}{'平均长度':>8}{'词汇量':>8}{'复杂度':>8}")
        print("-" * 32)
        for c_item in complexity:
            print(f"{c_item['episode']:>4}{c_item['avg_line_length']:>8}"
                  f"{c_item['vocabulary_size']:>8}{c_item['complexity_score']:>8}")

    stages = result.get("activity_stages", [])
    if stages:
        print(c("\n📊 活跃度阶段", "cyan"))
        for s in stages:
            print(f"  {s['stage']:<10} 台词数: {s['line_count']}")

    print(c(f"\n📝 {result.get('growth_summary', '')}", "yellow"))

    cm.close()
    return 0


def cmd_scene_rhythm(args):
    """场景节奏分析（v5.4.3 新增）"""
    cm = _get_memory(args)
    print(c("\n🎵 场景节奏分析（v5.4.3）", "bold"))
    print("=" * 60)
    print(f"  短剧 ID:    {args.drama_id}")

    try:
        result = cm.scene_rhythm(args.drama_id)
    except (ValueError, TypeError) as e:
        print(c(f"\n❌ 失败: {e}", "red"))
        cm.close()
        return 1

    if result.get("error"):
        print(c(f"\n❌ {result['error']}", "red"))
        cm.close()
        return 1

    print(f"  剧名:       {result['title']}")
    print(f"  总场景数:   {result['total_scenes']}")
    print(f"  整体节奏:   {result['overall_pace']}")
    print(f"  节奏变化度: {result['rhythm_variability']}")

    pace = result.get("pace_distribution", {})
    print(c("\n📋 节奏分布", "cyan"))
    print(f"  快节奏:     {pace.get('fast', 0)}")
    print(f"  中节奏:     {pace.get('moderate', 0)}")
    print(f"  慢节奏:     {pace.get('slow', 0)}")
    print(f"  无台词:     {pace.get('silent', 0)}")

    rhythms = result.get("scene_rhythms", [])
    if rhythms:
        print(c("\n🎬 各场景节奏（前 20 个）", "cyan"))
        print(f"{'集':>4}{'场景':>6}{'台词':>6}{'密度':>8}{'节奏':>10}  标题")
        print("-" * 60)
        for sr in rhythms[:20]:
            pace_label = {"fast": "⚡快", "moderate": "🎵中",
                          "slow": "🐢慢", "silent": "🔇无"}.get(sr["pace"], "?")
            print(f"{sr['episode']:>4}{sr['scene_number']:>6}{sr['line_count']:>6}"
                  f"{sr['density']:>8}{pace_label:>10}  {sr['title'][:20]}")

    for s in result.get("suggestions", []):
        print(c(f"\n  ✦ {s}", "yellow"))

    cm.close()
    return 0


if __name__ == "__main__":
    # v5.4.6 修复：入口必须位于文件末尾，
    # 否则 dispatch 表中引用的 cmd_agent_influence 等函数尚未定义
    main()
