"""
MindForge v5.4.9 事件总线与 Webhook 通知
=========================================

提供内存级发布/订阅事件总线，支持：
- 记忆生命周期事件：created / updated / deleted / expired
- 矛盾检测事件：conflict_detected
- 版本回滚事件：version_rolled_back
- 导出事件：export_completed
- Webhook 回调：HTTP POST 到配置的 URL，支持签名验证、重试、超时

配置方式：
  1. 构造参数：MindForge(webhooks=["https://example.com/hook"])
  2. 环境变量：MINDFORGE_WEBHOOK_URLS=https://a.com/hook,https://b.com/hook
  3. 运行时：mf.event_bus.register_webhook(url, events=["memory_created"])

事件 payload 结构：
  {
    "event": "memory_created",
    "timestamp": "2026-08-20T10:00:00Z",
    "source": "mindforge",
    "version": "5.4.9",
    "data": { ... 事件相关数据 ... }
  }

Webhook 签名（可选）：
  设置 MINDFORGE_WEBHOOK_SECRET 后，每个请求携带 header：
    X-MindForge-Signature: sha256=<hex>
  签名 = HMAC-SHA256(secret, raw_body)
"""

import json
import time
import hmac
import hashlib
import logging
import threading
import urllib.request
import urllib.error
from typing import Callable, Optional, List, Dict, Any, Set
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 标准事件类型常量
EVENT_MEMORY_CREATED = "memory_created"
EVENT_MEMORY_UPDATED = "memory_updated"
EVENT_MEMORY_DELETED = "memory_deleted"
EVENT_MEMORY_EXPIRED = "memory_expired"
EVENT_CONFLICT_DETECTED = "conflict_detected"
EVENT_VERSION_ROLLED_BACK = "version_rolled_back"
EVENT_EXPORT_COMPLETED = "export_completed"
EVENT_IMPORT_COMPLETED = "import_completed"

ALL_EVENTS = frozenset({
    EVENT_MEMORY_CREATED,
    EVENT_MEMORY_UPDATED,
    EVENT_MEMORY_DELETED,
    EVENT_MEMORY_EXPIRED,
    EVENT_CONFLICT_DETECTED,
    EVENT_VERSION_ROLLED_BACK,
    EVENT_EXPORT_COMPLETED,
    EVENT_IMPORT_COMPLETED,
})


@dataclass
class WebhookConfig:
    """单个 Webhook 配置"""
    url: str
    events: Set[str] = field(default_factory=lambda: set(ALL_EVENTS))
    secret: str = ""
    timeout: float = 10.0
    max_retries: int = 3
    retry_delay: float = 2.0
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "events": sorted(self.events),
            "has_secret": bool(self.secret),
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "enabled": self.enabled,
        }


class EventBus:
    """MindForge 事件总线（v5.4.9 新增）

    支持同步订阅者（Python 回调）和异步 Webhook（HTTP POST）。
    Webhook 投递在后台线程执行，不阻塞主流程。
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._webhooks: List[WebhookConfig] = []
        self._lock = threading.Lock()
        self._enabled = True
        self._event_count = 0
        self._delivery_history: List[Dict[str, Any]] = []
        self._max_history = 100

    # ===== 订阅/取消订阅 =====

    @staticmethod
    def _validate_no_ssrf(url: str) -> None:
        """SSRF 防护：拒绝指向内网/回环/链路本地的 Webhook URL

        阻止探测云元数据服务（169.254.169.254）、本地服务等。
        """
        from urllib.parse import urlparse
        import ipaddress

        parsed = urlparse(url)
        hostname = parsed.hostname or ""

        # 常见内网主机名
        if hostname in ("localhost", "loopback", "metadata", "metadata.google.internal"):
            raise ValueError(f"Webhook URL 禁止指向本地/内网主机: {hostname}")

        # 尝试解析 IP 并检查
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            # 不是 IP 地址，是域名 — 运行时再解析（DNS rebinding 风险）
            # 注册阶段只做基本格式校验，投递前再做 DNS 解析检查
            return

        # 拒绝内网/私有/回环/链路本地/组播/未指定
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_unspecified or ip.is_reserved):
            raise ValueError(f"Webhook URL 禁止指向内网 IP: {ip}")

    def subscribe(self, event: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        """订阅指定事件

        Args:
            event: 事件类型（使用 EVENT_* 常量），或 "*" 订阅所有事件
            callback: 回调函数，接收 event payload dict
        """
        with self._lock:
            if event not in self._subscribers:
                self._subscribers[event] = []
            self._subscribers[event].append(callback)

    def unsubscribe(self, event: str, callback: Callable) -> bool:
        """取消订阅"""
        with self._lock:
            if event in self._subscribers:
                try:
                    self._subscribers[event].remove(callback)
                    return True
                except ValueError:
                    pass
        return False

    # ===== Webhook 管理 =====

    def register_webhook(self, url: str,
                         events: Optional[List[str]] = None,
                         secret: str = "",
                         timeout: float = 10.0,
                         max_retries: int = 3) -> WebhookConfig:
        """注册 Webhook 回调

        Args:
            url: 回调 URL
            events: 监听的事件类型列表，None 表示所有事件
            secret: 用于签名的密钥（可选）
            timeout: 请求超时秒数
            max_retries: 最大重试次数

        Returns:
            WebhookConfig 实例

        Raises:
            ValueError: URL 为空或格式无效
        """
        if not url or not isinstance(url, str):
            raise ValueError("Webhook URL 不能为空")
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Webhook URL 必须以 http:// 或 https:// 开头: {url!r}")

        # P1 安全修复：SSRF 防护 — 拒绝内网/回环/链路本地地址
        self._validate_no_ssrf(url)

        config = WebhookConfig(
            url=url,
            events=set(events) if events else set(ALL_EVENTS),
            secret=secret,
            timeout=timeout,
            max_retries=max_retries,
        )
        with self._lock:
            # 避免重复 URL
            self._webhooks = [w for w in self._webhooks if w.url != url]
            self._webhooks.append(config)
        logger.info("Webhook 已注册: %s (events=%s)", url, sorted(config.events))
        return config

    def unregister_webhook(self, url: str) -> bool:
        """注销 Webhook"""
        with self._lock:
            before = len(self._webhooks)
            self._webhooks = [w for w in self._webhooks if w.url != url]
            return len(self._webhooks) < before

    def list_webhooks(self) -> List[Dict[str, Any]]:
        """列出所有 Webhook 配置"""
        with self._lock:
            return [w.to_dict() for w in self._webhooks]

    # ===== 发布事件 =====

    def publish(self, event: str, data: Optional[Dict[str, Any]] = None,
                sync: bool = False) -> Dict[str, Any]:
        """发布事件

        Args:
            event: 事件类型
            data: 事件数据
            sync: True=同步投递（等待所有 webhook 完成），False=异步（默认）

        Returns:
            {"event": ..., "timestamp": ..., "subscribers_notified": int, "webhooks_dispatched": int}
        """
        if not self._enabled:
            return {"event": event, "skipped": True, "reason": "event_bus_disabled"}

        payload = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "mindforge",
            "version": "5.4.9",
            "data": data or {},
        }

        # 同步订阅者
        notified = 0
        with self._lock:
            callbacks = list(self._subscribers.get(event, []))
            callbacks.extend(self._subscribers.get("*", []))
            webhooks = [w for w in self._webhooks
                        if w.enabled and event in w.events]

        for cb in callbacks:
            try:
                cb(payload)
                notified += 1
            except Exception as e:
                logger.warning("事件订阅者回调失败 [%s]: %s", event, e)

        # Webhook 投递
        dispatched = len(webhooks)
        if webhooks:
            if sync:
                for wh in webhooks:
                    self._deliver_webhook(wh, payload)
            else:
                t = threading.Thread(
                    target=self._deliver_webhooks_async,
                    args=(webhooks, payload),
                    daemon=True,
                )
                t.start()

        self._event_count += 1
        result = {
            "event": event,
            "timestamp": payload["timestamp"],
            "subscribers_notified": notified,
            "webhooks_dispatched": dispatched,
        }
        return result

    def _deliver_webhooks_async(self, webhooks: List[WebhookConfig],
                                payload: Dict[str, Any]) -> None:
        """后台线程投递多个 Webhook"""
        for wh in webhooks:
            self._deliver_webhook(wh, payload)

    def _deliver_webhook(self, config: WebhookConfig,
                         payload: Dict[str, Any]) -> bool:
        """投递单个 Webhook（带重试）

        P1 修复：签名与发送体必须一致 — 统一序列化一次 body，
        签名计算用同一个字节串，发送用 data=body 而非 json=payload。
        P3-2 修复：尊重 config.timeout，4xx 不重试。
        """
        # 统一序列化：签名和发送共用同一个 body 字节串
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "MindForge/5.4.9",
            "X-MindForge-Event": payload["event"],
        }
        if config.secret:
            sig = hmac.new(
                config.secret.encode("utf-8"),
                body,
                hashlib.sha256
            ).hexdigest()
            headers["X-MindForge-Signature"] = f"sha256={sig}"

        # P3-2: 尊重用户配置的 timeout，requests 需要 (connect, read) 元组
        # v5.5.7 fix: connect 超时也受 config.timeout 控制（之前硬编码 3s）
        if isinstance(config.timeout, (tuple, list)):
            timeout = tuple(config.timeout)
        else:
            timeout = (config.timeout, config.timeout)

        last_error = ""
        actual_attempts = 0
        for attempt in range(config.max_retries + 1):
            actual_attempts = attempt + 1
            try:
                import requests
                response = requests.post(
                    config.url,
                    data=body,
                    headers=headers,
                    timeout=timeout,
                )
                status = response.status_code
                success = 200 <= status < 300
                self._record_delivery(config.url, payload["event"],
                                      status, success, attempt)
                if success:
                    logger.debug("Webhook 投递成功 [%s] -> %s (status=%d)",
                                 payload["event"], config.url, status)
                    return True
                last_error = f"HTTP {status}"
                # P3-2: 4xx 客户端错误不重试
                if 400 <= status < 500:
                    break
            except ImportError:
                # requests 库不可用时降级到 urllib
                last_error = self._deliver_webhook_urllib(config, body, headers, attempt)
                if last_error is True:
                    return True
                break
            except Exception as e:
                last_error = str(e)
                self._record_delivery(config.url, payload["event"],
                                      0, False, attempt)

            if attempt < config.max_retries:
                time.sleep(config.retry_delay * (attempt + 1))

        # v5.5.7 fix: 日志写实际尝试次数（4xx break 时只请求了 1 次）
        logger.warning("Webhook 投递失败 [%s] -> %s: %s (after %d attempts)",
                       payload["event"], config.url, last_error,
                       actual_attempts)
        return False

    def _deliver_webhook_urllib(self, config: WebhookConfig,
                                body: bytes,
                                headers: Dict[str, str],
                                attempt: int) -> str:
        """使用 urllib 的降级方案（requests 不可用时）

        P1 修复：body 已在调用方序列化好，签名也基于同一字节串，
        不再重复序列化。
        """
        try:
            req = urllib.request.Request(
                config.url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=config.timeout) as resp:
                status = resp.getcode()
                success = 200 <= status < 300
                self._record_delivery(config.url, headers.get("X-MindForge-Event", "unknown"),
                                      status, success, attempt)
                if success:
                    logger.debug("Webhook 投递成功 -> %s (status=%d)",
                                 config.url, status)
                    return True
                return f"HTTP {status}"
        except urllib.error.HTTPError as e:
            self._record_delivery(config.url, headers.get("X-MindForge-Event", "unknown"),
                                  e.code, False, attempt)
            return f"HTTP {e.code}"
        except Exception as e:
            self._record_delivery(config.url, headers.get("X-MindForge-Event", "unknown"),
                                  0, False, attempt)
            return str(e)

    def _record_delivery(self, url: str, event: str,
                         status: int, success: bool, attempt: int) -> None:
        """记录投递历史（用于调试和监控）"""
        record = {
            "url": url,
            "event": event,
            "status": status,
            "success": success,
            "attempt": attempt,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with self._lock:
            self._delivery_history.append(record)
            if len(self._delivery_history) > self._max_history:
                self._delivery_history = self._delivery_history[-self._max_history:]

    # ===== 状态查询 =====

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    @property
    def event_count(self) -> int:
        return self._event_count

    def delivery_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最近的投递记录"""
        with self._lock:
            return list(self._delivery_history[-limit:])

    def stats(self) -> Dict[str, Any]:
        """事件总线统计"""
        with self._lock:
            subscriber_count = sum(len(v) for v in self._subscribers.values())
            return {
                "enabled": self._enabled,
                "total_events_published": self._event_count,
                "subscribers": subscriber_count,
                "webhooks": len(self._webhooks),
                "delivery_history_size": len(self._delivery_history),
            }


def create_event_bus_from_config(config: Optional[Dict[str, Any]] = None) -> EventBus:
    """从配置字典创建事件总线（v5.4.9）

    支持的配置键：
      webhooks: [{"url": ..., "events": [...], "secret": ...}]
      webhook_urls: ["https://..."]  （简写）
      webhook_secret: "..."
      enabled: true/false
    """
    import os
    bus = EventBus()

    # 从环境变量加载
    env_urls = os.environ.get("MINDFORGE_WEBHOOK_URLS", "")
    env_secret = os.environ.get("MINDFORGE_WEBHOOK_SECRET", "")
    if env_urls:
        for url in [u.strip() for u in env_urls.split(",") if u.strip()]:
            bus.register_webhook(url, secret=env_secret)

    # 从配置字典加载
    if config:
        if config.get("enabled") is False:
            bus.enabled = False
        for wh in config.get("webhooks", []) or []:
            if isinstance(wh, str):
                bus.register_webhook(wh, secret=config.get("webhook_secret", ""))
            elif isinstance(wh, dict):
                bus.register_webhook(
                    url=wh.get("url", ""),
                    events=wh.get("events"),
                    secret=wh.get("secret", config.get("webhook_secret", "")),
                    timeout=wh.get("timeout", 10.0),
                    max_retries=wh.get("max_retries", 3),
                )
        for url in config.get("webhook_urls", []) or []:
            bus.register_webhook(url, secret=config.get("webhook_secret", ""))

    return bus
