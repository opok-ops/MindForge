# -*- coding: utf-8 -*-
"""v5.6.7 安全与健壮性加固回归测试

覆盖本轮公开审查发现（P1×2 / P2×5 / P3×4）中可单元测试的行为：

  - P1-01 版本号兜底不再漂移（cli/mcp 回退值与 core/version.py 一致，且无 5.6.1 残留）；
  - P2-04 api._sanitize_tag：控制字符 / 空白 / 超长 / 非字符串标签归一化；
  - P3-03 api._log_unhandled_api_error：常规日志只记异常类型名，不含 traceback；
  - P3-07 mcp._safe_error_msg：错误消息抹掉绝对路径并截断；
  - P2-13 HardwareProfiler._detect_disk_type：多线程下结果一致（双检锁定）；
  - P3-05 / P3-10 federated：并发入/出队不崩溃，空内容 accept_incoming 记日志返回 None。

P2-02（/api/health 最小化）与 P2-09（MCP fail-closed 开关）依赖进程 / 网络环境，
由既有的 REST API 冒烟与手动核验覆盖，这里不重复起服务以免引入不稳定。
"""

import logging
import os
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import core.version as core_version


_REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# P1-01 版本号兜底同步
# --------------------------------------------------------------------------
class TestVersionFallbackSync(unittest.TestCase):
    """P1-01：最后兜底 __version__ 不得落后于 core/version.py 真值。"""

    def _fallback_literal(self, rel_path):
        text = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        # 匹配兜底分支里的 `__version__ = "x.y.z"`（位于 except 之后）
        matches = re.findall(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
        return matches

    def test_cli_and_mcp_fallback_match_truth(self):
        truth = core_version.__version__
        for rel in ("cli/main.py", "mcp/server.py"):
            literals = self._fallback_literal(rel)
            self.assertTrue(literals, f"{rel} 应含兜底 __version__ 字面量")
            for lit in literals:
                self.assertEqual(
                    lit, truth,
                    f"{rel} 的兜底版本 {lit} 与真值 {truth} 漂移（P1-01）")

    def test_no_stale_5_6_1_fallback(self):
        for rel in ("cli/main.py", "mcp/server.py"):
            literals = self._fallback_literal(rel)
            self.assertNotIn("5.6.1", literals, f"{rel} 仍残留 5.6.1 兜底（P1-01）")


# --------------------------------------------------------------------------
# P2-04 /api/tags 标签消毒
# --------------------------------------------------------------------------
class TestSanitizeTag(unittest.TestCase):
    def setUp(self):
        from api.server import _sanitize_tag
        self.sanitize = _sanitize_tag

    def test_strips_whitespace(self):
        self.assertEqual(self.sanitize("  spaced  "), "spaced")

    def test_removes_control_characters(self):
        self.assertEqual(self.sanitize("a\x00b\x1fc\x7fd"), "abcd")

    def test_truncates_oversized(self):
        self.assertEqual(len(self.sanitize("x" * 500)), 64)

    def test_coerces_non_string(self):
        self.assertEqual(self.sanitize(12345), "12345")
        self.assertEqual(self.sanitize(None), "None")

    def test_empty_after_sanitize(self):
        self.assertEqual(self.sanitize("   "), "")


# --------------------------------------------------------------------------
# P3-03 未捕获异常日志不写 traceback
# --------------------------------------------------------------------------
class TestLogUnhandledApiError(unittest.TestCase):
    def test_error_level_omits_traceback(self):
        from api.server import _log_unhandled_api_error
        with self.assertLogs("api.server", level="ERROR") as cm:
            try:
                raise ValueError("boom at C:\\Users\\secret\\db.sqlite")
            except ValueError as e:
                _log_unhandled_api_error(e)
        joined = "\n".join(cm.output)
        # 常规级别只应有异常类型名，不应含真实回溯路径细节
        self.assertIn("ValueError", joined)
        self.assertNotIn("Traceback", joined)
        self.assertNotIn("db.sqlite", joined)


# --------------------------------------------------------------------------
# P3-07 MCP 错误消息脱敏
# --------------------------------------------------------------------------
class TestSafeErrorMsg(unittest.TestCase):
    def setUp(self):
        from mcp.server import _safe_error_msg
        self.safe = _safe_error_msg

    def test_redacts_windows_and_posix_paths(self):
        msg = self.safe(ValueError(
            "cannot open C:\\Users\\SMDS\\secret\\db.sqlite or /home/op/.mf/data.db"))
        self.assertNotIn("SMDS", msg)
        self.assertNotIn("db.sqlite", msg)
        self.assertNotIn("data.db", msg)
        self.assertIn("<path>", msg)

    def test_truncates_to_200(self):
        msg = self.safe(ValueError("y" * 1000))
        self.assertLessEqual(len(msg), 200)

    def test_keeps_plain_validation_text(self):
        msg = self.safe(ValueError("content cannot be empty"))
        self.assertEqual(msg, "content cannot be empty")


# --------------------------------------------------------------------------
# P2-13 磁盘类型探测双检锁定
# --------------------------------------------------------------------------
class TestDetectDiskTypeRace(unittest.TestCase):
    def test_concurrent_results_consistent(self):
        from core.storage import HardwareProfiler
        HardwareProfiler._cached_disk_type = None  # 强制首次测量
        results = []
        lock = threading.Lock()

        def worker():
            r = HardwareProfiler._detect_disk_type()
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 8)
        self.assertEqual(len(set(results)), 1, "并发下探测结果应一致且被缓存")
        self.assertIsNotNone(HardwareProfiler._cached_disk_type)
        self.assertEqual(results[0], HardwareProfiler._cached_disk_type)


# --------------------------------------------------------------------------
# P3-05 / P3-10 联邦队列锁与空内容处理
# --------------------------------------------------------------------------
class TestFederatedQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v567_")
        self.db = os.path.join(self.tmp, "fed.db")
        from core.storage import StorageEngine
        from modules.federated import FederatedMemory
        self.storage = StorageEngine(db_path=self.db)
        self.fed = FederatedMemory(storage=self.storage, local_peer_id="local",
                                   config={"allow_unsigned_peers": True})
        self.fed.register_peer("peerA", "节点A", trust_level=0.9)

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_accept_incoming_empty_content_returns_none_and_logs(self):
        ok = self.fed.receive_memory("peerA", {"content": "   "})
        self.assertTrue(ok)  # 入队成功
        self.assertEqual(len(self.fed._incoming_queue), 1)
        with self.assertLogs("modules.federated", level="WARNING"):
            result = self.fed.accept_incoming()
        self.assertIsNone(result)  # 空内容丢弃但记日志，不再静默
        self.assertEqual(len(self.fed._incoming_queue), 0)

    def test_accept_incoming_non_list_tags_coerced(self):
        ok = self.fed.receive_memory("peerA", {"content": "hello", "tags": "single"})
        self.assertTrue(ok)
        rid = self.fed.accept_incoming()
        self.assertIsNotNone(rid)

    def test_concurrent_enqueue_and_dequeue_consistent(self):
        # 预置若干消息，令总量可控；并发 accept 不应越界/丢失
        for i in range(20):
            self.fed.receive_memory("peerA", {"content": f"c{i}"})
        n = len(self.fed._incoming_queue)
        accepted = []
        lock = threading.Lock()

        def worker():
            r = self.fed.accept_incoming()
            if r is not None:
                with lock:
                    accepted.append(r)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 每条消息只会被恰好一个线程取走
        self.assertEqual(len(self.fed._incoming_queue), 0)
        self.assertEqual(len(accepted), n)


if __name__ == "__main__":
    unittest.main()
