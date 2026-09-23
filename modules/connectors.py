# -*- coding: utf-8 -*-
"""数据连接器框架（v5.7.7 新增）

插件式 Connector 接口：统一「连接 → 摄取 → 返回统计」契约。
内置 File / Json / Csv / Markdown / Url 五类连接器，可注册自定义实现。

与既有导入能力的关系：连接器是统一入口与扩展点，
JSON / CSV 摄取复用 facade 的 import_json / import_csv（不重复实现），
Markdown / 文本按段落摄取，URL 摄取带基础 SSRF 防护。
"""

from __future__ import annotations

import re
import socket
import urllib.parse
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type


class BaseConnector(ABC):
    """连接器基类：实现 name / description / ingest 即可注册。"""

    name: str = ""
    description: str = ""

    @abstractmethod
    def ingest(self, mf: Any, source: str, **kwargs: Any) -> Dict[str, Any]:
        """摄取入口。

        Args:
            mf: MindForge 门面实例（用于调用导入能力）
            source: 数据源（文件路径 / URL 等）
            **kwargs: 连接器特定参数

        Returns:
            统计结果 dict（至少含 imported / skipped / failed / deduped）
        """


_CONNECTORS: Dict[str, Type[BaseConnector]] = {}


def register_connector(cls: Type[BaseConnector]) -> Type[BaseConnector]:
    """注册连接器（可作装饰器使用）。"""
    if not getattr(cls, "name", ""):
        raise ValueError("连接器必须定义 name")
    _CONNECTORS[cls.name.lower()] = cls
    return cls


def get_connector(name: str) -> Optional[Type[BaseConnector]]:
    """按名称查找连接器（大小写不敏感）。"""
    return _CONNECTORS.get(str(name or "").lower())


def list_connector_names() -> List[str]:
    return sorted(_CONNECTORS.keys())


def list_connectors() -> List[Dict[str, str]]:
    return [{"name": n, "description": _CONNECTORS[n].description}
            for n in list_connector_names()]


# ---------------- 内置连接器 ----------------

@register_connector
class JsonConnector(BaseConnector):
    name = "json"
    description = "从 JSON 文件导入记忆（复用 import_json，支持去重/目标层级）"

    def ingest(self, mf, source, **kwargs):
        # import_json 不支持 category/source_agent，过滤后再透传
        kwargs.pop("category", None)
        kwargs.pop("source_agent", None)
        return mf.import_json(source, **kwargs)


@register_connector
class CsvConnector(BaseConnector):
    name = "csv"
    description = "从 CSV 文件导入记忆（复用 import_csv）"

    def ingest(self, mf, source, **kwargs):
        kwargs.pop("category", None)
        kwargs.pop("source_agent", None)
        return mf.import_csv(source, **kwargs)


@register_connector
class MarkdownConnector(BaseConnector):
    name = "markdown"
    description = "从 Markdown / 文本文件按段落导入记忆"

    def ingest(self, mf, source, **kwargs):
        import os
        path = os.path.abspath(source)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"文件不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        target_layer = kwargs.get("target_layer")
        category = kwargs.get("category") or "import"
        source_agent = kwargs.get("source_agent") or "connector:markdown"
        stats = {"imported": 0, "skipped": 0, "failed": 0, "deduped": 0}
        for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
            if len(para) < 3:
                continue
            try:
                mf.add(para, category=category, source_agent=source_agent,
                       layer=target_layer)
                stats["imported"] += 1
            except Exception:
                stats["failed"] += 1
        return stats


@register_connector
class FileConnector(BaseConnector):
    name = "file"
    description = "按扩展名自动分派（.json / .csv / .md / .txt）"

    def ingest(self, mf, source, **kwargs):
        import os
        ext = os.path.splitext(str(source))[1].lower()
        mapping = {".json": "json", ".csv": "csv",
                   ".md": "markdown", ".txt": "markdown"}
        name = mapping.get(ext)
        if not name:
            raise ValueError(
                f"不支持的文件类型: {ext}（支持 .json / .csv / .md / .txt）")
        return _CONNECTORS[name]().ingest(mf, source, **kwargs)


def _is_private_ip(host: str) -> bool:
    """基础 SSRF 防护：拒绝内网 / 回环 / 链路本地地址。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return True  # 解析失败按不安全处理（fail-closed）
    for info in infos:
        ip = info[4][0]
        if ip == "::1" or ip.startswith("127.") or ip.startswith("10.") \
                or ip.startswith("192.168.") or ip.startswith("169.254."):
            return True
        parts = ip.split(".")
        if len(parts) == 4 and parts[0] == "172" and parts[1].isdigit() \
                and 16 <= int(parts[1]) <= 31:
            return True
    return False


@register_connector
class UrlConnector(BaseConnector):
    name = "url"
    description = ("从网页 URL 抓取文本导入（基础 SSRF 防护；"
                   "生产环境建议使用 CLI import-url 的严格 DNS 校验）")

    def ingest(self, mf, source, **kwargs):
        import urllib.request
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"仅支持 http/https URL，收到: {parsed.scheme}")
        host = parsed.hostname or ""
        if _is_private_ip(host):
            raise ValueError(f"拒绝访问内网/回环地址: {host}（SSRF 防护）")
        req = urllib.request.Request(
            source, headers={"User-Agent": "MindForge-Connector/5.7.7"})
        with urllib.request.urlopen(req, timeout=kwargs.get("timeout", 15)) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read()
        if "json" in content_type or source.rstrip("/").endswith(".json"):
            raise ValueError(
                "URL 返回 JSON：请先下载后使用 json 连接器导入")
        text = data.decode("utf-8", "replace")
        # 粗略去除 HTML 标签与脚本样式
        text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        stats = {"imported": 0, "skipped": 0, "failed": 0, "deduped": 0}
        if len(text) < 3:
            return stats
        mf.add(text[:100000] if len(text) > 100000 else text,
               category=kwargs.get("category") or "url",
               source_agent=kwargs.get("source_agent") or f"connector:url:{host}")
        stats["imported"] = 1
        return stats
