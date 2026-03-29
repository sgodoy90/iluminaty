<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="180"/>
</p>

<h1 align="center">ILUMINATY</h1>

<p align="center">
  <strong>Real-time visual perception for AI. Zero-disk. RAM-only. Universal.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.5.0-00ff88?style=flat-square&labelColor=0a0a12" alt="Version"/>
  <img src="https://img.shields.io/badge/python-3.10+-00ff88?style=flat-square&labelColor=0a0a12&logo=python&logoColor=00ff88" alt="Python"/>
  <img src="https://img.shields.io/badge/platform-Win%20%7C%20Mac%20%7C%20Linux-00ff88?style=flat-square&labelColor=0a0a12" alt="Platform"/>
  <img src="https://img.shields.io/badge/disk_usage-ZERO-00ff88?style=flat-square&labelColor=0a0a12" alt="Zero Disk"/>
  <img src="https://img.shields.io/badge/license-MIT-00ff88?style=flat-square&labelColor=0a0a12" alt="License"/>
</p>

<p align="center">
  <em>Give any AI eyes on your screen. Not screenshots. Live, continuous perception.</em>
</p>

---

## The Problem

Every AI today is **blind between screenshots**. You manually take a screenshot, paste it, wait for analysis. Meanwhile, the bug already disappeared, the error scrolled away, the notification vanished. You lose context. The AI loses context.

## The Solution

ILUMINATY gives AI **persistent vision** — a lightweight daemon that captures your screen in real-time, stores frames in a RAM-only ring buffer (zero disk, ever), and exposes everything through a universal API that any AI can consume.

```
Your Screen ──→ Capture ──→ Ring Buffer (RAM) ──→ API ──→ Any AI
                  │              │                  │
                  ├ Adaptive FPS  ├ Zero disk        ├ REST + WebSocket
                  ├ WebP/JPEG/PNG ├ Auto-eviction    ├ MCP Protocol
                  ├ Smart quality  ├ ~2MB for 30s     ├ Python/Node SDK
                  └ Multi-monitor  └ Dies on exit     └ 4 AI providers
```

When the process dies, **everything disappears**. No traces. No recovery. Privacy by destruction.

## Features

### Vision
| Feature | Description |
|---|---|
| Screen capture | Adaptive FPS (0.2-5.0), WebP/JPEG/PNG, 36KB/frame |
| OCR | RapidOCR engine, 91% accuracy, 114+ text blocks |
| Visual diff | Grid 8x6 change detection with heatmap |
| Spatial map | Knows WHERE things are on screen |
| Multi-monitor | 3+ monitors with smart FPS routing |
| Annotations | Draw rect/circle/arrow/text on live stream |

### Audio
| Feature | Description |
|---|---|
| Mic capture | Cross-platform via sounddevice |
| System audio | Capture what the computer plays |
| VAD | Voice Activity Detection (speech vs silence) |
| Transcription | Whisper local engine (optional) |

### Intelligence
| Feature | Description |
|---|---|
| Context engine | 9 workflow types: coding, browsing, meeting, designing... |
| Focus tracking | HIGH/MEDIUM/LOW based on app switch frequency |
| Proactive watchdog | 8 triggers: errors, build fails, security warnings |
| User profile | Learns your preferences across sessions |
| Multi-modal fusion | Unifies vision + audio + context in one prompt |
| AI router | Auto-selects cheapest model. 80% cost savings |

### Privacy & Security
| Feature | Description |
|---|---|
| Zero disk | RAM-only ring buffer. Nothing ever written to storage |
| Auto-blur | Passwords, credit cards, emails, API keys blurred before AI sees them |
| OCR redaction | Sensitive text replaced with `[REDACTED]` |
| Token auth | Rotating tokens with TTL |
| Rate limiting | 120 req/min per client |
| Audit log | Who accessed what (no frames stored) |
| Process death = data death | Kill the process, everything is gone |

### Integrations
| Feature | Description |
|---|---|
| AI adapters | Gemini Live, OpenAI, Claude, Generic |
| MCP server | 7 tools for Claude Code / Cursor |
| REST API | 32 endpoints |
| WebSocket | Live frame streaming |
| Plugin system | Event-driven with auto-load |
| Collaborative | Shared rooms with annotations |
| Cloud relay | E2E encrypted remote access |

## Quick Start

### Option 1: Portable executable (recommended)
```
Download ILUMINATY.exe → Double click → Done.
No Python. No dependencies. No terminal.
```

### Option 2: From source
```bash
git clone https://github.com/sgodoy90/iluminaty.git
cd iluminaty
pip install -e ".[ocr]"
python main.py start
# Open http://localhost:8420
```

### Option 3: With audio
```bash
python main.py start --audio mic --fps 2 --format webp
```

### Option 4: Connect to Claude Code (MCP)
```bash
claude mcp add iluminaty -- python /path/to/iluminaty/iluminaty/mcp_server.py
# Then ask: "what do you see on my screen?"
```

## What the AI Receives

ILUMINATY doesn't just send pixels. It sends an **enriched perception package**:

```
## Live Screen Perception - 2026-03-29 17:15:42
**User is coding** in VS Code | Focus: HIGH | Silent | 3 monitor(s)

### ALERTS (action may be needed)
- **[ERROR]** detected: build failed at line 42

**Window**: main.py - iluminaty - Visual Studio Code

### Visible Text (114 blocks, 91% confidence)
[OCR extracted text here]

### Recent Speech
> "Can you fix the import error on line 15?"

### Screen Layout
- top-left: code content (25% of screen)
- bottom-left: terminal content (25%)

### User Profile
- Editor: VS Code
- Languages: Python, TypeScript, Rust
- Primary workflow: coding

### How to Help
An image of the current screen is attached.
You have full visual, audio, and contextual awareness.
```

## API Reference

### Vision
| Method | Path | Description |
|---|---|---|
| GET | `/vision/snapshot` | Enriched frame: image + OCR + context + prompt |
| GET | `/vision/ocr` | OCR text (full screen or region) |
| GET | `/vision/diff` | What changed and where (grid + heatmap) |
| GET | `/vision/window` | Active window info |

### Frames
| Method | Path | Description |
|---|---|---|
| GET | `/frame/latest` | Latest frame (raw image) |
| GET | `/frame/latest?base64` | Latest frame (base64 JSON) |
| GET | `/frame/annotated` | Frame with annotations overlay |
| GET | `/frames?last=5` | Last N frames |

### Audio
| Method | Path | Description |
|---|---|---|
| GET | `/audio/stats` | Audio buffer stats |
| GET | `/audio/level` | Real-time VU meter |
| GET | `/audio/transcribe?seconds=10` | Transcribe recent audio |
| GET | `/audio/devices` | List audio devices |

### Context
| Method | Path | Description |
|---|---|---|
| GET | `/context/state` | Current workflow + focus level |
| GET | `/context/apps` | Time per app |
| GET | `/context/workflows` | Time per workflow type |
| GET | `/context/timeline` | Activity timeline |

### Watchdog
| Method | Path | Description |
|---|---|---|
| GET | `/watchdog/alerts` | Active alerts |
| GET | `/watchdog/triggers` | Configured triggers |
| POST | `/watchdog/scan` | Manual scan |
| POST | `/watchdog/acknowledge/{id}` | Acknowledge alert |

### AI
| Method | Path | Description |
|---|---|---|
| POST | `/ai/ask` | Send screen to any AI provider |
| POST | `/ai/route` | Auto-route to cheapest model |
| GET | `/ai/router/stats` | Cost savings stats |

### Collaborative
| Method | Path | Description |
|---|---|---|
| POST | `/collab/create` | Create shared room |
| POST | `/collab/join` | Join as viewer |
| GET | `/collab/rooms` | List active rooms |
| POST | `/collab/annotate` | Shared annotation |

### Control
| Method | Path | Description |
|---|---|---|
| GET | `/` | Live dashboard |
| GET | `/health` | Health check |
| GET | `/system/overview` | All components status |
| GET | `/buffer/stats` | Buffer statistics |
| GET | `/monitors` | Monitor info |
| GET | `/plugins` | Loaded plugins |
| POST | `/config` | Change settings live |
| POST | `/buffer/flush` | Destroy all visual data |

## MCP Tools

| Tool | Description |
|---|---|
| `see_screen` | Enriched screenshot with OCR + context |
| `see_changes` | What changed in the last N seconds |
| `annotate_screen` | Mark an area on screen |
| `read_screen_text` | OCR the screen or a region |
| `screen_status` | System status |
| `get_context` | User workflow + focus level |
| `get_audio_level` | Audio level + speech detection |

## SDKs

### Python
```python
from iluminaty_client import Iluminaty

eye = Iluminaty()
snapshot = eye.see()           # see the screen
text = eye.read()              # OCR text
diff = eye.what_changed()      # visual diff
ctx = eye.what_doing()         # user workflow
eye.mark(100, 200, "Bug here") # annotate

# Ask AI
answer = eye.ask("gemini", "What error?", api_key="...")

# Stream
for frame in eye.watch(fps=2):
    print(f"{frame.width}x{frame.height}")
```

### Node.js
```typescript
import { Iluminaty } from 'iluminaty';

const eye = new Iluminaty();
const snapshot = await eye.see();
const text = await eye.read();
const diff = await eye.whatChanged();

const stop = eye.watch((frame) => {
  console.log(`${frame.width}x${frame.height}`);
}, 2);
```

## CLI Options

```
python main.py start [OPTIONS]

--port 8420              API port
--host 127.0.0.1         Localhost only (default)
--fps 1.0                Target FPS
--buffer-seconds 30      Ring buffer duration
--quality 80             Image quality (10-95)
--format webp            Image format (webp/jpeg/png)
--max-width 1280         Max frame width
--monitor 1              Monitor (0=all, 1=primary)
--audio off              Audio (off/mic/system/all)
--audio-buffer 60        Audio buffer seconds
--api-key KEY            Authentication key
--no-adaptive            Disable adaptive FPS
--no-smart-quality       Disable smart quality
```

## Architecture

```
iluminaty/
├── ring_buffer.py      RAM-only circular buffer
├── capture.py          Screen capture engine (mss + PIL)
├── vision.py           OCR + annotations + auto-blur + window detection
├── smart_diff.py       Grid-based visual diff + heatmap
├── audio.py            Audio capture + VAD + transcription
├── context.py          Workflow detection + focus tracking
├── watchdog.py         Proactive alerts (8 built-in triggers)
├── spatial.py          Screen layout mapping
├── actions.py          Computer use (click, type, scroll)
├── profile.py          User preference learning
├── fusion.py           Multi-modal perception fusion
├── router.py           AI model cost optimizer
├── relay.py            E2E encrypted cloud relay
├── collab.py           Collaborative shared sessions
├── adapters.py         AI providers (Gemini/OpenAI/Claude/Generic)
├── security.py         Auth + rate limit + sensitive detection
├── plugin_system.py    Event-driven plugin architecture
├── monitors.py         Multi-monitor management
├── memory.py           Optional temporal memory
├── server.py           FastAPI (32 endpoints + WebSocket)
├── dashboard.py        Professional live web UI
├── mcp_server.py       MCP protocol (7 tools)
└── main.py             CLI entry point
```

## Performance

| Metric | Value |
|---|---|
| RAM (30s video buffer) | ~2 MB |
| RAM (60s audio buffer) | ~0.3 MB |
| CPU (1 fps) | ~2-3% |
| Frame size (WebP q80) | ~36 KB |
| Frame efficiency | ~80% dropped (no change) |
| OCR accuracy | 91% |
| Disk usage | **ZERO** |
| Startup time | <3 seconds |
| Portable exe | 184 MB (includes everything) |
| Tauri installer | 2.5 MB |

## Platform Support

| OS | Screen | Audio | Window | Status |
|---|---|---|---|---|
| Windows | DXGI | sounddevice | ctypes | Tested |
| macOS | CoreGraphics | sounddevice | AppleScript | Supported |
| Linux (X11) | XShm | sounddevice | xdotool | Supported |

## What Makes This Different

| | Screenshots | Screenpipe | ILUMINATY |
|---|---|---|---|
| **Mode** | Manual | Records everything | Live perception |
| **Storage** | Disk | ~20GB/month | **ZERO** |
| **RAM** | N/A | 0.5-3GB | **~2MB** |
| **When** | After the fact | After the fact | **Real-time** |
| **AI** | Paste manually | Search later | **Sees now** |
| **Privacy** | Files on disk | SQLite database | **RAM only, dies on exit** |
| **Audio** | No | Yes | **Yes (optional)** |
| **Annotations** | No | No | **Yes (draw on screen)** |
| **Auto-blur** | No | DRM only | **Passwords, cards, emails** |
| **Cost** | Free | $30+ | **Free & open source** |

## License

MIT

---

<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="60"/>
  <br/>
  <em>The AI sees all.</em>
</p>
