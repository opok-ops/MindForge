# -*- coding: utf-8 -*-
"""v6.6.3 契约：benchmarks/repro_benchmark.py 头部完整性守卫。

回归背景：该脚本入仓时（v6.5.12–v6.6.2 窗口）文件头部常量与 import 整块
缺失（DEFAULT_N / _REPO / MindForge / MemoryConfig / time / threading /
pathlib.Path / CONTENT_TMPL / QUERY_CN），任何人按文档命令直接运行都在
main() 处 NameError。本测试在 CI 里静态守卫全部必需名字，防再次回归。
"""
import ast
import unittest
from pathlib import Path

_BENCH = Path(__file__).resolve().parent.parent / "benchmarks" / "repro_benchmark.py"

_REQUIRED_NAMES = {
    # 常量（此前缺失即 NameError）
    "_REPO", "DEFAULT_N", "DEFAULT_ROUNDS", "DEFAULT_LATENCY_ROUNDS",
    "CONTENT_TMPL", "QUERY_CN",
}
_REQUIRED_IMPORTS = {"time", "threading"}
_REQUIRED_FROM_IMPORTS = {"pathlib": {"Path"}}


class TestReproBenchmarkHeader(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = _BENCH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.src)

    def test_all_required_names_defined(self):
        defined = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        defined.add(tgt.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
        missing = _REQUIRED_NAMES - defined
        self.assertEqual(missing, set(),
                         f"repro_benchmark.py 缺少必需定义：{missing}")

    def test_required_imports_present(self):
        imported = set()
        from_map = {}
        for node in self.tree.body:  # 顶层 import 即可
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                from_map.setdefault(node.module, set()).update(
                    a.name for a in node.names)
        missing = _REQUIRED_IMPORTS - imported
        self.assertEqual(missing, set(), f"缺少顶层 import：{missing}")
        for mod, names in _REQUIRED_FROM_IMPORTS.items():
            have = from_map.get(mod, set())
            self.assertTrue(
                names <= have,
                f"缺少 from {mod} import {names - have}")

    def test_mindforge_package_imported(self):
        self.assertIn("from mindforge import MindForge", self.src)
        self.assertIn("MemoryConfig", self.src)

    def test_script_compiles(self):
        compile(self.src, str(_BENCH), "exec")


if __name__ == "__main__":
    unittest.main()
