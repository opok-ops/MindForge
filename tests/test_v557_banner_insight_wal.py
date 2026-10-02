"""
MindForge v5.5.7 P2/P3 修复验证测试
===================================

验证：
- P2: agent-insight c() 三参数崩溃修复 + JSON 分支
- P3: banner 在 json_output 时跳过
- P3: WAL 网络文件系统检测与降级
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestP2AgentInsightFix(unittest.TestCase):
    """P2: agent-insight c() 三参数崩溃修复"""

    def test_c_function_accepts_two_args(self):
        """验证 c() 函数只接受 2 个参数"""
        from mindforge.cli.main import c
        result = c("test", "green")
        self.assertIn("test", result)

    def test_no_three_arg_c_calls(self):
        """验证代码中不存在 c() 三参数调用"""
        cli_path = Path(__file__).parent.parent / "mindforge" / "cli" / "main.py"
        content = cli_path.read_text(encoding="utf-8")
        import re
        matches = re.findall(r'c\(.*?,\s*"[^"]+",\s*"[^"]+"\s*\)', content)
        self.assertEqual(len(matches), 0, f"发现 c() 三参数调用: {matches}")

    def test_agent_insight_has_json_branch(self):
        """验证 agent-insight 命令有 JSON 输出分支"""
        import inspect
        from mindforge.cli.main import cmd_agent_insight
        source = inspect.getsource(cmd_agent_insight)
        self.assertIn('json_output', source)
        self.assertIn('_json_out', source)

    def test_agent_insight_json_parser_registered(self):
        """验证 agent-insight 子命令注册了 json_parser"""
        cli_path = Path(__file__).parent.parent / "mindforge" / "cli" / "main.py"
        content = cli_path.read_text(encoding="utf-8")
        # 找到 agent-insight 的 add_parser 行
        import re
        match = re.search(r'add_parser\("agent-insight".*\)', content)
        self.assertIsNotNone(match, "agent-insight 子命令未找到")
        self.assertIn('json_parser', match.group(), "agent-insight 未注册 json_parser")


class TestP3BannerSuppression(unittest.TestCase):
    """P3: banner 在 json_output 时跳过"""

    def test_print_banner_checks_json_mode(self):
        """验证 print_banner() 检查 _json_mode 标志"""
        import inspect
        from mindforge.cli.main import print_banner
        source = inspect.getsource(print_banner)
        self.assertIn('_json_mode', source)

    def test_experimental_banner_checks_json_mode(self):
        """验证 _print_experimental_banner 检查 _json_mode"""
        import inspect
        from mindforge.cli.main import _print_experimental_banner
        source = inspect.getsource(_print_experimental_banner)
        self.assertIn('_json_mode', source)

    def test_json_mode_flag_exists(self):
        """验证 _json_mode 模块级标志存在"""
        from mindforge.cli import main as cli_main
        self.assertTrue(hasattr(cli_main, '_json_mode'))

    def test_json_mode_set_in_main(self):
        """验证 main() 函数中设置了 _json_mode"""
        import inspect
        from mindforge.cli.main import main
        source = inspect.getsource(main)
        self.assertIn('_json_mode', source)
        self.assertIn('json_output', source)

    def test_banner_suppressed_when_json(self):
        """验证 _json_mode=True 时 banner 不输出到 stdout"""
        import io
        from contextlib import redirect_stdout
        import mindforge.cli.main as cli_main

        # 设置 _json_mode = True
        original = cli_main._json_mode
        cli_main._json_mode = True
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_main.print_banner()
            output = buf.getvalue()
            self.assertEqual(output, "", f"JSON 模式下不应输出 banner，实际输出: {output[:50]}")
        finally:
            cli_main._json_mode = original

    def test_banner_shown_when_not_json(self):
        """验证 _json_mode=False 时 banner 正常输出"""
        import io
        from contextlib import redirect_stdout
        import mindforge.cli.main as cli_main

        original = cli_main._json_mode
        cli_main._json_mode = False
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_main.print_banner()
            output = buf.getvalue()
            self.assertIn("MindForge", output)
        finally:
            cli_main._json_mode = original


class TestP3WALDetection(unittest.TestCase):
    """P3: WAL 网络文件系统检测与降级"""

    def test_is_network_fs_function_exists(self):
        """验证 _is_network_fs 函数存在"""
        from mindforge.core.storage import _is_network_fs
        self.assertTrue(callable(_is_network_fs))

    def test_local_path_not_network(self):
        """验证本地路径不被检测为网络文件系统"""
        from mindforge.core.storage import _is_network_fs
        local_path = Path(tempfile.gettempdir()) / "test_local.db"
        self.assertFalse(_is_network_fs(local_path))

    def test_wal_detection_in_get_conn(self):
        """验证 _get_conn 方法包含网络 FS 检测逻辑"""
        import inspect
        from mindforge.core.storage import StorageEngine
        source = inspect.getsource(StorageEngine._get_conn)
        self.assertIn('_is_network_fs', source)
        self.assertIn('journal_mode=DELETE', source)
        self.assertIn('journal_mode=WAL', source)

    def test_storage_uses_correct_journal_mode(self):
        """验证在本地盘上使用 WAL 模式"""
        tmp_dir = tempfile.mkdtemp(prefix="mf_wal_")
        db_path = os.path.join(tmp_dir, "test.db")
        try:
            from mindforge.core.storage import StorageEngine
            storage = StorageEngine(db_path=db_path, encrypted=False)
            conn = storage._get_conn()
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
            storage.close()
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
