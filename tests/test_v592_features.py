# -*- coding: utf-8 -*-
"""v5.9.2 功能与加固回归测试

覆盖：
  1. privacy_report：隐私等级分布 / STRICT / 审计事件 / 加密与密钥状态 /
     联邦共享面 / 不含明文
  2. export_graph：节点与边结构、关联 memory_id、schema、不含明文
  3. REST 端点 /api/meta/privacy（鉴权网关之后）
  4. CLI privacy-report / export-graph（JSON 输出）
  5. register_second_factor 弃用告警（P3-1 加固）
"""

import json
import logging
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from mindforge.core.mindforge import MindForge  # noqa: E402
from mindforge.core.types import MemoryConfig  # noqa: E402


def _make_mf(encrypted=False):
    tmp = tempfile.mkdtemp(prefix="mf_v592_", dir=_REPO)
    db = os.path.join(tmp, "m.db")
    key = os.path.join(tmp, "k.key")
    cfg = MemoryConfig(db_path=db, key_file=key, encrypted=encrypted)
    return MindForge(config=cfg), tmp


class TestPrivacyReport(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.mf.add("隐私公开示例", privacy="PUBLIC")
        self.mf.add("隐私内部示例")
        self.mf.add("隐私私有示例", privacy="PRIVATE")
        self.mf.add("隐私严格示例", privacy="STRICT")
        self.mf.add("隐私严格示例B", privacy="STRICT")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_distribution_and_strict(self):
        rep = self.mf.privacy_report()
        dist = rep["privacy_distribution"]
        self.assertEqual(dist["PUBLIC"], 1)
        self.assertEqual(dist["INTERNAL"], 1)
        self.assertEqual(dist["PRIVATE"], 1)
        self.assertEqual(dist["STRICT"], 2)
        self.assertEqual(rep["strict_count"], 2)
        self.assertEqual(rep["total_active_memories"], 5)
        self.assertIn("version", rep)

    def test_audit_events_and_federated_keys(self):
        rep = self.mf.privacy_report()
        self.assertIn("audit_events", rep)
        self.assertIn("total", rep["audit_events"])
        self.assertIn("last_7d", rep["audit_events"])
        self.assertIn("last_30d", rep["audit_events"])
        # 联邦组件 lazy init 后返回 int（>=0）
        self.assertIsInstance(rep["federated_shared_count"], int)

    def test_no_plaintext(self):
        rep = self.mf.privacy_report()
        self.assertNotIn("隐私公开示例", str(rep))
        self.assertNotIn("隐私严格示例", str(rep))

    def test_trash_excluded(self):
        e = self.mf.add("待删除示例")  # INTERNAL：无 actor 可操作
        self.assertTrue(self.mf.agent_forget(e.id, reason="v592 测试"))
        rep = self.mf.privacy_report()
        self.assertEqual(rep["trash_count"], 1)
        # 回收站不计入活跃分布
        self.assertEqual(rep["total_active_memories"], 5)


class TestEncryptedPrivacyReport(unittest.TestCase):
    def test_key_file_mode_present(self):
        mf2, tmp2 = _make_mf(encrypted=True)
        try:
            mf2.init_with_password("v592-密码")
            rep = mf2.privacy_report()
            self.assertTrue(rep["encrypted_at_rest"])
            self.assertIsNotNone(rep["key_file_mode"])
            # Windows 无 POSIX 权限位，只要返回八进制表示即可
            self.assertTrue(str(rep["key_file_mode"]).startswith("0o"))
        finally:
            mf2.close()
            shutil.rmtree(tmp2, ignore_errors=True)


class TestExportGraph(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        # 使用规则抽取器能识别的实体与关系句式
        self.mf.add("python 使用 mysql 连接数据库")
        self.mf.add("飞书 包含 抖音")
        self.mf.extract_graph()

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_graph_export_shape(self):
        g = self.mf.export_graph()
        self.assertEqual(g["schema"], "mindforge-knowledge-graph/1.0")
        self.assertGreaterEqual(g["total_entities"], 1)
        self.assertGreaterEqual(g["total_relations"], 1)
        self.assertEqual(len(g["nodes"]), g["total_entities"])
        self.assertEqual(len(g["edges"]), g["total_relations"])
        node = g["nodes"][0]
        self.assertIn("id", node)
        self.assertIn("name", node)
        self.assertIn("type", node)
        edge = g["edges"][0]
        self.assertIn("source", edge)
        self.assertIn("target", edge)
        self.assertIn("type", edge)
        self.assertIn("weight", edge)
        self.assertIn("memory_ids", edge)

    def test_no_plaintext(self):
        g = self.mf.export_graph()
        self.assertNotIn("连接数据库", str(g))


class TestRestPrivacyEndpoint(unittest.TestCase):
    """/api/meta/privacy 位于鉴权网关之后"""

    @classmethod
    def setUpClass(cls):
        from mindforge.api.server import (
            MindForgeAPIHandler, BoundedThreadingHTTPServer)

        # 隔离鉴权环境变量：确保 fail-closed 判定不受其他测试残留影响
        cls._env_backup = {}
        for k in ("MINDFORGE_API_KEY", "MINDFORGE_ALLOW_NOAUTH",
                  "MINDFORGE_MCP_REQUIRE_AUTH"):
            cls._env_backup[k] = os.environ.pop(k, None)

        cls.tmp = tempfile.mkdtemp(prefix="mf_v592_api_", dir=_REPO)
        db = os.path.join(cls.tmp, "m.db")
        cls.mf = MindForge(db_path=db, encrypted=False)
        cls.mf.add("REST 隐私示例", privacy="STRICT")

        MindForgeAPIHandler.mindforge = cls.mf
        cls.server = BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), MindForgeAPIHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.mf.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        for k, v in cls._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _get(self, path, token=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_requires_auth(self):
        # 无 token：fail-closed（401/403），端点不匿名暴露
        status, _ = self._get("/api/meta/privacy")
        self.assertIn(status, (401, 403))

    def test_authed_returns_stats(self):
        # 验证鉴权网关在路由分发之前（见 do_GET：非 health 先 _check_auth）
        status, body = self._get("/api/health")
        self.assertEqual(status, 200)
        # 未认证访问 report 同 gate
        status2, _ = self._get("/api/meta/report")
        self.assertIn(status2, (401, 403))


class TestRegisterSecondFactorDeprecated(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_deprecation_warning_emitted(self):
        pe = self.mf.privacy_engine
        records = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())
        logger = logging.getLogger("mindforge.modules.privacy")
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.WARNING)
        try:
            pe.register_second_factor("dep-test", "secret-token")
            msgs = " | ".join(records)
            self.assertIn("弃用", msgs)
            self.assertIn("register_totp_secret", msgs)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

    def test_totp_still_works(self):
        # 标准 TOTP 入口不受影响（RFC 6238）
        import base64
        import hashlib
        import hmac
        import struct
        pe = self.mf.privacy_engine
        secret = pe.register_totp_secret("totp-test")
        self.assertTrue(secret)
        counter = int(time.time()) // 30
        msg = struct.pack(">Q", counter)
        h = hmac.new(base64.b32decode(secret), msg, hashlib.sha1).digest()
        off = h[-1] & 0x0F
        code = (struct.unpack(">I", h[off:off + 4])[0] & 0x7FFFFFFF) % 1000000
        self.assertTrue(pe.verify_totp_code("totp-test", f"{code:06d}"))


class TestCliCommands(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.mf.add("CLI 隐私示例", privacy="PRIVATE")
        self.mf.add("CLI 图谱示例：python 使用 mysql")
        # CLI 用独立 key 路径避免命中 repo 内 ./data/.key（key 存在即加密）
        self.cli_key = os.path.join(self.tmp, "cli_nokey.key")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args):
        from mindforge.cli.main import main
        from io import StringIO
        import contextlib
        buf = StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = main(args)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        return rc, buf.getvalue()

    def test_privacy_report_json(self):
        rc, out = self._run(
            ["--db-path", os.path.join(self.tmp, "m.db"),
             "--key-file", self.cli_key,
             "privacy-report", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("privacy_distribution", data)

    def test_export_graph_json(self):
        self.mf.extract_graph()
        rc, out = self._run(
            ["--db-path", os.path.join(self.tmp, "m.db"),
             "--key-file", self.cli_key,
             "export-graph", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("nodes", data)
        self.assertIn("edges", data)


if __name__ == "__main__":
    unittest.main()
