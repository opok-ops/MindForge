# MindForge

**生产级 AI Agent 终身记忆系统，本地优先。**

> **维护者** — **小顾**，2008 年出生 · GitHub: [opok-ops](https://github.com/opok-ops) · 抖音: [shidianduo3116](https://v.douyin.com/ZIsw2VpuT9o/) · 小红书: [4406811524](https://xhslink.cn/o/3m71WBRPD17) · 反馈: [2638895480@qq.com](mailto:2638895480@qq.com)

MindForge 为 AI Agent 提供持久、结构化的长期记忆：四层记忆生命周期、知识图谱、混合检索、端到端加密，以及 MCP / CLI / REST 统一接口。所有数据运行并存储在本机。

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-5.8.8-green.svg)](https://github.com/opok-ops/MindForge/releases)
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
- **开放接口。** MCP Server（33 个工具）、Python SDK、带 Shell 补全的 CLI，以及面向非 Python 应用的 REST API。

## 快速开始

源码安装后，几分钟内即可写入与检索记忆：

```bash
git clone https://github.com/opok-ops/MindForge.git
cd MindForge
pip install -e .
```

```python
from MindForge import MindForge

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

## 核心特性

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

- **MCP Server** — 33 个工具，分钟级接入 Claude Code、OpenClaw 或任意 MCP 客户端。
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
from MindForge.adapters import ClaudeCodeAdapter

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

- [ ] v6.0 — 多 Agent 协作端口、记忆预览流式传输
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