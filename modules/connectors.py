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
import ipaddress
import urllib.parse
import urllib.request
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
    """SSRF 防护：拒绝内网/回环/链路本地/保留/组播/未指定地址（v5.8.3 P1-1）。

    用 ipaddress 标准属性判定替代字符串前缀黑名单，覆盖此前可稳定绕过的：
      - 十进制整数 / 八进制编码 IP（getaddrinfo 解析后即真实内网地址）
      - IPv4-mapped IPv6（::ffff:a9fe:a9fe 解包后按 IPv4 判定）
      - CGNAT 100.64.0.0/10（Python<3.13 的 is_private 不含该段）
      - 0.0.0.0（Linux 上 connect 它即访问 127.0.0.1）
      - IPv6 链路本地 fe80::/10 与 ULA fc00::/7（除 ::1 外的内网 IPv6）
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return True  # 解析失败按不安全处理（fail-closed）
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return True
        # IPv4-mapped IPv6：解包后按 IPv4 判定（::ffff:169.254.169.254）
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return True
        # CGNAT 100.64.0.0/10：低版本 is_private 不含，显式补判
        if ip in ipaddress.ip_network("100.64.0.0/10"):
            return True
    return False


def _read_limited(resp, max_bytes: int = 5 * 1024 * 1024) -> bytes:
    """流式分块读取远端响应，累计超过 max_bytes 即中止（v5.8.3 P2-1）。

    此前 resp.read() 无上限：攻击者可返回超大 body 造成进程内存膨胀，
    入库截断（100000 字符）发生在完整读入内存之后。现在超限直接拒绝。
    """
    chunks = []
    total = 0
    while True:
        chunk = resp.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"响应体超过 {max_bytes} 字节上限，已中止读取（防内存放大）")
        chunks.append(chunk)
    return b"".join(chunks)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向逐跳 SSRF 复核（v5.8.3 P1-2）。

    默认 HTTPRedirectHandler 自动跟随 3xx 且不重新校验目标地址，攻击者
    可用公网 URL 302 到内网（如 http://169.254.169.254/latest/meta-data/）。
    本 handler 对每个跳转目标重新做 scheme/host 校验，并限制最大跳转数。
    """

    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            return None  # 拒绝跟随到非 http(s) 协议
        newhost = parsed.hostname or ""
        if _is_private_ip(newhost):
            raise ValueError(
                f"拒绝重定向到内网/回环地址: {newhost}（SSRF 防护）")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
        opener = urllib.request.build_opener(_SafeRedirectHandler())
        req = urllib.request.Request(
            source, headers={"User-Agent": "MindForge-Connector/5.7.7"})
        with opener.open(req, timeout=kwargs.get("timeout", 15)) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = _read_limited(resp)
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
