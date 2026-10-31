"""
MindForge v6.0.0 多 Agent 记忆空间正式化测试
=========================================

覆盖 modules/multi_agent.py 在 v6.0.0 的正式化状态：
- EXPERIMENTAL 标志关闭（正式版）
- 空间 / 成员 / 共享全链路仍可用
- 隐私护栏（PRIVATE/STRICT 禁止共享）仍生效
- 审计动作（space_*）进入 storage 白名单（不再降级为 other）
- facade multi_agent property + v6_port 注入可用
"""

import os
import shutil
import tempfile
import unittest

from mindforge.modules.multi_agent import MultiAgentMemoryManager


def _make_cm(db_path=None):
    from mindforge.core.mindforge import MindForge

    if db_path is None:
        db_path = os.path.join(
            tempfile.mkdtemp(prefix="mf_v6_ma_"), "test.db")
    return MindForge(db_path=db_path, encrypted=False)


class TestFormalizationStatus(unittest.TestCase):
    """正式化状态：EXPERIMENTAL 关闭 / 版本号"""

    def test_experimental_flag_off(self):
        """v6.0.0 起 EXPERIMENTAL=False（正式能力）"""
        self.assertFalse(MultiAgentMemoryManager.EXPERIMENTAL)

    def test_target_version(self):
        self.assertEqual(MultiAgentMemoryManager.TARGET_VERSION, "6.0.0")


class TestSpaceLifecycle(unittest.TestCase):
    """空间全链路：建 / 列 / 成员 / 共享 / 统计"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_v6_ma_")
        self.cm = _make_cm(os.path.join(self.tmp_dir, "test.db"))

    def tearDown(self):
        try:
            self.cm.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_full_flow(self):
        ma = self.cm.multi_agent
        entry = self.cm.add("多 Agent 共享的知识", category="team")

        r = ma.create_space(name="s1", owner_agent="leader",
                            description="desc", policy="shared")
        self.assertTrue(r["success"])
        sid = r["space_id"]

        r = ma.add_member(sid, "worker", role="editor", actor="leader")
        self.assertTrue(r["success"])
        self.assertEqual(r["role"], "editor")

        r = ma.share_memory(sid, entry.id, actor="worker")
        self.assertTrue(r["success"])
        self.assertEqual(r["version"], 1)

        r = ma.list_space_memories(sid, actor="leader")
        self.assertTrue(r["success"])
        self.assertEqual(r["count"], 1)

        r = ma.space_stats(space_ref=sid)
        self.assertTrue(r["success"])
        self.assertEqual(r["space"]["member_count"], 2)
        self.assertEqual(r["space"]["item_count"], 1)
        # 正式版不再输出 experimental 字段
        self.assertNotIn("experimental", r)

        spaces = ma.list_spaces(agent_id="leader")
        self.assertTrue(any(s["space_id"] == sid for s in spaces))

    def test_global_stats_no_experimental(self):
        ma = self.cm.multi_agent
        r = ma.space_stats(space_ref="")
        self.assertTrue(r["success"])
        self.assertNotIn("experimental", r)
        self.assertEqual(r["version"], "6.0.0")


class TestPrivacyGuard(unittest.TestCase):
    """隐私护栏：PRIVATE/STRICT 记忆禁止共享"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_v6_ma_")
        self.cm = _make_cm(os.path.join(self.tmp_dir, "test.db"))

    def tearDown(self):
        try:
            self.cm.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_private_memory_cannot_share(self):
        from mindforge.core.types import PrivacyLevel

        ma = self.cm.multi_agent
        ma.create_space(name="s1", owner_agent="leader")
        priv = self.cm.add("隐私记忆", privacy=PrivacyLevel.PRIVATE)
        r = ma.share_memory("s1", priv.id, actor="leader")
        self.assertFalse(r["success"])
        self.assertIn("隐私", r.get("error", ""))


class TestAuditWhitelist(unittest.TestCase):
    """审计动作白名单：space_* 与 preview_* 不再降级为 other"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_v6_ma_")
        self.cm = _make_cm(os.path.join(self.tmp_dir, "test.db"))

    def tearDown(self):
        try:
            self.cm.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_space_actions_in_whitelist(self):
        src = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mindforge", "core", "storage.py"), encoding="utf-8").read()
        for action in ("space_create", "space_delete", "space_join",
                       "space_leave", "space_share", "space_unshare",
                       "preview_channel_open", "preview_channel_close",
                       "preview_snapshot"):
            self.assertIn(f'"{action}"', src, f"白名单缺 {action}")

    def test_share_writes_space_share_audit(self):
        """共享记忆后审计动作为 space_share（而非 other）"""
        ma = self.cm.multi_agent
        entry = self.cm.add("可共享记忆")
        ma.create_space(name="s1", owner_agent="leader")
        ma.share_memory("s1", entry.id, actor="leader")

        conn = self.cm._storage._get_conn()
        rows = conn.execute(
            "SELECT action FROM audit_log WHERE memory_id = ? ORDER BY timestamp DESC",
            (entry.id,)).fetchall()
        actions = [r["action"] for r in rows]
        self.assertIn("space_share", actions, f"审计动作被降级: {actions}")


class TestFacadeIntegration(unittest.TestCase):
    """facade 集成：multi_agent property + v6_port 注入 + v6_status"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_v6_ma_")
        self.cm = _make_cm(os.path.join(self.tmp_dir, "test.db"))

    def tearDown(self):
        try:
            self.cm.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_facade_v6_status_released(self):
        st = self.cm.v6_status()
        self.assertTrue(st["released"])
        caps = {p["capability"] for p in st["ports"]}
        self.assertIn("multi_agent_collaboration", caps)
        self.assertIn("memory_preview", caps)

    def test_facade_v6_port_injects_manager(self):
        from mindforge.modules.v6 import MultiAgentAdapterPort

        port = self.cm.v6_port("multi_agent_collaboration")
        self.assertIsInstance(port, MultiAgentAdapterPort)
        r = port.create_space("f1", owner_agent="me")
        self.assertTrue(r["success"])

    def test_facade_v6_port_unknown_capability(self):
        with self.assertRaises(ValueError):
            self.cm.v6_port("no-such-capability")

    def test_facade_memory_preview_available(self):
        pv = self.cm.memory_preview
        self.assertTrue(pv.is_available())


if __name__ == "__main__":
    unittest.main()
