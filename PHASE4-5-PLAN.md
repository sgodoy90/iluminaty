# Phase 4 & 5 - Implementation Plan

## Phase 4: Production Ready

### F12: Rust Core (PyO3 bindings)

**Structure:**
```
iluminaty-core/              # Rust crate
├── Cargo.toml
├── src/
│   ├── lib.rs               # PyO3 module entry
│   ├── capture.rs           # Screen capture (DXGI/CoreGraphics/X11)
│   ├── buffer.rs            # Lock-free ring buffer
│   ├── compress.rs          # WebP/JPEG encoding (image crate)
│   ├── diff.rs              # Grid-based visual diff
│   ├── ocr.rs               # OCR via onnxruntime-rs
│   └── audio.rs             # Audio capture (cpal crate)
└── python/
    └── iluminaty_core/      # Python bindings
        └── __init__.pyi     # Type stubs
```

**Key Rust crates:**
- `mss` equivalent: `scrap` or `xcap` for screen capture
- `image` for compression
- `onnxruntime` for OCR models
- `cpal` for audio capture
- `pyo3` for Python bindings
- `parking_lot` for fast locks

**Migration path:**
1. Build Rust crate separately
2. `pip install iluminaty-core` installs the .pyd/.so
3. Python `iluminaty` package imports from `iluminaty_core` when available
4. Falls back to pure Python when Rust isn't installed

---

### F13: Desktop App (Tauri v2)

**Structure:**
```
iluminaty-app/
├── src-tauri/
│   ├── Cargo.toml
│   ├── src/
│   │   ├── main.rs
│   │   ├── tray.rs          # System tray icon + menu
│   │   ├── commands.rs      # Tauri commands (IPC)
│   │   └── config.rs        # App configuration
│   └── capabilities/
│       └── default.json     # Permissions
├── src/                     # Frontend (web)
│   ├── App.tsx              # Main app (React/Solid)
│   ├── pages/
│   │   ├── Dashboard.tsx    # Live stream + stats
│   │   ├── Settings.tsx     # Config panel
│   │   ├── Annotations.tsx  # Drawing tools
│   │   └── Security.tsx     # Auth + privacy settings
│   └── components/
│       ├── StreamView.tsx   # WebSocket stream viewer
│       ├── StatsPanel.tsx   # Buffer/FPS/RAM stats
│       ├── DrawCanvas.tsx   # Annotation drawing
│       ├── AudioMeter.tsx   # VU meter
│       └── AlertBanner.tsx  # Watchdog alerts
├── package.json
└── tauri.conf.json

Skills installed: 39 Tauri v2 skills in .agents/skills/
```

**Key features:**
- System tray icon (always running, minimal footprint)
- One-click start/stop
- Live dashboard (reuse existing HTML dashboard)
- Settings UI (format, quality, FPS, audio, privacy)
- Auto-start with OS (optional)
- Auto-update mechanism
- Installer: .msi (Windows), .dmg (macOS), .AppImage (Linux)

---

### F14: Client SDKs

**Python SDK:**
```
iluminaty-client/
├── pyproject.toml
├── iluminaty_client/
│   ├── __init__.py
│   ├── client.py            # IluminatyClient class
│   ├── models.py            # Typed response models
│   ├── stream.py            # WebSocket streaming
│   └── async_client.py      # Async version
└── tests/
```

**Usage:**
```python
from iluminaty_client import IluminatyClient

client = IluminatyClient("http://localhost:8420")
frame = client.get_frame()           # Latest frame
text = client.read_screen()          # OCR text
diff = client.get_diff()             # What changed
context = client.get_context()       # User workflow

# Streaming
for frame in client.stream():
    process(frame)

# AI integration
response = client.ask_ai("gemini", "What bug do you see?", api_key="...")
```

**Node.js SDK:**
```
iluminaty-js/
├── package.json
├── src/
│   ├── index.ts
│   ├── client.ts            # IluminatyClient class
│   ├── types.ts             # TypeScript interfaces
│   └── stream.ts            # WebSocket streaming
└── tests/
```

**Usage:**
```typescript
import { IluminatyClient } from 'iluminaty';

const client = new IluminatyClient('http://localhost:8420');
const frame = await client.getFrame();
const text = await client.readScreen();

// Streaming
client.onFrame((frame) => {
  console.log('New frame:', frame.width, frame.height);
});
```

---

## Phase 5: Ecosystem

### F15: Cloud Relay

**Architecture:**
```
User's PC                    Cloud                    Phone/Remote
┌──────────┐    E2E encrypted    ┌──────────┐    E2E encrypted    ┌──────────┐
│ILUMINATY │───WebSocket/WSS───→│ Relay    │←──WebSocket/WSS────│ Client   │
│(daemon)  │                    │ Server   │                    │ (web/app)│
└──────────┘                    └──────────┘                    └──────────┘
```

- Relay server NEVER sees raw frames (E2E encrypted)
- WebRTC for lowest latency (P2P when possible)
- Fallback to WSS relay when P2P not available
- Auth via OAuth (Google/GitHub)
- Deploy: Cloudflare Workers + Durable Objects

### F16: AI Model Router

**Logic:**
```
User question → Router → Decision:
  "What color is the button?"    → Vision API (need image)     → $$$
  "What does the error say?"     → OCR text only (no image)    → $
  "Summarize last 5 minutes"     → Context + memory (no image) → $
  "Watch for errors"             → Watchdog (no API call)      → FREE
```

- Reduces AI API costs by 60-80%
- Auto-selects cheapest sufficient model
- Cost tracking per session

### F17: Collaborative Mode

**Features:**
- Share ILUMINATY stream with another user (invite link)
- Both see the same screen in real-time
- Shared annotation layer
- AI mediator can see both perspectives
- Use case: remote pair programming, tech support, tutoring
