#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档与官网数字一致性守卫（CI 用）。

背景：官网 / README 的版本号与统计数字历史上反复漂移——
  - 代码已发到 v5.6.8，官网还停在 v5.6.6（版本号 / 安装示例 / 统计数字全旧）；
  - 测试用例数从 608 → 650 → 663 → 751 的过程中，官网多次只改了一部分位置；
  - 官网「更新日志」出现过**版本标签与内容错位**（v5.6.8 的内容被标成 v5.6.9，
    v5.6.0 的内容被标成 v5.6.1），以及整个版本条目漏加。
而 CI 此前只断言 MCP 工具数 == 33，版本号与测试数**完全没有守护**，
所以每次发版都要靠人工通读官网才能发现，漏一次就错一次。

本脚本把这些数字变成可被 CI 守护的断言。退出码非 0 即表示不一致。

用法：
    python scripts/check_docs_consistency.py            # 全量校验（含测试数比对）
    python scripts/check_docs_consistency.py --no-testcount   # 无 pytest 环境时跳过测试数比对
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []
OKS = []


def ok(msg):
    OKS.append(msg)
    print("  PASS  " + msg)


def fail(msg):
    FAILS.append(msg)
    print("  FAIL  " + msg)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def check(name, got, want):
    if got == want:
        ok("%s = %s" % (name, got))
    else:
        fail("%s = %r，期望 %r" % (name, got, want))


# ---------- 1. 版本真值 ----------
print("[1] 版本真值")
ver_src = read("core/version.py")
m = re.search(r'__version__\s*(?::\s*str\s*)?=\s*["\']([\d.]+)["\']', ver_src)
if not m:
    fail("core/version.py 中找不到 __version__")
    sys.exit(1)
VERSION = m.group(1)
ok("core/version.py __version__ = %s" % VERSION)

pyproject = read("pyproject.toml")
m = re.search(r'^\[project\][\s\S]*?^version\s*=\s*"([^"]+)"', pyproject, re.M)
check("pyproject.toml project.version", m.group(1) if m else None, VERSION)

# VERSION_INFO 应与之对应
m = re.search(r'VERSION_INFO\s*(?::[^=]+)?=\s*\(([^)]*)\)', ver_src)
if m:
    nums = re.findall(r"\d+", m.group(1))
    check("core/version.py VERSION_INFO", ".".join(nums), VERSION)

# ---------- 2. README ----------
print("[2] README.md")
readme = read("README.md")
badges = sorted(set(re.findall(r"badge/version-([\d.]+)-", readme)))
check("版本徽章", badges, [VERSION])

# 架构图方框内的标题行（形如「│      MindForge v5.7.0      │」）
arch = re.findall(r"MindForge v([\d.]+)\s*[│|]", readme)
check("架构图标题版本", sorted(set(arch)), [VERSION])

# ---------- 3. 官网 index.html ----------
print("[3] website/index.html")
index = read("website/index.html")
check("JSON-LD softwareVersion",
      sorted(set(re.findall(r'"softwareVersion"\s*:\s*"([^"]+)"', index))), [VERSION])
check("顶栏 brand-ver",
      sorted(set(re.findall(r'class="brand-ver">v([\d.]+)<', index))), [VERSION])
# hero 区有两个徽标：当前版本 + v6.0 路线图（带 chip-badge「开发中」）。
# 只取当前版本那个，否则会把路线图版本误判为漂移。
hero_cur = []
for chunk in index.split('class="hero-badge"')[1:]:
    chunk = chunk[:400]
    if "chip-badge" in chunk:
        continue
    hm = re.search(r"<b>v([\d.]+)</b>", chunk)
    if hm:
        hero_cur.append(hm.group(1))
check("hero 徽标（当前版本，排除路线图徽标）", sorted(set(hero_cur)), [VERSION])
check("安装示例输出",
      sorted(set(re.findall(r'Successfully installed mindforge-([\d.]+)', index))), [VERSION])
check("开发者板块标题",
      sorted(set(re.findall(r'敲到 v([\d.]+)</h2>', index))), [VERSION])
check("更新日志导语",
      sorted(set(re.findall(r'从 v[\d.]+ 到 v([\d.]+)，', index))), [VERSION])

# ---------- 4. 测试用例数 ----------
print("[4] 测试用例数")
tc_jsonld = sorted(set(re.findall(r'"testCount"\s*:\s*(\d+)', index)))
tc_stat = sorted(set(re.findall(r'class="stat-num">(\d+)</div><div class="stat-lbl">测试用例<', index)))
tc_about = sorted(set(re.findall(r'class="about-stat"><b>(\d+)</b><span>测试用例全绿<', index)))
tc_text = sorted(set(re.findall(r'(\d+) 个测试用例全部通过', index)))
groups = {"JSON-LD": tc_jsonld, "统计卡": tc_stat, "开发者统计": tc_about, "开发者正文": tc_text}
for k, v in groups.items():
    if len(v) != 1:
        fail("%s 的测试用例数不唯一：%s" % (k, v))
    else:
        ok("%s = %s" % (k, v[0]))
present = {k: v[0] for k, v in groups.items() if len(v) == 1}
if len(set(present.values())) > 1:
    fail("四处测试用例数不一致：%s" % present)
elif present:
    ok("四处测试用例数彼此一致")

if "--no-testcount" not in sys.argv:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True, timeout=600)
        collected = None
        for line in reversed((out.stdout or "").strip().splitlines()):
            mm = re.search(r"(\d+)\s+tests?\s+collected", line)
            if mm:
                collected = mm.group(1)
                break
        if collected is None:
            fail("无法解析 pytest --collect-only 的收集数（退出码 %s）" % out.returncode)
        else:
            for k in present:
                check("官网%s vs 实际收集数" % k, present[k], collected)
    except Exception as e:                                  # noqa: BLE001
        fail("运行 pytest --collect-only 失败：%s: %s" % (type(e).__name__, e))
else:
    print("  SKIP  测试数比对（--no-testcount）")

# ---------- 5. 更新日志条目 vs CHANGELOG ----------
print("[5] 更新日志条目 vs CHANGELOG.md")
changelog = read("CHANGELOG.md")
sections = {}
for sm in re.finditer(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})\s*$", changelog, re.M):
    sections[sm.group(1)] = sm.group(2)

entries = []
for am in re.finditer(r'<article class="cl-item[^"]*reveal">(.*?)</article>', index, re.S):
    blk = am.group(1)
    vm = re.search(r'class="cl-ver">v([\d.]+)<', blk)
    dm = re.search(r'class="cl-date">([\d-]+)<', blk)
    if vm and dm:
        entries.append((vm.group(1), dm.group(1), "cl-kind-latest" in blk))

if not entries:
    fail("官网更新日志区解析不到任何条目")
else:
    ok("解析到 %d 条更新日志" % len(entries))
    for v, d, _ in entries:
        if v not in sections:
            fail("官网有条目 v%s，但 CHANGELOG.md 中不存在该版本" % v)
        elif sections[v] != d:
            fail("官网 v%s 标注日期 %s，CHANGELOG 为 %s" % (v, d, sections[v]))
        else:
            ok("v%s 日期与 CHANGELOG 一致（%s）" % (v, d))

    # 严格降序
    nums = [tuple(int(x) for x in v.split(".")) for v, _, _ in entries]
    if nums != sorted(nums, reverse=True) and nums != sorted(nums):
        fail("更新日志条目版本号未按顺序排列：%s" % [v for v, _, _ in entries])
    else:
        ok("更新日志条目版本号有序")

    # 「最新」标记必须落在最高版本上
    if entries and not entries[0][2]:
        fail("首条更新日志（v%s）没有 cl-kind-latest 标记" % entries[0][0])
    elif len([1 for _, _, latest in entries if latest]) > 1:
        fail("多于一条更新日志带 cl-kind-latest 标记")
    else:
        ok("「最新」标记落在最高版本 v%s" % (entries[0][0] if entries else "?"))

# ---------- 汇总 ----------
print()
if FAILS:
    print("== 一致性守卫: FAIL（%d 项不一致）==" % len(FAILS))
    for f in FAILS:
        print("   - " + f)
    sys.exit(1)
print("== 一致性守卫: PASS（%d 项断言全部通过）==" % len(OKS))
