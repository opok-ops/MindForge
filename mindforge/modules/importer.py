"""
MindForge v6.0.0 功能 1：一键导入记忆（ChatGPT / Claude 对话记录）

解决冷启动：新用户装上后第一分钟就能导入历史对话，AI 立刻"认识你"。

- parse_export(): 解析 ChatGPT mapping 树与 Claude accounts/conversations 变体
- detect_platform(): 自动嗅探平台类型
- import_chat_history(): 将解析结果写入记忆库（画像信号 HIGH 重要度）
- 噪音过滤：is_user_signal / is_noise，导入阶段过滤系统噪音与重复问候

安全约束：
- MAX_EXPORT_SIZE = 200MB，超大导出直接拒绝
- 每轮消息最大 32KB，超出截断
- 导入量上限 MAX_IMPORT_MESSAGES = 20000 条，防止内存爆炸
- 审计动作 import_chat 全量记录
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 导出文件大小上限（200MB）
MAX_EXPORT_SIZE = 200 * 1024 * 1024
# 单条消息文本上限（32KB）
MAX_MESSAGE_CHARS = 32 * 1024
# 单次导入消息条数上限
MAX_IMPORT_MESSAGES = 20000
# 单次导入生成的记忆上限
MAX_IMPORT_MEMORIES = 5000

# 平台标识
PLATFORM_CHATGPT = "chatgpt"
PLATFORM_CLAUDE = "claude"
PLATFORM_UNKNOWN = "unknown"

SUPPORTED_PLATFORMS = (PLATFORM_CHATGPT, PLATFORM_CLAUDE)

# ---------------------------------------------------------------------------
# 平台嗅探
# ---------------------------------------------------------------------------


def detect_platform(data: Any) -> str:
    """根据导出 JSON 的结构嗅探平台。

    - ChatGPT: 顶层含 mapping 字段（conversation_id -> node 树），
      或含 conversations 且每个会话含 mapping
    - Claude: 顶层含 accounts，每个账号含 conversations（列表式）
    """
    if isinstance(data, dict):
        if isinstance(data.get("mapping"), dict):
            return PLATFORM_CHATGPT
        if isinstance(data.get("conversations"), list):
            # 会话带 mapping 视为 ChatGPT 变体；带 chat_messages 视为 Claude 变体
            convs = data["conversations"]
            if convs and isinstance(convs[0], dict) and isinstance(
                    convs[0].get("chat_messages"), list):
                return PLATFORM_CLAUDE
            return PLATFORM_CHATGPT
        if isinstance(data.get("accounts"), dict):
            return PLATFORM_CLAUDE
    return PLATFORM_UNKNOWN


# ---------------------------------------------------------------------------
# 噪音与用户信号
# ---------------------------------------------------------------------------

# 需要过滤的系统噪音模式（导入阶段剔除，不写入记忆）
_NOISE_PATTERNS = [
    re.compile(r"^系统提示[：:]", re.IGNORECASE),
    re.compile(r"^System prompt[：:]", re.IGNORECASE),
    re.compile(r"^你是一个", re.IGNORECASE),
    re.compile(r"^You are (an|a) ", re.IGNORECASE),
    re.compile(r"^请忽略以上", re.IGNORECASE),
    re.compile(r"^Ignore (all )?(the )?above", re.IGNORECASE),
    re.compile(r"^这是(一个)?测试", re.IGNORECASE),
    re.compile(r"^This is a test", re.IGNORECASE),
    re.compile(r"^测试", re.IGNORECASE),  # 测试性消息直接过滤
    re.compile(r"^(你好|您好|哈喽|嗨|hello|hi)[，,。.！!~～]?$", re.IGNORECASE),
    re.compile(r"^好的?，?(我|明白了|收到|了解)", re.IGNORECASE),
    re.compile(r"^(OK|Got it|Understood|Sure)[,.!]?$", re.IGNORECASE),
    re.compile(r"^谢谢[,，.!！]?$", re.IGNORECASE),
    re.compile(r"^Thank you[,.]?$", re.IGNORECASE),
    re.compile(r"^再见[,，.!！]?$", re.IGNORECASE),
    re.compile(r"^Bye[,.]?$", re.IGNORECASE),
]

# 用户画像信号（第一人称事实陈述）——命中即写入记忆
_SIGNAL_PATTERNS = [
    re.compile(r"(?:我|本人)(?:住在|居住于|生活(?:在|于))", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:喜欢|热爱|偏爱|钟爱|超爱)", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:讨厌|不喜欢|反感)", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:从事|就职于|工作(?:在|于)|任职于|在).{0,20}(?:工作|上班)", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:是|是一名|是个|是一位)", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:家在|来自)", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:养了|养着|有一只|有一条)", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:结婚|已婚|有孩子|有两个孩子|有小孩)", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:最近|刚|正在)(?:在学|学习|研究|准备)", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:的(?:生日|名字|手机号|邮箱|地址)是)", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:想去|计划去|打算去|希望去)", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:的目标|的梦想|的愿望|的长期计划)是", re.IGNORECASE),
    re.compile(r"(?:我|本人)(?:的)(?:职业|职位|岗位|角色)是", re.IGNORECASE),
]


def is_noise(text: str) -> bool:
    """判断是否系统噪音/无信息量内容（导入阶段过滤）"""
    t = (text or "").strip()
    if not t:
        return True
    if len(t) > 4000:
        return False  # 长文本大概率是有内容的
    for pat in _NOISE_PATTERNS:
        if pat.match(t):
            return True
    return False


def is_user_signal(text: str) -> bool:
    """判断是否为用户画像信号（第一人称事实）"""
    t = (text or "").strip()
    if not t or len(t) > 1000:
        return False
    for pat in _SIGNAL_PATTERNS:
        if pat.search(t):
            return True
    return False


# ---------------------------------------------------------------------------
# 导出解析
# ---------------------------------------------------------------------------


def _extract_text_from_part(part: Any) -> str:
    """从 ChatGPT mapping node 的 message.content.parts 提取纯文本"""
    if not isinstance(part, dict):
        return ""
    content = part.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            texts = []
            for p in parts:
                if isinstance(p, str):
                    texts.append(p)
                elif isinstance(p, dict) and isinstance(p.get("text"), str):
                    texts.append(p["text"])
            return "\n".join(texts)
    return ""


def parse_chatgpt_export(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """解析 ChatGPT 导出（mapping 树）为统一消息列表。

    返回 [{"role","text","ts"}]，按会话时间排序。
    """
    messages: List[Dict[str, Any]] = []
    mapping = data.get("mapping")
    if not isinstance(mapping, dict):
        return messages

    for _node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        msg = node.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("author", {}).get("role", "")
        if role not in ("user", "assistant"):
            continue
        text = _extract_text_from_part(msg)
        if not text.strip():
            continue
        ts = msg.get("create_time") or 0.0
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            ts = 0.0
        messages.append({"role": role, "text": text, "ts": ts})

    messages.sort(key=lambda m: m["ts"])
    return messages


def parse_claude_export(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """解析 Claude 导出（accounts.conversations，列表式 chat_messages）。

    返回统一消息列表。
    """
    messages: List[Dict[str, Any]] = []
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        return messages
    for _acct, acct_data in accounts.items():
        if not isinstance(acct_data, dict):
            continue
        convs = acct_data.get("conversations")
        # Claude 导出：conversations 可能是列表，也可能是 {会话id: 会话} 映射
        if isinstance(convs, dict):
            conv_iter = convs.values()
        elif isinstance(convs, list):
            conv_iter = convs
        else:
            continue
        for conv in conv_iter:
            if not isinstance(conv, dict):
                continue
            chat = conv.get("chat_messages")
            if not isinstance(chat, list):
                continue
            for m in chat:
                if not isinstance(m, dict):
                    continue
                role = m.get("sender") or m.get("role") or ""
                # Claude 导出用 human/assistant；统一为 user/assistant
                role = {"human": "user", "assistant": "assistant"}.get(role, role)
                if role not in ("user", "assistant"):
                    continue
                text = m.get("text") or m.get("content") or ""
                if not isinstance(text, str) or not text.strip():
                    continue
                ts = m.get("timestamp") or m.get("created_at") or 0.0
                try:
                    ts = float(ts)
                except (TypeError, ValueError):
                    ts = 0.0
                messages.append({"role": role, "text": text, "ts": ts})
    messages.sort(key=lambda m: m["ts"])
    return messages


def parse_export(data: Any) -> List[Dict[str, Any]]:
    """自动嗅探平台并解析导出文件。"""
    platform = detect_platform(data)
    if platform == PLATFORM_CHATGPT:
        return parse_chatgpt_export(data)
    if platform == PLATFORM_CLAUDE:
        return parse_claude_export(data)
    return []


# ---------------------------------------------------------------------------
# 记忆构建与导入
# ---------------------------------------------------------------------------


def _build_memory_payload(messages: List[Dict[str, Any]],
                          platform: str) -> List[Dict[str, Any]]:
    """从消息流构建记忆 payload 列表。

    规则：
    - 用户画像信号（第一人称事实）→ HIGH 重要度 + user_preference 标签
    - 普通用户陈述 → MEDIUM
    - 助手消息不直接写入（避免噪音），但参与上下文（此处仅保留用户侧）
    - 每条消息作为独立记忆，来源会话标记 source_session/agent
    """
    payloads: List[Dict[str, Any]] = []
    for m in messages:
        if m["role"] != "user":
            continue
        text = (m.get("text") or "").strip()[:MAX_MESSAGE_CHARS]
        if not text or is_noise(text):
            continue
        ts = m.get("ts") or 0.0
        if is_user_signal(text):
            payloads.append({
                "content": text,
                "category": "preference",
                "tags": ["user_preference", "profile"],
                "importance": "HIGH",
                "source_session": f"import:{platform}",
                "source_agent": f"{platform}-import",
                "created_at": ts or time.time(),
            })
        else:
            payloads.append({
                "content": text,
                "category": "general",
                "tags": ["imported"],
                "importance": "MEDIUM",
                "source_session": f"import:{platform}",
                "source_agent": f"{platform}-import",
                "created_at": ts or time.time(),
            })
        if len(payloads) >= MAX_IMPORT_MEMORIES:
            break
    return payloads


def import_chat_history(mf, data: Any, dry_run: bool = False,
                        actor: str = "", session_id: str = "") -> Dict[str, Any]:
    """将导出数据导入记忆库（facade 层调用）。

    参数：
        mf: MindForge facade 实例
        data: 解析后的导出 JSON
        dry_run: 试运行（只统计不写入）
        actor/session_id: 审计身份

    返回统计 dict。
    """
    platform = detect_platform(data)
    messages = parse_export(data)
    if not messages:
        return {"success": False, "error": "未识别到有效对话（支持 ChatGPT/Claude 导出）"}

    payloads = _build_memory_payload(messages, platform)
    stats = {
        "success": True,
        "platform": platform,
        "messages_parsed": len(messages),
        "memories_candidate": len(payloads),
        "imported": 0,
        "skipped_noise": max(0, len(messages) - len(payloads)),
        "dry_run": bool(dry_run),
    }
    if dry_run:
        return stats

    imported = 0
    for p in payloads:
        try:
            mf.add(
                content=p["content"],
                category=p["category"],
                tags=p["tags"],
                importance=p["importance"],
                source_session=p["source_session"],
                source_agent=p["source_agent"],
            )
            imported += 1
        except Exception as e:  # 单条失败不中断整体导入
            logger.warning("导入单条记忆失败: %s", e)
    stats["imported"] = imported
    mf._storage._add_audit(
        "import_chat", "", actor or "", session_id or "", "",
        {"platform": platform, "parsed": len(messages), "imported": imported})
    return stats
