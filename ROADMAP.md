# ILUMINATY - Product Roadmap
## Real-time visual (and audio) perception for AI

---

## Phase 0: Foundation (DONE - v0.2.0)
> "Make it work"

- [x] Ring buffer in RAM (zero disk)
- [x] Screen capture with adaptive FPS
- [x] WebP/JPEG/PNG smart compression
- [x] REST + WebSocket API
- [x] Live dashboard (stream + stats)
- [x] Annotation system (rect/circle/arrow/text)
- [x] Cross-platform window detection
- [x] Security layer (tokens, rate limit)
- [x] Sensitive content detection + auto-redact
- [x] MCP server (5 tools)
- [x] Universal AI prompt in English

---

## Phase 1: Core Intelligence (v0.3.0)
> "Make the AI actually understand what it sees"

### F01: Native OCR Engine
**Priority: CRITICAL | Effort: Medium**
- [ ] Integrate PaddleOCR (faster than tesseract, better multilang)
- [ ] Fallback chain: PaddleOCR -> Tesseract -> None
- [ ] OCR caching: only re-OCR when frame changes significantly
- [ ] Region-aware OCR: prioritize active window area
- [ ] Output: structured text blocks with positions
- **Why**: Without OCR, the AI depends 100% on vision models to read text.
  Most vision models struggle with small text. OCR makes it reliable.

### F02: Smart Visual Diff
**Priority: HIGH | Effort: Medium**
- [ ] Perceptual hashing (pHash) instead of MD5 for similarity
- [ ] Region-level change detection (WHERE on screen changed)
- [ ] Change heatmap: accumulate changes over time
- [ ] Motion vector estimation (which direction things moved)
- [ ] Semantic diff: "a new window appeared" vs "text scrolled"
- [ ] Delta frames: only send the changed region, not full frame
- **Why**: Sending full frames when only 5% changed wastes tokens.
  Delta frames = 90% token savings for the AI.

### F03: Screen Region Blur (Auto-Privacy)
**Priority: HIGH | Effort: Low**
- [ ] Auto-detect password fields (OCR + UI element detection)
- [ ] Blur banking/financial apps by window title pattern
- [ ] User-configurable blur zones (always blur region X,Y,W,H)
- [ ] Blur-before-send: frame goes to AI already sanitized
- [ ] Privacy profiles: "work" (blur nothing) vs "personal" (blur banking/social)
- **Why**: If someone uses ILUMINATY with ChatGPT/Claude and their bank
  is open, those credentials go to the cloud. Auto-blur prevents this.

### F04: pip install iluminaty
**Priority: HIGH | Effort: Low**
- [ ] Package as proper Python package with setup.py/pyproject.toml
- [ ] CLI entry point: `iluminaty start`, `iluminaty status`
- [ ] Auto-dependency installation
- [ ] Platform-specific extras: `iluminaty[ocr]`, `iluminaty[audio]`
- [ ] PyPI publish (private first, public when ready)
- **Why**: Adoption. `pip install iluminaty && iluminaty start` is the
  entire onboarding. Zero friction.

---

## Phase 2: Full Perception (v0.4.0)
> "See AND hear like a human"

### F05: Audio Capture + Transcription
**Priority: HIGH | Effort: Medium**
- [ ] System audio capture (what the computer plays)
- [ ] Microphone capture (what the user says) - opt-in only
- [ ] Audio ring buffer (same pattern as video - RAM only, no disk)
- [ ] Real-time transcription: Whisper.cpp (local) or Deepgram (cloud)
- [ ] Speaker diarization: "Speaker 1 said X, Speaker 2 said Y"
- [ ] Audio + visual sync: match transcript to screen state
- [ ] Enriched frame now includes: image + OCR + transcript
- [ ] Privacy: auto-mute/redact during sensitive audio (banking calls)
- **Why**: A Zoom call where the AI sees the screen but can't hear
  the conversation is half-blind. Audio completes the picture.

### F06: Gemini Live Streaming Adapter
**Priority: HIGH | Effort: High**
- [ ] WebSocket bridge: ILUMINATY -> Gemini Multimodal Live API
- [ ] Bidirectional: Gemini can see screen AND talk back
- [ ] Frame rate adaptation: match Gemini's ingestion rate
- [ ] Audio passthrough: screen + audio -> Gemini in real-time
- [ ] Function calling: Gemini can trigger actions based on what it sees
- [ ] Session management: reconnect, resume, context preservation
- **Why**: Gemini Live is the most capable real-time multimodal API.
  This makes ILUMINATY the bridge between your screen and Gemini.

### F07: OpenAI Realtime Adapter  
**Priority: MEDIUM | Effort: Medium**
- [ ] Frame-by-frame image injection into Realtime API sessions
- [ ] Smart frame selection: only send when content changes
- [ ] Audio passthrough to gpt-realtime
- [ ] Image + audio sync for coherent multimodal input
- **Why**: OpenAI Realtime supports image input now. But it's
  frame-by-frame, not streaming. ILUMINATY handles the optimization.

---

## Phase 3: Intelligence Layer (v0.5.0)
> "Don't just see — understand"

### F08: Context Engine
**Priority: HIGH | Effort: High**
- [ ] App state tracking: know which app is focused and for how long
- [ ] Workflow detection: "user is coding" vs "user is browsing" vs "user is in a meeting"
- [ ] Activity timeline: compressed summary of what happened in the last hour
- [ ] Intent prediction: "user switched to browser after error in code" -> probably searching for fix
- [ ] Context injection: AI prompt includes workflow context, not just current frame
- **Why**: A single frame is a photo. Context is a story. The AI needs
  the story to give useful help.

### F09: Plugin System (Pipes)
**Priority: MEDIUM | Effort: High**
- [ ] Plugin API: `class IluminatyPlugin` with hooks for frame/audio/events
- [ ] Built-in plugins: log-to-file, alert-on-change, auto-screenshot
- [ ] Plugin marketplace / registry
- [ ] Sandboxed execution: plugins can't access raw frames directly
- [ ] Event system: on_frame, on_change, on_app_switch, on_text_detected
- **Why**: Extensibility. Let the community build what we can't imagine.
  Screenpipe's pipes are their strongest ecosystem feature.

### F10: Multi-Monitor Intelligence
**Priority: MEDIUM | Effort: Low**
- [ ] Per-monitor capture with independent FPS/quality
- [ ] Focus-follows-mouse: higher FPS on active monitor
- [ ] Cross-monitor context: "user moved window from monitor 1 to 2"
- [ ] Monitor-specific blur profiles
- **Why**: Power users have 2-3 monitors. Capturing all at the same
  FPS/quality wastes resources. Smart routing is the answer.

### F11: Temporal Memory (Optional Persistent Mode)
**Priority: MEDIUM | Effort: Medium**
- [ ] Optional: write compressed summaries to disk (not raw frames)
- [ ] "What did I do at 3pm?" without storing actual screenshots
- [ ] Semantic index: search by description, not by time
- [ ] Auto-expire: summaries older than N days are deleted
- [ ] Encrypted at rest with user-provided key
- [ ] Toggle: pure RAM mode (default) vs memory mode (opt-in)
- **Why**: Some users WANT history. But we never store raw frames.
  Only AI-generated summaries of what was on screen.

---

## Phase 4: Production Ready (v1.0.0)
> "Ship it"

### F12: Rust Core Migration
**Priority: HIGH (when traffic demands it) | Effort: Very High**
- [ ] Rewrite capture engine in Rust (screen + audio)
- [ ] Rewrite ring buffer in Rust (lock-free circular buffer)
- [ ] Rewrite compression pipeline in Rust (WebP/AVIF native)
- [ ] Python bindings via PyO3 (import iluminaty_core)
- [ ] Keep API layer in Python (FastAPI is fine for this)
- [ ] Keep plugins in Python (developer experience > raw speed)
- [ ] Benchmarks: target <1% CPU at 5fps, <500us frame latency
- **Why**: Python works for v0.x. But at scale (10fps, OCR, audio,
  plugins), the GIL becomes a bottleneck. Rust core + Python API
  is the pattern that works (see: screenpipe, polars, ruff).

### F13: Desktop App (Tauri)
**Priority: MEDIUM | Effort: High**
- [ ] System tray app (always running, minimal footprint)
- [ ] Native UI for config, blur zones, plugin management
- [ ] One-click installer for Windows/Mac/Linux
- [ ] Auto-update mechanism
- [ ] First-run setup wizard (permissions, default AI provider)
- **Why**: Not everyone is comfortable with `python main.py start`.
  A tray icon that "just works" is the difference between dev tool
  and product.

### F14: SDK for Developers
**Priority: HIGH | Effort: Medium**
- [ ] `npm install iluminaty` - Node.js client SDK
- [ ] `pip install iluminaty-client` - Python client SDK
- [ ] Typed interfaces for all endpoints
- [ ] Streaming helpers: async generators for frame/audio
- [ ] Provider adapters: plug-and-play for OpenAI/Claude/Gemini
- [ ] Example apps: "build a coding assistant that sees your screen"
- **Why**: If we want other developers to build on ILUMINATY,
  they need SDKs, not curl commands.

---

## Phase 5: Ecosystem (v2.0.0)
> "Let others build on it"

### F15: Cloud Relay (Optional)
**Priority: LOW | Effort: Very High**
- [ ] Optional cloud proxy for remote access (phone -> PC screen)
- [ ] E2E encrypted: cloud never sees raw frames
- [ ] WebRTC for low-latency streaming
- [ ] Auth via OAuth (Google/GitHub/Apple)
- **Why**: "See my PC screen from my phone" is a killer feature.
  But only if E2E encrypted.

### F16: AI Model Router
**Priority: MEDIUM | Effort: Medium**
- [ ] Auto-select best AI based on query type
- [ ] Text question -> send OCR text only (cheap, fast)
- [ ] Visual question -> send frame + text (more expensive)
- [ ] Complex question -> send multiple frames + audio + context
- [ ] Cost tracking: "this session cost $0.12 in API calls"
- **Why**: Not every question needs a $0.05 vision API call.
  Smart routing saves 70% of costs.

### F17: Collaborative Mode
**Priority: LOW | Effort: High**
- [ ] Share your ILUMINATY stream with another user
- [ ] Real-time pair programming: both see the same screen
- [ ] AI mediator: summarizes what both users are looking at
- [ ] Annotation sharing: one user draws, the other sees
- **Why**: Remote pair programming where both devs AND the AI
  can see the screen in real-time.

---

## Migration Strategy: Python -> Rust

```
v0.2 - v0.5:  Pure Python (validate product-market fit)
v0.6 - v0.9:  Rust core module (capture + buffer + compression)
               Python API + plugins (FastAPI + plugin system)
               Bridge: PyO3 bindings
v1.0:          Rust core + Python shell (the polars model)
v2.0:          Consider full Rust if Python shell becomes bottleneck
```

### Why this order:
1. Python ships 10x faster -> validate faster
2. Rust core handles the hot path (capture at 60fps if needed)
3. Python API handles the cold path (HTTP requests, plugin logic)
4. Developers write plugins in Python (bigger community, easier)
5. End users don't care what language it's in — they care that it works

---

## Success Metrics

| Milestone | Metric | Target |
|-----------|--------|--------|
| v0.3 | OCR reads screen text accurately | >90% accuracy |
| v0.4 | Audio transcription works | <2s latency |
| v0.5 | Context engine detects workflow | 5+ workflow types |
| v1.0 | CPU usage at 5fps + OCR | <5% |
| v1.0 | RAM usage (30s buffer) | <50MB |
| v1.0 | Frame latency (capture to API) | <100ms |
| v1.0 | Startup time | <2 seconds |
| v2.0 | GitHub stars | 1,000+ |
| v2.0 | Weekly active users | 500+ |
