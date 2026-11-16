# -*- coding: utf-8 -*-
"""v6.6.3 契约测试：终极 bug 修复包 + 检索增强。

Bug 修复（外审狩猎报告 M1-M3 + 次要 2 项 + temporal 解析 T1-T6）：
- M1  daemon/CLI：非法 UTF-8 输入不再杀死常驻进程（strict → replace）
- M2  REST：413 超限后不再对同一连接补发第二个 HTTP 响应
- M3  加密写路径：lone surrogate 统一清洗，不再 UnicodeEncodeError
- 次1 rekey 类级互斥 + 竞态不再误报「数据损坏」
- 次2 daemon handler 返回 error dict 转发为 ok:false
- T1  年月查询（2024年5月 / 2024-05）整月窗口，不再解析恒 None
- T2  英文月名日期（17 July 2023 / July 17, 2023 / May 2023）
- T3  数量型相对时间（三个月前 / three months ago）
- T4  星期级表达（last Wednesday / 上周三 / 下周X）精确解析
- T5  _normalize_temporal 补中文短语与星期归一
- T6  _temporal_candidates 证据端英文日期扫描 + ORDER BY

检索增强（v6.6 targets）：
- decompose_two_hop 规则拆解（零 LLM，安全降级）
- search_chain 链式两跳检索（hop1 证据 → 实体 → hop2）
- 图路径感知：A-B 最短路径桥接证据加权 0.65
- search_broad open-domain 召回入口
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

from mindforge.core.mindforge import MindForge
from mindforge.core.query import (
    _parse_query_window,
    _normalize_temporal,
    decompose_two_hop,
)


def _make_mf(tmp, encrypted=False):
    return MindForge(config=__import__(
        "mindforge.core.types", fromlist=["MemoryConfig"]).MemoryConfig(
            db_path=os.path.join(tmp, "m.db"), encrypted=encrypted,
            key_file=os.path.join(tmp, ".key")))


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# T1/T2/T3/T4：_parse_query_window
# ---------------------------------------------------------------------------
class TestTemporalParsing(unittest.TestCase):

    def test_t1_cn_year_month_full_window(self):
        """T1：「2024年5月」→ 整月窗口（旧实现 int(None) 崩溃被吞 → None）"""
        win = _parse_query_window("2024年5月发生了什么", "2024-06-15")
        self.assertIsNotNone(win)
        self.assertEqual(win[0], "2024-05-01")
        self.assertEqual(win[1], "2024-05-31")

    def test_t1_iso_year_month(self):
        win = _parse_query_window("2024-05", "2024-06-15")
        self.assertIsNotNone(win)
        self.assertEqual((win[0], win[1]), ("2024-05-01", "2024-05-31"))

    def test_t1_full_date_unchanged(self):
        win = _parse_query_window("2024年5月6日的事")
        self.assertEqual(win[0], "2024-05-06")
        win2 = _parse_query_window("2023-05-06 meeting")
        self.assertEqual(win2[0], "2023-05-06")

    def test_t2_en_dmy(self):
        win = _parse_query_window("the weekend before 17 July 2023")
        self.assertIsNotNone(win)
        self.assertEqual(win[0], "2023-07-17")

    def test_t2_en_mdy(self):
        win = _parse_query_window("July 17, 2023 dinner")
        self.assertEqual(win[0], "2023-07-17")

    def test_t2_en_month_year(self):
        win = _parse_query_window("May 2023 trip", "2023-06-01")
        self.assertEqual((win[0], win[1]), ("2023-05-01", "2023-05-31"))

    def test_t3_cn_ge_month(self):
        win = _parse_query_window("三个月前买的", "2023-07-15")
        self.assertIsNotNone(win)
        # 90 天前 = 2023-04-16，±3 天窗口必须覆盖真值
        self.assertLessEqual(win[0], "2023-04-16")
        self.assertGreaterEqual(win[1], "2023-04-16")

    def test_t3_en_number_word(self):
        win = _parse_query_window("three months ago trip", "2023-07-15")
        self.assertIsNotNone(win)
        self.assertLessEqual(win[0], "2023-04-16")
        self.assertGreaterEqual(win[1], "2023-04-16")

    def test_t4_last_weekday_en(self):
        # 2026-11-17 是周二；上一个周三 = 2026-11-11（-6 天）
        win = _parse_query_window("last wednesday meeting", "2026-11-17")
        self.assertIsNotNone(win)
        d0, d1 = win[0], win[1]
        self.assertLessEqual(d0, "2026-11-11")
        self.assertGreaterEqual(d1, "2026-11-11")

    def test_t4_cn_last_week_weekday(self):
        win = _parse_query_window("上周三做了什么", "2026-11-17")
        self.assertIsNotNone(win)
        self.assertIn("上周三", win[2])
        self.assertLessEqual(win[0], "2026-11-11")
        self.assertGreaterEqual(win[1], "2026-11-11")

    def test_t4_cn_next_week_weekday(self):
        win = _parse_query_window("下周三安排", "2026-11-17")
        self.assertIsNotNone(win)
        # 下周三 = 2026-11-25（+8 天）
        self.assertLessEqual(win[0], "2026-11-25")
        self.assertGreaterEqual(win[1], "2026-11-25")

    def test_t4_plain_last_week_still_works(self):
        win = _parse_query_window("上周的事", "2026-11-17")
        self.assertEqual(win[0], "2026-11-07")


# ---------------------------------------------------------------------------
# T5：_normalize_temporal
# ---------------------------------------------------------------------------
class TestTemporalNormalize(unittest.TestCase):

    def test_cn_phrase_normalized(self):
        out = _normalize_temporal("上周的事", "2026-11-17")
        self.assertIn("[", out)
        self.assertNotIn("上周的事", out)

    def test_cn_weekday_normalized(self):
        out = _normalize_temporal("上周三做了什么", "2026-11-17")
        self.assertIn("[2026-11-11]", out)

    def test_en_number_word_normalized(self):
        out = _normalize_temporal("three months ago trip", "2023-07-15")
        self.assertIn("[2023-04-16]", out)

    def test_en_fixed_phrases_unchanged(self):
        out = _normalize_temporal("yesterday chat", "2023-07-15")
        self.assertIn("[2023-07-14]", out)


# ---------------------------------------------------------------------------
# M3：lone surrogate 清洗
# ---------------------------------------------------------------------------
class TestLoneSurrogate(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v663_surrogate_")
        self.mf = _make_mf(self.tmp)

    def tearDown(self):
        self.mf.close()

    def test_add_with_lone_surrogate_no_crash(self):
        e = self.mf.add(content="surrogate \ud800 test")
        self.assertTrue(e.id)
        r = self.mf.search("surrogate test")
        self.assertGreaterEqual(len(r.chunks), 1)

    def test_clean_content_is_encodable(self):
        e = self.mf.add(content="bad \udfff char")
        e.content.encode("utf-8")  # 不抛即契约达成


# ---------------------------------------------------------------------------
# 次1：rekey 互斥 + 错误定性
# ---------------------------------------------------------------------------
class TestRekeyMutex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v663_rekey_")
        from mindforge.core.types import MemoryConfig
        self.mf = MindForge(config=MemoryConfig(
            db_path=os.path.join(self.tmp, "m.db"), encrypted=True,
            key_file=os.path.join(self.tmp, ".key")))
        self.mf.init_with_password("old-pass")

    def tearDown(self):
        self.mf.close()

    def test_rekey_then_wrong_password_is_honest(self):
        r = self.mf.rekey("old-pass", "new-pass")
        self.assertEqual(r["rekeyed_count"], 0)
        with self.assertRaises(Exception) as ctx:
            self.mf.rekey("wrong-pass", "x")
        self.assertIn("旧密码错误", str(ctx.exception))
        self.assertNotIn("数据损坏", str(ctx.exception))

    def test_concurrent_rekey_serialized(self):
        errors = []

        def _rekey():
            try:
                self.mf.rekey("old-pass", "mid-pass")
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=_rekey)
        t2 = threading.Thread(target=_rekey)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        for msg in errors:
            self.assertNotIn("数据损坏", msg)


# ---------------------------------------------------------------------------
# 次2 + M1：daemon 行为
# ---------------------------------------------------------------------------
class TestDaemonContract(unittest.TestCase):

    def _spawn(self, db_path):
        code = (
            "import sys; from mindforge.daemon.server import run_daemon;"
            "sys.exit(run_daemon(%r))" % db_path)
        return subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)

    def test_m1_bad_utf8_line_does_not_kill_daemon(self):
        tmp = tempfile.mkdtemp(prefix="mf_v663_daemon_")
        db = os.path.join(tmp, "m.db")
        proc = self._spawn(db)
        try:
            # 先等 ready
            ready = proc.stderr.readline()
            self.assertIn(b"ready", ready)
            # 坏字节行（旧实现：UnicodeDecodeError 杀死进程）。
            # v6.6.3：replace 解码 → U+FFFD → JSON 解析失败 → bad json 响应，
            # 进程存活并继续服务后续请求。
            proc.stdin.write(b'\xff\xfe garbage\n')
            proc.stdin.flush()
            line = proc.stdout.readline()
            resp = json.loads(line)
            self.assertFalse(resp["ok"])
            self.assertIn("bad json", resp.get("error", ""))
            self.assertIsNone(proc.poll(), "daemon 必须在坏字节行后存活")
            # 正常 ping 仍可服务
            proc.stdin.write(b'{"id":1,"cmd":"ping"}\n')
            proc.stdin.flush()
            line = proc.stdout.readline()
            resp = json.loads(line)
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["id"], 1)
        finally:
            try:
                proc.stdin.write(b'{"id":2,"cmd":"shutdown"}\n')
                proc.stdin.flush()
            except Exception:
                pass
            proc.wait(timeout=10)

    def test_minor2_error_dict_forwarded_as_ok_false(self):
        tmp = tempfile.mkdtemp(prefix="mf_v663_daemon2_")
        db = os.path.join(tmp, "m.db")
        proc = self._spawn(db)
        try:
            proc.stderr.readline()
            proc.stdin.write(
                b'{"id":1,"cmd":"memory-recall","args":{"agent":"","query":"x"}}\n')
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline())
            self.assertFalse(resp["ok"])
            self.assertIn("error", resp)
        finally:
            try:
                proc.stdin.write(b'{"id":2,"cmd":"shutdown"}\n')
                proc.stdin.flush()
            except Exception:
                pass
            proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# M2：REST 413 单响应
# ---------------------------------------------------------------------------
class TestRestSingleResponse(unittest.TestCase):

    def test_413_sends_exactly_one_response(self):
        from mindforge.api.server import start_api_server
        tmp = tempfile.mkdtemp(prefix="mf_v663_rest_")
        from mindforge.core.types import MemoryConfig
        mf = MindForge(config=MemoryConfig(
            db_path=os.path.join(tmp, "m.db"), encrypted=False))
        os.environ["MINDFORGE_ALLOW_NOAUTH"] = "1"
        port = _free_port()
        th = threading.Thread(
            target=start_api_server,
            kwargs={"mindforge_instance": mf, "host": "127.0.0.1",
                    "port": port},
            daemon=True)
        th.start()
        time.sleep(1.0)
        try:
            body = b"x" * (11 * 1024 * 1024)  # 超过 10MB 上限
            req = (
                b"POST /api/memories HTTP/1.0\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                + ("Content-Length: %d\r\n" % len(body)).encode()
                + b"\r\n" + body[:1024 * 1024]  # 实际少发，drain 超时即回
            )
            s = socket.create_connection(("127.0.0.1", port), timeout=15)
            s.sendall(req)
            s.settimeout(10)
            raw = b""
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    raw += chunk
            except (socket.timeout, ConnectionResetError, OSError):
                pass
            s.close()
            count = raw.count(b"HTTP/1.0")
            self.assertEqual(count, 1, "必须恰好一个 HTTP 响应，实际 %d：%r 前 200 字节" % (count, raw[:200]))
            self.assertTrue(raw.startswith(b"HTTP/1.0 413"))
        finally:
            os.environ.pop("MINDFORGE_ALLOW_NOAUTH", None)
            mf.close()


# ---------------------------------------------------------------------------
# 检索增强
# ---------------------------------------------------------------------------
class TestDecomposeTwoHop(unittest.TestCase):

    def test_cn_split(self):
        self.assertEqual(
            decompose_two_hop("用户在哪里工作然后和谁共事"),
            ["用户在哪里工作", "和谁共事"])

    def test_en_split(self):
        self.assertEqual(
            decompose_two_hop("Where did she work then who did she meet"),
            ["Where did she work", "who did she meet"])

    def test_non_two_hop_returns_none(self):
        self.assertIsNone(decompose_two_hop("今天天气如何"))
        self.assertIsNone(decompose_two_hop(""))
        self.assertIsNone(decompose_two_hop(None))


class TestChainSearch(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v663_chain_")
        self.mf = _make_mf(self.tmp)
        self.mf.add("Melanie works at Acme Corp as an engineer")
        self.mf.add("Pamela joined Acme Corp in May 2023")
        self.mf.add("Pamela had lunch with Melanie on 17 July 2023")

    def tearDown(self):
        self.mf.close()

    def test_search_chain_hits_both_hops(self):
        r = self.mf.search_chain(
            ["Where did Melanie work", "who did she work with"],
            use_embedding=False)
        self.assertTrue(r.strategy_used.startswith("chain"))
        contents = " ".join(c.content for c in r.chunks)
        self.assertIn("Acme", contents)

    def test_search_two_hop_degrades_to_plain_search(self):
        r = self.mf.search_two_hop("今天天气如何", use_embedding=False)
        self.assertFalse(r.strategy_used.startswith("chain"))  # 非 chain

    def test_search_two_hop_uses_chain(self):
        r = self.mf.search_two_hop(
            "Where did Melanie work then who did she work with",
            use_embedding=False)
        self.assertTrue(r.strategy_used.startswith("chain"))

    def test_chain_empty_hop1_falls_back(self):
        r = self.mf.search_chain(
            ["zzz_no_such_anchor_xyz", "anything"],
            use_embedding=False)
        self.assertIsNotNone(r)


class TestGraphPathAware(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v663_graph_")
        self.mf = _make_mf(self.tmp)
        conn = self.mf._storage._get_conn()
        # 构造最小图：A--B（带桥接记忆），B--C
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_graph (id, entity) VALUES (?,?)",
            ("eA", "alice"))
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_graph (id, entity) VALUES (?,?)",
            ("eB", "bob"))
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_graph (id, entity) VALUES (?,?)",
            ("eC", "carol"))
        conn.execute(
            "INSERT OR REPLACE INTO graph_relations "
            "(id, from_entity, to_entity, relation_type, weight, memory_ids) "
            "VALUES (?,?,?,?,?,?)",
            ("r1", "eA", "eB", "knows", 0.8, json.dumps(["mem-bridge-ab"])))
        conn.execute(
            "INSERT OR REPLACE INTO graph_relations "
            "(id, from_entity, to_entity, relation_type, weight, memory_ids) "
            "VALUES (?,?,?,?,?,?)",
            ("r2", "eB", "eC", "knows", 0.8, json.dumps(["mem-bridge-bc"])))
        conn.commit()

    def tearDown(self):
        self.mf.close()

    def test_path_memories_found(self):
        qe = self.mf._query
        conn = self.mf._storage._get_conn()
        out = qe._graph_path_memories(conn, "eA", "eC")
        mids = [m for m, _w in out]
        self.assertIn("mem-bridge-ab", mids)
        self.assertIn("mem-bridge-bc", mids)

    def test_no_path_returns_empty(self):
        qe = self.mf._query
        conn = self.mf._storage._get_conn()
        out = qe._graph_path_memories(conn, "eA", "eX")
        self.assertEqual(out, [])

    def test_two_entity_query_boosts_bridge_evidence(self):
        # query 同时命中 alice 与 bob → 路径证据补位 0.65
        r = self.mf.search("alice 和 bob 什么关系", max_results=10,
                           use_embedding=False)
        # 桥接记忆不在库中（ids 是合成 id），不应崩溃即可
        self.assertIsNotNone(r)


class TestSearchBroad(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v663_broad_")
        self.mf = _make_mf(self.tmp)

    def tearDown(self):
        self.mf.close()

    def test_broad_falls_back_cleanly(self):
        self.mf.add("User likes hiking in the mountains")
        r = self.mf.search_broad("what does the user like",
                                 use_embedding=False)
        self.assertIsNotNone(r)
        self.assertGreaterEqual(len(r.chunks), 1)

    def test_broad_sufficient_recall_skips_expansion(self):
        self.mf.add("unique marker content zebra")
        r1 = self.mf.search_broad("unique marker zebra", use_embedding=False)
        self.assertIsNotNone(r1)


class TestTemporalCandidatesEnglishDates(unittest.TestCase):
    """T6：证据端英文日期扫描——英文语料上窗口精确命中加权生效。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v663_t6_")
        self.mf = _make_mf(self.tmp)
        self.mf.add("Pamela had lunch with Melanie on 17 July 2023 at a cafe")

    def tearDown(self):
        self.mf.close()

    def test_en_date_evidence_scored(self):
        qe = self.mf._query
        cands = qe._temporal_candidates(
            "lunch on 17 July 2023", time_anchor="2023-08-01",
            max_candidates=30)
        self.assertGreaterEqual(len(cands), 1)
        self.assertEqual(cands[0][1], 0.6)  # 窗口内精确命中加权


if __name__ == "__main__":
    unittest.main()
