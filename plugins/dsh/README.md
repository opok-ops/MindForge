# mindforge-dsh-plugin

> Give your DeepSeek Harness agent a **persistent, 4-layer memory engine** — local-first, encrypted, with knowledge graph and federation support.

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![dsh-plugin](https://img.shields.io/badge/dsh-plugin-purple.svg)](https://github.com/topics/dsh-plugin)
[![MindForge v5.8.13](https://img.shields.io/badge/MindForge-v5.8.13-green.svg)](https://github.com/opok-ops/MindForge)

## What it does

DeepSeek Harness is an incredible agent runtime, but like every coding agent it's **amnesiac between sessions**. Close the terminal, come back tomorrow, and it's forgotten your project conventions, past decisions, and the gotchas you talked through last week.

This plugin fixes that by connecting Harness to [MindForge](https://github.com/opok-ops/MindForge) — a local-first AI agent memory engine with:

- **4-layer memory architecture**: sensory → short-term → long-term → permanent, with automatic decay and consolidation
- **AES-256 encryption** at rest (PBKDF2-SHA256, 100k iterations)
- **Knowledge graph** for semantic relationships between memories
- **Full-text search** with TF-IDF + fuzzy matching (embedding-ready)
- **Audit log** — every read/write is tracked
- **Auto-archive** — stale short-term memories move to archived storage, recoverable
- **Zero cloud dependency** — everything runs on localhost

## How it works

```
┌─────────────────────────────────┐     HTTP      ┌──────────────────────────┐
│  DeepSeek Harness (TypeScript)  │  localhost    │  MindForge (Python)      │
│                                 │ ──────────►   │                          │
│  ┌───────────────────────────┐  │               │  ┌────────────────────┐  │
│  │ mindforge-dsh-plugin      │  │               │  │ REST API :8765     │  │
│  │                           │  │               │  │ /api/memories      │  │
│  │ • memory_add tool         │  │               │  │ /api/search        │  │
│  │ • memory_search tool      │  │               │  │ /api/stats         │  │
│  │ • turn/start → recall     │  │               │  └────────────────────┘  │
│  │ • turn/end → auto-capture │  │               │                          │
│  └───────────────────────────┘  │               │  ┌────────────────────┐  │
│                                 │               │  │ SQLite (WAL mode)  │  │
│  ctx.tools, ctx.agentLoop       │               │  │ 4-layer + graph    │  │
└─────────────────────────────────┘               │  │ AES-256 encrypted  │  │
                                                  │  └────────────────────┘  │
                                                  └──────────────────────────┘
```

The plugin is a **native Cordis plugin** (not MCP), running in-process with Harness. It communicates with MindForge's REST API over localhost HTTP — no external network calls.

## Quick start

### 1. Install MindForge backend

```bash
pip install -e git+https://github.com/opok-ops/MindForge.git#egg=mindforge-memory
```

Or clone and install:
```bash
git clone https://github.com/opok-ops/MindForge.git
cd MindForge
pip install -e .
```

### 2. Start the MindForge REST API

```bash
# Initialize (first time only — sets up encryption)
MindForge init

# Start the API server
MindForge serve --api --port 8765
```

> Note: the CLI command is `MindForge` (as declared in the package's `[project.scripts]`).

### 3. Install this plugin in DSH

```bash
dsh plugin --profile web add github:opok-ops/MindForge#plugins/dsh
```

Or from npm (coming soon):
```bash
dsh plugin --profile web add mindforge-dsh-plugin
```

### 4. Restart Harness

```bash
dsh web
```

Your agent now has persistent memory. Try saying:

> "Remember that this project uses pnpm, not npm"

Then start a new session and ask:

> "What package manager does this project use?"

## Tools registered

The plugin registers these tools that the agent can call autonomously:

| Tool | Description |
|------|-------------|
| `memory_add` | Store information in long-term memory |
| `memory_search` | Search memories by natural language query |
| `memory_get` | Retrieve a specific memory by ID |
| `memory_list` | List recent memories with pagination / category filter |
| `memory_update` | Update a memory's content, importance, or tags |
| `memory_delete` | Delete a memory (use sparingly) |
| `memory_stats` | Get memory store statistics |
| `memory_tags` | List all tags with usage counts |
| `memory_star` | Star / unstar a memory for quick access |

## Automatic behavior

Beyond the explicit tools, the plugin hooks into the agent lifecycle:

- **turn/start**: Automatically searches memory for context relevant to the user's message and makes it available to the agent.
- **turn/end**: Auto-captures a summary of substantive turns (>5s) as new memories, so future sessions benefit from past interactions.

Both behaviors can be disabled in configuration.

## Configuration

Edit `cordis.patch.yml` in your DSH profile:

```yaml
- id: mindforge-memory
  plugin:
    name: mindforge-dsh-plugin
    inject:
      - tools
      - agentLoop
  config:
    host: "127.0.0.1"
    port: 8765
    autoStart: true              # Auto-start MindForge backend
    mindforgePath: "/path/to/MindForge"  # Required for autoStart
    pythonPath: "python3"
    dbPath: "mindforge_agent.db"
    autoCapture: true            # Auto-capture turn summaries
    autoInject: true             # Auto-inject recalled memories
    maxInjectMemories: 5         # Max memories per turn injection
    minRelevance: 0.3            # Min relevance for auto-injection
    captureTags: ["dsh", "agent-session"]
    captureImportance: "MEDIUM"
```

## How this differs from Hindsight

| Feature | Hindsight | mindforge-dsh-plugin |
|---------|-----------|---------------------|
| Architecture | Cloud-first / self-hosted bank | **Local-first** (SQLite on disk) |
| Memory model | Flat memory bank | **4-layer** (sensory → short → long → permanent) |
| Encryption | Depends on deployment | **AES-256** at rest (always on) |
| Knowledge graph | No | **Yes** — semantic relationships |
| Federation | No | **Yes** — multi-agent memory sharing |
| Auto-decay | No | **Yes** — memories consolidate and decay |
| Audit log | No | **Yes** — every read/write tracked |
| Cross-agent | Yes (shared bank) | Via federation protocol |
| Offline | No (needs cloud) | **Yes** — fully offline capable |

## Development

```bash
# Clone
git clone https://github.com/opok-ops/MindForge.git
cd MindForge/plugins/dsh

# Install deps
npm install

# Build
npm run build

# Local test in DSH
dsh plugin --profile web add link:$(pwd)
dsh web
```

## Requirements

- DeepSeek Harness v0.2.0-rc.2+ (`npx @deepseek-ai/dsh web`)
- Node.js 22.19+ (DSH requirement)
- Python 3.10+ (for MindForge backend)
- MindForge v5.8.13+

## License

MIT — same as DeepSeek Harness and MindForge.
