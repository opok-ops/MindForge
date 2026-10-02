#!/usr/bin/env python3
"""
MindForge v5.5.8 测试套件

覆盖内容：
1. memory_diff 版本对比功能
2. MCP 参数校验
3. Falsy 枚举值修复 (PrivacyLevel.NONE)
4. storage.py __version__ 可用性
5. 版本号一致性
"""

import os
import sys
import tempfile
import unittest

# 确保项目根目录在 path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class TestVersionConsistency(unittest.TestCase):
    """版本号一致性测试"""

    @staticmethod
    def _pyproject_version():
        """从 pyproject.toml 动态读取发布版本（v5.8.13 起为 dynamic attr，读唯一真值 core/version.py）"""
        import re
        from mindforge.core.version import __version__ as _truth
        pyproject = os.path.join(_PROJECT_ROOT, "pyproject.toml")
        if not os.path.exists(pyproject):
            return None
        with open(pyproject, "r", encoding="utf-8") as f:
            content = f.read()
        if re.search(r'attr\s*=\s*"mindforge\.core\.version\.__version__"', content):
            return _truth
        m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.M)
        return m.group(1) if m else None

    def test_version_matches_pyproject(self):
        """顶层包 __version__ 必须与唯一真值 core/version.py 一致"""
        from mindforge import __version__
        expected = self._pyproject_version()
        if expected is None:
            self.skipTest("pyproject.toml not found")
        self.assertEqual(__version__, expected)

    def test_version_semver_format(self):
        from mindforge import __version__
        parts = __version__.split(".")
        self.assertEqual(len(parts), 3)
        for p in parts:
            self.assertTrue(p.isdigit())

    def test_pyproject_version(self):
        """pyproject.toml 必须声明 dynamic version 并指向 core/version.py 的唯一真值"""
        expected = self._pyproject_version()
        self.assertIsNotNone(expected, "pyproject.toml 缺少 dynamic version attr 或静态版本")
        parts = expected.split(".")
        self.assertEqual(len(parts), 3)
        for p in parts:
            self.assertTrue(p.isdigit())

    def test_storage_has_version(self):
        """storage.py 的 __version__ 应与顶层包一致（v5.5.8 修复，v5.5.9 起动态比对）"""
        from mindforge import __version__ as top_version
        from mindforge.core.storage import __version__ as storage_version
        self.assertEqual(storage_version, top_version)


class TestMemoryDiff(unittest.TestCase):
    """memory_diff 版本对比测试"""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="mindforge_test_")
        cls.db_path = os.path.join(cls.tmpdir, "test.db")
        from mindforge import MindForge
        cls.mf = MindForge(db_path=cls.db_path, encrypted=False)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mf.close()
        except Exception:
            pass

    def setUp(self):
        """每条测试前添加一条记忆并保存两个版本"""
        self.entry = self.mf.add(
            "Hello World",
            category="test",
            tags=["tag1"],
        )
        # 保存版本 A
        result_a = self.mf.save_version(
            self.entry.id,
            content="Hello World",
            category="test",
            tags=["tag1"],
            importance="MEDIUM",
            actor="tester",
        )
        self.version_a_id = result_a["version_id"]

        # 保存版本 B（修改了内容、分类、标签、重要度）
        result_b = self.mf.save_version(
            self.entry.id,
            content="Hello World Updated",
            category="updated",
            tags=["tag1", "tag2"],
            importance="HIGH",
            actor="tester",
        )
        self.version_b_id = result_b["version_id"]

    def test_memory_diff_basic(self):
        """测试基本 diff 功能"""
        result = self.mf.memory_diff(self.version_a_id, self.version_b_id)
        self.assertNotIn("error", result)
        self.assertIn("version_a", result)
        self.assertIn("version_b", result)
        self.assertIn("changes", result)
        self.assertIn("changed_fields", result)

    def test_memory_diff_content_changed(self):
        """测试内容差异检测"""
        result = self.mf.memory_diff(self.version_a_id, self.version_b_id)
        changes = result["changes"]
        self.assertTrue(changes["content"]["changed"])
        self.assertIn("diff", changes["content"])

    def test_memory_diff_category_changed(self):
        """测试分类差异检测"""
        result = self.mf.memory_diff(self.version_a_id, self.version_b_id)
        changes = result["changes"]
        self.assertTrue(changes["category"]["changed"])
        self.assertEqual(changes["category"]["from"], "test")
        self.assertEqual(changes["category"]["to"], "updated")

    def test_memory_diff_tags_changed(self):
        """测试标签差异检测"""
        result = self.mf.memory_diff(self.version_a_id, self.version_b_id)
        changes = result["changes"]
        self.assertTrue(changes["tags"]["changed"])
        self.assertIn("tag2", changes["tags"]["added"])

    def test_memory_diff_importance_changed(self):
        """测试重要度差异检测"""
        result = self.mf.memory_diff(self.version_a_id, self.version_b_id)
        changes = result["changes"]
        self.assertTrue(changes["importance"]["changed"])
        self.assertEqual(changes["importance"]["from"], "MEDIUM")
        self.assertEqual(changes["importance"]["to"], "HIGH")

    def test_memory_diff_identical_versions(self):
        """测试相同版本对比"""
        result = self.mf.memory_diff(self.version_a_id, self.version_a_id)
        self.assertNotIn("error", result)
        self.assertEqual(result["changed_fields"], [])

    def test_memory_diff_nonexistent_version(self):
        """测试不存在的版本"""
        result = self.mf.memory_diff("nonexistent_v1", "nonexistent_v2")
        self.assertIn("error", result)

    def test_memory_diff_changed_fields_list(self):
        """测试变更字段列表"""
        result = self.mf.memory_diff(self.version_a_id, self.version_b_id)
        changed = result["changed_fields"]
        self.assertIn("content", changed)
        self.assertIn("category", changed)
        self.assertIn("tags", changed)
        self.assertIn("importance", changed)


class TestFalsyEnumFix(unittest.TestCase):
    """v5.5.8 修复：falsy 枚举值不应被默认值覆盖"""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="mindforge_test_")
        cls.db_path = os.path.join(cls.tmpdir, "test_enum.db")
        from mindforge import MindForge
        cls.mf = MindForge(db_path=cls.db_path, encrypted=False)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mf.close()
        except Exception:
            pass

    def test_add_with_explicit_none_privacy(self):
        """显式传入 None 不应崩溃"""
        entry = self.mf.add(
            "test content",
            category="test",
            privacy=None,
        )
        self.assertIsNotNone(entry)

    def test_add_preserves_explicit_enum(self):
        """显式传入的枚举值应该被保留"""
        from mindforge import Importance
        entry = self.mf.add(
            "test importance",
            category="test",
            importance=Importance.LOW,
        )
        self.assertEqual(entry.importance, Importance.LOW)


class TestMCPParamValidation(unittest.TestCase):
    """MCP 参数校验测试"""

    def test_require_args_missing(self):
        """测试 _require_args 检测缺失参数"""
        sys.path.insert(0, _PROJECT_ROOT)
        from mindforge.mcp.server import _require_args
        result = _require_args({}, "content")
        self.assertIsNotNone(result)
        self.assertIn("content", result["error"])

    def test_require_args_present(self):
        """测试 _require_args 通过"""
        from mindforge.mcp.server import _require_args
        result = _require_args({"content": "hello"}, "content")
        self.assertIsNone(result)

    def test_require_args_multiple(self):
        """测试多参数校验"""
        from mindforge.mcp.server import _require_args
        # 缺少一个
        result = _require_args({"agent_id": "x"}, "agent_id", "query")
        self.assertIsNotNone(result)
        self.assertIn("query", result["error"])
        # 都有
        result = _require_args({"agent_id": "x", "query": "q"}, "agent_id", "query")
        self.assertIsNone(result)

    def test_handle_tools_call_validates_params_type(self):
        """测试 params 类型校验"""
        from mindforge.mcp.server import _handle_tools_call
        # params 是 list 而非 dict
        with self.assertRaises(ValueError):
            _handle_tools_call(None, {"params": []})

    def test_memory_add_missing_content(self):
        """测试 memory_add 缺少 content 参数"""
        from mindforge.mcp.server import h_memory_add
        # 模拟一个空的 MindForge 实例
        result = h_memory_add(None, {})
        self.assertIn("error", result)
        self.assertIn("content", result["error"])

    def test_memory_search_missing_query(self):
        """测试 memory_search 缺少 query 参数"""
        from mindforge.mcp.server import h_memory_search
        result = h_memory_search(None, {})
        self.assertIn("error", result)
        self.assertIn("query", result["error"])

    def test_memory_context_missing_params(self):
        """测试 memory_context 缺少参数"""
        from mindforge.mcp.server import h_memory_context
        result = h_memory_context(None, {"agent_id": "x"})
        self.assertIn("error", result)
        self.assertIn("query", result["error"])

    def test_memory_diff_tool_registered(self):
        """测试 memory_diff 工具已注册"""
        from mindforge.mcp.server import HANDLERS, TOOL_SCHEMAS
        self.assertIn("memory_diff", HANDLERS)
        tool_names = [t["name"] for t in TOOL_SCHEMAS]
        self.assertIn("memory_diff", tool_names)

    def test_mcp_tool_count(self):
        """测试 MCP 工具数量 >= 30"""
        from mindforge.mcp.server import TOOL_SCHEMAS
        self.assertGreaterEqual(len(TOOL_SCHEMAS), 30)


class TestEncryptionValidation(unittest.TestCase):
    """加密引擎输入校验测试"""

    def test_encrypt_non_string_raises_security_error(self):
        from mindforge.core.encryption import SecurityError
        # 先创建一个可用的引擎
        try:
            from mindforge.core.encryption import EncryptionEngine
            engine, _ = EncryptionEngine.from_password("testpass")
            with self.assertRaises(SecurityError):
                engine.encrypt(123)  # type: ignore
            with self.assertRaises(SecurityError):
                engine.encrypt(None)  # type: ignore
        except SecurityError:
            # cryptography 库可能未安装
            self.skipTest("cryptography not available")

    def test_hash_non_string_raises_security_error(self):
        from mindforge.core.encryption import SecurityError
        try:
            from mindforge.core.encryption import EncryptionEngine
            engine, _ = EncryptionEngine.from_password("testpass")
            with self.assertRaises(SecurityError):
                engine.hash(123)  # type: ignore
        except SecurityError:
            self.skipTest("cryptography not available")

    def test_decrypt_non_blob_raises_security_error(self):
        from mindforge.core.encryption import SecurityError
        try:
            from mindforge.core.encryption import EncryptionEngine
            engine, _ = EncryptionEngine.from_password("testpass")
            with self.assertRaises(SecurityError):
                engine.decrypt("not a blob")  # type: ignore
        except SecurityError:
            self.skipTest("cryptography not available")


class TestCLIListFilteredCount(unittest.TestCase):
    """v5.5.8 修复：cmd_list 筛选后总数"""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="mindforge_test_")
        cls.db_path = os.path.join(cls.tmpdir, "test_list.db")
        from mindforge import MindForge
        cls.mf = MindForge(db_path=cls.db_path, encrypted=False)
        # 添加不同分类的记忆
        cls.mf.add("cat A item 1", category="catA")
        cls.mf.add("cat A item 2", category="catA")
        cls.mf.add("cat B item 1", category="catB")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mf.close()
        except Exception:
            pass

    def test_list_with_category_filter(self):
        """筛选分类后返回的条目数应正确"""
        entries = self.mf.list(category="catA", limit=100)
        self.assertEqual(len(entries), 2)

    def test_list_with_different_category(self):
        entries = self.mf.list(category="catB", limit=100)
        self.assertEqual(len(entries), 1)


if __name__ == "__main__":
    unittest.main()
