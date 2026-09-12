#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MindForge 官网 —— 百度站长平台「主动推送（API 提交）」脚本

为什么需要它：
    等百度蜘蛛自己发现新页面通常要几周，主动推送是收录最快的方式（通常几小时内来抓）。
    本脚本把 Sitemap 里的 URL 直接推给百度。

接口来源：
    百度站长平台 → 资源提交 → API 提交 → 推送接口
    https://ziyuan.baidu.com/linksubmit/index

用法：
    # 从 sitemap.xml 自动提取 URL 推送（推荐）
    python scripts/baidu_push.py

    # 直接指定 URL
    python scripts/baidu_push.py --url https://opok-ops.github.io/MindForge/

    # 完整参数
    python scripts/baidu_push.py --site https://opok-ops.github.io/MindForge/ --token xxxxx

凭据的三种给法（优先级从高到低）：
    1. 命令行参数 --site / --token
    2. 环境变量 BAIDU_PUSH_SITE / BAIDU_PUSH_TOKEN
    3. 都不给则交互式询问（token 输入不回显）

两个极易踩的坑：
    · site 必须与百度站长平台「添加网站」时填的站点地址**完全一致**，
      包括尾斜杠、http/https、子路径。否则一律 400。
    · token 是站长平台「推送接口」里那一串密钥，跟「站点验证用的验证码」不是同一个东西。

依赖：仅 Python 标准库（3.7+），不需要安装任何包。
"""

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# 百度主动推送接口地址（使用 HTTPS，使 site/token 查询参数在传输中加密，
# 避免经 HTTP 明文泄露；百度接口要求 site 与 token 以查询参数形式提交）。
PUSH_ENDPOINT = "https://data.zz.baidu.com/urls"

# MindForge 官网默认值（在站长平台登记站点时需与此一致）
DEFAULT_SITE = "https://opok-ops.github.io/MindForge/"
DEFAULT_SITEMAP = "website/sitemap.xml"

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# 百度接口常见错误码的人话解释
ERROR_HINTS = {
    400: "请求参数有误 —— 最常见原因是 site 与站长平台登记的站点地址不一致"
         "（请核对尾斜杠、http/https、是否带 /MindForge/ 子路径）",
    401: "token 无效 —— 请到站长平台「API 提交」页面重新复制推送接口里的 token",
    404: "站点未添加或接口地址有误 —— 请先在站长平台添加站点并通过所有权验证",
    405: "请求方式错误 —— 必须是 POST",
    500: "百度服务暂时异常 —— 稍后重试即可",
}


def extract_urls_from_sitemap(sitemap_path):
    """从 Sitemap XML 中提取所有 <loc> 地址。

    兼容带命名空间与不带命名空间两种写法，并跳过锚点地址（#xxx），
    因为锚点对搜索引擎不产生独立页面。
    """
    path = Path(sitemap_path)
    if not path.is_file():
        raise FileNotFoundError("找不到 Sitemap 文件：{}".format(path))

    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as exc:
        raise ValueError("Sitemap 不是合法的 XML：{}".format(exc))

    urls = []
    for loc in root.iter():
        if not loc.tag.endswith("loc"):
            continue
        url = (loc.text or "").strip()
        if not url:
            continue
        # 丢弃锚点：# 后面的部分不参与搜索引擎的 URL 识别
        url = url.split("#")[0]
        if url and url not in urls:
            urls.append(url)
    return urls


def push_urls(site, token, urls, timeout=15):
    """调用百度主动推送接口。

    返回 (是否成功, 提示信息)。
    成功时形如：{"remain": 2999, "success": 1}
    失败时形如：{"error": 401, "message": "token is not valid"}
    """
    if not urls:
        return False, "没有可推送的 URL"

    query = urllib.parse.urlencode({"site": site, "token": token})
    endpoint = "{}?{}".format(PUSH_ENDPOINT, query)

    # 请求体：纯文本，每行一个 URL
    payload = "\n".join(urls).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "text/plain",
            "User-Agent": "MindForge-baidu-pusher/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # 百度在 4xx/5xx 的响应体里同样带 JSON 错误详情，要读出来
        raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        status = exc.code
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        message = data.get("message", raw[:200] or "无返回内容")
        hint = ERROR_HINTS.get(status, "")
        return False, "推送失败 HTTP {}：{}{}".format(
            status, message, ("\n  排查建议：" + hint) if hint else ""
        )
    except urllib.error.URLError as exc:
        return False, "网络请求失败：{}（若本机网络无法直连百度，请换网络重试）".format(exc.reason)
    except Exception as exc:  # noqa: BLE001 - 兜底，避免脚本抛出裸异常
        return False, "推送时发生未知错误：{}".format(exc)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False, "百度返回了非 JSON 内容，可能接口有变动：{}".format(raw[:200])

    if "error" in data:
        hint = ERROR_HINTS.get(data["error"], "")
        return False, "推送失败（错误码 {}）：{}{}".format(
            data["error"],
            data.get("message", "无说明"),
            ("\n  排查建议：" + hint) if hint else "",
        )

    remain = data.get("remain", "未知")
    success = data.get("success", 0)
    not_same = data.get("not_same_site", [])
    not_valid = data.get("not_valid", [])

    detail = "推送成功：本轮提交 {} 条，当天剩余配额 {}".format(success, remain)
    if not_valid:
        detail += "\n  以下 URL 不合法，已忽略：{}".format(", ".join(not_valid))
    if not_same:
        detail += (
            "\n  以下 URL 不在本站点下，已忽略：{}\n"
            "  → 检查站长平台登记的站点地址与 --site 是否一致".format(", ".join(not_same))
        )
    return True, detail


def resolve_credentials(args):
    """确定 site 与 token：命令行 > 环境变量 > 交互式输入。"""
    site = args.site or os.environ.get("BAIDU_PUSH_SITE") or DEFAULT_SITE

    token = args.token or os.environ.get("BAIDU_PUSH_TOKEN")
    if not token and sys.stdin.isatty():
        print("未检测到 token。")
        print("获取方式：百度站长平台 → 资源提交 → API 提交 → 推送接口")
        token = getpass.getpass("请输入推送 token（输入不回显）: ").strip()

    if not token:
        # 非交互环境（如 CI）下没拿到 token，直接给明确指引而不是报晦涩错误
        raise SystemExit(
            "缺少推送 token。请任选其一：\n"
            "  1. 环境变量：export BAIDU_PUSH_TOKEN=你的token\n"
            "  2. 命令行参数：python scripts/baidu_push.py --token 你的token\n"
            "  3. 配置 GitHub Secrets：BAIDU_PUSH_TOKEN（CI 会自动用）"
        )
    return site, token


def main():
    parser = argparse.ArgumentParser(
        description="把 MindForge 官网 URL 主动推送给百度站长平台，加速收录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--site", help="站长平台登记的站点地址，默认 " + DEFAULT_SITE)
    parser.add_argument("--token", help="百度推送接口 token")
    parser.add_argument(
        "--sitemap",
        default=DEFAULT_SITEMAP,
        help="Sitemap 路径，默认 " + DEFAULT_SITEMAP,
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        metavar="URL",
        help="手动指定要推送的 URL，可重复传入；指定后忽略 --sitemap",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要推送的内容，不真正请求百度（用于自检）",
    )
    args = parser.parse_args()

    # 收集待推送的 URL
    if args.urls:
        urls = [u.split("#")[0] for u in args.urls if u.strip()]
        source = "命令行参数"
    else:
        # 允许在仓库任意位置执行，自动向上找 sitemap
        candidates = [Path(args.sitemap), Path(__file__).resolve().parent.parent / args.sitemap]
        sitemap = next((p for p in candidates if p.is_file()), None)
        if sitemap is None:
            raise SystemExit(
                "找不到 Sitemap：{}\n请在仓库根目录执行，或用 --sitemap 指定路径。".format(args.sitemap)
            )
        urls = extract_urls_from_sitemap(sitemap)
        source = str(sitemap)

    if not urls:
        raise SystemExit("Sitemap 中没有解析到任何 URL，请检查文件内容。")

    print("待推送 URL（来源：{}）：".format(source))
    for url in urls:
        print("  · " + url)

    if args.dry_run:
        print("\n[dry-run] 以上 {} 条未真正提交。".format(len(urls)))
        return 0

    site, token = resolve_credentials(args)
    print("\n推送到站点：{}".format(site))

    ok, message = push_urls(site, token, urls)
    print("\n" + message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
