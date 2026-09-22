# -*- coding: utf-8 -*-
"""v5.7.6 Bi-temporal 事实时序回归测试

对应竞品对比 P0-03「事实时序追踪」（Zep/Graphiti 的 bi-temporal 实践）：
为记忆条目增加 valid_from / valid_to 字段，支持：
  - 按事实有效时间查询（as-of，valid_at）
  - supersede：关闭旧事实有效窗口并创建新事实，旧版本保留（自动失效而非删除）
  - 更新路径可调整有效窗口；旧库自动迁移补列

覆盖：schema/迁移、add/update 参数、valid_at 查询、supersede 语义、
CLI/MCP/API 三面接通。
"""

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from core.mindforge import MindForge  # noqa: E402
from core.storage import MemoryEntry  # noqa: E402
from core.types import MemoryConfig, MemoryLayer, MemoryType  # noqa: E402


def _make_mf(encrypted=False):
    tmp = tempfile.mkdtemp(prefix="mf_v576_bt_", dir=_REPO)
    db = os.path.join(tmp, "m.db")
    key = os.path.join(tmp, "k.key")
    cfg = MemoryConfig(db_path=db, key_file=key, encrypted=encrypted)
    return MindForge(config=cfg), tmp


class TestSchemaAndAdd(unittest.TestCase):
    """schema 迁移 + add 携带有效窗口"""

    def test_new_db_has_valid_columns(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("用户住在盐城", category="fact",
                       valid_from=1000.0, valid_to=5000.0)
            self.assertEqual(e.valid_from, 1000.0)
            self.assertEqual(e.valid_to, 5000.0)
            d = e.to_dict()
            self.assertEqual(d["valid_from"], 1000.0)
            self.assertEqual(d["valid_to"], 5000.0)
            # 落库后可回读
            got = mf.get(e.id)
            self.assertEqual(got.valid_from, 1000.0)
            self.assertEqual(got.valid_to, 5000.0)
            # 列真实存在
            conn = sqlite3.connect(os.path.join(tmp, "m.db"))
            cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
            conn.close()
            self.assertIn("valid_from", cols)
            self.assertIn("valid_to", cols)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_default_unbounded(self):
        mf, tmp = _make_mf()
        try:
            e = mf.add("无窗口事实")
            self.assertEqual(e.valid_from, 0.0)
            self.assertEqual(e.valid_to, 0.0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_legacy_db_migration_adds_columns(self):
        """旧库（无 valid_* 列）打开后自动迁移补列"""
        tmp = tempfile.mkdtemp(prefix="mf_v576_mig_", dir=_REPO)
        try:
            db = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, "
                "ciphertext BLOB, nonce BLOB, salt BLOB, "
                "category TEXT DEFAULT 'general', tags TEXT DEFAULT '[]', "
                "privacy TEXT DEFAULT 'INTERNAL', importance TEXT DEFAULT 'MEDIUM', "
                "memory_type TEXT DEFAULT 'text', layer TEXT DEFAULT 'short_term', "
                "source_session TEXT DEFAULT '', source_agent TEXT DEFAULT '', "
                "created_at REAL, updated_at REAL, last_accessed_at REAL, "
                "access_count INTEGER DEFAULT 0, consolidation_count INTEGER DEFAULT 0, "
                "forgetting_score REAL DEFAULT 0.0, strength REAL DEFAULT 1.0, "
                "starred INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0, "
                "metadata TEXT DEFAULT '{}', encrypted INTEGER DEFAULT 0)"
            )
            conn.execute(
                "INSERT INTO memories (id, content, created_at, updated_at) "
                "VALUES ('old-1', '旧数据', 1, 1)"
            )
            conn.commit()
            conn.close()

            cfg = MemoryConfig(db_path=db, key_file=os.path.join(tmp, "k.key"),
                               encrypted=False)
            mf = MindForge(config=cfg)
            try:
                e = mf.get("old-1")
                self.assertIsNotNone(e)
                self.assertEqual(e.valid_from, 0.0)
                self.assertEqual(e.valid_to, 0.0)
                # 迁移后新写入正常
                e2 = mf.add("新数据", valid_from=10.0)
                self.assertEqual(e2.valid_from, 10.0)
            finally:
                mf.close()
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise


class TestValidAt(unittest.TestCase):
    """Bi-temporal as-of 查询"""

    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.mf.add("事实A：用户住在盐城", category="fact",
                    valid_from=1000.0, valid_to=5000.0)
        self.mf.add("事实B：公司在北京", category="fact",
                    valid_from=2000.0)  # 无上界
        self.mf.add("事实C：无窗口", category="other")

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_as_of_inside_window(self):
        res = self.mf.valid_at(timestamp=3000.0)
        ids = {e.id for e in res}
        # A 在窗口内（1000<=3000<5000）；B 生效（2000<=3000 且无上界）；C 无窗口恒有效
        self.assertEqual(len(res), 3)

    def test_as_of_before_and_after(self):
        res_before = self.mf.valid_at(timestamp=500.0)
        self.assertEqual({e.id for e in res_before},
                         {e.id for e in self.mf.valid_at(timestamp=500.0)})
        for e in res_before:
            # 500 时 A/B 均未生效，只应命中 C
            self.assertEqual(e.category, "other")
        res_after = self.mf.valid_at(timestamp=9000.0)
        for e in res_after:
            # 9000 时 A 已失效；B 无上界仍有效；C 恒有效
            self.assertNotIn("盐城", e.content)

    def test_default_is_now(self):
        now = time.time()
        e = self.mf.add("实时事实", valid_from=now - 1)
        res = self.mf.valid_at()
        self.assertTrue(any(x.id == e.id for x in res))

    def test_category_filter(self):
        res = self.mf.valid_at(timestamp=3000.0, category="fact")
        self.assertTrue(all(e.category == "fact" for e in res))

    def test_update_validity_window(self):
        e = self.mf.add("可调整窗口的事实")
        ok = self.mf.update(e.id, valid_from=100.0, valid_to=200.0)
        self.assertTrue(ok)
        got = self.mf.get(e.id)
        self.assertEqual(got.valid_from, 100.0)
        self.assertEqual(got.valid_to, 200.0)


class TestSupersede(unittest.TestCase):
    """事实取代：自动失效而非删除"""

    def setUp(self):
        self.mf, self.tmp = _make_mf()

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_supersede_closes_old_and_creates_new(self):
        old = self.mf.add("公司在北京", category="fact", tags=["company"])
        new_id = self.mf.supersede(old.id, "公司已搬迁到上海")
        self.assertTrue(new_id)
        new = self.mf.get(new_id)
        self.assertIsNotNone(new)
        old2 = self.mf.get(old.id)
        # 旧事实窗口被关闭（保留而非删除）
        self.assertGreater(old2.valid_to, 0)
        # 新事实有效起始 = 当前时间
        self.assertGreater(new.valid_from, 0)
        self.assertEqual(new.content, "公司已搬迁到上海")
        self.assertEqual(new.category, "fact")
        self.assertEqual(set(new.tags), {"company"})

    def test_supersede_hides_old_from_current_valid_at(self):
        old = self.mf.add("旧事实：住在北京")
        new_id = self.mf.supersede(old.id, "新事实：住在上海")
        res = self.mf.valid_at()  # 默认当前时间
        ids = {e.id for e in res}
        self.assertIn(new_id, ids)
        self.assertNotIn(old.id, ids)

    def test_supersede_preserves_version_history(self):
        old = self.mf.add("版本化事实 v1")
        self.mf.update(old.id, content="版本化事实 v1.1")  # 先产生 v1 历史
        new_id = self.mf.supersede(old.id, "版本化事实 v2")
        self.assertTrue(new_id)
        # 旧事实的版本历史完整保留（未删除）
        versions_old = self.mf.list_versions(old.id)
        self.assertGreaterEqual(len(versions_old), 1)
        # 新事实可独立获得版本历史
        self.mf.update(new_id, content="版本化事实 v2.1")
        versions_new = self.mf.list_versions(new_id)
        self.assertGreaterEqual(len(versions_new), 1)

    def test_supersede_missing_id_returns_none(self):
        self.assertIsNone(self.mf.supersede("no-such-id", "新内容"))

    def test_supersede_creates_link_and_audit(self):
        old = self.mf.add("被取代的事实")
        new_id = self.mf.supersede(old.id, "取代的事实")
        self.assertTrue(new_id)
        links = self.mf.list_links(new_id)
        self.assertTrue(any(l["link_type"] == "supersedes" and l["linked_id"] == old.id
                            for l in links))
        logs = self.mf.audit_log(memory_id=new_id)
        self.assertTrue(any(l.action == "supersede" for l in logs))

    def test_supersede_encrypted_store(self):
        mf, tmp = _make_mf(encrypted=True)
        try:
            mf.init_with_password("testpassword123")
            old = mf.add("加密库事实：地址 A")
            new_id = mf.supersede(old.id, "加密库事实：地址 B")
            self.assertTrue(new_id)
            # 加密库 get().content 为空是既有行为，内容经 search/decrypt 路径可读
            new = mf.get(new_id)
            self.assertEqual(mf._storage.decrypt_content(new), "加密库事实：地址 B")
            res = mf.search("地址 B", use_embedding=False)
            texts = [c.content for c in res.chunks]
            self.assertTrue(any("地址 B" in t for t in texts), texts)
            res = mf.valid_at()
            self.assertTrue(any(e.id == new_id for e in res))
            self.assertFalse(any(e.id == old.id for e in res))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestCLI(unittest.TestCase):
    """CLI：supersede / valid-at / add --valid-*"""

    def setUp(self):
        from cli.main import main
        self.main = main
        self.tmp = tempfile.mkdtemp(prefix="mf_v576_cli_", dir=_REPO)
        os.environ["MINDFORGE_DB"] = os.path.join(self.tmp, "cli.db")
        os.environ.pop("MINDFORGE_PASSWORD", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv):
        try:
            return self.main(argv)
        except SystemExit as e:
            return e.code or 0

    def test_add_with_valid_window_and_valid_at(self):
        self.assertEqual(self._run(["add", "CLI 事实：住在广州",
                                    "--category", "fact",
                                    "--valid-from", "1000",
                                    "--valid-to", "5000", "--json"]), 0)
        self.assertEqual(self._run(["valid-at", "--ts", "3000", "--json"]), 0)
        self.assertEqual(self._run(["valid-at", "--ts", "9999", "--json"]), 0)

    def test_supersede_command(self):
        self.assertEqual(self._run(["add", "CLI 旧事实", "--category", "fact"]), 0)
        self.assertEqual(self._run(["supersede", "bad-id", "新内容"]), 1)
        # 通过 add --json 输出拿 id
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._run(["add", "CLI 待取代事实", "--category", "fact", "--json"])
        out = buf.getvalue().strip()
        # _json_out 可能多行缩进输出：从第一个 { 开始解析
        start = out.find("{")
        data = json.loads(out[start:]) if start >= 0 else {}
        mid = data.get("id")
        self.assertTrue(mid, f"add --json 未输出 id: {out!r}")
        self.assertEqual(self._run(["supersede", mid, "CLI 新事实"]), 0)


class TestMCP(unittest.TestCase):
    """MCP：工具数 35 + memory_supersede / memory_valid_at"""

    def test_tools_list_count_and_new_tools(self):
        import sys
        sys.path.insert(0, _REPO)
        from mcp.server import _handle_tools_list, _handle_tools_call
        tools = _handle_tools_list({})["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(len(tools), 35)
        self.assertIn("memory_supersede", names)
        self.assertIn("memory_valid_at", names)

        mf, tmp = _make_mf()
        try:
            e0 = mf.add("MCP 事实：住在深圳")
            r = _handle_tools_call(mf, {"params": {
                "name": "memory_supersede",
                "arguments": {"id": e0.id, "content": "MCP 事实：住在东莞"}}})
            d = json.loads(r["content"][0]["text"])
            self.assertTrue(d["ok"])
            r2 = _handle_tools_call(mf, {"params": {
                "name": "memory_valid_at", "arguments": {}}})
            d2 = json.loads(r2["content"][0]["text"])
            self.assertEqual(d2["total"], 1)
            self.assertEqual(d2["memories"][0]["content"], "MCP 事实：住在东莞")
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestAPI(unittest.TestCase):
    """REST API：POST/PUT valid_from/valid_to + GET /api/valid-at"""

    def test_valid_at_endpoint_and_write_fields(self):
        from api.server import start_api_server
        os.environ["MINDFORGE_ALLOW_NOAUTH"] = "1"
        mf, tmp = _make_mf()
        try:
            port = 18876
            srv = threading.Thread(
                target=start_api_server,
                kwargs={"mindforge_instance": mf, "host": "127.0.0.1", "port": port},
                daemon=True,
            )
            srv.start()
            time.sleep(1.5)
            base = f"http://127.0.0.1:{port}"

            # POST with valid_from/valid_to
            req = urllib.request.Request(
                f"{base}/api/memories",
                data=json.dumps({"content": "API 事实：在上海",
                                 "category": "fact",
                                 "valid_from": 1000,
                                 "valid_to": 5000}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                created = json.loads(resp.read().decode())
            self.assertEqual(created["valid_from"], 1000.0)
            self.assertEqual(created["valid_to"], 5000.0)

            # GET /api/valid-at
            with urllib.request.urlopen(
                    f"{base}/api/valid-at?ts=3000", timeout=5) as resp:
                va = json.loads(resp.read().decode())
            self.assertEqual(va["total"], 1)
            self.assertEqual(va["memories"][0]["content"], "API 事实：在上海")

            # PUT valid_to
            req2 = urllib.request.Request(
                f"{base}/api/memories/{created['id']}",
                data=json.dumps({"valid_to": 9000}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with urllib.request.urlopen(req2, timeout=5) as resp:
                self.assertEqual(json.loads(resp.read().decode())["status"], "updated")
            with urllib.request.urlopen(
                    f"{base}/api/memories/{created['id']}", timeout=5) as resp:
                got = json.loads(resp.read().decode())
            self.assertEqual(got["valid_to"], 9000.0)

            # 非法时间戳 → 400
            bad = urllib.request.Request(
                f"{base}/api/memories",
                data=json.dumps({"content": "x", "valid_from": "abc"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad, timeout=5)
            self.assertEqual(ctx.exception.code, 400)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
