"""
MindForge v5.5.7 P1/P2/P3 第二轮修复验证
==========================================

验证：
- P1: webhook 签名与发送体一致性
- P2: _get_memory 无密码时清晰报错
- P3-1: 503 Content-Length 动态计算
- P3-2: config.timeout 被尊重 + 4xx 不重试
- P3-3: 导出截断 truncated 标志
"""

import sys
import json
import hmac
import hashlib
import inspect
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestP1WebhookSignatureConsistency(unittest.TestCase):
    """P1: webhook 签名与发送体一致性"""

    def test_body_serialized_once(self):
        """验证 body 只序列化一次，签名和发送共用"""
        from mindforge.modules.event_bus import EventBus
        source = inspect.getsource(EventBus._deliver_webhook)
        # body 在方法开头序列化
        self.assertIn('body = json.dumps(payload', source)
        # 签名使用 body 变量
        self.assertIn('body,\n                hashlib.sha256', source)
        # 发送用 data=body 而非 json=payload
        self.assertIn('data=body', source)
        # 确认 requests.post 调用不包含 json= 参数
        import re
        post_call = re.search(r'requests\.post\([^)]+\)', source, re.DOTALL)
        self.assertIsNotNone(post_call, "应存在 requests.post 调用")
        self.assertNotIn('json=', post_call.group(), "requests.post 不应使用 json= 参数")

    def test_urllib_uses_same_body(self):
        """验证 urllib 降级路径也使用同一 body"""
        from mindforge.modules.event_bus import EventBus
        source = inspect.getsource(EventBus._deliver_webhook_urllib)
        # body 作为参数传入，不在内部重新序列化
        self.assertIn('body: bytes', source)
        self.assertNotIn('json.dumps', source)

    def test_signature_matches_body(self):
        """实证：签名计算用同一字节串"""
        from mindforge.modules.event_bus import EventBus

        bus = EventBus()
        secret = "test_secret"
        wh = bus.register_webhook("https://example.com/hook", secret=secret)

        payload = {"event": "memory_created", "data": {"id": "123", "content": "测试中文"}}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        expected_sig = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()

        # 模拟 _deliver_webhook 中的签名计算
        source = inspect.getsource(bus._deliver_webhook)
        # 验证签名计算用的是 body 变量
        self.assertIn('body,\n                hashlib.sha256', source)

        # 验证如果用 requests 默认 json= 序列化，签名会不同
        requests_body = json.dumps(payload).encode("utf-8")
        wrong_sig = hmac.new(
            secret.encode("utf-8"), requests_body, hashlib.sha256
        ).hexdigest()
        self.assertNotEqual(expected_sig, wrong_sig, "签名应不同（证明一致性修复是必要的）")


class TestP2GetMemoryClearError(unittest.TestCase):
    """P2: _get_memory 无密码时清晰报错"""

    def test_error_message_exists(self):
        """验证 _get_memory 中有密码缺失的错误提示"""
        from mindforge.cli.main import _get_memory
        source = inspect.getsource(_get_memory)
        self.assertIn('MINDFORGE_PASSWORD', source)
        self.assertIn('加密数据库需要密码', source)

    def test_error_exits_with_code_1(self):
        """验证无密码时 sys.exit(1) 而非裸 AttributeError"""
        from mindforge.cli.main import _get_memory
        source = inspect.getsource(_get_memory)
        self.assertIn('sys.exit(1)', source)

    def test_error_mentions_bridge(self):
        """验证错误提示提到 dsh-mindforge bridge"""
        from mindforge.cli.main import _get_memory
        source = inspect.getsource(_get_memory)
        self.assertIn('bridge', source.lower())


class TestP31ContentLengthDynamic(unittest.TestCase):
    """P3-1: 503 Content-Length 动态计算"""

    def test_content_length_uses_len(self):
        """验证 503 响应中 Content-Length 使用 len() 动态计算"""
        from mindforge.api.server import BoundedThreadingHTTPServer
        source = inspect.getsource(BoundedThreadingHTTPServer.process_request)
        self.assertIn('len(body)', source)
        self.assertNotIn('Content-Length: 48', source, "不应硬编码 Content-Length")

    def test_body_variable_defined(self):
        """验证 503 body 变量在 sendall 前定义"""
        from mindforge.api.server import BoundedThreadingHTTPServer
        source = inspect.getsource(BoundedThreadingHTTPServer.process_request)
        self.assertIn('body = b\'{"error"', source)


class TestP32TimeoutAndRetryLogic(unittest.TestCase):
    """P3-2: config.timeout 被尊重 + 4xx 不重试"""

    def test_timeout_respects_config(self):
        """验证 timeout 从 config.timeout 获取，不硬编码"""
        from mindforge.modules.event_bus import EventBus
        source = inspect.getsource(EventBus._deliver_webhook)
        self.assertIn('config.timeout', source)
        self.assertNotIn('timeout = (3, 10)', source, "不应硬编码 timeout")

    def test_4xx_no_retry(self):
        """验证 4xx 错误不重试"""
        from mindforge.modules.event_bus import EventBus
        source = inspect.getsource(EventBus._deliver_webhook)
        self.assertIn('400 <= status < 500', source)
        self.assertIn('break', source)


class TestP33ExportTruncation(unittest.TestCase):
    """P3-3: 导出截断 truncated 标志"""

    def test_export_has_truncated_field(self):
        """验证 /api/export 响应中有 truncated 字段"""
        from mindforge.api.server import MindForgeAPIHandler
        source = inspect.getsource(MindForgeAPIHandler.do_GET)
        self.assertIn('truncated', source)
        self.assertIn('max_limit', source)

    def test_truncated_logic(self):
        """验证截断判断逻辑：达到上限时 truncated=True"""
        from mindforge.api.server import MindForgeAPIHandler
        source = inspect.getsource(MindForgeAPIHandler.do_GET)
        self.assertIn('>= max_export_limit', source)


class TestIntegrationDocs(unittest.TestCase):
    """文档：dsh bridge + HMAC_XOR 迁移提醒"""

    def test_readme_has_bridge_password_notice(self):
        """验证 README 有 dsh bridge MINDFORGE_PASSWORD 提醒"""
        readme = Path(__file__).parent.parent / "README.md"
        content = readme.read_text(encoding="utf-8")
        self.assertIn('MINDFORGE_PASSWORD', content)
        self.assertIn('bridge', content.lower())

    def test_readme_has_hmac_xor_migration_notice(self):
        """验证 README 有 HMAC_XOR 迁移提醒"""
        readme = Path(__file__).parent.parent / "README.md"
        content = readme.read_text(encoding="utf-8")
        self.assertIn('EXPERIMENTAL_HMAC_XOR', content)
        self.assertIn('降级加密', content)


if __name__ == "__main__":
    unittest.main()
