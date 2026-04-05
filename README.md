<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="180"/>
</p>

<h1 align="center">ILUMINATY</h1>

<p align="center">
  <strong>Real-time visual perception + PC control for AI agents.</strong><br/>
  Local MCP server. Zero cloud. Zero disk. AI sees your screen — all monitors — live.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.3.0-00ff88?style=flat-square&labelColor=0a0a12" alt="Version"/>
  <img src="https://img.shields.io/badge/IPA-v3-00ff88?style=flat-square&labelColor=0a0a12" alt="IPA v3"/>
  <img src="https://img.shields.io/badge/MCP_tools-38-00ff88?style=flat-square&labelColor=0a0a12" alt="MCP Tools"/>
  <img src="https://img.shields.io/badge/multi--monitor-3%2B-00ff88?style=flat-square&labelColor=0a0a12" alt="Multi-Monitor"/>
  <img src="https://github.com/sgodoy90/iluminaty/actions/workflows/tests.yml/badge.svg" alt="Tests"/>
  <img src="https://img.shields.io/badge/license-MIT-00ff88?style=flat-square&labelColor=0a0a12" alt="License"/>
</p>

---

## What Is This?

ILUMINATY is a local MCP server that gives any AI (Claude, GPT-4o, Cursor, etc.) **real-time visual perception and OS-level control** of your desktop — without sending screenshots to the cloud.

The AI doesn't guess. It reads structured data from a continuously running perception engine that watches your screen at 3fps and turns raw pixels into semantic events, spatial context, OCR text, and OS state — all delivered as ~200-token text payloads.

When the AI needs to *see* something specific, it gets a real screen image. When it needs to *act*, it sends commands that resolve to exact OS-level operations — no coordinate estimation.

---

## Quick Start

```bash
pip install iluminaty[ocr]
iluminaty start
```

Server starts on `:8420`, auto-detects all monitors:

```
[ILUMINATY] IPA v3 active — 3 monitors detected
[ILUMINATY] Capture: 3.0 fps per monitor  |  Buffer: 70s RAM-only
[ILUMINATY] OCR worker: running (subprocess isolated)
[ILUMINATY] API: http://127.0.0.1:8420
```

**Connect to Claude Code** — add to `.mcp.json`:

```json
{
  "mcpServers": {
    "iluminaty": {
      "command": "python",
      "args": ["-m", "iluminaty.mcp_server"],
      "env": {
        "ILUMINATY_API_URL": "http://127.0.0.1:8420"
      }
    }
  }
}
```

> **No registration, no license key, no account.** All 38 tools work immediately.
>
> The optional `--api-key` flag is a local auth token — it protects the server
> if you expose it on a network. On localhost it's not needed.

Or run `iluminaty mcp-config` to write the config automatically.

**Start a session:**

```
call get_spatial_context    → maps all monitors, windows, cursor position
call see_now                → current screen image + IPA context
call act action=click target="Save button"   → clicks it exactly
```

---

## Install from Source

```bash
git clone https://github.com/sgodoy90/iluminaty
cd iluminaty
pip install -e ".[ocr]"
iluminaty start
```

**Optional flags:**

| Flag | Default | Description |
|---|---|---|
| `--port` | `8420` | HTTP port |
| `--fps` | `3` | Capture rate per monitor |
| `--api-key` | _(none)_ | Local auth token — only needed if exposing on a network |
| `--audio` | `off` | Audio capture: `off`, `system`, `mic`, `all` |

---

## Why Not Just Use Computer Use?

| | Computer Use | ILUMINATY |
|---|---|---|
| **Privacy** | Screenshots sent to Anthropic cloud | 100% local — nothing leaves your machine |
| **Monitors** | 1 only | 3+ with per-monitor spatial context |
| **Token cost / action** | ~20-30K tokens (full screenshot) | ~200 tokens (text) or ~5K (low_res image) |
| **Cost per 20-action task** | ~600K tokens | ~40K tokens (**15× cheaper**) |
| **Change detection** | Blind between calls | Continuous IPA at 3fps — events always ready |
| **Click precision** | Model estimates coordinates | `smart_locate` via OCR — exact coords in 3–34ms |
| **Waiting for events** | Polling loop (burns tokens) | `watch_and_notify` — zero tokens while waiting |
| **Session continuity** | Starts blind each session | `get_session_memory` restores previous context |
| **Multi-agent** | Not supported | Multiple AI agents on different monitors |
| **Works offline** | No | Yes — no internet required |
| **Window control** | Screenshot + estimated click | Direct OS handle (`window_close`, `move_window`) |

**Measured in production (stress test, 60s, 4 concurrent clients):**
- 158 requests served · 0 crashes · 29ms max latency

---

## Architecture

```
Physical Screens (1–N monitors)
         │
         ▼  3 fps per monitor
[MultiMonitorCapture]
  mss screenshot per monitor
  adaptive FPS: active=3fps, inactive=0.5fps
         │
         ▼
[RingBuffer]  ←── RAM only, zero disk
  ~210 slots  ─── ~70s of history
  per-monitor frame tagging
  skip_unchanged (histogram diff)
         │
         ├──────────────────────────────────┐
         ▼                                  ▼
[IPA v3 Bridge]                   [Perception Engine]
  VisualEncoder (imagehash)          SceneStateMachine
  DeltaCompressor (int8 numpy)       AttentionMap (8×6 grid)
  VisualStream (patch timeline)      KeyframeDetector
  MotionField                        TemporalEventFuser
  gate events: motion_start,         WorldState (task phase,
    motion_end, content_loaded         affordances, readiness)
         │                                  │
         │            ┌─────────────────────┘
         ▼            ▼
[Workers System]
  MonitorWorker × N   (per-screen semantic digest)
  SpatialWorker       (layout + cursor + active window)
  FusionWorker        (global world snapshot)
  ActionArbiter       (single-writer execution lease)
  VerifyWorker        (post-action verification timeline)
  SchedulerWorker     (multi-monitor attention budget)
         │
         ▼
[FastAPI :8420]
  /vision/snapshot      /perception         /spatial/state
  /actions/*            /terminal/exec      /memory/*
  /watch/*              /domain-packs/*     /buffer/stats
  WebSocket /ws/stream
         │
         ▼
[MCP stdio]  (mcp_server.py — persistent HTTP keep-alive)
         │
         ▼
   Claude / GPT-4o / Cursor / any MCP client
```

**Key design choices:**

- **OCR isolation**: RapidOCR runs in a fully separate subprocess (`spawn`). The main process never loads ONNX/DirectML — prevents segfaults on multi-monitor Windows setups where only one DirectML session is allowed per process.
- **RAM-only ring buffer**: `collections.deque` with per-monitor frame tagging. No temp files, no SQLite for frames. Zero disk I/O on the hot path.
- **Persistent HTTP connection pool**: `mcp_server.py` reuses one TCP connection across all MCP tool calls (keep-alive, 30s TTL). Eliminates 1–3ms TCP handshake per call.
- **Non-blocking terminal**: `run_command` runs in `asyncio.run_in_executor()` — shell commands never block the FastAPI event loop.
- **Capture watchdog**: background coroutine checks `frame_count` every 30s, auto-restarts capture if stalled for 90s.

---

## IPA v3 — Intelligent Perception Algorithm

IPA runs continuously in background, processing your screen through a **codec-inspired pipeline**:

```
Frame
  │
  ├─ Gate 0: Window change detection (ctypes)         < 0.1ms
  ├─ Gate 1: Histogram change_score 0.0–1.0           < 0.5ms
  ├─ Gate 2: Perceptual hash (imagehash phash)        < 1ms
  ├─ Gate 3: Optical flow (Farneback 480p) + SmartDiff + AttentionMap   5–25ms
  └─ Gate 4: OCR diff (RapidOCR, structural, throttled 30s)            50–200ms
```

**I-frames** (keyframes every 10s): full screen state stored as reference.  
**P-frames**: only the patches that changed since the last frame — 95% smaller payload.  
**`change_mask`**: 25-byte bitmask indicating which of the 196 screen zones changed.  
**Gate events**: `motion_start`, `motion_end`, `content_loaded` — discrete signals the AI can react to.

**7 IPA classes running per monitor:**

| Class | Role |
|---|---|
| `SceneStateMachine` | IDLE / TYPING / SCROLLING / LOADING / VIDEO / TRANSITION / INTERACTION |
| `AttentionMap` | 8×6 spatial heatmap with temporal decay — where screen activity is concentrated |
| `ROITracker` | Up to 6 regions of interest tracked across frames |
| `KeyframeDetector` | Detects scene boundary transitions |
| `TemporalEventFuser` | Merges raw events into composite narratives ("started scrolling → stopped → text appeared") |
| `CapturePredictor` | Autocorrelation-based FPS advisor — slows down capture when screen is idle |
| `MonitorPerceptionState` | Independent state machine per physical monitor |

**100% proprietary. Zero external model dependencies in core.**  
IPA v3 uses only `numpy + pillow + imagehash`. No Google SigLIP, no TurboQuant, no torch in the core pipeline.

---

## MCP Tools (38)

All 38 tools available to everyone. No tiers, no registration.

### Vision — *what the AI sees*

| Tool | Description |
|---|---|
| `see_now` | **Start here.** Current screen image + IPA context: scene state, motion, gate events, OCR snippets. Supports `mode=low_res` (~5K tokens) or `mode=high_res`. |
| `what_changed` | What changed in the last N seconds. Returns image of the most significant change moment + textual diff. |
| `see_screen` | Screen snapshot. `text_only=true` returns OCR-only (~200 tokens, no image). |
| `see_changes` | Multiple frames showing temporal progression of a change. |
| `see_monitor` | Specific monitor's current frame with click coordinate mapping included. |
| `read_screen_text` | All visible text via OCR. Optionally scoped to a region `(x, y, w, h)`. Returns structured text blocks with bounding boxes. |
| `vision_query` | Ask about visual history: "what was on screen 30s ago?", "when did the terminal last show an error?" |

### Perception — *what IPA understands*

| Tool | Description |
|---|---|
| `get_spatial_context` | **Run at session start.** Full workspace map: monitor layout with positions/resolutions, windows per monitor, cursor position, active app, user activity phase. ~50 tokens. |
| `get_context` | Current workflow state: app name, focus level, task phase (`editing`, `browsing`, `building`), time in current workflow. |
| `perception` | Raw IPA event stream: scene state, motion type, change score, OCR events, attention targets. Per-monitor breakdown. |
| `perception_world` | WorldState snapshot: task phase, affordances (what UI actions are possible), readiness signal, uncertainty level. |
| `spatial_state` | Monitor layout + cursor coordinates + active window info. Lower overhead than `get_spatial_context`. |

### Active Waiting — *delegate monitoring, zero tokens while waiting*

| Tool | Description |
|---|---|
| `watch_and_notify` | Block until a screen condition is met. Returns immediately when triggered. Zero tokens consumed while waiting. Timeout configurable. |
| `monitor_until` | Like `watch_and_notify` but for long tasks (builds, uploads, deployments) — up to 10 minutes. |

**Supported conditions for both tools:**

| Condition | Triggers when |
|---|---|
| `page_loaded` | IPA `content_loaded` gate event fires |
| `motion_stopped` | Screen activity stops (animation/scroll ends) |
| `motion_started` | Screen activity begins (useful for detecting reactions) |
| `text_appeared` | Specific text appears in OCR output |
| `text_disappeared` | Text disappears from OCR output |
| `window_opened` | Window with matching title becomes visible |
| `window_closed` | Window with matching title disappears |
| `build_passed` | Terminal shows exit 0, "passed", or "✓" |
| `build_failed` | Terminal shows error, "failed", or non-zero exit |
| `element_visible` | `smart_locate` finds the named element |
| `idle` | No screen activity for N seconds |

### Session Memory — *continuity across AI sessions*

| Tool | Description |
|---|---|
| `get_session_memory` | Restore context from previous session: monitor layout, last active windows, recent IPA events, OCR snippets. ~300 tokens, no images. |
| `save_session_memory` | Snapshot current context to `~/.iluminaty/memory/session_*.json.gz`. Auto-called on server shutdown. |

### Actions — *OS-level control*

| Tool | Description |
|---|---|
| `act` | Execute a single action: `click`, `double_click`, `right_click`, `type`, `key`, `scroll`, `move_mouse`. Accepts `target="element name"` — resolved via `smart_locate`. |
| `do_action` | Natural language instruction with a SAFE loop: observe → plan → act → verify. |
| `operate_cycle` | Full human-like cycle: orient → locate → focus → read → act → verify. Highest success rate for complex interactions. |
| `drag_screen` | Drag from `(x1, y1)` to `(x2, y2)`. Works for sliders, sortable lists, file drag-and-drop. |

### Window Management

| Tool | Description |
|---|---|
| `list_windows` | All visible windows with title, position, size, monitor_id, minimized state. |
| `focus_window` | Bring window to front by handle or title substring. |
| `window_minimize` | Minimize window by handle or title. |
| `window_maximize` | Maximize window by handle or title. |
| `window_close` | Close window by handle. Direct OS API — no coordinate guessing. |
| `move_window` | Reposition and resize window to exact coordinates. |

### Browser Control

| Tool | Description |
|---|---|
| `browser_navigate` | Navigate to URL in active browser (Chrome DevTools Protocol). |
| `browser_tabs` | List all open tabs with titles and URLs. |

### System

| Tool | Description |
|---|---|
| `run_command` | Execute shell command. Returns stdout/stderr/exit code. Blocks are sandboxed (no `rm -rf /`, no registry deletes). Non-blocking async execution. |
| `read_file` | Read file contents (sandboxed to safe paths). |
| `write_file` | Write file (auto-backup of original, sandboxed). |
| `get_clipboard` | Read current clipboard text. |
| `screen_status` | Buffer stats: FPS, slots used, memory, frames captured, OCR worker status. |
| `agent_status` | Multi-agent coordinator state, active sessions, worker runtimes. |
| `os_dialog_status` | Detect open system dialogs (save/open/confirm). |
| `os_dialog_resolve` | Dismiss or confirm a system dialog by button name. |

---

## Smart Locate

When the AI calls `act(action="click", target="Save button")`, ILUMINATY resolves exact coordinates without asking the AI to guess pixels:

```
Resolution hierarchy (fastest first):

1. OCR cache          (~0ms)   — pre-computed text blocks from last RapidOCR pass
2. UIAutomation tree  (~5ms)   — native Windows Accessibility API
3. returns not_found           — AI falls back to visual estimation

Warm cache: 3–34ms. Works for any element with visible text.
```

The `LocateResult` includes `x`, `y`, `w`, `h`, `source` (`ocr`/`ui_tree`), `confidence`, and `monitor_id`. The AI never handles raw coordinates — it passes element names.

---

## Visual Memory

ILUMINATY persists a compact session snapshot between AI sessions so the next session starts with context instead of blind:

```python
# What gets persisted (~10–50KB gzip JSON per session):
{
  "monitors": [...],          # layout, zones, resolutions
  "active_windows": [...],    # last N windows with timestamps
  "gate_events": [...],       # last 20 significant IPA events
  "world_state": {...},       # task phase, scene state at shutdown
  "ocr_snippets": {...}       # last OCR text per monitor
}
```

Stored at `~/.iluminaty/memory/` (keeps last 10 sessions). Never stores raw images.

---

## Domain Packs

Specialize ILUMINATY's semantic interpretation for specific apps via `.toml` config files:

```toml
[pack]
name = "tradingview"
version = "1.0"

[detection]
url_keywords = ["tradingview.com"]
text_keywords = ["btcusd", "rsi", "macd", "volume"]

[semantics]
task_context = "financial-trading"
readiness_signals = ["price updated", "chart loaded"]

[[watch_conditions]]
name = "price_above"
type = "ocr_number_above"
field = "last_price"
threshold = 50000.0
```

Drop `.toml` files in `domain_packs/` — loaded automatically at startup. Example packs included: `tradingview.toml.example`, `vscode.toml.example`.

---

## Audio (Optional)

ILUMINATY can capture system audio or microphone in parallel with video:

```bash
iluminaty start --audio system   # capture system audio
iluminaty start --audio all      # system + microphone
```

Audio is stored in a RAM ring buffer (same zero-disk model as video). VAD (voice activity detection) marks chunks with `is_speech=true`. Query via `get_audio_level`.

---

## Security

- **Local auth (optional)**: pass `--api-key <token>` at startup to require `X-API-Key` on all requests. Useful if you expose the server on a LAN. On localhost with no flag, the server runs open — no key needed, no account, no registration.
- **Shell sandboxing**: `run_command` blocks destructive patterns (`rm -rf /`, `format`, `del /s`, registry deletes, etc.).
- **File sandboxing**: `read_file`/`write_file` restricted to safe paths. Auto-backup before write.
- **Sensitive content detection**: security layer can blur/mask regions containing passwords or credit card numbers before sending to AI.
- **Audit log**: access log with timestamp and tool name — no raw frames stored.
- **CORS**: strict origin policy in production mode.

---

## Workers System

Behind the FastAPI layer, a lightweight in-process orchestration system manages concurrent AI agents:

| Worker | Role |
|---|---|
| `MonitorWorker × N` | Per-screen semantic digest tick |
| `SpatialWorker` | Layout map + cursor + window tracking |
| `FusionWorker` | Global world snapshot from all monitors |
| `IntentWorker` | Intent timeline (what the user is trying to do) |
| `ActionArbiter` | Single-writer execution lease — prevents two agents from clicking simultaneously |
| `VerifyWorker` | Post-action verification timeline |
| `MemoryWorker` | Worker-level event compression in RAM |
| `SchedulerWorker` | Multi-monitor attention budget routing |

All workers run in RAM. No queues, no message brokers.

---

## Host Telemetry

Optional lightweight hardware monitoring used by the perception engine to adapt behavior under load:

- CPU and memory pressure (via `psutil`)
- GPU metrics (NVIDIA only, via `nvidia-smi` if present)
- Temperature readings (optional)
- Policy hints: reduces capture FPS automatically when CPU > 80%

---

## App Behavior Cache

ILUMINATY learns from action outcomes and reuses that knowledge across sessions:

- Stores per-app/per-action success rates in `~/.iluminaty/app_behavior_cache.sqlite3`
- Metadata only (no frames, no images)
- Improves `smart_locate` confidence on known apps over time
- Recovers from previous action failures using stored patterns

---

## Testing

```bash
pip install -e ".[ocr]"
pytest          # runs all 101 tests
pytest -v       # verbose
```

Test suite covers: MCP auth, multi-monitor capture consistency, perception pipeline, ring buffer, IPA compressor/encoder/stream, watch engine, visual memory, domain packs, grounding engine, workers, and more.

CI: GitHub Actions on Windows with Python 3.11 and 3.12.

---

## Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 | Windows 11 |
| Python | 3.10 | 3.12 |
| RAM | 2GB | 8GB (3 monitors) |
| CPU | Any | 4+ cores |
| GPU | Not required | NVIDIA/AMD for faster OCR |
| Network | None | None — fully local |

macOS and Linux: partial support (screen capture + perception work; some window management features are Windows-only).

---

## Project Structure

```
iluminaty/
├── main.py              # Entry point — argparse, server init
├── server.py            # FastAPI app — all HTTP/WS endpoints (6000 lines)
├── mcp_server.py        # MCP stdio server — 38 tool handlers
├── perception.py        # IPA 4-gate pipeline — 7 classes
├── vision.py            # OCR proxy + enriched frame builder
├── ocr_worker.py        # RapidOCR in isolated subprocess (spawn)
├── ring_buffer.py       # RAM-only circular frame buffer
├── multi_capture.py     # Multi-monitor capture orchestrator
├── capture.py           # Single-monitor screen capture (mss)
├── workers.py           # In-process worker system
├── watch_engine.py      # Event-driven wait conditions (11 types)
├── visual_memory.py     # Session persistence (~10–50KB gzip)
├── domain_packs.py      # TOML-based app specialization plugins
├── smart_locate.py      # OCR + UIAutomation coordinate resolver
├── grounding.py         # Hybrid grounding engine (multi-source)
├── actions.py           # OS action bridge (pyautogui)
├── windows.py           # Window management (user32.dll)
├── ui_tree.py           # Accessibility tree (UIAutomation)
├── browser.py           # Chrome DevTools Protocol bridge
├── spatial.py           # Screen zone semantic map
├── world_state.py       # WorldState engine (task/intent tracking)
├── security.py          # Auth, rate limiting, content masking
├── audio.py             # Audio ring buffer (optional)
├── host_telemetry.py    # CPU/GPU/memory monitoring
├── app_behavior_cache.py # Per-app action outcome learning
├── verifier.py          # Post-action verification
└── dashboard.py         # Web dashboard (/:8420)

ipa/
├── engine.py     # IPAEngine — main orchestrator
├── encoder.py    # VisualEncoder — imagehash patch encoding
├── compressor.py # DeltaCompressor — I/P frame compression
├── stream.py     # VisualStream — temporal patch buffer
└── types.py      # PatchFrame, MotionField, VisualContext
```

---

## License

MIT — free for personal and commercial use.

Built by [@sgodoy90](https://github.com/sgodoy90)
