# -*- coding: utf-8 -*-
"""MindForge 发版 spots 一键收敛（v6.5.3）

背景（2026-11-13 v6.5.2 三次推送教训）：
版本号散落 10+ 处（version.py 2 处、7 个测试钉文件、官网 9 处、
README×2 若干处、测试计数 4 处）——每次发版手动同步必漏，v6.5.2
发版因 docs guard + CI 版本钉连续两次返工。

本脚本把「bump 版本 + 同步全部 spots + 本地预检」收敛为一条命令：
  python scripts/release.py bump 6.5.4          # 执行替换
  python scripts/release.py bump 6.5.4 --dry    # 只看将改哪些
  python scripts/release.py spots               # 列出当前全部 spots

不自动 commit/push（人审后手动）——安全边界。
CHANGELOG.md 条目仍需手写（内容无法自动化），脚本会提醒。

spots 清单（与 docs consistency guard、版本钉测试的断言一一对应）：
  1. mindforge/core/version.py        __version__ + VERSION_INFO
  2. tests/test_v5*_fixes.py 等 7 钉   字符串钉 "X.Y.Z" + 元组钉 (X, Y, Z)
  3. website/index.html               版本位 9 处 + 测试计数 4 处
  4. README.md / README.zh-CN.md      badge / 架构图 / 开发历程 / 计数
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- spots 定义 -----------------------------------------------------------

# 测试版本钉文件（字符串钉 + 元组钉两种形态）
PIN_TEST_FILES = [
    "tests/test_v568_fixes.py",
    "tests/test_v575_release_consistency.py",
    "tests/test_v612_fixes.py",
    "tests/test_v643_fixes.py",
    "tests/test_v645_fixes.py",
    "tests/test_v646_fixes.py",
    "tests/test_v649_fixes.py",
]

VERSION_FILE = "mindforge/core/version.py"
WEBSITE = "website/index.html"
READMES = ["README.md", "README.zh-CN.md"]


def current_version() -> str:
    src = (ROOT / VERSION_FILE).read_text(encoding="utf-8")
    m = re.search(r'__version__: str = "([\d.]+)"', src)
    if not m:
        sys.exit("FATAL: version.py 里找不到 __version__")
    return m.group(1)


def parse_version(v: str):
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        sys.exit(f"FATAL: 版本格式应为 X.Y.Z，收到 {v!r}")
    return tuple(int(x) for x in m.groups())


# ---- spots 扫描 -----------------------------------------------------------

def scan_spots(ver: str) -> dict:
    """扫描指定版本字面量在各文件的出现位置（行号）"""
    x, y, z = parse_version(ver)
    spots = {}

    src = (ROOT / VERSION_FILE).read_text(encoding="utf-8")
    hits = [i + 1 for i, ln in enumerate(src.splitlines())
            if f'"{ver}"' in ln or f"({x}, {y}, {z})" in ln]
    spots[VERSION_FILE] = hits

    for f in PIN_TEST_FILES:
        p = ROOT / f
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        hits = [i + 1 for i, ln in enumerate(src.splitlines())
                if f'"{ver}"' in ln or f"({x}, {y}, {z})" in ln]
        spots[f] = hits

    src = (ROOT / WEBSITE).read_text(encoding="utf-8")
    spots[WEBSITE] = [i + 1 for i, ln in enumerate(src.splitlines())
                      if ver in ln]

    for f in READMES:
        src = (ROOT / f).read_text(encoding="utf-8")
        spots[f] = [i + 1 for i, ln in enumerate(src.splitlines())
                    if ver in ln]
    return {k: v for k, v in spots.items() if v}


def scan_test_count_spots() -> dict:
    """扫描测试计数 spots（官网 4 处 + README 计数行）"""
    out = {}
    for f in [WEBSITE] + READMES:
        p = ROOT / f
        src = p.read_text(encoding="utf-8")
        out[f] = [i + 1 for i, ln in enumerate(src.splitlines())
                  if re.search(r"\b1[23]\d\d\b.*(?:测试|test|passed|项)", ln)
                  or re.search(r'"testCount"\s*:', ln)]
    return {k: v for k, v in out.items() if v}


# ---- bump 执行 ------------------------------------------------------------

def apply_bump(old: str, new: str, test_count: int) -> list:
    """执行替换，返回被修改的文件列表"""
    ox, oy, oz = parse_version(old)
    nx, ny, nz = parse_version(new)
    changed = []

    def sub_file(rel, patterns, required=True):
        p = ROOT / rel
        src = p.read_text(encoding="utf-8")
        orig = src
        for pat, rep in patterns:
            src = re.sub(pat, rep, src)
        if src != orig:
            p.write_text(src, encoding="utf-8")
            changed.append(rel)
        elif required:
            print(f"WARN: {rel} 未发生替换（spots 形态可能已变化，请人工核对）")

    # 1. version.py 真值
    sub_file(VERSION_FILE, [
        (rf'__version__: str = "{re.escape(old)}"',
         f'__version__: str = "{new}"'),
        (rf'VERSION_INFO: Tuple\[int, int, int\] = \({ox}, {oy}, {oz}\)',
         f'VERSION_INFO: Tuple[int, int, int] = ({nx}, {ny}, {nz})'),
    ])

    # 2. 测试钉（字符串 + 元组两形态；v558 动态断言不受影响）
    for f in PIN_TEST_FILES:
        sub_file(f, [
            (rf'"{re.escape(old)}"', f'"{new}"'),
            (rf'\({ox}, {oy}, {oz}\)', f'({nx}, {ny}, {nz})'),
        ], required=False)

    # 3. 官网（版本位 + 计数）
    sub_file(WEBSITE, [
        (re.escape(old), new),
        (r'"testCount"\s*:\s*\d+', f'"testCount": {test_count}'),
    ])

    # 4. README ×2（badge / 架构图 / 开发历程 / 计数行）
    for f in READMES:
        sub_file(f, [
            (re.escape(old), new),
            (r'(\d+) passing tests \(\d+ skipped',
             f'{test_count - 1} passing tests ($ skipped'),
            (r'(\d+) 项测试通过（\d+ 项中',
             f'{test_count - 1} 项测试通过（{test_count} 项中'),
        ])
    return changed


# ---- 预检 ----------------------------------------------------------------

def run_guard() -> bool:
    r = subprocess.run(
        [sys.executable, "scripts/check_docs_consistency.py"],
        cwd=ROOT, capture_output=True, text=True)
    ok = "PASS" in r.stdout
    tail = (r.stdout or r.stderr).strip().splitlines()
    for ln in tail[-3:]:
        print("   ", ln)
    return ok


def run_pin_tests() -> bool:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no"] + PIN_TEST_FILES,
        cwd=ROOT, capture_output=True, text=True)
    ok = " failed" not in r.stdout.splitlines()[-1] if r.stdout else False
    print("   ", (r.stdout or "").strip().splitlines()[-1] if r.stdout else "无输出")
    return ok


# ---- CLI ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    p_bump = sub.add_parser("bump", help="版本号 bump + spots 同步")
    p_bump.add_argument("version", help="目标版本 X.Y.Z")
    p_bump.add_argument("--count", type=int, default=0,
                        help="测试计数（默认保持不动）")
    p_bump.add_argument("--dry", action="store_true", help="只展示不执行")
    sub.add_parser("spots", help="列出当前版本全部 spots")
    args = ap.parse_args()

    if args.cmd == "spots":
        cur = current_version()
        print(f"当前版本 {cur}，spots 分布：")
        for f, lines in scan_spots(cur).items():
            print(f"  {f}: 行 {lines}")
        tc = scan_test_count_spots()
        print("测试计数 spots：")
        for f, lines in tc.items():
            print(f"  {f}: 行 {lines}")
        return

    if args.cmd == "bump":
        old = current_version()
        new = args.version
        if old == new:
            sys.exit(f"FATAL: 目标版本与当前相同（{old}）")
        print(f"bump: {old} -> {new}")
        if args.dry:
            print("（dry 模式）将替换以下 spots：")
            for f, lines in scan_spots(old).items():
                print(f"  {f}: {len(lines)} 处")
            if args.count:
                print(f"  测试计数 -> {args.count}（官网 4 处 + README 计数行）")
            return
        changed = apply_bump(old, new, args.count or 0)
        print(f"已修改 {len(changed)} 个文件：")
        for f in changed:
            print("  ", f)
        print("\n[预检] docs consistency guard：")
        g_ok = run_guard()
        print("[预检] 版本钉测试组：")
        t_ok = run_pin_tests()
        print(f"\n预检结果：guard={'PASS' if g_ok else 'FAIL'} "
              f"pins={'PASS' if t_ok else 'FAIL'}")
        print("提醒：CHANGELOG.md 需手写条目；确认后手动 commit + tag + push。")
        if not (g_ok and t_ok):
            sys.exit(1)


if __name__ == "__main__":
    main()
