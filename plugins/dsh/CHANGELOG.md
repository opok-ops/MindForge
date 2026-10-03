# Changelog

All notable changes to the mindforge-dsh-plugin are documented here.

## [0.3.0] — 2026-10-03

Version jump from 0.1.1 (0.2.x line merged into this release).

### Fixed

- **Auto-start backend with MindForge v5.8.13 namespace** — `ensureRunning()` spawned
  `python -m cli.main`, which fails with `ModuleNotFoundError: No module named 'cli'`
  since the MindForge v5.8.13 refactor moved all packages under the `mindforge.*`
  namespace. Now spawns `python -m mindforge.cli.main` (verified against the 5.8.13
  wheel/venv; `MindForge` console script and `mindforge.cli.main` module both work).
- **Manual-start error messages** now show the correct, cross-platform invocation
  `python -m mindforge.cli.main --db-path <db> serve --api --port <port>`
  (previously `mindforge --db-path ...` — the lowercase command does not exist on
  case-sensitive systems; the console script is declared as `MindForge`).

### Changed

- Version bumped to **0.3.0** in `package.json`, `.dsh-plugin/plugin.json`,
  `src/index.ts` header / load log, and `cordis.patch.yml`.
- Dependencies aligned to the current DeepSeek Harness 0.1.x line:
  `@deepseek-ai/dsh-session` / `@deepseek-ai/dsh-tools` `^0.1.0-rc.6` → `^0.1.7-rc.2`.
- Peer `@deepseek-ai/cordis ^4.0.1` kept — verified compatible with DSH v0.2.0-rc.2
  (which uses `cordis@~4.0.4`).

### Docs

- README: MindForge badge and requirements bumped `v5.4.6+` → `v5.8.13+`.
- README: install example `#egg=MindForge` → `#egg=mindforge-memory` (distribution
  renamed in MindForge v5.8.13).
- README: tool table completed — all **9** registered tools now listed
  (`memory_add / search / get / list / update / delete / stats / tags / star`).
- README: quick-start CLI commands use `MindForge` (declared console script),
  note added about case sensitivity.
- README: DSH requirement `v0.1+` → `v0.1.7+` (compatible with v0.2.0-rc.x).

### Compatibility (verified upstream)

- DeepSeek Harness v0.2.0-rc.2 (2026-09-29) has **no breaking change** for this
  plugin's API surface: `ctx.tools.register`, `ctx.on('turn/start' | 'turn/end')`,
  `ctx.effect`, `ctx.agentLoop` remain stable; upgrade-guides
  (`remove-runtime-invariants`, `subpath-plugin-display-manifest`) only affect
  packages unrelated to this plugin (invariants registry / subpath-package display).

## [0.1.1] — with MindForge v5.4.8

- Security: untracked `lib/*.map` source maps (source-map leak) — build now emits
  no maps (`tsconfig.build.json` with `sourceMap/declarationMap=false`).
- Bug fixes from the v5.4.8 audit (8 fixes, v5.5.8 audit: 7 fixes).

## [0.1.0] — initial release

- 4-layer memory plugin for DeepSeek Harness: 9 tools, turn/start auto-recall,
  turn/end auto-capture, auto-start backend.
