# -*- coding: utf-8 -*-
"""mindforge.bench — 标准化评测工具集（v6.7.0）

开放横向对比 Agent 记忆系统的统一基准。三段式解耦管道：

    Backend（记忆检索） → Answerer（答案生成） → Judge（评分）

设计原则：
- 中立性：MindForge 只是内置 backend 之一，任何实现按 BaseBackend
  协议接入即为同等地位的参赛者。
- 零成本默认：smoke 数据集 + deterministic 判官全程无 API 调用，
  pip install 后即可复现。
- 口径同源：LLM 判官 1-10 分、>=9 记正确——与 MindForge v6.5.12
  LoCoMo 全量报告（58.4%）同一口径，内部实验可复现。
- 环境声明：每份报告自带版本/平台/seed/数据集声明，保证可复现性。

接口层只依赖协议（BaseBackend/BaseAnswerer/BaseJudge），不依赖
MindForge 实现层——为未来拆分为独立基准包预留结构。
"""
from mindforge.bench.backends import (
    BaseBackend, MindForgeBackend, ContextWindowBackend, BACKEND_REGISTRY)
from mindforge.bench.datasets import (
    load_dataset, SMOKE_SESSIONS, SMOKE_QUESTIONS, LOCOMO_CATEGORY_MAP)
from mindforge.bench.judges import BaseJudge, DeterministicJudge, LLMJudge
from mindforge.bench.answerers import (
    BaseAnswerer, ExtractiveAnswerer, OpenAICompatAnswerer)
from mindforge.bench.runner import run_benchmark
from mindforge.bench.report import aggregate_markdown

__all__ = [
    "BaseBackend", "MindForgeBackend", "ContextWindowBackend",
    "BACKEND_REGISTRY",
    "load_dataset", "SMOKE_SESSIONS", "SMOKE_QUESTIONS", "LOCOMO_CATEGORY_MAP",
    "BaseJudge", "DeterministicJudge", "LLMJudge",
    "BaseAnswerer", "ExtractiveAnswerer", "OpenAICompatAnswerer",
    "run_benchmark", "aggregate_markdown",
]
