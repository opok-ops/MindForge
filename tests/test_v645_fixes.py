"""
MindForge v6.4.5 回归测试

覆盖发版前审计发现的两类缺陷（2026-11-10）：

  - Bug-A（P1）: connectors.py 对外请求的 User-Agent 硬编码为
    "MindForge-Connector/5.8.9"——5.8.9 时代写死后未跟上，v6.x 连发
    20+ 版本一直在对外谎报版本（event_bus.py v5.6.8 修过同款病，
    此处漏网）。修复：动态引用 mindforge/core/version.py 唯一真值。
  - Bug-B（P2，数据过时）: 官网「核心模块」数正文 17 / 统计卡 15，
    实测 mindforge/modules/ 为 31 个模块文件——两处口径双双过时且
    互相矛盾。修正为实测 31，并由本守卫钉住（website 数字必须等于
    modules/ 实测文件数，防再漂移）。

审计存档（非缺陷）：WAL auto-checkpoint 行为验证——四轮 × 400 次
混合操作（add+search），WAL 稳定收敛于 ~4.1MB（默认 1000 页阈值），
无无限膨胀；主动 TRUNCATE 可归零。daemon 长连接场景健康。
"""

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = Path(__file__).resolve().parent.parent

from mindforge.core.version import __version__


class TestUserAgentVersionTruth(unittest.TestCase):
    """对外 User-Agent 的版本必须动态引用唯一真值，不得硬编码。"""

    def test_connector_ua_is_dynamic(self):
        t = (_REPO / "mindforge" / "modules" / "connectors.py").read_text(
            encoding="utf-8")
        # 修复后的形态：f-string 引用 __version__
        self.assertIn('f"MindForge-Connector/{__version__}"', t,
                      "connectors UA 应动态引用 __version__")
        # 病灶形态必须消失：任何带版本号的硬编码 UA
        bad = re.findall(r'User-Agent[^)]*?/[0-9]+\.[0-9]+\.[0-9]+', t)
        self.assertEqual(bad, [], f"发现硬编码版本 UA: {bad}")
        # import 必须存在（防 f-string 引用悬空）
        self.assertIn("from mindforge.core.version import __version__", t)

    def test_no_stale_version_in_runtime_ua(self):
        """全包扫描：运行时 UA/标识字符串不得携带过时版本字面量。"""
        pat = re.compile(r'MindForge[-A-Za-z]*/(?:\d+\.\d+\.\d+)')
        for py in (_REPO / "mindforge").rglob("*.py"):
            hits = pat.findall(py.read_text(encoding="utf-8"))
            # 允许出现的位置：注释里的历史说明（v6.4.5 fix 注释提及旧值）
            for line_no, line in enumerate(
                    py.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line) and "v6.4.5 fix" not in line:
                    # schema 标识（mindforge-knowledge-graph/1.0）为协议
                    # 格式版本，非产品版本，豁免。
                    if "knowledge-graph" in line:
                        continue
                    self.fail(
                        f"{py.name}:{line_no} 疑似硬编码版本 UA: {line.strip()}")


class TestWebsiteModuleCountTruth(unittest.TestCase):
    """官网「核心模块」数 = mindforge/modules/ 实测文件数（防再过时）。"""

    def _modules_count(self) -> int:
        return len([p for p in (_REPO / "mindforge" / "modules").glob("*.py")
                    if not p.name.startswith("_")])

    def test_website_module_count_matches_reality(self):
        want = str(self._modules_count())
        index = (_REPO / "website" / "index.html").read_text(encoding="utf-8")
        spots = {
            "正文": re.findall(r'(\d+) 个核心模块', index),
            "统计卡": re.findall(
                r'class="about-stat"><b>(\d+)</b><span>核心模块<', index),
        }
        for k, v in spots.items():
            self.assertEqual(len(v), 1, f"官网「{k}」核心模块数不唯一: {v}")
            self.assertEqual(v[0], want,
                f"官网「{k}」={v[0]}，实测 modules/={want}")

    def test_website_mcp_tool_count_matches_reality(self):
        """MCP 工具数守卫（87 实测口径；import server 拿 TOOL_SCHEMAS）。"""
        try:
            from mindforge.mcp.server import TOOL_SCHEMAS
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"server 模块不可导入: {exc}")
        want = str(len(TOOL_SCHEMAS))
        index = (_REPO / "website" / "index.html").read_text(encoding="utf-8")
        hits = re.findall(r'(\d+) 个? ?MCP 工具|(\d+) MCP tools', index)
        flat = [x for pair in hits for x in pair if x]
        self.assertTrue(flat, "官网未声明 MCP 工具数")
        for v in flat:
            self.assertEqual(v, want, f"官网 MCP 工具数 {v} ≠ 实测 {want}")


class TestVersionBump(unittest.TestCase):
    """版本钉值：忘记 bump 时在此显形。"""

    def test_version_is_current(self):
        self.assertEqual(__version__, "6.5.5")


if __name__ == "__main__":
    unittest.main()
