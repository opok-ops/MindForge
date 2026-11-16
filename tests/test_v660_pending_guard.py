# -*- coding: utf-8 -*-
"""v6.6.0 契约测试：加密 defer guard 接线（bug #1）+ 建库 schema_version（bug #2）。

bug #1：_require_ready() 自 v6.3.5 存在但从未接线到公开方法——
encrypted=True 且无密码（defer 状态）下调任何公开 API 得到
AttributeError('NoneType') 哑谜。修复后统一抛 SecurityError 明示。

bug #2：schema_version 表只在 migrate_to_latest() 里创建——全新库
get_db_version() 恒为 0，doctor 对出厂库恒报「版本落后 v0->v1」伪警告。
修复后建库即落 schema_version=_LATEST_DB_VERSION。
"""

import os
import sqlite3
import tempfile
import unittest

from mindforge.core.mindforge import MindForge
from mindforge.core.storage import StorageEngine
from mindforge.core.encryption import EncryptionEngine, SecurityError
from mindforge.core.types import MemoryConfig

PASSWORD = "v660-contract-password-456"


class PendingGuardContract(unittest.TestCase):
    """bug #1：defer 状态下公开 API 必须抛 SecurityError（而非 AttributeError）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v660_guard_")
        self.db = os.path.join(self.tmp, "pending.db")
        # 关键：key_file 指向不存在的路径 → 构造即 defer
        self.mf = MindForge(db_path=self.db,
                            key_file=os.path.join(self.tmp, "guard.key"),
                            encrypted=True)
        self.assertTrue(self.mf._pending_encryption)
        self.assertIsNone(self.mf._storage)

    def test_core_api_raises_security_error_not_attribute_error(self):
        """defer 状态下核心 API 抛 SecurityError，错误信息含指引。"""
        for call in (
            lambda: self.mf.add("内容"),
            lambda: self.mf.search("查询"),
            lambda: self.mf.get("any-id"),
            lambda: self.mf.update("any-id", content="新"),
            lambda: self.mf.delete("any-id"),
            lambda: self.mf.stats(),
            lambda: self.mf.list(),
        ):
            with self.assertRaises(SecurityError) as ctx:
                call()
            self.assertIn("init_with_password", str(ctx.exception))

    def test_exempt_methods_callable_in_defer_state(self):
        """豁免方法（状态查询/关闭/初始化入口）defer 下可正常调用。"""
        info = self.mf.get_encryption_info()
        self.assertIsNotNone(info)  # 不抛即为过
        self.mf.close()             # 资源释放不拦

    def test_init_with_password_unlocks_full_api(self):
        """init_with_password 后完整 API 可用，且后续调用不再被哑谜阻塞。"""
        self.mf.init_with_password(PASSWORD)
        self.assertFalse(self.mf._pending_encryption)
        entry = self.mf.add("guard 解锁后写入", tags=["contract"])
        self.assertIsNotNone(entry)
        hits = self.mf.search("guard 解锁")
        self.assertTrue(len(hits.chunks) >= 1 if hasattr(hits, "chunks")
                        else len(hits) >= 1)

    def test_normal_construction_unwrapped(self):
        """encrypted=False 正常构造不挂 wrapper（零开销路径不受影响）。"""
        plain = MindForge(db_path=os.path.join(self.tmp, "plain.db"),
                          encrypted=False)
        self.assertFalse(plain._pending_encryption)
        self.assertIs(plain.__dict__.get("add", None), None)  # 实例级未覆盖
        plain.add("明文路径正常")
        plain.close()

    def test_property_access_untouched(self):
        """property（storage 等）访问不被拦截——拦截面只覆盖方法调用。"""
        # defer 状态下 property 返回 None（原语义保留），不抛
        self.assertIsNone(self.mf.storage)


class SchemaVersionContract(unittest.TestCase):
    """bug #2：建库即落 schema_version；老库打开自动补齐。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v660_schema_")

    def test_fresh_db_version_is_latest(self):
        """全新库 get_db_version() == _LATEST_DB_VERSION（出厂即最新）。"""
        st = StorageEngine(db_path=os.path.join(self.tmp, "fresh.db"))
        try:
            self.assertEqual(st.get_db_version(), st._LATEST_DB_VERSION)
            self.assertEqual(st.get_db_version(), 1)
        finally:
            st.close()

    def test_legacy_v0_db_autoupgrades_on_open(self):
        """存量 v0 老库（无 schema_version 表）打开后自动补到 v1。"""
        db = os.path.join(self.tmp, "legacy.db")
        # 手工造 v0 老库：真实 v1 列结构的 memories 表，但无 schema_version
        # （_init_db 的 executescript 引用 starred/pinned 等列，装置须贴近真实存量库）
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, content TEXT, ciphertext BLOB, nonce BLOB,
                salt BLOB, category TEXT DEFAULT 'general', tags TEXT DEFAULT '[]',
                privacy TEXT DEFAULT 'INTERNAL', importance TEXT DEFAULT 'MEDIUM',
                memory_type TEXT DEFAULT 'text', layer TEXT DEFAULT 'short_term',
                source_session TEXT DEFAULT '', source_agent TEXT DEFAULT '',
                created_at REAL, updated_at REAL, last_accessed_at REAL,
                starred INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO memories (id, content, created_at, updated_at) "
            "VALUES ('legacy-1', '老库条目', 1.0, 1.0)")
        conn.commit()
        conn.close()
        self.assertEqual(self._read_version(db), 0)  # 前置：确认是 v0

        st = StorageEngine(db_path=db)  # 打开即触发 _init_db
        try:
            self.assertEqual(st.get_db_version(), 1)
        finally:
            st.close()

    def test_migrate_to_latest_idempotent(self):
        """migrate_to_latest 在已最新库上幂等（不产生副作用差异）。"""
        st = StorageEngine(db_path=os.path.join(self.tmp, "idem.db"))
        try:
            v1 = st.get_db_version()
            result = st.migrate_to_latest()
            self.assertEqual(result["final_version"], st._LATEST_DB_VERSION)
            self.assertEqual(st.get_db_version(), v1)
        finally:
            st.close()

    def test_doctor_would_pass_version_check(self):
        """对齐 doctor 的判定语义：current >= latest → 无伪警告。"""
        st = StorageEngine(db_path=os.path.join(self.tmp, "doctor.db"))
        try:
            self.assertGreaterEqual(st.get_db_version(), st.get_latest_db_version())
        finally:
            st.close()

    @staticmethod
    def _read_version(db_path):
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT value FROM schema_version WHERE id = 1").fetchone()
            return int(row[0]) if row else 0
        except sqlite3.OperationalError:
            return 0
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
