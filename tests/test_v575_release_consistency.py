# -*- coding: utf-8 -*-
"""v5.7.5 发布治理回归测试

覆盖 v5.7.5 修复的发布治理问题（对应外部审查指出的「版本与发布真实性」缺口）：

  P0-01 README 重写丢失 HMAC_XOR 迁移提示（`EXPERIMENTAL_HMAC_XOR` 与
        「降级加密」），导致 tests/test_v557_webhook_sig_export.py 回归失败，
        v5.7.3 发布时测试套件实际未全绿；
  P0-02 版本号漂移：v5.7.3 已发版，官网仍停在 v5.7.2 / 751 测试，
        文档一致性守卫（scripts/check_docs_consistency.py）8 项不一致；
  P0-03 README 架构图重写后丢失版本标题行，守卫架构图断言失配；
  P0-04 版本号兜底（cli/main.py、mcp/server.py）与 pyproject.toml
        未随 core/version.py 真值同步。

本轮验证方式：不重启完整守卫（CI 已跑），而是直接断言版本真值
在各处的投影一致，并把 CHANGELOG 声明的全量测试数作为官网测试数
的唯一对照源（CI 的守卫再校验官网 == pytest 实际收集数，形成闭环）。
"""

import re
import unittest
from pathlib import Path

import core.version as core_version

_REPO = Path(__file__).resolve().parent.parent
_VERSION = core_version.__version__


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# P0-04 版本号真值唯一源
# --------------------------------------------------------------------------
class TestVersionTruth(unittest.TestCase):
    """版本号真值必须收敛在 core/version.py，且各处投影一致。"""

    def test_core_version_is_580(self):
        self.assertEqual(core_version.__version__, "5.8.4")
        self.assertEqual(core_version.VERSION_INFO, (5, 8, 4))

    def test_pyproject_matches_core_version(self):
        m = re.search(r'^version\s*=\s*"([^"]+)"', _read("pyproject.toml"), re.M)
        self.assertIsNotNone(m, "pyproject.toml 缺少 project.version")
        self.assertEqual(m.group(1), _VERSION)

    def test_cli_and_mcp_fallback_match_truth(self):
        for rel in ("cli/main.py", "mcp/server.py"):
            literals = re.findall(
                r'__version__\s*=\s*["\']([^"\']+)["\']', _read(rel)
            )
            self.assertTrue(literals, f"{rel} 应含兜底 __version__ 字面量")
            for lit in literals:
                self.assertEqual(
                    lit, _VERSION,
                    f"{rel} 兜底版本 {lit} 与真值 {_VERSION} 漂移（P0-04）")

    def test_test_expectation_matches_truth(self):
        src = _read("tests/test_v568_fixes.py")
        m = re.search(r'_EXPECTED_VERSION\s*=\s*"([^"]+)"', src)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), _VERSION)


# --------------------------------------------------------------------------
# P0-03 / P0-01 README 文档
# --------------------------------------------------------------------------
class TestReadmeDocs(unittest.TestCase):
    """README 徽章、架构图版本标题与 HMAC_XOR 迁移提示。"""

    def test_readme_badge_matches_truth(self):
        self.assertIn(f"version-{_VERSION}-green.svg", _read("README.md"))
        self.assertIn(f"version-{_VERSION}-green.svg", _read("README.zh-CN.md"))

    def test_readme_architecture_title_has_version(self):
        readme = _read("README.md")
        # 与 scripts/check_docs_consistency.py 的架构图断言同一正则
        arch = re.findall(r"MindForge v([\d.]+)\s*[│|]", readme)
        self.assertEqual(sorted(set(arch)), [_VERSION],
                         "README 架构图缺少版本标题行（P0-03）")

    def test_readme_hmac_xor_notice_present(self):
        # 回归：README 重写丢失精确标志名与中文说明，导致
        # tests/test_v557_webhook_sig_export.py 失败（P0-01）
        content = _read("README.md")
        self.assertIn("EXPERIMENTAL_HMAC_XOR", content)
        self.assertIn("降级加密", content)


# --------------------------------------------------------------------------
# P0-02 官网数字一致性
# --------------------------------------------------------------------------
class TestWebsiteConsistency(unittest.TestCase):
    """官网 index.html 的版本与测试数必须与真值 / CHANGELOG 一致。"""

    def test_website_version_spots_match_truth(self):
        index = _read("website/index.html")
        self.assertIn(f'"softwareVersion": "{_VERSION}"', index)
        self.assertIn(f'class="brand-ver">v{_VERSION}<', index)
        self.assertIn(f"Successfully installed mindforge-{_VERSION}", index)
        self.assertIn(f"敲到 v{_VERSION}</h2>", index)
        # hero 徽标：排除 v6 路线图 chip-badge 块后必须命中当前版本
        hero = []
        for chunk in index.split('class="hero-badge"')[1:]:
            chunk = chunk[:400]
            if "chip-badge" in chunk:
                continue
            m = re.search(r"<b>v([\d.]+)</b>", chunk)
            if m:
                hero.append(m.group(1))
        self.assertEqual(sorted(set(hero)), [_VERSION])
        # 更新日志导语「从 vX 到 vY」
        intro = re.findall(r"从 v[\d.]+ 到 v([\d.]+)，", index)
        self.assertEqual(sorted(set(intro)), [_VERSION])

    def _changelog_declared_test_count(self) -> str:
        changelog = _read("CHANGELOG.md")
        # 取 v5.7.5 条目内的「全量 NNN 项通过」
        sec = changelog.split(f"## [{_VERSION}] - ", 1)[1]
        sec = sec.split("\n## [", 1)[0]
        m = re.search(r"全量\s*\**(\d+)\**\s*项.*?通过", sec)
        self.assertIsNotNone(m, "CHANGELOG v5.7.5 条目缺少「全量 NNN 项通过」")
        return m.group(1)

    def test_website_test_count_spots_consistent(self):
        want = self._changelog_declared_test_count()
        index = _read("website/index.html")
        spots = {
            "JSON-LD": re.findall(r'"testCount"\s*:\s*(\d+)', index),
            "统计卡": re.findall(
                r'class="stat-num">(\d+)</div><div class="stat-lbl">测试用例<',
                index),
            "开发者统计": re.findall(
                r'class="about-stat"><b>(\d+)</b><span>测试用例全绿<', index),
            "开发者正文": re.findall(r'(\d+) 个测试用例全部通过', index),
        }
        for k, v in spots.items():
            self.assertEqual(len(v), 1, f"官网「{k}」测试用例数不唯一：{v}")
            self.assertEqual(v[0], want,
                             f"官网「{k}」={v[0]}，CHANGELOG 声明 {want}")
        self.assertEqual(len({v[0] for v in spots.values()}), 1)

    def test_website_changelog_latest_is_575(self):
        index = _read("website/index.html")
        entries = []
        for am in re.finditer(
                r'<article class="cl-item[^"]*reveal">(.*?)</article>',
                index, re.S):
            blk = am.group(1)
            vm = re.search(r'class="cl-ver">v([\d.]+)<', blk)
            dm = re.search(r'class="cl-date">([\d-]+)<', blk)
            if vm and dm:
                entries.append((vm.group(1), dm.group(1), "cl-kind-latest" in blk))
        self.assertTrue(entries, "官网更新日志解析不到条目")
        # 首条必须是 v5.7.5 且带「最新」标记，且全页仅一条
        self.assertEqual(entries[0][0], _VERSION,
                         f"官网更新日志首条应为 v{_VERSION}")
        self.assertTrue(entries[0][2], "首条更新日志缺少 cl-kind-latest 标记")
        self.assertEqual(len([1 for _, _, latest in entries if latest]), 1)

    def test_website_changelog_dates_match_changelog(self):
        changelog = _read("CHANGELOG.md")
        sections = dict(
            re.findall(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})\s*$",
                       changelog, re.M))
        index = _read("website/index.html")
        for am in re.finditer(
                r'<article class="cl-item[^"]*reveal">(.*?)</article>',
                index, re.S):
            vm = re.search(r'class="cl-ver">v([\d.]+)<', am.group(1))
            dm = re.search(r'class="cl-date">([\d-]+)<', am.group(1))
            if vm and dm:
                self.assertEqual(
                    sections.get(vm.group(1)), dm.group(1),
                    f"官网 v{vm.group(1)} 日期 {dm.group(1)} 与 CHANGELOG 不一致")


# --------------------------------------------------------------------------
# CHANGELOG 顶部条目
# --------------------------------------------------------------------------
class TestChangelog(unittest.TestCase):
    def test_changelog_top_entry_is_575(self):
        changelog = _read("CHANGELOG.md")
        head = changelog.split("## [", 1)[1]
        self.assertTrue(head.startswith(f"{_VERSION}] - "),
                        f"CHANGELOG 首条应为 v{_VERSION}")
        # 版本必须按严格降序排列（v5.7.3 应紧跟其后）
        versions = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M)
        nums = [tuple(int(x) for x in v.split(".")) for v in versions]
        self.assertEqual(nums, sorted(nums, reverse=True),
                         "CHANGELOG 版本条目未按降序排列")


if __name__ == "__main__":
    unittest.main()
