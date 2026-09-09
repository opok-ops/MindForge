"""
MindForge v5.6.0 加密引擎
AES-256-GCM + PBKDF2 密钥派生（版本化 KDF 参数）
"""

import os
import hashlib
import hmac
import json
import base64
import threading  # v5.4.7 修复 H-4：全局引擎初始化线程安全
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass

# 懒加载 cryptography：避免 CLI 启动时导入耗时（低配电脑 300ms+）
_CRYPTO_MODULE = None

def _get_crypto():
    global _CRYPTO_MODULE
    if _CRYPTO_MODULE is None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
            _CRYPTO_MODULE = (AESGCM, PBKDF2HMAC, hashes)
        except ImportError:
            _CRYPTO_MODULE = False
    return _CRYPTO_MODULE


# =============================================================================
# KDF 参数版本化
# =============================================================================
# 当前推荐的 PBKDF2 迭代次数（OWASP 2023 推荐：SHA-256 至少 600,000）
# 新创建的密钥文件使用此值
PBKDF2_ITERATIONS_CURRENT = 600_000

# 历史最低迭代次数（v5.5.10 及之前使用 60,000）
# 用于解密旧数据时的向后兼容，不作为新加密的默认值
PBKDF2_ITERATIONS_LEGACY = 60_000

# 密钥文件格式版本
KEY_FILE_VERSION = "5.6"

# KDF 算法标识
KDF_ALGORITHM = "PBKDF2-SHA256"

# 密钥验证令牌（用于验证密码是否正确，而无需解密实际数据）
_KEY_VERIFY_TOKEN = "MindForge-Key-Verify-v1"


@dataclass
class KDFParams:
    """KDF 参数集合，用于版本化密钥派生

    存储在密钥文件和密文头中，确保解密时使用与加密时相同的参数，
    从而支持 KDF 参数的平滑升级（如迭代次数从 60k → 600k）。
    """
    algorithm: str = KDF_ALGORITHM
    iterations: int = PBKDF2_ITERATIONS_CURRENT
    salt_length: int = 16

    def to_dict(self) -> dict:
        return {
            "algorithm": self.algorithm,
            "iterations": self.iterations,
            "salt_length": self.salt_length,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KDFParams":
        """从字典构造，缺失字段用安全默认值

        向后兼容：旧密钥文件可能只有 "iterations" 字段，没有 "algorithm" 等。
        """
        iterations = data.get("iterations", PBKDF2_ITERATIONS_LEGACY)
        return cls(
            algorithm=data.get("algorithm", KDF_ALGORITHM),
            iterations=int(iterations),
            salt_length=int(data.get("salt_length", 16)),
        )


class SecurityError(Exception):
    """安全相关异常"""
    pass


@dataclass
class EncryptedBlob:
    """加密数据块

    v5.6.0: 新增 kdf_params 字段，实现密文级 KDF 参数版本化。
    向后兼容：from_dict 时若缺 kdf_params 则假定为 legacy 参数。
    """
    ciphertext: bytes
    nonce: bytes
    salt: bytes
    tag: Optional[bytes] = None
    algorithm: str = "AES-256-GCM"  # 加密算法标识
    kdf_params: Optional[KDFParams] = None  # v5.6.0: 密文级 KDF 参数

    def to_dict(self) -> dict:
        result = {
            "ciphertext": base64.b64encode(self.ciphertext).decode(),
            "nonce": base64.b64encode(self.nonce).decode(),
            "salt": base64.b64encode(self.salt).decode(),
            "tag": base64.b64encode(self.tag).decode() if self.tag else None,
            "algorithm": self.algorithm,
        }
        if self.kdf_params:
            result["kdf_params"] = self.kdf_params.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "EncryptedBlob":
        kdf_params = None
        if "kdf_params" in data and data["kdf_params"]:
            kdf_params = KDFParams.from_dict(data["kdf_params"])
        return cls(
            ciphertext=base64.b64decode(data["ciphertext"]),
            nonce=base64.b64decode(data["nonce"]),
            salt=base64.b64decode(data["salt"]),
            tag=base64.b64decode(data["tag"]) if data.get("tag") else None,
            algorithm=data.get("algorithm", "AES-256-GCM"),
            kdf_params=kdf_params,
        )


class EncryptionEngine:
    """加密引擎"""

    def __init__(self, key: bytes, kdf_params: Optional[KDFParams] = None):
        self._key = key
        self._kdf_params = kdf_params  # v5.6.0: 记录派生此密钥所用的 KDF 参数
        crypto = _get_crypto()
        if not crypto:
            # P1-008: 移除 HMAC-XOR fallback，cryptography 缺失时直接拒绝初始化
            raise SecurityError(
                "cryptography library is required for encryption. "
                "Install with: pip install cryptography"
            )
        AESGCM = crypto[0]
        self._aesgcm = AESGCM(key)

    @property
    def kdf_params(self) -> Optional[KDFParams]:
        """派生此密钥所用的 KDF 参数（只读）"""
        return self._kdf_params

    @classmethod
    def from_password(
        cls,
        password: str,
        salt: Optional[bytes] = None,
        iterations: Optional[int] = None,
    ) -> Tuple["EncryptionEngine", bytes]:
        """从密码派生密钥

        Args:
            password: 用户密码
            salt: 盐值，为 None 时随机生成
            iterations: PBKDF2 迭代次数，为 None 时使用当前推荐值

        v5.6.0: 新增 iterations 参数，支持版本化 KDF。旧数据解密时
        传入历史迭代次数，新加密使用当前推荐值。
        """
        if salt is None:
            salt = os.urandom(16)

        if iterations is None:
            iterations = PBKDF2_ITERATIONS_CURRENT

        crypto = _get_crypto()
        if not crypto:
            # P1-008: 移除 fallback，cryptography 缺失时直接拒绝
            raise SecurityError(
                "cryptography library is required for encryption. "
                "Install with: pip install cryptography"
            )
        _, PBKDF2HMAC, hashes = crypto
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        key = kdf.derive(password.encode())

        kdf_params = KDFParams(
            algorithm=KDF_ALGORITHM,
            iterations=iterations,
            salt_length=len(salt),
        )
        return cls(key, kdf_params=kdf_params), salt

    def encrypt(self, plaintext: str) -> EncryptedBlob:
        """加密文本"""
        if not isinstance(plaintext, str):
            raise SecurityError("encrypt() 要求 str 类型输入")
        plaintext_bytes = plaintext.encode("utf-8")
        nonce = os.urandom(12)
        salt = os.urandom(16)

        ciphertext = self._aesgcm.encrypt(nonce, plaintext_bytes, None)
        return EncryptedBlob(
            ciphertext=ciphertext,
            nonce=nonce,
            salt=salt,
            algorithm="AES-256-GCM",
            kdf_params=self._kdf_params,  # 密文头携带密钥的 KDF 参数
        )

    def decrypt(self, blob: EncryptedBlob) -> str:
        """解密文本"""
        if not isinstance(blob, EncryptedBlob):
            raise SecurityError("decrypt() 要求 EncryptedBlob 类型输入")
        if not blob.nonce or not blob.ciphertext:
            raise SecurityError("EncryptedBlob 缺少 nonce 或 ciphertext 字段")
        try:
            plaintext = self._aesgcm.decrypt(blob.nonce, blob.ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception as e:
            raise SecurityError(f"解密失败：{e}")

    def hash(self, data: str) -> str:
        """计算数据哈希"""
        if not isinstance(data, str):
            raise SecurityError("hash() 要求 str 类型输入")
        return hashlib.sha256(data.encode()).hexdigest()

    def verify_hash(self, data: str, hash_value: str) -> bool:
        """验证哈希"""
        return hmac.compare_digest(self.hash(data), hash_value)


_global_engine: Optional[EncryptionEngine] = None
_init_lock = threading.Lock()  # v5.4.7 修复 H-4：全局引擎初始化线程安全


def _write_key_file(key_path: Path, salt: bytes, kdf_params: KDFParams,
                    engine: EncryptionEngine):
    """安全地写入密钥文件

    v5.6.0: 抽出为独立函数，供 init_engine 和 rekey 共用。
    v5.6.0: 新增 verify_blob 验证令牌，用于快速验证密码正确性。
    """
    verify_blob = engine.encrypt(_KEY_VERIFY_TOKEN)
    key_content = json.dumps({
        "salt": base64.b64encode(salt).decode(),
        "version": KEY_FILE_VERSION,
        "kdf": kdf_params.algorithm,
        "iterations": kdf_params.iterations,
        "kdf_params": kdf_params.to_dict(),
        "verify_blob": verify_blob.to_dict(),
    }, indent=2)

    import sys
    if sys.platform != "win32":
        # Unix/Linux/macOS: 使用 os.open 以 0o600 权限创建文件
        import stat
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        try:
            os.write(fd, key_content.encode("utf-8"))
        finally:
            os.close(fd)
    else:
        # Windows: 先写文件再尝试设置 ACL
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key_content)
        try:
            import subprocess
            import os as _os
            username = _os.getlogin()
            subprocess.run(
                ["icacls", str(key_path), "/inheritance:r", "/grant:r", f"{username}:F"],
                capture_output=True, timeout=5, check=False
            )
        except Exception:
            pass  # icacls 失败不阻断流程


def _verify_key_password(engine: EncryptionEngine, key_data: dict) -> bool:
    """使用验证令牌检查密码是否正确

    v5.6.0: 新密钥文件包含 verify_blob 字段。旧文件没有则跳过验证
    （向后兼容，避免老用户升级后被锁）。
    """
    if "verify_blob" not in key_data:
        return True  # legacy 密钥文件，无法验证，放行

    try:
        blob = EncryptedBlob.from_dict(key_data["verify_blob"])
        decrypted = engine.decrypt(blob)
        return decrypted == _KEY_VERIFY_TOKEN
    except Exception:
        return False


def get_key_params(key_file: str) -> Optional[KDFParams]:
    """读取密钥文件中的 KDF 参数

    Returns:
        KDFParams，如果密钥文件不存在则返回 None
    """
    key_path = Path(key_file)
    if not key_path.exists():
        return None

    with open(key_path, "r", encoding="utf-8") as f:
        key_data = json.load(f)

    # v5.6.0+: 优先使用完整的 kdf_params 结构
    if "kdf_params" in key_data:
        return KDFParams.from_dict(key_data["kdf_params"])

    # 向后兼容：旧格式只有 iterations 字段
    return KDFParams(
        algorithm=key_data.get("kdf", KDF_ALGORITHM),
        iterations=int(key_data.get("iterations", PBKDF2_ITERATIONS_LEGACY)),
    )


def init_engine(password: str, key_file: str = "./data/.key") -> EncryptionEngine:
    """初始化全局加密引擎

    v5.2.2 修复：显式指定文件 encoding='utf-8'，避免在中文/Windows 系统上
    出现 UnicodeDecodeError 或编码不一致问题。
    v5.4.7 修复 H-4：添加线程锁保护全局引擎初始化。
    v5.4.7 修复 H-2：密钥文件创建时即设置受限权限，避免权限窗口期。
    v5.6.0 升级：版本化 KDF 参数——读取密钥文件中的 iterations 解密旧数据，
    新创建的密钥使用 OWASP 推荐的 600,000 次迭代。
    """
    global _global_engine

    with _init_lock:
        key_path = Path(key_file)
        key_path.parent.mkdir(parents=True, exist_ok=True)

        if key_path.exists():
            with open(key_path, "r", encoding="utf-8") as f:
                key_data = json.load(f)
            salt = base64.b64decode(key_data["salt"])

            # v5.6.0: 从密钥文件读取 KDF 参数，向后兼容
            kdf_params = get_key_params(key_file)
            if kdf_params is None:
                kdf_params = KDFParams(iterations=PBKDF2_ITERATIONS_LEGACY)

            engine, _ = EncryptionEngine.from_password(
                password, salt, iterations=kdf_params.iterations
            )

            # v5.6.0: 验证密码正确性（有 verify_blob 时才验证）
            if not _verify_key_password(engine, key_data):
                raise SecurityError("密码错误")

            # 如果是 legacy 密钥文件（无 verify_blob），升级写入新格式
            if "verify_blob" not in key_data:
                try:
                    _write_key_file(key_path, salt, kdf_params, engine)
                except Exception:
                    pass  # 升级写入失败不影响使用
        else:
            # 新密钥文件：使用当前推荐的 KDF 参数
            kdf_params = KDFParams()
            engine, salt = EncryptionEngine.from_password(
                password, iterations=kdf_params.iterations
            )
            _write_key_file(key_path, salt, kdf_params, engine)

        _global_engine = engine
        return engine


def rekey_engine(
    old_password: str,
    new_password: str,
    key_file: str = "./data/.key",
    new_iterations: Optional[int] = None,
) -> Tuple[EncryptionEngine, EncryptionEngine]:
    """更换密钥（密码变更或 KDF 参数升级）

    生成新的加密密钥并更新密钥文件，同时返回新旧引擎供调用者
    对已加密数据进行重加密。

    Args:
        old_password: 旧密码
        new_password: 新密码（可与旧密码相同，仅升级 KDF 参数）
        key_file: 密钥文件路径
        new_iterations: 新的迭代次数，默认使用当前推荐值

    Returns:
        (old_engine, new_engine) 旧引擎和新引擎

    Raises:
        SecurityError: 旧密码验证失败或密钥文件不存在
    """
    key_path = Path(key_file)
    if not key_path.exists():
        raise SecurityError("密钥文件不存在，无法执行 rekey")

    # 1. 用旧密码和旧参数解密（验证旧密码）
    old_kdf_params = get_key_params(key_file)
    if old_kdf_params is None:
        old_kdf_params = KDFParams(iterations=PBKDF2_ITERATIONS_LEGACY)

    with open(key_path, "r", encoding="utf-8") as f:
        key_data = json.load(f)
    old_salt = base64.b64decode(key_data["salt"])

    old_engine, _ = EncryptionEngine.from_password(
        old_password, old_salt, iterations=old_kdf_params.iterations
    )

    # 1.5 验证旧密码正确性（关键安全检查）
    if not _verify_key_password(old_engine, key_data):
        raise SecurityError("旧密码错误")

    # 2. 生成新的 KDF 参数和密钥
    if new_iterations is None:
        new_iterations = PBKDF2_ITERATIONS_CURRENT

    new_kdf_params = KDFParams(
        algorithm=KDF_ALGORITHM,
        iterations=new_iterations,
    )
    new_salt = os.urandom(16)
    new_engine, _ = EncryptionEngine.from_password(
        new_password, new_salt, iterations=new_iterations
    )

    # 3. 写入新的密钥文件（原子操作前先备份）
    backup_path = key_path.with_suffix(key_path.suffix + ".bak")
    if key_path.exists():
        import shutil
        shutil.copy2(key_path, backup_path)

    try:
        _write_key_file(key_path, new_salt, new_kdf_params, new_engine)
    except Exception:
        # 写入失败时恢复备份
        import shutil
        if backup_path.exists():
            shutil.copy2(backup_path, key_path)
        raise

    # 4. 更新全局引擎
    global _global_engine
    _global_engine = new_engine

    return old_engine, new_engine


def get_engine() -> EncryptionEngine:
    """获取全局加密引擎"""
    if _global_engine is None:
        raise SecurityError("加密引擎未初始化，请先调用 init_engine()")
    return _global_engine


def _set_global_engine(engine: EncryptionEngine) -> None:
    """恢复全局加密引擎（rekey 回滚用）"""
    global _global_engine
    _global_engine = engine
