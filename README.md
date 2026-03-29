# ILUMINATY
# Real-time visual perception for AI
# Zero-disk · RAM-only · Universal API

## Quick Start

```bash
cd iluminaty
python main.py start
```

## Custom config

```bash
python main.py start --fps 2 --buffer-seconds 60 --port 8420 --api-key my_secret
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/frame/latest` | Last frame as JPEG |
| GET | `/frame/latest?base64` | Last frame as base64 JSON |
| GET | `/frames?last=5` | Last N frames metadata |
| GET | `/frames?seconds=10&include_images=true` | Recent frames with images |
| GET | `/buffer/stats` | Buffer stats (memory, fps, efficiency) |
| POST | `/config` | Change settings live (fps, quality, etc.) |
| POST | `/buffer/flush` | Destroy all visual data |
| POST | `/capture/start` | Start capture |
| POST | `/capture/stop` | Stop capture |
| WS | `/ws/stream` | Live WebSocket stream |
| GET | `/health` | Health check |

## Architecture

```
Screen → Capture Engine → Ring Buffer (RAM) → API Server → Any AI
         (mss + PIL)     (deque, no disk)    (FastAPI)
```

## Security

- Zero disk: nothing is ever written to storage
- When the process dies, ALL visual data is gone
- Optional API key authentication
- Runs on localhost only by default
