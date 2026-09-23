# -*- coding: utf-8 -*-
"""v5.7.7 Agent 记忆治理 + 冲突自动调和 + 多源连接器框架 回归测试

对应六项能力建议中已裁定的三个真实增量：
  P1 Agent 自主记忆管理 → agent_pin / agent_forget / agent_decay_boost（expire_memory）
  P2 冲突检测与自动调和 → reconcile_conflicts（Bi-temporal 自动失效，非删除）
  P1 多源连接器框架     → modules/connectors.py（json/csv/markdown/file/url + 注册表 + SSRF 防护）

覆盖：storage/facade 语义、CLI/MCP/API 三面接通、审计动作白名单。
"""

import contextlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from core.mindforge import MindForge  # noqa: E402
from core.types import MemoryConfig  # noqa: E402


def _make_mf(encrypted=False):
    tmp = tempfile.mkdtemp(prefix="mf_v577_", dir=_REPO)
    db = os.path.join(tmp, "m.db")
    key = os.path.join(tmp, "k.key")
    cfg = MemoryConfig(db_path=db, key_file=key, encrypted=encrypted)
    return MindForge(config=cfg), tmp


class TestAgentGovernance(unittest.TestCase):
    """Agent 自主记忆治理：pin / forget / decay_boost / expire"""

    def test_agent_pin_freezes_decay_and_raises_importance(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("关键偏好：偏好 Rust", importance="MEDIUM")
            self.assertTrue(mf.agent_pin(e.id, importance="HIGH"))
            got = mf.get(e.id)
            self.assertTrue(got.pinned)
            self.assertEqual(got.forgetting_score, 0.0)
            self.assertEqual(str(got.importance), "Importance.HIGH")
            # 审计
            rows = mf._storage.get_audit_log(memory_id=e.id)
            self.assertTrue(any(r.action == "agent_pin" for r in rows))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_pin_missing_memory_returns_false(self):
        mf, tmp = _make_mf()
        try:
            self.assertFalse(mf.agent_pin("no-such-id"))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_forget_soft_deletes_and_restorable(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("待遗忘内容")
            self.assertTrue(mf.agent_forget(e.id, reason="agent 判定过时"))
            self.assertIsNone(mf.get(e.id))
            self.assertTrue(mf.restore(e.id))
            self.assertIsNotNone(mf.get(e.id))
            rows = mf._storage.get_audit_log(memory_id=e.id)
            self.assertTrue(any(
                r.action == "agent_forget"
                and "agent 判定过时" in str((r.details or {}).get("reason", ""))
                for r in rows))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_decay_boost_increases_score_and_caps(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("低价值噪声")
            self.assertTrue(mf.agent_decay_boost(e.id, amount=3.0))
            self.assertEqual(mf.get(e.id).forgetting_score, 3.0)
            # 封顶 10
            self.assertTrue(mf.agent_decay_boost(e.id, amount=100.0))
            self.assertEqual(mf.get(e.id).forgetting_score, 10.0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_expire_memory_closes_valid_window_once(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("开放窗口事实", valid_from=100.0, valid_to=0.0)
            self.assertEqual(mf.get(e.id).valid_to, 0.0)
            self.assertTrue(mf.expire_memory(e.id))
            got = mf.get(e.id)
            self.assertGreater(got.valid_to, 0.0)
            # 幂等：已关闭返回 False
            self.assertFalse(mf.expire_memory(e.id))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_governance_respects_privacy_access(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("私有内容", privacy="STRICT")
            # 无权限 actor 无法治理该记忆
            self.assertFalse(mf.agent_pin(e.id, actor="stranger"))
            self.assertFalse(mf.agent_forget(e.id, actor="stranger"))
            self.assertFalse(mf.agent_decay_boost(e.id, actor="stranger"))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestConflictReconcile(unittest.TestCase):
    """冲突自动调和（Bi-temporal：自动失效而非删除）"""

    def _two_numeric_conflicts(self, mf, imp_a="HIGH", imp_b="LOW"):
        a = mf.add("预算上限是100万", category="fact", importance=imp_a)
        b = mf.add("预算上限是200万", category="fact", importance=imp_b)
        return a, b

    def test_keep_higher_importance_expires_lower(self):
        mf, tmp = _make_mf()
        try:
            a, b = self._two_numeric_conflicts(mf, "HIGH", "LOW")
            res = mf.reconcile_conflicts()
            self.assertEqual(len(res["reconciled"]), 1)
            r = res["reconciled"][0]
            self.assertEqual(r["suggested_action"], "keep_higher_importance")
            self.assertEqual(r["expired_id"], b.id)
            # 低重要度一方窗口已关闭，高重要度一方保持开放
            self.assertGreater(mf.get(b.id).valid_to, 0.0)
            self.assertEqual(mf.get(a.id).valid_to, 0.0)
            # 审计总账
            rows = mf._storage.get_audit_log()
            self.assertTrue(any(r2.action == "conflict_reconcile" for r2 in rows))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_keep_newer_expires_older(self):
        mf, tmp = _make_mf()
        try:
            old = mf.add("预算上限是100万", category="fact", importance="MEDIUM")
            new = mf.add("预算上限是300万", category="fact", importance="MEDIUM")
            # 人为拉开 created_at 差距（>7 天）触发 keep_newer
            conn = sqlite3.connect(os.path.join(tmp, "m.db"))
            conn.execute("UPDATE memories SET created_at = ? WHERE id = ?",
                         (time.time() - 8 * 86400, old.id))
            conn.commit()
            conn.close()
            res = mf.reconcile_conflicts()
            self.assertEqual(len(res["reconciled"]), 1)
            r = res["reconciled"][0]
            self.assertEqual(r["suggested_action"], "keep_newer")
            self.assertEqual(r["expired_id"], old.id)
            self.assertGreater(mf.get(old.id).valid_to, 0.0)
            self.assertEqual(mf.get(new.id).valid_to, 0.0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_review_needed_goes_to_needs_review(self):
        mf, tmp = _make_mf()
        try:
            a, b = self._two_numeric_conflicts(mf, "MEDIUM", "MEDIUM")
            res = mf.reconcile_conflicts()
            self.assertEqual(len(res["reconciled"]), 0)
            self.assertEqual(len(res["needs_review"]), 1)
            self.assertEqual(res["needs_review"][0]["status"], "needs_review")
            # 未执行任何失效
            self.assertEqual(mf.get(a.id).valid_to, 0.0)
            self.assertEqual(mf.get(b.id).valid_to, 0.0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_auto_false_reports_without_executing(self):
        mf, tmp = _make_mf()
        try:
            a, b = self._two_numeric_conflicts(mf, "HIGH", "LOW")
            res = mf.reconcile_conflicts(auto=False)
            self.assertEqual(len(res["reconciled"]), 0)
            self.assertEqual(len(res["needs_review"]), 1)
            self.assertEqual(res["needs_review"][0]["status"], "pending")
            self.assertEqual(mf.get(b.id).valid_to, 0.0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestConnectors(unittest.TestCase):
    """多源连接器框架：注册表 + 内置连接器 + SSRF 防护"""

    def test_builtin_connectors_registered(self):
        from modules.connectors import list_connector_names, list_connectors
        names = list_connector_names()
        for n in ("json", "csv", "markdown", "file", "url"):
            self.assertIn(n, names)
        self.assertTrue(all(c["name"] for c in list_connectors()))

    def test_json_connector_ingest(self):
        mf, tmp = _make_mf()
        try:
            src = os.path.join(tmp, "in.json")
            with open(src, "w", encoding="utf-8") as f:
                json.dump({"memories": [{"content": "连接器导入的记忆",
                                         "category": "import"}]}, f)
            stats = mf.connector_ingest("json", src)
            self.assertEqual(stats["imported"], 1)
            hits = mf.search("连接器导入", max_results=5)
            self.assertTrue(any("连接器导入" in h.content for h in hits.chunks))
            # 审计
            rows = mf._storage.get_audit_log()
            self.assertTrue(any(
                r.action == "connector_ingest" and "json" in str(r.details)
                for r in rows))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_file_connector_dispatches_markdown(self):
        mf, tmp = _make_mf()
        try:
            src = os.path.join(tmp, "notes.md")
            with open(src, "w", encoding="utf-8") as f:
                f.write("第一条笔记段落\n\n第二条笔记段落")
            stats = mf.connector_ingest("file", src)
            self.assertEqual(stats["imported"], 2)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_url_connector_rejects_private_ip(self):
        mf, tmp = _make_mf()
        try:
            with self.assertRaises(ValueError) as ctx:
                mf.connector_ingest("url", "http://127.0.0.1:8080/secret")
            self.assertIn("SSRF", str(ctx.exception))
            with self.assertRaises(ValueError):
                mf.connector_ingest("url", "ftp://example.com/x")
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unknown_connector_raises_with_available_list(self):
        mf, tmp = _make_mf()
        try:
            with self.assertRaises(ValueError) as ctx:
                mf.connector_ingest("nope", "whatever")
            self.assertIn("未知连接器", str(ctx.exception))
            self.assertIn("json", str(ctx.exception))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestCLI(unittest.TestCase):
    """CLI：agent-pin / memory-forget / memory-decay-boost / connector / conflict-reconcile"""

    def setUp(self):
        from cli.main import main
        self.main = main
        self.tmp = tempfile.mkdtemp(prefix="mf_v577_cli_", dir=_REPO)
        os.environ["MINDFORGE_DB"] = os.path.join(self.tmp, "cli.db")
        os.environ.pop("MINDFORGE_PASSWORD", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv):
        try:
            return self.main(argv)
        except SystemExit as e:
            return e.code or 0

    def _add_and_get_id(self, content):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._run(["add", content, "--json"])
        out = buf.getvalue().strip()
        start = out.find("{")
        data = json.loads(out[start:]) if start >= 0 else {}
        return data.get("id")

    def test_agent_pin_and_memory_forget(self):
        mid = self._add_and_get_id("CLI 治理目标")
        self.assertTrue(mid)
        self.assertEqual(self._run(["agent-pin", mid, "--importance", "high"]), 0)
        self.assertEqual(self._run(["memory-forget", mid, "--reason", "测试"]), 0)
        self.assertEqual(self._run(["agent-pin", "bad-id"]), 1)

    def test_connector_ingest_command(self):
        src = os.path.join(self.tmp, "c.json")
        with open(src, "w", encoding="utf-8") as f:
            json.dump({"memories": [{"content": "CLI 连接器导入"}]}, f)
        self.assertEqual(self._run(["connector-ingest", "json", src]), 0)
        self.assertEqual(self._run(["connector-list"]), 0)
        self.assertEqual(self._run(["connector-ingest", "bad-conn", src]), 1)

    def test_conflict_reconcile_command(self):
        self._run(["add", "预算上限是100万", "--category", "fact",
                   "--importance", "HIGH"])
        self._run(["add", "预算上限是200万", "--category", "fact",
                   "--importance", "LOW"])
        self.assertEqual(self._run(["conflict-reconcile", "--dry-run", "--json"]), 0)
        self.assertEqual(self._run(["conflict-reconcile", "--json"]), 0)


class TestMCP(unittest.TestCase):
    """MCP：工具数 40 + 新工具调用"""

    def test_tools_list_count_and_new_tools(self):
        import sys
        sys.path.insert(0, _REPO)
        from mcp.server import _handle_tools_list, _handle_tools_call
        tools = _handle_tools_list({})["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(len(tools), 40)
        for n in ("memory_agent_pin", "memory_agent_forget",
                  "memory_agent_decay_boost", "memory_connector_ingest",
                  "conflict_reconcile"):
            self.assertIn(n, names)

        mf, tmp = _make_mf()
        try:
            e0 = mf.add("MCP 治理目标")
            r = _handle_tools_call(mf, {"params": {
                "name": "memory_agent_pin",
                "arguments": {"id": e0.id, "importance": "high"}}})
            d = json.loads(r["content"][0]["text"])
            self.assertTrue(d["ok"])

            r2 = _handle_tools_call(mf, {"params": {
                "name": "memory_agent_forget",
                "arguments": {"id": e0.id, "reason": "mcp 测试"}}})
            d2 = json.loads(r2["content"][0]["text"])
            self.assertTrue(d2["ok"])

            r3 = _handle_tools_call(mf, {"params": {
                "name": "conflict_reconcile", "arguments": {"auto": False}}})
            d3 = json.loads(r3["content"][0]["text"])
            self.assertTrue(d3["ok"])
            self.assertIn("reconciled", d3)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestAPI(unittest.TestCase):
    """REST API：POST /api/agent/forget + GET /api/connectors + POST /api/conflicts/reconcile"""

    def test_governance_and_reconcile_endpoints(self):
        from api.server import start_api_server
        os.environ["MINDFORGE_ALLOW_NOAUTH"] = "1"
        mf, tmp = _make_mf()
        try:
            port = 18877
            srv = threading.Thread(
                target=start_api_server,
                kwargs={"mindforge_instance": mf, "host": "127.0.0.1", "port": port},
                daemon=True,
            )
            srv.start()
            time.sleep(1.5)
            base = f"http://127.0.0.1:{port}"

            # 创建记忆
            req = urllib.request.Request(
                f"{base}/api/memories",
                data=json.dumps({"content": "API 待遗忘内容"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                created = json.loads(resp.read().decode())

            # GET /api/connectors
            with urllib.request.urlopen(f"{base}/api/connectors", timeout=5) as resp:
                conns = json.loads(resp.read().decode())["connectors"]
            self.assertTrue(any(c["name"] == "json" for c in conns))

            # POST /api/agent/forget
            req2 = urllib.request.Request(
                f"{base}/api/agent/forget",
                data=json.dumps({"id": created["id"], "reason": "api 测试"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req2, timeout=5) as resp:
                self.assertEqual(json.loads(resp.read().decode())["status"], "forgotten")

            # 缺失 id → 400
            bad = urllib.request.Request(
                f"{base}/api/agent/forget",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad, timeout=5)
            self.assertEqual(ctx.exception.code, 400)

            # POST /api/conflicts/reconcile
            req3 = urllib.request.Request(
                f"{base}/api/conflicts/reconcile",
                data=json.dumps({"auto": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req3, timeout=5) as resp:
                rep = json.loads(resp.read().decode())
            self.assertEqual(rep["status"], "ok")
            self.assertIn("reconciled", rep)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
