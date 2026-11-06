"""v6.3.1 加密升级回归测试：scrypt KDF + 门面 rekey 修复

覆盖四件事：
1. scrypt 成为新默认 KDF（N=2^16, r=8, p=1，64 MiB 内存墙）
2. 历史密钥文件（PBKDF2 600k / 60k / 仅 iterations 旧格式）解密兼容不变
3. rekey（模块级与门面）默认升级到 scrypt，数据全程可解
4. 门面 rekey 自 v5.8.13 起因 `__import__("core.encryption")` 死路径不可用，
   本版修复——以本文件钉死，防止回归
"""

import json
import os
import shutil
import tempfile
import unittest
import base64

from mindforge.core.encryption import (
    EncryptionEngine,
    KDFParams,
    EncryptedBlob,
    SecurityError,
    init_engine,
    get_key_params,
    rekey_engine,
    _set_global_engine,
    SCRYPT_N_CURRENT,
    SCRYPT_R_CURRENT,
    SCRYPT_P_CURRENT,
    KDF_ALGORITHM_CURRENT,
    KDF_ALGORITHM_LEGACY,
    PBKDF2_ITERATIONS_CURRENT,
    PBKDF2_ITERATIONS_LEGACY,
    KEY_FILE_VERSION,
)


class TestScryptDefaultKDF(unittest.TestCase):
    """v6.3.1：scrypt 成为默认 KDF"""

    def test_default_kdfparams_is_scrypt(self):
        params = KDFParams()
        self.assertEqual(params.algorithm, KDF_ALGORITHM_CURRENT)
        self.assertEqual(params.n, SCRYPT_N_CURRENT)
        self.assertEqual(params.r, SCRYPT_R_CURRENT)
        self.assertEqual(params.p, SCRYPT_P_CURRENT)

    def test_from_password_default_engine_is_scrypt(self):
        engine, _ = EncryptionEngine.from_password("v631-pass")
        self.assertEqual(engine.kdf_params.algorithm, KDF_ALGORITHM_CURRENT)
        self.assertEqual(engine.kdf_params.n, SCRYPT_N_CURRENT)

    def test_scrypt_roundtrip(self):
        engine, _ = EncryptionEngine.from_password("roundtrip-pass")
        blob = engine.encrypt("scrypt roundtrip 秘密数据 v6.3.1")
        self.assertEqual(blob.kdf_params.algorithm, KDF_ALGORITHM_CURRENT)
        self.assertEqual(engine.decrypt(blob), "scrypt roundtrip 秘密数据 v6.3.1")

    def test_scrypt_deterministic_same_salt(self):
        """同 salt 同密码 → 同密钥（密文可互解）"""
        salt = os.urandom(16)
        params = KDFParams(salt_length=len(salt))
        e1, _ = EncryptionEngine.from_password("same-pass", salt, kdf_params=params)
        e2, _ = EncryptionEngine.from_password("same-pass", salt, kdf_params=params)
        blob = e1.encrypt("cross engine decrypt")
        self.assertEqual(e2.decrypt(blob), "cross engine decrypt")

    def test_scrypt_wrong_password_fails(self):
        e1, salt = EncryptionEngine.from_password("right-pass")
        blob = e1.encrypt("secret")
        e2, _ = EncryptionEngine.from_password(
            "wrong-pass", salt, kdf_params=KDFParams()
        )
        with self.assertRaises(SecurityError):
            e2.decrypt(blob)

    def test_kdfparams_scrypt_dict_roundtrip(self):
        params = KDFParams(algorithm="scrypt", iterations=65536,
                           salt_length=16, n=65536, r=8, p=1)
        d = params.to_dict()
        self.assertEqual(d["n"], 65536)
        self.assertEqual(d["r"], 8)
        self.assertEqual(d["p"], 1)
        restored = KDFParams.from_dict(d)
        self.assertEqual(restored.to_dict(), d)

    def test_kdfparams_from_dict_pbkdf2_has_no_nrp_semantics(self):
        """PBKDF2 参数从 dict 恢复：algorithm 保持 PBKDF2"""
        d = {"algorithm": "PBKDF2-SHA256", "iterations": 600000, "salt_length": 16}
        params = KDFParams.from_dict(d)
        self.assertEqual(params.algorithm, KDF_ALGORITHM_LEGACY)
        self.assertEqual(params.iterations, 600000)


class TestKeyFileScrypt(unittest.TestCase):
    """新密钥文件格式 v6.3（scrypt）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.key_file = os.path.join(self.tmp, ".key")
        _set_global_engine(None)

    def tearDown(self):
        _set_global_engine(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_key_file_is_scrypt_v63(self):
        init_engine("new-pass", key_file=self.key_file)
        with open(self.key_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("version"), KEY_FILE_VERSION)
        self.assertEqual(data["kdf_params"]["algorithm"], KDF_ALGORITHM_CURRENT)
        self.assertEqual(data["kdf_params"]["n"], SCRYPT_N_CURRENT)

        params = get_key_params(self.key_file)
        self.assertEqual(params.algorithm, KDF_ALGORITHM_CURRENT)

    def test_reload_scrypt_key_file(self):
        engine1 = init_engine("reload-pass", key_file=self.key_file)
        blob = engine1.encrypt("persist secret")
        # 重新加载（模拟重启）
        engine2 = init_engine("reload-pass", key_file=self.key_file)
        self.assertEqual(engine2.decrypt(blob), "persist secret")

    def test_wrong_password_rejected(self):
        init_engine("correct-pass", key_file=self.key_file)
        with self.assertRaises(SecurityError):
            init_engine("wrong-pass", key_file=self.key_file)


class TestLegacyKeyFileCompat(unittest.TestCase):
    """历史密钥文件（PBKDF2 各代）解密兼容不变"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.key_file = os.path.join(self.tmp, ".key")
        _set_global_engine(None)

    def tearDown(self):
        _set_global_engine(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_key_file(self, algorithm, iterations, with_params=True):
        salt = os.urandom(16)
        engine, _ = EncryptionEngine.from_password(
            "legacy-pass", salt, iterations=iterations
        )
        blob = engine.encrypt("MindForge-Key-Verify-v1")
        data = {
            "salt": base64.b64encode(salt).decode(),
            "version": "5.6" if with_params else "5.0",
            "kdf": algorithm,
            "iterations": iterations,
            "verify_blob": blob.to_dict(),
        }
        if with_params:
            data["kdf_params"] = {
                "algorithm": algorithm,
                "iterations": iterations,
                "salt_length": 16,
            }
        with open(self.key_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return engine

    def test_pbkdf2_600k_key_file_loads(self):
        old_engine = self._write_key_file("PBKDF2-SHA256", 600000, with_params=True)
        blob = old_engine.encrypt("legacy 600k secret")
        engine = init_engine("legacy-pass", key_file=self.key_file)
        self.assertEqual(engine.decrypt(blob), "legacy 600k secret")
        self.assertEqual(engine.kdf_params.algorithm, KDF_ALGORITHM_LEGACY)

    def test_pbkdf2_60k_legacy_key_file_loads(self):
        old_engine = self._write_key_file("PBKDF2-SHA256", 60000, with_params=False)
        blob = old_engine.encrypt("legacy 60k secret")
        engine = init_engine("legacy-pass", key_file=self.key_file)
        self.assertEqual(engine.decrypt(blob), "legacy 60k secret")

    def test_iterations_only_ancient_format(self):
        """极旧格式：只有 salt + iterations，无 kdf/algorithm 字段"""
        salt = os.urandom(16)
        engine, _ = EncryptionEngine.from_password(
            "ancient-pass", salt, iterations=60000
        )
        data = {
            "salt": base64.b64encode(salt).decode(),
            "iterations": 60000,
        }
        with open(self.key_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        restored = init_engine("ancient-pass", key_file=self.key_file)
        self.assertEqual(restored.kdf_params.iterations, 60000)


class TestRekeyUpgradeToScrypt(unittest.TestCase):
    """rekey：PBKDF2 → scrypt 迁移路径"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.key_file = os.path.join(self.tmp, ".key")
        _set_global_engine(None)

    def tearDown(self):
        _set_global_engine(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rekey_engine_upgrades_to_scrypt(self):
        init_engine("old-pass", key_file=self.key_file)
        # 手工降级为 PBKDF2 600k 密钥文件（模拟 v6.3.0 部署）
        salt = os.urandom(16)
        eng, _ = EncryptionEngine.from_password(
            "old-pass", salt, iterations=PBKDF2_ITERATIONS_CURRENT
        )
        from mindforge.core.encryption import _write_key_file
        _write_key_file(
            __import__("pathlib").Path(self.key_file), salt,
            KDFParams(algorithm=KDF_ALGORITHM_LEGACY,
                      iterations=PBKDF2_ITERATIONS_CURRENT),
            eng,
        )

        old_blob = eng.encrypt("before upgrade")

        old_engine, new_engine = rekey_engine("old-pass", "new-pass", self.key_file)

        # 新引擎 = scrypt
        self.assertEqual(new_engine.kdf_params.algorithm, KDF_ALGORITHM_CURRENT)
        # 旧数据由旧引擎解；新引擎解不了旧密文（密钥已换）
        self.assertEqual(old_engine.decrypt(old_blob), "before upgrade")
        with self.assertRaises(SecurityError):
            new_engine.decrypt(old_blob)
        # 密钥文件已更新为 scrypt
        params = get_key_params(self.key_file)
        self.assertEqual(params.algorithm, KDF_ALGORITHM_CURRENT)
        # 备份存在
        self.assertTrue(os.path.exists(self.key_file + ".bak"))

    def test_rekey_explicit_iterations_keeps_pbkdf2(self):
        init_engine("old-pass", key_file=self.key_file)
        _, new_engine = rekey_engine(
            "old-pass", "new-pass", self.key_file, new_iterations=600000
        )
        self.assertEqual(new_engine.kdf_params.algorithm, KDF_ALGORITHM_LEGACY)
        self.assertEqual(new_engine.kdf_params.iterations, 600000)


class TestFacadeRekeyFixed(unittest.TestCase):
    """门面 rekey 修复：v5.8.13 命名空间迁移后死路径已接活

    原实现在密钥文件写入前调用 `__import__("core.encryption")`，
    包安装模式下必然 ModuleNotFoundError → 门面 rekey 自 v5.8.13
    起不可用。同时 v6.3.1 前的 verify 路径强制 PBKDF2，scrypt 密钥
    文件会误报「旧密码错误」。两条都在此钉死。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.key_file = os.path.join(self.tmp, ".key")
        self.db_path = os.path.join(self.tmp, "test.db")
        _set_global_engine(None)

    def tearDown(self):
        _set_global_engine(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_facade(self):
        import mindforge as mf
        return mf.MindForge(
            db_path=self.db_path, encrypted=True, key_file=self.key_file
        )

    def test_facade_rekey_end_to_end(self):
        cm = self._make_facade()
        cm.init_with_password("facade-old-pass")
        cm.add("facade rekey secret", category="test", importance="HIGH")

        result = cm.rekey("facade-old-pass", "facade-new-pass")

        self.assertEqual(result["new_algorithm"], KDF_ALGORITHM_CURRENT)
        self.assertGreaterEqual(result["rekeyed_count"], 1)

        # 重加密后的数据在新密码下可读
        hits = cm.search("facade rekey")
        self.assertGreaterEqual(len(hits.chunks), 1)
        self.assertIn("facade rekey secret", hits.chunks[0].content)
        cm.close()

    def test_facade_rekey_wrong_password_rejected(self):
        cm = self._make_facade()
        cm.init_with_password("right-pass")
        with self.assertRaises(SecurityError):
            cm.rekey("wrong-pass", "whatever-pass")
        cm.close()

    def test_get_encryption_info_is_current(self):
        cm = self._make_facade()
        cm.init_with_password("info-pass")
        info = cm.get_encryption_info()
        self.assertTrue(info["enabled"])
        self.assertTrue(info["is_current"])
        self.assertEqual(info["algorithm"], KDF_ALGORITHM_CURRENT)
        self.assertEqual(info["kdf_params"]["n"], SCRYPT_N_CURRENT)
        cm.close()


class TestExportImportKDFCompat(unittest.TestCase):
    """加密导出/导入的 KDF 参数分派（v6.3.1 payload v2）"""

    def _make_payload(self, kdf_dict, password, plaintext):
        salt = base64.b64decode(kdf_dict["salt"])
        if kdf_dict.get("algorithm") == KDF_ALGORITHM_CURRENT:
            params = KDFParams.from_dict(kdf_dict)
            engine, _ = EncryptionEngine.from_password(
                password, salt, kdf_params=params
            )
        else:
            engine, _ = EncryptionEngine.from_password(
                password, salt, iterations=kdf_dict["iterations"]
            )
        blob = engine.encrypt(plaintext)
        return {
            "format": "mindforge-encrypted-export",
            "version": 2,
            "kdf": kdf_dict,
            "cipher": blob.to_dict(),
        }

    def _import_payload(self, payload, password):
        """模拟 cmd_import_json 的 v6.3.1 派生逻辑"""
        kdf = payload.get("kdf", {})
        salt = base64.b64decode(kdf.get("salt", "") or "")
        if kdf.get("algorithm"):
            engine, _ = EncryptionEngine.from_password(
                password, salt, kdf_params=KDFParams.from_dict(kdf)
            )
        else:
            engine, _ = EncryptionEngine.from_password(
                password, salt, iterations=kdf.get("iterations")
            )
        blob = EncryptedBlob.from_dict(payload["cipher"])
        return engine.decrypt(blob)

    def test_v2_scrypt_payload_roundtrip(self):
        kdf_dict = {
            "algorithm": "scrypt",
            "iterations": 65536,
            "salt_length": 16,
            "n": 65536,
            "r": 8,
            "p": 1,
            "salt": base64.b64encode(os.urandom(16)).decode(),
        }
        payload = self._make_payload(kdf_dict, "export-pass", "v2 scrypt export data")
        text = self._import_payload(payload, "export-pass")
        self.assertEqual(text, "v2 scrypt export data")

    def test_v1_pbkdf2_payload_importable(self):
        """旧版导出（PBKDF2 600k + algorithm 字段）可按分派逻辑导入"""
        kdf_dict = {
            "algorithm": "PBKDF2-SHA256",
            "iterations": 600000,
            "salt_length": 16,
            "salt": base64.b64encode(os.urandom(16)).decode(),
        }
        payload = self._make_payload(kdf_dict, "v1-pass", "v1 pbkdf2 export data")
        text = self._import_payload(payload, "v1-pass")
        self.assertEqual(text, "v1 pbkdf2 export data")

    def test_v0_iterations_only_payload_importable(self):
        """极旧导出（只有 iterations）走回落路径"""
        kdf_dict = {
            "iterations": 60000,
            "salt": base64.b64encode(os.urandom(16)).decode(),
        }
        payload = self._make_payload(kdf_dict, "v0-pass", "v0 export data")
        text = self._import_payload(payload, "v0-pass")
        self.assertEqual(text, "v0 export data")

    def test_import_wrong_password_fails_cleanly(self):
        kdf_dict = {
            "algorithm": "scrypt",
            "iterations": 65536,
            "salt_length": 16,
            "n": 65536,
            "r": 8,
            "p": 1,
            "salt": base64.b64encode(os.urandom(16)).decode(),
        }
        payload = self._make_payload(kdf_dict, "export-pass", "secret export")
        with self.assertRaises(SecurityError):
            self._import_payload(payload, "wrong-pass")


if __name__ == "__main__":
    unittest.main()
