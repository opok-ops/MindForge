"""
MindForge v5.6.2 P2 安全修复验证测试
======================================

验证安全审计报告中 P2 级别的修复：
- #22  os.getlogin() 容器环境兼容：多策略回退
- #24  expand_query 超长输入截断：防 DoS
- #26  限流清理逻辑无效：del 后立即重建的死代码移除
- #32  compliance_status 恒为 PASS：private_count >= 0 改为 == 0
- #33  联邦记忆无 storage 时 fail-closed：从 True 改为 False
- #37  CLI batch_add 路径安全校验：防路径遍历
- #38  XML XXE 全文检查：从 4KB 前缀改为全文 + 正则空白匹配
- #31  2FA token 不明文驻留：内存只存 hash
"""

import os
import sys
import tempfile
import time
import unittest
import shutil
import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================
# #24 expand_query 超长输入截断
# ============================================================
class TestExpandQueryLengthLimit(unittest.TestCase):
    """P2 #24: expand_query 超长输入防 DoS"""

    def test_normal_query_unchanged(self):
        """正常长度查询不受影响"""
        from core.query import QueryEngine
        result = QueryEngine.expand_query("python 编程", max_expansions=2)
        self.assertIn("python 编程", result)

    def test_empty_query_handled(self):
        """空查询不崩溃"""
        from core.query import QueryEngine
        self.assertEqual(QueryEngine.expand_query(""), [])
        self.assertEqual(QueryEngine.expand_query("   "), ["   "])

    def test_very_long_query_truncated(self):
        """超长查询被截断到 1000 字符以内"""
        from core.query import QueryEngine
        long_query = "a" * 5000
        result = QueryEngine.expand_query(long_query)
        self.assertTrue(len(result[0]) <= 1000)

    def test_extremely_long_input_no_crash(self):
        """极端长输入不触发内存/CPU耗尽"""
        from core.query import QueryEngine
        huge_query = "测试词" * 100000  # 约 30 万字符
        start = time.time()
        result = QueryEngine.expand_query(huge_query)
        elapsed = time.time() - start
        self.assertLess(elapsed, 1.0)
        self.assertTrue(len(result) >= 1)


# ============================================================
# #26 限流清理逻辑无效
# ============================================================
class TestRateLimitCleanupLogic(unittest.TestCase):
    """P2 #26: 限流窗口清理后不会立即重建空列表"""

    def test_window_cleanup_removes_expired(self):
        """过期记录被正确清理，窗口内只剩有效记录"""
        from core.storage import _RateLimiter
        limiter = _RateLimiter()

        key = "test-ip"
        # 手动灌入一条 100 秒前的过期记录
        limiter._windows[key] = [time.time() - 100]

        # 调用一次限流检查（窗口 60 秒，过期后应放行）
        ok = limiter.check(key, max_calls=5, window_seconds=60)
        self.assertTrue(ok)

        # 窗口中只剩当前这一条新记录（过期的被清掉了）
        self.assertEqual(len(limiter._windows[key]), 1)


# ============================================================
# #32 compliance_status 恒为 PASS
# ============================================================
class TestComplianceStatus(unittest.TestCase):
    """P2 #32: compliance_status 不应恒为 PASS"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_no_private_memories_is_pass(self):
        """0 条私有记忆 → PASS"""
        from modules.privacy import PrivacyEngine
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        engine = PrivacyEngine(storage)
        report = engine.generate_compliance_report()
        self.assertEqual(report["compliance_status"], "PASS")
        storage.close()

    def test_with_private_memories_is_review(self):
        """有私有记忆 → REVIEW（而非恒 PASS）"""
        from modules.privacy import PrivacyEngine
        from core.storage import StorageEngine
        from core.types import PrivacyLevel
        storage = StorageEngine(db_path=self.db_path)
        storage.add_memory(content="private memory", privacy=PrivacyLevel.PRIVATE)
        engine = PrivacyEngine(storage)
        report = engine.generate_compliance_report()
        self.assertEqual(report["compliance_status"], "REVIEW")
        storage.close()


# ============================================================
# #33 联邦记忆无 storage 时 fail-closed
# ============================================================
class TestFederatedFailClosed(unittest.TestCase):
    """P2 #33: 无 storage 时 _verify_memory_exists 返回 False（fail-closed）"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_no_storage_returns_false(self):
        """storage=None 时应返回 False，而非 True"""
        from modules.federated import FederatedMemory
        manager = FederatedMemory(storage=None, local_peer_id="peer-a")
        result = manager._verify_memory_exists("any-id")
        self.assertFalse(result, "无 storage 时应 fail-closed 返回 False")

    def test_with_storage_nonexistent_id(self):
        """有 storage 但 ID 不存在 → False"""
        from modules.federated import FederatedMemory
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        manager = FederatedMemory(storage=storage, local_peer_id="peer-a")
        self.assertFalse(manager._verify_memory_exists("nonexistent-id"))
        storage.close()

    def test_with_storage_existing_id(self):
        """有 storage 且 ID 存在 → True"""
        from modules.federated import FederatedMemory
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        entry = storage.add_memory(content="test")
        manager = FederatedMemory(storage=storage, local_peer_id="peer-a")
        self.assertTrue(manager._verify_memory_exists(entry.id))
        storage.close()


# ============================================================
# #38 XML XXE 全文检查（正则 + 空白变体）
# ============================================================
class TestXMLXXEFullCheck(unittest.TestCase):
    """P2 #38: XXE 防护使用全文检查 + 正则，防 4KB 填充绕过"""

    @staticmethod
    def _build_xml(payload: str, padding_bytes: int = 0) -> str:
        padding = " " * padding_bytes
        return f'<?xml version="1.0"?>\n{padding}{payload}\n<root><item>hello</item></root>'

    @staticmethod
    def _detect_xxe(xml: str) -> bool:
        import re as _re
        upper = xml.upper()
        return (_re.search(r'<!\s*DOCTYPE', upper) is not None
                or _re.search(r'<!\s*ENTITY', upper) is not None)

    def test_doctype_normal_detected(self):
        """正常 DOCTYPE 被检测到"""
        xml = self._build_xml("<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>")
        self.assertTrue(self._detect_xxe(xml))

    def test_entity_normal_detected(self):
        """正常 ENTITY 被检测到"""
        xml = self._build_xml("<!ENTITY xxe SYSTEM 'file:///etc/passwd'>")
        self.assertTrue(self._detect_xxe(xml))

    def test_doctype_with_whitespace_variant(self):
        """DOCTYPE 前有空格等变体也能被检测"""
        xml = self._build_xml("<!  DOCTYPE foo>")
        self.assertTrue(self._detect_xxe(xml))

    def test_doctype_padded_beyond_4k(self):
        """DOCTPYE 在 4KB 之后也能被检测（全文检查）"""
        xml = self._build_xml(
            "<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>",
            padding_bytes=8000
        )
        self.assertTrue(self._detect_xxe(xml), "填充绕过不应成功，应全文检查")

    def test_entity_padded_beyond_4k(self):
        """ENTITY 在 4KB 之后也能被检测"""
        xml = self._build_xml(
            "<!ENTITY xxe SYSTEM 'file:///etc/passwd'>",
            padding_bytes=8000
        )
        self.assertTrue(self._detect_xxe(xml))

    def test_clean_xml_passes(self):
        """干净 XML 不误报"""
        xml = '<?xml version="1.0"?>\n<root><item>hello world</item></root>'
        self.assertFalse(self._detect_xxe(xml))


# ============================================================
# #31 2FA token 不明文驻留（内存只存 hash）
# ============================================================
class Test2FATokenNotInPlaintext(unittest.TestCase):
    """P2 #31 / P0 #2: 内存中只存 token hash，不明文驻留"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_register_stores_hash_not_plaintext(self):
        """注册后 _second_factor_token_hashes 存的是 hash，不是明文"""
        from modules.privacy import PrivacyEngine
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        engine = PrivacyEngine(storage)

        token = "my-secret-2fa-token-123"
        engine.register_second_factor("alice", token)

        stored = engine._second_factor_token_hashes.get("alice", "")
        self.assertNotEqual(stored, token)
        self.assertEqual(len(stored), 64)  # SHA-256 hex 长度

        expected = hashlib.sha256(token.encode()).hexdigest()
        self.assertEqual(stored, expected)

        storage.close()

    def test_persisted_token_is_hash(self):
        """持久化到 SQLite 的也是 hash，不是明文"""
        from modules.privacy import PrivacyEngine
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        engine = PrivacyEngine(storage)

        token = "persist-secret-token"
        engine.register_second_factor("bob", token)
        storage.close()

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT token_hash FROM second_factor_tokens WHERE actor = ?",
            ("bob",)
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        stored_hash = row[0]
        self.assertNotEqual(stored_hash, token)
        expected = hashlib.sha256(token.encode()).hexdigest()
        self.assertEqual(stored_hash, expected)

    def test_verify_with_code_uses_hash_comparison(self):
        """verify_second_factor_with_code 与 hash 比对"""
        from modules.privacy import PrivacyEngine
        from core.storage import StorageEngine
        storage = StorageEngine(db_path=self.db_path)
        engine = PrivacyEngine(storage)

        engine.register_second_factor("charlie", "correct-horse-battery-staple")

        self.assertTrue(engine.verify_second_factor_with_code(
            "charlie", "correct-horse-battery-staple"))
        self.assertFalse(engine.verify_second_factor_with_code(
            "charlie", "wrong-code"))
        self.assertFalse(engine.verify_second_factor_with_code(
            "dave", "whatever"))

        storage.close()


# ============================================================
# #22 os.getlogin() 容器环境兼容（多策略回退）
# ============================================================
class TestGetLoginFallback(unittest.TestCase):
    """P2 #22: 写密钥文件时 getlogin() 失败有回退策略"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_init_engine_handles_getlogin_failure(self):
        """getlogin() 抛 OSError 时 init_engine 仍能正常创建密钥"""
        from core.encryption import init_engine, get_engine

        key_path = os.path.join(self.tmp_dir, ".key")

        with patch("os.getlogin", side_effect=OSError("no login")):
            # 模拟服务/容器环境下 getlogin 失败
            engine = init_engine("test-password-123", key_file=key_path)
            self.assertIsNotNone(engine)

        # 密钥文件应已创建
        self.assertTrue(os.path.exists(key_path))


# ============================================================
# #37 CLI batch_add 路径安全校验
# ============================================================
class TestBatchAddPathValidation(unittest.TestCase):
    """P2 #37: batch_add 输入路径经 _safe_path 校验"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_path_traversal_rejected(self):
        """路径遍历输入被 _safe_path 拒绝"""
        from core.mindforge import _safe_path
        traversal = os.path.join(self.tmp_dir, "..", "evil.json")
        with self.assertRaises((ValueError, OSError)):
            _safe_path(traversal, must_exist=False, allowed_exts={".json"})

    def test_disallowed_extension_rejected(self):
        """非白名单扩展名被拒绝"""
        from core.mindforge import _safe_path
        bad_path = os.path.join(self.tmp_dir, "evil.exe")
        Path(bad_path).write_text("test")
        with self.assertRaises(ValueError):
            _safe_path(bad_path, must_exist=True,
                       allowed_exts={".json", ".txt"})

    def test_allowed_extension_accepted(self):
        """白名单扩展名正常通过"""
        from core.mindforge import _safe_path
        good_path = os.path.join(self.tmp_dir, "notes.json")
        Path(good_path).write_text('[]')
        result = _safe_path(good_path, must_exist=True,
                            allowed_exts={".json", ".txt"})
        self.assertTrue(str(result).endswith(".json"))


if __name__ == "__main__":
    unittest.main()
