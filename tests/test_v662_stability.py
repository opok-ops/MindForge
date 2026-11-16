# -*- coding: utf-8 -*-
"""v6.6.2 契约测试：storage + modules 稳定性修复包。

对应 .zwork_v662_conc.md（主题 D 并发报告）与 .zwork_v662_crypto.md（主题 A
加密报告）的九项缺陷，每项一个测试：

- P0   STRICT 守卫失效（枚举 × 字符串比较恒 False）→ 绝密记忆被压缩
- P1   rekey 写冻结（_write_gate + 持锁引擎切换，C3）
- P2   record_search 立即短事务提交（写锁长持有 P1-3 + 跨线程审计丢失 P2-4）
- P2   _flush_all_access 失败路径回滚（缺陷 #7 / P2-5 访问计数双写）
- P2   rekey BEGIN 与挂账事务碰撞（C4）
- P2   auto_archive FTS 空值 'delete'（两处，v6.6.1 cleanup_expired 同款）
- P3   _close_conns 无条件清空 _all_conns（P3-7 遗留连接回收失效）
- P3   AdaptiveContextEngine._windows 无界增长 + check-then-insert 竞态（P3-8）
- P3   KnowledgeGraph 实体/关系写竞态
"""

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from mindforge.core.types import MemoryConfig, PrivacyLevel
from mindforge.core.mindforge import MindForge
from mindforge.core.storage import StorageEngine
from mindforge.modules.context_window import AdaptiveContextEngine, MAX_WINDOWS
from mindforge.modules.knowledge_graph import KnowledgeGraph

try:
    from mindforge.core.encryption import EncryptionEngine
    _HAS_CRYPTO = True
except Exception:  # pragma: no cover
    _HAS_CRYPTO = False


def _make_mf(tmp, encrypted=False):
    return MindForge(config=MemoryConfig(
        db_path=os.path.join(tmp, "m.db"), encrypted=encrypted))


class TestStrictGuardContract(unittest.TestCase):
    """P0：STRICT 记忆绝不进压缩（auto_compress 与 compress_group 双路径）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662_strict_")
        self.mf = _make_mf(self.tmp, encrypted=False)
        self.st = self.mf._storage
        self.strict_ids = []
        for i in range(3):
            e = self.st.add_memory(
                "绝密项目 Alpha 部署密钥统一存放于内网保险柜第%d层" % i,
                privacy=PrivacyLevel.STRICT)
            self.strict_ids.append(e.id)
        # 前置条件：index._doc_texts 非空（真实搜索 hydrate 的等价形态）
        n = self.mf._index.hydrate(self.st.get_indexable_documents())
        self.assertGreater(n, 0)

    def tearDown(self):
        self.mf.close()

    def _strict_marked(self):
        marked = 0
        for mid in self.strict_ids:
            e = self.st.get_memory(mid)
            if e.privacy == PrivacyLevel.STRICT and \
                    (e.metadata or {}).get("compressed_into"):
                marked += 1
        return marked

    def test_auto_compress_never_touches_strict(self):
        """auto_compress：STRICT 簇整体跳过，源不被标记、明文不进摘要。"""
        res = self.mf.compress_memories(threshold=0.5, min_cluster_size=2)
        self.assertEqual(self._strict_marked(), 0,
                         "STRICT 源被标记 compressed_into（守卫失效）")
        for c in res.get("clusters", []):
            comp = {x["cluster_id"]: x for x in self.mf.list_compressions()}.get(
                c["cluster_id"])
            if comp:
                sm = self.st.get_memory(comp["summary_id"])
                self.assertNotIn("绝密项目 Alpha", sm.content,
                                 "STRICT 明文泄漏进摘要记忆")

    def test_compress_group_excludes_strict(self):
        """compress_group：STRICT 被剔除；全 STRICT 组返回 None 不写摘要。"""
        # 全 STRICT：剔除后不足 2 条 → None
        compressor = self.mf._get_compressor()
        self.assertIsNone(compressor.compress_group(self.strict_ids))
        # 混合组：摘要不含 STRICT 明文，STRICT 源不被标记
        internal = [self.st.add_memory("公开周报 Beta 第%d期" % i).id
                    for i in range(2)]
        res = compressor.compress_group(self.strict_ids + internal)
        self.assertIsNotNone(res)
        self.assertNotIn("绝密项目 Alpha", res["summary"])
        self.assertEqual(self._strict_marked(), 0)


@unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装，跳过加密测试")
class TestRekeyWriteGateContract(unittest.TestCase):
    """P1/C3：rekey 全程写冻结，storage.encryption 在写闸内完成切换。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662_gate_")
        self.old_engine, _ = EncryptionEngine.from_password("oldpass")
        self.new_engine, _ = EncryptionEngine.from_password(
            "newpass", os.urandom(16))
        self.st = StorageEngine(db_path=os.path.join(self.tmp, "m.db"),
                                encryption=self.old_engine, encrypted=True)

    def tearDown(self):
        self.st.close()

    def test_write_gate_freezes_writes_during_rekey(self):
        """rekey 进行中到达的 add 被冻结到重加密+切钥完成后，明文可用新引擎解出。"""
        self.st.add_memory("row one")
        started = threading.Event()
        orig_decrypt = self.old_engine.decrypt

        def slow_decrypt(blob):
            started.set()
            time.sleep(0.3)  # 拉宽 rekey 窗口
            return orig_decrypt(blob)

        self.old_engine.decrypt = slow_decrypt
        try:
            done = threading.Event()

            def rekey_worker():
                try:
                    self.st.rekey_memories(self.old_engine, self.new_engine)
                finally:
                    done.set()

            t = threading.Thread(target=rekey_worker)
            t.start()
            self.assertTrue(started.wait(5), "rekey 未进入解密阶段")
            t0 = time.time()
            late = self.st.add_memory("窗口内到达的写入")  # 应在写闸上阻塞
            blocked = time.time() - t0
            self.assertTrue(done.wait(1), "add 返回时 rekey 尚未完成（写闸未串行化）")
            self.assertGreaterEqual(blocked, 0.2,
                                    "写未被 rekey 冻结（闸门未生效）")
            # 引擎已在闸门内切换：晚到写入用新引擎加密，可正常读出
            self.assertIs(self.st.encryption, self.new_engine)
            got = self.st.get_memory(late.id)
            self.assertEqual(self.st.decrypt_content(got), "窗口内到达的写入")
            t.join(5)
        finally:
            self.old_engine.decrypt = orig_decrypt

    def test_empty_db_rekey_still_switches_engine(self):
        """空库 rekey 返回 0，但仍持闸完成引擎切换（窗口内首条写入不被旧引擎加密）。"""
        n = self.st.rekey_memories(self.old_engine, self.new_engine)
        self.assertEqual(n, 0)
        self.assertIs(self.st.encryption, self.new_engine)
        e = self.st.add_memory("empty-db late write")
        self.assertEqual(self.st.decrypt_content(self.st.get_memory(e.id)),
                         "empty-db late write")


class TestRecordSearchImmediateCommit(unittest.TestCase):
    """P2/P1-3 + P2-4：search 审计立即短事务——无 flush 即可见、不持写锁。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662_rs_")
        self.db = os.path.join(self.tmp, "m.db")
        self.st = StorageEngine(db_path=self.db)

    def tearDown(self):
        self.st.close()

    def test_audit_committed_without_flush(self):
        """record_search 返回后，另一条独立连接即可见审计行（已 commit）。"""
        self.st.record_search("q-immediate")
        probe = sqlite3.connect(self.db)  # 独立连接：只看已提交数据
        try:
            n = probe.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action='search' "
                "AND memory_id='q-immediate'").fetchone()[0]
        finally:
            probe.close()
        self.assertEqual(n, 1, "search 审计未随 record_search 提交")

    def test_concurrent_write_not_blocked_by_search(self):
        """搜索主体进行期间（模拟慢检索），并发写不再被搜索审计的写锁阻塞。"""
        self.st.add_memory("种子条目")
        res = {}

        def slow_search():
            self.st.record_search("q-slow")  # 修复前：此处起持写锁直至尾部 flush
            time.sleep(1.0)                  # 模拟嵌入/fit 等搜索耗时
            self.st.flush_access()

        def concurrent_write():
            time.sleep(0.2)
            t0 = time.time()
            try:
                self.st.add_memory("搜索进行中的并发写入")
                res["b"] = ("ok", round(time.time() - t0, 2))
            except Exception as e:  # pragma: no cover
                res["b"] = ("FAILED", round(time.time() - t0, 2),
                            type(e).__name__, str(e))

        t1 = threading.Thread(target=slow_search)
        t2 = threading.Thread(target=concurrent_write)
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(res["b"][0], "ok", "并发写失败：%r" % (res["b"],))
        self.assertLess(res["b"][1], 5, "并发写被写锁阻塞超过 busy_timeout")


class TestFlushFailureRollbackContract(unittest.TestCase):
    """P2/缺陷 #7（P2-5）：flush 失败必须回滚线程连接，增量只应用一次。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662_flush_")
        self.st = StorageEngine(db_path=os.path.join(self.tmp, "m.db"))

    def tearDown(self):
        self.st.close()

    def test_failed_flush_rolls_back_no_double_count(self):
        """executemany 已应用后 commit 失败：回滚 + 合并回挂账，终值无双写。"""
        e = self.st.add_memory("访问计数双写验证条目")
        mid = e.id
        # 两次访问事件（用 SimpleNamespace 避免 get_memory 自身的访问计数干扰）
        probe = SimpleNamespace(id=mid)
        self.st._update_access(probe, "", "", _batch=True)
        self.st._update_access(probe, "", "", _batch=True)
        self.assertEqual(self.st._access_pending.get(mid), 2)
        self.assertEqual(
            self.st._get_conn().execute(
                "SELECT access_count FROM memories WHERE id=?", (mid,)).fetchone()[0],
            0)

        orig_get_conn = self.st._get_conn
        real = orig_get_conn()

        class ConnProxy:
            def __init__(self, r): self._r = r
            def __getattr__(self, name): return getattr(self._r, name)
            def executemany(self, sql, rows):
                self._r.executemany(sql, rows)  # UPDATE 已全部应用
                raise RuntimeError("simulated disk-full at commit boundary")

        self.st._get_conn = lambda: ConnProxy(real)
        self.st._flush_all_access()  # 失败路径
        self.st._get_conn = orig_get_conn

        # 修复点 1：失败事务已回滚（修复前 in_transaction=True）
        self.assertFalse(real.in_transaction, "失败 flush 后事务未回滚")
        # 修复点 2：挂账增量合并回，等待重试
        self.assertEqual(self.st._access_pending.get(mid), 2)

        self.st.flush_access()  # 二次 flush：+2 一次性应用
        final = real.execute(
            "SELECT access_count FROM memories WHERE id=?", (mid,)).fetchone()[0]
        # 正确终值 = 0 + 2×1（修复前 executemany 已应用的 +2 与合并回的 +2
        # 随同一事务重复提交 → 4）
        self.assertEqual(final, 2, "访问计数双写：executemany 部分应用未回滚")


@unittest.skipUnless(_HAS_CRYPTO, "cryptography 未安装，跳过加密测试")
class TestRekeyBeginCollisionContract(unittest.TestCase):
    """P2/C4：rekey 显式 BEGIN 前提交同线程连接上的挂账事务。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662_e5_")
        self.eng, _ = EncryptionEngine.from_password("oldpass")
        self.st = StorageEngine(db_path=os.path.join(self.tmp, "m.db"),
                                encryption=self.eng, encrypted=True)

    def tearDown(self):
        self.st.close()

    def test_rekey_after_pending_audit_transaction(self):
        """同线程连接残留未提交 DML（挂账审计）时 rekey 不再抛事务碰撞。"""
        self.st.add_memory("row one")
        conn = self.st._get_conn()
        # 构造挂账事务：任一 commit=False 审计路径（如 import/合并路径）
        self.st._add_audit("search", "q", "", "", "INTERNAL", {}, commit=False)
        self.assertTrue(conn.in_transaction, "前置：挂账事务未打开")

        new_engine, _ = EncryptionEngine.from_password("newpass", os.urandom(16))
        n = self.st.rekey_memories(self.eng, new_engine)  # 修复前：SecurityError
        self.assertEqual(n, 1)
        self.assertFalse(conn.in_transaction)
        e = self.st.get_memory(
            conn.execute("SELECT id FROM memories LIMIT 1").fetchone()[0])
        self.assertEqual(self.st.decrypt_content(e), "row one")


class TestAutoArchiveFtsConsistency(unittest.TestCase):
    """P2：auto_archive 两处 FTS 'delete' 用真实旧值（空值 delete 致词项/文档不一致）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662_arc_")
        self.mf = _make_mf(self.tmp, encrypted=False)
        self.st = self.mf._storage

    def tearDown(self):
        self.mf.close()

    def _archive_and_check(self, archive_fn):
        e = self.st.add_memory("uniquewordXYZ marker content", category="general")
        conn = self.st._get_conn()
        # 伪装成过期 sensory 记忆
        conn.execute(
            "UPDATE memories SET layer = 'sensory', created_at = ? WHERE id = ?",
            (0.0, e.id))
        conn.commit()

        archived = archive_fn()
        archived = archived["archived"] if isinstance(archived, dict) else archived
        self.assertGreaterEqual(archived, 1, "未归档任何记忆")

        # trash 行仍应存在（软删除），FTS 词项只允许指向存活文档行
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS fts_v662 "
            "USING fts5vocab('memory_fts', 'row')")
        docs = [r[0] for r in conn.execute(
            "SELECT doc FROM fts_v662 WHERE term = 'xyz'").fetchall()]
        self.assertTrue(docs, "词项丢失：FTS 与文档行不一致（delete 过度）")
        for docid in docs:
            live = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE rowid = ?",
                (docid,)).fetchone()[0]
            self.assertEqual(live, 1,
                             "FTS 词项指向不存在的文档行（索引不一致）")

    def test_auto_archive_fts_terms_match_live_rows(self):
        """auto_archive（按层过期路径，storage.py ~8771）：词项/文档一致。"""
        self._archive_and_check(
            lambda: self.st.auto_archive(max_age_hours=0, layer="sensory"))

    def test_archive_by_ids_fts_terms_match_live_rows(self):
        """archive_memories_by_ids（按 ID 归档路径，storage.py ~8868）：同款修复。"""
        e = self.st.add_memory("uniquewordXYZ marker content", category="general")
        self.st._get_conn().execute(
            "UPDATE memories SET layer = 'sensory' WHERE id = ?", (e.id,))
        self.st._get_conn().commit()
        self._archive_and_check(
            lambda: self.st.archive_memories_by_ids(
                [e.id], reason="test", actor="t"))


class TestCloseConnsKeepsLiveRegistrations(unittest.TestCase):
    """P3/P3-7：_close_conns 不再清空 _all_conns——存活线程连接保留注册。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v662_cc_")
        self.st = StorageEngine(db_path=os.path.join(self.tmp, "m.db"))

    def tearDown(self):
        self.st.close()

    def test_live_thread_conn_stays_registered(self):
        ready = threading.Event()
        release = threading.Event()
        holder = {}

        def worker():
            holder["conn"] = self.st._get_conn()  # 登记到 _all_conns
            ready.set()
            release.wait(10)
            self.st.close_thread_conn()

        t = threading.Thread(target=worker)
        t.start()
        self.assertTrue(ready.wait(5))
        self.assertIn(holder["conn"], self.st._all_conns, "前置：未登记")

        self.st._close_conns()  # 主线程清理：存活属主的连接必须保留
        self.assertIn(holder["conn"], self.st._all_conns,
                      "存活线程连接被从注册表抹掉（P3-7 回归）")
        self.assertIn(id(holder["conn"]), self.st._conn_owners)

        release.set()
        t.join(10)
        # 线程自己 close 后应从注册表移除（登记表不泄漏）
        self.assertNotIn(holder["conn"], self.st._all_conns)


class TestContextWindowCapContract(unittest.TestCase):
    """P3/P3-8：_windows 封顶淘汰 + get_or_create_window 加锁。"""

    def setUp(self):
        self.eng = AdaptiveContextEngine(storage=None, index=None)

    def test_windows_capped_with_lru_eviction(self):
        """超过 MAX_WINDOWS 后按最久未活跃淘汰，总量被封顶。"""
        for i in range(MAX_WINDOWS + 200):
            self.eng.get_or_create_window("s-%d" % i)
        self.assertEqual(len(self.eng._windows), MAX_WINDOWS)
        # 最新会话保留，最早会话被淘汰
        self.assertIn("s-%d" % (MAX_WINDOWS + 199), self.eng._windows)
        self.assertNotIn("s-0", self.eng._windows)

    def test_concurrent_get_or_create_same_session(self):
        """并发首次访问同一 session 只产生一个窗口实例。"""
        barrier = threading.Barrier(8)
        wins = []

        def worker():
            barrier.wait(5)
            wins.append(self.eng.get_or_create_window("shared"))

        ts = [threading.Thread(target=worker) for _ in range(8)]
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertEqual(len(self.eng._windows), 1, "竞态产生重复窗口")
        self.assertTrue(all(w is wins[0] for w in wins), "窗口实例被互相覆盖")


class TestKnowledgeGraphLockContract(unittest.TestCase):
    """P3：KnowledgeGraph 实体/关系内存写竞态由 RLock 串行化。"""

    def setUp(self):
        self.kg = KnowledgeGraph(storage=None, auto_load=False)

    def test_concurrent_get_or_create_entity_single_instance(self):
        """并发 _get_or_create_entity 同名实体只创建一份。"""
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait(5)
            return self.kg._get_or_create_entity("Alice")

        results = []
        ts = [threading.Thread(target=lambda: results.append(worker()))
              for _ in range(16)]
        for t in ts: t.start()
        for t in ts: t.join()
        named = [e for e in self.kg.entities.values() if e.name == "Alice"]
        self.assertEqual(len(named), 1, "同名实体被并发重复创建")
        self.assertTrue(all(e is named[0] for e in results), "调用方拿到不同实例")

    def test_concurrent_add_relation_consistent(self):
        """并发 add_relation 不丢关系、邻接表计数一致。"""
        barrier = threading.Barrier(8)

        def worker(i):
            barrier.wait(5)
            self.kg.add_relation("A", "B", "knows", memory_id="m-%d" % i)

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertEqual(len(self.kg.relations), 8, "并发写丢失关系")
        total_edges = sum(len(v) for v in self.kg._adjacency.values())
        self.assertEqual(total_edges, 16, "邻接表丢失边（8 关系 × 双向）")


if __name__ == "__main__":
    unittest.main()
