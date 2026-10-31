"""
MindForge v6.0.0 扩展端口正式版测试
================================

覆盖 modules/v6.py 的（v6.0.0 已发布）：
- v6_status() 总览结构与默认注册（released=True，双端口 AVAILABLE）
- 多 Agent 协作端口（MultiAgentAdapterPort 状态 AVAILABLE + 实战转发）
- 6G AI 记忆预览端口（MemoryPreviewPort 状态 AVAILABLE + 五项能力完整实现）
- 端口注册表（类型校验 / 重复注册 / 覆盖 / 注销 / 获取）
- 版本号单一真值一致性（core.version ↔ 顶层包 ↔ core.storage ↔ core.mindforge）

设计约束（v6.0.0 正式版）：两个端口默认注册可用实现，不再是占位。
"""

import os
import shutil
import tempfile
import unittest

from mindforge.modules.v6 import (
    V6_TARGET_VERSION,
    V6Capability,
    V6Port,
    V6Status,
    MultiAgentCollaborationPort,
    MultiAgentAdapterPort,
    MemoryPreviewPort,
    create_multi_agent_adapter,
    register_v6_port,
    unregister_v6_port,
    get_v6_port,
    list_v6_ports,
    v6_status,
)


class TestV6StatusOverview(unittest.TestCase):
    """v6_status() 总览与默认注册状态（v6.0.0 正式发布）"""

    def test_target_version_constant(self):
        """v6 目标版本号常量"""
        self.assertEqual(V6_TARGET_VERSION, "6.0.0")

    def test_status_structure(self):
        """总览结构：目标版本 / 已发布 / 两个默认端口齐全"""
        s = v6_status()
        self.assertEqual(s["target_version"], "6.0.0")
        self.assertTrue(s["released"])
        self.assertIn("current_version", s)
        caps = {p["capability"] for p in s["ports"]}
        self.assertEqual(
            caps,
            {
                V6Capability.MULTI_AGENT_COLLABORATION.value,
                V6Capability.MEMORY_PREVIEW.value,
            },
        )

    def test_default_ports_available(self):
        """v6.0.0 正式版：两个端口默认注册可用实现，状态 AVAILABLE"""
        by_cap = {p["capability"]: p for p in v6_status()["ports"]}
        for cap in (V6Capability.MULTI_AGENT_COLLABORATION.value,
                    V6Capability.MEMORY_PREVIEW.value):
            self.assertTrue(by_cap[cap]["available"])
            self.assertEqual(by_cap[cap]["status"], V6Status.AVAILABLE.value)
            self.assertEqual(by_cap[cap]["target_version"], "6.0.0")


class TestMultiAgentPorts(unittest.TestCase):
    """多 Agent 协作端口：规范端口 + 适配器端口"""

    def test_spec_port_status_available(self):
        """规范端口声明 STATUS=AVAILABLE（正式发布）"""
        port = MultiAgentCollaborationPort()
        self.assertEqual(port.status(), V6Status.AVAILABLE)
        self.assertTrue(port.is_available())

    def test_spec_port_methods_raise_with_hint(self):
        """规范端口业务方法抛 NotImplementedError，提示走适配器/multi_agent"""
        port = MultiAgentCollaborationPort()
        for call in (
            lambda: port.create_space("s1", "leader"),
            lambda: port.list_spaces(),
            lambda: port.add_member("s1", "a", "reader", "leader"),
            lambda: port.share_memory("s1", "m1", "leader"),
            lambda: port.list_space_memories("s1", "leader"),
            lambda: port.orchestrate_task({}),
            lambda: port.trace_memory_lineage("m1"),
        ):
            with self.assertRaises(NotImplementedError) as ctx:
                call()
            self.assertIn("6.0.0", str(ctx.exception))

    def test_adapter_status_available(self):
        """适配器端口声明 STATUS=AVAILABLE 且 requires_args=True"""
        self.assertEqual(MultiAgentAdapterPort.STATUS, V6Status.AVAILABLE)
        self.assertTrue(MultiAgentAdapterPort.requires_args)

    def test_adapter_describe(self):
        """适配器 describe() 自描述：available / requires_args"""
        cm = _make_mindforge()
        try:
            adapter = create_multi_agent_adapter(cm.multi_agent)
            d = adapter.describe()
            self.assertEqual(d["capability"], "multi_agent_collaboration")
            self.assertEqual(d["status"], "available")
            self.assertTrue(d["available"])
            self.assertTrue(d["requires_args"])
        finally:
            cm.close()


class TestMemoryPreviewPort(unittest.TestCase):
    """6G AI 记忆预览端口：状态与完整能力"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mf_v6_preview_")
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.cm = _make_mindforge(self.db_path)

    def tearDown(self):
        try:
            self.cm.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_status_available(self):
        """MemoryPreviewPort 声明 STATUS=AVAILABLE，requires_args=True"""
        self.assertEqual(MemoryPreviewPort.STATUS, V6Status.AVAILABLE)
        self.assertTrue(MemoryPreviewPort.requires_args)

    def test_port_via_get_v6_port(self):
        """get_v6_port(MEMORY_PREVIEW, storage) 返回可用端口"""
        port = get_v6_port(V6Capability.MEMORY_PREVIEW, self.cm._storage)
        self.assertIsNotNone(port)
        self.assertTrue(port.is_available())

    def test_open_close_channel_lifecycle(self):
        """通道生命周期：open → list → snapshot → stats → close → 清理"""
        port = MemoryPreviewPort(self.cm._storage)
        entry = self.cm.add("可快照的记忆", category="team")
        ch = port.open_preview_channel(config={"name": "p1"})
        self.assertTrue(ch["success"])
        cid = ch["channel_id"]
        self.assertTrue(ch["db_path"].endswith(".db"))

        chans = port.list_preview_channels()
        self.assertTrue(any(c["channel_id"] == cid for c in chans))

        snap = port.snapshot_memory(cid, entry.id)
        self.assertTrue(snap["success"])
        self.assertEqual(snap["memory_id"], entry.id)

        stats = port.channel_stats(cid)
        self.assertTrue(stats["success"])
        self.assertEqual(stats["total_memories"], 1)

        cl = port.close_preview_channel(cid)
        self.assertTrue(cl["success"])
        self.assertEqual(cl["status"], "closed")
        # 关闭后通道库文件已清理
        self.assertFalse(os.path.exists(ch["db_path"]))

    def test_close_missing_channel(self):
        """关闭不存在的通道返回失败而不是抛异常"""
        port = MemoryPreviewPort(self.cm._storage)
        r = port.close_preview_channel("no-such-channel")
        self.assertFalse(r["success"])

    def test_distill_context_layers_and_budget(self):
        """上下文蒸馏：分层（HIGH 保留全文 / MEDIUM 摘要）+ 预算约束"""
        port = MemoryPreviewPort(self.cm._storage)
        e1 = self.cm.add("高价值记忆：团队年度目标与里程碑", category="general",
                         importance="high")
        e2 = self.cm.add("普通记忆：某次会议的流水记录内容偏长用于测试截断行为",
                         category="note", importance="medium")
        d = port.distill_context([e1.id, e2.id], max_tokens=30)
        self.assertTrue(d["success"])
        self.assertTrue(d["layers"]["HIGH"])
        # 高价值层保留全文
        high = d["layers"]["HIGH"][0]
        self.assertEqual(high["memory_id"], e1.id)
        self.assertIn("团队年度目标", high["content"])
        # 预算约束生效
        self.assertLessEqual(d["total_distilled_tokens"], 30)
        # 压缩确实发生（原文 tokens 更大）
        self.assertLess(d["total_distilled_tokens"], d["total_original_tokens"])

    def test_incremental_index_stats(self):
        """增量索引统计：总量 / 近 24h / FTS 覆盖 / 写入延迟"""
        port = MemoryPreviewPort(self.cm._storage)
        self.cm.add("用于索引统计的记忆")
        ix = port.incremental_index_stats()
        self.assertTrue(ix["success"])
        self.assertGreaterEqual(ix["total_memories"], 1)
        self.assertGreaterEqual(ix["new_24h"], 1)
        self.assertIn("fts_available", ix["index"])
        self.assertIn("embedding_available", ix["index"])
        self.assertGreaterEqual(ix["latest_write_lag_seconds"], 0)

    def test_timeline_shape(self):
        """单记忆时间线：current / versions / evolutions / audit_trail"""
        port = MemoryPreviewPort(self.cm._storage)
        e = self.cm.add("时间线测试记忆")
        tl = port.timeline(e.id)
        self.assertTrue(tl["success"])
        self.assertEqual(tl["memory_id"], e.id)
        self.assertIsNotNone(tl["current"])
        self.assertEqual(tl["current"]["memory_id"], e.id)
        self.assertIsInstance(tl["versions"], list)
        self.assertIsInstance(tl["evolutions"], list)
        self.assertIsInstance(tl["audit_trail"], list)


class TestV6Registry(unittest.TestCase):
    """端口注册表：类型校验 / 重复注册 / 覆盖 / 注销 / 获取

    注意：注册表是模块级全局状态，测试前后保存并恢复，
    避免影响其他用例对默认注册的断言。
    """

    def setUp(self):
        import mindforge.modules.v6 as v6mod

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


class TestVersionSingleSource(unittest.TestCase):
    """版本号单一真值：core.version ↔ 各处引用一致"""

    def test_top_level_version_matches_core_version(self):
        from mindforge.core.version import __version__ as v_core

        import mindforge as mf_pkg

        self.assertEqual(mf_pkg.__version__, v_core)

    def test_storage_and_mindforge_versions_match(self):
        from mindforge.core.mindforge import __version__ as v_mf
        from mindforge.core.storage import __version__ as v_storage
        from mindforge.core.version import __version__ as v_core

        self.assertEqual(v_storage, v_core)
        self.assertEqual(v_mf, v_core)

    def test_version_info_tuple_consistent(self):
        from mindforge.core.version import VERSION_INFO, get_version, get_version_info

        v = get_version()
        parts = tuple(int(x) for x in v.split("."))
        self.assertEqual(parts, get_version_info())
        self.assertEqual(parts, VERSION_INFO)


def _make_mindforge(db_path=None):
    from mindforge.core.mindforge import MindForge

    if db_path is None:
        db_path = os.path.join(
            tempfile.mkdtemp(prefix="mf_v6_ports_"), "test.db")
    return MindForge(db_path=db_path, encrypted=False)


if __name__ == "__main__":
    unittest.main()
