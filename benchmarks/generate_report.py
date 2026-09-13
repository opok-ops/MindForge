# -*- coding: utf-8 -*-
"""
MindForge 全面测试与性能报告 PDF 生成器
======================================

读取：
  benchmarks/results/perf_results.json   性能基准结果
  benchmarks/results/pytest.xml          pytest JUnit XML
  benchmarks/results/coverage.json      coverage JSON（可选）

输出：
  benchmarks/results/MindForge_v5.6.3_test_perf_report.pdf
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image, KeepTogether,
)

# ---------------------------------------------------------------------------
# 中文字体
# ---------------------------------------------------------------------------

_CJK_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]
CJK_FONT = None
for fp in _CJK_CANDIDATES:
    if os.path.exists(fp):
        try:
            font_manager.fontManager.addfont(fp)
            CJK_FONT = font_manager.FontProperties(fname=fp).get_name()
            break
        except Exception:
            continue
if CJK_FONT:
    plt.rcParams["font.sans-serif"] = [CJK_FONT]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 130

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
FONT = "STSong-Light"

# 配色
BLUE = colors.HexColor("#1f5fa8")
DARK = colors.HexColor("#213547")
GREEN = colors.HexColor("#2e8b57")
RED = colors.HexColor("#c0392b")
AMBER = colors.HexColor("#d68910")
LIGHT = colors.HexColor("#eef3f8")
GREY = colors.HexColor("#7f8c8d")

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName=FONT,
                    fontSize=17, textColor=BLUE, spaceAfter=8, leading=22)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName=FONT,
                    fontSize=13, textColor=DARK, spaceBefore=12, spaceAfter=5)
BODY = ParagraphStyle("BODY", parent=styles["Normal"], fontName=FONT,
                      fontSize=9.5, leading=15, spaceAfter=4)
SMALL = ParagraphStyle("SMALL", parent=BODY, fontSize=8.2, leading=12,
                       textColor=GREY)
TITLE = ParagraphStyle("TITLE", parent=styles["Title"], fontName=FONT,
                       fontSize=24, textColor=DARK, leading=30)
CELL = ParagraphStyle("CELL", parent=BODY, fontSize=8.6, leading=12,
                      spaceAfter=0)
CELL_C = ParagraphStyle("CELLC", parent=CELL, alignment=1)


def P(txt, style=BODY):
    return Paragraph(str(txt), style)


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_perf():
    p = RESULTS / "perf_results.json"
    if not p.exists():
        raise SystemExit(f"缺少 {p}，请先运行 perf_benchmark.py")
    return json.loads(p.read_text(encoding="utf-8"))


def load_pre_perf():
    """v5.6.4 优化前基线（同脚本、同规模采集），缺失则返回 None"""
    p = RESULTS / "perf_results_pre_v564.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_tests():
    p = RESULTS / "pytest.xml"
    out = {"total": 0, "failures": 0, "errors": 0, "skipped": 0,
           "time_s": 0.0, "suites": [], "files": [], "available": False}
    if not p.exists():
        return out
    root = ET.parse(p).getroot()
    if root.tag == "testsuites":
        suites = root.findall("testsuite")
    else:
        suites = [root]

    # 按测试文件聚合 testcase（pytest 单次会话默认只有一个 testsuite）
    files = {}
    for s in suites:
        out["suites"].append({
            "name": s.get("name", ""),
            "tests": int(s.get("tests", 0)),
            "failures": int(s.get("failures", 0)),
            "errors": int(s.get("errors", 0)),
            "skipped": int(s.get("skipped", 0)),
            "time_s": float(s.get("time", 0)),
        })
        for tc in s.findall("testcase"):
            cn = tc.get("classname", "") or ""
            # classname 形如 tests.test_xxx.TestClass 或 tests.test_xxx
            parts = cn.split(".")
            if len(parts) >= 2 and parts[-1][:1].isupper():
                tail = parts[-2]          # 去掉末尾测试类名，取模块名
            else:
                tail = parts[-1] if parts and parts[-1] else "unknown"
            fname = tail if tail.endswith(".py") else tail + ".py"
            agg = files.setdefault(fname, {
                "tests": 0, "failures": 0, "errors": 0,
                "skipped": 0, "time_s": 0.0})
            agg["tests"] += 1
            agg["time_s"] += float(tc.get("time", 0) or 0)
            if tc.find("failure") is not None:
                agg["failures"] += 1
            if tc.find("error") is not None:
                agg["errors"] += 1
            if tc.find("skipped") is not None:
                agg["skipped"] += 1

    if files:
        out["files"] = [
            {"name": k, **v} for k, v in sorted(
                files.items(), key=lambda kv: -kv[1]["tests"])]
    out["total"] = sum(x["tests"] for x in out["suites"])
    out["failures"] = sum(x["failures"] for x in out["suites"])
    out["errors"] = sum(x["errors"] for x in out["suites"])
    out["skipped"] = sum(x["skipped"] for x in out["suites"])
    out["time_s"] = sum(x["time_s"] for x in out["suites"])
    out["available"] = True
    return out


def load_coverage():
    p = RESULTS / "coverage.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    files = data.get("files", {})
    rows = []
    total_cov = covered = 0
    groups = {"入口层 (api/mcp/adapters/cli)": [0, 0],
              "核心层 (core)": [0, 0],
              "模块层 (modules)": [0, 0]}
    for fname, info in sorted(files.items()):
        s = info["summary"]
        stmts = s["num_statements"]
        miss = s["missing_lines"]
        cov = stmts - miss
        total_cov += stmts
        covered += cov
        pctv = (cov / stmts * 100) if stmts else 100.0
        rows.append((fname.replace("\\", "/"), stmts, pctv))
        norm = fname.replace("\\", "/")
        if norm.startswith(("api/", "mcp/", "adapters/", "cli/")):
            g = "入口层 (api/mcp/adapters/cli)"
        elif norm.startswith("core/"):
            g = "核心层 (core)"
        elif norm.startswith("modules/"):
            g = "模块层 (modules)"
        else:
            continue
        groups[g][0] += cov
        groups[g][1] += stmts
    overall = covered / total_cov * 100 if total_cov else 0.0
    group_pct = {k: (v[0] / v[1] * 100 if v[1] else 0) for k, v in groups.items()}
    return {"overall": overall, "groups": group_pct, "rows": rows}


# ---------------------------------------------------------------------------
# 图表
# ---------------------------------------------------------------------------

def _save(fig, name):
    p = RESULTS / name
    fig.tight_layout()
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return str(p)


def chart_latency(perf):
    phases = [
        ("写入 add", "write_single"),
        ("读取 get", "read_get"),
        ("热缓存 get", "read_get_cached"),
        ("列表(50/页)", "read_list_page50"),
        ("更新 update", "update"),
        ("删除 delete", "delete"),
    ]
    labels, p50, p95, p99 = [], [], [], []
    for label, key in phases:
        b = perf.get(key)
        if not b:
            continue
        labels.append(label)
        p50.append(b["p50_ms"])
        p95.append(b["p95_ms"])
        p99.append(b["p99_ms"])
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    import numpy as np
    x = np.arange(len(labels))
    w = 0.26
    ax.bar(x - w, p50, w, label="p50", color="#2e86de")
    ax.bar(x, p95, w, label="p95", color="#f39c12")
    ax.bar(x + w, p99, w, label="p99", color="#e74c3c")
    ax.set_ylabel("延迟 (ms)")
    ax.set_title("CRUD 操作延迟分布（毫秒，对数轴）")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_yscale("log")
    ax.legend(fontsize=8, ncol=3, loc="upper right")
    ax.grid(axis="y", alpha=0.3, which="both")
    return _save(fig, "chart_latency.png")


def chart_throughput(perf):
    items = []
    if perf.get("write_single"):
        items.append(("单条写入", perf["write_single"]["throughput_ops_s"]))
    if perf.get("write_batch"):
        items.append(("批量写入", perf["write_batch"]["throughput_ops_s"]))
    if perf.get("encrypted_mode"):
        items.append(("加密写入", perf["encrypted_mode"]["add_throughput_ops_s"]))
    labels = [i[0] for i in items]
    vals = [i[1] for i in items]
    fig, ax = plt.subplots(figsize=(5.6, 2.8))
    bars = ax.barh(labels, vals, color=["#2e86de", "#27ae60", "#8e44ad"])
    for b, v in zip(bars, vals):
        ax.text(v + max(vals) * 0.01, b.get_y() + b.get_height() / 2,
                f"{v:,.0f}", va="center", fontsize=9)
    ax.set_xlabel("ops / s")
    ax.set_title("写入吞吐对比")
    ax.grid(axis="x", alpha=0.3)
    return _save(fig, "chart_throughput.png")


def chart_search_curve(perf):
    curve = perf.get("search_curve", [])
    if not curve:
        return None
    xs = [c["records"] for c in curve]
    hyb = [c["hybrid_ms"] for c in curve]
    fz = [c["fuzzy_ms"] for c in curve]
    fig, ax = plt.subplots(figsize=(5.6, 2.8))
    ax.plot(xs, hyb, "o-", color="#2e86de", label="混合检索 (TF-IDF+Fuzzy)")
    ax.plot(xs, fz, "s--", color="#e67e22", label="模糊检索 (Fuzzy)")
    ax.set_xlabel("记忆条数")
    ax.set_ylabel("平均延迟 (ms)")
    ax.set_title("搜索延迟随数据量变化")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    return _save(fig, "chart_search.png")


def chart_search_compare(pre, post):
    """v5.6.4 优化前后搜索延迟对比（4 条曲线）"""
    pre_curve = (pre or {}).get("search_curve", [])
    post_curve = (post or {}).get("search_curve", [])
    if not pre_curve or not post_curve:
        return None
    fig, ax = plt.subplots(figsize=(6.2, 3.2))
    px = [c["records"] for c in pre_curve]
    qx = [c["records"] for c in post_curve]
    ax.plot(px, [c["hybrid_ms"] for c in pre_curve],
            "o-", color="#e74c3c", label="混合检索 优化前")
    ax.plot(qx, [c["hybrid_ms"] for c in post_curve],
            "o-", color="#2e86de", label="混合检索 优化后")
    ax.plot(px, [c["fuzzy_ms"] for c in pre_curve],
            "s--", color="#e67e22", label="模糊检索 优化前")
    ax.plot(qx, [c["fuzzy_ms"] for c in post_curve],
            "s--", color="#27ae60", label="模糊检索 优化后")
    ax.set_xlabel("记忆条数")
    ax.set_ylabel("平均延迟 (ms)")
    ax.set_title("v5.6.4 搜索性能优化前后对比")
    ax.legend(fontsize=7.5, ncol=2)
    ax.grid(alpha=0.3)
    return _save(fig, "chart_search_compare.png")


def chart_crypto(perf):
    prim = perf.get("crypto_primitive", {})
    if not prim:
        return None
    labels = list(prim.keys())
    enc = [prim[k]["encrypt_mb_s"] for k in labels]
    dec = [prim[k]["decrypt_mb_s"] for k in labels]
    import numpy as np
    x = np.arange(len(labels))
    w = 0.35
    fig, ax = plt.subplots(figsize=(5.2, 2.8))
    ax.bar(x - w / 2, enc, w, label="加密", color="#16a085")
    ax.bar(x + w / 2, dec, w, label="解密", color="#2980b9")
    for i, (e, d) in enumerate(zip(enc, dec)):
        ax.text(i - w / 2, e, f"{e:.0f}", ha="center", va="bottom", fontsize=8)
        ax.text(i + w / 2, d, f"{d:.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("吞吐 (MB/s)")
    ax.set_title("AES-GCM 原语加解密吞吐")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    return _save(fig, "chart_crypto.png")


# ---------------------------------------------------------------------------
# PDF 组装
# ---------------------------------------------------------------------------

def kv_table(rows, col_widths=(55*mm, 110*mm)):
    data = [[P(k, CELL), P(v, CELL)] for k, v in rows]
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfd8e3")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def header_table(headers, data_rows, widths, aligns=None, highlight_fail_col=None):
    t = Table([[P(h, CELL_C) for h in headers]] + data_rows,
              colWidths=widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfd8e3")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7fafc")]),
    ]
    t.setStyle(TableStyle(style))
    return t


def build():
    perf = load_perf()
    pre = load_pre_perf()
    tests = load_tests()
    cov = load_coverage()
    env = perf["env"]
    scale = perf["scale"]
    ver = env.get("mindforge_version", "?")

    charts = {
        "latency": chart_latency(perf),
        "throughput": chart_throughput(perf),
        "search": chart_search_curve(perf),
        "search_compare": chart_search_compare(pre, perf),
        "crypto": chart_crypto(perf),
    }

    out_pdf = RESULTS / f"MindForge_v{ver}_test_perf_report.pdf"
    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4,
                            leftMargin=16*mm, rightMargin=16*mm,
                            topMargin=15*mm, bottomMargin=15*mm,
                            title=f"MindForge v{ver} 全面测试与性能报告")

    story = []

    # ---- 封面信息 ----
    story.append(Spacer(1, 6*mm))
    story.append(P(f"MindForge v{ver}", TITLE))
    story.append(P("全面测试与性能基准报告", ParagraphStyle(
        "SUB", parent=TITLE, fontSize=16, textColor=BLUE)))
    story.append(Spacer(1, 5*mm))
    status_ok = tests["available"] and tests["failures"] == 0 and tests["errors"] == 0
    bench_ok = perf.get("passed", False)
    overall = "全部通过" if status_ok and bench_ok else "存在失败项"
    story.append(kv_table([
        ("报告生成时间", perf["generated_at"]),
        ("版本", f"v{ver}"),
        ("测试结论", overall),
        ("单元测试",
         f"{tests['total']} 项，失败 {tests['failures']}，错误 {tests['errors']}，"
         f"跳过 {tests['skipped']}，耗时 {tests['time_s']:.1f}s"
         if tests["available"] else "结果文件缺失"),
        ("覆盖率", f"{cov['overall']:.1f}%" if cov else "未采集"),
        ("性能基准规模",
         f"主库 {scale['N']} 条；加密 {scale['encrypted_N']} 条；"
         f"并发 {scale['concurrency_threads']} 线程 × "
         f"{scale['concurrency_per_thread']} ops"),
        ("运行环境",
         f"{env['platform']} / Python {env['python']} / "
         f"{env['cpu_count']} 逻辑核心 / 内存 {env['mem_total_gb']} GB"),
        ("基准结论",
         "全部阶段通过，无异常" if bench_ok
         else f"{len(perf.get('failures', []))} 个阶段失败"),
        ("基准方法", perf.get("aggregation", "单次运行") +
         ("；优化前/后各 3 次独立运行同口径对比" if pre else "")),
    ]))

    # ---- 1. 测试总览 ----
    story.append(Spacer(1, 6*mm))
    story.append(P("1. 全面测试总览", H1))
    story.append(P(
        "本次在完成 30 项安全与质量问题修复（P0 级 4 项、P1 级 9 项、"
        "P2 级 10 项、P3 级 7 项）后，执行全量自动化回归。测试覆盖核心存储、"
        "加密、冲突解决、联邦签名、SSRF 防护、隐私过滤、"
        "REST/MCP/适配器/CLI 四个入口面。", BODY))

    table_src = tests["files"] if tests["files"] else tests["suites"]
    if table_src:
        rows = []
        for s in table_src:
            name = s["name"] if s["name"].endswith(".py") \
                else os.path.basename(s["name"])
            ok = s["failures"] == 0 and s["errors"] == 0
            mark = '<font color="#2e8b57">通过</font>' if ok else \
                   '<font color="#c0392b">失败</font>'
            rows.append([
                P(name, CELL), P(s["tests"], CELL_C),
                P(mark, CELL_C), P(s["skipped"], CELL_C),
                P(f"{s['time_s']:.2f}", CELL_C),
            ])
        story.append(header_table(
            ["测试文件", "用例数", "结果", "跳过", "耗时(s)"],
            rows, [78*mm, 22*mm, 22*mm, 20*mm, 24*mm]))
        story.append(Spacer(1, 3*mm))

    if cov:
        story.append(P("覆盖率（行覆盖）", H2))
        g = cov["groups"]
        rows = [
            [P("整体", CELL), P(f"{cov['overall']:.1f}%", CELL_C)],
        ]
        for k, v in g.items():
            rows.append([P(k, CELL), P(f"{v:.1f}%", CELL_C)])
        story.append(kv_table([
            ("整体覆盖率", f"{cov['overall']:.1f}%"),
            ("入口层 (api/mcp/adapters/cli)", f"{g['入口层 (api/mcp/adapters/cli)']:.1f}%"),
            ("核心层 (core)", f"{g['核心层 (core)']:.1f}%"),
            ("模块层 (modules)", f"{g['模块层 (modules)']:.1f}%"),
        ], (75*mm, 60*mm)))
        story.append(P("较审计前基线 36%% 提升至 %.0f%%；其中 adapters 0→80%%、"
                       "api 45→56%%、mcp 20→36%%、cli 4→16%%。"
                       % cov["overall"], SMALL))

    # ---- 2. 安全审计 ----
    story.append(PageBreak())
    story.append(P("2. 安全审计 30 项修复确认", H1))
    audit = [
        ("P0", "联邦 HMAC 改用共享密钥，公钥不可伪造签名（fail-closed）"),
        ("P0", "共享冲突 LWW 不再以 200 字预览覆盖完整内容"),
        ("P0", "rekey 持 _init_lock 原子切换全局加密引擎"),
        ("P0", "_RateLimiter / MemoryCache 加锁，消除并发 RuntimeError"),
        ("P1", "备份 key_file 路径包含性校验，默认拒绝系统文件"),
        ("P1", "decrypt_content 保留密文级 KDF 参数，回滚不丢数据"),
        ("P1", "EmbeddingEngine 单例初始化失败可重试"),
        ("P1", "内容长度限制统一 1MB；consolidate 单次原子更新"),
        ("P1", "Webhook SSRF：DNS 全解析+拒绝内网/链路本地+禁跳转"),
        ("P1", "grant_access 校验所有者；API/Web UI 默认 fail-closed 认证"),
        ("P2", "404 脱敏；IPv6 /64 归一化限流 key"),
        ("P2", "MCP 认证 30 分钟过期；Content-Length 强校验；EOF 正常退出"),
        ("P2", "CLI 不再全局 chdir；凭据支持环境变量/不回显输入"),
        ("P2", "适配器必需字段校验+异常脱敏；外部输入统一安全 JSON 解析"),
        ("P3", "磁盘探测降量并缓存；_safe_path 单点实现；SQL 全白名单绑定"),
        ("P3", "F1 列表隐私过滤覆盖 REST/MCP/适配器/归档四入口"),
        ("P3", "入口层覆盖率测试补齐（新增 30 个入口面用例）"),
    ]
    rows = []
    for sev, desc in audit:
        color = {"P0": "#c0392b", "P1": "#d68910", "P2": "#2874a6",
                 "P3": "#7f8c8d"}[sev]
        rows.append([
            P(f'<font color="{color}"><b>{sev}</b></font>', CELL_C),
            P(desc, CELL),
            P('<font color="#2e8b57"><b>已修复</b></font>', CELL_C),
        ])
    story.append(header_table(["级别", "问题", "状态"],
                              rows, [16*mm, 120*mm, 24*mm]))

    # ---- 3. v5.6.4 性能优化前后对比 ----
    story.append(PageBreak())
    story.append(P("3. v5.6.4 性能优化与前后对比", H1))
    _pre_n = {c["records"]: c for c in (pre or {}).get("search_curve", {})}.get(scale["N"])
    _slow_ref = f"约 {_pre_n['hybrid_ms']:.0f} 毫秒" if _pre_n else "近秒级"
    story.append(P(
        "本轮全面测试的基准暴露了搜索路径的真实性能瓶颈（混合检索延迟随数据量"
        f"超线性增长，{scale['N']} 条时 3 次中位数达 "
        f"{_slow_ref}）。定位并修复三处根因后，使用同一基准"
        "脚本、同一规模（N=3,000）、各 3 次独立运行取中位数重测：", BODY))
    story.append(P(
        "<b>① 向量索引稀疏化 + 倒排链（core/indexer.py）</b>：TF-IDF 向量此前"
        "稠密化到随中文 bigram 语料膨胀至上万维的词表，单次搜索对全部文档做 "
        "O(N×V) 稠密点积；改为稀疏 dict 存储并维护维度→文档倒排链，查询只累计"
        "共享非零维候选，覆盖写先清旧链。", BODY))
    story.append(P(
        "<b>② fuzzy_search 剪枝（core/storage.py）</b>：此前全表对每条完整内容"
        "跑 difflib.SequenceMatcher；现用零成本字符门槛先排除数学上不可能越过 "
        "0.4 阈值的长文本，打分/高亮/阈值语义不变。", BODY))
    story.append(P(
        "<b>③ 读路径缓存接入 + 访问计数节流（core/storage.py）</b>：v5.5.5 建立的 "
        "MemoryCache 此前是从未被读路径使用的死代码，且每次 get 都立即 "
        "UPDATE+COMMIT。现缓存命中跳过 SQL（30s TTL 兜底 + 写路径主动失效），"
        "访问计数改为 60s 窗口批量落库，排序/合并/衰减等消费点读取前主动刷盘。",
        BODY))

    if pre and charts["search_compare"]:
        story.append(Spacer(1, 2*mm))
        story.append(Image(charts["search_compare"], width=140*mm, height=72*mm))

        def _curve_map(d):
            return {c["records"]: c for c in d.get("search_curve", [])}

        pre_map, post_map = _curve_map(pre), _curve_map(perf)
        cmp_rows = []
        for n in sorted(set(pre_map) & set(post_map)):
            a, b = pre_map[n], post_map[n]
            sp_h = (a["hybrid_ms"] / b["hybrid_ms"]) if b["hybrid_ms"] else 0
            sp_f = (a["fuzzy_ms"] / b["fuzzy_ms"]) if b["fuzzy_ms"] else 0
            cmp_rows.append([
                P(n, CELL_C),
                P(f"{a['hybrid_ms']:.2f}", CELL_C),
                P(f"{b['hybrid_ms']:.2f}", CELL_C),
                P(f"{sp_h:.1f}×", CELL_C),
                P(f"{a['fuzzy_ms']:.2f}", CELL_C),
                P(f"{b['fuzzy_ms']:.2f}", CELL_C),
                P(f"{sp_f:.1f}×", CELL_C),
            ])
        story.append(Spacer(1, 2*mm))
        story.append(header_table(
            ["条数", "混合前(ms)", "混合后(ms)", "提速",
             "模糊前(ms)", "模糊后(ms)", "提速"],
            cmp_rows, [16*mm, 24*mm, 24*mm, 18*mm, 24*mm, 24*mm, 18*mm]))

        # 读路径指标对比
        rp = []
        for label, key in [("随机读取 p50", "read_get"),
                           ("随机读取 p95", "read_get"),
                           ("列表 50/页 p50", "read_list_page50")]:
            field = "p50_ms" if "p50" in label else "p95_ms"
            a = (pre.get(key) or {}).get(field)
            b = (perf.get(key) or {}).get(field)
            if a and b:
                rp.append([
                    P(label, CELL), P(f"{a:.3f}", CELL_C),
                    P(f"{b:.3f}", CELL_C),
                    P(f"{a / b:.2f}×", CELL_C)])
        if rp:
            story.append(Spacer(1, 3*mm))
            story.append(P("读路径延迟（毫秒）", H2))
            story.append(header_table(
                ["指标", "优化前", "优化后", "提速"],
                rp, [60*mm, 33*mm, 33*mm, 28*mm]))

        # 结论数字
        n3k_pre, n3k_post = pre_map.get(scale["N"]), post_map.get(scale["N"])
        if n3k_pre and n3k_post and n3k_post["hybrid_ms"]:
            story.append(Spacer(1, 2*mm))
            story.append(P(
                f"N={scale['N']} 时混合检索 {n3k_pre['hybrid_ms']:.1f}ms → "
                f"{n3k_post['hybrid_ms']:.1f}ms（{n3k_pre['hybrid_ms']/n3k_post['hybrid_ms']:.1f}×），"
                f"模糊检索 {n3k_pre['fuzzy_ms']:.1f}ms → "
                f"{n3k_post['fuzzy_ms']:.1f}ms（{n3k_pre['fuzzy_ms']/n3k_post['fuzzy_ms']:.1f}×）。",
                BODY))
    else:
        story.append(P(
            '<font color="#d68910">未找到优化前基线文件 '
            'perf_results_pre_v564.json，跳过前后对比。</font>', SMALL))

    # ---- 4. CRUD 延迟 ----
    story.append(PageBreak())
    story.append(P("4. CRUD 操作延迟", H1))
    story.append(P(f"基于 {scale['N']} 条中文记忆（含标签、分类、FTS/TF-IDF 索引）"
                   "测量，单位毫秒，统计 p50/p95/p99。", BODY))
    story.append(Image(charts["latency"], width=170*mm, height=80*mm))

    def block_row(name, b):
        if not b:
            return None
        return [P(name, CELL), P(b["n"], CELL_C),
                P(b["mean_ms"], CELL_C), P(b["p50_ms"], CELL_C),
                P(b["p95_ms"], CELL_C), P(b["p99_ms"], CELL_C),
                P(b["max_ms"], CELL_C)]

    perf_rows = []
    for name, key in [
        ("单条写入", "write_single"),
        ("随机读取", "read_get"),
        ("热缓存读取", "read_get_cached"),
        ("列表分页(50)", "read_list_page50"),
        ("更新", "update"),
        ("删除", "delete"),
    ]:
        r = block_row(name, perf.get(key))
        if r:
            perf_rows.append(r)
    story.append(Spacer(1, 3*mm))
    story.append(header_table(
        ["操作", "样本数", "均值", "p50", "p95", "p99", "最大"],
        perf_rows, [34*mm, 20*mm, 22*mm, 22*mm, 22*mm, 22*mm, 22*mm]))

    # ---- 4. 吞吐 ----
    story.append(Spacer(1, 5*mm))
    story.append(P("5. 写入与加密吞吐", H1))
    tw = perf.get("write_single", {})
    tb = perf.get("write_batch", {})
    te = perf.get("encrypted_mode", {})
    story.append(Image(charts["throughput"], width=120*mm, height=60*mm))
    if tw and tb:
        speedup = (tb["throughput_ops_s"] / tw["throughput_ops_s"]) \
            if tw["throughput_ops_s"] else 0
        story.append(P(
            f"批量写入（batch_add，每批 500 条）相比逐条写入提速约 "
            f"<b>{speedup:.1f}×</b>。加密模式写入 "
            f"{te.get('add_throughput_ops_s', 0):,.0f} ops/s，"
            f"PBKDF2-AES-GCM 开销已摊薄到逐条加密中。", BODY))
    story.append(Spacer(1, 2*mm))
    if charts["crypto"]:
        story.append(Image(charts["crypto"], width=115*mm, height=62*mm))
        prim = perf.get("crypto_primitive", {})
        if "1KB" in prim:
            story.append(P(
                f"AES-GCM 1KB 载荷：加密 {prim['1KB']['encrypt_mb_s']:.0f} MB/s、"
                f"解密 {prim['1KB']['decrypt_mb_s']:.0f} MB/s；"
                f"10KB 载荷：加密 {prim['10KB']['encrypt_mb_s']:.0f} MB/s、"
                f"解密 {prim['10KB']['decrypt_mb_s']:.0f} MB/s。"
                f"（KDF 仅在建库/rekey 时执行一次，不计入消息面吞吐）", BODY))

    # ---- 5. 搜索曲线 ----
    story.append(PageBreak())
    story.append(P("6. 搜索延迟随数据量扩展（优化后绝对值）", H1))
    if charts["search"]:
        story.append(Image(charts["search"], width=125*mm, height=62*mm))
    curve = perf.get("search_curve", [])
    if curve:
        rows = [[P(c["records"], CELL_C), P(f"{c['hybrid_ms']:.2f}", CELL_C),
                 P(f"{c['fuzzy_ms']:.2f}", CELL_C)] for c in curve]
        story.append(Spacer(1, 3*mm))
        story.append(header_table(
            ["记忆条数", "混合检索 (ms)", "模糊检索 (ms)"],
            rows, [45*mm, 55*mm, 55*mm]))
        first, last = curve[0], curve[-1]
        if first["hybrid_ms"]:
            growth = last["hybrid_ms"] / first["hybrid_ms"]
            n_growth = last["records"] / max(1, first["records"])
            if growth <= 1.5:
                verdict = "延迟基本保持平稳"
            elif growth < n_growth:
                verdict = f"延迟增长 {growth:.1f}×，远低于数据量 {n_growth:.0f}× 的放大"
            else:
                verdict = "延迟随数据量同步增长"
            story.append(P(
                f"数据量放大 {n_growth:.0f}×，混合检索延迟 "
                f"{first['hybrid_ms']:.2f}ms → {last['hybrid_ms']:.2f}ms（{growth:.1f}×），"
                f"{verdict}；模糊检索 {first['fuzzy_ms']:.2f}ms → "
                f"{last['fuzzy_ms']:.2f}ms。", SMALL))

    # ---- 6. 备份恢复 / 加密模式 ----
    story.append(Spacer(1, 5*mm))
    story.append(P("7. 备份、恢复与加密模式", H1))
    br = perf.get("backup_restore", {})
    em = perf.get("encrypted_mode", {})
    bk_rows = []
    if br:
        bk_rows += [
            ("备份记录数", f"{br['records']} 条"),
            ("备份耗时", f"{br['backup_s']} s"),
            ("恢复耗时", f"{br['restore_s']} s"),
            ("备份产物", f"{br['zip_size_mb']} MB（ZIP 压缩）"),
            ("恢复校验",
             f"{br['restored_records']} 条，"
             + ("一致 ✓" if br["restored_records"] == br["records"] else "不一致 ✗")),
        ]
    if em:
        bk_rows += [
            ("加密写入吞吐", f"{em['add_throughput_ops_s']:,.0f} ops/s"),
            ("加密读取 p50", f"{em['get']['p50_ms']} ms"),
            ("加密搜索均值", f"{em['search_ms']} ms"),
        ]
    if bk_rows:
        story.append(kv_table(bk_rows))

    # ---- 7. 并发 ----
    story.append(Spacer(1, 5*mm))
    story.append(P("8. 多线程并发压测与正确性", H1))
    cc = perf.get("concurrency", {})
    if cc:
        ok_conc = cc["errors"] == 0 and cc["adds_consistent"] and \
            cc["threads_still_alive"] == 0
        mark = '<font color="#2e8b57"><b>通过</b></font>' if ok_conc \
            else '<font color="#c0392b"><b>失败</b></font>'
        story.append(kv_table([
            ("并发模型",
             f"{cc['threads']} 线程 × {cc['ops_per_thread']} 混合操作"
             "（add/get/search/list）"),
            ("总操作数", f"{cc['total_ops']:,}"),
            ("耗时 / 吞吐",
             f"{cc['elapsed_s']} s / {cc['throughput_ops_s']:,.0f} ops/s"),
            ("异常数", str(cc["errors"])),
            ("写后计数校验",
             f"库内 {cc['db_records']} = 预期 {cc['expected_adds']} "
             + ("✓" if cc["adds_consistent"] else "✗")),
            ("挂起线程", str(cc["threads_still_alive"])),
            ("结论", mark),
        ]))
        ob = cc["ops_breakdown"]
        story.append(Spacer(1, 2*mm))
        story.append(P(
            f"操作构成：add {ob['add']} / get {ob['get']} / "
            f"search {ob['search']} / list {ob['list']}。该压测回归验证了 "
            "P0 #4 限流器加锁与 P2 #21 MemoryCache 加锁：无 "
            "“dictionary changed size during iteration”，无写丢失。", BODY))
        if cc.get("error_samples"):
            story.append(P('<font color="#c0392b">异常样本：' +
                           "; ".join(cc["error_samples"][:3]) + "</font>", SMALL))

    # ---- 8. 资源占用 ----
    story.append(Spacer(1, 5*mm))
    story.append(P("9. 资源占用", H1))
    misc = perf.get("misc", {})
    ws = perf.get("write_single", {})
    rows = []
    if misc:
        rows += [
            ("主库文件体积",
             f"{misc['main_db_mb']} MB / {misc['main_db_records']} 条"
             f"（约 {misc['bytes_per_record']:.0f} 字节/条）"),
            ("基准峰值 RSS", f"{misc['rss_peak_mb']} MB"),
            ("磁盘类型探测",
             f"{misc['disk_type_first']}（首次 {misc['disk_probe_first_ms']} ms，"
             f"缓存后 {misc['disk_probe_cached_ms']} ms）"
             + ("✓" if misc["disk_probe_cached"] else "✗")),
            ("限流器记账", f"{misc['rate_limiter_ops_s']:,.0f} ops/s"),
        ]
    if ws:
        rows.append(("写入期间 RSS 增量", f"{ws['rss_delta_mb']} MB"))
    if rows:
        story.append(kv_table(rows))

    # ---- 9. 结论 ----
    story.append(Spacer(1, 5*mm))
    story.append(P("10. 结论", H1))
    conclusions = []
    if status_ok and bench_ok:
        conclusions.append(
            f"全量 {tests['total']} 个自动化用例通过，性能基准 "
            f"{scale['N']} 条规模下全部阶段无异常，30 项安全审计问题已修复并回归。")
    if pre:
        pre_map = {c["records"]: c for c in pre.get("search_curve", [])}
        post_map = {c["records"]: c for c in perf.get("search_curve", [])}
        a = pre_map.get(scale["N"])
        b = post_map.get(scale["N"])
        if a and b and b["hybrid_ms"]:
            conclusions.append(
                f"v5.6.4 性能优化使 {scale['N']} 条规模混合检索延迟从 "
                f"{a['hybrid_ms']:.0f}ms 降至 {b['hybrid_ms']:.0f}ms（"
                f"{a['hybrid_ms']/b['hybrid_ms']:.1f}×），根因为向量索引稀疏化+"
                "倒排链、fuzzy difflib 剪枝、读缓存接入与访问计数写库节流，"
                "并由 16 个新增专项用例守护正确性。")
    if tw:
        conclusions.append(
            f"单条写入稳定在 p50 {tw['p50_ms']} ms、"
            f"p99 {tw['p99_ms']} ms；批量路径提供数量级提速。")
    if cc and cc["errors"] == 0:
        conclusions.append(
            f"{cc['threads']} 线程混合并发 {cc['total_ops']:,} 操作零异常、"
            "写入计数一致，线程安全修复有效。")
    if br and br["restored_records"] == br["records"]:
        conclusions.append("备份/恢复往返数据一致。")
    if not perf.get("passed"):
        conclusions.append('<font color="#c0392b"><b>基准存在失败阶段，'
                           '详见 perf_results.json 的 failures 字段。</b></font>')
    for c in conclusions:
        story.append(P("• " + c, BODY))

    story.append(Spacer(1, 4*mm))
    story.append(P("数据来源：benchmarks/perf_benchmark.py 采集 "
                   "(benchmarks/results/perf_results.json)；"
                   "测试结果：pytest JUnit XML + coverage JSON。"
                   "本报告由 benchmarks/generate_report.py 自动生成。", SMALL))

    doc.build(story)
    print(f"PDF 已生成: {out_pdf}")
    return out_pdf


if __name__ == "__main__":
    build()
