"""
MindForge v5.6.3 入口层覆盖率补充测试
====================================

P3 #30：整体覆盖率 36%，入口层（adapters / mcp / api / cli）偏低。
本文件针对四个入口面补充覆盖：

- adapters/generic_api.py  GenericAPIAdapter 分发 / 必需字段校验 / 错误脱敏 / F1 过滤透传
- mcp/server.py            MCP 帧协议（Content-Length 强校验、超限拒绝）+ 认证过期
- api/server.py            REST 认证 fail-closed / IPv6 限流 key / 404 脱敏 / 请求体上限
- cli/main.py              CLI 入口分发（--version / 帮助 / 未知命令）

设计约束：不触碰仓库真实数据目录；所有 MindForge 实例使用临时 SQLite。
"""

import http.client
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from adapters.generic_api import GenericAPIAdapter
from core.mindforge import MindForge


# ---------------------------------------------------------------------------
# 1. GenericAPIAdapter：分发 / 校验 / 脱敏 / F1 透传
# ---------------------------------------------------------------------------

class TestGenericAPIAdapter(unittest.TestCase):
    """GenericAPIAdapter 入口覆盖（P3 #30 / P2 #19 / F1）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_adapter_")
        self.cm = MindForge(db_path=os.path.join(self.tmp, "t.db"),
                            encrypted=False)
        self.api = GenericAPIAdapter(self.cm)

    def tearDown(self):
        self.cm.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dispatch_add_and_list(self):
        """add → list 全链路可用，且 F1 actor 参数透传到核心层"""
        r = self.api.handle_request({"action": "memory.add", "params": {
            "content": "适配器测试记忆", "category": "test",
            "tags": ["a"], "agent": "alice"}})
        self.assertTrue(r["success"])
        mid = r["data"]["id"]

        r = self.api.handle_request({"action": "memory.get", "params": {
            "id": mid, "actor": "alice", "session_id": "alice"}})
        self.assertTrue(r["success"])
        self.assertEqual(r["data"]["content"], "适配器测试记忆")

        # F1：非 owner（actor/session 不一致）→ 隐私过滤拒绝，返回 None 而非泄露
        r_deny = self.api.handle_request({"action": "memory.get", "params": {
            "id": mid, "actor": "eve", "session_id": "eve"}})
        self.assertTrue(r_deny["success"])
        self.assertIsNone(r_deny["data"])

        # F1：同一 actor 可见自己的记忆
        r = self.api.handle_request({"action": "memory.list", "params": {
            "actor": "alice", "session_id": "alice"}})
        self.assertTrue(r["success"])
        self.assertGreaterEqual(len(r["data"]), 1)

        # F1：无关 actor 的列表被隐私引擎过滤，不泄露其他调用者的记忆
        r_bob = self.api.handle_request({"action": "memory.list", "params": {
            "actor": "bob", "session_id": "bob"}})
        self.assertTrue(r_bob["success"])
        self.assertEqual(len(r_bob["data"]), 0)

    def test_dispatch_missing_action(self):
        r = self.api.handle_request({})
        self.assertFalse(r["success"])
        self.assertIn("action", r["error"])

    def test_dispatch_invalid_params(self):
        r = self.api.handle_request({"action": "memory.add", "params": "not-a-dict"})
        self.assertFalse(r["success"])
        self.assertIn("params", r["error"])

    def test_dispatch_unknown_action(self):
        r = self.api.handle_request({"action": "no_such_action", "params": {}})
        self.assertFalse(r["success"])
        self.assertIn("Unknown action", r["error"])

    def test_required_field_missing_returns_friendly_error(self):
        r = self.api.handle_request({"action": "memory.get", "params": {}})
        self.assertFalse(r["success"])
        self.assertIn("Missing required field", r["error"])

    def test_unexpected_error_is_sanitized(self):
        """未预期异常 → 脱敏 'Internal error'，内部细节不泄露（P2 #19）"""
        def boom(params):
            raise RuntimeError("secret-internal-detail: /etc/passwd")

        self.api.register_handler("boom", boom)
        r = self.api.handle_request({"action": "boom", "params": {}})
        self.assertFalse(r["success"])
        self.assertEqual(r["error"], "Internal error")
        self.assertNotIn("passwd", json.dumps(r))

    def test_search_and_stats_handlers(self):
        self.cm.add("搜索引擎测试内容", category="web")
        r = self.api.handle_request({"action": "memory.search", "params": {"query": "搜索"}})
        self.assertTrue(r["success"])
        self.assertIn("results", r["data"])

        r = self.api.handle_request({"action": "stats.get", "params": {}})
        self.assertTrue(r["success"])
        self.assertIn("total", r["data"])

    def test_update_and_delete(self):
        e = self.cm.add("待更新", category="x")
        r = self.api.handle_request({"action": "memory.update", "params": {
            "id": e.id, "content": "已更新"}})
        self.assertTrue(r["success"])
        self.assertTrue(r["data"])
        self.assertEqual(self.cm.get(e.id).content, "已更新")

        r = self.api.handle_request({"action": "memory.delete", "params": {"id": e.id}})
        self.assertTrue(r["success"])
        self.assertTrue(r["data"])


# ---------------------------------------------------------------------------
# 2. MCP 传输层：帧协议强校验 + 认证过期
# ---------------------------------------------------------------------------

class _FakeStream:
    """带 .buffer 的伪 stdin/stdout，供 MCP 帧函数使用"""

    def __init__(self, data: bytes = b""):
        self.buffer = io.BytesIO(data)


class TestMCPFraming(unittest.TestCase):
    """MCP Content-Length 帧协议（P2 #17 / P2 #23）"""

    def _read(self, data: bytes):
        import mcp.server as mcp
        with mock.patch.object(mcp.sys, "stdin", _FakeStream(data)):
            return mcp._read_message()

    def test_read_valid_frame(self):
        body = b'{"jsonrpc":"2.0","method":"ping","id":1}'
        framed = b"Content-Length: %d\r\n\r\n%s" % (len(body), body)
        self.assertEqual(self._read(framed), {
            "jsonrpc": "2.0", "method": "ping", "id": 1})

    def test_read_missing_content_length_rejected(self):
        with self.assertRaises(ValueError):
            self._read(b"\r\n\r\n")

    def test_read_zero_content_length_rejected(self):
        with self.assertRaises(ValueError):
            self._read(b"Content-Length: 0\r\n\r\n")

    def test_read_negative_content_length_rejected(self):
        with self.assertRaises(ValueError):
            self._read(b"Content-Length: -1\r\n\r\n")

    def test_read_non_numeric_content_length_rejected(self):
        with self.assertRaises(ValueError):
            self._read(b"Content-Length: abc\r\n\r\n")

    def test_read_oversized_content_length_rejected(self):
        with self.assertRaises(ValueError):
            self._read(b"Content-Length: 9999999999\r\n\r\n")

    def test_write_message_framing(self):
        import mcp.server as mcp
        out = _FakeStream()
        with mock.patch.object(mcp.sys, "stdout", out):
            mcp._write_message({"jsonrpc": "2.0", "id": 1, "result": {}})
        payload = out.buffer.getvalue()
        header, _, body = payload.partition(b"\r\n\r\n")
        self.assertTrue(header.startswith(b"Content-Length: "))
        length = int(header.split(b":")[1].strip())
        self.assertEqual(length, len(body))
        self.assertEqual(json.loads(body.decode()), {"jsonrpc": "2.0", "id": 1, "result": {}})

    def test_check_auth(self):
        import mcp.server as mcp
        self.assertTrue(mcp._check_auth({}, ""))  # 无密钥 → 放行（兼容 stdio 本地）
        self.assertFalse(mcp._check_auth({"params": {"_meta": {"authSecret": "x"}}}, "y"))
        self.assertTrue(mcp._check_auth({"params": {"_meta": {"authSecret": "y"}}}, "y"))


class TestMCPAuthExpiry(unittest.TestCase):
    """MCP 认证过期（P2 #16）：30 分钟无活动自动登出"""

    def test_session_expired_after_idle_timeout(self):
        import mcp.server as mcp

        tmp = tempfile.mkdtemp(prefix="mf_mcp_")
        try:
            db = os.path.join(tmp, "mcp.db")
            init = {"jsonrpc": "2.0", "method": "initialize",
                    "id": 1,
                    "params": {"_meta": {"authSecret": "sekret"}}}
            ping = {"jsonrpc": "2.0", "method": "ping", "id": 2}
            init_raw = json.dumps(init).encode()
            ping_raw = json.dumps(ping).encode()
            stream = _FakeStream(
                b"Content-Length: %d\r\n\r\n%s"
                b"Content-Length: %d\r\n\r\n%s"
                % (len(init_raw), init_raw, len(ping_raw), ping_raw)
            )
            out = _FakeStream()
            with mock.patch.object(mcp.sys, "stdin", stream), \
                 mock.patch.object(mcp.sys, "stdout", out):
                # 把最后活动时间推到过期窗口之外，模拟 30 分钟无活动
                with mock.patch.object(mcp, "_auth_last_activity",
                                       time.time() - mcp._AUTH_IDLE_TIMEOUT - 60):
                    rc = mcp.serve_forever(db_path=db, auth_secret="sekret")

            self.assertEqual(rc, 0)  # 流结束正常退出
            written = out.buffer.getvalue().decode("utf-8", "replace")
            # initialize 成功 + 过期后 ping 被拒
            self.assertIn("protocolVersion", written)
            self.assertIn("Session expired", written)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. REST API：认证 fail-closed / IPv6 限流 / 404 脱敏 / 体积上限
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestRestAPIEntry(unittest.TestCase):
    """REST 入口层覆盖（P1 #12 / P2 #14 / P2 #15 / P2 #23）"""

    _ENV_KEYS = ("MINDFORGE_API_KEY", "MINDFORGE_ALLOW_NOAUTH")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_api_")
        self.cm = MindForge(db_path=os.path.join(self.tmp, "api.db"),
                            encrypted=False)
        self.cm.add("API 入口覆盖", category="test", source_agent="alice")
        self.port = _free_port()
        self._saved_env = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        self.cm.close()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _boot(self, **env):
        # 环境变量在测试期间保持生效（认证检查按请求时读取），tearDown 统一恢复
        from api.server import start_api_server
        os.environ.update(env)
        t = threading.Thread(
            target=start_api_server,
            kwargs={"mindforge_instance": self.cm,
                    "host": "127.0.0.1", "port": self.port},
            daemon=True)
        t.start()
        time.sleep(1.2)
        return t

    def test_auth_fail_closed_without_key(self):
        """默认（无 API Key、未显式 ALLOW_NOAUTH）→ 401（P1 #12）"""
        t = self._boot()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api/stats", timeout=5)
            self.assertEqual(ctx.exception.code, 401)
        finally:
            self.cm.close()

    def test_auth_with_bearer_token(self):
        """设置 API Key 后，Bearer 正确 → 200；缺失 → 401"""
        t = self._boot(MINDFORGE_API_KEY="sekret")
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/stats",
                headers={"Authorization": "Bearer sekret"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode())
            self.assertIn("total", data)
        finally:
            self.cm.close()

    def test_noauth_mode_and_actor_filter(self):
        """显式 ALLOW_NOAUTH=1 → 200；list 带 agent 参数（F1 透传）"""
        t = self._boot(MINDFORGE_ALLOW_NOAUTH="1")
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api/memories?agent=bob",
                    timeout=5) as resp:
                data = json.loads(resp.read().decode())
            self.assertIsInstance(data.get("memories"), list)
        finally:
            self.cm.close()

    def test_404_generic_no_path_leak(self):
        """404 不泄露请求路径（P2 #14）"""
        t = self._boot(MINDFORGE_ALLOW_NOAUTH="1")
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api/definitely-not-here",
                    timeout=5)
            self.assertEqual(ctx.exception.code, 404)
            body = ctx.exception.read().decode("utf-8", "replace")
            self.assertNotIn("definitely-not-here", body)
            self.assertIn("Not found", body)
        finally:
            self.cm.close()

    def test_oversized_body_rejected_413(self):
        """Content-Length 超过 10MB → 413（P2 #23 配套防护）"""
        t = self._boot(MINDFORGE_ALLOW_NOAUTH="1")
        try:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.putrequest("POST", "/api/memories")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(11 * 1024 * 1024))
            conn.endheaders()
            resp = conn.getresponse()
            self.assertEqual(resp.status, 413)
            resp.read()
            conn.close()
        finally:
            self.cm.close()

    def test_startup_rejects_non_localhost_without_key(self):
        """非 localhost 绑定且无 API Key → SecurityError（fail-closed 启动）"""
        from api.server import start_api_server, SecurityError
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SecurityError):
                start_api_server(self.cm, host="0.0.0.0", port=self.port)


class TestRateLimitKeyNormalization(unittest.TestCase):
    """IPv6 临时地址限流绕过（P2 #15）"""

    def _norm(self, ip):
        from api.server import _normalize_ip
        return _normalize_ip(ip)

    def test_ipv4_unchanged(self):
        self.assertEqual(self._norm("1.2.3.4"), "1.2.3.4")

    def test_ipv6_collapses_to_prefix64(self):
        a = self._norm("2001:db8:1234:5678:aaaa:bbbb:cccc:dddd")
        b = self._norm("2001:db8:1234:5678:1111:2222:3333:4444")
        self.assertEqual(a, b)
        self.assertTrue(a.endswith("/64"))

    def test_invalid_ip_fallback(self):
        self.assertEqual(self._norm("not-an-ip"), "not-an-ip")


# ---------------------------------------------------------------------------
# 4. CLI 入口分发（P3 #30）
# ---------------------------------------------------------------------------

class TestCLIEntry(unittest.TestCase):
    """CLI 入口 main() 分发：不触碰真实数据目录"""

    def test_version_flag(self):
        import cli.main as cm
        with mock.patch.object(sys, "argv", ["mindforge", "--version"]):
            with self.assertRaises(SystemExit) as ctx:
                cm.main(["--version"])
            self.assertEqual(ctx.exception.code, 0)

    def test_help_flag(self):
        import cli.main as cm
        with self.assertRaises(SystemExit) as ctx:
            cm.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_no_command_prints_help(self):
        import cli.main as cm
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            rc = cm.main([])
        self.assertIsNone(rc)
        self.assertIn("usage", buf.getvalue().lower())

    def test_unknown_command_exits_2(self):
        import cli.main as cm
        with self.assertRaises(SystemExit) as ctx:
            cm.main(["no-such-command-xyz"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
