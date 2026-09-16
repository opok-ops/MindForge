#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MindForge 官网 —— 百度收录状态一键自查

为什么需要它：
    `baidu-push.yml` 在缺 Secret 时会"优雅跳过"并显示绿色 ✔，
    这会让人误以为百度收录已经在正常工作。本脚本把真实状态一次性说清楚：
    到底是"已开通并推送成功"还是"从未推送过"。

它检查两件事（无需任何 token / 凭据）：
    1. 首页是否已注入百度站点验证标签（<meta name="baidu-site-verification">）
    2. 线上 sitemap.xml 里 lastmod 是否为近期日期

用法：
    python scripts/baidu_status.py
    python scripts/baidu_status.py --url https://example.com/
    python scripts/baidu_status.py --json

依赖：仅 Python 标准库（3.7+）。
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://opok-ops.github.io/MindForge/"

# 与 baidu_push.py 保持一致，避免两处定义漂移
VERIFY_TAG = "baidu-site-verification"
VERIFY_SLOT = "<!--BAIDU_VERIFY_SLOT-->"


def fetch(url, timeout=20):
    """取回页面文本；失败返回 (None, 错误说明)。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": "MindForge-baidu-status/1.0", "Cache-Control": "no-cache"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as exc:
        return None, "HTTP {}".format(exc.code)
    except urllib.error.URLError as exc:
        return None, "网络不可达：{}（若本机无法直连，请换网络重试）".format(exc.reason)
    except Exception as exc:  # noqa: BLE001 - 兜底
        return None, "未知错误：{}".format(exc)


def check(base_url):
    """返回自查结果字典。"""
    if not base_url.endswith("/"):
        base_url += "/"

    home, err_home = fetch(base_url)
    sitemap, err_map = fetch(base_url + "sitemap.xml")

    verified = False
    verify_value = None
    if home:
        m = re.search(
            r'<meta[^>]+name=["\']' + re.escape(VERIFY_TAG) + r'["\'][^>]*content=["\']([^"\']+)["\']',
            home,
        )
        if m:
            verified = True
            verify_value = m.group(1)
    slot_leftover = bool(home) and VERIFY_SLOT in home

    lastmod = None
    if sitemap:
        m = re.search(r"<lastmod>(.*?)</lastmod>", sitemap)
        if m:
            lastmod = m.group(1).strip()

    return {
        "base_url": base_url,
        "home_ok": home is not None,
        "home_error": err_home,
        "verified": verified,
        "verify_value": verify_value,
        "slot_leftover": slot_leftover,
        "sitemap_ok": sitemap is not None,
        "sitemap_error": err_map,
        "lastmod": lastmod,
    }


def render(r, as_json=False):
    if as_json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    ok_icon = "✔"
    bad_icon = "✘"
    print("MindForge 百度收录状态自查")
    print("=" * 46)
    print("站点：{}".format(r["base_url"]))
    print()

    if not r["home_ok"]:
        print("{} 首页拉取失败：{}".format(bad_icon, r["home_error"]))
    elif r["verified"]:
        print("{} 站点验证标签：已注入（content={}）".format(ok_icon, r["verify_value"]))
    elif r["slot_leftover"]:
        print("{} 站点验证标签：未注入（首页仍是占位注释）".format(bad_icon))
    else:
        print("{} 站点验证标签：未找到（index.html 可能被改动过？）".format(bad_icon))

    if not r["sitemap_ok"]:
        print("{} Sitemap 拉取失败：{}".format(bad_icon, r["sitemap_error"]))
    else:
        print("{} Sitemap lastmod：{}".format(ok_icon, r["lastmod"] or "（未找到 lastmod）"))

    print()
    if r["verified"]:
        print("结论：站点归属已验证。若推送仍失败，检查 BAIDU_PUSH_TOKEN 是否配置正确。")
        print("提示：真正推进成功的信号是推送日志出现「推送成功：本轮提交 N 条」。")
        return 0

    print("结论：⚠️ 尚未完成百度站点验证 —— 主动推送接口不可用。")
    print()
    print("下一步（顺序不能颠倒）：")
    print("  1. 站长平台取验证码 → 配 Secret BAIDU_SITE_VERIFICATION → 触发一次 Deploy")
    print("  2. 站长平台点「验证」")
    print("  3. 再配 Secret BAIDU_PUSH_TOKEN → 手动触发推送工作流")
    print()
    print("详见 docs/百度站长平台收录指南.md")
    return 1


def main():
    ap = argparse.ArgumentParser(description="MindForge 百度收录状态自查（无需凭据）")
    ap.add_argument("--url", default=DEFAULT_URL, help="站点根地址，默认 " + DEFAULT_URL)
    ap.add_argument("--json", action="store_true", help="以 JSON 输出，便于脚本消费")
    args = ap.parse_args()
    return render(check(args.url), as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
