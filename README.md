<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="180"/>
</p>

<h1 align="center">ILUMINATY</h1>

<p align="center">
  <strong>Real-time eyes + hands for AI on desktop systems.</strong><br/>
  <strong>Pure MCP — the external AI decides, ILUMINATY observes and executes.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-00ff88?style=flat-square&labelColor=0a0a12" alt="Version"/>
  <img src="https://img.shields.io/badge/IPA-v2.1-00ff88?style=flat-square&labelColor=0a0a12" alt="IPA v2.1"/>
  <img src="https://img.shields.io/badge/python-3.10+-00ff88?style=flat-square&labelColor=0a0a12&logo=python&logoColor=00ff88" alt="Python"/>
  <img src="https://img.shields.io/badge/MCP_tools-35-00ff88?style=flat-square&labelColor=0a0a12" alt="MCP Tools"/>
  <img src="https://img.shields.io/badge/VLM-SmolVLM2_on_demand-00ff88?style=flat-square&labelColor=0a0a12" alt="VLM"/>
  <img src="https://img.shields.io/badge/license-MIT-00ff88?style=flat-square&labelColor=0a0a12" alt="License"/>
</p>

<p align="center">
  <a href="https://iluminaty.com">iluminaty.com</a> &nbsp;|&nbsp;
  <a href="https://iluminaty.dev">iluminaty.dev</a>
</p>

---

## What Is ILUMINATY?

ILUMINATY is a **local runtime** that gives any external AI full control of a desktop computer — seeing the screen, understanding context, and executing actions — without sending screenshots to the cloud.

Think of it this way:

- **Claude / GPT / Gemini** = the brain (decides what to do)
- **ILUMINATY** = the eyes + hands (sees the screen, clicks, types, navigates)

The AI connects via **MCP (Model Context Protocol)** or **HTTP API**. ILUMINATY handles everything else: multi-monitor perception, OCR, VLM visual descriptions, keyboard/mouse control, window management, and safety.

---

## Why ILUMINATY? The Token Problem

Traditional computer use (like Anthropic's Computer Use) sends a **screenshot** to the AI every time it needs to "see" the screen. Each screenshot costs **~30,000 tokens**.

ILUMINATY pre-processes everything locally and sends only **~200 tokens** of structured text:

| Approach | Tokens per "look" | Cost for 20-step task | Privacy |
|---|---|---|---|
| Screenshot-based (Computer Use) | ~30,000 | ~600,000 tokens (~$1.80) | Screenshots sent to cloud |
| **ILUMINATY** | **~200** | **~4,000 tokens (~$0.01)** | **Everything local** |

**150x more token efficient.** Same result. Zero cloud screenshots.

### How It Works

Instead of sending a raw image, ILUMINATY sends pre-digested context:

```
## Perception -- IDLE (conf 0.9)
Scene: TradingView chart showing BTCUSD at 66,409
3 events in last 30s:
  - Page loaded: BTCUSD candlestick chart (1h timeframe)
  - Content stabilized
Attention: center (chart area)
Mon 1: idle (active) | Mon 2: idle | Mon 3: idle
World: phase=idle readiness=True uncertainty=0.1
Window: brave -- BTCUSD 66,409 - TradingView
```

The AI reads this (~200 tokens), understands the full context, and decides what to do. No image processing needed.

---

## Key Features

### Perception (Eyes)
- **Multi-monitor**: Independent capture + perception per monitor (tested with 3)
- **4-gate pipeline**: Window change > histogram > perceptual hash > optical flow > OCR
- **Scene state machine**: Detects IDLE, TYPING, SCROLLING, LOADING, VIDEO, TRANSITION, INTERACTION
- **WorldState**: Semantic snapshot — phase, readiness, uncertainty, affordances, visual facts
- **Attention heatmap**: 8x6 grid tracking where screen activity concentrates
- **Context awareness**: Detects workflow (coding, trading, browsing, etc.), focus level, session time

### VLM (Visual Understanding)
- **SmolVLM2-500M** running 100% local (HuggingFace model)
- **On-demand mode** (default): Model loaded in GPU VRAM idle, fires only when you ask — zero background cost
- **GPU auto-detect**: CUDA fp16 if NVIDIA GPU available (~2s inference), CPU INT8 fallback (~8-22s)
- **describe_screen** tool: Ask "what do you see?" and get a visual description
- **Privacy**: No images ever leave your machine

### Actions (Hands)
- **`act` tool**: Direct executor — click, double_click, type, key, scroll, focus, move_mouse
- **~150ms per action**: No middleware, no grounding, no intent classifier. Claude decides, ILUMINATY executes.
- **Window management**: minimize, maximize, close, move, resize
- **Drag & drop**: Full drag_screen support

### Domain Packs
ILUMINATY auto-detects your current workflow and adapts perception:

| Domain | Detection | Behavior |
|---|---|---|
| **Trading** | TradingView, candlestick, BTCUSD, etc. | Higher FPS on chart area, price tracking |
| **Coding** | VS Code, terminal, git, IDE patterns | Focus on editor region, code change detection |
| **Research** | Browser, multiple tabs, reading patterns | Content extraction priority |
| **Support** | Helpdesk, ticket systems | Form detection, queue monitoring |
| **QA/Ops** | CI/CD, monitoring dashboards | Alert detection, status tracking |
| **Back Office** | Email, spreadsheets, ERP | Data entry assistance |

### Safety
- **Kill switch**: Emergency stop in all modes
- **3 operating modes**: SAFE (default), HYBRID, RAW
- **Rate limiting**: Configurable per action category
- **Audit log**: All actions recorded

---

## Quick Start

### Option 1: Desktop App (Recommended)

Download `ILUMINATY_1.0.0_x64-setup.exe` from [Releases](https://github.com/sgodoy90/iluminaty/releases).

The app:
1. Detects Python on your system
2. Creates an isolated virtual environment
3. Installs all dependencies automatically
4. Starts the server with one click
5. Shows GPU status and VLM configuration

### Option 2: Terminal

```bash
git clone https://github.com/sgodoy90/iluminaty.git
cd iluminaty
pip install -e ".[ocr]"

# Start server with actions enabled
python -m iluminaty.main --actions --monitor 0 --fps 2

# With VLM on-demand (requires torch)
pip install torch transformers
set ILUMINATY_VLM_CAPTION=1
set ILUMINATY_VLM_MODE=on_demand
python -m iluminaty.main --actions --monitor 0 --fps 2
```

### Option 3: Batch Launcher (Windows + VLM)

```bash
start_vlm.bat
```

Sets up VLM with GPU auto-detection, on-demand mode, and optimized parameters.

---

## Connecting an AI Provider

ILUMINATY works with any AI that supports MCP or HTTP. Here's how to connect each one:

### Claude (via Claude Code / Codex)

```bash
# Add ILUMINATY as MCP server
claude mcp add iluminaty -- python /path/to/iluminaty/iluminaty/mcp_server.py
```

Claude Code connects automatically. All 35 tools are available immediately.

### Claude (via API)

Use the Anthropic API with tool_use. ILUMINATY exposes tools that match Claude's tool calling format. Point your API calls to `http://127.0.0.1:8420` endpoints.

### OpenAI / GPT

Use function calling with the HTTP API. Example:

```python
import requests

# 1. See the screen
perception = requests.get("http://127.0.0.1:8420/perception").json()
print(perception["summary"])  # ~200 tokens of context

# 2. Send to GPT for decision
# GPT responds: "click at coordinates (500, 300)"

# 3. Execute via ILUMINATY
requests.post("http://127.0.0.1:8420/action/click?x=500&y=300")
```

### Any MCP-Compatible Client

ILUMINATY implements the full MCP stdio protocol. Any client that speaks MCP can connect:

```bash
# Generic MCP connection
python /path/to/iluminaty/iluminaty/mcp_server.py
```

---

## Usage Examples

### Example 1: Open Notepad and Write

```
AI: spatial_state → "3 monitors, active: Claude on Mon 3"
AI: act(key, keys="win+r") → "OK"
AI: act(type, text="notepad") → "OK"
AI: act(key, keys="enter") → "OK"
AI: act(type, text="Hello from ILUMINATY!") → "OK"
```

5 calls, ~750ms total. No screenshots needed.

### Example 2: Read What's on Screen

```
AI: see_screen(monitor=1) → "TradingView BTCUSD 66,409 chart with candlesticks..."
AI: perception_world → "phase=idle, domain=trading, readiness=true"
AI: describe_screen(monitor=1) → "VLM: Trading platform showing Bitcoin price chart..."
```

3 calls, ~200 tokens for OCR + ~7s for VLM. Full understanding.

### Example 3: Multi-Monitor Navigation

```
AI: spatial_state → "Mon 1: Brave (TradingView), Mon 2: Desktop, Mon 3: Claude"
AI: act(focus, title="Brave") → "OK"
AI: read_screen_text(monitor=1) → "BTCUSD 66,409 ..."
AI: act(key, keys="ctrl+t") → "OK" (new tab)
AI: act(type, text="github.com") → "OK"
AI: act(key, keys="enter") → "OK"
```

### Example 4: Check System Resources

```
AI: host_telemetry → "CPU 42%, RAM 68%, GPU 16%, Disk 89%"
AI: workers_status → "9 workers, 984 processed, 0 errors"
AI: screen_status → "Capture running, 5fps, 4.66MB RAM"
```

---

## MCP Tools Reference (35 tools)

### Perception (Eyes)

| Tool | Purpose | Tokens |
|---|---|---|
| `spatial_state` | Monitor layout, active window, cursor position | ~50 |
| `see_screen` | OCR text or screenshot of any monitor | ~200 |
| `see_changes` | Recent screen changes with key frames | ~500 |
| `read_screen_text` | Full OCR of a monitor | ~300 |
| `perception` | Events, scene state, attention map | ~200 |
| `perception_world` | WorldState: phase, readiness, uncertainty | ~100 |
| `perception_trace` | Semantic transition history | ~300 |
| `vision_query` | Query visual memory | ~100 |
| `describe_screen` | On-demand VLM description (SmolVLM2) | ~200 |
| `get_context` | Workflow, focus level, session info | ~100 |

### Actions (Hands)

| Tool | Actions | Latency |
|---|---|---|
| `act` | click, double_click, type, key, scroll, focus, move_mouse | ~150ms |
| `drag_screen` | Drag from point A to B | ~200ms |
| `window_minimize` | Minimize window by title | ~100ms |
| `window_maximize` | Maximize window by title | ~100ms |
| `window_close` | Close window by title | ~100ms |
| `move_window` | Move/resize window | ~100ms |

### System & Context

| Tool | Purpose |
|---|---|
| `screen_status` | Capture stats, FPS, RAM usage |
| `host_telemetry` | CPU, RAM, GPU, disk, temperature |
| `token_status` | Vision token usage and budget |
| `set_operating_mode` | Switch SAFE / HYBRID / RAW |
| `action_precheck` | Safety check before acting |
| `verify_action` | Post-action verification |
| `workers_*` | Multi-monitor worker system (6 tools) |
| `domain_pack_*` | Domain detection config (2 tools) |
| `behavior_*` | Action behavior cache (3 tools) |
| `get_audio_level` | Audio level + speech detection |

---

## VLM (Visual Language Model)

### On-Demand Mode (Default)

The model loads into GPU VRAM at startup and stays **completely idle** — 0% GPU, 0% CPU. Only runs inference when you explicitly call `describe_screen`.

```
Model loaded → idle in VRAM (1.2GB) → describe_screen called → inference (2s GPU) → result → idle
```

### Configuration

| Variable | Default | Description |
|---|---|---|
| `ILUMINATY_VLM_CAPTION` | `0` | Enable VLM (`1` to enable) |
| `ILUMINATY_VLM_MODE` | `on_demand` | `on_demand` or `continuous` |
| `ILUMINATY_VLM_DEVICE` | `auto` | `auto`, `cuda`, `cpu` |
| `ILUMINATY_VLM_DTYPE` | `auto` | `auto`, `fp16`, `bf16`, `fp32` |
| `ILUMINATY_VLM_MODEL` | `HuggingFaceTB/SmolVLM2-500M-Instruct` | HuggingFace model ID |
| `ILUMINATY_VLM_INT8` | `1` | CPU INT8 quantization |
| `ILUMINATY_VLM_IMAGE_SIZE` | `384` | Input image resize |
| `ILUMINATY_VLM_MAX_TOKENS` | `64` | Max generation tokens |

### GPU Auto-Detection

The `/system/gpu` endpoint reports:

```json
{
  "cuda_available": true,
  "gpu_name": "NVIDIA GeForce RTX 3070",
  "vram_total_mb": 8192,
  "vram_free_mb": 6800,
  "torch_version": "2.11.0",
  "cuda_version": "12.6"
}
```

The desktop app shows this in Settings > VLM section.

---

## Architecture

```
Screen(s) ──> Per-Monitor Capture ──> RingBuffer (RAM) ──> Perception Pipeline ──> WorldState
                                                               │
                                                               ├── Fast Loop (4-10Hz): scene, OCR diff, attention
                                                               └── Deep Loop (0.5-2Hz): visual analysis (native)
                                                                                          │
                                                               VLM (on-demand only) <─────┘

MCP/HTTP ──> AI reads WorldState (~200 tokens)
         ──> AI decides action
         ──> act(click/type/key/scroll) ──> pyautogui ──> screen changes
         ──> AI verifies result
```

### 7 Layers

| Layer | Components | Purpose |
|---|---|---|
| 1. OS Control | ActionBridge, WindowManager, Clipboard, ProcessManager | Direct system interaction |
| 2. UI Intelligence | UITree (Accessibility), find_element | Element detection |
| 3. App Control | VSCode Bridge, Terminal, Git | Application-specific ops |
| 4. Web Control | Chrome DevTools Protocol | Browser automation |
| 5. File System | Sandboxed read/write/list | Safe file access |
| 6. Orchestration | ActionResolver, Verifier, Recovery | Action flow management |
| 7. Safety | Kill switch, rate limiting, audit | Protection layer |

### Perception Pipeline (4 Gates)

| Gate | Latency | What It Does |
|---|---|---|
| 0. Window Change | <0.1ms | OS-level window/title detection |
| 1. Histogram | <0.5ms | Fast change scoring (0.0-1.0) |
| 2. Perceptual Hash | <1ms | Structural similarity check |
| 3. Optical Flow | 5-25ms | Motion vectors + spatial diff |
| 4. OCR Diff | 50-200ms | Text change detection (throttled) |

---

## Operating Modes

| Mode | Safety | Speed | Use Case |
|---|---|---|---|
| **SAFE** (default) | Full: readiness + freshness + rate limits | ~200ms overhead | General use, untrusted environments |
| **HYBRID** | Only blocks destructive actions | ~50ms overhead | Trusted AI, productivity workflows |
| **RAW** | Kill switch only | ~0ms overhead | Expert setups, external AI handles safety |

---

## Privacy & Security

- **Zero cloud screenshots**: All perception is local. No images leave your machine.
- **VLM is local**: SmolVLM2 runs on your GPU/CPU. No API calls for visual understanding.
- **RAM-first**: All frames in memory ring buffer. Optional encrypted disk spool.
- **CORS restricted**: Only allows GET/POST/OPTIONS with specific headers.
- **Auth supported**: `x-api-key` header across all endpoints.
- **Kill switch**: Available in all operating modes.
- **Rate limiting**: Configurable per action category (safe/normal/destructive).

---

## Domains

- **Website**: [iluminaty.com](https://iluminaty.com)
- **Developer Portal**: [iluminaty.dev](https://iluminaty.dev)
- **GitHub**: [github.com/sgodoy90/iluminaty](https://github.com/sgodoy90/iluminaty)

---

## Source Structure

```
iluminaty/
  main.py              -- CLI entry point
  server.py            -- FastAPI HTTP/WS server (139 endpoints)
  mcp_server.py        -- MCP tools (35 tools for Claude Code / Cursor)
  perception.py        -- 4-gate perception pipeline + scene state
  visual_engine.py     -- VLM provider (SmolVLM2 + on-demand describe)
  capture.py           -- Screen capture (mss)
  multi_capture.py     -- Per-monitor capture orchestration
  ring_buffer.py       -- Zero-disk RAM frame buffer
  world_state.py       -- Semantic world state engine
  actions.py           -- Action bridge (pyautogui)
  monitors.py          -- Multi-monitor detection
  safety.py            -- Kill switch + rate limiting
  autonomy.py          -- SUGGEST / CONFIRM / AUTO modes
  domain_packs.py      -- Auto-detect trading/coding/research context
  licensing.py         -- Free / Pro tier management
desktop-app/           -- Tauri v2 desktop app (Rust + HTML/JS)
start_vlm.bat          -- Windows launcher with VLM + GPU config
```

---

## License

MIT

---

<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="60"/>
  <br/>
  <em>AI decides + ILUMINATY sees and acts.</em><br/>
  <em>150x more efficient than screenshot-based computer use.</em>
</p>
