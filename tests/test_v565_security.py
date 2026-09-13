# -*- coding: utf-8 -*-
"""v5.6.5 第三轮安全/健壮性审计回归测试。

覆盖：
- P0 #1 Ed25519 非对称签名；P0 #2 联邦重放防护；P0 #3 访问计数落库失败不丢
- P1 #4 last_flush 回收；#5 索引 LRU 上限；#6 auto_archive 失败可见；
  #7 过期授权清理；#9 X-Agent-Id 默认不可伪造；#10 共享记录过期清理
- P2 #12 向量流式召回正确性；#14 备份自动轮转；#15 CORS 条件下发；
  #17 MCP 认证状态加锁；#18 入口默认库路径统一
- P3 #19 _safe_path 单点委托；#20 磁盘探测 1MB 且进程缓存；#24 依赖策略一致
"""

import os
import sys
import time
import json
import shutil
import struct
import tempfile
import inspect
import unittest
import types
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class _StorageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v565_")
        self.db = os.path.join(self.tmp, "test.db")
        from core.storage import StorageEngine
        self.storage = StorageEngine(db_path=self.db, encrypted=False)

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------- P0 #1 / #2：Ed25519 + 重放防护 ----------------
class TestFederatedEd25519(unittest.TestCase):
    def _two_nodes(self):
        from modules.federated import FederatedMemory
        node_a = FederatedMemory(local_peer_id="nodeA")
        node_b = FederatedMemory(local_peer_id="nodeB")
        # 交叉注册：只交换公钥（公钥公开即可），不共享任何对称密钥
        node_a.register_peer("nodeB", "B", trust_level=0.9,
                             public_key=node_b.local_public_key)
        node_b.register_peer("nodeA", "A", trust_level=0.9,
                             public_key=node_a.local_public_key)
        return node_a, node_b

    def test_keypair_is_ed25519_and_distinct(self):
        from modules.federated import generate_keypair
        import base64
        priv1, pub1 = generate_keypair()
        priv2, pub2 = generate_keypair()
        self.assertEqual(len(base64.urlsafe_b64decode(priv1 + "==")), 32)
        self.assertEqual(len(base64.urlsafe_b64decode(pub1 + "==")), 32)
        self.assertNotEqual(priv1, priv2)
        self.assertNotEqual(pub1, pub2)

    def test_signed_message_accepted_without_shared_secret(self):
        a, b = self._two_nodes()
        envelope, sig = a.sign_payload({"content": "hello", "v": 1}, "nodeB")
        self.assertTrue(sig)
        self.assertTrue(b.receive_memory("nodeA", envelope, sig))

    def test_forged_or_tampered_message_rejected(self):
        a, b = self._two_nodes()
        envelope, sig = a.sign_payload({"content": "hello", "v": 1}, "nodeB")
        # 无发送方私钥的攻击者篡改内容后重用原签名 → 验签失败
        tampered = dict(envelope)
        tampered["content"] = "evil"
        self.assertFalse(b.receive_memory("nodeA", tampered, sig))
        # 乱造签名同样拒绝
        self.assertFalse(b.receive_memory("nodeA", envelope, "A" * 64))

    def test_replay_same_nonce_rejected(self):
        a, b = self._two_nodes()
        envelope, sig = a.sign_payload({"content": "once"}, "nodeB")
        self.assertTrue(b.receive_memory("nodeA", envelope, sig))
        # 完全相同的合法签名消息重放 → nonce 重复，拒绝
        self.assertFalse(b.receive_memory("nodeA", envelope, sig))

    def test_distinct_nonces_each_accepted(self):
        a, b = self._two_nodes()
        e1, s1 = a.sign_payload({"content": "1"}, "nodeB")
        e2, s2 = a.sign_payload({"content": "2"}, "nodeB")
        self.assertNotEqual(e1["_mid"], e2["_mid"])
        self.assertTrue(b.receive_memory("nodeA", e1, s1))
        self.assertTrue(b.receive_memory("nodeA", e2, s2))

    def test_stale_timestamp_rejected(self):
        a, b = self._two_nodes()
        envelope, sig = a.sign_payload({"content": "old"}, "nodeB")
        envelope["_ts"] = time.time() - 9999  # 远超偏差窗口
        # 重新用真实私钥对“旧时间戳”信封签名（攻击者无法靠改时间戳绕过）
        sig_old = a._compute_signature(envelope, "nodeB")
        self.assertFalse(b.receive_memory("nodeA", envelope, sig_old))

    def test_signed_channel_requires_envelope(self):
        a, b = self._two_nodes()
        data = {"content": "no envelope"}
        sig = a._compute_signature(data, "nodeB")
        # 已签名通道缺少 _ts/_mid → 拒绝（防止不带防重放字段的消息灌入）
        self.assertFalse(b.receive_memory("nodeA", data, sig))

    def test_hmac_legacy_still_verifies(self):
        from modules.federated import FederatedMemory
        a = FederatedMemory(local_peer_id="a")
        b = FederatedMemory(local_peer_id="b")
        a.register_peer("b", "B", trust_level=0.9, shared_secret="shh")
        b.register_peer("a", "A", trust_level=0.9, shared_secret="shh")
        env, sig = a.sign_payload({"content": "legacy"}, "b")
        self.assertTrue(b.receive_memory("a", env, sig))


# ---------------- P0 #3：落库失败不丢访问计数 ----------------
class TestAccessFlushDurability(_StorageCase):
    class _BadConn:
        def execute(self, *a, **k):
            return self

        def commit(self):
            raise RuntimeError("disk full")

    def test_pending_preserved_on_commit_failure_then_flushed(self):
        entry = self.storage.add_memory(content="x")
        real_get_conn = self.storage._get_conn  # 绑定方法，先备份再恢复
        self.storage._access_pending[entry.id] = 5
        self.storage._get_conn = lambda: self._BadConn()
        # commit 抛错：增量必须保留，且不更新 last_flush
        self.storage._flush_access(entry.id, 123.0)
        self.assertEqual(self.storage._access_pending.get(entry.id), 5)
        self.assertNotIn(entry.id, self.storage._access_last_flush)
        # 恢复真实连接后落库成功，计数精确写入并清空挂账
        self.storage._get_conn = real_get_conn
        self.storage._flush_access(entry.id, time.time())
        self.assertNotIn(entry.id, self.storage._access_pending)
        conn = real_get_conn()
        n = conn.execute(
            "SELECT access_count FROM memories WHERE id=?", (entry.id,)
        ).fetchone()[0]
        self.assertEqual(n, 5)


# ---------------- P1 #4：last_flush 回收 ----------------
class TestAccessLastFlushPrune(_StorageCase):
    def test_stale_ids_pruned_active_kept(self):
        now = time.time()
        self.storage._access_last_flush = {"old": now - 9999, "hot": now}
        self.storage._access_pending = {"hot": 1}
        self.storage._access_last_prune = 0.0
        self.storage._maybe_prune_access_locked(now)
        self.assertNotIn("old", self.storage._access_last_flush)
        self.assertIn("hot", self.storage._access_last_flush)


# ---------------- P1 #5：IndexEngine LRU 上限 ----------------
class TestIndexEngineBound(unittest.TestCase):
    def test_lru_cap_bounds_all_structures(self):
        from core.indexer import IndexEngine
        idx = IndexEngine(max_docs=5)
        for i in range(8):
            idx.index_memory(f"d{i}", f"这是第 {i} 条关于人工智能与记忆系统的内容")
        self.assertEqual(len(idx._doc_texts), 5)
        self.assertEqual(len(idx.vector_index.vectors), 5)
        self.assertEqual(idx.evicted_count, 3)
        # 被淘汰的是最早写入的 d0/d1/d2
        self.assertNotIn("d0", idx._doc_texts)
        self.assertIn("d7", idx._doc_texts)

    def test_unlimited_when_zero(self):
        from core.indexer import IndexEngine
        idx = IndexEngine(max_docs=0)
        self.assertIsNone(idx.max_docs)
        for i in range(6):
            idx.index_memory(f"x{i}", f"内容 {i} 人工智能 记忆")
        self.assertEqual(len(idx._doc_texts), 6)


# ---------------- P1 #6：auto_archive 失败可见 ----------------
class TestAutoArchiveReportsFailure(_StorageCase):
    def test_result_contains_failure_fields(self):
        e = self.storage.add_memory(content="old")
        conn = self.storage._get_conn()
        conn.execute("UPDATE memories SET created_at=? WHERE id=?",
                     (time.time() - 999999, e.id))
        conn.commit()
        res = self.storage.auto_archive(max_age_hours=0, layer="short_term")
        self.assertIn("failed", res)
        self.assertIn("failed_ids", res)
        self.assertEqual(res["failed"], 0)
        self.assertGreaterEqual(res["archived"], 1)

    def test_failure_counted_not_swallowed(self):
        e = self.storage.add_memory(content="old2")
        conn = self.storage._get_conn()
        conn.execute("UPDATE memories SET created_at=? WHERE id=?",
                     (time.time() - 999999, e.id))
        conn.commit()
        real = self.storage._get_conn()

        class _ConnProxy:
            def execute(self, sql, *params):
                u = sql.lstrip().upper()
                if u.startswith("INSERT") and "archived_memories" in sql:
                    raise RuntimeError("disk locked")
                return real.execute(sql, *params)

            def commit(self):
                return real.commit()

            def __getattr__(self, name):
                return getattr(real, name)

        self.storage._get_conn = lambda: _ConnProxy()
        res = self.storage.auto_archive(max_age_hours=0, layer="short_term")
        self.assertGreaterEqual(res["failed"], 1)
        self.assertIn(e.id, res["failed_ids"])


# ---------------- P1 #7：过期授权清理 ----------------
class TestPrivacyGrantPurge(_StorageCase):
    def test_purge_removes_expired_everywhere(self):
        from modules.privacy import PrivacyEngine
        entry = self.storage.add_memory(content="私密", source_agent="owner")
        pe = PrivacyEngine(self.storage)
        pe.grant_access(entry.id, "bob", granted_by="owner", duration_hours=1)
        # 手动置为已过期（内存 + 持久层）
        g = pe._grants[entry.id][0]
        g.expires_at = time.time() - 10
        conn = self.storage._get_conn()
        conn.execute("UPDATE access_grants SET expires_at=? WHERE grant_id=?",
                     (time.time() - 10, g.grant_id))
        conn.commit()
        removed = pe.purge_expired_grants()
        self.assertEqual(removed, 1)
        self.assertNotIn(entry.id, pe._grants)
        n = conn.execute("SELECT COUNT(*) FROM access_grants").fetchone()[0]
        self.assertEqual(n, 0)


# ---------------- P1 #10：共享记录过期清理 ----------------
class TestSharedMemoryPurge(_StorageCase):
    def test_expired_shared_purged(self):
        from modules.federated import FederatedMemory
        fed = FederatedMemory(storage=self.storage, local_peer_id="local")
        fed.register_peer("p", "P", trust_level=0.9, public_key=fed.local_public_key)
        m = self.storage.add_memory(content="s")
        shared = fed.share_memory(m.id, ["p"], expires_hours=1)
        self.assertIsNotNone(shared)
        shared.expires_at = time.time() - 1
        self.assertEqual(fed.purge_expired_shared_memories(), 1)
        self.assertEqual(fed.get_shared_memories(), [])


# ---------------- P2 #9：X-Agent-Id 默认不可伪造 ----------------
class TestAgentHeaderTrust(unittest.TestCase):
    def setUp(self):
        from api.server import MindForgeAPIHandler
        self.H = MindForgeAPIHandler

    def _h(self, headers=None):
        h = types.SimpleNamespace(headers=headers or {})
        # 绑定同一判定方法（其只依赖环境变量），便于以桩对象调用 _extract_actor
        h._client_identity_trusted = types.MethodType(
            self.H._client_identity_trusted, h)
        return h

    def test_default_ignores_spoofed_header(self):
        env = {k: "" for k in ("MINDFORGE_API_KEY", "MINDFORGE_TRUST_AGENT_HEADER",
                               "MINDFORGE_AGENT_ID")}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("MINDFORGE_API_KEY", None)
            os.environ.pop("MINDFORGE_TRUST_AGENT_HEADER", None)
            os.environ.pop("MINDFORGE_AGENT_ID", None)
            self.assertEqual(
                self.H._extract_actor(self._h({"X-Agent-Id": "eve"}), {}), "")

    def test_header_accepted_only_when_explicitly_trusted(self):
        env = {"MINDFORGE_API_KEY": "secret",
               "MINDFORGE_TRUST_AGENT_HEADER": "1"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                self.H._extract_actor(self._h({"X-Agent-Id": "eve"}), {}), "eve")

    def test_api_key_without_flag_still_ignores_header(self):
        env = {"MINDFORGE_API_KEY": "secret", "MINDFORGE_TRUST_AGENT_HEADER": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("MINDFORGE_TRUST_AGENT_HEADER", None)
            self.assertEqual(
                self.H._extract_actor(self._h({"X-Agent-Id": "eve"}), {}), "")


# ---------------- P2 #12：向量流式召回正确性 ----------------
class TestVectorSearchStreaming(_StorageCase):
    def test_chunked_streaming_returns_best_match(self):
        # 强制走无引擎 fallback 路径（纯 float32）
        self.storage._embedding_eng = None
        dim = 4
        docs = {
            "good": [1.0, 0.0, 0.0, 0.0],
            "mid": [0.7, 0.7, 0.0, 0.0],
            "opp": [0.0, 0.0, 1.0, 0.0],
        }
        ids = {}
        for name in docs:
            ids[name] = self.storage.add_memory(content=name).id
        conn = self.storage._get_conn()
        now = time.time()
        for name, vec in docs.items():
            blob = struct.pack(f"<{dim}f", *vec)
            conn.execute(
                "INSERT INTO memory_embeddings (memory_id, embedding, model_name,"
                " dimension, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (ids[name], blob, "fb", dim, now, now))
        conn.commit()
        res = self.storage.vector_search(query_vector=[1.0, 0.0, 0.0, 0.0], top_k=2)
        self.assertTrue(res)
        self.assertEqual(res[0]["entry"].id, ids["good"])
        self.assertEqual({r["entry"].id for r in res}, {ids["good"], ids["mid"]})


# ---------------- P2 #14：备份自动轮转 ----------------
class TestBackupRotation(_StorageCase):
    def test_delete_old_backups_keeps_n(self):
        bdir = os.path.join(self.tmp, "backups")
        os.makedirs(bdir, exist_ok=True)
        for i in range(12):
            open(os.path.join(bdir, f"memory_backup_2026010{1 if i < 9 else 2}_"
                                    f"{i:02d}0000.db"), "w").close()
        self.assertEqual(self.storage.delete_old_backups(bdir, keep_count=10), 2)
        remain = [f for f in os.listdir(bdir) if f.startswith("memory_backup_")]
        self.assertEqual(len(remain), 10)

    def test_create_backup_reports_rotation(self):
        bdir = os.path.join(self.tmp, "backups2")
        res = self.storage.create_backup(bdir, auto_rotate=True, keep_count=10)
        self.assertTrue(res["success"])
        self.assertIn("rotated", res)


# ---------------- P2 #15：CORS 条件下发 ----------------
class TestCorsHeaders(unittest.TestCase):
    class _Stub:
        def __init__(self):
            self.sent = []

        def send_response(self, code):
            self.code = code

        def send_header(self, k, v):
            self.sent.append(k)

        def end_headers(self):
            pass

        @property
        def wfile(self):
            return types.SimpleNamespace(write=lambda data: None)

    def test_no_cors_headers_without_origin(self):
        from api.server import MindForgeAPIHandler as H
        stub = self._Stub()
        with mock.patch.dict(os.environ, {"MINDFORGE_CORS_ORIGIN": ""}, clear=False):
            os.environ.pop("MINDFORGE_CORS_ORIGIN", None)
            H._send_json(stub, {"ok": True})
        self.assertNotIn("Access-Control-Allow-Methods", stub.sent)
        self.assertNotIn("Access-Control-Allow-Origin", stub.sent)

    def test_cors_headers_when_origin_set(self):
        from api.server import MindForgeAPIHandler as H
        stub = self._Stub()
        with mock.patch.dict(os.environ,
                             {"MINDFORGE_CORS_ORIGIN": "http://x.example"},
                             clear=False):
            H._send_json(stub, {"ok": True})
        self.assertIn("Access-Control-Allow-Origin", stub.sent)
        self.assertIn("Access-Control-Allow-Methods", stub.sent)
        self.assertIn("Access-Control-Allow-Headers", stub.sent)


# ---------------- P2 #17：MCP 认证状态持有者 ----------------
class TestMcpAuthState(unittest.TestCase):
    def test_state_transitions_and_lock(self):
        from mcp.server import _AuthState
        import threading
        st = _AuthState()
        self.assertFalse(st.is_authed())
        st.login(100.0)
        self.assertTrue(st.is_authed())
        self.assertTrue(st.authorize(150.0, 30 * 60))  # 窗口内，刷新到 150
        self.assertEqual(st.authorize(150.0 + 30 * 60 + 1, 30 * 60),
                         "expired")  # 距上次活动超过 30 分钟
        self.assertFalse(st.is_authed())
        st.login(1.0)
        st.reset()
        self.assertFalse(st.is_authed())
        self.assertIsInstance(st._lock, type(threading.Lock()))


# ---------------- P2 #18：入口默认库路径统一 ----------------
class TestDefaultDbPath(unittest.TestCase):
    def test_env_override(self):
        from core.paths import get_default_db_path
        with mock.patch.dict(os.environ, {"MINDFORGE_DB_PATH": "/x/y.db"}, clear=False):
            self.assertEqual(get_default_db_path(), "/x/y.db")

    def test_default_is_stable_home_path(self):
        from core.paths import get_default_db_path
        env = dict(os.environ)
        env.pop("MINDFORGE_DB_PATH", None)
        with mock.patch.dict(os.environ, env, clear=True):
            p = get_default_db_path()
            self.assertTrue(os.path.isabs(p))
            norm = p.replace("\\", "/")
            self.assertTrue(norm.endswith(".MindForge/data/store/memory.db"))

    def test_mcp_uses_shared_resolver(self):
        import inspect
        import mcp.server as mcp
        self.assertIn("get_default_db_path",
                      inspect.getsource(mcp.serve_forever))


# ---------------- P3 #19 / #20 / #24：核验型回归 ----------------
class TestCodebaseConsistency(unittest.TestCase):
    def test_safe_path_single_delegating_impl(self):
        import inspect
        import core.mindforge as mf
        import core.storage as storage
        src = inspect.getsource(mf._safe_path)
        self.assertIn("_storage_safe_path", src)
        # 功能等价：同一临时路径解析结果一致
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, "a.txt")
            open(f, "w").close()
            self.assertEqual(str(storage._safe_path(f)), str(mf._safe_path(f)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_disk_probe_is_1mb_and_cached(self):
        import inspect
        import core.storage as storage
        src = inspect.getsource(storage.HardwareProfiler)
        self.assertIn("_probe_bytes = 1024 * 1024", src)
        storage.HardwareProfiler._cached_disk_type = None
        a = storage.HardwareProfiler._detect_disk_type()
        b = storage.HardwareProfiler._detect_disk_type()
        self.assertEqual(a, b)
        self.assertEqual(storage.HardwareProfiler._cached_disk_type, a)

    def test_requirements_pin_within_pyproject_range(self):
        import re
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        req = open(os.path.join(root, "requirements.txt"), encoding="utf-8").read()
        py = open(os.path.join(root, "pyproject.toml"), encoding="utf-8").read()
        pin = re.search(r"cryptography==(\d+)\.(\d+)\.(\d+)", req).groups()
        rng = re.search(r"cryptography>=(\d+)\.(\d+)\.(\d+),<(\d+)", py)
        lo = tuple(map(int, rng.group(1, 2, 3)))
        hi_major = int(rng.group(4))
        pin = tuple(map(int, pin))
        self.assertGreaterEqual(pin, lo)
        self.assertLess(pin[0], hi_major)


if __name__ == "__main__":
    unittest.main()
