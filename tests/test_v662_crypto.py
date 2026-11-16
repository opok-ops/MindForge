# -*- coding: utf-8 -*-
"""v6.6.2 加密面修复包契约测试

对应 .zwork_v662_crypto.md 报告的 C2/C5/C6/C7/C8/C9 六项修复：
- C2 (P1)：_maybe_recover_rekey 区分「解密失败=库是旧密文，可删 tmp」与
  「环境故障=状态不明，保留 tmp（唯一新密钥副本）留待下次恢复」
- C5 (P2)：KDFParams.from_dict 反降级补全——r 白名单 {1,8}、p∈[1,8]、
  n/iterations 成本上限、int() 包 try 转 SecurityError、未知 algorithm 拒绝、
  scrypt 缺 n 直接拒绝；合法既有参数（scrypt 65536/8/1、PBKDF2 600000）照常通过
- C6 (P2)：init_engine 密钥文件损坏统一包装 SecurityError；verify_blob
  校验失败区分「密码错误」与「校验材料损坏」
- C7 (P2)：EncryptedBlob.from_dict 缺字段/坏 base64/未知算法拒绝
- C8 (P2)：init_with_password 重入先 close 旧 storage（连接不泄漏）
- C9 (清理)：死 import rekey_engine 删除；rekey_engine 标注仅测试/内部使用
"""
import base64
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from mindforge.core import encryption as enc_mod
from mindforge.core.encryption import (
    SecurityError,
    KDFParams,
    EncryptedBlob,
    EncryptionEngine,
    init_engine,
    _write_key_file,
)
from mindforge.core.mindforge import MindForge
from mindforge.core.storage import StorageEngine
from mindforge.core.types import MemoryConfig

try:
    import cryptography  # noqa: F401
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False


# =============================================================================
# C5：KDFParams.from_dict 反降级补全（纯解析，无需 cryptography）
# =============================================================================
class TestKDFParamsAntiDowngrade(unittest.TestCase):
    """C5：r/p 白名单、成本上限、类型异常包装、未知算法拒绝"""

    def test_backward_compat_scrypt_current(self):
        """合法既有密钥文件参数（scrypt n=65536,r=8,p=1）必须照常通过"""
        p = KDFParams.from_dict(
            {"algorithm": "scrypt", "iterations": 65536, "salt_length": 16,
             "n": 65536, "r": 8, "p": 1})
        self.assertEqual((p.algorithm, p.n, p.r, p.p), ("scrypt", 65536, 8, 1))

    def test_backward_compat_pbkdf2_600k(self):
        """合法既有密钥文件参数（PBKDF2 600000）必须照常通过"""
        p = KDFParams.from_dict(
            {"algorithm": "PBKDF2-SHA256", "iterations": 600000})
        self.assertEqual((p.algorithm, p.iterations), ("PBKDF2-SHA256", 600000))

    def test_backward_compat_legacy_minimal(self):
        """旧格式（缺 algorithm / 空 dict）仍按 legacy PBKDF2 60000 兼容"""
        p1 = KDFParams.from_dict({})
        self.assertEqual((p1.algorithm, p1.iterations),
                         ("PBKDF2-SHA256", 60000))
        p2 = KDFParams.from_dict({"iterations": 60000})
        self.assertEqual(p2.iterations, 60000)

    def test_r_whitelist(self):
        """r 只允许 {1, 8}：r=2/0 等自由篡改拒绝"""
        self.assertEqual(KDFParams.from_dict(
            {"algorithm": "scrypt", "n": 65536, "r": 1, "p": 1}).r, 1)
        self.assertEqual(KDFParams.from_dict(
            {"algorithm": "scrypt", "n": 65536, "r": 8, "p": 1}).r, 8)
        for bad_r in (0, 2, 3, 16):
            with self.assertRaises(SecurityError):
                KDFParams.from_dict(
                    {"algorithm": "scrypt", "n": 65536, "r": bad_r, "p": 1})

    def test_p_range(self):
        """p ∈ [1, 8]：0 与 9+ 拒绝"""
        self.assertEqual(KDFParams.from_dict(
            {"algorithm": "scrypt", "n": 65536, "r": 8, "p": 8}).p, 8)
        for bad_p in (0, 9, 100):
            with self.assertRaises(SecurityError):
                KDFParams.from_dict(
                    {"algorithm": "scrypt", "n": 65536, "r": 8, "p": bad_p})

    def test_n_upper_bound(self):
        """scrypt n 上限 2^22：2^22 可解析，2^22+1 拒绝"""
        p = KDFParams.from_dict(
            {"algorithm": "scrypt", "n": 2 ** 22, "r": 8, "p": 1})
        self.assertEqual(p.n, 2 ** 22)
        with self.assertRaises(SecurityError):
            KDFParams.from_dict(
                {"algorithm": "scrypt", "n": 2 ** 22 + 1, "r": 8, "p": 1})

    def test_n_lower_bound_and_power_of_two(self):
        """scrypt n 下限（v6.5.6 既有）+ 非 2 的幂直接拒绝"""
        with self.assertRaises(SecurityError):
            KDFParams.from_dict({"algorithm": "scrypt", "n": -1, "r": 8, "p": 1})
        with self.assertRaises(SecurityError):
            KDFParams.from_dict(
                {"algorithm": "scrypt", "n": 60000, "r": 8, "p": 1})

    def test_pbkdf2_iterations_upper_bound(self):
        """PBKDF2 iterations 上限 2^21：10^9 等资源耗尽参数拒绝"""
        self.assertEqual(KDFParams.from_dict(
            {"algorithm": "PBKDF2-SHA256", "iterations": 2 ** 21}).iterations,
            2 ** 21)
        with self.assertRaises(SecurityError):
            KDFParams.from_dict(
                {"algorithm": "PBKDF2-SHA256", "iterations": 10 ** 9})

    def test_non_int_fields_wrapped_as_security_error(self):
        """int() 解析失败统一转 SecurityError（此前裸抛 ValueError）"""
        for bad in ({"algorithm": "PBKDF2-SHA256", "iterations": "abc"},
                    {"algorithm": "scrypt", "n": "abc", "r": 8, "p": 1},
                    {"algorithm": "PBKDF2-SHA256", "iterations": 600000,
                     "salt_length": "abc"}):
            with self.assertRaises(SecurityError):
                KDFParams.from_dict(bad)

    def test_unknown_algorithm_rejected(self):
        """未知 algorithm 拒绝（缺 algorithm 仍走 legacy 兼容路径）"""
        with self.assertRaises(SecurityError):
            KDFParams.from_dict({"algorithm": "argon2id", "n": 65536})
        with self.assertRaises(SecurityError):
            KDFParams.from_dict({"algorithm": "AES-256-GCM", "iterations": 600000})

    def test_scrypt_missing_n_rejected(self):
        """scrypt 缺 n 直接 SecurityError（不再落到 iterations=60000 的
        PBKDF2 语义）"""
        with self.assertRaises(SecurityError):
            KDFParams.from_dict({"algorithm": "scrypt", "r": 8, "p": 1})

    def test_non_dict_rejected(self):
        with self.assertRaises(SecurityError):
            KDFParams.from_dict(None)

    @unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装，跳过派生测试")
    def test_derive_unchanged_for_valid_params(self):
        """合法参数解析后的派生结果与既有默认 KDF 完全一致（兼容不漂移）"""
        from mindforge.core.encryption import _derive_key
        salt = os.urandom(16)
        parsed = KDFParams.from_dict(
            {"algorithm": "scrypt", "iterations": 65536, "n": 65536,
             "r": 8, "p": 1})
        self.assertEqual(_derive_key("pw", salt, parsed),
                         _derive_key("pw", salt, KDFParams()))


# =============================================================================
# C7：EncryptedBlob.from_dict 鲁棒性（无需 cryptography）
# =============================================================================
class TestEncryptedBlobRobustness(unittest.TestCase):
    """C7：缺字段/坏 base64/未知算法统一 SecurityError"""

    @unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装")
    def test_roundtrip_preserves_fields(self):
        eng, _ = EncryptionEngine.from_password("pw")
        blob = eng.encrypt("round trip")
        restored = EncryptedBlob.from_dict(blob.to_dict())
        self.assertEqual(restored.ciphertext, blob.ciphertext)
        self.assertEqual(restored.nonce, blob.nonce)
        self.assertEqual(restored.salt, blob.salt)
        self.assertEqual(restored.algorithm, "AES-256-GCM")

    def test_missing_fields_rejected(self):
        base_ok = {"ciphertext": "AA==", "nonce": "AA==", "salt": "AA=="}
        with self.assertRaises(SecurityError):
            EncryptedBlob.from_dict({})
        for field in ("ciphertext", "nonce", "salt"):
            broken = dict(base_ok)
            broken.pop(field)
            with self.assertRaises(SecurityError):
                EncryptedBlob.from_dict(broken)

    def test_bad_base64_wrapped(self):
        with self.assertRaises(SecurityError):
            EncryptedBlob.from_dict(
                {"ciphertext": "!!!not-b64", "nonce": "AA==", "salt": "AA=="})

    def test_unknown_algorithm_rejected(self):
        """algorithm 非 AES-256-GCM 一律拒绝（含未来格式占位标签）"""
        with self.assertRaises(SecurityError):
            EncryptedBlob.from_dict(
                {"ciphertext": "AA==", "nonce": "AA==", "salt": "AA==",
                 "algorithm": "AES-256-GCM-v9-future"})

    def test_missing_algorithm_defaults_accepted(self):
        b = EncryptedBlob.from_dict(
            {"ciphertext": "AA==", "nonce": "AA==", "salt": "AA=="})
        self.assertEqual(b.algorithm, "AES-256-GCM")

    def test_non_dict_rejected(self):
        with self.assertRaises(SecurityError):
            EncryptedBlob.from_dict(None)


# =============================================================================
# C6：init_engine 密钥文件损坏路径
# =============================================================================
@unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装，跳过加密测试")
class TestKeyFileCorruption(unittest.TestCase):
    """C6：文件级损坏包装 SecurityError；verify_blob 失败区分两种定性"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662kf_")
        self.key = os.path.join(self.tmp, ".key")

    def _write_raw(self, content):
        with open(self.key, "w", encoding="utf-8") as f:
            f.write(content)

    def _make_valid_key(self, password="correct-pass"):
        if os.path.exists(self.key):
            os.remove(self.key)
        init_engine(password, self.key)
        with open(self.key, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_bad_json_wrapped(self):
        self._write_raw("{ this is not json")
        with self.assertRaises(SecurityError) as cm:
            init_engine("pw", self.key)
        self.assertIn("密钥文件损坏", str(cm.exception))

    def test_missing_salt_wrapped(self):
        self._write_raw(json.dumps({"kdf": "scrypt", "iterations": 65536}))
        with self.assertRaises(SecurityError) as cm:
            init_engine("pw", self.key)
        self.assertIn("密钥文件损坏", str(cm.exception))

    def test_bad_base64_salt_wrapped(self):
        self._write_raw(json.dumps(
            {"salt": "!!!not-b64", "kdf": "scrypt", "iterations": 65536}))
        with self.assertRaises(SecurityError) as cm:
            init_engine("pw", self.key)
        self.assertIn("密钥文件损坏", str(cm.exception))

    def test_scrypt_missing_n_in_key_file_rejected(self):
        """scrypt 密钥文件缺 n → SecurityError（不再落到 PBKDF2 语义）"""
        self._write_raw(json.dumps({
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "kdf": "scrypt",
            "kdf_params": {"algorithm": "scrypt", "r": 8, "p": 1},
        }))
        with self.assertRaises(SecurityError):
            init_engine("pw", self.key)

    def test_verify_blob_unparseable_reports_material_damage(self):
        """verify_blob 结构损坏 → 「校验材料损坏」，不再误报纯「密码错误」"""
        self._make_valid_key()
        with open(self.key, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["verify_blob"] = {"ciphertext": "!!!not-b64",
                               "nonce": "AA==", "salt": "AA=="}
        self._write_raw(json.dumps(data))
        with self.assertRaises(SecurityError) as cm:
            init_engine("correct-pass", self.key)
        self.assertIn("校验材料损坏", str(cm.exception))

    def test_verify_blob_auth_failure_message_distinguishes(self):
        """verify_blob 认证失败（单字符翻转）+ 正确密码：消息同时列出
        「密码错误」与「校验材料损坏」两种可能，不再单一定性误导"""
        self._make_valid_key()
        with open(self.key, "r", encoding="utf-8") as f:
            data = json.load(f)
        vb = data["verify_blob"]["ciphertext"]
        data["verify_blob"]["ciphertext"] = \
            ("A" if vb[0] != "A" else "B") + vb[1:]
        self._write_raw(json.dumps(data))
        with self.assertRaises(SecurityError) as cm:
            init_engine("correct-pass", self.key)
        msg = str(cm.exception)
        self.assertIn("密码错误", msg)
        self.assertIn("校验材料损坏", msg)

    def test_wrong_password_on_intact_file(self):
        self._make_valid_key()
        with self.assertRaises(SecurityError) as cm:
            init_engine("totally-wrong", self.key)
        self.assertIn("密码错误", str(cm.exception))

    def test_valid_scrypt_file_still_initializes(self):
        """向后兼容：合法 scrypt 密钥文件 + 正确密码照常初始化"""
        self._make_valid_key()
        engine = init_engine("correct-pass", self.key)
        self.assertIsInstance(engine, EncryptionEngine)


# =============================================================================
# C2：_maybe_recover_rekey——环境故障保留 tmp（唯一新密钥副本）
# =============================================================================
@unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装，跳过加密测试")
class TestRekeyRecoverTmpPreserved(unittest.TestCase):
    """C2 (P1)：试探阶段区分解密失败与环境故障，瞬时异常不删 tmp"""

    def _build_crash_window(self, tmp, do_rekey=True):
        """构造 rekey 崩溃窗口现场：正式密钥文件仍是旧密钥，
        .rekey-tmp 是新密钥的唯一副本；do_rekey 决定库是否已重加密 commit"""
        db = os.path.join(tmp, "m.db")
        key = os.path.join(tmp, ".key")
        tmp_key = key + ".rekey-tmp"
        eng = init_engine("oldpass", key)
        st = StorageEngine(db_path=db, encryption=eng, encrypted=True)
        mid = st.add_memory("top secret").id
        new_salt = os.urandom(16)
        new_engine, _ = EncryptionEngine.from_password(
            "newpass", new_salt, kdf_params=KDFParams())
        _write_key_file(tmp_key, new_salt, KDFParams(), new_engine)
        if do_rekey:
            st.rekey_memories(eng, new_engine)  # commit：库已是新密文
        st.close()
        return db, key, tmp_key, mid

    def _locked_connect(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked (simulated transient)")

    def test_transient_probe_failure_preserves_tmp(self):
        """探测阶段瞬时异常（DB 锁/IO）：返回 False 且 tmp 必须保留"""
        tmp = tempfile.mkdtemp(prefix="mf_v662c2a_")
        db, key, tmp_key, mid = self._build_crash_window(tmp, do_rekey=True)
        mf = MindForge(config=MemoryConfig(db_path=db, key_file=key,
                                           encrypted=True))
        with mock.patch("sqlite3.connect", side_effect=self._locked_connect):
            r = mf._maybe_recover_rekey("newpass")
        self.assertFalse(r)
        self.assertTrue(
            os.path.exists(tmp_key),
            "瞬时环境故障不得删除唯一新密钥副本（C2/P1）")

    def test_retry_after_transient_recovers_and_reads(self):
        """瞬时故障后重试 init_with_password：自动完成恢复且数据可读"""
        tmp = tempfile.mkdtemp(prefix="mf_v662c2b_")
        db, key, tmp_key, mid = self._build_crash_window(tmp, do_rekey=True)
        mf = MindForge(config=MemoryConfig(db_path=db, key_file=key,
                                           encrypted=True))
        with mock.patch("sqlite3.connect", side_effect=self._locked_connect):
            self.assertFalse(mf._maybe_recover_rekey("newpass"))
        # 下次 init（故障已消除）：内部再次触发恢复 → 成功
        mf.init_with_password("newpass")
        self.assertFalse(os.path.exists(tmp_key))
        self.assertEqual(mf.get(mid).content, "top secret")
        mf.close()

    def test_replace_failure_preserves_tmp(self):
        """os.replace 失败（环境故障）：tmp 保留，不得删唯一副本"""
        tmp = tempfile.mkdtemp(prefix="mf_v662c2c_")
        db, key, tmp_key, mid = self._build_crash_window(tmp, do_rekey=True)
        mf = MindForge(config=MemoryConfig(db_path=db, key_file=key,
                                           encrypted=True))
        with mock.patch("os.replace",
                        side_effect=OSError("simulated disk fault")):
            r = mf._maybe_recover_rekey("newpass")
        self.assertFalse(r)
        self.assertTrue(os.path.exists(tmp_key))

    def test_deterministic_decrypt_failure_discards_tmp(self):
        """解密失败（确定性：库仍是旧密文）→ 删 tmp，按旧密钥正常初始化"""
        tmp = tempfile.mkdtemp(prefix="mf_v662c2d_")
        db, key, tmp_key, mid = self._build_crash_window(tmp, do_rekey=False)
        mf = MindForge(config=MemoryConfig(db_path=db, key_file=key,
                                           encrypted=True))
        r = mf._maybe_recover_rekey("newpass")
        self.assertFalse(r)
        self.assertFalse(os.path.exists(tmp_key), "库是旧密文时 tmp 是垃圾，应删除")
        mf.init_with_password("oldpass")
        self.assertEqual(mf.get(mid).content, "top secret")
        mf.close()

    def test_wrong_password_keeps_tmp(self):
        """密码不对：保留 tmp 等下次（可能只是打错密码，既有语义钉死）"""
        tmp = tempfile.mkdtemp(prefix="mf_v662c2e_")
        db, key, tmp_key, mid = self._build_crash_window(tmp, do_rekey=True)
        mf = MindForge(config=MemoryConfig(db_path=db, key_file=key,
                                           encrypted=True))
        self.assertFalse(mf._maybe_recover_rekey("wrongpass"))
        self.assertTrue(os.path.exists(tmp_key))


# =============================================================================
# C8：init_with_password 重入不泄漏旧连接
# =============================================================================
@unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装，跳过加密测试")
class TestInitWithPasswordReentry(unittest.TestCase):
    """C8 (P2)：重建 storage 前先 close 旧 self._storage"""

    def _make_mf(self, tmp):
        mf = MindForge(db_path=os.path.join(tmp, "m.db"),
                       key_file=os.path.join(tmp, ".key"),
                       encrypted=True)
        mf.init_with_password("pw-v662")
        return mf

    def test_reentry_replaces_storage(self):
        tmp = tempfile.mkdtemp(prefix="mf_v662c8a_")
        mf = self._make_mf(tmp)
        try:
            s1 = mf._storage
            mf.init_with_password("pw-v662")  # 重入
            self.assertIsNot(mf._storage, s1)
        finally:
            mf.close()

    def test_reentry_closes_old_connection(self):
        """重入后旧 StorageEngine 的 SQLite 连接必须已关闭（不泄漏）"""
        tmp = tempfile.mkdtemp(prefix="mf_v662c8b_")
        mf = self._make_mf(tmp)
        try:
            s1 = mf._storage
            conn1 = s1._get_conn()
            mf.init_with_password("pw-v662")
            with self.assertRaises(sqlite3.ProgrammingError):
                conn1.execute("SELECT 1")
        finally:
            mf.close()

    def test_reentry_data_intact(self):
        tmp = tempfile.mkdtemp(prefix="mf_v662c8c_")
        mf = self._make_mf(tmp)
        try:
            e = mf.add("persist across reentry")
            mf.init_with_password("pw-v662")
            got = mf.get(e.id)
            self.assertIsNotNone(got)
            self.assertEqual(got.content, "persist across reentry")
        finally:
            mf.close()


# =============================================================================
# C9：死 import 清理 + rekey_engine 危险顺序标注
# =============================================================================
class TestRekeyEngineCleanup(unittest.TestCase):
    """C9：mindforge.py 死 import rekey_engine 删除；docstring 标注"""

    def test_dead_import_removed(self):
        from mindforge.core import mindforge as mf_mod
        self.assertFalse(
            hasattr(mf_mod, "_rekey_engine"),
            "mindforge.py 不再引用 rekey_engine（生产路径走门面 rekey）")

    def test_rekey_engine_docstring_marks_internal_use(self):
        doc = enc_mod.rekey_engine.__doc__ or ""
        self.assertIn("内部使用", doc)
        self.assertIn("自行完成", doc)


if __name__ == "__main__":
    unittest.main()
