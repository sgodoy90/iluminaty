<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="180"/>
</p>

<h1 align="center">ILUMINATY</h1>

<p align="center">
  <strong>Real-time eyes + hands for AI on desktop systems.</strong><br/>
  <strong>IPA v2.1: semantic streaming, temporal memory, and closed-loop control.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-00ff88?style=flat-square&labelColor=0a0a12" alt="Version"/>
  <img src="https://img.shields.io/badge/IPA-v2.1-00ff88?style=flat-square&labelColor=0a0a12" alt="IPA v2.1"/>
  <img src="https://img.shields.io/badge/python-3.10+-00ff88?style=flat-square&labelColor=0a0a12&logo=python&logoColor=00ff88" alt="Python"/>
  <img src="https://img.shields.io/badge/routes-139-00ff88?style=flat-square&labelColor=0a0a12" alt="Routes"/>
  <img src="https://img.shields.io/badge/MCP_tools-40-00ff88?style=flat-square&labelColor=0a0a12" alt="MCP Tools"/>
  <img src="https://img.shields.io/badge/license-MIT-00ff88?style=flat-square&labelColor=0a0a12" alt="License"/>
</p>

---

## What Is ILUMINATY?

ILUMINATY is a local runtime that gives any external AI:

- **Eyes**: continuous screen perception and semantic world state.
- **Hands**: real desktop actions (mouse, keyboard, windows, terminal, browser, files).
- **Loop**: `precheck -> execute -> verify -> recover`.

The external AI decides. ILUMINATY observes, structures context, and executes.

---

## What Changed (IPA v2.1)

This repo now includes the full IPA v2.1 runtime:

- **Dual perception loop**
  - `Fast Loop` (8-12Hz target): low-latency semantic updates.
  - `Deep Loop` (0.5-2Hz): local visual analysis worker (non-blocking).
- **WorldState contract upgrade**
  - `tick_id`, `staleness_ms`, `visual_facts`, `evidence`.
- **Temporal visual memory**
  - `90s` semantic+frame references in RAM.
  - Optional `vision_plus` profile with encrypted rotating disk spool (TTL).
- **Context freshness gates for actions**
  - `context_tick_id` and `max_staleness_ms` in SAFE/HYBRID flows.
- **New API capability**
  - `POST /perception/query` for temporal visual questions.
- **New MCP capability**
  - `vision_query`, `window_minimize`, `window_maximize`, `window_close`.
- **Desktop app sync**
  - Updated to real backend contracts + `x-api-key` propagation.

---

## Architecture (Current)

```text
Screen(s) -> Capture -> RingBuffer -> Perception Fast Loop -> WorldState -> MCP/HTTP -> External AI
                                 \-> Perception Deep Loop -> VisualEngine -> Visual Facts -> Temporal Store

External AI -> action_precheck -> action_execute -> verify -> recover
```

### Perception Layers

1. **Signal Layer**
   - Capture, change score, pHash, optical flow, OCR diff, active window/context.
2. **Semantic Layer**
   - `WorldState`: phase, surface, readiness, uncertainty, affordances, evidence.
3. **Control Layer**
   - Safety and readiness checks, execution, verification, recovery.

---

## Operating Modes

- `SAFE` (default): safety + readiness + freshness checks.
- `HYBRID`: guardrails focused on critical/destructive actions.
- `RAW`: minimal path (kill switch still available).

---

## API Highlights

### Perception / Temporal Context

- `GET /perception/world`
- `GET /perception/trace?seconds=90`
- `GET /perception/readiness`
- `POST /perception/query` `{question, at_ms|window_seconds, monitor_id}`
- `WS /perception/stream` (`tick_id`, `world`, `readiness`, `events`, `visual_facts_delta`)

### Action Loop

- `POST /action/precheck`
- `POST /action/execute`
- `POST /action/raw`
- `POST /action/verify`

`/action/precheck` and `/action/execute` support:

- `context_tick_id`
- `max_staleness_ms`

for stale-context rejection in SAFE/HYBRID.

### System Control

- `GET /system/overview`
- `GET /health`
- `GET /operating/mode`
- `POST /operating/mode`

---

## MCP Tools (40 total)

Key perception/control tools:

- `perception_world`
- `perception_trace`
- `vision_query`
- `do_action`
- `action_precheck`
- `raw_action`
- `verify_action`
- `set_operating_mode`
- `window_minimize`
- `window_maximize`
- `window_close`

Plus vision/UI/browser/terminal/filesystem tools for full desktop operation.

---

## Quick Start

### 1) From source

```bash
git clone https://github.com/sgodoy90/iluminaty.git
cd iluminaty
pip install -e ".[ocr]"
python main.py start --actions --autonomy confirm --monitor 0
```

Open: `http://127.0.0.1:8420`

### 2) IPA v2.1 tuned runtime

```bash
python main.py start \
  --actions \
  --autonomy confirm \
  --monitor 0 \
  --fps 5 \
  --fast-loop-hz 10 \
  --deep-loop-hz 1.0 \
  --vision-profile core_ram
```

### 3) Optional temporal disk spool (`vision_plus`)

```bash
python main.py start \
  --actions \
  --vision-profile vision_plus \
  --vision-plus-disk
```

### 4) MCP with external AI

```bash
claude mcp add iluminaty -- python /path/to/iluminaty/iluminaty/mcp_server.py
```

---

## CLI (Current Main Flags)

```text
python main.py start [options]

Core:
  --port, --host, --fps, --buffer-seconds
  --quality, --format, --max-width, --monitor
  --api-key, --no-adaptive, --no-smart-quality

Computer use:
  --actions
  --autonomy suggest|confirm|auto
  --browser-debug-port
  --file-sandbox PATH...

IPA v2.1:
  --vision-profile core_ram|vision_plus
  --vision-plus-disk
  --deep-loop-hz
  --fast-loop-hz

Audio:
  --audio off|mic|system|all
  --audio-buffer
```

---

## Repo Status

- Core platform: **v1.0.0**
- IPA runtime: **v2.1 integrated**
- Regression suite: **11 tests passing**
- Desktop app: **API contract aligned with backend**

---

## Privacy & Security Notes

- Visual context is RAM-first by default (`core_ram`).
- `vision_plus` disk spool is optional and encrypted/rotated.
- Kill switch and operational mode controls are available.
- Auth headers (`x-api-key`) are supported in API + MCP + desktop bridge.

---

## Main Source Areas

- `iluminaty/perception.py` — IPA loops and semantic integration.
- `iluminaty/world_state.py` — semantic contract + freshness gate.
- `iluminaty/temporal_store.py` — temporal memory store.
- `iluminaty/visual_engine.py` — local deep visual worker.
- `iluminaty/server.py` — HTTP/WS API.
- `iluminaty/mcp_server.py` — MCP tools/handlers.
- `desktop-app/` — Tauri desktop control plane.

---

## License

MIT

---

<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="60"/>
  <br/>
  <em>AI decides + ILUMINATY sees and acts.</em>
</p>
