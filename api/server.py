"""
MindForge v5.5.8 REST API Server
================================

标准 REST API，让非 Python 应用（JS、Go、移动端）也能直接调用 MindForge。

基于 Python 内置 http.server，无需额外依赖（FastAPI/Flask 可选）。

端点概览：
  GET    /api/memories          列出记忆（?limit=&offset=&category=）
  POST   /api/memories          添加记忆
  GET    /api/memories/{id}     获取单条记忆
  PUT    /api/memories/{id}     更新记忆
  DELETE /api/memories/{id}     删除记忆
  GET    /api/search            搜索记忆（?q=&limit=&min_relevance=）
  GET    /api/stats             统计信息
  GET    /api/health            健康检查
  GET    /api/tags              标签列表
  POST   /api/import            导入记忆（JSON body）
  GET    /api/export            导出记忆（JSON）

启动方式：
  MindForge serve --api
  MindForge serve --api --port 9000
  MindForge serve --api --host 0.0.0.0
"""

import json
import logging
import sys
import os
import time
import hmac
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from collections import defaultdict
from MindForge import __version__ as MF_VERSION

try:
    from core.encryption import SecurityError
except ImportError:
    class SecurityError(Exception):
        pass

# 确保项目根目录在 path 中
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

# v5.4.8 安全修复：请求体大小限制（10MB）
MAX_BODY_SIZE = 10 * 1024 * 1024


# v5.4.7 修复 H-7：简单速率限制器
class _RateLimiter:
    """基于 IP 的请求速率限制

    v5.6.3 稳定性修复：
    - 清理过期记录后，若某 IP 的计数已归零则删除该键，避免冷 IP 永久驻留内存；
    - 对跟踪的 IP 总数设上限（默认 10000），超出时丢弃最久未活动的 IP，
      防止长期运行（数月）后字典无界增长导致内存泄漏。
    """
    _MAX_TRACKED_IPS = 10000

    def __init__(self, max_requests=100, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, client_ip: str) -> bool:
        """返回 True 表示允许，False 表示限流"""
        now = time.time()
        with self._lock:
            times = self._requests[client_ip]
            times = [t for t in times if now - t < self.window]
            if len(times) >= self.max_requests:
                # 仍写回（保留未过期的计数），但不追加本次请求
                self._requests[client_ip] = times
                return False
            times.append(now)
            # 计数为空则删除键，避免长期驻留
            if not times:
                self._requests.pop(client_ip, None)
            else:
                self._requests[client_ip] = times
            # IP 总数上限：丢弃最久未活动的键
            if len(self._requests) > self._MAX_TRACKED_IPS:
                excess = len(self._requests) - self._MAX_TRACKED_IPS
                for stale_ip in list(self._requests.keys())[:excess]:
                    self._requests.pop(stale_ip, None)
            return True


_rate_limiter = _RateLimiter(max_requests=100, window_seconds=60)


def _normalize_ip(ip: str) -> str:
    """将 IP 规范化为限流 key

    P2 #15 修复：IPv6 主机可生成大量临时地址绕过限流。
    对 IPv6 使用 /64 子网前缀作为限流 key（同一子网共享计数）。
    IPv4 直接使用原地址。
    """
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip  # 解析失败直接用原值（fail-open，但仅用于限流 key）
    if isinstance(addr, ipaddress.IPv6Address):
        # 取 /64 前缀
        network = ipaddress.IPv6Network((addr, 64), strict=False)
        return str(network.network_address) + "/64"
    return ip


# P0-003: 限制最大并发线程数的 HTTP 服务器
class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """带最大并发线程数限制的 HTTP 服务器

    防止大量并发请求耗尽服务器资源（线程、内存、文件描述符）。
    超过限制时返回 503 Service Unavailable。
    """
    max_threads = 50
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._active_threads = 0
        self._thread_lock = threading.Lock()
        self._max_threads = self.__class__.max_threads

    def process_request(self, request, client_address):
        """覆盖 process_request 以限制并发线程数"""
        with self._thread_lock:
            if self._active_threads >= self._max_threads:
                # 超过并发限制，返回 503
                try:
                    body = b'{"error": "Too many concurrent requests"}'
                    request.sendall(
                        b"HTTP/1.1 503 Service Unavailable\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                        b"\r\n"
                        + body
                    )
                except Exception:
                    pass
                request.close()
                return
            self._active_threads += 1

        # 在新线程中处理请求
        t = threading.Thread(
            target=self._process_request_thread,
            args=(request, client_address),
            daemon=True,
        )
        # v5.5.8 修复：t.start() 失败（如线程资源耗尽）时计数不会回退，
        # 并发槽位会逐步泄漏直至服务永久返回 503。
        try:
            t.start()
        except RuntimeError:
            with self._thread_lock:
                self._active_threads -= 1
            try:
                body = b'{"error": "Failed to start worker thread"}'
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                    b"\r\n"
                    + body
                )
            except Exception:
                pass
            request.close()

    def _process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            with self._thread_lock:
                self._active_threads -= 1


def _safe_int(value, default=10, min_val=1, max_val=10000):
    """v5.4.7 修复 H-1：安全解析整数参数"""
    try:
        v = int(value)
        return max(min_val, min(max_val, v))
    except (ValueError, TypeError):
        return default


def _safe_float(value, default=0.3, min_val=0.0, max_val=1.0):
    """v5.4.7 修复 H-1：安全解析浮点参数"""
    try:
        v = float(value)
        return max(min_val, min(max_val, v))
    except (ValueError, TypeError):
        return default


class MindForgeAPIHandler(BaseHTTPRequestHandler):
    """REST API 请求处理器"""

    # MindForge 实例由 server 注入
    mindforge = None

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # v5.4.8 安全修复：CORS 限制为配置的源（默认仅允许同源）
        allowed_origin = os.environ.get("MINDFORGE_CORS_ORIGIN", "")
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        # v5.6.2 安全修复：Content-Length 非数值时返回 None，不抛异常
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            return None
        if content_length == 0:
            # v5.5.3 fix: 返回 None 让调用方统一处理错误响应，避免双重写入
            return None
        # v5.4.8 安全修复：请求体大小限制
        if content_length > MAX_BODY_SIZE:
            self._send_json({"error": f"Request body too large (max {MAX_BODY_SIZE // 1024 // 1024}MB)"}, 413)
            return None
        if content_length < 0:
            return None
        raw = self.rfile.read(content_length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _extract_mem_id(self, path: str, prefix: str = "/api/memories/") -> str:
        """从路径中安全提取记忆 ID（v5.6.2 安全修复：防路径遍历/注入）

        拒绝包含路径分隔符、空字节、超长值等可疑输入。
        """
        if not path.startswith(prefix):
            return ""
        mem_id = path[len(prefix):]
        # v5.6.2 安全校验：拒绝路径遍历字符、空字节、过长 ID
        if not mem_id or len(mem_id) > 128:
            return ""
        if "\x00" in mem_id or "/" in mem_id or "\\" in mem_id:
            return ""
        if ".." in mem_id:
            return ""
        return mem_id

    def _check_auth(self):
        """验证 Bearer Token（通过 MINDFORGE_API_KEY 环境变量配置）

        v5.6.3 安全增强（P1 #12 修复）：
        - MINDFORGE_ALLOW_NOAUTH 默认 "0"（fail-closed），多用户主机安全。
          单用户本地使用需显式设 MINDFORGE_ALLOW_NOAUTH=1 开启无认证模式。
        - 设置 MINDFORGE_API_KEY 后，所有非 /api/health 端点必须携带正确 Bearer Token。
        - 非 localhost 绑定且无 API Key 时，启动阶段直接拒绝（fail-closed）。
        """
        api_key = os.environ.get("MINDFORGE_API_KEY", "")
        if not api_key:
            # P1 修复：默认 fail-closed，显式设置 ALLOW_NOAUTH=1 才开放本地无认证
            allow_noauth = os.environ.get("MINDFORGE_ALLOW_NOAUTH", "0").strip().lower()
            if allow_noauth not in ("1", "true", "yes", "on"):
                self._send_json({"error": "Unauthorized (MINDFORGE_API_KEY required)"}, 401)
                return False
            # 显式开启的无认证模式：记录一次性告警
            if not getattr(self.__class__, '_auth_warned', False):
                logger.warning(
                    "MINDFORGE_API_KEY not set — API is open to all LOCAL requests. "
                    "Set MINDFORGE_API_KEY to require authentication, or set "
                    "MINDFORGE_ALLOW_NOAUTH=0 to force auth on all interfaces. "
                    "Binding to a non-localhost host without MINDFORGE_API_KEY is refused at startup."
                )
                self.__class__._auth_warned = True
            return True
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if hmac.compare_digest(token, api_key):
                return True
        self._send_json({"error": "Unauthorized"}, 401)
        return False

    def do_OPTIONS(self):
        # v5.6.2 安全修复：OPTIONS 也走限流，防止 CORS Preflight DoS
        if not self._check_rate_limit():
            return
        self._send_json({"status": "ok"})

    def _check_rate_limit(self) -> bool:
        """v5.6.1 安全修复：统一限流检查（所有 HTTP 方法共用）。
        返回 True 表示允许，False 表示已返回 429。
        """
        client_ip = self.client_address[0]
        rate_key = _normalize_ip(client_ip)
        if not _rate_limiter.check(rate_key):
            self._send_json({"error": "Rate limit exceeded. Try again later."}, 429)
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        # v5.4.7 修复 H-7：速率限制
        if not self._check_rate_limit():
            return

        if path != "/api/health" and not self._check_auth():
            return

        try:
            if path == "/api/health":
                result = self.mindforge.health_check()
                # v5.4.8 安全修复：未认证时只返回基本状态
                api_key = os.environ.get("MINDFORGE_API_KEY", "")
                if not api_key:
                    result = {
                        "status": result.get("status", "unknown"),
                        "total_memories": result.get("total_memories", 0),
                    }
                self._send_json(result)

            elif path == "/api/stats":
                result = self.mindforge.stats()
                self._send_json(result)

            elif path == "/api/tags":
                conn = self.mindforge.storage._get_conn()
                rows = conn.execute(
                    "SELECT tags FROM memories WHERE tags IS NOT NULL AND tags != '' AND tags != '[]' LIMIT 10000"  # P1-002: 限制查询行数
                ).fetchall()
                tag_counts = {}
                for row in rows:
                    try:
                        tags = json.loads(row[0]) if row[0].strip().startswith('[') else [t.strip() for t in row[0].split(',') if t.strip()]
                    except (json.JSONDecodeError, TypeError):
                        continue
                    for tag in tags:
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
                self._send_json({"tags": sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)})

            elif path == "/api/search":
                q = qs.get("q", [""])[0]
                if not q:
                    self._send_json({"error": "Missing query parameter 'q'"}, 400)
                    return
                # v5.4.7 修复 H-1：安全解析参数
                limit = _safe_int(qs.get("limit", ["10"])[0], default=10, min_val=1, max_val=1000)
                min_relevance = _safe_float(qs.get("min_relevance", ["0.3"])[0], default=0.3, min_val=0.0, max_val=1.0)
                categories = qs.get("categories", None)
                result = self.mindforge.search(
                    query=q,
                    max_results=limit,
                    min_relevance=min_relevance,
                    categories=categories,
                )
                chunks = []
                if hasattr(result, "chunks"):
                    for chunk in result.chunks:
                        chunks.append({
                            "id": chunk.memory_id,
                            "content": chunk.content,
                            "category": chunk.category,
                            "relevance_score": chunk.relevance_score,
                            "tags": chunk.tags if hasattr(chunk, "tags") else [],
                        })
                self._send_json({"query": q, "results": chunks, "total": len(chunks)})

            elif path == "/api/memories":
                # v5.4.7 修复 H-1：安全解析参数
                limit = _safe_int(qs.get("limit", ["50"])[0], default=50, min_val=1, max_val=10000)
                offset = _safe_int(qs.get("offset", ["0"])[0], default=0, min_val=0, max_val=1000000)
                category = qs.get("category", [None])[0]
                entries = self.mindforge.list(
                    category=category,
                    limit=limit,
                    offset=offset,
                )
                memories = []
                for e in entries:
                    memories.append(e.to_dict() if hasattr(e, "to_dict") else vars(e))
                self._send_json({"memories": memories, "total": len(memories), "limit": limit, "offset": offset})

            elif path.startswith("/api/memories/"):
                mem_id = self._extract_mem_id(path)
                if not mem_id:
                    self._send_json({"error": "Invalid memory ID"}, 400)
                    return
                entry = self.mindforge.get(mem_id)
                if entry:
                    self._send_json(entry.to_dict() if hasattr(entry, "to_dict") else vars(entry))
                else:
                    self._send_json({"error": "Memory not found"}, 404)

            elif path == "/api/export":
                max_export_limit = 5000  # P1-001: 导出上限 5000 条
                entries = self.mindforge.list(limit=max_export_limit)
                memories = [e.to_dict() if hasattr(e, "to_dict") else vars(e) for e in entries]
                # P3-3: 截断时添加 truncated 标志
                truncated = len(entries) >= max_export_limit
                self._send_json({
                    "version": MF_VERSION,
                    "total": len(memories),
                    "truncated": truncated,
                    "max_limit": max_export_limit if truncated else None,
                    "memories": memories,
                })

            elif path == "/":
                self._send_json({
                    "name": "MindForge REST API",
                    "version": MF_VERSION,
                    "endpoints": [
                        "GET /api/memories", "POST /api/memories",
                        "GET /api/memories/{id}", "PUT /api/memories/{id}",
                        "DELETE /api/memories/{id}",
                        "GET /api/search", "GET /api/stats",
                        "GET /api/health", "GET /api/tags",
                        "POST /api/import", "GET /api/export",
                    ],
                })

            else:
                self._send_json({"error": "Not found"}, 404)

        except Exception as e:
            logger.exception("API error")
            self._send_json({"error": "Internal server error"}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # v5.6.1: 写操作也加限流
        if not self._check_rate_limit():
            return

        if not self._check_auth():
            return

        try:
            body = self._read_body()
            if body is None:
                self._send_json({"error": "Invalid JSON body"}, 400)
                return
            # v5.5.8: 校验 JSON body 必须是 dict（非数组/字符串/数字）
            if not isinstance(body, dict):
                self._send_json({"error": "Request body must be a JSON object"}, 400)
                return

            if path == "/api/memories":
                content = body.get("content", "")
                # v5.6.3 安全修复：content 必须是字符串，否则下游 add() 会抛
                # ValueError 并最终返回 500。在此提前返回 400，给出明确错误。
                if not isinstance(content, str):
                    self._send_json({"error": "Field 'content' must be a string"}, 400)
                    return
                if not content:
                    self._send_json({"error": "Missing 'content' field"}, 400)
                    return
                entry = self.mindforge.add(
                    content=content,
                    category=body.get("category", "general"),
                    tags=body.get("tags", []),
                    importance=body.get("importance"),
                    starred=body.get("starred"),
                    source_agent=body.get("source_agent", ""),
                )
                self._send_json(entry.to_dict() if hasattr(entry, "to_dict") else vars(entry), 201)

            elif path == "/api/import":
                memories = body.get("memories", [])
                # v5.4.8 安全修复：导入批次大小限制
                MAX_IMPORT_BATCH = 10000
                if len(memories) > MAX_IMPORT_BATCH:
                    self._send_json({"error": f"Too many memories (max {MAX_IMPORT_BATCH})"}, 400)
                    return
                imported = 0
                failed = 0
                for mem in memories:
                    try:
                        self.mindforge.add(
                            content=mem.get("content", ""),
                            category=mem.get("category", "general"),
                            tags=mem.get("tags", []),
                            importance=mem.get("importance"),
                            starred=mem.get("starred"),
                            source_agent=mem.get("source_agent", ""),
                        )
                        imported += 1
                    except Exception:
                        failed += 1
                self._send_json({"imported": imported, "failed": failed})

            else:
                self._send_json({"error": "Not found"}, 404)

        except Exception as e:
            logger.exception("API error")
            self._send_json({"error": "Internal server error"}, 500)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # v5.6.1: 写操作也加限流
        if not self._check_rate_limit():
            return

        if not self._check_auth():
            return

        try:
            body = self._read_body()
            if body is None:
                self._send_json({"error": "Invalid JSON body"}, 400)
                return
            # v5.5.8: 校验 JSON body 必须是 dict
            if not isinstance(body, dict):
                self._send_json({"error": "Request body must be a JSON object"}, 400)
                return

            if path.startswith("/api/memories/"):
                mem_id = self._extract_mem_id(path)
                if not mem_id:
                    self._send_json({"error": "Invalid memory ID"}, 400)
                    return
                success = self.mindforge.update(
                    memory_id=mem_id,
                    content=body.get("content"),
                    category=body.get("category"),
                    tags=body.get("tags"),
                    importance=body.get("importance"),
                    starred=body.get("starred"),
                )
                if success:
                    self._send_json({"status": "updated", "id": mem_id})
                else:
                    self._send_json({"error": "Update failed or memory not found"}, 404)
            else:
                self._send_json({"error": "Not found"}, 404)

        except Exception as e:
            logger.exception("API error")
            self._send_json({"error": "Internal server error"}, 500)


    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # v5.6.1: 写操作也加限流
        if not self._check_rate_limit():
            return

        if not self._check_auth():
            return

        try:
            if path.startswith("/api/memories/"):
                mem_id = self._extract_mem_id(path)
                if not mem_id:
                    self._send_json({"error": "Invalid memory ID"}, 400)
                    return
                success = self.mindforge.delete(mem_id)
                if success:
                    self._send_json({"status": "deleted", "id": mem_id})
                else:
                    self._send_json({"error": "Delete failed or memory not found"}, 404)
            else:
                self._send_json({"error": "Not found"}, 404)

        except Exception as e:
            logger.exception("API error")
            self._send_json({"error": "Internal server error"}, 500)

    def log_message(self, format, *args):
        logger.info("%s - %s", self.address_string(), format % args)


def start_api_server(mindforge_instance, host="127.0.0.1", port=8080,
                     ssl_certfile: str = "", ssl_keyfile: str = ""):
    """启动 REST API 服务器

    Args:
        mindforge_instance: MindForge 实例
        host: 绑定地址
        port: 端口
        ssl_certfile: TLS 证书文件路径（启用 HTTPS）
        ssl_keyfile: TLS 私钥文件路径（启用 HTTPS）

    v5.6.1 安全修复：新增 TLS 支持，通过 ssl_certfile / ssl_keyfile 启用 HTTPS，
    避免 Bearer Token 和记忆数据明文传输。
    """
    MindForgeAPIHandler.mindforge = mindforge_instance

    # v5.6.1 安全修复：非 localhost 绑定且未设置 API Key 时拒绝启动
    api_key = os.environ.get("MINDFORGE_API_KEY", "")
    is_localhost = host in ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1")
    if not is_localhost and not api_key:
        raise SecurityError(
            "拒绝在非 localhost 地址上启动无认证的 API。"
            "请设置 MINDFORGE_API_KEY 环境变量，或仅绑定到 127.0.0.1。"
        )

    # P0-003: 使用带并发限制的多线程服务器
    server = BoundedThreadingHTTPServer((host, port), MindForgeAPIHandler)

    # v5.6.1: TLS 支持
    use_https = bool(ssl_certfile)
    if use_https:
        import ssl as _ssl
        if not ssl_keyfile:
            ssl_keyfile = ssl_certfile
        ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = _ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=ssl_certfile, keyfile=ssl_keyfile)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        proto = "https"
    else:
        proto = "http"

    print(f"MindForge REST API serving on {proto}://{host}:{port}")
    print(f"  Max concurrent threads: {BoundedThreadingHTTPServer.max_threads}")
    if use_https:
        print(f"  TLS: enabled (cert: {ssl_certfile})")
    print("  Endpoints: /api/memories, /api/search, /api/stats, /api/health, ...")
    print("  Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAPI server stopped.")
        server.server_close()
