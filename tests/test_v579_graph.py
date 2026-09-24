# -*- coding: utf-8 -*-
"""v5.7.9 知识图谱自动管道 回归测试

真实增量：
  1. modules/knowledge_graph.py：持久化加载（重启恢复）+ 同进程防抖 + 中文模式
  2. core/types.py：MemoryConfig.auto_extract_graph 开关
  3. core/mindforge.py：extract_graph / graph_stats / graph_related / graph_path / graph_entities
  4. CLI graph path 子命令 + --memory-id；API 3 个 GET + 1 个 POST；MCP +3（40→43）
  5. 审计白名单 graph_extract

覆盖：自动抽取 / 持久化 / 显式抽取 / STRICT 隐私跳过 / 关系与路径 / CLI / API / MCP / 白名单。
"""

import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from core.mindforge import MindForge  # noqa: E402
from core.types import MemoryConfig, PrivacyLevel  # noqa: E402
from modules.knowledge_graph import KnowledgeGraph  # noqa: E402


def _make_mf(encrypted=False, **cfg_extra):
    tmp = tempfile.mkdtemp(prefix="mf_v579_", dir=_REPO)
    db = os.path.join(tmp, "m.db")
    key = os.path.join(tmp, "k.key")
    cfg = MemoryConfig(db_path=db, key_file=key, encrypted=encrypted, **cfg_extra)
    return MindForge(config=cfg), tmp


class TestAutoExtract(unittest.TestCase):
    """add 自动抽取开关"""

    def test_auto_extract_on_populates_graph_and_audit(self):
        mf, tmp = _make_mf(auto_extract_graph=True)
        try:
            e = mf.add("python 使用 mysql 数据库，字节跳动公司内部团队负责维护")
            stats = mf.graph_stats()
            self.assertGreater(stats["total_entities"], 0)
            # 审计动作非 other
            rows = mf._storage.get_audit_log(memory_id=e.id)
            self.assertTrue(any(r.action == "graph_extract" for r in rows))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_auto_extract_off_by_default(self):
        mf, tmp = _make_mf()
        try:
            mf.add("python 使用 mysql")
            self.assertEqual(mf.graph_stats()["total_entities"], 0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestPersistence(unittest.TestCase):
    """重启后从持久化表恢复"""

    def test_entities_survive_restart(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("python 使用 mysql 数据库")
            st = mf.extract_graph(memory_id=e.id)
            self.assertGreater(st["entities_added"], 0)
            mf.close()
            # 新实例（模拟重启）→ load 恢复
            mf2 = MindForge(config=MemoryConfig(
                db_path=os.path.join(tmp, "m.db"),
                key_file=os.path.join(tmp, "k.key"), encrypted=False))
            try:
                st2 = mf2.graph_stats()
                self.assertGreater(st2["total_entities"], 0)
                related = mf2.graph_related("python")
                self.assertTrue(any("mysql" in n for n, _, _ in related))
            finally:
                mf2.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dedupe_same_memory_reprocess(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("python 使用 mysql")
            st1 = mf.extract_graph(memory_id=e.id)
            st2 = mf.extract_graph(memory_id=e.id)  # 防抖 → 0 新增
            self.assertGreater(st1["entities_added"], 0)
            self.assertEqual(st2["entities_added"], 0)
            self.assertEqual(st2["relations_added"], 0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestExplicitExtract(unittest.TestCase):
    """显式抽取 / 隐私 / 全库增量"""

    def test_extract_by_memory_id_and_content(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("飞书 是 办公协作工具，python 使用 mysql")
            st = mf.extract_graph(memory_id=e.id)
            self.assertGreater(st["entities_added"], 0)
            self.assertGreaterEqual(st["relations_added"], 0)
            # 纯文本抽取（不入库 memory 但入图）
            st2 = mf.extract_graph(content="docker 使用 kubernetes")
            self.assertGreater(st2["entities_added"], 0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_extract_missing_memory_raises(self):
        mf, tmp = _make_mf()
        try:
            with self.assertRaises(ValueError):
                mf.extract_graph(memory_id="no-such-id")
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_strict_privacy_skipped(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("隐私密钥内容 使用 aes256", privacy=PrivacyLevel.STRICT)
            st = mf.extract_graph(memory_id=e.id)
            self.assertEqual(st.get("skipped"), "strict_privacy")
            self.assertEqual(mf.graph_stats()["total_entities"], 0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bulk_extract_all_memories(self):
        mf, tmp = _make_mf()
        try:
            mf.add("python 使用 mysql")
            mf.add("docker 使用 kubernetes")
            st = mf.extract_graph()
            self.assertGreaterEqual(st["memory_processed"], 2)
            self.assertGreater(st["entities_added"], 0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestRelatedAndPath(unittest.TestCase):
    """关系查询与路径（BFS）"""

    def _seed_graph(self, mf):
        mf.extract_graph(content="python 使用 mysql")
        mf.extract_graph(content="mysql 使用 docker")
        mf.extract_graph(content="docker 使用 kubernetes")

    def test_related_and_path(self):
        mf, tmp = _make_mf()
        try:
            self._seed_graph(mf)
            related = mf.graph_related("python", depth=3)
            names = [n for n, _, _ in related]
            self.assertIn("mysql", names)
            self.assertIn("docker", names)
            # python → mysql → docker 路径
            path = mf.graph_path("python", "docker")
            self.assertIsNotNone(path)
            self.assertGreaterEqual(len(path["entities"]), 3)
            self.assertEqual(path["entities"][0], "python")
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_path_missing_returns_none(self):
        mf, tmp = _make_mf()
        try:
            self._seed_graph(mf)
            self.assertIsNone(mf.graph_path("python", "不存在的实体"))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_graph_entities_list(self):
        mf, tmp = _make_mf()
        try:
            self._seed_graph(mf)
            lst = mf.graph_entities(limit=10)
            names = {x["name"] for x in lst}
            self.assertIn("python", names)
            self.assertIn("mysql", names)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestCLI(unittest.TestCase):
    """CLI graph：stats / related / path / extract"""

    def _args(self, **kw):
        import argparse
        base = dict(db_path="", key_file="", json_output=False, depth=2,
                    entity="", text="", memory_id="", from_name="", to_name="")
        base.update(kw)
        return argparse.Namespace(**base)

    def test_cli_graph_stats_and_extract(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("python 使用 mysql 数据库")
            mf.extract_graph(memory_id=e.id)
            mf.close()
            from cli.main import cmd_graph
            dbp = os.path.join(tmp, "m.db")
            kp = os.path.join(tmp, "k.key")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cmd_graph(self._args(graph_action="stats",
                                            db_path=dbp, key_file=kp))
            self.assertEqual(code, 0)
            self.assertIn("实体总数", buf.getvalue())
            self.assertIn("uses", buf.getvalue())
            # related
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cmd_graph(self._args(graph_action="related", entity="python",
                                            db_path=dbp, key_file=kp))
            self.assertEqual(code, 0)
            self.assertIn("mysql", buf.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestAPI(unittest.TestCase):
    """REST API：graph 端点"""

    def _start(self):
        from api.server import start_api_server
        os.environ["MINDFORGE_ALLOW_NOAUTH"] = "1"
        mf, tmp = _make_mf()
        port = 18879
        srv = threading.Thread(
            target=start_api_server,
            kwargs={"mindforge_instance": mf, "host": "127.0.0.1", "port": port},
            daemon=True,
        )
        srv.start()
        time.sleep(1.5)
        return mf, tmp, port

    def test_graph_endpoints(self):
        mf, tmp, port = self._start()
        try:
            e = mf.add("python 使用 mysql 数据库")
            mf.extract_graph(memory_id=e.id)
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/graph/stats", timeout=5) as resp:
                stats = json.loads(resp.read().decode())
            self.assertGreater(stats["total_entities"], 0)
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/graph/related?entity=python",
                    timeout=5) as resp:
                rel = json.loads(resp.read().decode())
            self.assertTrue(any("mysql" in x[0] for x in rel["related"]))
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/graph/path?from=python&to=mysql",
                    timeout=5) as resp:
                path = json.loads(resp.read().decode())
            self.assertIsNotNone(path.get("entities"))
            # POST extract
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/graph/extract",
                data=json.dumps({"memory_id": e.id}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                ex = json.loads(resp.read().decode())
            self.assertEqual(ex["status"], "ok")
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestMCP(unittest.TestCase):
    """MCP：48 工具（43 基线 + 5 个 v5.8.0 经验工具）+ 3 个图谱工具 + handlers"""

    def test_mcp_tool_schemas(self):
        from mcp.server import TOOL_SCHEMAS, HANDLERS
        names = [t["name"] for t in TOOL_SCHEMAS]
        self.assertEqual(len(names), 48)
        self.assertIn("memory_graph_stats", names)
        self.assertIn("memory_graph_related", names)
        self.assertIn("memory_graph_extract", names)
        for n in ("memory_graph_stats", "memory_graph_related", "memory_graph_extract"):
            self.assertIn(n, HANDLERS)

    def test_mcp_handler_smoke(self):
        from mcp.server import HANDLERS
        mf, tmp = _make_mf()
        try:
            mf.add("python 使用 mysql")
            mf.extract_graph()
            r = HANDLERS["memory_graph_stats"](mf, {})
            self.assertTrue(r["ok"])
            self.assertGreater(r["stats"]["total_entities"], 0)
            r2 = HANDLERS["memory_graph_related"](mf, {"entity": "python"})
            self.assertTrue(r2["ok"])
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
