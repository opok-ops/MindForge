# MindForge

**生产级 AI Agent 终身记忆系统，本地优先。**

> **维护者** — **顾冯雨**，2008 年 1 月 10 日出生 · GitHub: [opok-ops](https://github.com/opok-ops) · 抖音: [shidianduo3116](https://www.douyin.com/user/shidianduo3116) · 小红书: [4406811524](https://xhslink.cn/o/3m71WBRPD17) · 反馈: [2638895480@qq.com](mailto:2638895480@qq.com)

MindForge 为 AI Agent 提供持久、结构化的长期记忆：四层记忆生命周期、知识图谱、混合检索、端到端加密，以及 MCP / CLI / REST 统一接口。所有数据运行并存储在本机。

> **开发历程** — 一个人，七个多月，从 v1.0（2026 年 3 月）到 v6.3.5：没有团队，没有融资，1210 项测试全绿。[读完整故事 →](https://opok-ops.github.io/MindForge/#developer)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-6.3.5-green.svg)](https://github.com/opok-ops/MindForge/releases)
[![Stars](https://img.shields.io/github/stars/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/stargazers)
[![Forks](https://img.shields.io/github/forks/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/forks)
[![Open Issues](https://img.shields.io/github/issues/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/issues)
[![Last Commit](https://img.shields.io/github/last-commit/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/commits/master)
[![CI](https://github.com/opok-ops/MindForge/actions/workflows/ci.yml/badge.svg)](https://github.com/opok-ops/MindForge/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-pages-blue.svg)](https://opok-ops.github.io/MindForge/)

[English](README.md) · [中文文档](README.zh-CN.md) · [在线体验](https://opok-ops.github.io/MindForge/) · [完整文档](docs/) · [讨论区](https://github.com/opok-ops/MindForge/discussions)

---

## 项目简介

依赖云端的 Agent 记忆方案会引入延迟、成本与隐私权衡。MindForge 采取相反的设计：记忆在本机计算与存储、落盘加密，并通过现有 Agent 已熟悉的统一接口对外提供。

核心设计原则：

- **本地优先。** 零云端依赖。存储基于 SQLite + FTS5（WAL 模式），单文件携带，可随环境迁移。
- **结构化记忆。** 四层模型（感官 / 短期 / 长期 / 永久），依据艾宾浩斯遗忘曲线自动晋升与衰减。
- **检索质量。** 六条互补检索通路融合为一份排序结果，而非单一向量索引。
- **默认加密。** 落盘 AES-256-GCM + PBKDF2 密钥派生；支持导出受信任的加密备份。
- **开放接口。** MCP Server（87 个工具）、Python SDK、带 Shell 补全的 CLI，以及面向非 Python 应用的 REST API。

## 快速开始

源码安装后，几分钟内即可写入与检索记忆：

```bash
git clone https://github.com/opok-ops/MindForge.git
cd MindForge
pip install -e .               # 零 ML 依赖：三路检索（TF-IDF + FTS5 + 模糊）
pip install -e ".[embedding]"  # + 向量通路 → 完整六路混合检索
```

> 未安装 `embedding` 扩展时，MindForge 以零依赖模式运行——功能完整，但换词表述的查询可能无法召回（无语义通路）。安装后首次向量检索会下载一个轻量 sentence-transformers 模型。

```python
from mindforge import MindForge

m = MindForge(db_path="./data/memory.db", encrypted=False)
m.add("用户偏好带类型提示的 Python 代码", category="preferences", importance="HIGH")
result = m.search("编码偏好")
print(result.chunks[0].content)
```

命令行：

```bash
MindForge add "用户喜欢猫" --category preferences --importance HIGH
MindForge search "用户喜欢什么"
MindForge stats
```

## 最新动态

v6.1.3 – v6.3.5 七天连发（2026-11-01 → 11-07），由 **1210 项全绿测试**护航。

- **v6.3.5 — 记忆压缩与自适应上下文**（2026-11-07）。两项新能力：MemoryCompressor 聚类近重复记忆（TF-IDF 余弦）并替换为可检索的摘要，`decompress` 一步还原——已压缩源在全部检索路径不再独立出现；STRICT 隐私记忆永不参与。AdaptiveContextEngine 按会话查询模式动态调整相关度阈值（探索模式降低、聚焦模式升高、恒有界）。门面方法 + CLI 四命令（`compress` / `compress-list` / `decompress` / `adaptive-search`）。同版修复压缩写路径草稿的五项集成缺陷（裸 SQL 绕过 FTS/嵌入/审计，含加密库明文直插安全缺陷）与关键词检索除零。
- **v6.3.2 — 可解释性全接口对齐**（2026-11-06）。检索得分分解从 CLI/Python 门面扩展到全部三层接口：MCP `memory_search` 新增 `explain` 参数、REST `/api/search` 支持 `?explain=1`——每条结果的四路召回分、胜出路径、融合明细随处可得；不请求时零开销且响应与旧版逐字节一致。
- **v6.3.1 — 加密系统升级：scrypt KDF**（2026-11-06）。默认密钥派生从 PBKDF2 升级为 scrypt（N=2^16, r=8, p=1，64 MiB 内存墙）：GPU/ASIC 无法再并行离线暴破，单次派生约 0.3s。新密钥文件与加密导出默认 scrypt；全部历史密钥文件（PBKDF2 600k / 60k / 极旧格式）解密兼容不变，`rekey` 一步迁移存量库。同版修复 P0：门面 `rekey()` 自 v5.8.13 起因死导入路径不可用；cryptography 升至 ≥50.0.2。
- **v6.3.0 — 检索可解释性**（`search --explain`）。混合检索不再是黑盒：每条结果可携带完整得分分解——四条召回路（TF-IDF / FTS5 / 模糊 / 向量）各自的原始分、胜出路径、文本+向量双命中时的融合权重。调试「这条记忆为什么排第一」「那条为什么没召回」，直接看数据。关闭 explain 时热路径零开销。
- **v6.2.0 — 增长治理与可复现基准**。审计日志装上刹车：搜索审计 60 秒去抖（合规类事件不采样不裁剪）、`audit-prune` 裁剪命令、doctor 第 7 项增长检查。同时收编 `benchmarks/repro_benchmark.py`——那套杀死「加密写入更快」基准伪影的方法学：同 N 交替对照、每类查询 50 轮 P50/P95 分位、8 线程并发压测、孤儿修复验证。
- **v6.1.3 — 数据完整性自愈**。GDPR 擦除覆盖派生数据：嵌入、笔记、复习计划、演变历史与图谱关系（此前五张表在「被遗忘权」行使后残留）。`doctor` 新增孤儿数据检测与一键修复（`--fix`），清理历史级联删除留下的失效引用与孤儿行，`dry_run` 可先评估影响面。
- **v6.1.1 — 跟进闭环**。记忆不再只等着被查——到点主动催办。跟进事项支持自动触发提醒（MCP 工具增至 87 个）。

## 核心特性

**v6.0.0 — 多 Agent 空间与 6G 预览（2026-10-31）**

- **多 Agent 记忆空间** — 三级角色（owner / editor / reader）的共享空间、last-write-wins
  冲突仲裁，以及 PRIVATE / STRICT 隐私护栏。本版由实验性能力升级为正式支持能力，
  通过 `multi_agent` facade、CLI `space-*`、MCP `space_*` 与 v6 端口注册表
  （状态 `AVAILABLE`）对外提供。
- **6G AI 记忆预览** — 预览端口的四项能力均由占位实现补全为可用功能：与稳定库并行的
  Preview Channel（独立 SQLite 预览库，不污染主库）、上下文蒸馏（`distill_context`
  按 HIGH / MEDIUM / LOW 分层并施加 Token 预算）、增量索引统计（写入即索引，提供近实时
  召回指标）、单记忆时间线（版本历史 + 演变记录 + 审计足迹）。通过 CLI `preview-*`、
  REST `/api/preview/*`、MCP `preview_*` 对外提供。
- **Agent 工作流工具集** — 八项覆盖记忆全生命周期的能力：对话历史导入（冷启动）、
  时间线可视化、可分享技能包、记忆演变、按结果学习、跨模型迁移的记忆护照、
  可解释的有原则遗忘，以及跟进事项闭环。八项能力在 CLI、REST、MCP 三层接口上保持一致暴露。
- **v6 端口注册表** — `v6_status()` 报告各项能力的就绪状态，`v6_port(capability)`
  按能力注入可直接使用的实例（多 Agent 适配器或记忆预览），使调用方依赖端口而非具体模块。

**检索**

- **六路混合检索** — 向量、FTS5、TF-IDF、模糊、查询扩展与 Cross-Encoder 重排，融合为单一排序结果。
- **知识图谱** — 自动实体与关系提取、路径查找、关联推理召回。
- **加权召回** — 覆盖率、重要度、访问频次、时间衰减多因子打分，而非单纯的相似度排序。

**记忆生命周期**

- **四层记忆** — 感官缓冲、短期、长期与永久四层，艾宾浩斯衰减 + 定期巩固。
- **冲突检测** — 反义词对、属性值不一致、时间线冲突三类检测，并自动衰减。
- **技能抽取** — 从记忆聚类中沉淀可复用的槽位、步骤与触发词。

**隐私与安全**

- **隐私引擎** — 四级隔离（PUBLIC / INTERNAL / PRIVATE / STRICT），细粒度访问控制。
- **落盘加密** — AES-256-GCM + PBKDF2-SHA256 密钥派生。
- **加密导出 / 导入** — `export-json --password` 与 `import-json --password` 生成加密备份，文件泄露也无法读取（v5.7.3 新增）。

**集成**

- **MCP Server** — 87 个工具，可直接接入 Claude Code、OpenClaw 或任意 MCP 客户端。
- **联邦记忆** — Agent 间 P2P 共享，含信任等级、ACL 与冲突解决。
- **REST API** — 面向非 Python 应用的标准 HTTP 接口。
- **CLI** — 200+ 命令，支持 bash / zsh / fish 自动补全。
- **多后端 Embedding** — sentence-transformers、OpenAI、Ollama 或自定义 HTTP 端点。

## 系统架构

```
┌──────────────────────────────────────────────────────┐
│  认知层  人格引擎 · 知识图谱 · 记忆演化 · 联邦记忆    │
├──────────────────────────────────────────────────────┤
│  功能层  召回引擎 · 混合检索 · 意图路由 · 冲突检测    │
│          技能抽取 · 会话焦点                           │
├──────────────────────────────────────────────────────┤
│  核心层  存储 (SQLite+FTS5) · 索引 · 加密 · 查询     │
├──────────────────────────────────────────────────────┤
│  适配层  OpenClaw · Claude Code · MCP · REST · CLI   │
└──────────────────────────────────────────────────────┘
```

### 四层记忆模型

| 层级 | 保留时长 | 容量 | 用途 |
|------|----------|------|------|
| 感官记忆 | 秒~分钟 | ~50 条 | 输入缓冲，噪声过滤 |
| 短期记忆 | 小时~天 | ~100 条 | 工作记忆，当前会话 |
| 长期记忆 | 周~月 | 无上限 | 已巩固的语义记忆 |
| 永久记忆 | 永久 | 无上限 | 核心知识、偏好、关键经验 |

高价值条目通过巩固机制随时间强化，低价值条目自然衰减。

## 性能基准

测试环境：i7-12700H / 32GB RAM / NVMe SSD / Python 3.12 / SQLite WAL 模式。

| 操作 | 1K 条 | 10K 条 | 100K 条 |
|------|-------|--------|---------|
| 写入（明文） | 0.3 ms | 0.5 ms | 0.9 ms |
| 写入（加密） | 0.8 ms | 1.2 ms | 2.1 ms |
| TF-IDF 检索 P50 | 4 ms | 12 ms | 38 ms |
| FTS5 检索 P50 | 0.4 ms | 0.9 ms | 3.2 ms |
| Cross-Encoder 重排 P50 | 5 ms | 15 ms | 45 ms |
| 巩固 100 条 | 45 ms | 120 ms | 580 ms |

检索精度（500 条多领域记忆，50 条标注查询）：

| 指标 | TF-IDF | +FTS5+Fuzzy | +Cross-Encoder | +查询扩展+向量 |
|------|--------|-------------|----------------|---------------|
| MRR@10 | 0.62 | 0.71 | **0.82** | **0.85** |
| NDCG@10 | 0.60 | 0.69 | **0.80** | **0.84** |
| Recall@10 | 0.70 | 0.80 | **0.88** | **0.92** |

## 集成方式

**Claude Code**

```python
from mindforge.adapters import ClaudeCodeAdapter

adapter = ClaudeCodeAdapter.from_env()
adapter.remember("用户偏好简洁代码风格", ["preferences"])
context = adapter.get_context("数据库优化")
```

**MCP Server**

```bash
MindForge-mcp --db-path ./data/memory.db
```

**REST API**

```bash
MindForge serve --api --port 9000
# GET  /api/memories
# POST /api/memories
# GET  /api/search?q=...
# GET  /api/stats
```

**OpenClaw**

```yaml
memory:
  adapter: MindForge
  adapter_config:
    db_path: ~/.MindForge/data/memory.db
    encrypted: true
    auto_consolidate: true
```

## 安全说明

- **加密数据库**：存在密钥文件时，需设置 `MINDFORGE_PASSWORD` 以便进程解锁存储。dsh-mindforge 桥接及一切非交互集成（MCP / REST API / 脚本）访问加密存储时均需该变量。
- **API 认证**：配置 `MINDFORGE_API_KEY` 后，未声明的调用者视为 `anonymous`，仅可访问 PUBLIC 记忆；全部读写路径携带操作者身份用于授权与审计。
- **日志脱敏**：访问日志剥离搜索词与记忆 ID。
- **升级提示**：v5.5.7 已移除实验性 HMAC-XOR 降级加密路径。曾启用该开关创建的密文无法再解密，升级前请先备份数据库。

完整安全策略见 [SECURITY.md](SECURITY.md)。

## 路线图

- [x] **v6.0** — 多 Agent 记忆空间与 6G AI 记忆预览均由实验性端口升级为正式支持能力，并同步落地八项 Agent 工作流功能（2026-10-31 发布）
- [x] **v6.1 – v6.3** — 信任与质量：检索可解释性（`search --explain`）、doctor 数据自愈、GDPR 全量擦除、审计增长治理、可复现基准（2026-11-05 发布）
- [ ] 向量数据库后端（Qdrant / Chroma）
- [ ] Web UI 仪表盘
- [ ] 插件生态

## 文档

- [完整文档](docs/)
- [更新日志](CHANGELOG.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)

## 许可证

[MIT](LICENSE) — Copyright (c) 2026 MindForge Project。如果 MindForge 对你有用，欢迎在 GitHub 上点亮 Star，这会让项目保持活跃与可见。
