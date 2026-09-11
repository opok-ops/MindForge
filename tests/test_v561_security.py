"""
MindForge v5.6.1 安全加固验证测试
===================================

验证 v5.6.1 引入的安全修复：
- fail-closed 启动：非 localhost 绑定且无 API Key 时拒绝启动
- TLS 参数存在性：start_api_server 支持 ssl_certfile / ssl_keyfile
- 写操作限流：POST/PUT/DELETE 全部过 _check_rate_limit()
- XSS 未闭合标签：_XSS_RE 匹配无 > 的不完整标签
- bulk_update 字段白名单：非白名单字段被拒绝
"""

import os
import sys
import tempfile
import unittest
import inspect
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestFailClosedStartup(unittest.TestCase):
    """v5.6.1: 非 localhost 绑定且未设 MINDFORGE_API_KEY 时拒绝启动"""

    def setUp(self):
        self._orig_key = os.environ.get("MINDFORGE_API_KEY", None)

    def tearDown(self):
        if self._orig_key is not None:
            os.environ["MINDFORGE_API_KEY"] = self._orig_key
        else:
            os.environ.pop("MINDFORGE_API_KEY", None)

    def test_non_localhost_rejects_without_api_key(self):
        """非 localhost 绑定且无 API Key → SecurityError"""
        os.environ.pop("MINDFORGE_API_KEY", None)

        from api.server import start_api_server, SecurityError

        with self.assertRaises(SecurityError):
            start_api_server(None, host="0.0.0.0", port=9999)

    @patch("api.server.BoundedThreadingHTTPServer")
    def test_non_localhost_allows_with_api_key(self, _mock_server):
        """非 localhost 绑定但有 API Key → 不抛 SecurityError"""
        os.environ["MINDFORGE_API_KEY"] = "test-secret-key"

        from api.server import start_api_server, SecurityError

        _mock_server.return_value.serve_forever.side_effect = KeyboardInterrupt
        try:
            start_api_server(None, host="0.0.0.0", port=19999)
        except SecurityError:
            self.fail("设置了 API Key 后不应抛 SecurityError")

    @patch("api.server.BoundedThreadingHTTPServer")
    def test_localhost_allows_without_api_key(self, _mock_server):
        """localhost 绑定且无 API Key → 不抛 SecurityError"""
        os.environ.pop("MINDFORGE_API_KEY", None)

        from api.server import start_api_server, SecurityError

        _mock_server.return_value.serve_forever.side_effect = KeyboardInterrupt
        try:
            start_api_server(None, host="127.0.0.1", port=19998)
        except SecurityError:
            self.fail("localhost 绑定不应抛 SecurityError")

    @patch("api.server.BoundedThreadingHTTPServer")
    def test_localhost_addresses_recognized(self, _mock_server):
        """验证所有 localhost 地址变体都被识别"""
        from api.server import start_api_server, SecurityError

        os.environ.pop("MINDFORGE_API_KEY", None)
        _mock_server.return_value.serve_forever.side_effect = KeyboardInterrupt
        for addr in ("127.0.0.1", "localhost", "::1"):
            try:
                start_api_server(None, host=addr, port=19997)
            except SecurityError:
                self.fail(f"地址 {addr} 应被识别为 localhost")


class TestTLSParameters(unittest.TestCase):
    """v5.6.1: start_api_server 支持 TLS 参数"""

    def test_start_api_server_has_tls_params(self):
        """验证 start_api_server 签名包含 ssl_certfile / ssl_keyfile"""
        from api.server import start_api_server

        sig = inspect.signature(start_api_server)
        self.assertIn("ssl_certfile", sig.parameters)
        self.assertIn("ssl_keyfile", sig.parameters)
        # 默认值应为空字符串
        self.assertEqual(sig.parameters["ssl_certfile"].default, "")
        self.assertEqual(sig.parameters["ssl_keyfile"].default, "")

    def test_tls_enforces_min_version_12(self):
        """验证 TLS 配置强制 TLSv1.2+"""
        source = inspect.getsource(__import__("api.server", fromlist=["start_api_server"]).start_api_server)
        self.assertIn("TLSv1_2", source)
        self.assertIn("minimum_version", source)
        self.assertIn("PROTOCOL_TLS_SERVER", source)

    def test_tls_cert_chain_loaded(self):
        """验证证书链加载逻辑存在"""
        source = inspect.getsource(__import__("api.server", fromlist=["start_api_server"]).start_api_server)
        self.assertIn("load_cert_chain", source)
        self.assertIn("wrap_socket", source)


class TestWriteOperationRateLimit(unittest.TestCase):
    """v5.6.1: POST/PUT/DELETE 全部过 _check_rate_limit()"""

    def test_do_POST_calls_rate_limit(self):
        """验证 do_POST 调用 _check_rate_limit"""
        from api.server import MindForgeAPIHandler

        source = inspect.getsource(MindForgeAPIHandler.do_POST)
        self.assertIn("_check_rate_limit", source)

    def test_do_PUT_calls_rate_limit(self):
        """验证 do_PUT 调用 _check_rate_limit"""
        from api.server import MindForgeAPIHandler

        source = inspect.getsource(MindForgeAPIHandler.do_PUT)
        self.assertIn("_check_rate_limit", source)

    def test_do_DELETE_calls_rate_limit(self):
        """验证 do_DELETE 调用 _check_rate_limit"""
        from api.server import MindForgeAPIHandler

        source = inspect.getsource(MindForgeAPIHandler.do_DELETE)
        self.assertIn("_check_rate_limit", source)

    def test_do_GET_calls_rate_limit(self):
        """验证 do_GET 仍调用 _check_rate_limit（回归）"""
        from api.server import MindForgeAPIHandler

        source = inspect.getsource(MindForgeAPIHandler.do_GET)
        self.assertIn("_check_rate_limit", source)


class TestXSSUnclosedTagBypass(unittest.TestCase):
    """v5.6.1: _XSS_RE 匹配未闭合标签（无 >）"""

    def test_unclosed_img_onerror_sanitized(self):
        """未闭合 <img onerror=alert(1) 被清除"""
        from core.storage import _sanitize_html

        payload = "<img onerror=alert(1) src=x"
        cleaned = _sanitize_html(payload)
        self.assertNotIn("onerror", cleaned)
        self.assertNotIn("<img", cleaned)

    def test_unclosed_script_tag_sanitized(self):
        """未闭合 <script 被清除"""
        from core.storage import _sanitize_html

        payload = "<script alert(1)"
        cleaned = _sanitize_html(payload)
        self.assertNotIn("<script", cleaned)

    def test_closed_tag_still_sanitized(self):
        """闭合标签仍被清除（回归）"""
        from core.storage import _sanitize_html

        payload = "<script>alert(1)</script>"
        cleaned = _sanitize_html(payload)
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("</script", cleaned)

    def test_plain_text_preserved(self):
        """普通文本不被消毒（回归）"""
        from core.storage import _sanitize_html

        text = "这是一段正常的记忆内容"
        cleaned = _sanitize_html(text)
        self.assertEqual(cleaned, text)

    def test_javascript_protocol_sanitized(self):
        """javascript: 协议被清除"""
        from core.storage import _sanitize_html

        payload = '<a href="javascript:alert(1)">click</a>'
        cleaned = _sanitize_html(payload)
        self.assertNotIn("javascript:", cleaned)


class TestBulkUpdateFieldWhitelist(unittest.TestCase):
    """v5.6.1: bulk_update_memory_fields 字段白名单拒绝"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_v561_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_non_whitelisted_field_rejected(self):
        """非白名单字段（如 content）被拒绝"""
        from core.storage import StorageEngine

        storage = StorageEngine(db_path=self.db_path)
        with self.assertRaises(ValueError) as ctx:
            storage.bulk_update_memory_fields(
                [{"id": "fake_id", "content": "hacked"}],
                ["content"],
            )
        self.assertIn("不支持的字段", str(ctx.exception))

    def test_sql_injection_field_rejected(self):
        """SQL 注入式字段名被拒绝"""
        from core.storage import StorageEngine

        storage = StorageEngine(db_path=self.db_path)
        with self.assertRaises(ValueError):
            storage.bulk_update_memory_fields(
                [{"id": "x", "strength": 0.5}],
                ["strength; DROP TABLE memories; --"],
            )

    def test_whitelisted_fields_accepted(self):
        """白名单字段正常工作"""
        from core.storage import StorageEngine

        storage = StorageEngine(db_path=self.db_path)
        # 先插入一条记忆
        entry = storage.add_memory(content="测试记忆")
        self.assertIsNotNone(entry.id)

        # 白名单字段更新应成功
        count = storage.bulk_update_memory_fields(
            [{"id": entry.id, "strength": 0.42}],
            ["strength"],
        )
        self.assertEqual(count, 1)

        # 验证更新结果
        result = storage.get_memory(entry.id)
        self.assertAlmostEqual(result.strength, 0.42, places=2)

    def test_empty_inputs_return_zero(self):
        """空列表返回 0"""
        from core.storage import StorageEngine

        storage = StorageEngine(db_path=self.db_path)
        self.assertEqual(storage.bulk_update_memory_fields([], ["strength"]), 0)
        self.assertEqual(storage.bulk_update_memory_fields([{"id": "x"}], []), 0)

    def test_allowed_fields_set_contents(self):
        """验证白名单集合包含预期字段"""
        from core.storage import _sanitize_html  # noqa: F401 — just importing storage module

        # 通过读取源码验证白名单
        source = inspect.getsource(
            __import__("core.storage", fromlist=["StorageEngine"]).StorageEngine.bulk_update_memory_fields
        )
        self.assertIn("strength", source)
        self.assertIn("forgetting_score", source)
        self.assertIn("metadata", source)
        self.assertIn("consolidation_count", source)
        self.assertIn("last_accessed_at", source)
        self.assertIn("updated_at", source)
        self.assertIn("ALLOWED_FIELDS", source)


if __name__ == "__main__":
    unittest.main()
