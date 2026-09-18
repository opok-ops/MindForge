# MindForge

**Production-Grade Lifelong Memory System for AI Agents**

Four-Tier Memory · Knowledge Graph · Local-First · AES-256-GCM Encryption · Federated P2P · MCP Server

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-5.7.2-green.svg)](https://github.com/opok-ops/MindForge)
[![CI](https://github.com/opok-ops/MindForge/actions/workflows/ci.yml/badge.svg)](https://github.com/opok-ops/MindForge/actions/workflows/ci.yml)

> 🇨🇳 [中文文档](README.zh-CN.md) | 📖 [Documentation](docs/) | 📝 [Changelog](CHANGELOG.md) | ⭐ [Discussions](https://github.com/opok-ops/MindForge/discussions)

---

## Why MindForge?

Most AI agent memory solutions are cloud-locked, flat, and slow. MindForge gives your agents **human-like memory** that runs entirely on your machine.

| | MindForge | Mem0 | Letta | Zep |
|---|---|---|---|---|
| **Local-first** | ✅ Zero cloud | ❌ Cloud | ❌ Cloud | ❌ Cloud |
| **Encryption** | ✅ AES-256-GCM | ❌ | ❌ | ⚠️ Limited |
| **Memory model** | ✅ 4-tier + Ebbinghaus | ❌ Flat | ❌ Blocks | ❌ Timeline |
| **Search** | ✅ 6-way hybrid | Vector only | Vector only | Vector only |
| **Federated P2P** | ✅ ACL + conflicts | ❌ | ❌ | ❌ |
| **Knowledge graph** | ✅ Built-in | ❌ | ❌ | ✅ |
| **MCP Server** | ✅ 33 tools | ❌ | ❌ | ❌ |
| **CLI** | ✅ 200+ commands | ❌ | ❌ | ❌ |
| **License** | MIT | Apache 2.0 | Apache 2.0 | Apache 2.0 |

---

## Quick Start (30 seconds)

```bash
git clone https://github.com/opok-ops/MindForge.git
cd MindForge
pip install -e .
```

```python
from MindForge import MindForge

m = MindForge(db_path="./data/memory.db", encrypted=False)
m.add("User prefers type-hinted Python code", category="preferences", importance="HIGH")
result = m.search("coding preferences")
print(result.chunks[0].content)  # → User prefers type-hinted Python code
```

**CLI:**
```bash
MindForge add "User likes cats" --category preferences --importance HIGH
MindForge search "what does user like"
MindForge stats
```

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Cognitive Layer  Personality · KnowledgeGraph       │
│                   MemoryEvolution · FederatedMemory  │
├──────────────────────────────────────────────────────┤
│  Function Layer   RecallEngine · HybridSearch        │
│                   IntentRouter · ConflictDetector     │
│                   SkillExtractor · SessionFocus       │
├──────────────────────────────────────────────────────┤
│  Core Layer       Storage (SQLite+FTS5) · Index     │
│                   Encryption (AES-256-GCM) · Query  │
├──────────────────────────────────────────────────────┤
│  Adapter Layer    OpenClaw · Claude Code · MCP       │
│                   REST API · CLI · Python SDK         │
└──────────────────────────────────────────────────────┘
```

### Four-Tier Memory Model

| Tier | Retention | Capacity | Purpose |
|------|-----------|----------|---------|
| Sensory | sec ~ min | ~50 | Input buffer, noise filtering |
| Short-term | hrs ~ days | ~100 | Working memory, active session |
| Long-term | wks ~ months | unlimited | Consolidated semantic memory |
| Permanent | forever | unlimited | Core knowledge, preferences, key experience |

Memory propagates upward based on the **Ebbinghaus forgetting curve** — high-value entries strengthen over time, low-value entries decay naturally.

---

## Performance

Tested on i7-12700H / 32GB RAM / NVMe SSD / Python 3.12 / SQLite WAL.

| Operation | 1K | 10K | 100K |
|-----------|-----|------|------|
| Write (plaintext) | 0.3 ms | 0.5 ms | 0.9 ms |
| Write (encrypted) | 0.8 ms | 1.2 ms | 2.1 ms |
| TF-IDF Search P50 | 4 ms | 12 ms | 38 ms |
| FTS5 Search P50 | 0.4 ms | 0.9 ms | 3.2 ms |
| Cross-Encoder Rerank P50 | 5 ms | 15 ms | 45 ms |
| Consolidate (100 entries) | 45 ms | 120 ms | 580 ms |

**Retrieval Quality** (500 multi-domain memories, 50 annotated queries):

| Metric | TF-IDF | +FTS5+Fuzzy | +Cross-Encoder | +Query Expansion + Vector |
|--------|--------|-------------|----------------|---------------------------|
| MRR@10 | 0.62 | 0.71 | **0.82** | **0.85** |
| NDCG@10 | 0.60 | 0.69 | **0.80** | **0.84** |
| Recall@10 | 0.70 | 0.80 | **0.88** | **0.92** |

---

## Key Features

- **Six-Way Hybrid Search** — Vector + FTS5 + TF-IDF + Fuzzy + Query Expansion + Cross-Encoder reranking
- **Knowledge Graph** — Auto entity/relation extraction, path finding, associative recall
- **Privacy Engine** — 4-level isolation (PUBLIC/INTERNAL/PRIVATE/STRICT) + AES-256-GCM + PBKDF2-SHA256
- **Federated Memory** — P2P multi-agent sharing with trust levels, fine-grained ACL, conflict resolution
- **Intent Router** — 3-layer routing (regex → keyword → LLM fallback), 10+ intent categories
- **Conflict Detection** — Antonym pairs, attribute inconsistency, timeline conflicts + auto-decay
- **Skill Extractor** — Cluster memories → slots → steps → trigger words → reusable skill templates
- **MCP Server** — 33 tools including intent_router, conflict_scan, memory_diff
- **CLI** — 200+ commands with bash/zsh/fish shell completion
- **REST API** — Standard HTTP API for non-Python applications
- **Embedding Backends** — sentence-transformers / OpenAI / Ollama / custom HTTP
- **751+ Test Cases** — Comprehensive test coverage with CI security scanning (bandit + pip-audit)

---

## Integration

### Claude Code

```python
from MindForge.adapters import ClaudeCodeAdapter

adapter = ClaudeCodeAdapter.from_env()
adapter.remember("User prefers concise code style", ["preferences"])
context = adapter.get_context("database optimization")
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

## Documentation

- [Full Documentation](docs/)
- [CLI Reference](docs/cli-reference.md)
- [API Reference](docs/api-reference.md)
- [MCP Tools](docs/mcp-tools.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

---

## Roadmap

- [ ] v6.0 — Multi-agent collaboration ports, memory preview streaming
- [ ] Vector database backend (Qdrant / Chroma)
- [ ] Web UI dashboard
- [ ] Plugin ecosystem

---

## Security & Upgrade Notes

- **`MINDFORGE_PASSWORD` (encrypted databases)**: with an encrypted database (key file present), set the `MINDFORGE_PASSWORD` environment variable so the process can unlock it. This is required for the **dsh-mindforge bridge** and any non-interactive integration (MCP / REST API / scripts) that reads or writes an encrypted store; otherwise operations fail with an "encrypted database needs a password" error.
- **`EXPERIMENTAL_HMAC_XOR` migration**: v5.5.7 removed the HMAC-XOR 降级加密 (downgrade encryption) path. Any blobs created under that experimental downgrade mode can no longer be decrypted. Back up your database **before upgrading** if you previously enabled this flag.

---

## License

[MIT](LICENSE) — Copyright (c) 2026 MindForge Project
