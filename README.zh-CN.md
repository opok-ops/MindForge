# MindForge

**生产级 AI Agent 终身记忆系统**

四层记忆架构 · 知识图谱 · 本地优先 · AES-256-GCM 加密 · 联邦 P2P · MCP Server

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-5.7.2-green.svg)](https://github.com/opok-ops/MindForge)
[![Stars](https://img.shields.io/github/stars/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/stargazers)
[![Forks](https://img.shields.io/github/forks/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/forks)
[![Open Issues](https://img.shields.io/github/issues/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/issues)
[![Last Commit](https://img.shields.io/github/last-commit/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/commits/master)
[![CI](https://github.com/opok-ops/MindForge/actions/workflows/ci.yml/badge.svg)](https://github.com/opok-ops/MindForge/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-pages-blue.svg)](https://opok-ops.github.io/MindForge/)

> 🌐 [English](README.md) | 🌐 [Live Demo](https://opok-ops.github.io/MindForge/) | 📖 [完整文档](docs/) | 💬 [讨论区](https://github.com/opok-ops/MindForge/discussions) | ⭐ [版本发布](https://github.com/opok-ops/MindForge/releases) | 📝 [更新日志](CHANGELOG.md)

**如果 MindForge 为你节省了时间，请点亮 Star ⭐ —— 这是对独立开发者最好的支持！**

---

## 为什么选择 MindForge？

大多数 AI Agent 记忆方案依赖云端、结构扁平、检索缓慢。MindForge 为你的 Agent 提供**类人记忆**，完全在本地运行。

| 特性 | MindForge | Mem0 | Letta | Zep |
|------|-----------|------|-------|-----|
| **本地优先** | ✅ 零云端依赖 | ❌ 依赖云端 | ❌ 依赖云端 | ❌ 依赖云端 |
| **加密** | ✅ AES-256-GCM | ❌ | ❌ | ⚠️ 有限 |
| **记忆模型** | ✅ 四层 + 艾宾浩斯 | ❌ 扁平 | ❌ 分块 | ❌ 时序 |
| **检索策略** | ✅ 六路混合 | 仅向量 | 仅向量 | 仅向量 |
| **联邦 P2P** | ✅ ACL + 冲突解决 | ❌ | ❌ | ❌ |
| **知识图谱** | ✅ 内置 | ❌ | ❌ | ✅ |
| **MCP Server** | ✅ 33 个工具 | ❌ | ❌ | ❌ |
| **CLI** | ✅ 200+ 命令 | ❌ | ❌ | ❌ |
| **开源协议** | MIT | Apache 2.0 | Apache 2.0 | Apache 2.0 |

---

## 为什么值得 Star ⭐？

- **本地优先、隐私安全** —— 记忆永不离开你的设备，可选 AES-256-GCM 静态加密
- **永久免费** —— MIT 协议，可自托管，零云依赖
- **原生 MCP** —— 33 个 MCP 工具，分钟级接入 Claude Code、OpenClaw 或任意 MCP 客户端
- **独立开发者维护** —— 每一颗 Star 都是对本地优先 AI 记忆方向最真实的认可

---

## 30 秒快速开始

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
print(result.chunks[0].content)  # → 用户偏好带类型提示的 Python 代码
```

**命令行：**
```bash
MindForge add "用户喜欢猫" --category preferences --importance HIGH
MindForge search "用户喜欢什么"
MindForge stats
```

---

## 架构

```
┌──────────────────────────────────────────────────────┐
│  认知层  人格引擎 · 知识图谱 · 记忆演化 · 联邦记忆    │
├──────────────────────────────────────────────────────┤
│  功能层  召回引擎 · 混合检索 · 意图路由 · 冲突检测    │
│          技能抽取 · 会话焦点                            │
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

记忆基于**艾宾浩斯遗忘曲线**向上传播——高价值条目随时间强化，低价值条目自然衰减。

---

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

**检索精度**（500 条多领域记忆，50 条标注查询）：

| 指标 | TF-IDF | +FTS5+Fuzzy | +Cross-Encoder | +查询扩展+向量 |
|------|--------|-------------|----------------|---------------|
| MRR@10 | 0.62 | 0.71 | **0.82** | **0.85** |
| NDCG@10 | 0.60 | 0.69 | **0.80** | **0.84** |
| Recall@10 | 0.70 | 0.80 | **0.88** | **0.92** |

---

## 核心特性

- **六路混合检索** — 向量 + FTS5 + TF-IDF + Fuzzy + 查询扩展 + Cross-Encoder 重排
- **知识图谱** — 自动实体/关系提取、路径查找、关联推理召回
- **隐私引擎** — 四级隔离（PUBLIC/INTERNAL/PRIVATE/STRICT）+ AES-256-GCM + PBKDF2-SHA256
- **联邦记忆** — P2P 多 Agent 共享，信任等级、细粒度 ACL、冲突解决
- **意图路由** — 三层路由（正则→关键词→LLM 兜底），10+ 业务意图分类
- **冲突检测** — 反义词对、属性值不一致、时间线冲突 + 自动衰减
- **技能抽取** — 记忆聚类→槽位→步骤→触发词→可复用技能模板
- **MCP Server** — 33 个工具，含 intent_router、conflict_scan、memory_diff
- **CLI** — 200+ 命令，支持 bash/zsh/fish 自动补全
- **REST API** — 标准 HTTP API，支持非 Python 应用
- **多后端 Embedding** — sentence-transformers / OpenAI / Ollama / 自定义 HTTP
- **751+ 测试用例** — 全面测试覆盖，CI 集成安全扫描（bandit + pip-audit）

---

## 集成方式

### Claude Code

```python
from MindForge.adapters import ClaudeCodeAdapter

adapter = ClaudeCodeAdapter.from_env()
adapter.remember("用户偏好简洁代码风格", ["preferences"])
context = adapter.get_context("数据库优化")
```

### MCP Server

```bash
MindForge-mcp --db-path ./data/memory.db
```

### REST API

```bash
MindForge serve --api --port 9000
# GET  /api/memories
# POST /api/memories
# GET  /api/search?q=...
# GET  /api/stats
```

### OpenClaw

```yaml
memory:
  adapter: MindForge
  adapter_config:
    db_path: ~/.MindForge/data/memory.db
    encrypted: true
    auto_consolidate: true
```

---

## 文档

- [完整文档](docs/)
- [CLI 参考](docs/cli-reference.md)
- [API 参考](docs/api-reference.md)
- [MCP 工具列表](docs/mcp-tools.md)
- [更新日志](CHANGELOG.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)

---

## 路线图

- [ ] v6.0 — 多 Agent 协作端口、记忆预览流式传输
- [ ] 向量数据库后端（Qdrant / Chroma）
- [ ] Web UI 仪表盘
- [ ] 插件生态

---

## 许可证

[MIT](LICENSE) — Copyright (c) 2026 MindForge Project
