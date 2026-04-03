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
  <img src="https://img.shields.io/badge/VLM-SmolVLM2_CUDA-00ff88?style=flat-square&labelColor=0a0a12" alt="VLM"/>
  <img src="https://img.shields.io/badge/license-MIT-00ff88?style=flat-square&labelColor=0a0a12" alt="License"/>
</p>

---

## What Is ILUMINATY?

ILUMINATY is a local runtime that gives any external AI (Claude, GPT, etc.):

- **Eyes**: continuous screen perception, OCR, multi-monitor, scene understanding.
- **Hands**: direct desktop actions via `act` tool (click, type, key, scroll, focus, move, drag).
- **VLM**: on-demand SmolVLM2-500M visual descriptions (GPU/CPU, idle until called).

The external AI decides. ILUMINATY observes, structures context, and executes.

---

## Architecture

```text
Screen(s) -> Per-Monitor Capture -> RingBuffer -> Perception Pipeline -> WorldState -> MCP -> AI
                                                                                        |
                                                          act(click/type/key/scroll) <--+
                                                          describe_screen (VLM on-demand) <--+
```

### Core Flow

```
1. AI calls spatial_state    -> knows where everything is
2. AI calls see_screen       -> sees what's on screen (OCR, ~200 tokens)
3. AI calls act(action, ...) -> executes directly (click, type, key, scroll, focus)
4. AI calls describe_screen  -> VLM describes what it sees (on-demand, ~7s GPU)
```

No grounding. No intent classifier. No middleware. The AI is the brain.

---

## Key Features

| Feature | Detail |
|---|---|
| **Multi-monitor** | Independent per-monitor capture, attention, scene state |
| **act tool** | Direct action executor — 7 actions, ~150ms each, zero middleware |
| **VLM on-demand** | SmolVLM2-500M loaded in VRAM idle, fires only when asked |
| **GPU auto-detect** | CUDA fp16 if available, CPU INT8 fallback |
| **4-gate perception** | Window change → histogram → pHash → optical flow → OCR |
| **Scene state machine** | IDLE, TYPING, SCROLLING, LOADING, VIDEO, TRANSITION, INTERACTION |
| **WorldState** | phase, readiness, uncertainty, affordances, visual facts |
| **Safety** | Kill switch, rate limiting, SAFE/HYBRID/RAW modes |
| **Zero-disk** | All frames in RAM ring buffer, encrypted optional disk spool |
| **~200 tokens/snapshot** | OCR text + metadata, no image tokens needed |

---

## Quick Start

### Terminal

```bash
# Clone and install
git clone https://github.com/sgodoy90/iluminaty.git
cd iluminaty
pip install -e ".[ocr]"

# Start with actions enabled
python -m iluminaty.main --actions --monitor 0 --fps 2

# With VLM (requires torch + transformers)
set ILUMINATY_VLM_CAPTION=1
set ILUMINATY_VLM_MODE=on_demand
python -m iluminaty.main --actions --monitor 0 --fps 2
```

### Batch Launcher (Windows)

```bash
start_vlm.bat    # Full setup with VLM + GPU auto-detect
```

### MCP Integration

```bash
claude mcp add iluminaty -- python /path/to/iluminaty/iluminaty/mcp_server.py
```

---

## MCP Tools (35)

### Eyes (Perception)
| Tool | Purpose |
|---|---|
| `spatial_state` | Monitors, active window, cursor position |
| `see_screen` | OCR text or screenshot of any monitor |
| `see_changes` | Recent screen changes with frames |
| `read_screen_text` | Full OCR of a monitor |
| `perception` | Events, scene state, attention |
| `perception_world` | WorldState: phase, readiness, uncertainty |
| `perception_trace` | Semantic transition history |
| `vision_query` | Query visual memory |
| `describe_screen` | On-demand VLM description |
| `get_context` | Workflow, focus, session info |

### Hands (Actions)
| Tool | Purpose |
|---|---|
| `act` | Direct executor: click, double_click, type, key, scroll, focus, move_mouse |
| `drag_screen` | Drag from A to B |
| `window_minimize/maximize/close` | Window control |
| `move_window` | Reposition windows |

### System
| Tool | Purpose |
|---|---|
| `screen_status` | Capture stats, FPS, RAM |
| `host_telemetry` | CPU, RAM, GPU, disk |
| `token_status` | Vision token usage |
| `set_operating_mode` | SAFE / HYBRID / RAW |
| `workers_*` | Multi-monitor worker system |
| `domain_pack_*` | Context-aware domain detection |

---

## VLM (Visual Language Model)

SmolVLM2-500M-Instruct runs locally:

- **On-demand mode** (default): model loaded in VRAM, 0% usage until `describe_screen` is called
- **Continuous mode**: deep loop triggers VLM every ~2s (set `ILUMINATY_VLM_MODE=continuous`)
- **GPU**: auto-detects CUDA, uses fp16 (~1.2GB VRAM, ~2s inference)
- **CPU fallback**: INT8 quantization (~600MB RAM, ~8-22s inference)

### Environment Variables

```
ILUMINATY_VLM_CAPTION=1              # Enable VLM
ILUMINATY_VLM_MODE=on_demand         # on_demand (default) or continuous
ILUMINATY_VLM_DEVICE=auto            # auto, cuda, cpu
ILUMINATY_VLM_MODEL=HuggingFaceTB/SmolVLM2-500M-Instruct
ILUMINATY_VLM_INT8=1                 # CPU INT8 quantization
ILUMINATY_VLM_IMAGE_SIZE=384         # Input image size
ILUMINATY_VLM_MAX_TOKENS=64          # Max generation tokens
```

---

## Operating Modes

- `SAFE` (default): safety + readiness + freshness checks on all actions.
- `HYBRID`: only blocks destructive actions.
- `RAW`: minimal path (kill switch still available).

---

## Privacy & Security

- All visual data is RAM-first by default (zero disk).
- VLM runs 100% local (no API calls, no data sent anywhere).
- Kill switch available in all modes.
- CORS restricted to specific methods/headers.
- Auth headers (`x-api-key`) supported across API + MCP + desktop.

---

## Source Structure

```
iluminaty/
  perception.py      — 4-gate perception pipeline + scene state
  visual_engine.py   — VLM provider (SmolVLM2 + on-demand describe)
  server.py          — FastAPI HTTP/WS server
  mcp_server.py      — MCP tools for Claude Code / Cursor
  capture.py         — Screen capture (mss)
  multi_capture.py   — Per-monitor capture orchestration
  ring_buffer.py     — Zero-disk RAM frame buffer
  world_state.py     — Semantic world state engine
  actions.py         — Action bridge (pyautogui)
  safety.py          — Kill switch + rate limiting
  monitors.py        — Multi-monitor detection
desktop-app/         — Tauri v2 desktop app
start_vlm.bat        — Windows launcher with VLM + GPU
```

---

## License

MIT

---

<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="60"/>
  <br/>
  <em>AI decides + ILUMINATY sees and acts.</em>
</p>
