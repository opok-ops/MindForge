# -*- coding: utf-8 -*-
"""v6.6.4 连接登记守护契约测试

针对缺陷 #10（pthread handle 复用导致属主判活系统性误判）与
v6.6.2 P3-7（clear 拆登记）的回归守护：

背景链：
- v6.5.7 引入「遍历全连接表关闭死线程遗留连接」
- v6.6.1 缺陷 #5：check_same_thread=False + _access_lock 串行化 +
  `_conn_owners` 记录属主（存 ident，enumerate 判活）
- v6.6.2 P3-7：删除 _close_conns 末尾无条件 _all_conns.clear()
- v6.6.4 缺陷 #10（本轮）：实测 pthread handle 复用是常态——连续
  5 线程 ident 全同。ThreadingHTTPServer 每请求一线程，死线程连接
  的属主 ident 被下一个请求线程撞上 → 误判「属主存活」→ skip →
  连接永久悬挂 → Windows WinError 32 文件锁只是概率性躲过、未根除。
  探针实测：B 收尾后死线程 A 的连接残留（owner=tA ident ∈ live）。

修复：_conn_owners 存 Thread 对象，判定 owner.is_alive()（基于
tstate_lock 状态，与 ident 复用无关）。

契约：
- 线程 B 收尾后，死亡线程 A 的连接必须被关闭并移出登记表
- 存活线程（主线程）的连接登记必须原样保留
- close 失败（对象已坏）的连接被丢弃且不 crash（v6.6.1 既定语义）
- 源码守护：不再存在 ident 判活（_live_idents）与全清（.clear()）
"""
import os
import sqlite3
import tempfile
import threading
import unittest

from mindforge.core.mindforge import MindForge


def _make_mf(tmp, encrypted=True):
    mf = MindForge(db_path=os.path.join(tmp, "m.db"),
                   key_file=os.path.join(tmp, ".key"),
                   encrypted=encrypted)
    if encrypted:
        mf.init_with_password("pw-v664")
    return mf


class TestConnRegistry(unittest.TestCase):
    """缺陷 #10：属主判活与连接登记生命周期"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v664_")
        self.mf = _make_mf(self.tmp, encrypted=False)
        self.se = self.mf.storage

    def tearDown(self):
        self.mf.close()

    def _dead_thread_conn_snapshot(self):
        """起一个线程建连接后立即死亡（模拟 finally 未执行的异常线程）。

        只返回属主已死的连接（不含主线程等存活属主的连接——保留登记
        是正确行为，不能混进「应被回收」的断言）。
        """
        holder = [None]
        holder[0] = threading.Thread(target=self.se._get_conn, name="tA-dead")
        holder[0].start()
        holder[0].join()
        with self.se._access_lock:
            dead = {c for c in self.se._all_conns
                    if (o := self.se._conn_owners.get(id(c))) is not None
                    and not o.is_alive()}
        return dead, holder[0]

    def test_owner_records_thread_object(self):
        """登记表存 Thread 对象而非 ident（缺陷 #10 修复本体）"""
        self.se._get_conn()
        with self.se._access_lock:
            owners = list(self.se._conn_owners.values())
        self.assertTrue(owners)
        for owner in owners:
            self.assertIsInstance(owner, threading.Thread,
                                  "属主必须是 Thread 对象（ident 会被复用）")
            self.assertTrue(owner.is_alive())

    def test_dead_thread_conn_closed_by_other_thread_exit(self):
        """死亡线程的连接必须被下一次 _close_conns 关闭并移出"""
        snapshot, _tA = self._dead_thread_conn_snapshot()
        # B 正常收尾（顺带触发对死连接的回收）
        t = threading.Thread(target=lambda: (
            self.se._get_conn(), self.se.close_thread_conn()), name="tB")
        t.start()
        t.join()
        with self.se._access_lock:
            leftover = snapshot & self.se._all_conns
        self.assertEqual(
            leftover, set(),
            "死亡线程的连接未被关闭移出——悬挂 fd（Windows WinError 32 回归）")
        # 被移出的连接确实已关闭（无法再操作）
        with self.se._access_lock:
            remaining = set(self.se._all_conns)
        for c in snapshot - remaining:
            with self.assertRaises((sqlite3.ProgrammingError, sqlite3.Error)):
                c.execute("SELECT 1")

    def test_live_registration_survives_other_thread_close(self):
        """线程 B 收尾后，存活线程（主线程）的连接登记必须保留"""
        main_conn = self.se._get_conn()  # 主线程连接：注册
        t = threading.Thread(target=lambda: (
            self.se._get_conn(), self.se.close_thread_conn()), name="tB2")
        t.start()
        t.join()
        with self.se._access_lock:
            self.assertIn(main_conn, self.se._all_conns,
                          "存活线程的连接登记被清除——死线程连接将永久悬挂")
            self.assertTrue(self.se._conn_owners.get(id(main_conn)).is_alive())

    def test_failed_close_discarded_without_crash(self):
        """close 失败（对象已坏）的连接被丢弃且不 crash：不留死对象重试"""
        snapshot, _tA = self._dead_thread_conn_snapshot()
        victim = next(iter(snapshot))
        victim.close()  # 预先关闭，再次 close 抛 ProgrammingError
        self.se._close_conns()  # 不应抛异常
        with self.se._access_lock:
            self.assertNotIn(victim, self.se._all_conns,
                             "坏对象残留——每次遍历都会重试 close")


class TestSourceGuards(unittest.TestCase):
    """源码级守护：防倒退"""

    def _body(self):
        import inspect
        from mindforge.core.storage import StorageEngine
        src = inspect.getsource(StorageEngine._close_conns)
        # 剥注释后再断言，避免注释文本误报
        return "\n".join(line.split("#")[0] for line in src.splitlines())

    def test_alive_check_not_ident(self):
        """属主判活必须用 is_alive()，不得回退到 enumerate ident"""
        body = self._body()
        self.assertIn("owner.is_alive()", body,
                      "属主判活必须用 is_alive()（缺陷 #10）")
        self.assertNotIn("_live_idents", body,
                         "ident 判活已废除——pthread handle 复用会误判")

    def test_no_clear_in_close_conns(self):
        """_close_conns 不得全清登记表（v6.6.2 P3-7 守护延续）"""
        body = self._body()
        self.assertNotIn(".clear()", body,
                         "全清登记表会让死线程连接永久悬挂（P3-7）")


if __name__ == "__main__":
    unittest.main()
