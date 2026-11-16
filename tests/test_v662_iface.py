# -*- coding: utf-8 -*-
"""v6.6.2 接口面修复契约测试

对应 .zwork_v662_iface.md 的接口面缺陷（F1-F7、F9-F12），以子进程级
复现转绿为主：
- F1 MCP：非对象 JSON 消息不再杀进程，回 -32600 Invalid Request
- F2 daemon：非对象 JSON 行不再杀进程，回 ok:false 错误行
- F3 CLI daemon：加密库（key_file 存在）fail-closed，退出码 1；
  daemon 与 CLI main() 的 stdin 强制 UTF-8 严格解码
- F4 MCP：tools/call 未知工具与 params 非对象回 -32602（不再折叠 -32000）
- F5 MCP：坏 JSON body 回 -32700 Parse error（不再静默吞掉）
- F6 MCP：initialize 不再向客户端回发 notifications/initialized
- F7 REST：/api/compress 非法 threshold/min_cluster_size 回 400（不再 500）
- F9 限流器：逐出按最久未活跃（last_seen），长期活跃 IP 不再先被逐出
- F10 CLI cmd_import_chat：接入 _safe_import_path 校验
- F12 MCP：退出路径统一 close 引擎（shutdown 退出码 0 验证路径可达）
"""
import io
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from argparse import Namespace
from contextlib import redirect_stdout

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# v6.6.2：不在模块级 setdefault MINDFORGE_ALLOW_NOAUTH——进程级污染会被
# 其他测试文件的环境卫生（save/restore/pop）清除，导致本文件内依赖它的
# API 测试在全量下拿到 401。鉴权隔离收进 TestApiCompressValidation 的
# setUpClass/tearDownClass（保存→设置→恢复）。

_MCP_LAUNCH = (
    "import sys; sys.path.insert(0, r'{repo}'); "
    "sys.argv=['mindforge.mcp.server', '--db-path', r'{db}']; "
    "import runpy; runpy.run_module('mindforge.mcp.server', run_name='__main__')"
)

_DAEMON_LAUNCH = (
    "import sys; sys.path.insert(0, r'{repo}'); "
    "from mindforge.cli.main import main; "
    "main(['--db-path', r'{db}', '--key-file', r'{key}', 'daemon'])"
)

_ENC_DB_SETUP = (
    "import sys; sys.path.insert(0, r'{repo}'); "
    "from mindforge.core.mindforge import MindForge; "
    "from mindforge.core.types import MemoryConfig; "
    "mf = MindForge(config=MemoryConfig(db_path=r'{db}', key_file=r'{key}', encrypted=True)); "
    "mf.init_with_password('password123'); "
    "mf.add(content='secret hello', category='general', tags=['t']); "
    "mf.close(); print('ENCDB-OK')"
)


def _clean_env():
    """去掉可能改变 MCP 认证路径的环境变量，保证测试走无密钥分支。"""
    env = dict(os.environ)
    env.pop("MINDFORGE_MCP_SECRET", None)
    env.pop("MINDFORGE_MCP_REQUIRE_AUTH", None)
    return env


class _McpProc:
    """子进程 MCP 服务器 + Content-Length 帧收发助手（对齐复现脚本 R1/R1b）。"""

    def __init__(self, db_path):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _MCP_LAUNCH.format(repo=_REPO, db=db_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_clean_env())
        self._q = queue.Queue()
        threading.Thread(target=self._read_frames, daemon=True).start()

    def _read_frames(self):
        try:
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    break
                length = int(line.decode("ascii").split(":", 1)[1].strip())
                self.proc.stdout.readline()  # 帧头后的空行
                raw = self.proc.stdout.read(length)
                self._q.put(json.loads(raw.decode("utf-8")))
        except Exception:
            pass

    def send(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
        self.proc.stdin.write(body)
        self.proc.stdin.flush()

    def send_raw_body(self, payload: bytes):
        """发送帧头合法但 body 非法的帧（F5 场景）。"""
        self.proc.stdin.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()

    def recv(self, timeout=30.0):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()


class _LineProc:
    """daemon 子进程 + stdout 行读取助手。"""

    def __init__(self, db_path, key=""):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _DAEMON_LAUNCH.format(repo=_REPO, db=db_path, key=key)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8")
        self._q = queue.Queue()
        threading.Thread(target=self._read_lines, daemon=True).start()

    def _read_lines(self):
        try:
            for line in self.proc.stdout:
                self._q.put(line.rstrip("\r\n"))
        except Exception:
            pass

    def send_line(self, text):
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def recv(self, timeout=30.0):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


class TestMcpIface(unittest.TestCase):
    """F1/F4/F5/F6：MCP stdio 服务器的畸形输入与错误码契约（共享一个子进程，
    用例按编号顺序执行，每条请求恰好收到一帧响应）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mf_v662_mcp_")
        cls.mcp = _McpProc(os.path.join(cls.tmp, "m.db"))

    @classmethod
    def tearDownClass(cls):
        cls.mcp.close()

    def test_01_initialize_returns_result_without_notification(self):
        # F6：第一帧必须是 initialize 的 result，而不是回发的通知
        self.mcp.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        frame = self.mcp.recv(timeout=60.0)
        self.assertIsNotNone(frame, "initialize 无响应")
        self.assertEqual(frame.get("id"), 1)
        self.assertIn("result", frame)
        # 短窗内不应再有帧（此前第一帧是回发的 notifications/initialized 通知）
        self.assertIsNone(self.mcp.recv(timeout=1.0), "initialize 后出现多余帧（疑似回发通知）")

    def test_02_ping_ok(self):
        self.mcp.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        frame = self.mcp.recv()
        self.assertEqual(frame.get("id"), 2)
        self.assertIn("result", frame)

    def test_03_unknown_tool_returns_32602(self):
        # F4：此前被折叠成 -32000 Internal server error
        self.mcp.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "no_such_tool", "arguments": {}}})
        frame = self.mcp.recv()
        self.assertEqual(frame.get("id"), 3)
        self.assertEqual(frame["error"]["code"], -32602)

    def test_04_non_dict_params_returns_32602(self):
        self.mcp.send({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": [1, 2]})
        frame = self.mcp.recv()
        self.assertEqual(frame.get("id"), 4)
        self.assertEqual(frame["error"]["code"], -32602)

    def test_05_bad_json_body_returns_32700(self):
        # F5：此前坏 JSON body 被静默吞掉，协议同步的客户端无限等待
        self.mcp.send_raw_body(b"this is not json")
        frame = self.mcp.recv()
        self.assertIsNotNone(frame, "坏 JSON body 无任何响应帧")
        self.assertEqual(frame["error"]["code"], -32700)
        self.assertIsNone(frame.get("id"))

    def test_06_non_dict_json_message_survives_with_32600(self):
        # F1：此前 [1,2,3] 直接 AttributeError 杀死整个进程
        self.mcp.send([1, 2, 3])
        frame = self.mcp.recv()
        self.assertIsNotNone(frame, "非对象 JSON 消息后无响应（进程疑似崩溃）")
        self.assertEqual(frame["error"]["code"], -32600)
        self.assertIsNone(frame.get("id"))
        # 进程仍存活且可继续服务
        self.mcp.send({"jsonrpc": "2.0", "id": 6, "method": "ping"})
        frame = self.mcp.recv()
        self.assertEqual(frame.get("id"), 6)
        self.assertIn("result", frame)


class TestMcpShutdownExitCode(unittest.TestCase):
    """F12：shutdown 走 finally 关闭引擎后以退出码 0 结束。"""

    def test_shutdown_exit_code_zero(self):
        tmp = tempfile.mkdtemp(prefix="mf_v662_mcp_exit_")
        mcp = _McpProc(os.path.join(tmp, "m.db"))
        try:
            mcp.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertIsNotNone(mcp.recv(timeout=60.0))
            mcp.send({"jsonrpc": "2.0", "id": 2, "method": "shutdown"})
            self.assertIsNotNone(mcp.recv(timeout=15.0))
            self.assertEqual(mcp.proc.wait(timeout=15), 0)
        finally:
            mcp.close()


class TestDaemonIface(unittest.TestCase):
    """F2/F3：daemon 畸形行不杀进程、加密库 fail-closed。"""

    def test_01_non_dict_line_survives(self):
        tmp = tempfile.mkdtemp(prefix="mf_v662_daemon_")
        p = _LineProc(os.path.join(tmp, "plain.db"))
        try:
            p.send_line(json.dumps({"id": 1, "cmd": "ping", "args": {}}))
            resp = json.loads(p.recv(timeout=60.0))
            self.assertTrue(resp["ok"], resp)
            # F2：此前 [1,2,3] 行 AttributeError 杀死常驻进程
            p.send_line("[1, 2, 3]")
            resp = json.loads(p.recv(timeout=15.0))
            self.assertFalse(resp["ok"])
            self.assertIsNone(resp["id"])
            # 进程仍存活，继续服务
            p.send_line(json.dumps({"id": 2, "cmd": "ping", "args": {}}))
            resp = json.loads(p.recv(timeout=15.0))
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["id"], 2)
            # shutdown 优雅退出
            p.send_line(json.dumps({"id": 3, "cmd": "shutdown", "args": {}}))
            self.assertIsNotNone(p.recv(timeout=15.0))
            self.assertEqual(p.proc.wait(timeout=15), 0)
        finally:
            if p.proc.poll() is None:
                p.proc.kill()

    def test_02_encrypted_db_fail_closed(self):
        # F3：此前以 encrypted=False 静默打开加密库，search 返回空结果且无报错
        tmp = tempfile.mkdtemp(prefix="mf_v662_daemon_enc_")
        enc_db = os.path.join(tmp, "enc.db")
        enc_key = os.path.join(tmp, "enc.key")
        r = subprocess.run(
            [sys.executable, "-c",
             _ENC_DB_SETUP.format(repo=_REPO, db=enc_db, key=enc_key)],
            capture_output=True, text=True, encoding="utf-8", timeout=180)
        self.assertIn("ENCDB-OK", r.stdout, "加密库初始化失败: " + r.stderr[-500:])
        proc = subprocess.Popen(
            [sys.executable, "-c",
             _DAEMON_LAUNCH.format(repo=_REPO, db=enc_db, key=enc_key)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8")
        try:
            out, err = proc.communicate(input="", timeout=120)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertEqual(proc.returncode, 1, f"daemon 未拒绝加密库: out={out} err={err}")
        self.assertIn("加密", out)
        self.assertNotIn('"ok":true', out.replace(" ", ""), "不应有任何成功响应")


class TestApiCompressValidation(unittest.TestCase):
    """F7：/api/compress 非法参数回 400（此前 ValueError → 500）。"""

    @classmethod
    def setUpClass(cls):
        from mindforge.core.mindforge import MindForge
        from mindforge.core.types import MemoryConfig
        from mindforge.api.server import start_api_server
        # v6.6.2：鉴权环境隔离（保存→设置，tearDownClass 恢复）
        cls._env_backup = {
            k: os.environ.get(k)
            for k in ("MINDFORGE_API_KEY", "MINDFORGE_ALLOW_NOAUTH",
                      "MINDFORGE_TRUST_PROXY")}
        os.environ["MINDFORGE_ALLOW_NOAUTH"] = "1"
        os.environ.pop("MINDFORGE_API_KEY", None)
        cls.tmp = tempfile.mkdtemp(prefix="mf_v662_api_")
        cls.mf = MindForge(config=MemoryConfig(db_path=os.path.join(cls.tmp, "api.db"),
                                               encrypted=False))
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        threading.Thread(target=start_api_server, args=(cls.mf,),
                         kwargs={"host": "127.0.0.1", "port": cls.port},
                         daemon=True).start()
        # 轮询直到 HTTP 服务可响应（HTTPError 也算就绪——说明服务已监听）
        deadline = time.time() + 30
        while True:
            try:
                cls._call("GET", "/api/stats")
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                if time.time() > deadline:
                    raise
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.mf.close()
        for k, v in cls._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @classmethod
    def _call(cls, method, path, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_bad_threshold_returns_400(self):
        status, body = self._call("POST", "/api/compress",
                                  {"threshold": "abc", "dry_run": True})
        self.assertEqual(status, 400, body)

    def test_bad_min_cluster_size_returns_400(self):
        status, body = self._call("POST", "/api/compress",
                                  {"threshold": 0.5, "min_cluster_size": "abc",
                                   "dry_run": True})
        self.assertEqual(status, 400, body)

    def test_valid_params_still_200(self):
        status, body = self._call("POST", "/api/compress",
                                  {"threshold": 0.9, "dry_run": True})
        self.assertEqual(status, 200, body)

    def test_memories_importance_400_unchanged(self):
        # 对照组：既有 400 口径不回归
        status, _ = self._call("POST", "/api/memories",
                               {"content": "x", "importance": 123})
        self.assertEqual(status, 400)


class TestRateLimiterEviction(unittest.TestCase):
    """F9：逐出按最久未活跃（last_seen），而非 dict 插入序。"""

    def test_evicts_least_recently_active(self):
        from mindforge.api.server import _RateLimiter
        rl = _RateLimiter(max_requests=1000, window_seconds=60)
        rl._MAX_TRACKED_IPS = 3
        for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
            self.assertTrue(rl.check(ip))
        # 人工设定活跃度：先插入的 10.0.0.1 最近仍活跃，后插入的 10.0.0.3 最久未活动
        rl._last_seen["10.0.0.1"] = 300.0
        rl._last_seen["10.0.0.2"] = 200.0
        rl._last_seen["10.0.0.3"] = 100.0
        self.assertTrue(rl.check("10.0.0.4"))  # 触发逐出（4 > 3）
        # 旧实现按插入序会逐出 10.0.0.1（长期活跃者先走，fail-open）；
        # 新实现逐出 last_seen 最旧的 10.0.0.3
        self.assertNotIn("10.0.0.3", rl._requests)
        self.assertNotIn("10.0.0.3", rl._last_seen)
        self.assertIn("10.0.0.1", rl._requests)

    def test_rate_limit_still_blocks(self):
        from mindforge.api.server import _RateLimiter
        rl = _RateLimiter(max_requests=2, window_seconds=60)
        self.assertTrue(rl.check("10.1.1.1"))
        self.assertTrue(rl.check("10.1.1.1"))
        self.assertFalse(rl.check("10.1.1.1"))


class TestImportChatPathValidation(unittest.TestCase):
    """F10：cmd_import_chat 接入 _safe_import_path（不存在/非法扩展名拒绝）。"""

    def test_rejects_missing_file_and_bad_ext(self):
        from mindforge.cli.main import cmd_import_chat
        tmp = tempfile.mkdtemp(prefix="mf_v662_import_")
        db = os.path.join(tmp, "m.db")

        # 1) 不存在的文件
        args = Namespace(input=os.path.join(tmp, "no_such.json"), dry_run=False,
                         db_path=db, key_file="", json_output=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_import_chat(args)
        self.assertEqual(rc, 1)
        self.assertIn("路径校验失败", buf.getvalue())

        # 2) 存在但扩展名不在允许列表
        bad = os.path.join(tmp, "evil.exe")
        with open(bad, "wb"):
            pass
        args2 = Namespace(input=bad, dry_run=False,
                          db_path=db, key_file="", json_output=False)
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = cmd_import_chat(args2)
        self.assertEqual(rc2, 1)
        self.assertIn("路径校验失败", buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
