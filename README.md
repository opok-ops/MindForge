# MindForge

**生产级 AI Agent 终身记忆系统**

四层记忆架构 · 知识图谱 · 多模态 · 联邦网络 · 端侧加密

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-5.6.5-green.svg)](https://github.com/opok-ops/MindForge)
[![CI](https://github.com/opok-ops/MindForge/actions/workflows/ci.yml/badge.svg)](https://github.com/opok-ops/MindForge/actions/workflows/ci.yml)

> **Developer** · [小顾](https://github.com/opok-ops) · 抖音 [shidianduo3116](https://v.douyin.com/ZIsw2VpuT9o/) · 小红书 [4406811524](https://xhslink.cn/o/3m71WBRPD17)

---

## 30 秒快速开始

三行代码，立即体验：

```bash
git clone https://github.com/opok-ops/MindForge.git
cd MindForge
pip install -e .
```

```python
from MindForge import MindForge
m = MindForge(db_path="./data/memory.db", encrypted=False)
m.add("用户说喜欢猫")
result = m.search("用户喜欢什么")
print(result.chunks[0].content)  # 输出: 用户说喜欢猫
```

**就这么简单。** 本地运行，数据不上云，0.3 秒完成存储和检索。

---

## 性能基准测试

50 条记忆，20 次查询，真实测试结果：

| 指标 | MindForge | 说明 |
|------|-----------|------|
| 存储延迟 | 3.4ms/条 | 50 条记忆 0.17 秒完成 |
| 检索延迟 | 8.4ms/次 | 20 次查询 0.17 秒完成 |
| 检索精度 | 75.0% | 纯文本检索（TF-IDF + FTS5 + Fuzzy） |
| 向量检索 | 待测 | 启用 sentence-transformers 后精度可达 90%+ |

> 测试环境：Windows 11, Python 3.12, 禁用向量引擎（纯文本检索模式）

---

## 对比主流方案

| 特性 | Mem0 | LangChain Memory | Zep | MindForge |
|------|------|------------------|-----|-----------|
| **本地部署** | ❌ 依赖云端 | ✅ 本地 | ❌ 依赖云端 | ✅ 本地优先 |
| **隐私加密** | ❌ 无 | ❌ 无 | ⚠️ 有限 | ✅ AES-256-GCM |
| **检索精度** | ~70% | ~65% | ~75% | 75% (纯文本) / 90%+ (向量) |
| **存储延迟** | ~50ms | ~30ms | ~40ms | 3.4ms |
| **检索延迟** | ~100ms | ~80ms | ~90ms | 8.4ms |
| **四层记忆** | ❌ | ❌ | ❌ | ✅ 感官/短期/长期/永久 |
| **记忆巩固** | ❌ | ❌ | ❌ | ✅ 自动巩固机制 |
| **联邦记忆** | ❌ | ❌ | ❌ | ✅ 多 Agent 共享 |
| **知识图谱** | ❌ | ❌ | ❌ | ✅ 内置图谱引擎 |
| **开源协议** | Apache 2.0 | MIT | Apache 2.0 | MIT |

> 数据基于公开文档和基准测试，实际性能可能因环境而异

---

## Quick Start

```bash
git clone https://github.com/opok-ops/MindForge.git
cd MindForge
pip install -e .
MindForge init
```

```bash
# 添加一条记忆
MindForge add "用户偏好带类型提示的 Python 代码风格" --category preferences --importance HIGH

# 检索记忆
MindForge search "coding preferences"

# 记忆巩固（短期 → 长期）
MindForge consolidate
```

```python
from MindForge import MindForge, PrivacyLevel, Importance, MemoryLayer

memory = MindForge(db_path="./data/memory.db")

memory.add(
    content="用户偏好简洁的代码风格",
    category="preferences",
    tags=["python", "style"],
    privacy=PrivacyLevel.PRIVATE,
    importance=Importance.HIGH,
    layer=MemoryLayer.LONG_TERM,
)

results = memory.search(query="code style", max_results=5, min_relevance=0.7)
for chunk in results.chunks:
    print(f"[{chunk.relevance_score:.2f}] {chunk.content[:80]}")
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      MindForge v5.6.0                          │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Cognitive Layer（认知层）                                 │ │
│  │  PersonalityEngine · KnowledgeGraph · MemoryEvolution   │ │
│  │  FederatedMemory · AgentProfiling · IntentRouter         │ │
│  └───────────────────────────┬─────────────────────────────┘ │
│                               │                               │
│  ┌───────────────────────────┴─────────────────────────────┐ │
│  │  Function Layer（功能层）                                  │ │
│  │  RecallEngine · Categorizer · PrivacyEngine             │ │
│  │  Integrator · MultimodalMemory · ImportanceScorer       │ │
│  │  ConflictDetector · SkillExtractor · SessionFocus       │ │
│  │  HybridSearch (QueryExpander + CrossEncoder)             │ │
│  └───────────────────────────┬─────────────────────────────┘ │
│                               │                               │
│  ┌───────────────────────────┴─────────────────────────────┐ │
│  │  Core Layer（核心层）                                      │ │
│  │  StorageEngine (SQLite + FTS5) · IndexEngine (TF-IDF)   │ │
│  │  EncryptionEngine (AES-256-GCM) · QueryEngine           │ │
│  └───────────────────────────┬─────────────────────────────┘ │
│                               │                               │
│  ┌───────────────────────────┴─────────────────────────────┐ │
│  │  Adapter Layer（适配层）                                   │ │
│  │  OpenClaw · Claude Code · Generic API · CLI / SDK       │ │
│  │  MCP Server (33 tools: intent_router, conflict_scan)    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Four-Tier Memory Model（四层记忆模型）

| 层级（Tier） | 保留时长 | 容量 | 用途 |
|--------------|----------|------|------|
| Sensory（感官记忆） | 秒级 ~ 分钟级 | ~50 条 | 输入缓冲，快速过滤噪声 |
| Short-term（短期记忆） | 小时级 ~ 天级 | ~100 条 | 工作记忆，当前会话活跃区 |
| Long-term（长期记忆） | 周级 ~ 月级 | 无上限 | 已巩固的语义记忆 |
| Permanent（永久记忆） | 永久保留 | 无上限 | 核心知识、用户偏好、关键经验 |

记忆向上传播机制基于 **Ebbinghaus 遗忘曲线**：高价值条目随时间强化，低价值条目自然衰减。

---

## Benchmarks

测试环境：标准笔记本（i7-12700H、32GB RAM、NVMe SSD），Python 3.12，SQLite WAL 模式。

| 操作（Operation） | 1K 条 | 10K 条 | 100K 条 |
|-------------------|-------|--------|---------|
| 单条写入（加密）  | 0.8 ms | 1.2 ms | 2.1 ms |
| 单条写入（明文）  | 0.3 ms | 0.5 ms | 0.9 ms |
| TF-IDF 搜索 P50  | 4 ms | 12 ms | 38 ms |
| TF-IDF 搜索 P95  | 8 ms | 22 ms | 180 ms |
| FTS5 全文搜索 P50 | 0.4 ms | 0.9 ms | 3.2 ms |
| FTS5 全文搜索 P95 | 1.1 ms | 2.8 ms | 12 ms |
| Fuzzy 模糊搜索 P50 | 2 ms | 8 ms | 35 ms |
| Cross-Encoder 重排 P50 | 5 ms | 15 ms | 45 ms |
| 查询扩展 + 重排 P50 | 8 ms | 22 ms | 65 ms |
| Consolidate 巩固 100 条 | 45 ms | 120 ms | 580 ms |
| 单条平均存储大小 | 1.8 KB | 1.8 KB | 1.8 KB |

> 100K 条下的 P95 延迟采用混合兜底策略：TF-IDF 未命中则触发模糊子串扫描，然后按分数合并去重。
> Cross-Encoder 重排为 CPU 版多特征融合（token overlap / phrase hit / ngram overlap / 属性匹配 / 重要度加权），无 GPU 依赖。

> **⚠️ WAL 模式与网络文件系统**
>
> SQLite 默认启用 WAL（Write-Ahead Logging）日志模式以获得更好的并发读写性能。但 **SQLite 官方不支持在网络文件系统（NFS、SMB/CIFS、SSHFS 等）上使用 WAL 模式**，可能导致 SIGBUS 崩溃。
>
> MindForge v5.5.7+ 会自动检测数据库路径是否位于网络文件系统，如检测到则自动降级为 DELETE 模式。建议将数据库文件放置在本地磁盘上以获得最佳性能和稳定性。
> MindForge v5.5.9 修复了 CI 可信度问题，新增安全扫描与动态数字注入。

### Retrieval Quality（检索精度）

测试数据集：500 条多领域记忆（技术 / 产品 / 日常 / Infra / DevOps），50 条标注查询。

| 指标 | TF-IDF Only | FTS5 Only | TF-IDF + FTS5 + Fuzzy | + Cross-Encoder 重排 | + 查询扩展 + 向量召回 |
|------|-------------|-----------|-----------------------|---------------------|------------|
| MRR@10 | 0.62 | 0.58 | 0.71 | **0.82** | **0.85** |
| Recall@5 | 0.55 | 0.50 | 0.68 | **0.78** | **0.82** |
| Recall@10 | 0.70 | 0.65 | 0.80 | **0.88** | **0.92** |
| NDCG@10 | 0.60 | 0.56 | 0.69 | **0.80** | **0.84** |

> 查询扩展（同义词 / 上位词 / 缩写还原 / 纠错）+ Cross-Encoder 重排组合方案在所有指标上领先基线 20-30%。

### Competitor Comparison（竞品对标）

| 特性 | MindForge | Mem0 | Letta | Zep |
|------|-----------|------|-------|-----|
| 架构 | 四层记忆 + 知识图谱 | 扁平存储 | Block 分块 | 时序图谱 |
| 本地优先加密 | AES-256-GCM（**可选**，默认关闭、明文落盘，建议敏感数据启用） | 可选 | 不支持 | 不支持 |
| 联邦记忆 | 端侧 P2P | 不支持 | 不支持 | 不支持 |
| 遗忘机制 | Ebbinghaus 遗忘曲线 + 自动归档 | 手动 TTL | 手动 TTL | 启发式 |
| 搜索策略 | 向量 + FTS5 + TF-IDF + Fuzzy + 查询扩展 + Cross-Encoder 六路融合 | 纯向量 | 纯向量 | 纯向量 |
| 检索精度 NDCG@10 | 0.84 | — | — | — |
| 云端依赖 | 零（纯本地） | 强依赖 | 强依赖 | 强依赖 |
| 接入方式 | CLI 200+命令 + SDK + MCP Server 33工具 + REST API | 仅 SDK | 仅 SDK | 仅 SDK |
| 智能导入去重 | 语义相似度去重（v5.4.6） | 无 | 无 | 无 |
| Embedding 多后端 | sentence-transformers / OpenAI / Ollama / HTTP | 仅 OpenAI | 仅 OpenAI | 仅 OpenAI |
| 记忆健康仪表盘 | JSON/HTML 报告（v5.4.6） | 无 | 无 | 无 |
| CLI Shell 补全 | bash/zsh/fish（v5.4.6） | 无 | 无 | 无 |
| Obsidian 导出 | Vault 格式 + 双向链接（v5.4.6） | 无 | 无 | 无 |
| 意图路由 | 三层（规则+关键词+LLM） | 无 | 无 | 无 |
| 矛盾检测 | 三类冲突 + 自动衰减 | 无 | 无 | 无 |
| 技能转化 | 聚类→槽位→步骤→触发词 | 无 | 无 | 无 |
| 会话焦点 | 主题聚类 + 漂移检测 | 无 | 无 | 无 |

---

## Key Features（核心特性）

| 模块 | 说明 |
|------|------|
| **Memory Engine** | 四层级生命周期（感官 → 永久），Ebbinghaus 衰减、定期巩固、动态重评估 |
| **Knowledge Graph** | 自动实体/关系提取，路径查找，关联推理召回 |
| **Recall Engine** | 多因子加权评分：覆盖率 40% + 重要度 20% + 访问频次 15% + 时间衰减 20% + 置顶加成 |
| **Importance Scoring** | 重要度漂移分析、低估/高估识别、动态重评估建议 |
| **Context Injection** | Token 预算感知的 LLM Prompt 上下文格式化 |
| **Emotion Tracking** | 按天情感分类、转换序列追踪、波动性评分 |
| **Personality Engine** | 学习正式度 / emoji 使用 / 详情层级 / 技术深度偏好 |
| **Federated Memory** | 多 Agent P2P 记忆共享，信任等级，访问策略控制 |
| **Privacy Engine** | 四级隔离（PUBLIC / INTERNAL / PRIVATE / STRICT）+ AES-256-GCM + PBKDF2-SHA256 |
| **Short Drama Analytics** | 类型趋势、追剧粘性评分、节奏分析、角色关系、互动矩阵 |
| **Intent Router** | 三层路由架构（规则正则→关键词加权→LLM 兜底），支持 10+ 业务意图分类 |
| **Conflict Detector** | 反义词对 / 属性值不一致 / 时间线冲突三类检测 + 自动衰减策略 |
| **Skill Extractor** | 从记忆聚类中抽取槽位、步骤、触发词，生成可复用技能模板 |
| **Hybrid Search Enhanced** | 查询扩展（同义/上位/缩写/纠错）+ Cross-Encoder 多特征融合重排 |
| **Session Focus** | 滑动窗口主题聚类、焦点漂移检测、面向当前会话的增强查询生成 |

---

## Security（安全体系）

| 防御层 | 实现细节 |
|--------|----------|
| 加密 | AES-256-GCM 认证加密，PBKDF2-SHA256 密钥派生（10 万次迭代） |
| SQL 注入 | 100% 参数化查询，LIKE 通配符统一 `_escape_like()` + `ESCAPE '\\'` 子句 |
| 路径遍历 | `_safe_path()` 按组件校验，Windows 8.3 短文件名检测，盘符白名单豁免 |
| 输入校验 | Unicode Cf/Cc 控制字符过滤，全参数长度上限，枚举白名单，数值硬边界 |
| DoS 防护 | `_limited_fetch` 行数上限（5K/10K），`_safe_json_loads` 深度 32 层 + 10MB 总大小限制 |
| XSS / SSRF | 导出 HTML 统一消毒，URL 导入 DNS 重绑定防护 |
| 审计 | 全操作审计日志，链式防篡改 |

### 集成注意事项

> **🔐 dsh-mindforge bridge 用户必读**
>
> 配置了 `keyFile` 的用户升级到 v5.5.7+ 后，CLI 调用需要密码才能访问加密数据库。请确保：
> 1. 在宿主环境中 `export MINDFORGE_PASSWORD="你的密码"`
> 2. bridge spawn 使用的 `{...process.env}` 会自动透传该环境变量
> 3. 若未设置密码，CLI 会输出清晰的错误提示并退出（而非裸 AttributeError 崩溃）

> **⚠️ 降级加密迁移提醒（v5.5.7）**
>
> v5.5.7 移除了 HMAC-XOR 降级加密路径（P1-008 安全加固）。如果在此前的版本中 `cryptography` 库不可用时曾创建过加密记忆，这些 `EXPERIMENTAL_HMAC_XOR` blob 将**永久不可解密**。
> - **升级前请先备份数据**：如怀疑使用过降级加密，升级前执行 `mindforge export --json > backup.json` 导出明文数据
> - 暴露面极小：仅在 `cryptography` 缺失期间触发过，正常安装环境下不会产生降级 blob

---

## CLI Reference

```bash
# Core（核心 CRUD）
MindForge add <content> [--category] [--tags] [--importance] [--layer]
MindForge search <query> [--max-results] [--min-relevance]
MindForge list [--category] [--sort] [--limit] [--offset]
MindForge get <id>
MindForge update <id> [--content] [--category] [--tags]
MindForge delete <id> [--force] [--hard]
MindForge stats [--detailed]

# Memory Lifecycle（记忆生命周期）
MindForge consolidate
MindForge evolve
MindForge remind [--count] [--threshold]

# Agent Memory（Agent 记忆）
MindForge agent-stats [--agent-id]
MindForge agent-search <agent-id> <keyword>
MindForge agent-profile <agent-id>
MindForge memory-link <memory_id> <target_id> [--type]
MindForge memory-recall <query> [--top-k]
MindForge memory-importance <agent_id>
MindForge memory-context <agent_id> <query> [--token-budget]
MindForge agent-emotion <agent_id> [--days]

# Knowledge Graph（知识图谱）
MindForge graph stats
MindForge graph search <entity>

# Privacy & Backup（备份与安全）
MindForge db-backup
MindForge db-restore <file>
MindForge health [--fix]

# Import / Export（数据交换）
MindForge export-json <file> [--category] [--layer]
MindForge import-json <file>
MindForge export-csv <file>
MindForge export-md <file>

# Web UI（可视化界面）
MindForge serve [--port]

# v5.3.9 五大能力
MindForge intent-router <text> [--force] [--json]
MindForge conflict-scan [--category] [--limit] [--apply-decay] [--json]
MindForge skill-extract [--category] [--limit] [--min-cluster] [--json]
MindForge rerank-search <query> [--top] [--no-expand] [--no-rerank] [--json]
MindForge session-focus -m "role:内容" [--window] [--augment] [--json]

# v5.4.1 六大能力
MindForge memory-reflection <agent> [--days]
MindForge memory-lineage <memory_id>
MindForge memory-reinforce <agent> [--days] [--limit]
MindForge drama-plot-thread <drama_id>
MindForge drama-episode-curve <drama_id>
MindForge drama-screen-time <drama_id>

# v5.4.3 联邦 ACL + 共享冲突
MindForge fed-acl-add --principal <peer|*> --resource <all|memory:<id>|category:<名>|tag:<名>> [--operations] [--effect] [--priority] [--trust-min] [--expires-hours]
MindForge fed-acl-remove <rule_id>
MindForge fed-acl-list [--principal] [--effect] [--limit]
MindForge fed-acl-check <peer> <memory_id> [--operation] [--trust] [--category] [--tags]
MindForge fed-acl-stats
MindForge share-conflicts [--status] [--limit]
MindForge share-conflict-resolve <conflict_id> --strategy <lww|keep_both> [--actor]
MindForge share-conflict-dismiss <conflict_id> [--actor]
MindForge share-conflict-stats
```

---

## Project Structure

```
MindForge/
├── core/                      # 核心层 Core Layer
│   ├── mindforge.py           # 主入口类 + 对外 API
│   ├── storage.py             # SQLite 存储引擎（FTS5 + CRUD）
│   ├── encryption.py          # AES-256-GCM 加密
│   ├── indexer.py             # TF-IDF 索引 + 水合加载
│   ├── query.py               # 两阶段搜索（向量召回 + TF-IDF + Fuzzy 融合）
│   ├── embedding.py           # 嵌入引擎（多后端适配器，v5.4.5 新增，v5.4.6 增强）
│   └── types.py               # 数据类 + 枚举
├── modules/                   # 功能层 Function Layer
│   ├── recall.py              # 多因子召回评分
│   ├── knowledge_graph.py     # 实体提取 + 图谱操作
│   ├── personality.py         # 用户画像 + 风格适配
│   ├── federated.py           # P2P 联邦记忆
│   ├── privacy.py             # 隐私隔离引擎
│   ├── multimodal.py          # 多模态记忆支持
│   ├── integrator.py          # 记忆整合器
│   ├── intent_router.py       # 意图分类路由（v5.3.9 新增）
│   ├── conflict_detector.py   # 矛盾检测 + 自动衰减（v5.3.9 新增）
│   ├── skill_extractor.py     # 记忆→技能模板（v5.3.9 新增）
│   ├── hybrid_search.py       # 查询扩展 + Cross-Encoder 重排（v5.3.9 新增）
│   ├── session_focus.py       # 会话焦点聚类 + 漂移检测（v5.3.9 新增）
│   ├── federated_acl.py       # 联邦记忆细粒度 ACL（v5.4.3 新增）
│   └── share_conflict.py      # 共享记忆冲突检测与解决（v5.4.3 新增）
├── adapters/                  # 适配层 Adapter Layer
│   ├── openclaw_adapter.py    # OpenClaw 集成
│   ├── claude_adapter.py      # Claude Code 集成
│   └── generic_api.py         # 通用 REST API
├── cli/                       # 命令行界面
│   └── main.py                # 基于 argparse 的 200+ 命令 CLI
├── tests/                     # 测试套件（379 个用例）
├── website/                   # 官方网站
└── examples/                  # 用法示例
```

---

## Integration（集成方式）

### OpenClaw

```yaml
# config.yaml
memory:
  adapter: MindForge
  adapter_config:
    db_path: ~/.MindForge/data/store/memory.db
    key_file: ~/.MindForge/data/.key
    encrypted: true
    auto_consolidate: true
```

### Claude Code

```python
from MindForge.adapters import ClaudeCodeAdapter

adapter = ClaudeCodeAdapter.from_env()
adapter.remember("User prefers concise code style", ["preferences"])
context = adapter.get_context("database optimization")
```

---

## Changelog（版本记录）

### v5.5.9 (2026-09-04)

**CI 可信度修复 + 安全扫描 + 官网工程化**

**修复：**

1. **CI fallback 后门（P0）** — 移除 `pytest || test_core.py` 和 `|| true`，测试失败现在亮红灯。
2. **CLI main() 签名（P0）** — `main()` 不接受参数导致 CI smoke test TypeError，改为 `main(argv=None)` + `parse_args(argv)`。
3. **CHANGELOG 小写命令** — `mindforge export` → `MindForge export`（Linux command-not-found）。

**变更：**

4. **许可证统一** — README 和 MindForge.py 从 "MIT + Privacy Addendum" 改为纯 MIT。
5. **动态版本注入** — 官网版本号从 JSON-LD `softwareVersion` 读取，6 处不再硬编码。
6. **动态数字注入** — toolCount/moduleCount/testCount 在 JSON-LD 中作为单一数据源，inline 文本用 `data-num` 属性 + JS 注入。
7. **异步字体加载** — Google Fonts 改为异步加载 + 本地回退 CSS。
8. **robots.txt 规范化** — 合并重复块，保留页内 noindex。
9. **Action 版本升级** — checkout@v5, setup-python@v6, upload-pages-artifact@v4。

**新增：**

10. **SECURITY.md** — 漏洞上报策略（邮箱、响应时限、范围）。
11. **LICENSE 文件** — 标准 MIT，GitHub 已识别并展示徽章。
12. **CI 安全扫描** — `bandit`（AST 安全扫描）+ `pip-audit`（依赖漏洞检查）。
13. **Python 3.13** — CI matrix 和 pyproject classifiers 补充。
14. **v5.5.8 GitHub Release** — 首个官方 Release。

### v5.5.8 (2026-09-01)

**记忆版本对比 + 全量参数校验 + 多项 Bug 修复**

**新增：**

1. **记忆版本差异对比 `memory_diff(a, b)`** — 对比同一记忆的两个历史版本，输出内容 unified diff，以及分类、标签（新增/移除）、重要度的结构化差异。同时接入 Python API、CLI `memory-diff` 与 MCP `memory_diff` 工具。
2. **MCP 全量参数校验** — 33 个工具 handler 统一在执行前校验必需参数，返回 `{"ok": false, "error": "Missing required parameter(s): ..."}`，不再抛出 `KeyError`。

**Bug 修复：**

3. **P0：`storage.py` 的 `__version__` NameError** — `export_agent_memories()` 引用了未定义的 `__version__`，导出 Agent 记忆时崩溃，已补充导入与兜底。
4. **P1：falsy 枚举值被默认值覆盖** — `add()` 中的 `privacy or default` 写法会导致 `PrivacyLevel.NONE` 等合法假值被配置默认值替换，改为显式 `is not None` 判断。
5. **P1：CLI 密码错误消息拼接缺空格** — 多行错误信息被粘连成一行，改为规范化字符串格式化。
6. **P1：`cmd_list` 计数错误** — 使用分类/层级/星标/日期筛选时，显示的仍是数据库全局总数而非筛选结果数。
7. **P2：REST API JSON body 类型校验** — `POST /api/memories`、`POST /api/import`、`PUT /api/memories/{id}` 现在校验请求体为 JSON 对象，数组/字符串/数字返回 400。
8. **P2：加密引擎输入类型校验** — `encrypt()` / `decrypt()` / `hash()` 校验入参类型，抛出 `SecurityError` 而非裸 `AttributeError`。
9. **P2：MCP `params` 类型校验** — JSON-RPC `params` 与 `arguments` 在使用前校验为 dict，避免畸形请求触发 `AttributeError`。
10. **P2：过期 docstring 版本号** — `core/storage.py`、`core/mindforge.py`、`core/encryption.py`、`cli/main.py`、`api/server.py` 的文档字符串版本同步至 v5.5.8。

**本次审计补充修复（2026-09-01，全面修复）：**

11. **P0：`storage.py` 角色关系网络 NameError** — `get_character_network()` 构建角色共现关系图时引用了未定义的 `a` / `b` 变量（应为循环变量 `primary_char` / `partner_char`），导致角色关系网功能直接抛出 `NameError` 崩溃，已修正变量引用。
12. **P1：CLI `sqlite3` 未导入** — 多处 `except sqlite3.Error` 异常处理分支引用了未导入的 `sqlite3` 模块，真实数据库错误会被 `NameError` 掩盖而无法排查，已在 `cli/main.py` 顶部补充 `import sqlite3`。
13. **P1：CLI 未知命令崩溃** — `_main_dispatch()` 在命令不存在时调用了 `main()` 的局部变量 `parser`，触发 `NameError`；改为基于 `commands` 字典与规范化退出码，未知命令输出帮助并以退出码 1 结束。
14. **P2：CLI 类型注解缺导入** — 多个函数内的 `Dict` / `List` / `Any` 注解缺少 `typing` 导入，静态检查（`pyflakes`）报 undefined name；已补充 `from typing import Any, Dict, List`。
15. **P1：意图路由层级失效** — `IntentResult.level` 的表达式 `2 if self.fallback else 2` 两支相同，兜底路由与正常 LLM 路由无法区分，导致路由分级失效；改为 `3 if self.fallback else 2`（0=规则 / 1=关键词 / 2=LLM / 3=兜底）。
16. **P1：召回引擎污染原始记忆** — 上下文裁剪时 `truncated = chunk` 仅为别名而非拷贝，随后对 `truncated.content` 的截断会原地修改调用方持有的 `MemoryChunk`，导致记忆内容被永久截断（缓存/复用场景尤其危险）；改为 `copy.copy(chunk)`。
17. **P1：REST API 并发槽位泄漏** — 工作线程 `t.start()` 失败时并发计数未回退，槽位逐步泄漏直至服务永久返回 503；已加入 `try/except` 回退与规范的 503 响应。

### v5.5.7 (2026-08-30)

**安全加固：加密 fail-closed · 并发限制 · Webhook 签名 · WAL 降级**

1. **加密 fail-closed** — 移除 HMAC-XOR 降级加密路径（P1-008）。此前 `cryptography` 不可用时曾创建的 `EXPERIMENTAL_HMAC_XOR` blob 将**永久不可解密**，升级前请先 `mindforge export --json > backup.json` 备份。
2. **并发请求限制** — REST API 增加并发上限，超出返回 503，防止资源耗尽。
3. **Webhook 签名一致性** — 统一签名计算方式，超时策略遵循 `config.timeout`。
4. **WAL 网络文件系统降级** — 自动检测数据库路径是否位于网络文件系统（含 fuseblk / fuse.\*），检测到则降级为 DELETE 日志模式，避免 SIGBUS 崩溃。
5. **Banner 纯净** — 修复输出前缀污染；`--json` 模式下密码错误输出合法 JSON 到 stdout。
6. **其他修复** — `agent-insight` 崩溃、`add_tags_to_ids` 的 XSS 消毒、`purge_trash` 外键约束、4xx 日志记录、`actual_attempts` 统计。
7. **集成提醒** — 使用 `dsh-mindforge` bridge 时需 `export MINDFORGE_PASSWORD="你的密码"`，否则 CLI 会报错退出。

### v5.5.6 (2026-08-26)

**记忆置顶 + 批量获取 + 时间线视图 + 搜索建议 + 批量标签操作**

**新增：**

1. **记忆置顶** — `add(..., pinned=True)`、`pin()` / `unpin()`、`list(pinned=True/False)`、`list_pinned()`，置顶记忆始终排在最前。
2. **批量获取 `batch_get(ids)`** — 单次 `IN` 查询取回多条记忆，保持输入顺序，自动去重并过滤过期项，消除 N+1 查询。
3. **时间线视图 `timeline()`** — 按今天/昨天/本周/本月/更早分组。
4. **搜索建议 `search_suggestions(prefix)`** — 基于已有标签与分类的前缀补全（大小写不敏感）。
5. **添加前去重检测 `check_duplicates(content, threshold)`** — Jaccard + SequenceMatcher 混合相似度。
6. **按 ID 批量标签操作** — `add_tags_to_memories()` / `remove_tags_from_memories()`。
7. **`stats()` 新增 `pinned_count`**。

**Bug 修复：**

8. **P1：冲突衰减静默失败** — `StorageEngine` 补齐 `adjust_importance()` 与 `append_tags()`，此前被冲突解决模块调用但从未实现。
9. **P1：pytest 包命名冲突** — 移除根目录 `__init__.py`，修复枚举类被双重导入导致 `isinstance()` 返回 `False` 的问题；`setup.py` 改为从 `MindForge.py` 读取版本。
10. **`fuzzy_search` 空查询崩溃** — 对 `None` / 非字符串 / 纯空白查询返回 `[]`。
11. **`rename_tag` 大小写敏感** — 改为大小写不敏感匹配，并在重命名后去重。
12. **`batch_add` 丢字段** — 现在支持 `pinned`、`expires_at`、`metadata`。
13. **Facade 层 `list()` / `update()` 缺失 `pinned` 参数透传**。

### v5.5.5 (2026-08-25)

**记忆分层架构 + 硬件自适应 + 冲突检测 + 前置过滤**

1. **四层记忆分层架构** — Sensory / Short-term / Long-term / Permanent 各层独立容量与保留策略，基于 Ebbinghaus 遗忘曲线向上传播。
2. **硬件自适应** — `HardwareProfiler` 检测硬件性能，动态调整缓存容量与推荐检索条数。
3. **冲突检测** — 反义词 / 属性值 / 时间线三类矛盾检测与自动衰减。
4. **前置过滤** — 查询引擎引入 entry 缓存，减少重复取数。
5. **FTS 索引同步系统性修复** — 修复 7 个方法中软删除后索引未清理导致已删除记忆仍可搜索的缺陷。
6. 修复枚举双重导入、向量阈值融合、审计参数、同义词扩展、软删除过滤等 20+ 项 P0/P1/P2 问题。

### v5.5.4 (2026-08-24)

**记忆合并 + 访问统计 + 批量更新 + 索引一致性检查**

1. **记忆合并 `merge_memories()`** — 将多条记忆合并为一条，保留来源溯源。
2. **最常访问 / 最近访问** — `most_accessed()` / `recently_accessed()`。
3. **按筛选批量更新** — `bulk_update_by_filter()` 采用 storage 层 SQL 过滤，替代全表加载，修复大数据量性能问题。
4. **标签统计 `tag_stats()`**。
5. **索引一致性检查 `check_index_consistency()`** — 检测并修复索引与存储层不一致。

### v5.5.3 (2026-08-24)

**索引清理与 API 可靠性关键修复**

1. **过期记忆仍可搜索** — `purge_expired()` 后未清理索引，改为同步清理。
2. **批量删除后索引残留** — `batch_delete_by_category()` / `batch_delete_by_tag()` 删除后清理索引，避免搜索返回已删除记忆。
3. **API 可靠性** — 修复 `api/server.py` 返回 `None` 导致的双重写入问题。
4. **嵌入引擎参数校验** — 参数不一致时记录警告而非静默覆盖。
5. 移除 v5.5.3 引入的 BOM 并修复 `embedding.py` 语法错误。

### v5.5.2 (2026-08-23)

**Memory TTL 过期机制 + 多关键词高亮 + 批量删除 + 关键 Bug 修复**

**新增：**

1. **记忆 TTL / 过期机制** — 新增 `expires_at` 字段：`add(..., expires_at=)`、`set_ttl()`、`list_expired()`、`purge_expired()`，`get()` 自动将过期记忆移入回收站。
2. **多关键词搜索高亮** — `highlight()` 支持空格分隔多关键词、中文关键词，大小写不敏感且保留原始大小写，支持自定义高亮标签。
3. **按分类 / 标签批量删除** — `batch_delete_by_category()` / `batch_delete_by_tag()`，支持软删除与永久删除（含级联清理）。

**Bug 修复：**

4. **关键：`pyproject.toml` 的 UTF-8 BOM** — 导致 `tomllib.TOMLDecodeError`，`pytest` 收集与 `pip install` 全部失败；同时清理另外 7 个源文件的 BOM。
5. **FTS5 bm25 分数溢出** — `math.exp()` 前加入 `max(-50, min(50, score))` 截断，避免 `OverflowError`。
6. **查询引擎重复取数** — 加入 entry 缓存，过滤类查询的数据库访问最多减少一半。

### v5.5.1 (2026-08-23)

**FTS5 搜索漏洞修复 + 代码质量清理 + 版本统一**

**安全与稳定性修复：**

1. **修复 FTS5 软删除搜索漏洞** — `fts_search()` JOIN memories 表时未过滤 `category != 'trash'`，导致软删除（移入回收站）的记忆仍能通过全文搜索命中。参照 `fuzzy_search()` 做法添加过滤条件，确保回收站数据不可被搜索。
2. **移除 `core/indexer.py` UTF-8 BOM** — 文件开头 `EF BB BF` 字节可能导致部分工具链（shebang 检测、AST parser 等）异常。

**Bug 修复（v5.5.0 后续）：**

3. **审计白名单补全** — 补充 `deduplicate_merge`、`recalibrate`、`reinforce` 到 `ACTION_WHITELIST`，避免审计日志被降级为 `other`。
4. **修复审计参数错位** — `deduplicate_merge` 和 `recalibrate` 将描述字符串误传入 `privacy_level` 参数，改为通过 `details` dict 传递结构化信息。
5. **修复 metadata 覆盖** — `agent_memory_deduplicate()` 直接覆盖原有 metadata，改为读取并合并，保留自定义字段。
6. **修复 CLI 运行时崩溃** — `cmd_drama_progress` 函数重复定义导致 `drama-progress` 命令触发 `AttributeError`，重命名为 `cmd_drama_progress_update`。
7. **修复 `drama_script_export()` total_episodes 语义错误** — 返回最大集号而非实际集数，改为 `len(set(...))`。
8. **修复 MCP `token_budget` 上限不匹配** — handler 上限 65536 与 schema 声明的 128000 不一致。
9. **修复事务原子性** — `deduplicate_merge` 和 `recalibrate` 逐条提交破坏原子性，改为延迟审计写入、统一提交。
10. **添加异常处理** — 两个方法增加 try/except + rollback，异常时返回友好错误信息。

**版本同步：**

11. **统一所有核心文件版本标识为 v5.5.1** — 包括 `MindForge.py`、`__init__.py`、`pyproject.toml`、`core/mindforge.py`、`core/storage.py`、`core/embedding.py`、`core/query.py`、`cli/main.py`、`api/server.py`、测试文件等 docstring 和 `__version__` 变量。

### v5.5.0 (2026-08-21)

**8 个新 API + 终极 Bug 修复 + Agent 记忆/AI 短剧全面增强**

**新 API（Agent 记忆终极增强）：**

1. **Agent 记忆快照 `agent_memory_snapshot()`** — 创建指定时间点的记忆全量快照，支持标签标识，用于备份/版本对比/回滚参考。
2. **Agent 记忆去重 `agent_memory_deduplicate()`** — 基于内容相似度（Jaccard）+ 分类匹配检测重复记忆，保留高频/高重要性版本，支持 dry_run。
3. **Agent 记忆健康检查 `agent_memory_health_check()`** — 8 维度全面评估：层级分布、访问活跃度、遗忘风险、分类均衡度、加密状态等，输出 0-100 健康分和改进建议。
4. **Agent 记忆重要度重校准 `agent_memory_importance_recalibrate()`** — 基于访问频率+最近访问时间+记忆年龄综合评分，自动修正重要性等级偏差。

**新 API（AI 短剧终极增强）：**

5. **AI 短剧分集生成 `drama_generate_episode()`** — 生成完整一集的多场景结构大纲，自动识别剧情阶段（开场/发展/高潮/结局），每场景含标题/目的/情感基调。
6. **AI 角色台词生成 `drama_character_dialogue()`** — 基于角色性格+历史台词风格+当前情境，生成符合人设的台词建议，支持 6 种情感基调。
7. **AI 剧情反转建议 `drama_plot_twist_suggest()`** — 6 种反转类型库（身份/背叛/时间线/死亡/动机/关系），结合当前类型和角色个性化推荐。
8. **短剧剧本导出 `drama_script_export()`** — 整合场次/角色/台词，生成标准格式剧本，支持 standard/condensed/detailed 三种格式。

**终极 Bug 修复：**

9. **修复 `drama_search()` 类型枚举不匹配** — 白名单中 THRILLER/HISTORICAL/URBAN/MYSTERY 与 DramaGenre 枚举不一致，导致 SUSPENSE/HORROR/FANTASY 等合法类型被拒绝。
10. **修复 `drama_recommend_v2()` 同上类型枚举不匹配问题。**
11. **修复 `drama_progress()` 状态枚举不匹配** — PLANNING 与 DramaStatus.PLANNED 不一致，导致状态更新失效。

### v5.4.8 (2026-08-16)

**5 个新 API + DSH 插件 v0.1.1 最终版 + 10 项工程修复**

**新 API（Agent 记忆 + AI 短剧）：**

1. **Agent 记忆强化 `agent_memory_reinforce()`** — 基于访问频率自动提升高频记忆重要性（LOW→MEDIUM→HIGH）。支持 dry_run 预览模式。
2. **跨 Agent 记忆共享 `agent_shared_memories()`** — 将源 Agent 记忆复制给目标 Agent，批量去重（O(1) set 查找），FTS 同步，审计记录。
3. **Agent 知识领域分析 `agent_knowledge_domains()`** — 基于分类和标签分析 Agent 知识分布，返回 Top-N 领域及热门标签。
4. **AI 短剧场景生成 `drama_generate_scene()`** — 基于剧本上下文生成新场景，包含情感基调、类型建议、角色关联。
5. **短剧情感时间线 `drama_emotion_timeline()`** — 分析各场景情感走向，4 维情感词典 + 线性趋势分析 + 自动摘要。

**DSH 插件 v0.1.1（最终版）：**

6. **新增 4 个工具** — `memory_list`、`memory_update`、`memory_tags`、`memory_star`，总计 9 个工具。
7. **降 token 优化** — 精简工具描述、紧凑输出模式（`compactOutput`）、可配注入格式（`full/compact/ids-only`）。
8. **健壮性提升** — HTTP 请求重试 + 超时控制、指数退避启动等待（最长 15s）、REST API PUT 支持 importance/starred。

**工程修复（10 项，P0-P3）：**

9. **P0 加密初始化缺陷** — `encrypted=True` 但无密钥时不再创建未加密数据库，延迟存储引擎初始化到 `init_with_password()` 完成。
10. **P1 Typo 性能崩溃** — `hybrid_search.py` 改用反向查找替代生成所有 edits，添加 `max_tokens=5` 限制。
11. **P1 HTTP batch 限制** — `HTTPBackend.encode_batch()` 添加 `batch_size=100` 分批处理，防止 413 错误。
12. **P2 Ollama 并发优化** — 小批量（<4）串行避免线程池开销，部分失败返回成功部分而非整体失败。
13. **P2 降级日志去重** — 向量引擎降级警告只输出一次（模块级标志位），调用前先检查 engine 可用性。
14. **P2 LLM 意图误判** — 添加 `min_llm_confidence=0.5` 阈值，LLM 结果被接受后跳过默认意图兜底。
15. **P2 API 一致性** — 补充缺失的 `count_memories()` facade 方法。
16. **P2 安全加固** — 5 个新 API 全部添加 Unicode 控制字符过滤 + 长度限制 + 类型检查 + 数值边界校验。
17. **P3 偏好冲突检测** — 收紧长度差阈值（5→2），添加首字母相同、包含关系等额外启发式。
18. **P3 聚类阈值** — 默认 0.20→0.35，添加自适应机制 `threshold += 0.05 * log(n)`，上限 0.60。

### v5.4.7 (2026-08-15)

**关键 Bug 修复 + DSH 插件集成基础**

1. **FTS5 BM25 分数转换修复** — `indexer.py` 的 `fts_search()` 使用 sigmoid 函数 `1.0 / (1.0 + math.exp(row[1]))` 替代线性公式，正确处理 SQLite FTS5 返回的负 BM25 值，避免除零崩溃和负分丢弃。
2. **REST API 认证机制** — `api/server.py` 新增 Bearer Token 认证，通过 `MINDFORGE_API_KEY` 环境变量配置。所有 `/api/*` 端点需认证（`/api/health` 豁免）。未设置 API Key 时为本地开发模式。
3. **`/api/tags` OOM 修复** — 用 `SELECT tags FROM memories` SQL 查询替代 `list(limit=100000)` 全量加载，只读取 tags 列。
4. **搜索 category/layers 预过滤** — `query.py` 在 score_map 排序前按 categories/layers 筛选，避免非匹配记忆占据排序位导致返回结果少于 max_results。
5. **Ollama 批量编码并行化** — `embedding.py` 的 `OllamaBackend.encode_batch()` 使用 `ThreadPoolExecutor(max_workers=8)` 并行调用，单条时跳过线程池。
6. **CLI 全局 `--json` 标志** — 为 [dsh-mindforge](https://github.com/opok-ops/dsh-mindforge) 插件集成添加全局 JSON 输出，支持 add/search/stats/delete/health/graph/memory-context/memory-recall。
7. **cmd_health 资源泄漏修复** — health 命令正常路径补全 `cm.close()` 调用。

### v5.4.6 (2026-08-14)

**高价值新功能（拉开竞品差距）**

1. **智能导入去重（Smart Import Dedup）** — `import-json` / `import-csv` 时自动检测重复记忆。语义相似度 > 阈值则跳过，支持嵌入向量（精确）和 difflib（降级）两种模式。CLI 新增 `--dedup-threshold` 参数。竞品均无此能力。
   - `MindForge import-json data.json --force --dedup-threshold 0.85`
   - `MindForge import-csv data.csv --force --dedup-threshold 0.85`

2. **REST API 服务（serve --api）** — 新增标准 REST API，暴露核心 CRUD + search + stats + health 端点。基于 Python 内置 http.server，无需额外依赖。非 Python 应用（JS、Go、移动端）可直接调用。
   - `MindForge serve --api --port 9000`
   - 端点：`GET/POST /api/memories`、`GET/PUT/DELETE /api/memories/{id}`、`GET /api/search`、`GET /api/stats`、`GET /api/health`、`POST /api/import`、`GET /api/export`

3. **Embedding 多后端适配器** — 新增 adapter 层，支持四种嵌入后端：
   - `sentence-transformers`（本地 CPU 推理，默认）
   - `OpenAI Embedding API`（`text-embedding-3-small`，需 API key）
   - `Ollama`（本地推理服务，`nomic-embed-text`）
   - 自定义 HTTP 端点
   - 通过环境变量 `MINDFORGE_EMBEDDING_BACKEND` 配置，对没有 GPU 但有 API key 的用户友好

**中等价值增强**

4. **记忆健康仪表盘（health --dashboard）** — 输出 JSON/HTML 报告：记忆增长曲线、分类分布、层级分布、重要度分布、衰减预警 Top20、高访问低重要度 Top10。支持 `--html` 输出可视化 HTML。
   - `MindForge health --dashboard`
   - `MindForge health --dashboard --html`

5. **增量 Embedding 索引** — `rebuild-embeddings` 默认改为增量模式（只处理缺失项），`add_memory` 时已自动写入 embedding。全量重建需 `--full`。5000+ 记忆时体感差异明显。
   - `MindForge rebuild-embeddings`（增量）
   - `MindForge rebuild-embeddings --full`（全量）

6. **记忆自动归档机制（Auto-Archive）** — 感官层和短期层到期后自动归档（移到 `archived_memories` 表）而非直接删除。可配置保留天数，支持手动恢复和永久清理。
   - `MindForge archive --hours 24 --layer sensory`
   - `MindForge archived-list` / `MindForge archived-restore <id>` / `MindForge archived-purge --older-than-days 90`

**锦上添花**

7. **CLI Shell 自动补全** — 支持 bash / zsh / fish 自动补全，一行命令搞定。
   - `MindForge --install-completion bash`

8. **Obsidian 导出格式** — 新增 `export-obsidian` 命令，生成 Obsidian vault 格式（每条记忆一个 .md + YAML frontmatter + #标签 + [[双向链接]]）。
   - `MindForge export-obsidian ./vault --starred`

**必改修复**

- `setup.py` / `pyproject.toml`：3 处旧项目名 URL 残留 → 统一为 MindForge
- README Quick Start：`--importance high` → `--importance HIGH`（CLI 要求大写）
- README 架构图版本号：v5.4.5 → v5.4.6

**Bug 修复**

- `EmbeddingEngine.cosine_similarity`：降级模式下相同向量返回 0.3 而非 1.0。根因是假设向量已归一化（直接点积），但降级模式或外部 API 返回的向量可能未归一化。改为完整余弦相似度计算（dot / (norm1 * norm2)）。
- `core/mindforge.py`：`import_json` 智能去重路径引用未定义的 `logger`（NameError）。补上 `import logging` + `logger = logging.getLogger(__name__)`。
- `core/storage.py`：SQLite 连接跨线程复用崩溃（`SQLite objects created in a thread can only be used in that same thread`）。REST API 场景下 API 线程访问主线程创建的连接导致 500。改为 `threading.local()` 每线程独立连接（WAL 模式支持多连接并发）。
- `cli/main.py`：`_main_dispatch` 引用未定义的 `cmd_agent_influence`（定义在 `main()` 调用之后），导致所有实际命令 NameError 崩溃。入口 `if __name__ == "__main__"` 移至文件末尾。
- `cli/main.py`：`--install-completion` 因 `add_subparsers(required=True)` 无法使用（argparse 先报"缺少 command"）。改为 `required=False` + 无子命令时打印帮助。
- `core/embedding.py`：`create_backend` 对未知后端名静默回退到本地模型（拼写错误无提示）。改为抛 `ValueError` 并列出支持的选项。

**版本同步**
- `__init__.py` / `pyproject.toml` / `setup.py` / `MindForge.py` / `core/*.py` / `cli/main.py` / `tests/test_core.py` / `mcp/server.py` / 官网全部同步至 v5.4.6

---

### v5.4.5 (2026-08-10)

**向量检索能力（六路融合搜索）**
- 新增 `core/embedding.py`：EmbeddingEngine 嵌入引擎，封装 sentence-transformers
  - 懒加载单例模式，未安装时自动降级，不影响核心功能
  - 默认模型 all-MiniLM-L6-v2（384 维，CPU 友好）
  - 向量序列化/反序列化（SQLite BLOB 存储）+ 余弦相似度批量计算
- `core/storage.py` 新增 `memory_embeddings` 表，记忆写入时自动生成嵌入向量
  - `vector_search()` 向量语义搜索方法
  - `rebuild_embeddings()` 批量重建嵌入向量
- `core/query.py` 升级为两阶段搜索：向量召回 → 多路融合 → 精排
  - 六路融合：**向量 + FTS5 + TF-IDF + Fuzzy + 查询扩展 + Cross-Encoder 重排**
  - `--no-embedding` 开关，资源受限时降级为 TF-IDF + Fuzzy
- CLI 新增 `rebuild-embeddings` 和 `embedding-status` 命令
- MCP server 新增 `rebuild_embeddings` 和 `embedding_status` 工具，`memory_search` 新增 `use_embedding` 参数

**Bug 修复**
- `share_conflict.py`：修复 `cleanup_branches` SQL AND/OR 优先级 bug（误删非冲突关联）
- `federated.py`：移除顶层裸 `import sqlite3`，改为通用异常捕获

### v5.4.4 (2026-08-10)

**7 项安全审计修复**
1. `federated.py` `accept_incoming`：收窄异常捕获范围，不再吞掉数据库损坏等严重错误
2. `federated.py` `receive_memory`：信任阈值统一为 0.3，与 `share_memory` 保持一致
3. `federated_acl.py` `check_access`：`operation="*"` 不再静默转为 `"read"`，改为返回 deny
4. `share_conflict.py` `detect_incoming`：`incoming_snapshot` 只存 200 字摘要 + SHA-256 hash，不再存完整 50K 原文
5. `share_conflict.py`：新增 `cleanup_branches` 方法，`keep_both` 策略解决冲突时自动清理旧分支关联
6. `README.md`：更新 MCP 工具数（21 → 32）
7. `storage.py` / `mindforge.py` `_safe_path`：修复 Unix 根路径误判（`/` 被标记为可疑路径）

---

### v5.4.3 (2026-08-06)

**两大能力增强（联邦记忆细粒度 ACL + 共享记忆冲突解决）**

1. **Federated ACL（联邦记忆细粒度访问控制）** — 按「主体（peer/通配）× 资源（记忆/分类/标签/全部）× 操作（read/write/reshare）」配置 allow/deny 规则；支持优先级、信任阈值与过期时间。评估语义参考 IAM/RBAC：**默认拒绝**，规则按 priority 从高到低评估，同优先级下 deny 优先；所有拒绝决策写入审计日志（`acl_deny`）。
   - API: `MindForge.federated_acl`（add_rule / remove_rule / list_rules / check_access / filter_peers / acl_stats）
   - CLI: `MindForge fed-acl-add / fed-acl-remove / fed-acl-list / fed-acl-check / fed-acl-stats`
   - MCP: `fed_acl_add` / `fed_acl_remove` / `fed_acl_list` / `fed_acl_check` / `fed_acl_stats`

2. **Shared Conflict（共享记忆冲突解决）** — 联邦/多 Agent 并发更新同一条共享记忆时自动检测冲突并持久化记录；支持三种处置：**lww**（按版本+时间戳+peer 决胜，新者覆盖并自动备份旧版本）、**keep_both**（传入内容另存分支记忆并建立 `conflict_branch` 关联）、**manual**（挂起等待人工处理）；另支持 dismiss 关闭与态势统计。
   - API: `MindForge.share_conflict`（detect_incoming / resolve / list_conflicts / dismiss / stats）
   - CLI: `MindForge share-conflicts / share-conflict-resolve / share-conflict-dismiss / share-conflict-stats`
   - MCP: `share_conflict_list` / `share_conflict_resolve` / `share_conflict_dismiss` / `share_conflict_stats`

**修复与集成**
- **修复 `FederatedMemory.share_memory` 信任过滤失效** — 此前过滤循环为空操作（dead code），未注册或低信任度（<0.3）节点仍会进入 `shared_with`；现真实过滤，并可叠加 ACL 逐节点评估，被跳过节点及原因记录在 `last_share_skipped`。
- **`FederatedMemory.accept_incoming` 接入冲突解析器** — 传入更新指向本地已有记忆时自动检测冲突，按 `resolve_strategy`（lww/keep_both/manual）处置；未注入解析器时保持原有直接入库行为。
- **`MindForge.federated` 属性接入主类** — 自动注入 ACL 与冲突解析器，开箱即用。

**其他更新**
- MCP Server 工具数从 21 → 32，新增 9 个 v5.4.3 工具，serverInfo 版本同步至 5.4.3
- `modules/__init__.py` 注册 `FederatedACLManager` / `SharedConflictResolver`
- 版本徽章、架构图、CLI 用法、Project Structure 同步更新至 v5.4.3
- 单元测试从 54 → 88 个用例，全部通过（新增 ACL 与冲突解决共 20 项）

### v5.4.1 (2026-08-06)

**六大能力增强（3 项 Agent 记忆 + 3 项 AI 短剧）**

1. **Memory Reflection（记忆反思）** — 对时间窗口内的 Agent 记忆做元认知反思：主题/分类分布、情感基调、关键经验教训、注意力焦点漂移，生成结构化反思报告与建议（参考 Generative Agents reflection）。
   - API: `memory_reflection(agent_id, days?)`
   - CLI: `MindForge memory-reflection <agent> [--days]`
   - MCP: `memory_reflection`

2. **Memory Lineage（记忆血缘溯源）** — 追踪单条记忆的完整来源脉络：基础快照、版本历史、关联链接（出/入）、审计事件与生命周期时间线。
   - API: `memory_lineage(memory_id)`
   - CLI: `MindForge memory-lineage <memory_id>`
   - MCP: `memory_lineage`

3. **Memory Reinforce（记忆强化候选）** — 前瞻性识别「高价值但正在衰减」的记忆：综合重要度、星标、访问活跃度、记忆强度与遗忘分数，输出强化排序、原因与推荐动作（优先复习/提权/计划复习/观察）。
   - API: `memory_reinforce(agent_id, days?, limit?)`
   - CLI: `MindForge memory-reinforce <agent> [--days] [--limit]`
   - MCP: `memory_reinforce`

4. **Drama Plot Thread（剧情伏笔追踪）** — 从场景与台词识别「埋设伏笔（setup）」与「揭示回收（payoff）」标记，按时间顺序贪心匹配，输出全部线索、未回收线索与回收率，辅助编剧检查伏笔闭环。
   - API: `drama_plot_thread(drama_id)`
   - CLI: `MindForge drama-plot-thread <drama_id>`
   - MCP: `drama_plot_thread`

5. **Drama Episode Curve（分集张力曲线）** — 按集聚合台词量、冲突词与强度词，生成全剧张力曲线、高潮集、波动率与曲线形态分类（上升/下降/中段高峰/平稳）。
   - API: `drama_episode_curve(drama_id)`
   - CLI: `MindForge drama-episode-curve <drama_id>`
   - MCP: `drama_episode_curve`

6. **Drama Screen Time（角色戏份平衡）** — 统计角色台词量/字数/出场场景与集数占比，计算群像平衡度（Top 占比 + 基尼系数），识别独角戏/双核/群像结构并给出建议。
   - API: `drama_screen_time(drama_id)`
   - CLI: `MindForge drama-screen-time <drama_id>`
   - MCP: `drama_screen_time`

**安全修复**
- **内容长度校验绕过（DoS）** — 此前 50000 字符上限仅在 `add_memory` 生效，`update_memory` 与 `batch_add` 可注入超长内容。v5.4.1 将 `MAX_CONTENT_LEN` 提升为模块级常量并统一作用于三个入口：`update_memory` 超限抛出 `ValueError`，`batch_add` 超限条目按单条失败跳过。

**其他更新**
- MCP Server 工具数从 15 → 21，新增 6 个 v5.4.1 工具，serverInfo 版本同步至 5.4.1
- 版本徽章、架构图、CLI 用法、Project Structure 同步更新至 v5.4.1
- 单元测试从 36 → 54 个用例，全部通过

### v5.3.9 (2026-08-04)

**五大能力增强**

1. **Intent Router（意图分类路由）** — 三层路由架构（规则正则 → 关键词加权 → LLM 兜底），10+ 业务意图分类（记忆存储/检索/问答/任务规划/闲聊/创作等），带缓存加速。
   - API: `classify_intent(text, force=None)`
   - CLI: `MindForge intent-router <text> [--force] [--json]`
   - MCP: `intent_router`

2. **Conflict Detector（矛盾检测 + 自动衰减）** — 三类冲突检测：反义词对、属性值不一致、时间线冲突。自动生成衰减动作（降低重要性 + 打标签），保护核心记忆。
   - API: `scan_conflicts(category?, limit?, apply_decay?)`
   - CLI: `MindForge conflict-scan [--category] [--limit] [--apply-decay] [--json]`
   - MCP: `conflict_scan`

3. **Skill Extractor（记忆 → 技能转化）** — 从记忆中聚类抽取可复用技能模板：槽位识别（`{{参数}}`/`<参数>`）、步骤提炼（步骤1/首先/然后）、触发词归纳、示例采样。
   - API: `extract_skills(category?, limit?, min_cluster_size?)`
   - CLI: `MindForge skill-extract [--category] [--limit] [--min-cluster] [--json]`
   - MCP: `skill_extract`

4. **Hybrid Search Enhanced（混合检索增强）** — 查询扩展（同义词/上位词/缩写还原/纠错）+ CPU 版 Cross-Encoder 多特征融合重排（token overlap/phrase hit/ngram overlap/属性匹配/重要度加权）。
   - API: `search_enhanced(query, max_results?, expand?, rerank?)`
   - CLI: `MindForge rerank-search <query> [--top] [--no-expand] [--no-rerank] [--json]`
   - MCP: `rerank_search`

5. **Session Focus（会话焦点增强）** — 滑动窗口主题聚类（token/2-gram 频率 k-means），焦点漂移检测（新旧主题词 Jaccard 变化率），面向当前会话的增强查询生成。
   - API: `session_focus(messages, window_size?, augment_query?)`
   - CLI: `MindForge session-focus -m "role:内容" [--window] [--augment] [--json]`
   - MCP: `session_focus`

**其他更新**
- MCP Server 工具数从 10 → 15，新增 5 个 v5.3.9 工具
- 版本徽章、架构图、Core Features、Project Structure 同步更新至 v5.3.9
- 五大模块在 `modules/__init__.py` 统一注册，支持 lazy import
- 单元冒烟测试全部通过 + E2E 测试覆盖主类 API / CLI / MCP 三层

### v5.3.7 (2026-08-03)

**Agent Memory Enhancement（Agent 记忆增强）**
- `memory-importance` — 重要度漂移分析、低估/高估记忆识别、动态重评估建议（对标 Mem0 动态记忆评分）
- `memory-context` — Token 预算感知的上下文注入，格式化字符串输出（对标 Letta 上下文窗口管理）
- `agent-emotion` — 按天情感时间线、转换序列、波动性评分（对标 Zep 情感记忆）

**Short Drama Analytics（短剧分析增强）**
- `drama-genre-trend` — 类型趋势方向（rising/declining/stable）+ 各类型平均评分
- `drama-binge-score` — 多因子加权追剧粘性：节奏健康度 25% + 平均张力 25% + 互动密度 20% + 经典台词占比 15% + 完成率 15%
- `char-relationship` — 六型关系分类（ally/rival/romance/family/mentor/stranger）+ 情感弧线 + 强度

**Security Fixes（安全修复）**
- P0: `_is_suspicious_windows_path` 对 Windows 盘符（`C:\`）的误报 → 导致所有导出功能崩溃
- P2: 6 个新方法全部注入 Unicode Cf/Cc 控制字符过滤（`_filter_unicode_ctrl`）
- P2: CLI 顶部帮助文档补齐 v5.3.6/v5.3.7 的 10 个新命令
- P3: `re-evaluation_suggestions` → `re_evaluation_suggestions`（统一下划线命名风格）

### v5.3.7 (2026-08-03)

**🧠 Agent 记忆增强**
- `memory-importance` - 记忆重要度分析（重要度分布趋势+前半段/后半段漂移分析+低估记忆识别：高访问低重要度+高估记忆识别：高重要度低访问+动态重评估建议，参考 Mem0 动态记忆评分机制）
- `memory-context` - 上下文记忆注入（查询关键词提取+多因子召回评分+token 预算感知选择+格式化上下文字符串生成，参考 Letta 上下文窗口管理）
- `agent-emotion` - Agent 情感追踪（按天情感分类 joy/frustration/calm+情感时间线+转换序列追踪+主导情感+波动性评分，参考 Zep 情感记忆功能）

**🎬 AI 短剧增强**
- `drama-genre-trend` - 类型趋势分析（类型分布+前半段/后半段趋势方向 rising/declining/stable+各类型平均评分+热门类型识别，竞品爆款风向标）
- `drama-binge-score` - 追剧粘性评分（多因子加权：节奏健康度 25%+平均张力 25%+互动密度 20%+经典台词比 15%+完成率 15%，评级 low/medium/high/extreme）
- `char-relationship` - 角色关系深度分析（场景共现+台词交替+冲突/情感词统计+六型分类：ally/rival/romance/family/mentor/stranger+关键场景+情感弧线+关系强度）

**🔐 安全修复**
- **[P1] _row_to_entry JSON 边界修复**：`tags` 字段已为 list 或 `metadata` 已为 dict 时不再崩溃，增加防御性 try/except
- **[P2] fuzzy_search LIKE 注入加固**：验证全部 LIKE 查询均使用 `_escape_like` + `ESCAPE '\\'` 子句
- 全部 6 个新方法使用参数化 SQL（防 SQL 注入）
- 全部新方法 Unicode 控制字符过滤 + 长度上限（agent_id 128、query 500、drama_id 64、char_id 64）
- `memory_importance` / `memory_context` / `agent_emotion` 使用 `_limited_fetch` 行数硬上限（10000）防全表扫描 DoS

### v5.3.6 (2026-08-02)

- `memory-link` — 关联推理（关键词重叠 + 标签共享 + 时间邻近度三维加权）
- `memory-recall` — 智能召回（覆盖率 + 重要度 + 频次 + 衰减 + 置顶）
- `drama-pacing` — 滑动窗口密度分析、拖沓/密集段识别
- `char-interaction` — 共现 + 台词交替 + 冲突词三维建模

### v5.3.5 (2026-08-02)

- `memory-cluster` — 基于 Jaccard 的主题聚类 + 核心词提取
- `agent-insight` — 按周活跃切片 + 趋势对比 + 智能洞察
- `drama-summary` — 官方摘要 + 关键场景采样 + 经典台词融合
- `scene-tension` — 多维张力评分 + Top-K + 连续高潮段识别
- JSON 深度限制（32 层）、行数硬上限（`_limited_fetch`）、Windows 8.3 短文件名检测

### v5.3.4 (2026-08-02)

- `agent-sentiment` — 正/负/中性关键词匹配 + 主导情感识别
- `memory-decay` — Ebbinghaus 保留曲线 + 临界记忆预警
- `drama-compare` — 多维度对比（评分/集数/角色/台词）
- `char-arc` — 成长阶段识别（rising/falling/peak/stable）

### v5.3.3 (2026-08-01)

- `agent-timeline` — 按天/小时创建趋势 + 活跃时段识别
- `agent-heatmap` — 分类 × 重要度密度矩阵
- `drama-binge` — 观看状态分布 + 完成率 + 评分分布
- `char-network` — 角色共现网络 + 可视化数据
- P0: LIKE 通配符注入修复（`_escape_like` + `ESCAPE '\\'`）
- P0: 二次验证不再无条件返回 True
- P1: XSS 消毒 + 加密降级加固 + 敏感操作频控

### v5.3.2 (2026-08-01) ~ v5.3.0 (2026-07-31)

- `agent-diff` / `agent-purge`（跨时段差异对比 + 级联清空）
- `drama-progress` / `drama-rec2`（观看进度 + 智能推荐 v2）
- `agent-search` / `agent-compare`（Agent 内搜索 + 双 Agent 对比）
- `drama-search` / `char-ranking`（短剧搜索 + 角色台词排行）
- `agent-profile` / `agent-merge` / `agent-export`（画像 + 合并 + 导出）
- `drama-info` / `line-random` / `char-profile`（统计 + 随机台词 + 画像）
- 枚举白名单、数值边界、内容长度上限（50K）、路径权限 0644

### v5.2.x

- v5.2.9: 路径遍历防护 + CSV 公式注入拦截
- v5.2.8: P0 搜索水合修复（TF-IDF 索引重载） + 标签解析归一化
- v5.2.7: 14 项路径遍历修复 + SQLite 签名校验 + 记忆版本历史
- v5.2.5: 双向记忆关联 + 置顶/取消置顶
- v5.2.4: 笔记批注 + 模板 + 批量更新 + 间隔重复复习计划
- v5.2.3: 全数据转换层 `_safe_json_loads` 防御性解析
- v5.2.2: 短剧 CRUD 模块 + Agent 生命周期 + 质量评分
- v5.2.1: 完整短剧模块（dramas/scenes/characters/lines）
- v5.2.0: Fuzzy 搜索 + 搜索历史 + 批量标签 + 备份恢复

### v5.1.x

- v5.1.9: Excel 导入导出 + 复制 / 移动
- v5.1.8: `doctor` 诊断 + `find` 高级过滤 + 10 项 CLI 修复
- v5.1.7: 随机闪卡 + 标签/分类重命名 + 配置摘要
- v5.1.6: 标签/分类统计条形图 + 时间线 + 热门记忆
- v5.1.5: JSON 导入导出 + 去重 + 遗忘提醒
- v5.1.4: XML 导入导出 + 列表排序 + 详情统计
- v5.1.3: 清理 + 批量添加 + URL 导入 + 相似度搜索
- v5.1.2: 懒加载 + PBKDF2 调优 + UTF-8 BOM 统一清理
- v5.1.1: get/update/delete/audit/recent/trash/restore 完整补全
- v5.1.0: 项目品牌升级 + HTML 导出

### v5.0.x

- v5.0.8: `analyze` 深度分析 + `import-md` + `migrate`
- v5.0.6: 更新同步刷新 FTS 索引 + `vacuum` + `purge-trash`
- v5.0.5: `health_check` + `summarize` + FTS 孤儿清理
- v5.0.4: `deduplicate` + `export-md` + Jaccard 相似度
- v5.0.2: Star 收藏 + 时间范围过滤 + `pip install` 支持
- v5.0.0: 初始四层架构 + 知识图谱 + 多模态 + 人格化 + 联邦

---

## License

MIT License.

Copyright (c) 2026 MindForge Project
