<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="180"/>
</p>

<h1 align="center">ILUMINATY</h1>

<p align="center">
  <strong>Real-time visual perception + PC control for AI agents.</strong><br/>
  Local MCP server. No cloud. No screenshots. AI sees your screen live.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-00ff88?style=flat-square&labelColor=0a0a12" alt="Version"/>
  <img src="https://img.shields.io/badge/IPA-v3-00ff88?style=flat-square&labelColor=0a0a12" alt="IPA v3"/>
  <img src="https://img.shields.io/badge/MCP_tools-38-00ff88?style=flat-square&labelColor=0a0a12" alt="MCP Tools"/>
  <img src="https://img.shields.io/badge/multi--monitor-3%2B-00ff88?style=flat-square&labelColor=0a0a12" alt="Multi-Monitor"/>
  <img src="https://github.com/sgodoy90/iluminaty/actions/workflows/tests.yml/badge.svg" alt="Tests"/>
  <img src="https://img.shields.io/badge/license-MIT-00ff88?style=flat-square&labelColor=0a0a12" alt="License"/>
</p>

---

## Quick Start

```bash
pip install iluminaty[ocr]
iluminaty start
```

That's it. The server starts on `:8420` and auto-detects all your monitors.

```
[ILUMINATY] IPA v3 running — 3 monitors detected
[ILUMINATY] Capture: 3.0 fps per monitor
[ILUMINATY] API: http://127.0.0.1:8420
[ILUMINATY] MCP config written → ~/.mcp.json
```

**Connect to Claude Code** — add to your project's `.mcp.json`:

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

Or run `iluminaty mcp-config` to write it automatically.

**Try it:**

```
call see_now
```

Claude now sees your screen in real-time. All 38 tools available immediately — no registration, no API key required.

---

## Install from source

```bash
git clone https://github.com/sgodoy90/iluminaty
cd iluminaty
pip install -e ".[ocr]"
iluminaty start
```

---

## Why Not Just Use Computer Use?

| | Computer Use | ILUMINATY |
|---|---|---|
| **Privacy** | Screenshots sent to Anthropic API | 100% local — nothing leaves your machine |
| **Monitors** | 1 only | 3+ with spatial context per monitor |
| **Token cost per action** | ~20-30K tokens (full screenshot) | ~200 tokens (text) or ~5K (low_res) |
| **Cost per 20-action task** | ~600K tokens | ~40K tokens (**15x cheaper**) |
| **Change detection** | None — blind between calls | Continuous IPA at 3fps — always ready |
| **Click precision** | Model estimates coordinates | `smart_locate` — OCR exact coords (3-34ms) |
| **Waiting for events** | Polling loop (token-expensive) | `watch_and_notify` — zero tokens while waiting |
| **Session continuity** | Starts blind each session | `get_session_memory` — knows what you were doing |
| **Multi-agent** | Not supported | Multiple AI agents on different monitors |
| **Works offline** | No | Yes — no internet required for operation |

**Measured in production (stress test, 60s, 4 concurrent clients):**
- 158 requests served
- 0 crashes, 0 errors
- Max latency: 29ms

---

## What Is IPA v3?

The **Intelligent Perception Algorithm** runs continuously in background, processing your screen at 3fps through a codec-inspired pipeline:

- **I-frames**: full screen state (keyframes)
- **P-frames**: only what changed since last frame — 95% smaller
- **change_mask**: 25-byte bitmask of which screen zones changed
- **Gate events**: `motion_start`, `motion_end`, `content_loaded` — signals for when to act

**100% proprietary.** IPA v3 uses only `numpy + pillow + imagehash`. No Google SigLIP, no external model dependencies in the core.

---

## MCP Tools (38)

### Vision

| Tool | What It Does |
|---|---|
| `see_now` | **PRIMARY.** Current screen image + IPA context (motion, scene, events) |
| `what_changed` | What changed in last N seconds + image of the key moment |
| `see_screen` | Screen image or text_only (~200 tokens) |
| `see_changes` | Multiple frames showing temporal progression |
| `see_monitor` | Specific monitor with click coordinate mapping |
| `read_screen_text` | All visible text via OCR, optionally by region |
| `vision_query` | Ask about visual history: "what was on screen 30s ago?" |

### Perception

| Tool | What It Does |
|---|---|
| `get_spatial_context` | **SESSION START.** Monitor layout, windows per monitor, user activity |
| `get_context` | Current workflow: app, focus level, time in workflow |
| `perception` | Raw IPA events: scene state, motion type, OCR events |
| `perception_world` | WorldState: task phase, affordances, readiness, uncertainty |
| `spatial_state` | Monitor layout, cursor position, active window |

### Active Waiting (NEW)

| Tool | What It Does |
|---|---|
| `watch_and_notify` | Wait for screen event without consuming tokens. Conditions: `page_loaded`, `text_appeared`, `build_passed`, `idle`, `element_visible`, and more |
| `monitor_until` | Like `watch_and_notify` but for long tasks (builds, uploads, deployments) — up to 10 minutes |

### Session Memory (NEW)

| Tool | What It Does |
|---|---|
| `get_session_memory` | Load context from previous session — monitor layout, active windows, recent events (~300 tokens, no images) |
| `save_session_memory` | Save current context before ending session — persists to `~/.iluminaty/memory/` |

### Actions

| Tool | What It Does |
|---|---|
| `act` | click, double_click, type, key, scroll, move_mouse. Supports `target="button name"` |
| `do_action` | Natural language instruction with SAFE loop |
| `operate_cycle` | Full human-like cycle: orient → locate → focus → read → act → verify |
| `drag_screen` | Drag from point A to point B |

### Windows

| Tool | What It Does |
|---|---|
| `list_windows` | All visible windows with position and monitor_id |
| `focus_window` | Bring window to front |
| `window_minimize` / `window_maximize` / `window_close` | Window management |
| `move_window` | Reposition and resize |

### Browser

| Tool | What It Does |
|---|---|
| `browser_navigate` | Navigate to URL |
| `browser_tabs` | List all open tabs |

### System

| Tool | What It Does |
|---|---|
| `run_command` | Execute shell command (sandboxed) |
| `read_file` / `write_file` | File I/O (sandboxed, auto-backup) |
| `get_clipboard` | Read clipboard |
| `screen_status` | Buffer stats, FPS, capture state |
| `os_dialog_status` / `os_dialog_resolve` | Detect and resolve system dialogs |

---

## Smart Locate

When the AI calls `act(action="click", target="Save button")`, ILUMINATY resolves exact coordinates:

1. **OCR cache** (~0ms) — pre-computed text blocks with exact bounding boxes
2. **UIAutomation tree** (~5ms) — native OS accessibility API
3. Returns `not_found` if element not visible — AI falls back to visual estimation

Warm cache latency: **3-34ms**. Works for any element with visible text.

---

## Architecture

```
Screen (1-3 monitors)
    |
    v  3fps
[MultiMonitorCapture] --> [RingBuffer] (RAM only, zero disk)
    |                          |
    v                          v
[IPA v3 Bridge]         [Perception Engine]
  change_mask              scene state
  motion events            OCR subprocess (isolated DML)
  gate events              WorldState
    |                          |
    +----------+---------------+
               |
         [FastAPI :8420]
               |
         [MCP stdio]
               |
      Claude / GPT-4o / Any AI
```

**OCR isolation**: RapidOCR runs in a fully isolated subprocess (no shared GIL). Main server process never loads ONNX/DirectML. Prevents segfaults on multi-monitor setups.

---

## Visual Memory

ILUMINATY persists session context between AI sessions:

```python
# At session start — Claude knows what you were doing
get_session_memory()
# Returns: monitor layout, last active window, recent events, OCR snippets

# At session end — saves context for next time
save_session_memory()
# Saves: ~/.iluminaty/memory/session_TIMESTAMP.json.gz (~10-50KB)
```

Also auto-saves on server shutdown.

---

## Domain Packs

Adapt ILUMINATY to specific apps with `.toml` config files:

```toml
[pack]
name = "tradingview"

[detection]
url_keywords = ["tradingview.com"]
text_keywords = ["btcusd", "rsi", "macd"]

[[watch_conditions]]
name = "price_above"
type = "ocr_number_above"
```

Example packs: `domain_packs/tradingview.toml.example`, `domain_packs/vscode.toml.example`

---

## Requirements

- **OS**: Windows 10/11 (primary), macOS/Linux (partial)
- **Python**: 3.10+
- **RAM**: 4GB+ (8GB recommended for 3 monitors)
- **GPU**: Optional — ONNX DirectML for faster OCR
- **Network**: None — fully local

---

## Plans

| Feature | Free | Pro |
|---|---|---|
| `see_now`, `see_screen`, `what_changed` | YES | YES |
| `get_spatial_context`, `perception` | YES | YES |
| `act`, window management | YES | YES |
| `watch_and_notify`, `monitor_until` | YES | YES |
| `get_session_memory`, `save_session_memory` | YES | YES |
| `do_action`, `operate_cycle` | — | YES |
| `browser_navigate`, `run_command` | — | YES |
| `read_file`, `write_file` | — | YES |

---

## License

MIT — free for personal and commercial use.

Built by [@sgodoy90](https://github.com/sgodoy90)
