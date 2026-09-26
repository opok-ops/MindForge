"""
MindForge v5.0 隐私引擎
隐私扫描、访问控制、合规报告
"""

import re
import hashlib
import hmac
import time
import uuid
import sqlite3
import logging
import threading
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any

from core.storage import StorageEngine, MemoryEntry
from core.types import PrivacyLevel
import base64
import secrets
import struct
import json

logger = logging.getLogger(__name__)


@dataclass
class PrivacyScanResult:
    """隐私扫描结果"""

    is_sensitive: bool = False
    suggested_privacy: PrivacyLevel = PrivacyLevel.INTERNAL
    confidence: float = 0.0
    detected_types: List[str] = field(default_factory=list)
    masked_preview: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AccessGrant:
    """访问授权"""

    grant_id: str
    memory_id: str
    grantee: str
    granted_by: str
    granted_at: float
    expires_at: Optional[float]
    access_level: str = "read"


SENSITIVE_PATTERNS = {
    "phone": [r"1[3-9]\d{9}"],
    "email": [r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"],
    "id_card": [r"\d{17}[\dXx]"],
    "bank_card": [r"\d{16,19}"],
    "password": [r"(password|passwd|pwd|密码)\s*[:=]\s*\S+"],
    "address": [r"(地址|住址|家|住)\s*[:：]\s*.+"],
    "name": [r"(姓名|名字)\s*[:：]\s*.+"],
}

SENSITIVE_KEYWORDS = [
    "密码",
    "秘钥",
    "密钥",
    "token",
    "secret",
    "私密",
    "隐私",
    "身份证",
    "银行卡",
    "手机号",
    "邮箱",
    "地址",
    "工资",
    "收入",
    "病历",
    "健康",
    "性",
    "账号",
    "口令",
]


class PrivacyEngine:
    """隐私引擎"""

    # v5.6.2 安全修复：2FA 验证会话有效期（5 分钟）
    # P1-08 语义澄清：会话时间戳统一使用 time.monotonic()（见
    # _verified_2fa_sessions），而非 time.time()。原因：monotonic 时钟单调递增、
    # 不受系统墙上时钟回拨 / NTP 校正影响，攻击者无法通过调慢系统时间来延长
    # 已授予的验证窗口。会话表仅存于内存，进程重启后自动清空（需重新验证），
    # 因此不存在跨重启的 monotonic 基准漂移问题。
    _2FA_VERIFY_WINDOW = 300  # 秒（按 time.monotonic() 计算的相对时长）

    # v5.7.3 安全加固：2FA 连续失败阈值与锁定时长（防在线穷举）
    _2FA_MAX_FAILURES = 5
    _2FA_LOCK_SECONDS = 60

    # v5.6.5 P1 #7：过期授权的后台清理节流间隔（秒）
    _GRANT_PURGE_INTERVAL = 300

    def __init__(self, storage: StorageEngine):
        self.storage = storage
        self._grants: Dict[str, List[AccessGrant]] = {}
        # v5.6.5 P1 #7：保护 _grants 的线程锁 + 过期清理节流水位
        self._grants_lock = threading.Lock()
        self._last_grant_purge = 0.0
        # v5.3.3 安全修复：二次验证令牌存储（替代始终返回 True 的漏洞）
        # v5.6.2 安全修复：存储 token hash，用于验证码比对（不明文驻留）
        self._second_factor_token_hashes: Dict[str, str] = {}
        # v5.6.2 安全修复：记录已通过 2FA 验证的会话（actor -> 验证时间戳）
        self._verified_2fa_sessions: Dict[str, float] = {}
        # v5.7.3 安全加固：2FA 失败计数与锁定截止时间（仅内存，重启即失效）
        self._2fa_fail_counts: Dict[str, int] = {}
        self._2fa_locked_until: Dict[str, float] = {}
        # v5.8.3：标准 TOTP（RFC 6238）密钥内存缓存（加密落库，见 totp_secrets 表）
        self._totp_secrets: Dict[str, str] = {}
        # v5.4.2 安全修复：持久化 grants 和 2FA tokens 到 SQLite，重启不丢失
        self._init_persistence()
        self._load_persisted_data()

    def _init_persistence(self):
        """创建持久化表（如不存在）"""
        try:
            conn = self.storage._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS access_grants (
                    grant_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    grantee TEXT NOT NULL,
                    granted_by TEXT NOT NULL,
                    granted_at REAL NOT NULL,
                    expires_at REAL,
                    access_level TEXT DEFAULT 'read'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS second_factor_tokens (
                    actor TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.commit()
        except sqlite3.Error as e:
            logger.error("隐私持久化表创建失败: %s", e)

    def _load_persisted_data(self):
        """从 SQLite 加载 grants 和 2FA tokens"""
        try:
            conn = self.storage._get_conn()
            # 加载 grants
            rows = conn.execute(
                "SELECT grant_id, memory_id, grantee, granted_by, granted_at, expires_at, access_level FROM access_grants"
            ).fetchall()
            for row in rows:
                grant = AccessGrant(
                    grant_id=row[0],
                    memory_id=row[1],
                    grantee=row[2],
                    granted_by=row[3],
                    granted_at=row[4],
                    expires_at=row[5],
                    access_level=row[6],
                )
                if row[1] not in self._grants:
                    self._grants[row[1]] = []
                self._grants[row[1]].append(grant)
            # v5.6.2 安全修复：加载 token_hash（而非空字符串），重启后验证码仍可验证
            token_rows = conn.execute(
                "SELECT actor, token_hash FROM second_factor_tokens"
            ).fetchall()
            for row in token_rows:
                self._second_factor_token_hashes[row[0]] = row[1]
        except sqlite3.Error as e:
            logger.error("隐私持久化数据加载失败: %s", e)

    def scan(self, text: str) -> PrivacyScanResult:
        """扫描文本中的敏感信息"""
        detected_types = []
        max_sensitivity = 0
        masked = text

        for info_type, patterns in SENSITIVE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    detected_types.append(info_type)
                    masked = re.sub(pattern, "[已脱敏]", masked, flags=re.IGNORECASE)
                    if info_type in ("password", "id_card", "bank_card"):
                        max_sensitivity = max(max_sensitivity, 3)
                    else:
                        max_sensitivity = max(max_sensitivity, 2)
                    break

        keyword_count = sum(
            1 for kw in SENSITIVE_KEYWORDS if kw.lower() in text.lower()
        )
        if keyword_count > 0:
            max_sensitivity = max(max_sensitivity, 1)
            if "敏感词" not in detected_types:
                detected_types.append("敏感词")

        is_sensitive = len(detected_types) > 0

        if max_sensitivity >= 3:
            suggested = PrivacyLevel.STRICT
        elif max_sensitivity >= 2:
            suggested = PrivacyLevel.PRIVATE
        elif max_sensitivity >= 1:
            suggested = PrivacyLevel.INTERNAL
        else:
            suggested = PrivacyLevel.PUBLIC

        confidence = min(1.0, len(detected_types) * 0.3 + keyword_count * 0.1)

        return PrivacyScanResult(
            is_sensitive=is_sensitive,
            suggested_privacy=suggested,
            confidence=confidence,
            detected_types=detected_types,
            masked_preview=masked[:500],
            details={
                "keyword_count": keyword_count,
                "sensitivity_score": max_sensitivity,
            },
        )

    def check_access(
        self, entry: MemoryEntry, actor: str = "", session_id: str = ""
    ) -> Tuple[bool, str]:
        """检查访问权限"""
        # v5.7.3 安全修复：保留原始 actor 判定"本机无身份调用"（CLI/单用户部署），
        # 与远程显式自称 anonymous 相区分，避免无来源 INTERNAL 记忆被误拒或越权。
        raw_actor = actor
        if not actor:
            actor = "anonymous"

        if entry.privacy == PrivacyLevel.PUBLIC:
            return True, "公开级记忆"

        if entry.privacy == PrivacyLevel.INTERNAL:
            # 本机进程内无身份调用（CLI / 单用户部署 / 进程内适配器未传 actor）：
            # 视同用户本人放行；一旦调用者显式携带身份（含 anonymous），
            # 必须精确匹配来源，杜绝远程越权读取。
            if not raw_actor and not session_id:
                return True, "本机内部调用"
            # 有来源：必须精确匹配，空值不参与匹配（杜绝越权读取）
            if (entry.source_agent and entry.source_agent == actor) or (
                entry.source_session and entry.source_session == session_id
            ):
                return True, "同 Agent/会话"
            return False, "内部级记忆仅同 Agent/会话可访问"

        if entry.privacy == PrivacyLevel.PRIVATE:
            if self._check_grant(entry.id, actor):
                return True, "已授权访问"
            if entry.source_agent == actor:
                return True, "记忆所有者"
            return False, "私密级记忆需要显式授权"

        if entry.privacy == PrivacyLevel.STRICT:
            if self._check_grant(entry.id, actor) and self._verify_second_factor(actor):
                return True, "严格级授权访问"
            return False, "严格级记忆需要二次验证"

        return False, "未知隐私级别"

    def grant_access(
        self,
        memory_id: str,
        grantee: str,
        granted_by: str = "",
        duration_hours: Optional[float] = None,
        access_level: str = "read",
    ) -> AccessGrant:
        """授予访问权限

        P1 安全修复：验证调用者（granted_by）有权授权 — 必须是记忆所有者
        或具有 admin 权限，防止任意用户给任意人授权 PRIVATE/STRICT 记忆。
        """
        # 权限校验：granted_by 必须是所有者或有授权权限
        if not granted_by:
            raise PermissionError("授权需要调用者身份（granted_by 不能为空）")

        if grantee == granted_by:
            raise PermissionError("不能给自己授权")

        is_owner = False
        if self.storage:
            entry = self.storage.get_memory(memory_id)
            if entry is None:
                raise ValueError(f"记忆不存在: {memory_id}")
            # 检查是否为所有者（source_agent 或 metadata 中的 owner 字段）
            entry_owner = getattr(entry, "source_agent", "") or ""
            meta_owner = (
                (entry.metadata or {}).get("owner", "")
                if hasattr(entry, "metadata")
                else ""
            )
            is_owner = (granted_by == entry_owner) or (granted_by == meta_owner)

        # 非所有者拒绝授权（admin 角色可在未来扩展）
        if not is_owner:
            raise PermissionError(f"用户 {granted_by} 无权授权记忆 {memory_id}")

        grant = AccessGrant(
            grant_id=str(uuid.uuid4()),
            memory_id=memory_id,
            grantee=grantee,
            granted_by=granted_by,
            granted_at=time.time(),
            expires_at=time.time() + duration_hours * 3600 if duration_hours else None,
            access_level=access_level,
        )

        self._maybe_purge_expired_grants()
        with self._grants_lock:
            if memory_id not in self._grants:
                self._grants[memory_id] = []
            self._grants[memory_id].append(grant)

        # v5.4.2：持久化到 SQLite
        try:
            conn = self.storage._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO access_grants (grant_id, memory_id, grantee, granted_by, granted_at, expires_at, access_level) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    grant.grant_id,
                    grant.memory_id,
                    grant.grantee,
                    grant.granted_by,
                    grant.granted_at,
                    grant.expires_at,
                    grant.access_level,
                ),
            )
            conn.commit()
        except sqlite3.Error as e:
            logger.error("授权持久化失败: %s", e)

        return grant

    def revoke_access(self, memory_id: str, grantee: str) -> bool:
        """撤销访问权限"""
        if memory_id not in self._grants:
            return False

        with self._grants_lock:
            self._grants[memory_id] = [
                g for g in self._grants[memory_id] if g.grantee != grantee
            ]
        # v5.4.2：同步删除持久化记录
        try:
            conn = self.storage._get_conn()
            conn.execute(
                "DELETE FROM access_grants WHERE memory_id = ? AND grantee = ?",
                (memory_id, grantee),
            )
            conn.commit()
        except sqlite3.Error as e:
            logger.error("撤销授权持久化失败: %s", e)
        return True

    def purge_expired_grants(self) -> int:
        """删除已过期授权（内存字典 + SQLite 持久层），返回删除条数。

        v5.6.5 P1 #7：此前 _check_grant 只在检查时跳过过期 grant，过期条目
        永久驻留 _grants 与 access_grants 表，长期运行持续膨胀。
        """
        now = time.time()
        removed = 0
        with self._grants_lock:
            for mid in list(self._grants.keys()):
                alive = [
                    g
                    for g in self._grants[mid]
                    if not (g.expires_at is not None and g.expires_at < now)
                ]
                removed += len(self._grants[mid]) - len(alive)
                if alive:
                    self._grants[mid] = alive
                else:
                    self._grants.pop(mid, None)
        if removed:
            try:
                conn = self.storage._get_conn()
                conn.execute(
                    "DELETE FROM access_grants "
                    "WHERE expires_at IS NOT NULL AND expires_at < ?",
                    (now,),
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.error("清理过期授权持久化失败: %s", e)
        return removed

    def _maybe_purge_expired_grants(self) -> None:
        """节流触发过期授权清理，失败不影响主流程"""
        now = time.time()
        if now - self._last_grant_purge < self._GRANT_PURGE_INTERVAL:
            return
        self._last_grant_purge = now
        try:
            self.purge_expired_grants()
        except Exception as e:
            logger.error("purge_expired_grants failed: %s", e)

    def _check_grant(self, memory_id: str, actor: str) -> bool:
        """检查授权（v5.6.5 P1 #7：节流清理 + 遍历加锁）"""
        self._maybe_purge_expired_grants()
        now = time.time()
        with self._grants_lock:
            grants = list(self._grants.get(memory_id, []))
        for grant in grants:
            if grant.grantee == actor:
                if grant.expires_at is not None and grant.expires_at < now:
                    continue
                return True
        return False

    def _verify_second_factor(self, actor: str) -> bool:
        """二次验证（v5.6.2 安全修复：基于会话的验证，而非仅检查注册）

        STRICT 级别记忆需要二次验证。此前该方法仅检查 token 是否存在（truthy），
        导致只要注册过 2FA 就直接放行，形同虚设。

        v5.6.2 修复：
        - 检查 actor 是否在验证会话窗口内（默认 5 分钟）
        - 未注册 2FA 的 actor 一律拒绝
        - 注册过但未验证的 actor 也拒绝（需先调用 verify_second_factor_with_code）
        - 验证会话过期后需重新验证
        """
        if not actor:
            return False
        # 检查是否已注册 2FA
        if actor not in self._second_factor_token_hashes:
            return False
        # 检查是否在验证会话有效期内（时间基准为 time.monotonic()）
        # 缺省 0 表示「从未验证」：monotonic() 读数恒远大于窗口，故被正确判为过期。
        verified_at = self._verified_2fa_sessions.get(actor, 0)
        if time.monotonic() - verified_at > self._2FA_VERIFY_WINDOW:
            return False
        return True

    def register_second_factor(self, actor: str, token: str) -> bool:
        """注册二次验证令牌（v5.3.3 新增）

        v5.4.2 安全加固：令牌以 SHA-256 hash 存储，不明文持久化。
        v5.6.2 安全加固：内存中也只存 hash，不明文驻留。

        Args:
            actor: 需要二次验证的用户/Agent
            token: 验证令牌（如 TOTP 密钥、一次性密码）

        Returns:
            是否注册成功
        """
        if not actor or not token:
            return False
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self._second_factor_token_hashes[actor] = token_hash
        # v5.4.2：持久化 hash 到 SQLite（不明文存储）
        try:
            conn = self.storage._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO second_factor_tokens (actor, token_hash, created_at) VALUES (?, ?, ?)",
                (actor, token_hash, time.time()),
            )
            conn.commit()
        except sqlite3.Error as e:
            logger.error("2FA 令牌持久化失败: %s", e)
        return True

    def verify_second_factor_with_code(self, actor: str, code: str) -> bool:
        """使用验证码进行二次验证（v5.3.3 新增）

        v5.6.2 安全修复：
        - 使用 hash 比对而非明文比对
        - 验证通过后记录到验证会话，_verify_second_factor 在窗口内放行

        Args:
            actor: 用户/Agent ID
            code: 验证码

        Returns:
            验证是否通过
        """
        if not actor or not code:
            return False
        now = time.monotonic()
        # v5.7.3 安全加固：锁定期内直接拒绝，避免在线穷举验证码
        locked_until = self._2fa_locked_until.get(actor, 0.0)
        if now < locked_until:
            return False
        token_hash = self._second_factor_token_hashes.get(actor)
        if not token_hash:
            return False
        # 使用 hmac.compare_digest 防止时序攻击（比较 hash 值）
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        ok = hmac.compare_digest(token_hash, code_hash)
        if ok:
            # 验证通过：清零失败计数，记录会话（time.monotonic，仅存内存，重启即失效）
            self._2fa_fail_counts.pop(actor, None)
            self._verified_2fa_sessions[actor] = now
            return True

        # 验证失败：累计计数，达到阈值触发锁定
        failures = self._2fa_fail_counts.get(actor, 0) + 1
        self._2fa_fail_counts[actor] = failures
        if failures >= self._2FA_MAX_FAILURES:
            self._2fa_locked_until[actor] = now + self._2FA_LOCK_SECONDS
            self._2fa_fail_counts.pop(actor, None)
            logger.warning(
                "2FA 验证连续失败 %d 次，锁定 actor=%s 共 %.0f 秒",
                failures,
                actor,
                self._2FA_LOCK_SECONDS,
            )
        return False



    # ===================== v5.8.3 标准 TOTP（RFC 6238）=====================
    def register_totp_secret(self, actor: str,
                             secret_b32: Optional[str] = None) -> Optional[str]:
        """注册标准 TOTP 共享密钥（v5.8.3，RFC 6238）。

        修复 P3-1：此前 register_second_factor 存的是 sha256(token)，验证时
        对传入 code 做同值哈希比较——若上层把长期共享密钥当 code 传，2FA
        即退化为静态口令。本方法按 RFC 6238 语义：注册只存 base32 密钥，
        验证对时间派生码（verify_totp_code）比较。

        Args:
            actor: 用户/Agent ID
            secret_b32: 可选 base32 密钥；缺省生成随机 20 字节密钥

        Returns:
            base32 密钥（用于配置 Authenticator），失败返回 None
        """
        if not actor:
            return None
        if secret_b32 is None:
            secret_b32 = base64.b32encode(secrets.token_bytes(20)).decode()
        secret_b32 = secret_b32.upper().strip()
        try:
            base64.b32decode(secret_b32, casefold=True)
        except Exception:
            return None
        blob = None
        if self.storage.encryption is not None:
            try:
                blob = self.storage.encryption.encrypt(secret_b32)
            except Exception:
                blob = None
        self._totp_secrets[actor] = secret_b32
        if blob is not None:
            try:
                conn = self.storage._get_conn()
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS totp_secrets ("
                    "actor TEXT PRIMARY KEY, secret_enc TEXT NOT NULL, "
                    "created_at REAL)")
                conn.execute(
                    "INSERT OR REPLACE INTO totp_secrets "
                    "(actor, secret_enc, created_at) VALUES (?, ?, ?)",
                    (actor, json.dumps(blob.to_dict()), time.time()))
                conn.commit()
            except sqlite3.Error as e:
                logger.error("TOTP 密钥持久化失败: %s", e)
        return secret_b32

    def verify_totp_code(self, actor: str, code: str,
                         window: int = 1) -> bool:
        """验证 TOTP 6 位码（v5.8.3，RFC 6238：HMAC-SHA1，30s 步长 ±window）。

        复用 2FA 会话窗口 / 失败锁定机制（与 verify_second_factor_with_code 一致）。
        """
        if not actor or not code:
            return False
        now = time.monotonic()
        locked_until = self._2fa_locked_until.get(actor, 0.0)
        if now < locked_until:
            return False
        secret = self._totp_secrets.get(actor)
        if secret is None:
            secret = self._load_totp_secret(actor)
            if secret is None:
                return False
        if self._verify_totp(secret, code, window):
            self._2fa_fail_counts.pop(actor, None)
            self._verified_2fa_sessions[actor] = now
            return True
        failures = self._2fa_fail_counts.get(actor, 0) + 1
        self._2fa_fail_counts[actor] = failures
        if failures >= self._2FA_MAX_FAILURES:
            self._2fa_locked_until[actor] = now + self._2FA_LOCK_SECONDS
            self._2fa_fail_counts.pop(actor, None)
        return False

    @staticmethod
    def _totp_value(secret_b32: str, at_time: float, step: int = 30,
                    digits: int = 6) -> str:
        """RFC 6238 TOTP 派生码：HMAC-SHA1(secret, counter) 动态截断取 6 位。"""
        counter = int(at_time // step)
        msg = struct.pack(">Q", counter)
        digest = hmac.new(base64.b32decode(secret_b32, casefold=True), msg,
                          hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        code = (struct.unpack(">I", digest[offset:offset + 4])[0]
                & 0x7FFFFFFF) % (10 ** digits)
        return str(code).zfill(digits)

    def _verify_totp(self, secret_b32: str, code: str, window: int) -> bool:
        now = time.time()
        for w in range(-window, window + 1):
            if hmac.compare_digest(
                    self._totp_value(secret_b32, now + w * 30), str(code)):
                return True
        return False

    def _load_totp_secret(self, actor: str) -> Optional[str]:
        """从 SQLite 读取并解密 TOTP 密钥（v5.8.3）。"""
        try:
            conn = self.storage._get_conn()
            row = conn.execute(
                "SELECT secret_enc FROM totp_secrets WHERE actor = ?",
                (actor,)).fetchone()
        except sqlite3.Error:
            return None
        if row is None or not row[0] or self.storage.encryption is None:
            return None
        try:
            from core.encryption import EncryptedBlob
            blob = EncryptedBlob.from_dict(json.loads(row[0]))
            secret = self.storage.encryption.decrypt(blob)
            self._totp_secrets[actor] = secret
            return secret
        except Exception as e:
            logger.error("TOTP 密钥解密失败: %s", e)
            return None

    def generate_compliance_report(self) -> dict:
        """生成合规报告"""
        import time

        stats = self.storage.get_stats()

        total = stats.get("total", 0)
        by_privacy = stats.get("by_privacy", {})

        audit_log = self.storage.get_audit_log(limit=1000)
        access_count = sum(1 for a in audit_log if a.action == "access")
        denied_count = 0

        private_count = by_privacy.get("PRIVATE", 0) + by_privacy.get("STRICT", 0)

        return {
            "report_time": time.time(),
            "total_memories": total,
            "private_memories": private_count,
            "strict_memories": by_privacy.get("STRICT", 0),
            "public_memories": by_privacy.get("PUBLIC", 0),
            "active_grants": sum(len(g) for g in self._grants.values()),
            "total_access_events": access_count,
            "compliance_status": "PASS" if private_count == 0 else "REVIEW",
            "by_privacy": by_privacy,
            "encryption_enabled": self.storage.encrypted,
            "audit_log_entries": len(audit_log),
        }

    def export_with_privacy(
        self, entries: List[MemoryEntry], anonymize: bool = False
    ) -> List[dict]:
        """带隐私保护的导出"""
        result = []
        for entry in entries:
            data = entry.to_dict()

            if entry.privacy in (PrivacyLevel.PRIVATE, PrivacyLevel.STRICT):
                if anonymize:
                    scan = self.scan(entry.content)
                    data["content"] = scan.masked_preview
                    data["anonymized"] = True
                else:
                    data["content"] = "[已加密 - 需要授权解密]"
                    data["encrypted_export"] = True

            result.append(data)

        return result

    def data_profiling(self) -> dict:
        """数据画像（隐私风险评估）"""
        all_memories = self.storage.list_memories(limit=1000)

        risk_scores = []
        categories_risk = {}

        for entry in all_memories:
            content = entry.content
            if entry.encrypted:
                try:
                    content = self.storage.decrypt_content(entry)
                except (ValueError, TypeError):
                    content = ""

            scan = self.scan(content)
            risk_scores.append(scan.confidence)

            if scan.is_sensitive:
                cat = entry.category
                categories_risk[cat] = categories_risk.get(cat, 0) + 1

        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0

        return {
            "total_scanned": len(all_memories),
            "average_risk_score": avg_risk,
            "high_risk_count": sum(1 for s in risk_scores if s > 0.7),
            "medium_risk_count": sum(1 for s in risk_scores if 0.3 < s <= 0.7),
            "low_risk_count": sum(1 for s in risk_scores if s <= 0.3),
            "high_risk_categories": sorted(
                categories_risk.items(), key=lambda x: x[1], reverse=True
            )[:5],
        }
