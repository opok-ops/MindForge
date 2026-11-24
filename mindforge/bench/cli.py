# -*- coding: utf-8 -*-
"""mindforge-bench CLI。

用法（零成本链路，无 API key）::

    MindForge-bench run                                  # smoke 全链
    MindForge-bench run --backend context_window         # 基线对照
    MindForge-bench report out1.json out2.json           # 对比表

完整链路（LLM 生成 + LLM 判官，与 58.4% 全量口径同源）::

    MindForge-bench run --dataset locomo --data locomo10.json \
        --judge llm --answerer llm --judge-api-key sk-xxx

判官/生成 API key 读取顺序：命令行 > DEEPSEEK_API_KEY > OPENAI_API_KEY。
"""
import argparse
import json
import os
import sys
from typing import List, Optional

from mindforge.bench.backends import BACKEND_REGISTRY
from mindforge.bench.datasets import load_dataset
from mindforge.bench.judges import build_judge
from mindforge.bench.answerers import build_answerer
from mindforge.bench.runner import run_benchmark
from mindforge.bench.report import aggregate_markdown


def _pick_key(cli_val: Optional[str]) -> Optional[str]:
    return (cli_val or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY"))


def _print_progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cmd_run(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="MindForge-bench run")
    p.add_argument("--dataset", default="smoke",
                   choices=["smoke", "locomo"])
    p.add_argument("--data", default=None,
                   help="locomo10.json 路径（--dataset locomo 时必填）")
    p.add_argument("--convs", type=int, default=None,
                   help="LoCoMo 取前 N 个会话（默认全部 10 个）")
    p.add_argument("--backend", default="mindforge",
                   choices=sorted(BACKEND_REGISTRY))
    p.add_argument("--judge", default="deterministic",
                   choices=["deterministic", "llm"])
    p.add_argument("--judge-api-key", default=None)
    p.add_argument("--judge-base-url", default=None)
    p.add_argument("--judge-model", default=None)
    p.add_argument("--answerer", default="extractive",
                   choices=["extractive", "llm"])
    p.add_argument("--answerer-api-key", default=None)
    p.add_argument("--answerer-base-url", default=None)
    p.add_argument("--answerer-model", default=None)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--encrypted", action="store_true",
                   help="MindForge 后端启用端侧加密")
    p.add_argument("--json", default=None, help="报告输出路径")
    args = p.parse_args(argv)

    items = load_dataset(args.dataset, data_path=args.data,
                         convs=args.convs)
    _print_progress(f"数据集 {args.dataset}: {len(items)} 问")

    backend_cls = BACKEND_REGISTRY[args.backend]
    backend = backend_cls(encrypted=args.encrypted)
    judge = build_judge(
        args.judge,
        api_key=_pick_key(args.judge_api_key),
        judge_base_url=args.judge_base_url,
        judge_model=args.judge_model)
    answerer = build_answerer(
        args.answerer,
        api_key=_pick_key(args.answerer_api_key),
        base_url=args.answerer_base_url,
        model=args.answerer_model)

    try:
        report = run_benchmark(
            items, backend, answerer, judge,
            k=args.k, seed=args.seed, progress=_print_progress,
            dataset_id=args.dataset,
            args_decl={"backend": args.backend, "answerer": args.answerer,
                       "judge": args.judge, "k": args.k, "seed": args.seed,
                       "encrypted": args.encrypted})
    finally:
        backend.close()
        judge.close()
        answerer.close()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        _print_progress(f"报告已写入 {args.json}")
    else:
        print(json.dumps(report["aggregate"], ensure_ascii=False, indent=1))
    return 0


def cmd_report(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="MindForge-bench report")
    p.add_argument("runs", nargs="+", help="评测 JSON 路径（>=1）")
    p.add_argument("--out", default=None, help="Markdown 输出路径")
    args = p.parse_args(argv)

    md = aggregate_markdown(args.runs)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        _print_progress(f"对比表已写入 {args.out}")
    else:
        print(md)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["run"]
    if argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "run":
        return cmd_run(argv[1:])
    if argv[0] == "report":
        return cmd_report(argv[1:])
    print(f"未知子命令: {argv[0]}（可选 run | report）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
