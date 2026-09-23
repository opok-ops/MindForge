# -*- coding: utf-8 -*-
"""v5.6.8 缺陷修复回归测试

覆盖本轮公开审查挖出的「会在运行期直接崩溃 / 静默失效」的缺陷：

  P0-01 cli/main.py 模块级缺 ``import os`` → ``init`` 加密模式必然 NameError；
  P0-02 cmd_rekey 三重崩溃：越界相对导入 ImportError + 调用未定义的
        ``_build_config`` NameError + 函数体内 ``import os`` 导致的
        UnboundLocalError（``os.environ.get`` 先于局部导入执行）；
  P0-03 cmd_backup_restore 越界相对导入 ImportError（命令完全不可用）；
  P0-04 core/mindforge.py ``restore_backup`` 非 Windows 分支缺 ``import os``，
        ``os.chmod`` 必崩且密钥文件权限不会收紧；
  P1-01 adapters/ 三个适配器越界相对导入（无 try/except 兜底，导入即失败）；
  P1-02 模块级重复定义 ``cmd_backup``（旧简版覆盖 v5.6.0 新版）；
  P1-03 ``_main_dispatch`` 分发表重复键（"restore" / "backup"）；
  P2-01 版本号漂移：event_bus payload/UA 硬编码 5.4.9、export-json 写死 5.1.5、
        openclaw 适配器写死 5.0.1；
  P2-02 modules/hybrid_search.py ``s if chunk.isupper() else s`` 恒等分支，
        全大写原词的替换同义词不会大写（大小写保留逻辑形同虚设）；
  P3-01 cmd_agent_purge ``"bold" if not dry_run else "bold"`` 恒等三元。

其中 P0/P1 能用真实子进程端到端复现，这里就以子进程方式跑真命令，
避免再退回「只做静态扫描、漏掉运行期崩溃」的老路。
"""

import ast
import io
import os
import re
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path

import core.version as core_version


_REPO = Path(__file__).resolve().parent.parent
_EXPECTED_VERSION = "5.7.7"

# 本轮修复涉及的、必须与 core/version.py 保持一致的源码文件
_VERSION_TRUTH_FILES = (
    "core/version.py",
    "pyproject.toml",
)


# --------------------------------------------------------------------------
# 辅助：取出「去掉注释与 docstring」后的源码，保留行号
#   注释/docstring 里会引用被修掉的坏写法（用于说明修复原因），
#   直接对原文做子串断言会误报，所以统一走这个工具。
# --------------------------------------------------------------------------
def _code_text(rel_path):
    path = _REPO / rel_path
    src = path.read_text(encoding="utf-8")

    # 1) 用 tokenize 抹掉注释（保留行号）
    lines = src.splitlines()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        toks = []
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            if 1 <= row <= len(lines):
                lines[row - 1] = lines[row - 1][:col]

    # 2) 用 ast 找出模块/类/函数 docstring 的行区间并清空
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            end = first.value.end_lineno or first.value.lineno
            for row in range(first.value.lineno, end + 1):
                lines[row - 1] = ""

    return "\n".join(lines)


def _run_cli(args, env_extra=None, cwd=None):
    """以真实子进程跑 CLI，返回 (returncode, stdout+stderr)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-m", "cli.main"] + list(args),
        cwd=str(cwd or _REPO),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# --------------------------------------------------------------------------
# P2-01 / 版本号
# --------------------------------------------------------------------------
class TestVersionTruth(unittest.TestCase):
    def test_version_is_5_6_8(self):
        self.assertEqual(core_version.__version__, _EXPECTED_VERSION)
        self.assertEqual(
            tuple(core_version.VERSION_INFO),
            tuple(int(p) for p in _EXPECTED_VERSION.split(".")),
        )

    def test_pyproject_matches_truth(self):
        text = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', text, re.M)
        self.assertIsNotNone(m, "pyproject.toml 应有 project.version")
        self.assertEqual(m.group(1), core_version.__version__)

    def test_cli_and_mcp_fallback_match_truth(self):
        """claude/mcp 的最后兜底 __version__ 不得落后于真值。"""
        for rel in ("cli/main.py", "mcp/server.py"):
            text = (_REPO / rel).read_text(encoding="utf-8")
            lits = re.findall(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
            self.assertTrue(lits, f"{rel} 应含兜底 __version__ 字面量")
            for lit in lits:
                self.assertEqual(lit, core_version.__version__, f"{rel} 版本漂移")

    def test_version_payloads_reference_truth_source(self):
        """P2-01：导出/上报 payload 中的版本字段必须引用真值，不得硬编码。

        注意：只校验 ``"version": ...`` 这类真值字段；源码里形如
        ``help="导出记忆为 JSON（v5.1.5 新增）"`` 的历史特性标注不是版本真值，
        不在校验范围内。
        """
        cases = {
            "modules/event_bus.py": ('"version": _MF_VERSION', '"5.4.9"'),
            "adapters/openclaw_adapter.py": ('"version": _MF_VERSION', '"5.0.1"'),
            "cli/main.py": ('"version": __version__', '"5.1.5"'),
        }
        for rel, (must, stale) in cases.items():
            code = _code_text(rel)
            self.assertIn(must, code, f"{rel} 的 version payload 未引用真值（P2-01）")
            self.assertNotIn(stale, code, f"{rel} 的 version payload 仍硬编码 {stale}")

    def test_event_bus_user_agent_references_truth(self):
        code = _code_text("modules/event_bus.py")
        self.assertIn("MindForge/{_MF_VERSION}", code, "event_bus UA 未引用真值")
        self.assertNotIn("MindForge/5.4", code, "event_bus UA 仍硬编码 5.4")

    def test_url_importer_user_agent_references_truth(self):
        code = _code_text("cli/main.py")
        self.assertIn("MindForge/{__version__} URL Importer", code)
        self.assertNotIn('"MindForge/5.4 URL Importer"', code)

    def test_event_bus_uses_version_truth(self):
        import modules.event_bus as event_bus

        self.assertTrue(hasattr(event_bus, "_MF_VERSION"), "event_bus 应导入版本真值")
        self.assertEqual(event_bus._MF_VERSION, core_version.__version__)

    def test_openclaw_adapter_uses_version_truth(self):
        import adapters.openclaw_adapter as openclaw

        self.assertEqual(
            getattr(openclaw, "_MF_VERSION", None), core_version.__version__
        )


# --------------------------------------------------------------------------
# P0-02 / P0-04 / P1-02 / P1-03 静态结构校验
# --------------------------------------------------------------------------
class TestStaticStructure(unittest.TestCase):
    def test_cli_has_module_level_import_os(self):
        """P0-01：init 加密分支在模块级用 os.environ，缺模块级 import 必崩。"""
        src = (_REPO / "cli/main.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        has_top_import = any(
            isinstance(node, ast.Import)
            and any(alias.name == "os" for alias in node.names)
            for node in tree.body
        )
        self.assertTrue(has_top_import, "cli/main.py 顶层应有 import os（P0-01）")

    def test_no_beyond_toplevel_relative_imports(self):
        """P0-02/P0-03/P1-01：`core`/`modules`/`adapters`/`cli` 都已是顶层包，
        `from ..x` 必抛 ``ImportError: attempted relative import beyond top-level package``。

        早期版本只扫 4 个显式列出的文件，因此漏掉了 ``core/mindforge.py`` 里
        10 处「`try: from ..modules.*` → `except ImportError: from modules.*`」。
        那种写法虽被 except 兜住、功能不受影响，但每次首次访问都要靠抛异常走兜底，
        且会掩盖内部真实的 ImportError。v5.6.8 收尾一并折叠为绝对导入，
        这里改成**全仓库 AST 扫描**（按语法树判定 level >= 2，避免把注释/字符串里的
        `from ..` 误判），防止同类写法再次混入。
        """
        offenders = []
        for path in sorted(_REPO.rglob("*.py")):
            parts = path.parts
            if any(x in parts for x in (".git", "__pycache__", ".venv",
                                        ".pytest_cache", "node_modules",
                                        "MindForge.egg-info")):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover
                self.fail(f"{path} 无法解析：{exc}")
            rel = path.relative_to(_REPO)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level >= 2:
                    offenders.append(f"{rel}:{node.lineno}  from ..{node.module or ''}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith(".."):
                            offenders.append(f"{rel}:{node.lineno}  import {alias.name}")
        self.assertEqual(
            offenders,
            [],
            "存在越界相对导入（顶层包内必然 ImportError）：\n" + "\n".join(offenders),
        )

    def test_no_undefined_build_config_call(self):
        """P0-02：cmd_rekey 曾调用从未定义的 _build_config。"""
        code = _code_text("cli/main.py")
        self.assertNotIn("_build_config", code)

    def test_rekey_imports_encryption_absolutely(self):
        code = _code_text("cli/main.py")
        self.assertIn("from core.encryption import", code)

    def test_single_module_level_cmd_backup(self):
        """P1-02：旧简版 cmd_backup 不得再覆盖 v5.6.0 版本。"""
        tree = ast.parse((_REPO / "cli/main.py").read_text(encoding="utf-8"))
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertEqual(
            names.count("cmd_backup"), 1, "cli/main.py 应有且仅有一个模块级 cmd_backup"
        )

    def test_dispatch_table_has_no_duplicate_keys(self):
        """P1-03：commands 字典出现重复键会让前者被静默丢弃。"""
        tree = ast.parse((_REPO / "cli/main.py").read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name != "_main_dispatch":
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    keys = [
                        k.value
                        for k in sub.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    ]
                    if keys:
                        found.append(keys)
                    break
        self.assertTrue(found, "应能在 _main_dispatch 中找到 commands 字典")
        keys = found[0]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(dupes, [], f"分发表存在重复命令键：{dupes}")

    def test_dispatch_entry_commands_are_defined(self):
        """分发表的每个 value 都必须是模块内真实存在的函数名。"""
        tree = ast.parse((_REPO / "cli/main.py").read_text(encoding="utf-8"))
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_main_dispatch":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Dict):
                        for v in sub.values:
                            if isinstance(v, ast.Name):
                                self.assertIn(
                                    v.id,
                                    defined,
                                    f"分发表引用了未定义的命令函数 {v.id}",
                                )
                        break
                break

    def test_restore_backup_has_local_import_os(self):
        """P0-04：restore_backup 的 POSIX 分支调用 os.chmod，需可用的 os。"""
        src = (_REPO / "core/mindforge.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "restore_backup":
                target = node
                break
        self.assertIsNotNone(target, "core/mindforge.py 应有 restore_backup")
        uses_os_chmod = any(
            isinstance(n, ast.Attribute) and n.attr == "chmod" for n in ast.walk(target)
        )
        self.assertTrue(uses_os_chmod, "restore_backup 应调用 os.chmod（权限收紧）")
        module_has_os = any(
            isinstance(n, ast.Import) and any(a.name == "os" for a in n.names)
            for n in tree.body
        )
        local_has_os = any(
            isinstance(n, ast.Import) and any(a.name == "os" for a in n.names)
            for n in ast.walk(target)
        )
        self.assertTrue(
            module_has_os or local_has_os,
            "restore_backup 使用 os.chmod，但作用域内没有 import os（P0-04）",
        )

    def test_hybrid_search_uppercase_branch_not_identity(self):
        """P2-02：`s if chunk.isupper() else s` 恒等，修复后应为 s.upper()。"""
        code = _code_text("modules/hybrid_search.py")
        self.assertNotIn("s if chunk.isupper() else s", code)
        self.assertIn("s.upper() if chunk.isupper() else s", code)


# --------------------------------------------------------------------------
# P1-01 适配器可导入
# --------------------------------------------------------------------------
class TestAdaptersImportable(unittest.TestCase):
    def test_adapters_import_without_relative_import_error(self):
        import importlib

        for mod in (
            "adapters.claude_adapter",
            "adapters.generic_api",
            "adapters.openclaw_adapter",
        ):
            try:
                importlib.import_module(mod)
            except ImportError as exc:  # pragma: no cover - 失败即回归
                self.fail(f"{mod} 导入失败（P1-01）：{exc}")


# --------------------------------------------------------------------------
# P2-02 行为验证
# --------------------------------------------------------------------------
class TestQueryExpanderCasePreservation(unittest.TestCase):
    def test_uppercase_token_gets_uppercase_synonym(self):
        from modules.hybrid_search import QueryExpander

        expander = QueryExpander(
            synonyms={"alpha": ["beta"]},
            abbr={},
            hyponyms={},
            max_rewrites=10,
            enable_typo=False,
        )
        result = expander.expand("ALPHA", hyponym=False, typo=False)
        self.assertIn("BETA", result.rewrites, f"大小写未保留：{result.rewrites}")
        self.assertNotIn("beta", result.rewrites, f"全大写原词被降为小写：{result.rewrites}")

    def test_lowercase_token_gets_lowercase_synonym(self):
        from modules.hybrid_search import QueryExpander

        expander = QueryExpander(
            synonyms={"alpha": ["beta"]},
            abbr={},
            hyponyms={},
            max_rewrites=10,
            enable_typo=False,
        )
        result = expander.expand("alpha", hyponym=False, typo=False)
        self.assertIn("beta", result.rewrites, f"小写原词应得小写同义词：{result.rewrites}")


# --------------------------------------------------------------------------
# P0-01 / P0-02 / P0-03 端到端子进程复现
# --------------------------------------------------------------------------
class TestCliEndToEnd(unittest.TestCase):
    """真跑 CLI 子进程——这些命令此前 100% 崩溃，静态扫描抓不到。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mf_v568_")
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "mf.db"
        self.key = self.tmp / ".key"
        self.password = "V568-regression-pass"
        self.new_password = "V568-rotated-pass"

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self, password=None):
        env = {}
        if password is not None:
            env["MINDFORGE_PASSWORD"] = password
        return env

    # ---- init -----------------------------------------------------------
    def test_init_no_encrypt_succeeds(self):
        rc, out = _run_cli(
            ["--db-path", str(self.db), "--key-file", str(self.key), "init", "--no-encrypt"],
            env_extra=self._env(),
        )
        self.assertEqual(rc, 0, f"init --no-encrypt 失败：\n{out}")
        self.assertTrue(self.db.exists(), "数据库文件未创建")

    def test_init_encrypted_does_not_raise_nameerror(self):
        """P0-01：加密模式此前必然 NameError: name 'os' is not defined。"""
        rc, out = _run_cli(
            ["--db-path", str(self.db), "--key-file", str(self.key), "init"],
            env_extra=self._env(self.password),
        )
        self.assertNotIn("NameError", out, f"init 仍抛 NameError：\n{out}")
        self.assertNotIn("os' is not defined", out)
        self.assertEqual(rc, 0, f"init（加密）失败：\n{out}")
        self.assertTrue(self.key.exists(), "加密密钥文件未生成（P0-01）")
        self.assertTrue(self.db.exists(), "数据库文件未生成")

    # ---- rekey ----------------------------------------------------------
    def _init_encrypted(self):
        rc, out = _run_cli(
            ["--db-path", str(self.db), "--key-file", str(self.key), "init"],
            env_extra=self._env(self.password),
        )
        self.assertEqual(rc, 0, f"前置 init 失败：\n{out}")

    def test_rekey_upgrade_only_succeeds(self):
        """P0-02：此前 ImportError / NameError / UnboundLocalError 三连崩。"""
        self._init_encrypted()
        rc, out = _run_cli(
            [
                "--db-path", str(self.db),
                "--key-file", str(self.key),
                "rekey", "--upgrade-only", "--yes",
            ],
            env_extra={
                "MINDFORGE_PASSWORD": self.password,
                "MINDFORGE_OLD_PASSWORD": self.password,
            },
        )
        for bad in ("ImportError", "NameError", "UnboundLocalError", "Traceback"):
            self.assertNotIn(bad, out, f"rekey 仍崩（{bad}）：\n{out}")
        self.assertEqual(rc, 0, f"rekey --upgrade-only 失败：\n{out}")

    def test_rekey_password_rotation_succeeds(self):
        self._init_encrypted()
        rc, out = _run_cli(
            [
                "--db-path", str(self.db),
                "--key-file", str(self.key),
                "rekey", "--yes",
            ],
            env_extra={
                "MINDFORGE_OLD_PASSWORD": self.password,
                "MINDFORGE_NEW_PASSWORD": self.new_password,
            },
        )
        for bad in ("ImportError", "NameError", "UnboundLocalError", "Traceback"):
            self.assertNotIn(bad, out, f"rekey 换密仍崩（{bad}）：\n{out}")
        self.assertEqual(rc, 0, f"rekey 换密失败：\n{out}")

    def test_rekey_on_unencrypted_db_exits_cleanly(self):
        rc, out = _run_cli(
            ["--db-path", str(self.db), "--key-file", str(self.key), "init", "--no-encrypt"],
        )
        self.assertEqual(rc, 0, f"前置 init 失败：\n{out}")
        rc2, out2 = _run_cli(
            ["--db-path", str(self.db), "--key-file", str(self.key), "rekey", "--yes"],
        )
        self.assertNotIn("Traceback", out2, f"rekey 未加密库时崩溃：\n{out2}")
        self.assertEqual(rc2, 1, "未加密库应给出明确错误并以 1 退出")

    # ---- backup / backup-restore ---------------------------------------
    def test_backup_and_restore_roundtrip(self):
        """P0-03：backup-restore 此前 ImportError，命令完全不可用。

        临时目录在仓库根之外，backup() 默认会拒绝打包外部 key_file
        （v5.6.x 的防 config 注入守卫），这里按官方开关显式放行。
        """
        self._init_encrypted()
        zip_path = self.tmp / "snapshot.zip"

        rc, out = _run_cli(
            [
                "--db-path", str(self.db),
                "--key-file", str(self.key),
                "backup", "--output", str(zip_path),
            ],
            env_extra={
                "MINDFORGE_PASSWORD": self.password,
                "MINDFORGE_ALLOW_EXTERNAL_KEY_FILE": "1",
            },
        )
        self.assertEqual(rc, 0, f"backup 失败：\n{out}")
        self.assertTrue(zip_path.exists(), "备份 ZIP 未生成")

        restore_db = self.tmp / "restored.db"
        restore_key = self.tmp / "restored.key"
        rc2, out2 = _run_cli(
            [
                "backup-restore", str(zip_path),
                "--db-path", str(restore_db),
                "--key-file", str(restore_key),
                "--force", "--yes",
            ],
            env_extra={
                "MINDFORGE_PASSWORD": self.password,
                "MINDFORGE_ALLOW_EXTERNAL_KEY_FILE": "1",
            },
        )
        for bad in ("ImportError", "Traceback"):
            self.assertNotIn(bad, out2, f"backup-restore 仍崩（{bad}）：\n{out2}")
        self.assertEqual(rc2, 0, f"backup-restore 失败：\n{out2}")
        self.assertTrue(restore_db.exists(), "恢复后的数据库未生成")

    def test_backup_restore_missing_file_is_graceful(self):
        rc, out = _run_cli(
            ["backup-restore", str(self.tmp / "nope.zip"), "--db-path", str(self.db), "--yes"],
        )
        self.assertNotIn("Traceback", out, f"缺失备份文件应优雅报错：\n{out}")
        self.assertEqual(rc, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
