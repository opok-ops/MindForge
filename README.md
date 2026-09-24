# MindForge

**A local-first, production-grade memory system for AI agents.**

> **Maintainer** — **Xiao Gu (小顾)**, born in 2008 · GitHub: [opok-ops](https://github.com/opok-ops) · 抖音: [shidianduo3116](https://v.douyin.com/ZIsw2VpuT9o/) · 小红书: [4406811524](https://xhslink.cn/o/3m71WBRPD17) · Feedback: [2638895480@qq.com](mailto:2638895480@qq.com)

MindForge gives AI agents a persistent, structured long-term memory: a four-tier lifecycle model, knowledge graphs, hybrid retrieval, end-to-end encryption, and MCP / CLI / REST interfaces. All data stays on your machine.

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-5.8.0-green.svg)](https://github.com/opok-ops/MindForge/releases)
[![Stars](https://img.shields.io/github/stars/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/stargazers)
[![Forks](https://img.shields.io/github/forks/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/forks)
[![Open Issues](https://img.shields.io/github/issues/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/issues)
[![Last Commit](https://img.shields.io/github/last-commit/opok-ops/MindForge.svg)](https://github.com/opok-ops/MindForge/commits/master)
[![CI](https://github.com/opok-ops/MindForge/actions/workflows/ci.yml/badge.svg)](https://github.com/opok-ops/MindForge/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-pages-blue.svg)](https://opok-ops.github.io/MindForge/)

[English](README.md) · [中文文档](README.zh-CN.md) · [Live Demo](https://opok-ops.github.io/MindForge/) · [Documentation](docs/) · [Discussions](https://github.com/opok-ops/MindForge/discussions)

---

## Overview

Agent memory solutions that rely on cloud backends introduce latency, cost, and privacy trade-offs. MindForge takes the opposite approach: memory is computed and stored locally, encrypted at rest, and exposed through standard interfaces your existing agents already speak.

Key design decisions:

- **Local-first.** No cloud dependency. Storage runs on SQLite + FTS5 with WAL, a single file that moves with you.
- **Structured memory.** Four tiers (sensory, short-term, long-term, permanent) with promotion and decay driven by the Ebbinghaus forgetting curve.
- **Retrieval quality.** Six complementary retrieval paths fused into one ranked result, instead of a single vector index.
- **Encryption by default.** AES-256-GCM at rest with PBKDF2 key derivation; encrypted backups you can export and trust.
- **Open interfaces.** MCP server (40 tools), Python SDK, CLI with shell completion, and a REST API for non-Python consumers.

## Quick Start

Install from source, then start writing and reading memories in minutes:

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
print(result.chunks[0].content)
```

CLI:

```bash
MindForge add "User likes cats" --category preferences --importance HIGH
MindForge search "what does user like"
MindForge stats
```

## Features

**Retrieval**

- **Six-way hybrid search** — vector, FTS5, TF-IDF, fuzzy, query expansion, and cross-encoder reranking fused into a single ranked result set.
- **Knowledge graph** — automatic entity and relation extraction, path finding, and associative recall.
- **Weighted recall** — multi-factor scoring (coverage, importance, access frequency, time decay) instead of similarity alone.

**Memory lifecycle**

- **Four-tier memory** — sensory buffer, short-term, long-term, and permanent tiers with Ebbinghaus-based decay and periodic consolidation.
- **Conflict detection** — antonym pairs, attribute inconsistencies, and timeline conflicts, with automatic decay.
- **Procedural memory (Cases → Skills)** — `record_experience()` stores agent success cases, `distill_skills()` turns them into reusable skill templates (persisted, restart-safe), `skill_match()`/`skill_render()` reuse them on new tasks with parameterized steps (v5.8.0).
- **Knowledge-graph pipeline** — `auto_extract_graph` hooks into `add()`; `extract_graph()` draws entities/relations into a persisted graph (survives restarts), `graph_related`/`graph_path` query it, STRICT-privacy memories are never extracted (v5.7.9).
- **Reproducible embedding eval** — `benchmarks/embedding_eval.py` compares hybrid / vector / FTS5 / TF-IDF on a fixed multilingual set with fixed seeds (Recall@5 / MRR@10 / NDCG@10); deterministic `fake` backend runs without any model for CI baselines (v5.7.8).
- **Agent memory governance** — `agent_pin` freezes decay and pins key memories, `agent_forget` soft-deletes with a reason, `agent_decay_boost` accelerates forgetting, and `expire_memory` closes a fact's validity window (v5.7.7).
- **Conflict auto-reconcile** — `reconcile_conflicts` closes the stale side of `keep_newer` / `keep_higher_importance` conflicts (Bi-temporal expiry, never deletes), and routes `merge` / `review_needed` to human review (v5.7.7).
- **Connector framework** — pluggable `json` / `csv` / `markdown` / `file` / `url` connectors with a registry and SSRF guard; extensible via `register_connector` (v5.7.7).
- **Bi-temporal facts** — `valid_from` / `valid_to` windows with as-of queries (`valid_at`) and `supersede` fact replacement that expires the old fact instead of deleting it, keeping full version history (v5.7.6).
- **Skill extraction** — clusters memories into reusable slots, steps, and trigger words.

**Privacy and security**

- **Privacy engine** — four isolation levels (PUBLIC / INTERNAL / PRIVATE / STRICT) with fine-grained access control.
- **At-rest encryption** — AES-256-GCM with PBKDF2-SHA256 key derivation.
- **Encrypted export / import** — `export-json --password` and `import-json --password` produce encrypted backups that remain unreadable even if the file leaks (v5.7.3).
- **GDPR toolkit** — `gdpr-report` / `gdpr-export-all` / `gdpr-erase` for data portability and the right to be forgotten, with automatic backup before erasure (v5.7.6).

**Integration**

- **MCP server** — 40 tools, drop into Claude Code, OpenClaw, or any MCP client.
- **Federated memory** — P2P sharing between agents with trust levels, ACLs, and conflict resolution.
- **REST API** — standard HTTP endpoints for non-Python applications.
- **CLI** — 200+ commands with bash / zsh / fish completion.
- **Embedding backends** — sentence-transformers, OpenAI, Ollama, or a custom HTTP endpoint.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│           MindForge v5.8.0            │
│  Cognitive Layer  Personality · KnowledgeGraph       │
│                   MemoryEvolution · FederatedMemory  │
├──────────────────────────────────────────────────────┤
│  Function Layer   RecallEngine · HybridSearch        │
│                   IntentRouter · ConflictDetector    │
│                   SkillExtractor · SessionFocus       │
├──────────────────────────────────────────────────────┤
│  Core Layer       Storage (SQLite+FTS5) · Index     │
│                   Encryption (AES-256-GCM) · Query  │
├──────────────────────────────────────────────────────┤
│  Adapter Layer    OpenClaw · Claude Code · MCP       │
│                   REST API · CLI · Python SDK        │
└──────────────────────────────────────────────────────┘
```

### Four-Tier Memory Model

| Tier | Retention | Capacity | Purpose |
|------|-----------|----------|---------|
| Sensory | sec ~ min | ~50 | Input buffer, noise filtering |
| Short-term | hrs ~ days | ~100 | Working memory, active session |
| Long-term | wks ~ months | unlimited | Consolidated semantic memory |
| Permanent | forever | unlimited | Core knowledge, preferences, key experience |

High-value entries strengthen over time through consolidation; low-value entries decay naturally.

## Performance

Benchmarked on i7-12700H / 32GB RAM / NVMe SSD / Python 3.12 / SQLite WAL.

| Operation | 1K | 10K | 100K |
|-----------|-----|------|------|
| Write (plaintext) | 0.3 ms | 0.5 ms | 0.9 ms |
| Write (encrypted) | 0.8 ms | 1.2 ms | 2.1 ms |
| TF-IDF Search P50 | 4 ms | 12 ms | 38 ms |
| FTS5 Search P50 | 0.4 ms | 0.9 ms | 3.2 ms |
| Cross-Encoder Rerank P50 | 5 ms | 15 ms | 45 ms |
| Consolidate (100 entries) | 45 ms | 120 ms | 580 ms |

Retrieval quality (500 multi-domain memories, 50 annotated queries):

| Metric | TF-IDF | +FTS5+Fuzzy | +Cross-Encoder | +Query Expansion + Vector |
|--------|--------|-------------|----------------|---------------------------|
| MRR@10 | 0.62 | 0.71 | **0.82** | **0.85** |
| NDCG@10 | 0.60 | 0.69 | **0.80** | **0.84** |
| Recall@10 | 0.70 | 0.80 | **0.88** | **0.92** |

## Integration

**Claude Code**

```python
from MindForge.adapters import ClaudeCodeAdapter

adapter = ClaudeCodeAdapter.from_env()
adapter.remember("User prefers concise code style", ["preferences"])
context = adapter.get_context("database optimization")
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

## Security

- **Encrypted databases**: when a key file is present, set `MINDFORGE_PASSWORD` so the process can unlock the store. This is required for the dsh-mindforge bridge and any non-interactive integration (MCP / REST API / scripts) accessing an encrypted store.
- **API authentication**: if `MINDFORGE_API_KEY` is configured, unauthenticated callers are treated as `anonymous` and can only reach PUBLIC memories. All read/write paths carry actor identity for authorization and audit.
- **Access logs are sanitized**: search terms and memory IDs are stripped from logs.
- **Upgrade note**: v5.5.7 removed the experimental HMAC-XOR downgrade path (config flag `EXPERIMENTAL_HMAC_XOR`, 即「降级加密」). Blobs created under that flag can no longer be decrypted — back up your database before upgrading if you enabled it.

See [SECURITY.md](SECURITY.md) for the full security policy.

## Roadmap

- [ ] v6.0 — multi-agent collaboration ports, memory preview streaming
- [ ] Vector database backend (Qdrant / Chroma)
- [ ] Web UI dashboard
- [ ] Plugin ecosystem

## Documentation

- [Full documentation](docs/)
- [Changelog](CHANGELOG.md)
- [Contributing guide](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## License

[MIT](LICENSE) — Copyright (c) 2026 MindForge Project. If MindForge is useful to you, consider starring it on GitHub — it helps the project stay active and visible.