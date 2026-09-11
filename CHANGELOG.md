# Changelog

All notable changes to MindForge will be documented in this file.

## [5.6.1] - 2026-09-11

### Security (P1)
- **审计事务竞态修复**：`delete_memory` / `purge_trash` 等写操作的审计日志并入同一事务（`commit=False` + `@_with_rollback`），堵住"数据已提交但审计失败"的竞态窗口
- **备份路径校验**：`backup()` 写入路径加 `_safe_path` 校验（目录校验 + 最终文件校验），防路径遍历写敏感目录
- **API TLS 支持**：`start_api_server` 新增 `ssl_certfile` / `ssl_keyfile` 参数，强制 TLSv1.2+；CLI 加 `--ssl-cert` / `--ssl-key` 参数

### Security (P2)
- **XSS 未闭合标签绕过**：`_XSS_RE` 正则从 `<[^>]*>` 改为 `<[a-zA-Z!/?][^>\s]*[^>]*>?`，匹配无 `>` 的不完整标签（如 `<img onerror=alert(1)`）
- **API fail-closed 启动**：非 localhost 绑定且未设 `MINDFORGE_API_KEY` 时直接 `raise SecurityError` 拒绝启动
- **写操作限流**：POST/PUT/DELETE 全部过 `_check_rate_limit()`，此前仅 GET 限流
- **storage.py docstring 版本**：v5.5.8 → v5.6.1

### Performance (P2)
- **N+1 写入优化**：新增 `StorageEngine.bulk_update_memory_fields()` 单事务批量更新标量字段（strength/forgetting_score/metadata），字段白名单安全校验。`evolution.update_forgetting_scores` 和 `memory_decay.compute_decay_scores` 从 N 次事务降为 1 次

## [5.6.0] - 2026-09-09

### Security
- **PBKDF2 版本化 KDF 参数（P0 安全债偿还）**：60k → 600k 迭代次数可平滑迁移。密文头存储 `iterations`，解密时按头参数走，新加密使用 600k。提供 `mindforge rekey` 命令一键升级（支持 `--upgrade-only` 仅升级参数不改密码），旧密文自动兼容。密码验证使用 key 文件内存储的加密 token，防止误输密码后批量重加密
- **rekey 失败恢复链完整化**：`rekey_memories` 抛异常时自动恢复 `.key.bak` 到 `.key`，同步恢复新旧引擎和全局引擎引用，确保旧密文 + 旧密钥一致（此前仅回滚数据库事务但密钥文件已换成新的，会导致全部解密失败）

### Added
- **记忆衰减/遗忘引擎（P2 特性）**：`modules/memory_decay.py` 实现基于 Ebbinghaus 遗忘曲线的记忆生命周期管理。三套预设策略（conservative/balanced/aggressive），支持衰减评分 → 强度归档 → 过期清除的完整 GC 周期。`mindforge gc` 命令支持 `--dry-run` 预览、`--status` 统计、`--skip-purge` 保守模式
- **记忆库备份/恢复（P1）**：`mindforge backup` 创建 ZIP 完整快照（数据库+密钥+配置+清单），`mindforge backup-restore` 从 ZIP 恢复，支持加密模式、force 覆盖。加密模式下 CLI 明确警告备份包含密钥文件
- **按 ID 批量归档**：`StorageEngine.archive_memories_by_ids()` 支持按强度阈值选择性归档（原有 `auto_archive` 仅支持按时间）
- **CI sitemap lastmod 自动更新**：部署流程中自动将 sitemap.xml 的 lastmod 更新为当天日期
- **woff2 字体自托管**：Sora/Inter/JetBrains Mono 的 14 个 woff2 文件本地托管（315KB），Google Fonts 仅保留 Noto Sans SC
- **测试补盲（P1）**：新增 34 个测试覆盖 skill_extractor（模板渲染/序列化/聚类）、share_conflict（detect_incoming/dismiss/stats/cleanup）、federated_acl（通配符/多操作/filter_peers/规则管理）
- **衰减/GC 测试**：24 个测试覆盖 DecayConfig 策略、compute_strength 衰减计算、保护规则、归档/删除流程、完整 GC 周期

### Changed
- CLI 命名修正：备份恢复命令从 `restore`（与回收站恢复冲突）改为 `backup-restore`，保持 `restore` 专用于回收站

## [5.5.10] - 2026-09-08

### Fixed
- **XML ParseError not caught**: `import-xml` only caught `(ValueError, TypeError)` but `ET.ParseError` inherits from `SyntaxError`; malformed XML now shows friendly error instead of traceback
- **serve command bind host**: Web UI mode ignored `--host` and always bound `0.0.0.0`; now respects `--host` (default `127.0.0.1`), matching API mode behavior
- **pip-audit CI red**: cryptography 49.0.0 had CVE-2026-69247; bumped minimum to cryptography>=50.0.1 (0 known vulnerabilities). Also narrowed audit scope to project direct dependencies only (`-r requirements` mode) to eliminate runner pre-installed package CVE noise and the `--strict --skip-editable` interaction bug

### Added
- **XXE protection in import-xml**: DOCTYPE/ENTITY declarations are rejected before XML parsing, preventing billion-laughs and external entity attacks
- **Dynamic version assertion**: website version numbers injected from JSON-LD `softwareVersion` single source; no more hardcoded version drift

### Changed
- **PBKDF2 comment corrected**: 60,000 iterations is below OWASP 2023 recommendation of 600,000; comment now accurately reflects the performance tradeoff
- **setup.py classifiers**: added Python 3.13 (was only in pyproject.toml)
- **Removed deprecated `MindForge_combined.py`**: 90KB dead code that was marked DEPRECATED; also eliminates bandit scan noise
- **CI security job**: setuptools and wheel upgraded to latest before pip-audit to clear runner-bundled CVE versions

### Security
- **CI security gate is now real**: removed `pytest || test_core.py` and `|| true` fallbacks in v5.5.9; CI now genuinely fails red on breakage. All 5 Python versions + security job pass green as of this release

## [5.5.9] - 2026-09-04

### Fixed
- **CI fallback removed (P0)**: `pytest || test_core.py` and `|| true` on smoke test were masking failures; CI now fails red when tests actually break
- **CLI main() signature (P0)**: `main()` did not accept `argv` parameter, causing `main(['--help'])` TypeError in CI smoke test; fixed to `main(argv=None)` with `parse_args(argv)`
- **CHANGELOG typo**: lowercase `mindforge export` → `MindForge export` (Linux command-not-found)

### Changed
- **License unified**: README and MindForge.py changed from "MIT + Privacy Addendum" to pure MIT, matching LICENSE file
- **Dynamic version injection**: website version numbers read from JSON-LD `softwareVersion` via JS, no longer hardcoded in 6 places
- **Dynamic number injection**: toolCount/moduleCount/testCount in JSON-LD as single source; inline text uses `data-num` attrs
- **Number fuzzing**: inline mentions use approximate display (30+ tools, 370+ tests) via JS injection
- **Async font loading**: Google Fonts loaded asynchronously with `media="print" onload` pattern + local fallback CSS
- **robots.txt**: merged duplicate User-agent blocks; removed Disallow (page-level noindex is canonical)
- **Action versions upgraded**: checkout@v5, setup-python@v6, upload-pages-artifact@v4 (Node.js 20 deprecation)

### Added
- **SECURITY.md**: vulnerability reporting policy (email, response timeline, scope)
- **LICENSE file**: standard MIT text, GitHub now recognizes and displays badge
- **CI security job**: `bandit` (AST security scan) + `pip-audit` (dependency vulnerability check)
- **Python 3.13**: added to CI matrix and pyproject classifiers
- **v5.5.8 GitHub Release**: first official release with release notes

## [5.5.8] - 2026-09-01

### Added
- **Memory Version Diff (记忆版本差异对比)**: `memory_diff(version_a, version_b)` — compare two historical versions of a memory, returning structured diff for content (unified diff), category, tags (added/removed), and importance changes
  - API: `mf.memory_diff(version_id_a, version_id_b)`
  - CLI: `MindForge memory-diff <version_a> <version_b> [--json]`
  - MCP: `memory_diff` tool with `version_a` / `version_b` parameters
- **MCP Parameter Validation**: All 33 MCP tool handlers now validate required parameters before execution, returning clear `{"ok": false, "error": "Missing required parameter(s): ..."}` instead of crashing with `KeyError`

### Fixed
- **P0: storage.py `__version__` NameError**: `export_agent_memories()` referenced undefined `__version__` variable, causing crash when exporting agent memories. Added `__version__` import with fallback.
- **P1: Falsy enum values silently overridden in `add()`**: `privacy or default` pattern caused valid falsy values like `PrivacyLevel.NONE` to be replaced by config defaults. Changed to explicit `is not None` checks for `privacy`, `importance`, `layer`, and `memory_type` parameters.
- **P1: CLI error message string concatenation**: Multi-line password error message was concatenated without spaces, producing mangled output. Fixed with proper string formatting.
- **P1: `cmd_list` showed global total instead of filtered count**: When using category/layer/starred/date filters, the displayed total was always the global database count instead of the filtered result count.
- **P2: REST API JSON body type validation**: `POST /api/memories`, `POST /api/import`, and `PUT /api/memories/{id}` now validate that the request body is a JSON object (dict), returning 400 error for arrays/strings/numbers instead of crashing with `AttributeError`.
- **P2: Encryption engine input validation**: `encrypt()`, `decrypt()`, and `hash()` now validate input types, raising `SecurityError` for non-string / non-EncryptedBlob inputs instead of bare `AttributeError`.
- **P2: MCP `_handle_tools_call` params type validation**: JSON-RPC `params` and `arguments` fields are now validated as dicts before accessing nested keys, preventing `AttributeError` on malformed requests.
- **P2: Stale docstrings**: Updated version strings in `core/storage.py`, `core/mindforge.py`, `core/encryption.py`, `cli/main.py`, `api/server.py` docstrings from stale versions to v5.5.8.

### Fixed (Comprehensive Audit, 2026-09-01)
- **P0: `storage.py` character network NameError**: `get_character_network()` referenced undefined `a` / `b` variables (should be loop variables `primary_char` / `partner_char`) when building the co-occurrence edge list, crashing the character relationship network feature with `NameError`. Fixed variable references.
- **P1: CLI `sqlite3` not imported**: Several `except sqlite3.Error` branches referenced the unimported `sqlite3` module, so real database errors were masked by a secondary `NameError`. Added `import sqlite3` to `cli/main.py`.
- **P1: CLI crash on unknown command**: `_main_dispatch()` called `parser`, a local variable of `main()`, when the command was not found, raising `NameError`. Replaced with `commands`-dict lookup and a clean exit code 1.
- **P2: CLI missing typing imports**: `Dict` / `List` / `Any` used in local function annotations without `from typing import ...`, failing static checks. Added `from typing import Any, Dict, List`.
- **P1: Intent router level broken**: `IntentResult.level` used `2 if self.fallback else 2` (both branches identical), so fallback routing was indistinguishable from normal LLM routing. Changed to `3 if self.fallback else 2` (0=rule / 1=keyword / 2=LLM / 3=fallback).
- **P1: Recall engine mutated original memory**: Context trimming did `truncated = chunk` (alias, not copy), so truncating `truncated.content` mutated the caller's `MemoryChunk` in place, permanently truncating stored memory content. Changed to `copy.copy(chunk)`.
- **P1: REST API concurrency slot leak**: When a worker thread `t.start()` failed, the concurrency counter was not decremented, leaking slots until the server permanently returned 503. Added `try/except` rollback with a proper 503 response.

### Tests
- Added `tests/test_v558_features.py` with test cases covering:
  - `memory_diff` basic diff, identical versions, nonexistent version, content/category/tags/importance changes
  - MCP parameter validation: missing required params for memory_add, memory_search, memory_context
  - Falsy enum fix: `PrivacyLevel.NONE` preserved through `add()`
  - CLI `cmd_list` filtered total count
  - Version verification: `__version__` and `pyproject.toml` consistency

## [5.5.7] - 2026-08-30

### Added
- **Single-file combined CLI**: `MindForge_combined.py` — all-in-one build bundling core, modules, adapters and CLI with AI drama features
- **REST API concurrency limit**: configurable cap on in-flight requests, returning 503 when exceeded to prevent resource exhaustion

### Security
- **Encryption fail-closed (P1-008)**: Removed the HMAC-XOR fallback encryption path. If `cryptography` was unavailable in an earlier version and encrypted memories were created, those `EXPERIMENTAL_HMAC_XOR` blobs are now **permanently undecryptable**. Back up with `MindForge export --json > backup.json` before upgrading.
- **WAL network-filesystem downgrade**: `_get_conn()` now detects network filesystems (including `fuseblk` / `fuse.*`) and falls back to `journal_mode=DELETE`, avoiding SIGBUS crashes on network-mounted databases.
- **Webhook signature consistency**: unified HMAC signature computation; request timeout now honours `config.timeout`
- **`add_tags_to_ids` XSS sanitization**: tag values sanitized before being persisted and rendered

### Fixed
- **P0/P1 security hardening batch**: fail-closed encryption, concurrency limiting, and audit coverage gaps
- **P2: `agent-insight` crash**: fixed case-sensitivity mismatch that raised on some agent identifiers
- **P2: banner pollution**: CLI banner no longer leaks into `--json` output; password errors emit valid JSON on stdout in JSON mode
- **P2: `purge_trash` FK constraint**: purge order corrected to satisfy foreign key constraints
- **P3: 4xx logging**: failed webhook attempts now log `actual_attempts`
- **P3: JSON password error**: `--json` mode emits a well-formed JSON error object instead of raw text

### Integration Notes
- When using the `dsh-mindforge` bridge, `MINDFORGE_PASSWORD` must be exported (`export MINDFORGE_PASSWORD="your-password"`), otherwise the CLI exits with an error on encrypted databases.

## [5.5.6] - 2026-08-26

### Added
- **Memory Pinning (置顶)**: Full pin/unpin lifecycle with priority sorting
  - `add(..., pinned=True)` — create memory already pinned
  - `pin(memory_id)` / `unpin(memory_id)` — toggle pin status with audit logging
  - `list(pinned=True/False)` — filter by pin status
  - `list_pinned(limit)` — list all pinned memories
  - Pinned memories always appear first in `list()` results (`ORDER BY pinned DESC`)
  - `update(memory_id, pinned=True/False)` — update pin status
- **Batch Get Memories**: `batch_get(memory_ids)` — fetch multiple memories in a single SQL query, preserving input order, with automatic dedup and expired-memory filtering
- **Memory Timeline View**: `timeline(category, layer, limit)` — group memories by time period (today / yesterday / this_week / this_month / earlier)
- **Search Suggestions**: `search_suggestions(prefix, limit, category)` — autocomplete suggestions based on existing tags and categories (case-insensitive prefix matching)
- **Duplicate Detection on Add**: `check_duplicates(content, threshold, category, limit)` — detect near-duplicate memories before adding, using Jaccard + SequenceMatcher hybrid similarity
- **Batch Tag Operations by ID**: `add_tags_to_memories(ids, tags)` and `remove_tags_from_memories(ids, tags)` — bulk add/remove tags for specific memory IDs
- **Stats Enhancement**: `stats()` now includes `pinned_count`

### Fixed
- **fuzzy_search crash on None/empty query**: Added defensive checks for `None`, non-string, and whitespace-only queries; returns `[]` instead of raising `AttributeError`
- **rename_tag case-insensitive matching**: Tags now match case-insensitively (`"MyTag"` renamed via `"mytag"` works); post-rename deduplication prevents duplicate tags
- **rename_tag empty/None input**: Returns `0` instead of crashing on empty or `None` tag names
- **batch_add missing fields**: Now supports `pinned`, `expires_at`, and `metadata` fields (previously silently ignored)
- **Facade `list()` missing `pinned` filter**: Now passes `pinned` parameter through to storage layer
- **Facade `update()` missing `pinned` parameter**: Now supports updating pin status via `update()`
- **P1: Conflict decay methods missing**: Added `adjust_importance()` and `append_tags()` to `StorageEngine`, which were called by the conflict resolution module but never implemented, causing silent failures in conflict decay functionality.
- **P1: Package naming conflict in pytest**: Removed root-level `__init__.py` that caused pytest to load the `MindForge` package from two different paths, resulting in enum classes with different identities and `isinstance()` returning `False`. Updated `setup.py` to read version from `MindForge.py` instead.
- **P2: Version hardcoded test failure**: Changed `test_version_is_555` in v5.5.5 tests to `test_version_at_least_555` to avoid breaking on minor version bumps.

### Performance
- `batch_get()` reduces N+1 query pattern to a single `IN` query for bulk memory retrieval
- Timeline view uses single sorted query instead of multiple date-range queries
- Search suggestions use in-memory set aggregation after one DB scan

### Tests
- Added `tests/test_v556_features.py` with 60+ test cases covering:
  - Pinning (10 tests): basic pin/unpin, add-with-pin, list filter, priority sorting, delete/restore persistence, update pin
  - Batch get (5 tests): basic, empty, nonexistent, order preservation, dedup
  - Timeline (4 tests): basic, empty, category filter, total count
  - Search suggestions (6 tests): tags, categories, empty/None prefix, no match, limit
  - Duplicate detection (6 tests): exact, high similarity, no match, empty, category filter, threshold
  - Batch tag ops (6 tests): add, no-duplicate, empty, nonexistent, remove, case-insensitive remove
  - adjust_importance (5 tests): lower, raise, zero delta, nonexistent, range clamping
  - append_tags (4 tests): basic, duplicate no-change, empty list, nonexistent
  - Package naming fix (3 tests): no root __init__.py, import consistency, isinstance cross-import
  - Bug fixes (12 tests): fuzzy_search None/empty/whitespace, rename_tag case/empty/dedup, batch_add pinned/expires/metadata, stats pinned_count
  - Version verification (3 tests): semver format, exact 5.5.6 match, pyproject.toml consistency
- Full suite: **260+ passed**

## [5.5.5] - 2026-08-25

### Added
- **Four-tier memory layering**: Sensory / Short-term / Long-term / Permanent layers with independent capacity and retention policies, propagating upward via the Ebbinghaus forgetting curve
- **Hardware-adaptive tuning**: `HardwareProfiler` detects machine capability and dynamically sizes the memory cache and recommended retrieval limits
- **Conflict detection**: antonym, attribute-value, and timeline contradiction detection with automatic confidence decay
- **Query pre-filtering**: `QueryEngine` entry cache to avoid redundant fetches

### Fixed
- **FTS index sync defect (7 methods)**: soft-deleted memories remained searchable because the index was not pruned after deletion; all seven call sites now clean up the index
- **Enum double-import**: enum classes loaded from two module paths broke `isinstance()` checks
- **Vector threshold fusion**: similarity thresholds no longer double-applied across fusion stages
- **Audit parameter plumbing**: corrected misrouted audit arguments
- **Synonym expansion / soft-delete filtering**: 20+ P0/P1/P2 issues resolved in the hardening pass

## [5.5.4] - 2026-08-24

### Added
- **Memory merging**: `merge_memories()` consolidates several memories into one, preserving provenance
- **Access analytics**: `most_accessed()` and `recently_accessed()`
- **Bulk update by filter**: `bulk_update_by_filter()` pushes filtering down to SQL instead of loading the full table, fixing a large-dataset performance problem
- **Tag statistics**: `tag_stats()`
- **Index consistency check**: `check_index_consistency()` detects and repairs drift between the index and storage layers

## [5.5.3] - 2026-08-24

### Fixed
- **Expired memories stayed searchable**: `purge_expired()` did not prune the index
- **Stale index after batch delete**: `batch_delete_by_category()` and `batch_delete_by_tag()` now clean up the index so deleted memories cannot be returned by search
- **API double-write**: `api/server.py` returned `None` on error, causing the caller to write a second response body
- **Embedding parameter mismatch**: warn instead of silently overriding when parameters differ
- Removed a BOM accidentally introduced in v5.5.3 that broke `core/embedding.py` parsing

## [5.5.2] - 2026-08-23

### Added
- **Memory TTL/Expiration System**: New `expires_at` field on memories with full lifecycle management
  - `add(..., expires_at=timestamp)` — create memory with expiration
  - `set_ttl(memory_id, ttl_seconds)` — set or cancel TTL on existing memory
  - `list_expired()` — list all expired memories pending cleanup
  - `purge_expired()` — batch-move expired memories to trash
  - Auto-expiry: `get()` automatically moves expired memories to trash and returns None
  - Database migration: `ALTER TABLE memories ADD COLUMN expires_at REAL DEFAULT 0` with index
- **Multi-keyword Search Highlighting**: Enhanced `highlight()` method
  - Space-separated multi-keyword support (e.g., "hello world" highlights both)
  - Chinese keyword support
  - Case-insensitive matching with original case preservation in output
  - Custom highlight tags (before_tag / after_tag)
  - Nested-replacement safe: long keywords processed first, placeholder-based replacement
- **Batch Delete by Category/Tag**: New bulk operations
  - `batch_delete_by_category(category, permanent=False)` — soft or permanent delete by category
  - `batch_delete_by_tag(tag, permanent=False)` — soft or permanent delete by tag (exact tag match, no false positives)
  - Full audit logging for all batch operations
  - Cascade deletion for permanent mode (versions, links, notes, embeddings, FTS index)

### Fixed
- **Critical: UTF-8 BOM in pyproject.toml**: Removed BOM from `pyproject.toml` that caused `tomllib.TOMLDecodeError: Invalid statement`, breaking `pytest` collection and `pip install`. Also removed BOM from 7 other source files (`api/server.py`, `cli/main.py`, `core/mindforge.py`, `core/storage.py`, `MindForge.py`, `__init__.py`).
- **FTS5 bm25 score overflow**: Added clamping (`max(-50, min(50, score))`) before `math.exp()` in `fts_search()` to prevent `OverflowError` on anomalous large positive bm25 scores. Also added `None` score handling.
- **Query engine double-fetch**: Added entry cache in `QueryEngine.search()` to avoid redundant `get_memory()` calls between the pre-filter phase and result-building phase, reducing DB queries by up to 2x for filtered searches.
- **Version consistency**: Updated README version badge from stale 5.5.0 to 5.5.2; updated version test to match current release.

### Performance
- Query engine filtered searches now use a single fetch per memory entry instead of two
- FTS5 search no longer risks exception on edge-case scores

### Tests
- Added `tests/test_v552_features.py` with 33 test cases covering:
  - TTL expiration (12 tests): add with expires_at, set_ttl, cancel TTL, auto-expire on get, list_expired, purge_expired, expired exclusion from search
  - Multi-keyword highlight (10 tests): single/multi keyword, Chinese, case-insensitive, empty inputs, custom tags, no nested replacement
  - Batch delete (7 tests): by category soft/permanent/empty, by tag soft/permanent/no-match/partial-match-no-false-positive
  - FTS5 score fix (2 tests)
  - Query engine cache (2 tests): category filter, layer filter
  - Version verification (2 tests): __version__ and pyproject.toml consistency
- Full suite: **189 passed** (155 existing + 33 new + 1 updated)

### Migration
- Existing databases are automatically migrated on first connection: `expires_at` column added with `DEFAULT 0` (never expires), with an index for efficient expired-item queries. No data loss.
