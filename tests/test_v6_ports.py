"""
MindForge v6.0.0 扩展端口预留测试
================================

覆盖 modules/v6.py 的：
- v6_status() 总览结构与默认注册
- 占位端口（业务方法必须抛 NotImplementedError，且错误信息提示 v6.0.0）
- 端口注册表（类型校验 / 重复注册 / 覆盖 / 注销 / 获取）
- MultiAgentAdapterPort 适配器实战（真实 SQLite + MultiAgentMemoryManager 转发）
- 版本号单一真值一致性（core.version ↔ 顶层包 ↔ core.storage ↔ core.mindforge）

设计约束：v6 端口仅做接口预留，默认不启用任何新行为——本文件同时守护这一约束。
"""

import os
import shutil
import tempfile
import unittest

from modules.v6 import (
    V6_TARGET_VERSION,
    V6Capability,
    V6Port,
    V6Status,
    MultiAgentCollaborationPort,
    MemoryPreviewPort,
    create_multi_agent_adapter,
    register_v6_port,
    unregister_v6_port,
    get_v6_port,
    list_v6_ports,
    v6_status,
)


class TestV6StatusOverview(unittest.TestCase):
    """v6_status() 总览与默认注册状态"""

    def test_target_version_constant(self):
        """v6 目标版本号常量"""
        self.assertEqual(V6_TARGET_VERSION, "6.0.0")

    def test_status_structure(self):
        """总览结构：目标版本 / 未发布 / 两个默认端口齐全"""
        s = v6_status()
        self.assertEqual(s["target_version"], "6.0.0")
        self.assertFalse(s["released"])
        self.assertIn("current_version", s)
        caps = {p["capability"] for p in s["ports"]}
        self.assertEqual(
            caps,
            {
                V6Capability.MULTI_AGENT_COLLABORATION.value,
                V6Capability.MEMORY_PREVIEW.value,
            },
        )

    def test_default_ports_not_available(self):
        """默认注册的是占位实现，状态必须不可用（不启用任何新行为）"""
        by_cap = {p["capability"]: p for p in v6_status()["ports"]}
        for cap in (V6Capability.MULTI_AGENT_COLLABORATION.value,
                    V6Capability.MEMORY_PREVIEW.value):
            self.assertFalse(by_cap[cap]["available"])
            self.assertEqual(by_cap[cap]["status"], V6Status.PLANNED.value)


class TestPlaceholderPorts(unittest.TestCase):
    """占位端口：业务方法一律抛 NotImplementedError，且信息含 v6.0.0"""

    def test_multi_agent_placeholder_raises(self):
        """多 Agent 协作占位：空间 / 编排 / 溯源方法均未实现"""
        port = MultiAgentCollaborationPort()
        self.assertEqual(port.status(), V6Status.PLANNED)
        self.assertFalse(port.is_available())

        with self.assertRaises(NotImplementedError) as ctx:
            port.create_space("s1", "leader")
        self.assertIn("6.0.0", str(ctx.exception))

        for call in (
            lambda: port.list_spaces(),
            lambda: port.add_member("s1", "a", "reader", "leader"),
            lambda: port.share_memory("s1", "m1", "leader"),
            lambda: port.list_space_memories("s1", "leader"),
            lambda: port.orchestrate_task({}),
            lambda: port.trace_memory_lineage("m1"),
        ):
            with self.assertRaises(NotImplementedError):
                call()

    def test_memory_preview_placeholder_raises(self):
        """6G AI 记忆预览占位：通道 / 蒸馏 / 索引 / 时间线方法均未实现"""
        port = MemoryPreviewPort()
        self.assertEqual(port.status(), V6Status.PLANNED)

        for call in (
            lambda: port.open_preview_channel(),
            lambda: port.close_preview_channel("ch1"),
            lambda: port.distill_context(["m1"]),
            lambda: port.incremental_index_stats(),
            lambda: port.timeline("m1"),
        ):
            with self.assertRaises(NotImplementedError) as ctx:
                call()
            self.assertIn("6.0.0", str(ctx.exception))

    def test_describe_dict(self):
        """describe() 自描述信息可供 CLI / MCP / 官网展示"""
        d = MemoryPreviewPort().describe()
        self.assertEqual(d["capability"], "memory_preview")
        self.assertEqual(d["status"], "planned")
        self.assertFalse(d["available"])
        self.assertEqual(d["target_version"], "6.0.0")
        self.assertIn("MemoryPreviewPort", d["port"])

    def test_summary_placeholder_empty(self):
        """占位端口的 summary() 返回空字典"""
        self.assertEqual(MemoryPreviewPort().summary(), {})


class TestV6Registry(unittest.TestCase):
    """端口注册表：类型校验 / 重复注册 / 覆盖 / 注销 / 获取

    注意：注册表是模块级全局状态，测试前后保存并恢复，
    避免影响其他用例对默认注册的断言。
    """

    def setUp(self):
        import modules.v6 as v6mod

        self.v6mod = v6mod
        self._saved = dict(v6mod._V6_PORT_REGISTRY)
        v6mod._V6_PORT_REGISTRY.clear()

    def tearDown(self):
        self.v6mod._V6_PORT_REGISTRY.clear()
        self.v6mod._V6_PORT_REGISTRY.update(self._saved)

    def test_register_rejects_non_port_class(self):
        """非 V6Port 子类 → TypeError"""
        with self.assertRaises(TypeError):
            register_v6_port(object)  # type: ignore[arg-type]

    def test_register_rejects_bad_capability(self):
        """capability 非法（不是 V6Capability）→ ValueError"""
        class BadPort(V6Port):
            capability = "not-an-enum"  # type: ignore[assignment]

            def status(self):
                return V6Status.PLANNED

        with self.assertRaises(ValueError):
            register_v6_port(BadPort)

    def test_register_duplicate_requires_override(self):
        """同一能力重复注册默认拒绝，override=True 可替换"""
        register_v6_port(MemoryPreviewPort)
        with self.assertRaises(ValueError):
            register_v6_port(MemoryPreviewPort)
        register_v6_port(MemoryPreviewPort, override=True)
        self.assertEqual(len(list_v6_ports()), 1)

    def test_get_returns_none_when_unregistered(self):
        """未注册能力 → get_v6_port 返回 None"""
        self.assertIsNone(get_v6_port(V6Capability.MEMORY_PREVIEW))

    def test_unregister_semantics(self):
        """注销：存在返回 True，不存在返回 False，之后 get 返回 None"""
        register_v6_port(MemoryPreviewPort)
        self.assertTrue(unregister_v6_port(V6Capability.MEMORY_PREVIEW))
        self.assertFalse(unregister_v6_port(V6Capability.MEMORY_PREVIEW))
        self.assertIsNone(get_v6_port(V6Capability.MEMORY_PREVIEW))


class TestMultiAgentAdapter(unittest.TestCase):
    """适配器实战：真实 SQLite + MultiAgentMemoryManager，验证转发可用"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_v6_adapter_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make(self):
        from core.mindforge import MindForge

        return MindForge(db_path=self.db_path, encrypted=False)

    def test_adapter_forwards_real_operations(self):
        """建空间 → 加成员 → 共享记忆 → 概览，全部经 v6 端口转发"""
        cm = self._make()
        try:
            entry = cm.add("可共享的团队知识", category="team")
            adapter = create_multi_agent_adapter(cm.multi_agent)

            # 状态：实验性（有真实实现），但未到 v6 正式版
            self.assertEqual(adapter.status(), V6Status.EXPERIMENTAL)
            self.assertFalse(adapter.is_available())

            r = adapter.create_space("v6s", owner_agent="leader")
            self.assertTrue(r["success"])

            r = adapter.add_member("v6s", "worker", role="editor", actor="leader")
            self.assertTrue(r["success"])

            r = adapter.share_memory("v6s", entry.id, actor="worker")
            self.assertTrue(r["success"])
            self.assertEqual(r["version"], 1)

            r = adapter.list_space_memories("v6s", actor="leader")
            self.assertTrue(r["success"])
            self.assertEqual(r["count"], 1)

            stats = adapter.summary()
            self.assertEqual(stats["total_spaces"], 1)
            self.assertEqual(stats["total_shared_items"], 1)
        finally:
            cm.close()

    def test_adapter_describe_and_list(self):
        """适配器自描述 + 注册表可见性：EXPERIMENTAL 状态暴露给 v6_status"""
        cm = self._make()
        try:
            adapter = create_multi_agent_adapter(cm.multi_agent)
            d = adapter.describe()
            self.assertEqual(d["capability"], "multi_agent_collaboration")
            self.assertEqual(d["status"], "experimental")
            self.assertTrue(d["requires_args"])

            register_v6_port(type(adapter), override=True)
            ports = {p["capability"]: p for p in list_v6_ports()}
            self.assertEqual(
                ports["multi_agent_collaboration"]["status"], "experimental"
            )
        finally:
            cm.close()


class TestVersionSingleSource(unittest.TestCase):
    """版本号单一真值：core.version ↔ 各处引用一致"""

    def test_top_level_version_matches_core_version(self):
        from core.version import __version__ as v_core

        import MindForge as mf_pkg

        self.assertEqual(mf_pkg.__version__, v_core)

    def test_storage_and_mindforge_versions_match(self):
        from core.mindforge import __version__ as v_mf
        from core.storage import __version__ as v_storage
        from core.version import __version__ as v_core

        self.assertEqual(v_storage, v_core)
        self.assertEqual(v_mf, v_core)

    def test_version_info_tuple_consistent(self):
        from core.version import VERSION_INFO, get_version, get_version_info

        v = get_version()
        parts = tuple(int(x) for x in v.split("."))
        self.assertEqual(parts, get_version_info())
        self.assertEqual(parts, VERSION_INFO)


if __name__ == "__main__":
    unittest.main()
