<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="180" />
</p>

<h1 align="center">ILUMINATY</h1>

<p align="center">
  Local "eyes + hands" runtime for AI agents.<br/>
  CLI-first control loop, MCP compatibility, multi-monitor perception, RAM-first context.
</p>

<p align="center">
  <img src="https://github.com/sgodoy90/iluminaty/actions/workflows/tests.yml/badge.svg" alt="Tests" />
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" />
</p>

---

## Current Project State (April 17, 2026)

ILUMINATY has moved to a **CLI-first architecture**:

- Main runtime control: `iluminaty <command>`
- Compatibility bridge: MCP server (`python -m iluminaty.mcp_server`)
- Core API: FastAPI server (`iluminaty start ...`)

This repo is now organized around:

- Real-time perception + action loops in `iluminaty/`
- CLI adapter in `iluminaty/cli_commands.py`
- MCP adapter in `iluminaty/mcp_server.py`
- Stress/benchmark/test coverage in `tests/`
- Domain pack extension system in `domain_packs/`
- Claude/Codex collaboration structure in `.claude/` + `CLAUDE.md`

---

## What ILUMINATY Does

ILUMINATY gives an external AI brain (Claude, GPT, Cursor, etc.):

- Multi-monitor situational awareness (`/spatial/state`, `/perception/world`)
- Real-time action primitives (click, type, drag, focus, hotkeys, windows)
- Action safety loop (`precheck -> execute -> verify -> recover`)
- Temporal context in RAM (`/perception/trace`, visual/event evidence)
- Worker-based monitor orchestration for attention and routing

By default, runtime is local and RAM-first for perception state. No cloud account is required.

---

## Reality Check: Visual Understanding

The default deep visual path is currently **local native heuristics** (OCR + motion + app/window context), not a heavy built-in VLM.

What this means today:

- Very fast semantic loop and low overhead for automation
- Strong for UI/task state, OCR, and control verification
- Not equivalent to full frame-by-frame image reasoning from a dedicated large VLM

If your use case requires rich image/video semantic QA, pair ILUMINATY with an external multimodal model through MCP/CLI vision calls.

---

## Quick Start

### 1) Install

```bash
git clone https://github.com/sgodoy90/iluminaty
cd iluminaty
pip install -e ".[ocr]"
```

Optional extras:

```bash
pip install -e ".[ocr,voice]"
pip install -e ".[ocr,trading]"
pip install -e ".[ocr,vlm,trading,voice]"
```

### 2) Start server

```bash
iluminaty start --api-key ILUM-dev-local --actions
```

Useful startup flags:

- `--profile low_power|balanced|performance`
- `--monitor 0` (auto all monitors) or `--monitor N`
- `--vision-profile core_ram|vision_plus`
- `--port 8420`

### 3) Use CLI (primary interface)

```bash
iluminaty status --json
iluminaty world --json
iluminaty spatial map
iluminaty see now --monitor 1 --mode medium_res
iluminaty act --instruction "click save button" --mode SAFE --verify
```

Alias also supported:

```bash
iluminaty cli world --json
```

### 4) Optional MCP compatibility

```json
{
  "mcpServers": {
    "iluminaty": {
      "command": "python",
      "args": ["-m", "iluminaty.mcp_server"],
      "env": {
        "ILUMINATY_API_URL": "http://127.0.0.1:8420",
        "ILUMINATY_KEY": "ILUM-dev-local"
      }
    }
  }
}
```

Or generate config automatically:

```bash
iluminaty mcp-config
```

---

## CLI Surface (Implemented)

Core command groups:

- Perception: `status`, `world`, `trace`, `readiness`, `spatial`, `see`, `verify`
- Control: `act`, `click-at`, `drag`, `windows`, `safety`, `watch`, `dialog`
- Runtime ops: `ui`, `file`, `clipboard`, `process`, `open`, `exec`
- Domains: `trading`, `voice`

References:

- `CLI-QUICKSTART.md`
- `CLI-MCP-MAPPING.md`
- `CLI-MIGRATION-PLAN.md`

---

## MCP Tooling (Compatibility Mode)

Open source licensing keeps broad MCP compatibility identifiers, but the current MCP server exposes a curated active set to reduce tool noise.

- Active tools currently exposed by `iluminaty.mcp_server`: **32**
- Includes vision, spatial, actions, UIA, windows, file/system, trading

See:

- `iluminaty/mcp_server.py` (`_ALLOWED_TOOLS`, `TOOLS`, `HANDLERS`)
- `iluminaty/licensing.py` (`ALL_MCP_TOOLS`)

---

## Architecture Snapshot

### Perception pipeline

- Multi-monitor capture with per-monitor tagging (`monitor_id`)
- Fast semantic loop (low-latency updates, never blocks on deep analysis)
- Deep visual loop with bounded queue and RT bias (drop oldest)
- World state contract with `tick_id`, readiness, uncertainty, evidence
- Temporal trace and query endpoints for recent context

### Workers orchestration

- Per-monitor digest worker layer
- Scheduler with monitor attention budgets
- Arbiter lease for action ownership
- Subgoal routing per monitor

### Control pipeline

- `POST /action/precheck`
- `POST /action/execute`
- `POST /action/raw`
- `POST /action/verify_visual`
- Recovery + safety hooks

---

## Core HTTP Endpoints

Perception and context:

- `GET /perception/world`
- `GET /perception/trace?seconds=90`
- `GET /perception/readiness`
- `POST /perception/query`
- `WS /perception/stream`

Control and safety:

- `POST /action/precheck`
- `POST /action/execute`
- `POST /action/raw`
- `POST /action/verify_visual`
- `GET|POST /operating/mode`
- `POST /safety/kill`
- `POST /safety/resume`

Workspace and monitor ops:

- `GET /spatial/state`
- `GET /windows/list`
- `POST /windows/focus`
- `GET /workers/status`
- `GET /workers/schedule`

---

## Domain Packs

Built-in semantic packs include:

- `coding`
- `trading`
- `support`
- `backoffice`
- `research`
- `qa_ops`
- fallback `general`

Custom domain packs:

- Place `.toml` or `.json` files in `domain_packs/`
- Or set `ILUMINATY_DOMAIN_PACKS_DIR`
- Reload at runtime: `POST /domain-packs/reload`
- Force override: `POST /domain-packs/override`

See `iluminaty/domain_packs.py` and `CONTRIBUTING.md`.

---

## Security Model

Auth is enabled by default.

- If server starts without `--api-key`, protected endpoints return `503`
- With wrong or missing key, requests return `401`
- Only use `ILUMINATY_NO_AUTH=1` for isolated local testing

Safety controls:

- Operating modes: `SAFE`, `HYBRID`, `RAW`
- Kill switch: `POST /safety/kill`
- Resume: `POST /safety/resume`

---

## Benchmarks and Stress Gates

Latest checked artifacts in repo:

- `BENCHMARKS-IPA-v2.1.md`
- `STRESS-REPORT-IPA-v2.1-latest.md`

Latest stress gate report shows:

- Mixed load p95 under 300ms for critical endpoints
- Stale-context blocking effective in SAFE mode
- Recovery scenario passing with high success
- WebSocket stream soak passing without malformed payloads

Run locally:

```bash
py tests/benchmark_ipa_v21.py --iterations 180 --warmup 30 --workers 3
py tests/stress_ipa_v21_release_gate.py --duration 20 --workers 3 --report STRESS-REPORT-IPA-v2.1-latest.md
```

---

## Development and Tests

Run baseline tests:

```bash
python -m pytest -q
```

Focused CLI tests:

```bash
python -m pytest tests/test_cli_client.py tests/test_cli_commands.py tests/test_main_cli_dispatch.py -q
```

Windows-first runtime is the primary supported target today.

---

## Desktop App

A Tauri shell exists in `desktop-app/` and can be extended for packaged desktop workflows.

```bash
cd desktop-app
npm install
npm run tauri dev
```

---

## Repository Map

- `iluminaty/` - core runtime, API routes, CLI adapter, MCP adapter
- `tests/` - unit/integration/stress/benchmark gates
- `domain_packs/` - custom domain specialization files
- `desktop-app/` - Tauri desktop shell
- `.claude/` - collaborative rules, commands, hooks, skills layout
- `CLAUDE.md` - shared project context for Claude Code/Codex

---

## License

MIT - see `LICENSE`.
