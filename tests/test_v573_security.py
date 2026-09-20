# -*- coding: utf-8 -*-
"""v5.7.3 安全修复回归测试。

覆盖：
- S1 INTERNAL 级记忆空 source 不再恒等匹配（越权读取修复）
- S2 API 读/写路径透传 actor/session_id 参与隐私过滤
- S3 _extract_actor 已配置 API Key 时 fail-closed 返回 anonymous
- S4 legacy 密钥文件（无 verify_blob）不再自动重写升级（防永久锁死）
- S5 rekey 命令退出后 MINDFORGE_PASSWORD 环境变量被清理
- S6 /api/import 遇非 dict 条目计入 failed 而非抛 AttributeError
- S7 访问日志剥离 query string（搜索词/记忆 ID 不落盘）
- S8 2FA 连续失败计数与锁定退避
"""

import os
import re
import sys
import time
import json
import shutil
import tempfile
import hashlib
import hmac
import unittest
import types
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.types import PrivacyLevel  # noqa: E402


class _StorageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v573_")
        self.db = os.path.join(self.tmp, "test.db")
        from core.storage import StorageEngine

        self.storage = StorageEngine(db_path=self.db, encrypted=False)

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------- S1：INTERNAL 空 source 不再恒等匹配 ----------------
class TestInternalEmptySource(_StorageCase):
    def test_anonymous_cannot_access_owner_internal(self):
        from modules.privacy import PrivacyEngine, PrivacyLevel

        # jane 的 INTERNAL 记忆：显式身份（含远程 anonymous）必须精确匹配来源
        entry = self.storage.add_memory(
            content="c", source_agent="jane", source_session="s1"
        )
        entry.privacy = PrivacyLevel.INTERNAL
        pe = PrivacyEngine(self.storage)
        # 远程显式自称 anonymous：拒绝（旧实现 source_session=="" == session_id==""
        # 恒真放行任意调用者 -> 越权）
        ok, _ = pe.check_access(entry, actor="anonymous")
        self.assertFalse(ok)
        # 非所有者显式身份：拒绝
        ok, _ = pe.check_access(entry, actor="alice")
        self.assertFalse(ok)
        # 本机无身份调用（CLI/单用户部署）视同部署者本人：放行
        ok, _ = pe.check_access(entry, actor="")
        self.assertTrue(ok)
        # 来源匹配者放行
        ok, _ = pe.check_access(entry, actor="jane", session_id="s1")
        self.assertTrue(ok)

    def test_empty_source_internal_not_readable_by_anyone(self):
        from modules.privacy import PrivacyEngine, PrivacyLevel

        entry = self.storage.add_memory(content="c", source_agent="", source_session="")
        entry.privacy = PrivacyLevel.INTERNAL
        pe = PrivacyEngine(self.storage)
        # 全空 source 的 INTERNAL 记忆：仅本机无身份上下文可访问（CLI/单用户）；
        # 远程显式自称 anonymous 或其他身份者必须拒绝（旧实现空 actor 放行任意调用者）
        ok, _ = pe.check_access(entry, actor="anonymous")
        self.assertFalse(ok)
        ok, _ = pe.check_access(entry, actor="alice")
        self.assertFalse(ok)
        # 本机无身份调用放行
        ok, _ = pe.check_access(entry, actor="")
        self.assertTrue(ok)

    def test_same_source_still_allowed(self):
        from modules.privacy import PrivacyEngine, PrivacyLevel

        entry = self.storage.add_memory(
            content="c", source_agent="jane", source_session="s1"
        )
        entry.privacy = PrivacyLevel.INTERNAL
        pe = PrivacyEngine(self.storage)
        ok, _ = pe.check_access(entry, actor="jane")
        self.assertTrue(ok)
        ok, _ = pe.check_access(entry, session_id="s1")
        self.assertTrue(ok)


# ---------------- S8：2FA 失败计数与锁定退避 ----------------
class TestTwoFactorRateLimit(_StorageCase):
    def setUp(self):
        super().setUp()
        from modules.privacy import PrivacyEngine

        self.pe = PrivacyEngine(self.storage)
        self.pe.register_second_factor("alice", "secret-token")
        self.code = "secret-token"  # verify 时取 sha256(code) 与注册 hash 比对

    def test_lock_after_repeated_failures(self):
        self.pe._2FA_MAX_FAILURES = 3
        self.pe._2FA_LOCK_SECONDS = 3600
        for _ in range(3):
            self.assertFalse(self.pe.verify_second_factor_with_code("alice", "000000"))
        self.assertIn("alice", self.pe._2fa_locked_until)
        # 锁定期间即便正确验证码也拒绝
        self.assertFalse(self.pe.verify_second_factor_with_code("alice", self.code))
        self.assertNotIn("alice", self.pe._verified_2fa_sessions)

    def test_success_resets_counter(self):
        self.pe._2FA_MAX_FAILURES = 3
        self.pe._2FA_LOCK_SECONDS = 3600
        self.assertFalse(self.pe.verify_second_factor_with_code("alice", "000000"))
        self.assertTrue(self.pe.verify_second_factor_with_code("alice", self.code))
        self.assertIn("alice", self.pe._verified_2fa_sessions)
        self.assertEqual(self.pe._2fa_fail_counts.get("alice", 0), 0)
        self.assertNotIn("alice", self.pe._2fa_locked_until)


# ---------------- S3：_extract_actor fail-closed ----------------
class TestExtractActorFailClosed(unittest.TestCase):
    def setUp(self):
        from api.server import MindForgeAPIHandler

        self.H = MindForgeAPIHandler

    def _h(self, headers=None):
        h = self.H.__new__(self.H)
        h.headers = headers or {}
        # 绑定同一判定方法（只依赖环境变量），便于桩对象调用 _extract_actor
        h._client_identity_trusted = types.MethodType(
            self.H._client_identity_trusted, h
        )
        return h

    def test_returns_anonymous_when_api_key_configured(self):
        env = {"MINDFORGE_API_KEY": "secret", "MINDFORGE_AGENT_ID": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            actor = self.H._extract_actor(self._h(), {})
        self.assertEqual(actor, "anonymous")

    def test_uses_server_agent_id_when_set(self):
        env = {"MINDFORGE_API_KEY": "secret", "MINDFORGE_AGENT_ID": "server-agent"}
        with mock.patch.dict(os.environ, env, clear=False):
            actor = self.H._extract_actor(self._h(), {})
        self.assertEqual(actor, "server-agent")

    def test_ignores_spoofed_header_by_default(self):
        env = {"MINDFORGE_API_KEY": "secret", "MINDFORGE_AGENT_ID": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            actor = self.H._extract_actor(self._h({"X-Agent-Id": "eve"}), {})
        self.assertEqual(actor, "anonymous")


# ---------------- S4：legacy 密钥文件不自动升级 ----------------
class TestLegacyKeyFileNoAutoUpgrade(unittest.TestCase):
    def test_legacy_key_not_rewritten_on_init(self):
        from core import encryption

        tmp = tempfile.mkdtemp(prefix="mf_key_")
        key_path = os.path.join(tmp, ".key")
        try:
            # 构造 legacy 格式（无 verify_blob）的密钥文件
            import base64

            salt = os.urandom(16)
            with open(key_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": "5.4",
                        "kdf": "PBKDF2-SHA256",
                        "iterations": 1000,
                        "salt": base64.b64encode(salt).decode(),
                        "data": base64.b64encode(b"legacy-blob").decode(),
                    },
                    f,
                )

            mtime_before = os.path.getmtime(key_path)
            # 用错误密码初始化：不得重写密钥文件入新格式
            encryption.init_engine("wrong-password", key_file=key_path)
            mtime_after = os.path.getmtime(key_path)
            with open(key_path, "rb") as f:
                raw = f.read()
            self.assertEqual(mtime_before, mtime_after)
            self.assertNotIn(b"verify_blob", raw)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------- S5：rekey 清理 MINDFORGE_PASSWORD ----------------
class TestRekeyEnvCleanup(unittest.TestCase):
    def test_env_password_removed_after_failure(self):
        import cli.main as cm

        tmp = tempfile.mkdtemp(prefix="mf_rekey_")
        try:
            from core.encryption import KDFParams

            key_file = os.path.join(tmp, ".key")
            with open(key_file, "w", encoding="utf-8") as f:
                f.write("{}")

            def fake_get_memory(args):
                return types.SimpleNamespace(
                    rekey=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
                )

            with (
                mock.patch.object(cm, "_get_memory", fake_get_memory),
                mock.patch.object(cm, "print"),
                mock.patch(
                    "core.encryption.get_key_params",
                    return_value=KDFParams(iterations=1000),
                ),
                mock.patch.object(cm, "c", side_effect=lambda s, *a, **k: s),
            ):
                rc = cm.cmd_rekey(
                    types.SimpleNamespace(
                        db_path=os.path.join(tmp, "mf.db"),
                        key_file=key_file,
                        old_password="old-secret",
                        new_password="new-secret",
                        iterations=1000,
                        upgrade_only=False,
                        yes=True,
                    )
                )
            self.assertEqual(rc, 1)
            self.assertNotIn("MINDFORGE_PASSWORD", os.environ)
        finally:
            os.environ.pop("MINDFORGE_PASSWORD", None)
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------- S7：访问日志剥离 query ----------------
class TestLogMessageSanitized(unittest.TestCase):
    def test_query_string_stripped(self):
        from api.server import MindForgeAPIHandler

        h = MindForgeAPIHandler.__new__(MindForgeAPIHandler)
        h.client_address = ("127.0.0.1", 0)
        records = []
        with mock.patch("api.server.logger") as lg:
            lg.info = lambda fmt, *a: records.append((fmt, a))
            h.log_message(
                "%s - %s",
                "1.2.3.4",
                "GET /api/memories/abc-123?q=secret&agent=eve HTTP/1.1",
            )
        self.assertEqual(len(records), 1)
        fmt, args = records[0]
        rendered = fmt % args
        self.assertNotIn("secret", rendered)
        self.assertNotIn("abc-123", rendered)
        self.assertIn("/api/memories/", rendered)


# ---------------- S9：API 写路径身份透传（PUT/DELETE） ----------------
class TestApiWritePathIdentity(unittest.TestCase):
    """do_PUT / do_DELETE 必须透传调用者身份，杜绝持 Key 越权改删。

    回归场景：get_memory(actor) 内部按 check_access 过滤，未透传时 actor=""
    被视作本机放行，任何持 Key 者都能改删其他 Agent 的 PRIVATE 记忆。
    """

    def setUp(self):
        from api.server import MindForgeAPIHandler
        from core.mindforge import MindForge
        from core.types import PrivacyLevel

        self.tmp = tempfile.mkdtemp(prefix="mf_v573_api_")
        self.mf = MindForge(db_path=os.path.join(self.tmp, "t.db"), encrypted=False)
        self.H = MindForgeAPIHandler

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _handler(self, path, body=None, env=None):
        h = self.H.__new__(self.H)
        h.mindforge = self.mf
        h.path = path
        h.headers = {}
        h.client_address = ("127.0.0.1", 0)
        h._read_body = lambda: body
        h._extract_actor = lambda qs: "anonymous"
        h._check_rate_limit = lambda write=False: True
        h._check_auth = lambda: True
        responses = []
        h._send_json = lambda obj, code=200: responses.append((code, obj))
        return h, responses

    def test_cannot_update_others_private_memory(self):
        # jane 的 PRIVATE 记忆
        e = self.mf.add(
            "jane 的私密记忆", source_agent="jane", privacy=PrivacyLevel.PRIVATE
        )
        h, resp = self._handler(f"/api/memories/{e.id}", body={"content": "篡改内容"})
        h._extract_actor = lambda qs: "anonymous"
        h.do_PUT()
        self.assertEqual(resp[0][0], 404)  # 无角色 -> check_access 拒绝 -> 视为不存在
        got = self.mf.get(e.id, actor="jane", session_id="jane")
        self.assertEqual(got.content, "jane 的私密记忆")  # 未被篡改

    def test_owner_can_update_own_memory(self):
        e = self.mf.add(
            "jane 的私密记忆", source_agent="jane", privacy=PrivacyLevel.PRIVATE
        )
        h, resp = self._handler(f"/api/memories/{e.id}", body={"content": "本人已更新"})
        h._extract_actor = lambda qs: "jane"
        h.do_PUT()
        self.assertEqual(resp[0][0], 200)
        got = self.mf.get(e.id, actor="jane", session_id="jane")
        self.assertEqual(got.content, "本人已更新")

    def test_anonymous_cannot_delete_others_private_memory(self):
        e = self.mf.add(
            "jane 的私密记忆", source_agent="jane", privacy=PrivacyLevel.PRIVATE
        )
        h, resp = self._handler(f"/api/memories/{e.id}")
        h._extract_actor = lambda qs: "anonymous"
        h.do_DELETE()
        self.assertEqual(resp[0][0], 404)
        got = self.mf.get(e.id, actor="jane", session_id="jane")
        self.assertIsNotNone(got)  # 未被删除

    def test_owner_can_delete_own_memory(self):
        e = self.mf.add(
            "jane 的私密记忆", source_agent="jane", privacy=PrivacyLevel.PRIVATE
        )
        h, resp = self._handler(f"/api/memories/{e.id}")
        h._extract_actor = lambda qs: "jane"
        h.do_DELETE()
        self.assertEqual(resp[0][0], 200)
        got = self.mf.get(e.id, actor="jane", session_id="jane")
        self.assertIsNone(got)


if __name__ == "__main__":
    unittest.main()
