#!/usr/bin/env python3
"""
MindForge v5.6.0 测试套件 — KDF 参数版本化 & rekey

覆盖内容：
1. KDFParams 数据类（序列化、向后兼容）
2. EncryptionEngine 携带 KDF 参数
3. EncryptedBlob 密文头 KDF 参数往返
4. 不同迭代次数的密钥不能互相解密
5. init_engine 新密钥使用当前推荐迭代次数
6. get_key_params 兼容 legacy / 新格式密钥文件
7. rekey_engine 密码变更 + KDF 升级
8. Storage 层 rekey_memories 批量重加密
"""

import os
import sys
import json
import base64
import tempfile
import shutil
import unittest

# 确保项目根目录在 path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# 仅在 cryptography 可用时运行加密相关测试
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False


@unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装，跳过加密测试")
class TestKDFParams(unittest.TestCase):
    """KDFParams 数据类测试"""

    def test_default_values(self):
        """默认值应为当前推荐参数"""
        from core.encryption import KDFParams, PBKDF2_ITERATIONS_CURRENT, KDF_ALGORITHM
        params = KDFParams()
        self.assertEqual(params.algorithm, KDF_ALGORITHM)
        self.assertEqual(params.iterations, PBKDF2_ITERATIONS_CURRENT)
        self.assertEqual(params.salt_length, 16)

    def test_to_dict_from_dict_roundtrip(self):
        """序列化 / 反序列化往返一致"""
        from core.encryption import KDFParams
        params = KDFParams(algorithm="PBKDF2-SHA256", iterations=123456, salt_length=32)
        d = params.to_dict()
        self.assertEqual(d["algorithm"], "PBKDF2-SHA256")
        self.assertEqual(d["iterations"], 123456)
        self.assertEqual(d["salt_length"], 32)

        restored = KDFParams.from_dict(d)
        self.assertEqual(restored.algorithm, params.algorithm)
        self.assertEqual(restored.iterations, params.iterations)
        self.assertEqual(restored.salt_length, params.salt_length)

    def test_from_dict_legacy_format(self):
        """向后兼容：只有 iterations 字段的旧格式"""
        from core.encryption import KDFParams, PBKDF2_ITERATIONS_LEGACY, KDF_ALGORITHM
        # v5.5.x 及之前的密钥文件只有 iterations
        legacy_data = {"iterations": 60000}
        params = KDFParams.from_dict(legacy_data)
        self.assertEqual(params.iterations, 60000)
        self.assertEqual(params.algorithm, KDF_ALGORITHM)
        self.assertEqual(params.salt_length, 16)

    def test_from_dict_empty_defaults_to_legacy(self):
        """完全空的 dict 应使用 legacy 迭代次数（安全兜底）"""
        from core.encryption import KDFParams, PBKDF2_ITERATIONS_LEGACY
        params = KDFParams.from_dict({})
        self.assertEqual(params.iterations, PBKDF2_ITERATIONS_LEGACY)


@unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装，跳过加密测试")
class TestEncryptionEngineKDF(unittest.TestCase):
    """EncryptionEngine KDF 参数版本化测试"""

    def test_from_password_carries_kdf_params(self):
        """from_password 创建的引擎应携带 KDF 参数"""
        from core.encryption import EncryptionEngine, PBKDF2_ITERATIONS_CURRENT
        engine, salt = EncryptionEngine.from_password("testpass")
        self.assertIsNotNone(engine.kdf_params)
        self.assertEqual(engine.kdf_params.iterations, PBKDF2_ITERATIONS_CURRENT)

    def test_legacy_iterations_engine(self):
        """使用 legacy 迭代次数创建的引擎参数正确"""
        from core.encryption import EncryptionEngine, PBKDF2_ITERATIONS_LEGACY
        engine, salt = EncryptionEngine.from_password(
            "testpass", iterations=PBKDF2_ITERATIONS_LEGACY
        )
        self.assertEqual(engine.kdf_params.iterations, PBKDF2_ITERATIONS_LEGACY)

    def test_encrypt_stamps_kdf_params_on_blob(self):
        """加密生成的 blob 应携带引擎的 KDF 参数"""
        from core.encryption import EncryptionEngine, PBKDF2_ITERATIONS_LEGACY
        engine, _ = EncryptionEngine.from_password(
            "testpass", iterations=PBKDF2_ITERATIONS_LEGACY
        )
        blob = engine.encrypt("hello world")
        self.assertIsNotNone(blob.kdf_params)
        self.assertEqual(blob.kdf_params.iterations, PBKDF2_ITERATIONS_LEGACY)

    def test_different_iterations_produce_different_keys(self):
        """不同迭代次数应派生不同的密钥（不能互相解密）"""
        from core.encryption import (
            EncryptionEngine, PBKDF2_ITERATIONS_CURRENT,
            PBKDF2_ITERATIONS_LEGACY, SecurityError
        )
        password = "samepassword"
        salt = b"\x01" * 16  # 相同盐

        eng_legacy, _ = EncryptionEngine.from_password(
            password, salt=salt, iterations=PBKDF2_ITERATIONS_LEGACY
        )
        eng_current, _ = EncryptionEngine.from_password(
            password, salt=salt, iterations=PBKDF2_ITERATIONS_CURRENT
        )

        blob = eng_legacy.encrypt("secret data")
        # current 引擎无法解密 legacy 加密的数据
        with self.assertRaises(SecurityError):
            eng_current.decrypt(blob)

    def test_blob_to_dict_from_dict_preserves_kdf_params(self):
        """EncryptedBlob 序列化反序列化保留 kdf_params"""
        from core.encryption import EncryptionEngine
        engine, _ = EncryptionEngine.from_password("testpass")
        blob = engine.encrypt("test content")
        self.assertIsNotNone(blob.kdf_params)

        d = blob.to_dict()
        self.assertIn("kdf_params", d)
        self.assertEqual(d["kdf_params"]["iterations"], blob.kdf_params.iterations)

        restored = type(blob).from_dict(d)
        self.assertIsNotNone(restored.kdf_params)
        self.assertEqual(restored.kdf_params.iterations, blob.kdf_params.iterations)

    def test_legacy_blob_without_kdf_params(self):
        """旧格式 blob（无 kdf_params 字段）仍可正常解密"""
        from core.encryption import EncryptionEngine, EncryptedBlob
        engine, _ = EncryptionEngine.from_password("testpass")
        blob = engine.encrypt("legacy format test")

        # 模拟旧格式：去掉 kdf_params
        d = blob.to_dict()
        del d["kdf_params"]

        legacy_blob = EncryptedBlob.from_dict(d)
        self.assertIsNone(legacy_blob.kdf_params)
        # 仍可正常解密
        plaintext = engine.decrypt(legacy_blob)
        self.assertEqual(plaintext, "legacy format test")


@unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装，跳过加密测试")
class TestKeyFileOperations(unittest.TestCase):
    """密钥文件操作测试（init_engine / get_key_params / rekey_engine）"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.key_file = os.path.join(self.tmpdir, ".key")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init_engine_creates_current_params(self):
        """新创建的密钥文件使用当前推荐迭代次数"""
        from core.encryption import init_engine, get_key_params, PBKDF2_ITERATIONS_CURRENT
        engine = init_engine("mypassword", self.key_file)
        self.assertIsNotNone(engine)

        params = get_key_params(self.key_file)
        self.assertIsNotNone(params)
        self.assertEqual(params.iterations, PBKDF2_ITERATIONS_CURRENT)

    def test_init_engine_reads_legacy_key_file(self):
        """能读取 legacy 格式的密钥文件并使用其迭代次数"""
        from core.encryption import (
            init_engine, get_key_params, PBKDF2_ITERATIONS_LEGACY,
            EncryptionEngine, KDF_ALGORITHM
        )
        password = "legacypass"
        salt = os.urandom(16)

        # 手动写一个 v5.5.x 格式的密钥文件
        eng, _ = EncryptionEngine.from_password(
            password, salt=salt, iterations=PBKDF2_ITERATIONS_LEGACY
        )
        legacy_key_data = {
            "salt": base64.b64encode(salt).decode(),
            "version": "5.0",
            "kdf": KDF_ALGORITHM,
            "iterations": PBKDF2_ITERATIONS_LEGACY,
        }
        with open(self.key_file, "w", encoding="utf-8") as f:
            json.dump(legacy_key_data, f, indent=2)

        # 验证 get_key_params 能正确读取
        params = get_key_params(self.key_file)
        self.assertEqual(params.iterations, PBKDF2_ITERATIONS_LEGACY)

        # 验证 init_engine 能用 legacy 参数初始化（密码正确则成功）
        engine = init_engine(password, self.key_file)
        self.assertIsNotNone(engine)

        # 验证引擎能加解密（证明密钥派生正确）
        blob = engine.encrypt("legacy test")
        decrypted = engine.decrypt(blob)
        self.assertEqual(decrypted, "legacy test")

    def test_rekey_engine_upgrades_iterations(self):
        """rekey_engine 可将 legacy 迭代次数升级到当前推荐值"""
        from core.encryption import (
            init_engine, rekey_engine, get_key_params,
            PBKDF2_ITERATIONS_CURRENT, PBKDF2_ITERATIONS_LEGACY,
            EncryptionEngine, KDF_ALGORITHM,
        )
        password = "rekeytest"
        salt = os.urandom(16)

        # 创建 legacy 密钥文件
        eng, _ = EncryptionEngine.from_password(
            password, salt=salt, iterations=PBKDF2_ITERATIONS_LEGACY
        )
        legacy_key_data = {
            "salt": base64.b64encode(salt).decode(),
            "version": "5.0",
            "kdf": KDF_ALGORITHM,
            "iterations": PBKDF2_ITERATIONS_LEGACY,
        }
        with open(self.key_file, "w", encoding="utf-8") as f:
            json.dump(legacy_key_data, f, indent=2)

        old_params = get_key_params(self.key_file)
        self.assertEqual(old_params.iterations, PBKDF2_ITERATIONS_LEGACY)

        # 执行 rekey（相同密码，仅升级迭代次数）
        old_engine, new_engine = rekey_engine(
            password, password, key_file=self.key_file
        )

        new_params = get_key_params(self.key_file)
        self.assertEqual(new_params.iterations, PBKDF2_ITERATIONS_CURRENT)

        # 验证备份文件存在
        self.assertTrue(os.path.exists(self.key_file + ".bak"))

        # 旧引擎加密的数据新引擎无法解密（密钥已变更）
        from core.encryption import SecurityError
        blob = old_engine.encrypt("before rekey")
        with self.assertRaises(SecurityError):
            new_engine.decrypt(blob)

        # 重加密后新引擎可解密
        plaintext = old_engine.decrypt(blob)
        new_blob = new_engine.encrypt(plaintext)
        self.assertEqual(new_engine.decrypt(new_blob), plaintext)

    def test_rekey_engine_wrong_old_password_fails(self):
        """旧密码错误时 rekey 应失败"""
        from core.encryption import init_engine, rekey_engine, SecurityError
        init_engine("correctpassword", self.key_file)

        with self.assertRaises(SecurityError):
            rekey_engine("wrongpassword", "newpassword", key_file=self.key_file)

    def test_rekey_engine_no_key_file_fails(self):
        """密钥文件不存在时 rekey 应失败"""
        from core.encryption import rekey_engine, SecurityError
        with self.assertRaises(SecurityError):
            rekey_engine("old", "new", key_file=self.key_file)


@unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装，跳过加密测试")
class TestStorageRekey(unittest.TestCase):
    """Storage 层 rekey_memories 集成测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.key_file = os.path.join(self.tmpdir, ".key")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rekey_memories_reencrypts_all(self):
        """rekey_memories 能重加密所有加密记忆"""
        from core.storage import StorageEngine
        from core.encryption import (
            EncryptionEngine, PBKDF2_ITERATIONS_LEGACY,
            PBKDF2_ITERATIONS_CURRENT,
        )

        password = "testpass"

        # 使用 legacy 迭代次数创建加密引擎
        old_engine, _ = EncryptionEngine.from_password(
            password, iterations=PBKDF2_ITERATIONS_LEGACY
        )

        storage = StorageEngine(db_path=self.db_path, encryption=old_engine, encrypted=True)

        # 添加若干加密记忆
        for i in range(5):
            storage.add_memory(f"记忆内容 #{i}", category="test")

        # 验证加密后能解密（证明加密正常工作）
        entries = storage.list_memories(limit=10)
        self.assertEqual(len(entries), 5)
        for entry in entries:
            content = storage.decrypt_content(entry)
            self.assertTrue(content.startswith("记忆内容 #"))

        # 创建新引擎
        new_engine, _ = EncryptionEngine.from_password(
            password, iterations=PBKDF2_ITERATIONS_CURRENT
        )

        # 执行重加密
        count = storage.rekey_memories(old_engine, new_engine)
        self.assertEqual(count, 5)

        # 验证：使用新引擎的 storage 能正确读取所有记忆
        storage2 = StorageEngine(db_path=self.db_path, encryption=new_engine, encrypted=True)
        results = storage2.list_memories(limit=10)
        self.assertEqual(len(results), 5)
        for entry in results:
            content = storage2.decrypt_content(entry)
            self.assertTrue(content.startswith("记忆内容 #"))
        storage2.close()

        # 验证：旧引擎无法解密新数据
        from core.encryption import SecurityError
        storage_old = StorageEngine(db_path=self.db_path, encryption=old_engine, encrypted=True)
        results_old = storage_old.list_memories(limit=1)
        if results_old:
            with self.assertRaises(SecurityError):
                storage_old.decrypt_content(results_old[0])
        storage_old.close()

        storage.close()

    def test_rekey_memories_empty_database(self):
        """无加密记忆时 rekey_memories 返回 0 且不报错"""
        from core.storage import StorageEngine
        from core.encryption import EncryptionEngine

        engine1, _ = EncryptionEngine.from_password("pass1")
        engine2, _ = EncryptionEngine.from_password("pass2")
        storage = StorageEngine(db_path=self.db_path, encryption=engine1, encrypted=True)

        count = storage.rekey_memories(engine1, engine2)
        self.assertEqual(count, 0)
        storage.close()

    def test_rekey_memories_unencrypted_database(self):
        """非加密数据库 rekey_memories 返回 0"""
        from core.storage import StorageEngine
        from core.encryption import EncryptionEngine

        engine1, _ = EncryptionEngine.from_password("pass1")
        engine2, _ = EncryptionEngine.from_password("pass2")
        storage = StorageEngine(db_path=self.db_path, encryption=None, encrypted=False)

        count = storage.rekey_memories(engine1, engine2)
        self.assertEqual(count, 0)
        storage.close()


if __name__ == "__main__":
    unittest.main()
