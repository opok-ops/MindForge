# -*- coding: utf-8 -*-
"""
MindForge HTML 数据报告生成器
============================

读取：
  benchmarks/results/perf_results.json   性能基准结果（perf_benchmark.py 产出）
  benchmarks/results/coverage.json       coverage.py / pytest-cov 覆盖率快照（可选）

输出：
  benchmarks/results/mindforge_report.html（自包含，无外部资源依赖）

用法：
    python benchmarks/html_report.py
"""

import html
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mindforge.core.version import __version__

RESULTS = ROOT / "benchmarks" / "results"
PERF = RESULTS / "perf_results.json"
COVERAGE = RESULTS / "coverage.json"
PYTEST_XML = RESULTS / "pytest.xml"
OUT = RESULTS / "mindforge_report.html"

def test_counts(xml):
    if not xml:
        return {"passed": "—", "skipped": "—", "failed": "—"}
    tests = int(xml.get("tests", 0))
    skipped = int(xml.get("skipped", 0))
    failures = int(xml.get("failures", 0))
    errors = int(xml.get("errors", 0))
    return {
        "passed": tests - skipped - failures - errors,
        "skipped": skipped,
        "failed": failures + errors,
    }


def fmt(v, digits=3):
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:.{digits}f}"
    return html.escape(str(v))


def row(label, value, unit=""):
    return f"<tr><td class='k'>{html.escape(label)}</td><td class='v'>{fmt(value)} {html.escape(unit)}</td></tr>"


def table(rows):
    return "<table>" + "".join(row(*r) for r in rows) + "</table>"


def load_perf():
    return json.loads(PERF.read_text(encoding="utf-8"))


def load_coverage():
    if not COVERAGE.exists():
        return None
    data = json.loads(COVERAGE.read_text(encoding="utf-8"))
    return data.get("totals", {}).get("percent_covered")


def load_pytest_xml():
    if not PYTEST_XML.exists():
        return None
    root = ET.parse(PYTEST_XML).getroot()
    suite = root.find("testsuite")
    return suite.attrib if suite is not None else None


def main():
    perf = load_perf()
    env = perf.get("env", {})
    coverage = load_coverage()
    xml = load_pytest_xml()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ws = perf.get("write_single", {})
    wb = perf.get("write_batch", {})
    rg = perf.get("read_get", {})
    rgc = perf.get("read_get_cached", {})
    rl = perf.get("read_list_page50", {})
    upd = perf.get("update", {})
    dele = perf.get("delete", {})
    enc = perf.get("encrypted_mode", {})
    backup = perf.get("backup_restore", {})
    conc = perf.get("concurrency", {})
    misc = perf.get("misc", {})
    curve = perf.get("search_curve", [])

    curve_rows = "".join(
        f"<tr><td>{c.get('records')}</td><td>{fmt(c.get('hybrid_ms'))} ms</td>"
        f"<td>{fmt(c.get('fuzzy_ms'))} ms</td></tr>"
        for c in curve
    )

    counts = test_counts(xml)
    cards = f"""
    <div class='cards'>
      <div class='card ok'><div class='num'>{counts['passed']}</div><div class='lbl'>测试通过</div></div>
      <div class='card skip'><div class='num'>{counts['skipped']}</div><div class='lbl'>环境性跳过</div></div>
      <div class='card bad'><div class='num'>{counts['failed']}</div><div class='lbl'>失败 / 错误</div></div>
      <div class='card'><div class='num'>{fmt(coverage, 2) if coverage is not None else '—'}</div><div class='lbl'>覆盖率 %</div></div>
    </div>"""

    sections = [
        ("写入", table([
            ("单条写入吞吐", ws.get("throughput_ops_s"), "ops/s"),
            ("单条写入 p50", ws.get("p50_ms"), "ms"),
            ("单条写入 p95", ws.get("p95_ms"), "ms"),
            ("单条写入 p99", ws.get("p99_ms"), "ms"),
            ("批量写入吞吐", wb.get("throughput_ops_s"), "ops/s"),
            ("批量分片大小", wb.get("chunk_size"), ""),
        ])),
        ("读取 / 更新 / 删除", table([
            ("随机读取 p50", rg.get("p50_ms"), "ms"),
            ("缓存读取 p50", rgc.get("p50_ms"), "ms"),
            ("分页列表 p50", rl.get("p50_ms"), "ms"),
            ("更新 p50", upd.get("p50_ms"), "ms"),
            ("删除 p50", dele.get("p50_ms"), "ms"),
        ])),
        ("检索延迟曲线", f"<table><tr><th>记录数</th><th>混合检索</th><th>模糊检索</th></tr>{curve_rows}</table>"),
        ("加密模式开销", table([
            ("加密写入吞吐", enc.get("add_throughput_ops_s"), "ops/s"),
            ("加密写入 p50", enc.get("add", {}).get("p50_ms"), "ms"),
            ("加密读取 p50", enc.get("get", {}).get("p50_ms"), "ms"),
            ("加密检索耗时", enc.get("search_ms"), "ms"),
        ])),
        ("备份 / 恢复", table([
            ("备份耗时", backup.get("backup_s"), "s"),
            ("恢复耗时", backup.get("restore_s"), "s"),
            ("备份体积", backup.get("zip_size_mb"), "MB"),
            ("恢复记录数", backup.get("restored_records"), ""),
        ])),
        ("并发（8 线程混合）", table([
            ("总操作数", conc.get("total_ops"), ""),
            ("吞吐", conc.get("throughput_ops_s"), "ops/s"),
            ("错误数", conc.get("errors"), ""),
            ("计数一致", "是" if conc.get("adds_consistent") else "否", ""),
        ])),
        ("资源占用", table([
            ("库记录数", misc.get("main_db_records"), ""),
            ("库文件体积", misc.get("main_db_mb"), "MB"),
            ("单条平均字节", misc.get("bytes_per_record"), "B"),
            ("RSS 峰值", misc.get("rss_peak_mb"), "MB"),
            ("磁盘探测命中缓存", "是" if misc.get("disk_probe_cached") else "否", ""),
        ])),
    ]

    body_sections = "".join(
        f"<section><h2>{html.escape(title)}</h2>{content}</section>"
        for title, content in sections
    )

    xml_note = ""
    if xml:
        xml_note = (f"JUnit 快照：{xml.get('tests')} 项测试，"
                    f"失败 {xml.get('failures')}，跳过 {xml.get('skipped')}（{xml.get('timestamp', '')[:10]}）。")

    failures = perf.get("failures", [])
    fail_html = ("<p class='ok-text'>基准无失败项。</p>" if not failures
                 else "<ul>" + "".join(f"<li>{html.escape(str(f))}</li>" for f in failures) + "</ul>")

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MindForge v{html.escape(__version__)} 数据报告</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f4f6fb; color: #1f2430; padding: 24px; }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  header h1 {{ font-size: 1.6rem; margin-bottom: 8px; }}
  header p {{ color: #6b7280; font-size: 0.9rem; }}
  .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 20px 0; }}
  .card {{ background: #fff; border-radius: 14px; padding: 18px; box-shadow: 0 1px 4px rgba(20,30,60,.08); }}
  .card .num {{ font-size: 1.7rem; font-weight: 700; }}
  .card .lbl {{ color: #6b7280; font-size: 0.8rem; margin-top: 4px; }}
  .card.ok .num {{ color: #16a34a; }} .card.skip .num {{ color: #d97706; }} .card.bad .num {{ color: #dc2626; }}
  section {{ background: #fff; border-radius: 14px; padding: 18px 20px; margin-bottom: 16px;
            box-shadow: 0 1px 4px rgba(20,30,60,.08); }}
  h2 {{ font-size: 1.05rem; margin-bottom: 12px; color: #302b63; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid #eef0f5; text-align: left; }}
  th {{ color: #6b7280; font-weight: 600; }}
  td.k {{ color: #374151; }} td.v {{ font-variant-numeric: tabular-nums; }}
  .meta {{ color: #6b7280; font-size: 0.8rem; margin-top: 10px; }}
  .ok-text {{ color: #16a34a; }}
  footer {{ margin-top: 24px; color: #9ca3af; font-size: 0.78rem; text-align: center; }}
</style>
</head>
<body>
<div class='wrap'>
  <header>
    <h1>🧠 MindForge 性能与测试数据报告</h1>
    <p>版本 v{html.escape(__version__)} · Python {html.escape(str(env.get('python', '—')))} ·
       {html.escape(str(env.get('platform', '—')))} · 生成于 {now}</p>
  </header>
  {cards}
  <section><h2>基准结论</h2>{fail_html}<p class='meta'>{html.escape(xml_note)}</p></section>
  {body_sections}
  <footer>Generated by benchmarks/html_report.py · 数据来自 perf_results.json 与 coverage.json</footer>
</div>
</body>
</html>"""

    cleaned = [line.rstrip() if line.strip() else "" for line in doc.splitlines()]
    OUT.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
    print(f"HTML 报告已生成: {OUT}")


if __name__ == "__main__":
    main()
