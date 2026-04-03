<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="180"/>
</p>

<h1 align="center">ILUMINATY</h1>

<p align="center">
  <strong>Real-time perception + action runtime for AI agents.</strong><br/>
  <strong>150x more token-efficient than screenshot-based computer use.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-00ff88?style=flat-square&labelColor=0a0a12" alt="Version"/>
  <img src="https://img.shields.io/badge/IPA-v2.1-00ff88?style=flat-square&labelColor=0a0a12" alt="IPA v2.1"/>
  <img src="https://img.shields.io/badge/MCP_tools-35-00ff88?style=flat-square&labelColor=0a0a12" alt="MCP Tools"/>
  <img src="https://img.shields.io/badge/VLM-on_demand-00ff88?style=flat-square&labelColor=0a0a12" alt="VLM"/>
  <img src="https://img.shields.io/badge/license-MIT-00ff88?style=flat-square&labelColor=0a0a12" alt="License"/>
</p>

<p align="center">
  <a href="https://iluminaty.com">iluminaty.com</a> &nbsp;|&nbsp;
  <a href="https://iluminaty.dev">iluminaty.dev</a>
</p>

---

## What Is ILUMINATY?

A local server that gives any AI **eyes and hands** on your desktop. The AI sees, decides, and acts — without sending screenshots to the cloud.

```
AI (Claude / GPT / Gemini)  ←→  ILUMINATY (local server)  ←→  Your Desktop
        decides                    sees + acts                 screen, keyboard, mouse
```

**The AI is the brain. ILUMINATY is the body.**

---

## What Is IPA?

**IPA (Intelligent Perception Algorithm)** is the core perception engine inside ILUMINATY. It processes your screen in real-time through 4 gates, each progressively deeper:

| Gate | What It Does | Speed |
|---|---|---|
| **0. Window Change** | Detects app/title switches via OS API | <0.1ms |
| **1. Histogram** | Fast pixel-level change scoring (0.0-1.0) | <0.5ms |
| **2. Perceptual Hash** | Structural similarity (catches layout changes) | <1ms |
| **3. Optical Flow** | Motion vectors + spatial diff on 8x6 grid | 5-25ms |
| **4. OCR Diff** | Text change detection (throttled) | 50-200ms |

IPA outputs a **WorldState** every tick — a semantic snapshot of what's happening on screen:

```json
{
  "task_phase": "idle",
  "active_surface": "brave :: TradingView - BTCUSD",
  "readiness": true,
  "uncertainty": 0.1,
  "staleness_ms": 112,
  "affordances": ["click", "type_text", "hotkey", "scroll"],
  "visual_facts": [
    {"kind": "surface", "text": "TradingView candlestick chart"},
    {"kind": "text", "text": "BTCUSD 66,409 -0.72%"}
  ]
}
```

This is what the AI reads — **~200 tokens** instead of a 30,000 token screenshot.

---

## Why 150x More Efficient?

| | Screenshot-Based | ILUMINATY |
|---|---|---|
| **Tokens per "look"** | ~30,000 (raw image) | **~200** (pre-digested text) |
| **20-step task** | 600,000 tokens (~$1.80) | **4,000 tokens (~$0.01)** |
| **Privacy** | Screenshots go to cloud | **100% local** |
| **Multi-monitor** | 1 screen only | **3+ monitors** |
| **Action latency** | 200-500ms (API round-trip) | **~150ms (local)** |

---

## Quick Start

### Desktop App (non-technical users)

Download the installer — it handles Python, dependencies, and server startup automatically.

### Terminal

```bash
git clone https://github.com/sgodoy90/iluminaty.git
cd iluminaty
pip install -e ".[ocr]"
python -m iluminaty.main --actions --monitor 0 --fps 2
```

Server starts at `http://127.0.0.1:8420`

### Connect Claude Code

```bash
claude mcp add iluminaty -- python /path/to/iluminaty/iluminaty/mcp_server.py
```

All 35 MCP tools become available immediately.

---

## Real Examples

### Open an app, type something, close it

```
spatial_state       → Mon 1: Brave, Mon 2: Desktop, Mon 3: Claude | cursor at (500,500)
act(key, "win+r")   → Run dialog opens
act(type, "notepad") → typed 7 chars
act(key, "enter")    → Notepad opens
act(type, "Hello!")   → typed 6 chars
act(key, "alt+f4")   → close dialog
act(key, "tab")      → focus "Don't Save"
act(key, "enter")    → closed without saving
```

8 steps. ~1.2 seconds total. Zero screenshots.

### Read what's on any monitor

```
see_screen(monitor=1)       → "TradingView BTCUSD 66,409 candlestick chart 1h timeframe..."
see_screen(monitor=3)       → "Claude Code conversation about ILUMINATY benchmark..."
read_screen_text(monitor=1) → Full OCR text, 93% confidence
```

~200 tokens each. The AI knows exactly what's on every screen.

### Get a visual description (VLM)

```
describe_screen(monitor=1) → "Trading platform showing Bitcoin price chart with
                              candlestick pattern, sidebar with FOREX pairs..."
```

On-demand. SmolVLM2-500M fires once (~2s on GPU), then goes idle.

### Check system state

```
perception_world → phase=idle, domain=trading, readiness=true, uncertainty=0.1
host_telemetry   → CPU 42%, RAM 68%, GPU 16%, Disk 89%
get_context      → workflow=coding, focus=HIGH, 29min session
```

---

## VLM (Visual Language Model)

SmolVLM2-500M runs **100% local** on your hardware:

- **On-demand** (default): Model loaded in VRAM idle. 0% usage. Fires only when you call `describe_screen`. Returns to idle immediately.
- **GPU**: Auto-detects NVIDIA CUDA. Uses fp16 (~1.2GB VRAM, ~2s per inference).
- **CPU fallback**: INT8 quantization (~600MB RAM, ~8-22s per inference).

```bash
# Enable VLM
set ILUMINATY_VLM_CAPTION=1
set ILUMINATY_VLM_MODE=on_demand
set ILUMINATY_VLM_DEVICE=auto
```

The desktop app shows detected GPU model and VRAM in Settings.

---

## MCP Tools (35)

### Eyes — See and understand

| Tool | What It Does |
|---|---|
| `spatial_state` | Monitor layout, active window, cursor position |
| `see_screen` | OCR text or image of any monitor (~200 tokens) |
| `read_screen_text` | Full OCR of a monitor (93%+ accuracy) |
| `describe_screen` | On-demand VLM visual description |
| `perception` | Scene state, events, attention heatmap |
| `perception_world` | WorldState: phase, readiness, uncertainty |
| `see_changes` | Recent screen changes with key frames |
| `get_context` | Current workflow, focus level, session info |

### Hands — Click, type, navigate

| Tool | What It Does |
|---|---|
| `act` | Direct executor: click, double_click, type, key, scroll, focus, move_mouse |
| `drag_screen` | Drag from A to B |
| `window_minimize/maximize/close` | Window control by title |
| `move_window` | Reposition/resize windows |

### System — Monitor and configure

| Tool | What It Does |
|---|---|
| `host_telemetry` | CPU, RAM, GPU, disk, temperature |
| `screen_status` | Capture FPS, RAM, frame stats |
| `workers_status` | Multi-monitor worker health |
| `domain_pack_list` | Active domain detection (trading, coding, etc.) |
| `set_operating_mode` | SAFE / HYBRID / RAW |

---

## Domain Packs

IPA auto-detects what you're doing and adapts perception:

| Domain | Triggers | Behavior |
|---|---|---|
| **Trading** | TradingView, BTCUSD, candlestick keywords | Higher FPS on chart, price tracking |
| **Coding** | VS Code, terminal, git patterns | Editor focus, code change detection |
| **Research** | Browser, multiple tabs, reading | Content extraction priority |
| **Support** | Helpdesk, ticket systems | Form + queue monitoring |
| **QA/Ops** | CI/CD, dashboards | Alert detection |
| **Back Office** | Email, spreadsheets, ERP | Data entry assistance |

---

## AI Provider Support

The desktop app stores API keys securely in your OS credential manager (Windows Credential Manager, macOS Keychain, Linux Secret Service):

| Provider | Key Format | Where to Get |
|---|---|---|
| **Anthropic** (Claude) | `sk-ant-api03-*` | [console.anthropic.com](https://console.anthropic.com) |
| **OpenAI** (GPT) | `sk-proj-*` | [platform.openai.com](https://platform.openai.com/api-keys) |
| **Google Gemini** | `AIzaSy*` | [aistudio.google.com](https://aistudio.google.com/apikey) |

Keys are validated against the provider API before saving. Never stored in plain text.

---

## Operating Modes

| Mode | What It Does | Best For |
|---|---|---|
| **SAFE** | Full safety checks on every action | General use |
| **HYBRID** | Only blocks destructive actions | Productivity |
| **RAW** | Kill switch only | Expert setups |

---

## Architecture

```
Screens → Per-Monitor Capture → RAM Buffer → IPA Pipeline → WorldState → MCP → AI
                                                                            ↓
                                              act(click/type/key) ←────────┘
                                              describe_screen (VLM) ←──────┘
```

### 7 Layers

| # | Layer | Purpose |
|---|---|---|
| 1 | OS Control | Mouse, keyboard, windows, clipboard |
| 2 | UI Intelligence | Accessibility tree, element detection |
| 3 | App Control | VS Code, terminal, git bridges |
| 4 | Web Control | Chrome DevTools Protocol |
| 5 | File System | Sandboxed read/write |
| 6 | Orchestration | Action resolver, verifier, recovery |
| 7 | Safety | Kill switch, rate limiting, audit |

---

## Privacy

- All perception runs locally. No screenshots leave your machine.
- VLM runs on your GPU/CPU. No cloud API for visual understanding.
- API keys stored in OS-native credential managers (encrypted).
- RAM-first architecture. Optional encrypted disk spool.

---

## Links

- **Website**: [iluminaty.com](https://iluminaty.com)
- **Developer Portal**: [iluminaty.dev](https://iluminaty.dev)
- **GitHub**: [github.com/sgodoy90/iluminaty](https://github.com/sgodoy90/iluminaty)

---

## License

MIT

<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="60"/>
  <br/>
  <em>AI decides. ILUMINATY sees and acts.</em>
</p>
