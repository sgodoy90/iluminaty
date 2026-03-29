# ILUMINATY

**Real-time visual perception for AI. Zero-disk. RAM-only. Universal.**

Give any AI **eyes on your screen** — not screenshots, not recordings. Live, continuous perception with intelligent change detection, OCR, audio capture, and privacy protection built in.

## Why ILUMINATY?

Every AI today is blind. They can process a screenshot you manually take, but they can't *see* what you're doing in real-time. ILUMINATY changes that:

- **Real-time**: The AI sees your screen as it changes, not after you take a screenshot
- **Zero disk**: Nothing is ever written to storage. Everything lives in RAM and dies with the process
- **Universal**: Works with any AI — Gemini, OpenAI, Claude, Ollama, or your own model
- **Privacy-first**: Auto-detects and blurs passwords, credit cards, API keys before the AI sees them
- **Lightweight**: ~2MB RAM for 30s of video buffer. 2-3% CPU

## Quick Start

```bash
# Clone and run
git clone https://github.com/sgodoy90/iluminaty.git
cd iluminaty
pip install -e ".[ocr]"

# Start ILUMINATY
python main.py start

# Open dashboard
# http://localhost:8420
```

### With audio
```bash
python main.py start --audio mic
```

### With Claude Code (MCP)
```bash
claude mcp add iluminaty -- python /path/to/iluminaty/iluminaty/mcp_server.py
# Then ask Claude: "what do you see on my screen?"
```

## What It Does

```
Your Screen ──→ [Capture] ──→ [Ring Buffer RAM] ──→ [API] ──→ Any AI
                  │                  │                │
                  ├─ Adaptive FPS    ├─ Zero disk     ├─ REST
                  ├─ WebP/JPEG/PNG   ├─ Auto-evict    ├─ WebSocket
                  ├─ Smart quality   ├─ ~2MB RAM      ├─ MCP
                  └─ Multi-monitor   └─ Dies on exit  └─ SDK
```

### The AI receives an enriched frame:
1. **Image** (WebP, 36KB) — the AI *sees*
2. **OCR text** (RapidOCR, 91% accuracy) — the AI *reads*
3. **Window context** — "user is in VS Code editing main.py"
4. **Workflow** — "user is coding, focused, 45 min in"
5. **Change diff** — "region at top-left changed 30%"
6. **Annotations** — user drew a red rectangle saying "look here"
7. **Audio transcript** — what was said in the last 10 seconds

## Features

| Feature | Description |
|---|---|
| Screen capture | Adaptive FPS (0.2-5.0), WebP/JPEG/PNG, multi-monitor |
| Ring buffer | RAM-only, circular, auto-eviction, zero disk ever |
| OCR | RapidOCR (ONNX), 91% accuracy, region crop, caching |
| Visual diff | Grid 8x6 change detection, heatmap, delta frames |
| Auto-blur | Passwords, credit cards, emails, API keys auto-blurred |
| Audio capture | Mic/system, VAD, ring buffer, transcription ready |
| Annotations | Draw rect/circle/arrow/text on live stream |
| Context engine | Workflow detection (9 types), focus tracking, app stats |
| AI adapters | Gemini Live, OpenAI, Claude, Generic — all built in |
| MCP server | 5 tools for Claude Code / Cursor integration |
| Plugin system | Event-driven, auto-load from plugins/ directory |
| Multi-monitor | 3+ monitors, smart FPS routing to active screen |
| Temporal memory | Optional text-only memory (no frames stored) |
| Security | Token auth, rate limiting, audit log, sensitive detection |
| Dashboard | Live web UI at localhost:8420 |

## API Endpoints

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
| GET | `/frames?last=5` | Last N frames metadata |

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

### AI
| Method | Path | Description |
|---|---|---|
| POST | `/ai/ask` | Send screen to any AI provider |

### Control
| Method | Path | Description |
|---|---|---|
| GET | `/` | Live dashboard |
| GET | `/health` | Health check |
| GET | `/buffer/stats` | Buffer statistics |
| POST | `/config` | Change settings live |
| POST | `/capture/start` | Start capture |
| POST | `/capture/stop` | Stop capture |
| POST | `/buffer/flush` | Destroy all visual data |
| GET | `/monitors` | Monitor info |
| GET | `/plugins` | Loaded plugins |

### Annotations
| Method | Path | Description |
|---|---|---|
| POST | `/annotations/add` | Draw annotation |
| GET | `/annotations/list` | List active annotations |
| DELETE | `/annotations/{id}` | Remove annotation |
| POST | `/annotations/clear` | Clear all |

## MCP Tools (Claude Code / Cursor)

| Tool | Description |
|---|---|
| `see_screen` | Get enriched screenshot with OCR + context |
| `see_changes` | What changed in the last N seconds |
| `annotate_screen` | Mark an area on screen |
| `read_screen_text` | OCR the screen or a region |
| `screen_status` | System status |

## CLI Options

```bash
python main.py start [OPTIONS]

--port 8420           API port
--host 127.0.0.1      API host (localhost only by default)
--fps 1.0             Target FPS
--buffer-seconds 30   Ring buffer duration
--quality 80          Image quality (10-95)
--format webp         Image format (webp/jpeg/png)
--max-width 1280      Max frame width
--monitor 1           Monitor (0=all, 1=primary)
--audio off           Audio mode (off/mic/system/all)
--audio-buffer 60     Audio buffer seconds
--api-key KEY         API key for authentication
--no-adaptive         Disable adaptive FPS
--no-smart-quality    Disable smart quality
```

## Architecture

```
iluminaty/
├── ring_buffer.py      RAM-only circular buffer
├── capture.py          Screen capture (mss + PIL)
├── vision.py           OCR + annotations + window detection
├── smart_diff.py       Grid-based visual diff + heatmap
├── security.py         Auth + rate limit + sensitive detection
├── audio.py            Audio capture + transcription
├── context.py          Workflow detection + focus tracking
├── adapters.py         AI providers (Gemini/OpenAI/Claude/Generic)
├── plugin_system.py    Event-driven plugin architecture
├── monitors.py         Multi-monitor management
├── memory.py           Optional temporal memory
├── server.py           FastAPI REST + WebSocket
├── dashboard.py        Live web dashboard
├── mcp_server.py       MCP protocol server
└── main.py             CLI entry point
```

## Platform Support

| OS | Screen | Audio | Window Detection |
|---|---|---|---|
| Windows | DXGI via mss | sounddevice | ctypes + user32 |
| macOS | CoreGraphics via mss | sounddevice | AppleScript |
| Linux (X11) | XShm via mss | sounddevice | xdotool |

## Performance

| Metric | Value |
|---|---|
| RAM (30s video buffer, WebP) | ~2 MB |
| RAM (60s audio buffer) | ~0.3 MB |
| CPU (1 fps) | ~2-3% |
| Frame size (WebP q80, 1280px) | ~36 KB |
| OCR accuracy | 91% |
| Disk usage | **ZERO** |
| Startup time | <3 seconds |

## Security

- **Zero disk**: Nothing written to storage. RAM-only buffers
- **Process death = data death**: Kill the process, everything is gone
- **Auto-blur**: Passwords, credit cards, emails, API keys detected and blurred
- **OCR redaction**: Sensitive text replaced with `[REDACTED]` before AI sees it
- **Token auth**: Rotating tokens with TTL
- **Rate limiting**: 120 req/min per client
- **Audit log**: Who accessed what, when (no frames stored)
- **Localhost only**: Binds to 127.0.0.1 by default

## License

MIT
