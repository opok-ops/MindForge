"""
MindForge v6.3.6 回归测试

覆盖测试过程中实测发现的 5 个缺陷（E2E：dsh-mindforge v3.0 桥接
真实 MindForge CLI，2026-11-07）：

  - Bug-A（P0）: MemoryCompressor 索引未水合——真实库上
    IndexEngine._doc_texts 恒为空，find_similar_clusters 静默返回
    []，compress 恒为 0 簇。修复：auto_compress 空索引时从
    storage 现场加载明文记忆并 index_memory。
  - Bug-B（P0）: compress / compress-list / decompress /
    adaptive-search 四个 CLI handler 无视 --json（parser 挂了
    json_parser 但 handler 只打人类格式）。
  - Bug-C（P1）: adaptive_search 把 session_id 透传给门面
    search 的隐私访问控制——INTERNAL 记忆（写入默认级别）对
    非空 session 精确匹配 source_session，adaptive_search 恒空。
  - Bug-D（P2）: _get_memory 对 key_file="" 的 Path('') 解析为
    cwd（恒存在），明文库被误判为加密库索要密码。
"""

import os
import sys
import tempfile
import argparse
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindforge.modules.memory_compressor import MemoryCompressor


def _make_index_stub():
    """最小 IndexEngine 替身：仅实现水合路径依赖的表面"""
    stub = MagicMock()
    stub._doc_texts = {}          # 空——触发水合
    stub._fitted = True
    return stub


def _make_storage_stub(entries):
    storage = MagicMock()
    storage.encrypted = False
    storage.list_memories.return_value = entries
    return storage


def _entry(mid, content, category="general", privacy=None, metadata=None):
    e = MagicMock()
    e.id = mid
    e.memory_id = mid
    e.content = content
    e.category = category
    e.privacy = privacy
    e.metadata = metadata
    return e


class TestBugAHydrateIndex(unittest.TestCase):
    """Bug-A：空索引时 auto_compress 从 storage 水合候选"""

    def test_hydrate_populates_empty_index(self):
        idx = _make_index_stub()
        entries = [
            _entry("m1", "用户喜欢 Rust 系统编程"),
            _entry("m2", "用户偏好 Rust 语言开发"),
            _entry("m3", "用户爱用 Rust 写系统级程序"),
            _entry("m4", "Python 是用户的主力脚本语言"),
        ]
        storage = _make_storage_stub(entries)
        comp = MemoryCompressor(storage, idx)

        count = comp._hydrate_index_from_storage()

        self.assertEqual(count, 4)
        # index_memory 逐条调用（stub 的 _doc_texts 不会自动填充）
        self.assertEqual(idx.index_memory.call_count, 4)

    def test_hydrate_excludes_trash_compressed_strict(self):
        """trash / 已压缩源 / STRICT 不进入候选"""
        idx = _make_index_stub()
        strict_priv = MagicMock()
        strict_priv.value = "STRICT"
        entries = [
            _entry("t1", "垃圾", category="trash"),
            _entry("c1", "已压缩源", metadata={"compressed_into": "cluster-x"}),
            _entry("s1", "严格隐私", privacy=strict_priv),
            _entry("ok", "正常记忆"),
        ]
        storage = _make_storage_stub(entries)
        comp = MemoryCompressor(storage, idx)

        count = comp._hydrate_index_from_storage()

        self.assertEqual(count, 1)
        idx.index_memory.assert_called_once_with("ok", "正常记忆")

    def test_auto_compress_triggers_hydration_once(self):
        """auto_compress 在空索引时先水合再聚类"""
        idx = _make_index_stub()
        entries = [_entry(f"m{i}", f"文本{i}") for i in range(3)]
        storage = _make_storage_stub(entries)
        comp = MemoryCompressor(storage, idx)

        comp._hydrate_index_from_storage = MagicMock(return_value=3)
        comp.find_similar_clusters = MagicMock(return_value=[["m0", "m1"]])
        # auto_compress 内部还调 generate_summary / _write_compression，
        # 对 dry_run=True 只需管线走通
        comp.generate_summary = MagicMock(return_value="摘要")
        comp._now_iso = MagicMock(return_value="2026-11-07T00:00:00")
        comp._majority_category = MagicMock(return_value="general")
        comp._max_importance = MagicMock(return_value="MEDIUM")

        result = comp.auto_compress(dry_run=True, min_cluster_size=2)

        comp._hydrate_index_from_storage.assert_called_once()
        self.assertEqual(result["compressed_count"], 1)


class TestBugCAdaptiveSessionLeak(unittest.TestCase):
    """Bug-C：adaptive_search 不得把 session_id 泄漏给门面 search 的访问控制"""

    def test_adaptive_search_omits_session_id_in_search_call(self):
        """源码级契约：self.search(...) 调用不携带 session_id 关键字"""
        import inspect
        from mindforge.core.mindforge import MindForge

        src = inspect.getsource(MindForge.adaptive_search)
        call_block = src[src.find("result = self.search("):src.find("engine.record_search")]
        self.assertIn("min_relevance=min_rel", call_block)
        self.assertNotIn("session_id=session_id", call_block)
        # 统计维度必须保留
        self.assertIn("record_search(session_id", src)


class TestBugDKeyFileBoundary(unittest.TestCase):
    """Bug-D：key_file 空字符串不再误判为加密库"""

    def test_empty_key_file_is_not_encrypted(self):
        """key_file='' 时 Path('')=cwd 恒存在，不得触发加密分支"""
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "plain.db")
            # 建一个空明文库目录环境
            os.makedirs(os.path.join(td, "data"), exist_ok=True)
            from mindforge.core.mindforge import MindForge

            # 不真正初始化引擎，只验证 _get_memory 的加密判定
            from mindforge.cli import main as cli_main
            args = argparse.Namespace(db_path=db, key_file="")
            # _get_memory 会构造 MindForge；空 key_file 应被当明文处理，
            # 不会进入"加密数据库需要密码"错误分支（构造允许失败于其他
            # 原因，但不得出现密码提示错误）
            try:
                cli_main._get_memory(args)
                err = ""
            except Exception as e:
                err = str(e)
            self.assertNotIn("密码", err)
            self.assertNotIn("password", err.lower())

    def test_key_file_none_is_not_encrypted(self):
        """key_file=None 同样安全（库调用方路径）"""
        from mindforge.cli import main as cli_main
        args = argparse.Namespace(db_path="/nonexistent/xx.db", key_file=None)
        try:
            cli_main._get_memory(args)
            err = ""
        except Exception as e:
            err = str(e)
        self.assertNotIn("密码", err)
        self.assertNotIn("password", err.lower())


class TestBugBJsonOutput(unittest.TestCase):
    """Bug-B：四个 v6.3.5 命令的 --json 输出契约（源码级）"""

    def _assert_json_branch(self, func_name):
        from mindforge.cli import main as cli_main
        import inspect
        src = inspect.getsource(getattr(cli_main, func_name))
        self.assertIn('getattr(args, "json_output", False)', src,
                      f"{func_name} 缺少 --json 分支")
        self.assertIn("_json_out", src, f"{func_name} 未调用 _json_out")

    def test_compress_json_branch(self):
        self._assert_json_branch("cmd_compress")

    def test_compress_list_json_branch(self):
        self._assert_json_branch("cmd_compress_list")

    def test_decompress_json_branch(self):
        self._assert_json_branch("cmd_decompress")

    def test_adaptive_search_json_branch(self):
        self._assert_json_branch("cmd_adaptive_search")


if __name__ == "__main__":
    unittest.main()
