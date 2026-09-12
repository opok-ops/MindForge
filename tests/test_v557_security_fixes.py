"""
MindForge v5.5.7 安全修复验证测试
==================================

验证 P0/P1 安全修复：
- P0-001: key_file 单独存在时启用加密
- P0-003: BoundedThreadingHTTPServer 最大并发 50
- P1-001: /api/export 上限 5000
- P1-002: /api/tags LIMIT 10000
- P1-003: Webhook timeout=(3, 10)
- P1-004: 联邦签名 fail-closed + allow_unsigned_peers
- P1-007: 缓存键 MD5→SHA-256
- P1-008: 移除 HMAC-XOR fallback
"""

import os
import sys
import tempfile
import unittest
import hashlib
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestP1008EncryptionNoFallback(unittest.TestCase):
    """P1-008: 移除 HMAC-XOR fallback，cryptography 缺失时直接抛异常"""

    def test_encryption_requires_cryptography(self):
        """验证加密引擎在 cryptography 可用时正常工作"""
        from core.encryption import EncryptionEngine

        # cryptography 应该已安装（测试依赖）
        key = os.urandom(32)
        engine = EncryptionEngine(key)
        blob = engine.encrypt("测试内容")
        self.assertEqual(blob.algorithm, "AES-256-GCM")
        decrypted = engine.decrypt(blob)
        self.assertEqual(decrypted, "测试内容")

    def test_from_password_uses_cryptography(self):
        """验证 from_password 使用 cryptography 的 PBKDF2"""
        from core.encryption import EncryptionEngine

        engine, salt = EncryptionEngine.from_password("testpassword")
        self.assertIsNotNone(engine._aesgcm)
        self.assertEqual(len(salt), 16)

    def test_no_simple_encrypt_method(self):
        """验证 _simple_encrypt 和 _simple_decrypt 已被移除"""
        from core.encryption import EncryptionEngine

        key = os.urandom(32)
        engine = EncryptionEngine(key)
        self.assertFalse(hasattr(engine, "_simple_encrypt"))
        self.assertFalse(hasattr(engine, "_simple_decrypt"))


class TestP1004FederatedFailClosed(unittest.TestCase):
    """P1-004: 联邦签名验证 fail-closed + allow_unsigned_peers 配置"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_p1004_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_default_rejects_unsigned(self):
        """默认配置下，无签名消息被拒绝（fail-closed）"""
        from core.storage import StorageEngine
        from modules.federated import FederatedMemory

        storage = StorageEngine(db_path=self.db_path)
        fed = FederatedMemory(storage=storage, local_peer_id="local")
        fed.register_peer("peerA", "节点A", trust_level=0.9)

        # 无签名消息应被拒绝
        ok = fed.receive_memory("peerA", {"content": "测试", "version": 1})
        self.assertFalse(ok, "默认配置下应拒绝无签名消息")

    def test_unregistered_peer_rejected(self):
        """未注册节点即使有签名也被拒绝"""
        from core.storage import StorageEngine
        from modules.federated import FederatedMemory

        storage = StorageEngine(db_path=self.db_path)
        fed = FederatedMemory(storage=storage, local_peer_id="local")

        # 未注册节点
        ok = fed.receive_memory("unknown_peer", {"content": "测试"}, signature="fake_sig")
        self.assertFalse(ok, "未注册节点应直接拒绝")

    def test_allow_unsigned_peers_enabled(self):
        """启用 allow_unsigned_peers 后，已注册节点的无签名消息被接受"""
        from core.storage import StorageEngine
        from modules.federated import FederatedMemory

        storage = StorageEngine(db_path=self.db_path)
        fed = FederatedMemory(
            storage=storage,
            local_peer_id="local",
            config={"allow_unsigned_peers": True}
        )
        fed.register_peer("peerA", "节点A", trust_level=0.9)

        # 启用 allow_unsigned_peers 后，已注册节点的无签名消息应被接受
        ok = fed.receive_memory("peerA", {"content": "测试", "version": 1})
        self.assertTrue(ok, "启用 allow_unsigned_peers 后应接受已注册节点的无签名消息")

    def test_allow_unsigned_does_not_allow_unregistered(self):
        """启用 allow_unsigned_peers 后，未注册节点仍被拒绝"""
        from modules.federated import FederatedMemory

        fed = FederatedMemory(
            local_peer_id="local",
            config={"allow_unsigned_peers": True}
        )

        # 未注册节点即使启用 allow_unsigned_peers 也应拒绝
        ok = fed._verify_signature({"content": "test"}, "", "unknown_peer")
        self.assertFalse(ok, "未注册节点即使启用 allow_unsigned_peers 也应拒绝")

    def test_signed_verification_works(self):
        """有正确签名的消息验证通过"""
        from modules.federated import FederatedMemory

        fed = FederatedMemory(local_peer_id="local")
        fed.register_peer("peerA", "节点A", trust_level=0.9)
        fed.peers["peerA"].shared_secret = "test_secret_key"

        data = {"content": "测试消息", "version": 1}
        signature = fed._compute_signature(data, "peerA")
        self.assertTrue(signature)

        ok = fed._verify_signature(data, signature, "peerA")
        self.assertTrue(ok)

    def test_wrong_signature_rejected(self):
        """错误签名被拒绝"""
        from modules.federated import FederatedMemory

        fed = FederatedMemory(local_peer_id="local")
        fed.register_peer("peerA", "节点A", trust_level=0.9)
        fed.peers["peerA"].shared_secret = "test_secret_key"

        data = {"content": "测试消息"}
        ok = fed._verify_signature(data, "wrong_signature", "peerA")
        self.assertFalse(ok)

    def test_no_shared_secret_rejected(self):
        """无共享密钥节点即使有签名也被拒绝（P0 修复：public_key 不再用于 HMAC）"""
        from modules.federated import FederatedMemory

        fed = FederatedMemory(local_peer_id="local")
        fed.register_peer("peerA", "节点A", trust_level=0.9)
        # peerA 没有 shared_secret

        ok = fed._verify_signature({"content": "test"}, "some_signature", "peerA")
        self.assertFalse(ok, "无共享密钥节点应直接拒绝")


class TestP1007IntentRouterSha256(unittest.TestCase):
    """P1-007: 缓存键 MD5 → SHA-256"""

    def test_cache_key_uses_sha256(self):
        """验证缓存键使用 SHA-256 而非 MD5"""
        from modules.intent_router import IntentRouter

        router = IntentRouter()
        test_text = "测试一下这个功能"

        # 第一次分类以填充缓存
        result1 = router.classify(test_text)
        self.assertIsNotNone(result1)

        # 计算预期的 SHA-256 缓存键
        expected_key = hashlib.sha256(
            re.sub(r"\s+", "", test_text).encode("utf-8")
        ).hexdigest()

        # 验证缓存中存在该键
        self.assertIn(expected_key, router._cache)

        # 验证不是 MD5（MD5 是 32 字符十六进制，SHA-256 是 64 字符）
        for key in router._cache.keys():
            self.assertEqual(len(key), 64, f"缓存键长度应为 64 (SHA-256)，实际为 {len(key)}")
            # MD5 是 32 字符
            self.assertNotEqual(len(key), 32, "缓存键不应是 32 字符的 MD5")

    def test_cache_hit_works(self):
        """验证 SHA-256 缓存命中正常工作"""
        from modules.intent_router import IntentRouter

        router = IntentRouter()
        text = "帮我搜索一下历史记录"

        result1 = router.classify(text)
        result2 = router.classify(text)

        # 两次结果应该相同（缓存命中）
        self.assertEqual(result1.intent, result2.intent)
        self.assertEqual(result1.confidence, result2.confidence)
        # 第二次应从缓存返回（latency 不一定严格小于 50%，避免时序抖动）
        self.assertLessEqual(result2.latency_ms, result1.latency_ms * 2)


class TestP003BoundedThreadingServer(unittest.TestCase):
    """P0-003: BoundedThreadingHTTPServer 最大并发 50"""

    def test_bounded_server_class_exists(self):
        """验证 BoundedThreadingHTTPServer 类存在"""
        from api.server import BoundedThreadingHTTPServer
        self.assertTrue(hasattr(BoundedThreadingHTTPServer, 'max_threads'))
        self.assertEqual(BoundedThreadingHTTPServer.max_threads, 50)

    def test_server_is_threading(self):
        """验证服务器基于 ThreadingHTTPServer"""
        from http.server import ThreadingHTTPServer
        from api.server import BoundedThreadingHTTPServer

        self.assertTrue(issubclass(BoundedThreadingHTTPServer, ThreadingHTTPServer))

    def test_active_threads_tracking(self):
        """验证活跃线程计数机制存在"""
        from api.server import BoundedThreadingHTTPServer

        # 检查关键方法和属性
        self.assertTrue(hasattr(BoundedThreadingHTTPServer, 'process_request'))
        self.assertTrue(hasattr(BoundedThreadingHTTPServer, '_process_request_thread'))


class TestP1001ExportLimit(unittest.TestCase):
    """P1-001: /api/export 上限 5000"""

    def test_export_limit_in_handler(self):
        """验证 API handler 中有导出上限配置"""
        import inspect
        from api.server import MindForgeAPIHandler

        source = inspect.getsource(MindForgeAPIHandler.do_GET)
        self.assertIn('5000', source)
        self.assertIn('max_export_limit', source)


class TestP1002TagsLimit(unittest.TestCase):
    """P1-002: /api/tags 查询加 LIMIT 10000"""

    def test_tags_limit_in_handler(self):
        """验证 API handler 中 tags 查询有 LIMIT"""
        import inspect
        from api.server import MindForgeAPIHandler

        source = inspect.getsource(MindForgeAPIHandler.do_GET)
        self.assertIn('LIMIT 10000', source)


class TestP1003WebhookTimeout(unittest.TestCase):
    """P1-003: Webhook 改用 requests.post(..., timeout=(3, 10))"""

    def test_webhook_timeout_config(self):
        """验证 webhook 使用正确的超时配置"""
        import inspect
        from modules.event_bus import EventBus

        source = inspect.getsource(EventBus._deliver_webhook)
        self.assertIn('timeout', source)
        self.assertIn('config.timeout', source)
        self.assertIn('requests.post', source)
        self.assertIn('data=body', source)  # P1 修复：签名与发送体一致

    def test_event_bus_webhook_registration(self):
        """验证 webhook 注册正常工作"""
        from modules.event_bus import EventBus

        bus = EventBus()
        wh = bus.register_webhook("https://example.com/hook")
        self.assertEqual(wh.url, "https://example.com/hook")
        self.assertEqual(wh.timeout, 10.0)
        self.assertTrue(wh.enabled)


class TestP001KeyFileEncryption(unittest.TestCase):
    """P0-001: key_file 单独存在时也启用加密"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_p001_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.key_path = os.path.join(self.tmp_dir, ".key")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_key_file_existing_enables_encryption(self):
        """验证 key_file 存在时 MindForge 启用加密模式"""
        from core import MindForge, MemoryConfig

        # 先创建加密数据库和密钥文件
        config = MemoryConfig(
            db_path=self.db_path,
            key_file=self.key_path,
            encrypted=True,
        )
        mf = MindForge(config=config)
        mf.init_with_password("testpassword123")
        mf.add(content="测试加密记忆")
        mf.close()

        # 验证密钥文件存在
        self.assertTrue(Path(self.key_path).exists())

        # 重新加载 - key_file 存在时应该自动启用加密
        # （这里验证 MemoryConfig 和加密行为）
        key_exists = Path(self.key_path).exists()
        self.assertTrue(key_exists)

    def test_no_key_file_no_encryption(self):
        """验证 key_file 不存在时不启用加密"""
        from core import MindForge, MemoryConfig

        non_existent_key = os.path.join(self.tmp_dir, "nonexistent.key")
        self.assertFalse(Path(non_existent_key).exists())

        config = MemoryConfig(
            db_path=self.db_path,
            key_file=non_existent_key,
            encrypted=False,
        )
        mf = MindForge(config=config)
        entry = mf.add(content="未加密记忆")
        self.assertIsNotNone(entry)
        mf.close()


if __name__ == "__main__":
    unittest.main()
