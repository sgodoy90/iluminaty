<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="180"/>
</p>

<h1 align="center">ILUMINATY</h1>

<p align="center">
  <strong>Real-time perception + action runtime for AI agents.</strong><br/>
  <strong>Give any AI real eyes on your desktop — no screenshots needed.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-00ff88?style=flat-square&labelColor=0a0a12" alt="Version"/>
  <img src="https://img.shields.io/badge/IPA-v3-00ff88?style=flat-square&labelColor=0a0a12" alt="IPA v3"/>
  <img src="https://img.shields.io/badge/MCP_tools-34-00ff88?style=flat-square&labelColor=0a0a12" alt="MCP Tools"/>
  <img src="https://img.shields.io/badge/multi--monitor-3%2B-00ff88?style=flat-square&labelColor=0a0a12" alt="Multi-Monitor"/>
  <img src="https://img.shields.io/badge/license-MIT-00ff88?style=flat-square&labelColor=0a0a12" alt="License"/>
</p>

<p align="center">
  <a href="https://iluminaty.dev">iluminaty.dev</a>
</p>

---

## What Is ILUMINATY?

A local server that gives any AI **real eyes and hands** on your desktop. Connect Claude, GPT-4o, or any MCP-compatible AI and it can see your screen, understand what's happening, and act with precision — without sending screenshots to the cloud.

```
AI (Claude / GPT-4o)  ←→  ILUMINATY (local server)  ←→  Your Desktop
      decides               sees + acts in RT           screen, keyboard, mouse
```

**The AI is the brain. ILUMINATY is the body.**

---

## What Is IPA v3?

**IPA (Intelligent Perception Algorithm) v3** is the visual perception engine. It runs continuously in the background, processing your screen at 3fps through a codec-inspired pipeline:

- **I-frames**: full screen state compressed and stored
- **P-frames**: only what changed since the last frame — minimal storage
- **change_mask**: 25-byte bitmask of exactly which zones changed
- **motion detection**: cursor, typing, scroll, video, loading — classified automatically
- **gate events**: `motion_start`, `motion_end`, `content_loaded` — signals for when to act

**100% ours.** IPA v3 uses only `numpy + pillow + imagehash`. No Google models, no external dependencies in the core.

The AI receives **real screen images** (WebP) via `see_now` — not summaries or embeddings. IPA manages the *when* and *how much*, the AI decides the *what*.

---

## How It Compares to Computer Use

| | Computer Use (Anthropic) | ILUMINATY |
|---|---|---|
| **Screen capture** | Screenshot on demand | Continuous 3fps, always ready |
| **Monitors** | 1 only | 3+ with spatial context |
| **Change detection** | Manual comparison | Automatic (IPA change_mask) |
| **User context** | None | `get_spatial_context` — knows what's the user's |
| **Tokens per look** | ~20-30K (full image) | ~200 (text) or ~5K (low_res image) |
| **Click precision** | Model estimates coords | `smart_locate` — exact coords via OCR (3-34ms) |
| **Modal handling** | Manual | `operate_cycle` handles automatically |
| **Filesystem/terminal** | No | Yes — sandboxed |
| **Cost per 20-action task** | ~600K tokens | ~40K tokens |

---

## Quickstart

```bash
# Install
pip install -e .

# Start server (single monitor, port 8420)
python main.py start --port 8420 --fps 3 --actions

# Multi-monitor (auto-detect all)
python main.py start --port 8420 --fps 3 --actions --monitor 0

# With Pro key
set ILUMINATY_KEY=ILUM-pro-your-key
python main.py start --port 8420 --fps 3 --actions --monitor 0
```

Then connect your AI via MCP:

```json
{
  "mcpServers": {
    "iluminaty": {
      "command": "python",
      "args": ["run_mcp.py"],
      "env": {
        "ILUMINATY_API_URL": "http://127.0.0.1:8420",
        "ILUMINATY_KEY": "your-key-here"
      }
    }
  }
}
```

---

## MCP Tools (34)

### Vision — Real-time eyes

| Tool | What It Does |
|---|---|
| `see_now` | **PRIMARY.** Current screen image + IPA context (motion, scene, events) |
| `what_changed` | What changed in last N seconds + image of the key moment |
| `see_screen` | Screen image or text_only (~200 tokens) — use for cheap context checks |
| `see_changes` | Multiple frames showing temporal progression |
| `see_monitor` | Specific monitor with click coordinate mapping |
| `read_screen_text` | OCR — all visible text, optionally by region |
| `vision_query` | Ask about visual history: "what was on screen 30s ago?" |

### Perception — Understand the environment

| Tool | What It Does |
|---|---|
| `get_spatial_context` | **SESSION START.** Full spatial context: monitor layout, windows per monitor, user activity, safety rules |
| `get_context` | Current workflow: app, focus level, time in workflow |
| `perception` | Raw IPA events: scene state, motion type, OCR events |
| `perception_world` | WorldState: task phase, affordances, readiness, uncertainty |
| `spatial_state` | Monitor layout, cursor position, active window |

### Actions — Control the PC

| Tool | What It Does |
|---|---|
| `act` | **DIRECT.** click, double_click, type, key, scroll, focus, move_mouse. Supports `target="button name"` for smart coordinate resolution |
| `do_action` | Natural language instruction via SAFE loop (precheck → execute → verify) |
| `operate_cycle` | Full human-like cycle: orient → locate → focus → read → act → verify. Handles modals automatically |
| `drag_screen` | Drag from point A to point B |
| `set_operating_mode` | SAFE (guardrails on) / RAW (no guardrails) / HYBRID |

### Windows

| Tool | What It Does |
|---|---|
| `list_windows` | All visible windows with handle, title, position, monitor_id |
| `focus_window` | Bring window to front by title or handle |
| `window_minimize` / `window_maximize` / `window_close` | Window management |
| `move_window` | Reposition/resize a window |

### Browser

| Tool | What It Does |
|---|---|
| `browser_navigate` | Navigate to URL — preserves user context, opens new tab by default |
| `browser_tabs` | List all open tabs with titles and URLs |

### System

| Tool | What It Does |
|---|---|
| `run_command` | Execute shell command, returns stdout/stderr |
| `read_file` | Read a file (sandboxed) |
| `write_file` | Write a file (sandboxed, auto-backup) |
| `get_clipboard` | Read clipboard content |

### Status

| Tool | What It Does |
|---|---|
| `screen_status` | Buffer stats, FPS, capture state, active window |
| `agent_status` | Actions enabled, safety state, autonomy level |
| `get_audio_level` | Current audio level + speech detection |
| `os_dialog_status` / `os_dialog_resolve` | Detect and resolve blocking system dialogs |

---

## Smart Locate — Click Without Guessing

When the AI calls `act(action="click", target="Save button")`, ILUMINATY resolves exact coordinates automatically:

1. **OCR blocks** (pre-computed by perception, ~0ms) — finds text elements with exact bounding boxes
2. **UIAutomation tree** (native COM, ~5ms when available) — finds named elements from OS accessibility
3. **Returns not_found** if neither source has the element — AI falls back to visual estimation

Typical latency with warm cache: **3-34ms**. No guessing needed for any element that has visible text.

---

## Architecture

```
Screen (1-3 monitors)
    ↓ 3fps
[MultiMonitorCapture] → [RingBuffer] (RAM only, zero disk)
    ↓                        ↓
[IPA v3 Bridge]         [Perception Engine]
  change_mask              scene state
  motion events            OCR (DirectML GPU)
  gate events              WorldState
    ↓                        ↓
              [FastAPI Server :8420]
                      ↓
              [MCP Server stdio]
                      ↓
            Claude / GPT-4o / Any AI
```

**IPA v3** runs separately alongside the main perception engine. It maintains a temporal buffer of compressed frames (I/P-frame codec) and detects significant visual events (gate events) for the AI to react to.

---

## Plans

| Feature | Free | Pro |
|---|---|---|
| `see_now`, `see_screen`, `what_changed` | ✅ | ✅ |
| `get_spatial_context`, `perception`, `spatial_state` | ✅ | ✅ |
| `act`, `drag_screen`, window management | ✅ | ✅ |
| `do_action`, `operate_cycle` | — | ✅ |
| `browser_navigate`, `browser_tabs` | — | ✅ |
| `run_command`, `read_file`, `write_file` | — | ✅ |
| `list_windows`, `focus_window` | — | ✅ |
| `os_dialog_*`, `agent_status` | — | ✅ |

---

## Requirements

- Python 3.10+
- Windows (primary), macOS/Linux (partial)
- 4GB+ RAM (8GB recommended for multi-monitor)
- NVIDIA GPU recommended for OCR acceleration (ONNX DirectML)
- `numpy`, `pillow`, `imagehash`, `mss`, `fastapi`, `uvicorn`

---

## License

MIT — free for personal and commercial use.
