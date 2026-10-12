# -*- coding: utf-8 -*-
"""v5.9.3 功能与加固测试

- export_graph() 隐私过滤（max_privacy，fail-closed 默认 INTERNAL）
- privacy_report() 未知隐私等级归类（unknown_privacy_count，不泄漏等级名）
- CLI export-graph --privacy 参数
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from mindforge.core.mindforge import MindForge
from mindforge.core.types import MemoryConfig


def _make_mf(encrypted=False):
    tmp = tempfile.mkdtemp(prefix="mf_v593_", dir=_REPO)
    db = os.path.join(tmp, "m.db")
    key = os.path.join(tmp, "k.key")
    cfg = MemoryConfig(db_path=db, key_file=key, encrypted=encrypted)
    return MindForge(config=cfg), tmp


class TestExportGraphPrivacyFilter(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        # INTERNAL 记忆建边：python -> mysql
        self.mf.add("python 使用 mysql 连接数据库")
        # PRIVATE 记忆建边：飞书 -> 抖音（extract_graph 不跳过 PRIVATE，
        # 但 export_graph 默认 INTERNAL 应过滤该边）
        self.mf.add("飞书 包含 抖音", privacy="PRIVATE")
        self.mf.extract_graph()

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _edge_pairs(self, g):
        return {(e["source_name"], e["target_name"]) for e in g["edges"]}

    def test_default_filters_private(self):
        g = self.mf.export_graph()  # 默认 INTERNAL
        pairs = self._edge_pairs(g)
        self.assertIn(("python", "mysql"), pairs)
        # PRIVATE 记忆的边默认不导出
        self.assertNotIn(("飞书", "抖音"), pairs)
        # 孤立节点裁剪：PRIVATE 实体的节点不出现
        node_names = {n["name"] for n in g["nodes"]}
        self.assertIn("python", node_names)
        self.assertNotIn("飞书", node_names)
        self.assertEqual(g["max_privacy"], "INTERNAL")

    def test_max_privacy_strict_exports_all(self):
        g = self.mf.export_graph(max_privacy="STRICT")
        pairs = self._edge_pairs(g)
        self.assertIn(("python", "mysql"), pairs)
        self.assertIn(("飞书", "抖音"), pairs)

    def test_legacy_edge_without_memory_kept(self):
        kg = self.mf._knowledge_graph
        kg.add_relation("python", "java", "related_to", memory_id=None)
        g = self.mf.export_graph()
        pairs = self._edge_pairs(g)
        self.assertIn(("python", "java"), pairs)  # 空 memory_ids 兼容保留

    def test_no_plaintext(self):
        g = self.mf.export_graph()
        self.assertNotIn("连接数据库", str(g))
        g2 = self.mf.export_graph(max_privacy="STRICT")
        self.assertNotIn("连接数据库", str(g2))


class TestPrivacyReportUnknownLevel(unittest.TestCase):
    def test_unknown_level_grouped(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("正常记忆")
            conn = mf._storage._get_conn()
            conn.execute(
                "UPDATE memories SET privacy=? WHERE id=?",
                ("TOP_SECRET", e.id))
            conn.commit()
            rep = mf.privacy_report()
            self.assertEqual(rep["unknown_privacy_count"], 1)
            self.assertEqual(rep["total_active_memories"], 1)
            self.assertEqual(rep["privacy_distribution"]["INTERNAL"], 0)
            # 未知等级名不泄漏进报告
            self.assertNotIn("TOP_SECRET", str(rep))
            # 四档分布键不变
            for k in ("PUBLIC", "INTERNAL", "PRIVATE", "STRICT"):
                self.assertIn(k, rep["privacy_distribution"])
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_null_level_grouped(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("空等级记忆")
            conn = mf._storage._get_conn()
            conn.execute("UPDATE memories SET privacy=NULL WHERE id=?", (e.id,))
            conn.commit()
            rep = mf.privacy_report()
            self.assertEqual(rep["unknown_privacy_count"], 1)
            self.assertEqual(rep["total_active_memories"], 1)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestCliExportGraphPrivacy(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.mf.add("python 使用 mysql")
        self.mf.add("飞书 包含 抖音", privacy="PRIVATE")
        self.mf.extract_graph()
        self.cli_key = os.path.join(self.tmp, "cli_nokey.key")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args):
        from mindforge.cli.main import main
        from io import StringIO
        import contextlib
        buf = StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = main(args)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        return rc, buf.getvalue()

    def test_cli_default_filters(self):
        rc, out = self._run(
            ["--db-path", os.path.join(self.tmp, "m.db"),
             "--key-file", self.cli_key,
             "export-graph", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        pairs = {(e["source_name"], e["target_name"]) for e in data["edges"]}
        self.assertIn(("python", "mysql"), pairs)
        self.assertNotIn(("飞书", "抖音"), pairs)

    def test_cli_privacy_strict(self):
        rc, out = self._run(
            ["--db-path", os.path.join(self.tmp, "m.db"),
             "--key-file", self.cli_key,
             "export-graph", "--json", "--privacy", "STRICT"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        pairs = {(e["source_name"], e["target_name"]) for e in data["edges"]}
        self.assertIn(("飞书", "抖音"), pairs)

    def test_cli_invalid_privacy_rejected(self):
        rc, _out = self._run(
            ["--db-path", os.path.join(self.tmp, "m.db"),
             "--key-file", self.cli_key,
             "export-graph", "--json", "--privacy", "BAD"])
        self.assertNotEqual(rc, 0)


class TestRestGraphEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mindforge.api.server import (
            MindForgeAPIHandler, BoundedThreadingHTTPServer)

        # 隔离鉴权环境变量：确保 fail-closed 判定不受其他测试残留影响
        cls._env_backup = {}
        for k in ("MINDFORGE_API_KEY", "MINDFORGE_ALLOW_NOAUTH",
                  "MINDFORGE_MCP_REQUIRE_AUTH"):
            cls._env_backup[k] = os.environ.pop(k, None)
        os.environ["MINDFORGE_API_KEY"] = "v593-test-key"

        cls.tmp = tempfile.mkdtemp(prefix="mf_v593_api_", dir=_REPO)
        db = os.path.join(cls.tmp, "m.db")
        cls.mf = MindForge(db_path=db, encrypted=False)
        cls.mf.add("python 使用 mysql")
        cls.mf.add("飞书 包含 抖音", privacy="PRIVATE")
        cls.mf.extract_graph()

        MindForgeAPIHandler.mindforge = cls.mf
        cls.server = BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), MindForgeAPIHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.mf.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        for k, v in cls._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _get(self, path, token=""):
        import urllib.request
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_graph_endpoint_default_filters(self):
        status, data = self._get("/api/meta/graph", token="v593-test-key")
        self.assertEqual(status, 200)
        pairs = {(e["source_name"], e["target_name"]) for e in data["edges"]}
        self.assertIn(("python", "mysql"), pairs)
        self.assertNotIn(("飞书", "抖音"), pairs)

    def test_graph_endpoint_max_privacy(self):
        status, data = self._get("/api/meta/graph?max_privacy=STRICT",
                                 token="v593-test-key")
        self.assertEqual(status, 200)
        pairs = {(e["source_name"], e["target_name"]) for e in data["edges"]}
        self.assertIn(("飞书", "抖音"), pairs)

    def test_graph_endpoint_requires_auth(self):
        status, _data = self._get("/api/meta/graph")
        # 无 token：fail-closed（401/403），图谱结构不匿名暴露
        self.assertIn(status, (401, 403))


if __name__ == "__main__":
    unittest.main()
