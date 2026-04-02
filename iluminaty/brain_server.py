"""
ILUMINATY Brain Web Server
===========================
Interfaz web tipo Claude para hablar con el modelo local.
Corre en http://localhost:8421 — sin Ollama, sin APIs externas.

Uso:
  python -m iluminaty.brain_server
  python -m iluminaty.brain_server --4bit
  python -m iluminaty.brain_server --model Qwen/Qwen2.5-7B-Instruct --4bit
  python -m iluminaty.brain_server --gguf C:/models/mi_modelo.gguf
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import pathlib
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import uvicorn

SESSIONS_DIR = pathlib.Path.home() / ".iluminaty" / "brain_sessions"
TRAINING_FILE = pathlib.Path("brain_training_data.jsonl")
ILUMINATY_URL = "http://127.0.0.1:8420"

# ─── Global state ────────────────────────────────────────────────────────────

_brain = None          # BrainEngine instance
_model_name = "?"
_backend = "?"
_executor = ThreadPoolExecutor(max_workers=1)  # one inference at a time
_streams: dict[str, asyncio.Queue] = {}        # stream_id -> token queue


# ─── ILUMINATY API connection ─────────────────────────────────────────────────

def _get_world() -> Optional[dict]:
    """Fetch current WorldState from ILUMINATY API."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{ILUMINATY_URL}/perception/world")
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _build_system_prompt(world: Optional[dict]) -> str:
    """
    Build a rich system prompt that tells the model exactly what it is,
    what ILUMINATY is, and what the current screen state looks like.
    """
    base = (
        "Eres IluminatyBrain — el cerebro de inteligencia artificial del sistema ILUMINATY.\n"
        "ILUMINATY es un sistema que ve la pantalla del usuario en tiempo real (IPA — ILUMINATY Perception Algorithm) "
        "y puede controlar el PC: clicks, teclado, comandos, browser, ventanas.\n"
        "Tienes acceso a lo que el usuario tiene en pantalla ahora mismo.\n"
        "Responde en español, de forma útil y directa.\n"
        "Cuando el usuario te pida hacer algo en su PC, puedes proponer la acción concreta "
        "o explicar cómo ILUMINATY lo ejecutaría.\n"
    )

    if not world:
        return base + "\n[ILUMINATY no está corriendo — sin acceso a la pantalla ahora mismo.]"

    # Extract world state info
    surface    = world.get("active_surface", "?")
    phase      = world.get("task_phase", "?")
    ready      = world.get("readiness", False)
    domain     = world.get("domain_pack", "general")
    risk       = world.get("risk_mode", "safe")
    affordances = world.get("affordances", [])[:8]
    entities   = world.get("entities", [])[:6]
    texts      = [
        f.get("text", "") or f.get("content", "")
        for f in (world.get("visual_facts") or [])[:5]
        if f.get("text") or f.get("content")
    ]
    reasons    = world.get("readiness_reasons", [])[:3]
    uncertainty = world.get("uncertainty", 0)
    staleness  = world.get("staleness_ms", 0)

    ctx = (
        f"\n[ESTADO ACTUAL DE PANTALLA — hace {staleness}ms]\n"
        f"  App activa   : {surface}\n"
        f"  Fase         : {phase}\n"
        f"  Dominio      : {domain}\n"
        f"  Listo para actuar: {ready}"
    )
    if reasons:
        ctx += f" ({', '.join(reasons)})"
    if affordances:
        ctx += f"\n  Acciones disponibles: {', '.join(affordances)}"
    if entities:
        ctx += f"\n  Elementos detectados: {', '.join(str(e) for e in entities)}"
    if texts:
        ctx += f"\n  Texto visible: {' | '.join(t[:60] for t in texts if t)}"
    if uncertainty > 0.5:
        ctx += f"\n  [contexto incierto: {uncertainty:.0%}]"

    return base + ctx


# ─── FastAPI app ─────────────────────────────────────────────────────────────

app = FastAPI(title="IluminatyBrain", docs_url=None, redoc_url=None)


# ─── Chat API ────────────────────────────────────────────────────────────────

def _build_chatml(messages: list[dict], system: str) -> str:
    """Build ChatML prompt for Qwen/compatible models."""
    parts = [f"<|im_start|>system\n{system}<|im_end|>"]
    for m in messages:
        parts.append(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def _infer_stream(
    stream_id: str,
    prompt: str,
    max_tokens: int,
    loop: asyncio.AbstractEventLoop,
):
    """Run in ThreadPoolExecutor — pushes tokens to asyncio queue."""
    q = _streams.get(stream_id)
    if not q:
        return

    def push(token: str | None):
        asyncio.run_coroutine_threadsafe(q.put(token), loop)

    try:
        if _brain._backend == "llamacpp":
            for chunk in _brain._model(
                prompt,
                max_tokens=max_tokens,
                temperature=0.7,
                stream=True,
                stop=["<|im_end|>", "<|im_start|>", "\n\nUser", "\n\nUsuario"],
                echo=False,
            ):
                token = chunk["choices"][0]["text"]
                push(token)
        else:
            # transformers — use TextIteratorStreamer
            import torch
            try:
                from transformers import TextIteratorStreamer
                from threading import Thread

                streamer = TextIteratorStreamer(
                    _brain._tokenizer,
                    skip_prompt=True,
                    skip_special_tokens=True,
                )
                device = next(_brain._model.parameters()).device
                inputs = _brain._tokenizer(
                    prompt, return_tensors="pt",
                    truncation=True, max_length=1536,
                ).to(device)
                gen_kwargs = {
                    **inputs,
                    "max_new_tokens": max_tokens,
                    "temperature": 0.7,
                    "do_sample": True,
                    "top_p": 0.9,
                    "pad_token_id": _brain._tokenizer.eos_token_id,
                    "streamer": streamer,
                }
                t = Thread(target=_brain._model.generate, kwargs=gen_kwargs)
                t.start()
                for chunk in streamer:
                    # Stop at ChatML end tokens
                    if "<|im_end|>" in chunk or "<|im_start|>" in chunk:
                        before = chunk.split("<|im")[0]
                        if before:
                            push(before)
                        break
                    push(chunk)
                t.join()
            except ImportError:
                # Fallback: generate all at once
                text = _brain._infer_transformers(prompt)
                push(text)
    except Exception as e:
        push(f"\n[Error: {e}]")
    finally:
        push(None)  # sentinel = done


@app.post("/api/chat")
async def api_chat(req: Request):
    data = await req.json()
    messages: list[dict] = data.get("messages", [])
    max_tokens: int = int(data.get("max_tokens", 512))

    if not _brain:
        return JSONResponse({"error": "Modelo no cargado"}, status_code=503)

    # Fetch live screen context from ILUMINATY
    world = await asyncio.get_event_loop().run_in_executor(None, _get_world)
    system = _build_system_prompt(world)

    prompt = _build_chatml(messages[-16:], system)
    stream_id = str(uuid.uuid4())
    _streams[stream_id] = asyncio.Queue()

    loop = asyncio.get_event_loop()
    _executor.submit(_infer_stream, stream_id, prompt, max_tokens, loop)

    # Return stream_id + whether ILUMINATY is connected
    return JSONResponse({"stream_id": stream_id, "iluminaty_connected": world is not None})


@app.get("/api/stream/{stream_id}")
async def api_stream(stream_id: str):
    """SSE endpoint — streams tokens for the given stream_id."""
    if stream_id not in _streams:
        return JSONResponse({"error": "stream not found"}, status_code=404)

    async def generate() -> AsyncIterator[str]:
        q = _streams[stream_id]
        full_text = []
        start = time.time()
        try:
            while True:
                token = await asyncio.wait_for(q.get(), timeout=60.0)
                if token is None:
                    elapsed = time.time() - start
                    tps = len(full_text) / elapsed if elapsed > 0 else 0
                    yield f"data: {json.dumps({'done': True, 'elapsed': round(elapsed, 2), 'tps': round(tps, 1)})}\n\n"
                    break
                full_text.append(token)
                yield f"data: {json.dumps({'token': token})}\n\n"
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'done': True, 'error': 'timeout'})}\n\n"
        finally:
            _streams.pop(stream_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/execute")
async def api_execute(req: Request):
    """Execute an action via ILUMINATY. Called when model outputs a JSON action."""
    import urllib.request as ureq
    data = await req.json()
    action = data.get("action", {})
    act = action.get("action", "")

    headers_bytes = {"Content-Type": "application/json"}

    def post(path, body):
        raw = json.dumps(body).encode()
        r = ureq.Request(f"{ILUMINATY_URL}{path}", data=raw,
                         headers=headers_bytes, method="POST")
        with ureq.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read())

    try:
        if act == "wait":
            await asyncio.sleep(min(int(action.get("ms", 500)), 5000) / 1000)
            return JSONResponse({"success": True})
        if act == "type_text":
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: post("/action/type", {"text": action.get("text", "")}))
        elif act == "hotkey":
            import urllib.parse
            k = urllib.parse.quote(str(action.get("keys", "")))
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: post(f"/action/hotkey?keys={k}", {}))
        elif act == "click":
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: post("/action/click", {"x": action.get("x", 0), "y": action.get("y", 0)}))
        elif act == "scroll":
            d = action.get("direction", "down")
            a = int(action.get("amount", 3))
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: post("/action/scroll", {"amount": a if d == "up" else -a}))
        elif act == "run_command":
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: post("/terminal/exec", {"command": action.get("cmd", "")}))
        elif act == "browser_navigate":
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: post("/browser/navigate", {"url": action.get("url", "")}))
        elif act in ("done", "ask"):
            return JSONResponse({"success": True, "action": act})
        else:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: post("/action/execute", {"instruction": json.dumps(action)}))
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/rating")
async def api_rating(req: Request):
    """Save a training sample (prompt + response + rating)."""
    data = await req.json()
    sample = {
        "timestamp": datetime.datetime.now().isoformat(),
        "model": _model_name,
        "user_msg": data.get("user_msg", ""),
        "response": data.get("response", ""),
        "rating": data.get("rating"),  # 1-5 or "up"/"down"
        "session": data.get("session", ""),
    }
    TRAINING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRAINING_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    return JSONResponse({"ok": True, "saved": str(TRAINING_FILE)})


@app.get("/api/status")
async def api_status():
    stats = _brain.status() if _brain else {}
    samples = 0
    if TRAINING_FILE.exists():
        with open(TRAINING_FILE) as f:
            samples = sum(1 for _ in f)
    world = await asyncio.get_event_loop().run_in_executor(None, _get_world)
    return JSONResponse({
        "model": _model_name,
        "backend": _backend,
        "loaded": _brain is not None,
        "training_samples": samples,
        "iluminaty_connected": world is not None,
        "screen_surface": world.get("active_surface") if world else None,
        "screen_phase": world.get("task_phase") if world else None,
        **stats,
    })


@app.get("/api/sessions")
async def api_sessions():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(f) as fh:
                d = json.load(fh)
            sessions.append({
                "name": d.get("name", f.stem),
                "messages": len(d.get("messages", [])),
                "created": d.get("created", ""),
            })
        except Exception:
            continue
    return JSONResponse(sessions[:20])


@app.post("/api/sessions/{name}")
async def api_save_session(name: str, req: Request):
    data = await req.json()
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{name}.json"
    payload = {
        "name": name,
        "model": _model_name,
        "mode": "chat",
        "created": datetime.datetime.now().isoformat(),
        "messages": data.get("messages", []),
        "message_count": len(data.get("messages", [])),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return JSONResponse({"ok": True})


@app.get("/api/sessions/{name}")
async def api_load_session(name: str):
    path = SESSIONS_DIR / f"{name}.json"
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    with open(path) as f:
        return JSONResponse(json.load(f))


# ─── Web UI ───────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IluminatyBrain</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0d1117;
    --bg2: #161b22;
    --bg3: #21262d;
    --border: #30363d;
    --text: #e6edf3;
    --text2: #8b949e;
    --accent: #58a6ff;
    --accent-btn: #1f6feb;
    --accent-hover: #388bfd;
    --user-bubble: #1a4a7a;
    --ai-bubble: #1c2128;
    --green: #3fb950;
    --red: #f85149;
    --yellow: #d29922;
    --radius: 12px;
  }

  html, body { height: 100%; overflow: hidden; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); display: flex; }

  /* ── Sidebar ── */
  .sidebar {
    width: 240px; min-width: 240px; background: var(--bg2);
    border-right: 1px solid var(--border);
    display: flex; flex-direction: column; height: 100%;
  }
  .sidebar-logo {
    padding: 18px 16px 12px;
    font-size: 15px; font-weight: 700; letter-spacing: .5px;
    color: var(--text); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 10px;
  }
  .sidebar-logo span.dot {
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--green); flex-shrink: 0;
  }
  .sidebar-logo span.dot.off { background: var(--red); }

  .sidebar-section { padding: 10px 12px 4px; font-size: 11px;
    color: var(--text2); text-transform: uppercase; letter-spacing: 1px; }

  .new-chat-btn {
    margin: 8px 12px; padding: 9px 14px;
    background: var(--accent-btn); border: none; border-radius: 8px;
    color: white; font-size: 14px; cursor: pointer; text-align: left;
    display: flex; align-items: center; gap: 8px; transition: background .15s;
  }
  .new-chat-btn:hover { background: var(--accent-hover); }

  .session-list { flex: 1; overflow-y: auto; padding: 4px 0; }
  .session-item {
    padding: 8px 16px; cursor: pointer; font-size: 13px;
    color: var(--text2); white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; transition: background .1s;
    display: flex; align-items: center; gap: 8px;
  }
  .session-item:hover { background: var(--bg3); color: var(--text); }
  .session-item.active { background: var(--bg3); color: var(--accent); }

  .sidebar-footer {
    padding: 12px 16px; border-top: 1px solid var(--border);
    font-size: 12px; color: var(--text2); line-height: 1.6;
  }
  .sidebar-footer .model-name { color: var(--accent); font-weight: 600; word-break: break-all; }
  .sidebar-footer .stat { margin-top: 2px; }

  /* ── Main area ── */
  .main { flex: 1; display: flex; flex-direction: column; height: 100%; min-width: 0; }

  .topbar {
    padding: 12px 24px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    background: var(--bg); flex-shrink: 0;
  }
  .topbar-title { font-size: 15px; font-weight: 600; color: var(--text2); }
  .topbar-actions { display: flex; gap: 8px; }
  .icon-btn {
    background: none; border: 1px solid var(--border); border-radius: 8px;
    color: var(--text2); padding: 6px 12px; cursor: pointer; font-size: 13px;
    transition: all .15s;
  }
  .icon-btn:hover { background: var(--bg3); color: var(--text); }

  /* ── Messages ── */
  .messages {
    flex: 1; overflow-y: auto; padding: 32px 0;
    scroll-behavior: smooth;
  }
  .message-row {
    display: flex; padding: 4px 24px; gap: 12px;
    max-width: 860px; margin: 0 auto; width: 100%;
  }
  .message-row.user { flex-direction: row-reverse; }

  .avatar {
    width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 13px; font-weight: 700;
  }
  .avatar.user { background: var(--accent-btn); color: white; }
  .avatar.ai   { background: #2d333b; color: var(--accent); font-size: 11px; }

  .bubble-wrap { display: flex; flex-direction: column; max-width: 72%; }
  .message-row.user .bubble-wrap { align-items: flex-end; }

  .bubble {
    padding: 12px 16px; border-radius: var(--radius); line-height: 1.65;
    font-size: 15px; word-break: break-word; white-space: pre-wrap;
  }
  .bubble.user { background: var(--user-bubble); border-radius: var(--radius) var(--radius) 4px var(--radius); }
  .bubble.ai   { background: var(--ai-bubble);   border-radius: var(--radius) var(--radius) var(--radius) 4px;
                 border: 1px solid var(--border); }

  .bubble-meta {
    font-size: 11px; color: var(--text2); margin-top: 5px;
    display: flex; align-items: center; gap: 8px;
  }

  /* Rating */
  .rating-btns { display: flex; gap: 4px; }
  .rate-btn {
    background: none; border: 1px solid var(--border); border-radius: 6px;
    padding: 3px 8px; cursor: pointer; color: var(--text2); font-size: 14px;
    transition: all .15s; line-height: 1;
  }
  .rate-btn:hover { background: var(--bg3); }
  .rate-btn.up.active   { background: rgba(63,185,80,.15); border-color: var(--green); color: var(--green); }
  .rate-btn.down.active { background: rgba(248,81,73,.15);  border-color: var(--red);   color: var(--red); }
  .rate-saved { font-size: 11px; color: var(--green); }

  /* Typing indicator */
  .typing {
    display: inline-flex; gap: 5px; align-items: center;
    padding: 14px 16px; background: var(--ai-bubble);
    border: 1px solid var(--border); border-radius: var(--radius) var(--radius) var(--radius) 4px;
  }
  .dot { width: 7px; height: 7px; background: var(--text2); border-radius: 50%; animation: pulse 1.4s ease-in-out infinite; }
  .dot:nth-child(2) { animation-delay: .2s; }
  .dot:nth-child(3) { animation-delay: .4s; }
  @keyframes pulse { 0%,80%,100%{opacity:.4; transform:scale(.8)} 40%{opacity:1; transform:scale(1)} }

  /* ── Input area ── */
  .input-area {
    padding: 16px 24px 20px; border-top: 1px solid var(--border);
    background: var(--bg); flex-shrink: 0;
  }
  .input-wrap {
    max-width: 860px; margin: 0 auto;
    background: var(--bg3); border: 1px solid var(--border); border-radius: 12px;
    display: flex; align-items: flex-end; gap: 8px; padding: 10px 12px;
    transition: border-color .15s;
  }
  .input-wrap:focus-within { border-color: var(--accent); }
  #user-input {
    flex: 1; background: none; border: none; outline: none;
    color: var(--text); font-size: 15px; font-family: inherit;
    resize: none; min-height: 24px; max-height: 200px; line-height: 1.5;
    overflow-y: auto;
  }
  #user-input::placeholder { color: var(--text2); }
  .send-btn {
    background: var(--accent-btn); border: none; border-radius: 8px;
    width: 36px; height: 36px; cursor: pointer; display: flex;
    align-items: center; justify-content: center; transition: background .15s; flex-shrink: 0;
  }
  .send-btn:hover:not(:disabled) { background: var(--accent-hover); }
  .send-btn:disabled { background: var(--bg3); cursor: not-allowed; }
  .send-btn svg { width: 16px; height: 16px; fill: white; }
  .send-btn:disabled svg { fill: var(--text2); }
  .input-hint {
    font-size: 11px; color: var(--text2); text-align: center; margin-top: 8px;
    max-width: 860px; margin-left: auto; margin-right: auto;
  }

  /* ── Empty state ── */
  .empty {
    flex: 1; display: flex; flex-direction: column; align-items: center;
    justify-content: center; text-align: center; padding: 24px;
    color: var(--text2);
  }
  .empty-logo {
    font-size: 48px; margin-bottom: 16px;
    background: linear-gradient(135deg, var(--accent), #a371f7);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    font-weight: 900; letter-spacing: -2px;
  }
  .empty h2 { font-size: 22px; color: var(--text); margin-bottom: 8px; }
  .empty p  { font-size: 14px; max-width: 380px; line-height: 1.6; }

  /* Toast */
  .toast {
    position: fixed; bottom: 24px; right: 24px;
    background: var(--bg3); border: 1px solid var(--border);
    border-radius: 8px; padding: 10px 16px; font-size: 13px;
    opacity: 0; transition: opacity .25s; pointer-events: none; z-index: 100;
  }
  .toast.show { opacity: 1; }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>

<!-- Sidebar -->
<aside class="sidebar">
  <div class="sidebar-logo">
    <span class="dot" id="status-dot"></span>
    IluminatyBrain
  </div>

  <button class="new-chat-btn" onclick="newChat()">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 2a1 1 0 011 1v4h4a1 1 0 010 2H9v4a1 1 0 01-2 0V9H3a1 1 0 010-2h4V3a1 1 0 011-1z"/>
    </svg>
    Nueva conversación
  </button>

  <div class="sidebar-section">Conversaciones</div>
  <div class="session-list" id="session-list"></div>

  <div class="sidebar-footer">
    <div class="model-name" id="footer-model">Cargando...</div>
    <div class="stat" id="footer-backend"></div>
    <div class="stat" id="footer-ipa" style="margin-top:6px;padding-top:6px;border-top:1px solid var(--border)"></div>
    <div class="stat" id="footer-screen"></div>
    <div class="stat" id="footer-samples" style="margin-top:4px"></div>
  </div>
</aside>

<!-- Main -->
<div class="main">
  <div class="topbar">
    <span class="topbar-title" id="chat-title">Nueva conversación</span>
    <div class="topbar-actions">
      <button class="icon-btn" id="agent-toggle" onclick="toggleAgentMode()" title="Modo agente: el modelo ejecuta acciones en tu PC">
        🤖 Agente: <span id="agent-label">OFF</span>
      </button>
      <button class="icon-btn" onclick="saveSession()" title="Guardar sesión">💾 Guardar</button>
      <button class="icon-btn" onclick="clearChat()" title="Limpiar chat">🗑 Limpiar</button>
    </div>
  </div>

  <!-- Messages or empty state -->
  <div id="messages-container" class="messages" style="display:none"></div>
  <div id="empty-state" class="empty">
    <div class="empty-logo">IB</div>
    <h2>IluminatyBrain</h2>
    <p>Tu modelo de IA local. Corre directo en tu GPU — sin Ollama, sin internet.</p>
  </div>

  <!-- Input -->
  <div class="input-area">
    <div class="input-wrap">
      <textarea
        id="user-input"
        placeholder="Escribí tu mensaje..."
        rows="1"
        onkeydown="handleKey(event)"
        oninput="autoResize(this)"
      ></textarea>
      <button class="send-btn" id="send-btn" onclick="sendMessage()" title="Enviar (Enter)">
        <svg viewBox="0 0 16 16"><path d="M.5 1.163A1 1 0 011.97.28l12.868 6.837a1 1 0 010 1.766L1.969 15.72A1 1 0 01.5 14.836V10.33a1 1 0 01.816-.983L8.5 8 1.316 6.653A1 1 0 01.5 5.67V1.163z"/></svg>
      </button>
    </div>
    <div class="input-hint">Enter = enviar &nbsp;·&nbsp; Shift+Enter = nueva línea &nbsp;·&nbsp; 👍👎 para entrenar el modelo</div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
let messages = [];   // [{role, content}]
let sessionName = genSessionName();
let isStreaming = false;
let agentMode = false;  // when true, JSON actions in responses get executed

function genSessionName() {
  const now = new Date();
  return `brain_${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}_${String(now.getHours()).padStart(2,'0')}${String(now.getMinutes()).padStart(2,'0')}${String(now.getSeconds()).padStart(2,'0')}`;
}

// ── UI helpers ────────────────────────────────────────────────────────────
function showToast(msg, duration=2500) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), duration);
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function scrollToBottom() {
  const c = document.getElementById('messages-container');
  c.scrollTop = c.scrollHeight;
}

function setStreaming(v) {
  isStreaming = v;
  document.getElementById('send-btn').disabled = v;
  document.getElementById('user-input').disabled = v;
}

// ── Render messages ────────────────────────────────────────────────────────
function renderMessage(msg, idx) {
  const row = document.createElement('div');
  row.className = `message-row ${msg.role}`;
  row.dataset.idx = idx;

  const initials = msg.role === 'user' ? 'Tú' : 'IB';
  const avatarClass = msg.role === 'user' ? 'user' : 'ai';

  let ratingHtml = '';
  if (msg.role === 'assistant') {
    const savedUp   = msg.ratingUp   ? 'active' : '';
    const savedDown = msg.ratingDown ? 'active' : '';
    const savedTxt  = msg.ratingSaved ? `<span class="rate-saved">✓ guardado</span>` : '';
    ratingHtml = `
      <div class="rating-btns">
        <button class="rate-btn up ${savedUp}"   onclick="rate(${idx}, 'up')"   title="Buena respuesta">👍</button>
        <button class="rate-btn down ${savedDown}" onclick="rate(${idx}, 'down')" title="Mala respuesta">👎</button>
        ${savedTxt}
      </div>`;
  }

  const meta = msg.role === 'assistant'
    ? `<div class="bubble-meta">
        ${msg.elapsed ? `<span>${msg.elapsed}s</span>` : ''}
        ${msg.tps     ? `<span>· ${msg.tps} tok/s</span>` : ''}
        ${ratingHtml}
       </div>`
    : '';

  row.innerHTML = `
    <div class="avatar ${avatarClass}">${initials}</div>
    <div class="bubble-wrap">
      <div class="bubble ${msg.role}" id="bubble-${idx}">${escHtml(msg.content)}</div>
      ${meta}
    </div>`;
  return row;
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderAll() {
  const c = document.getElementById('messages-container');
  c.innerHTML = '';
  if (messages.length === 0) {
    c.style.display = 'none';
    document.getElementById('empty-state').style.display = 'flex';
    return;
  }
  c.style.display = 'block';
  document.getElementById('empty-state').style.display = 'none';
  messages.forEach((m, i) => c.appendChild(renderMessage(m, i)));
  scrollToBottom();
}

// ── Send message ────────────────────────────────────────────────────────────
async function sendMessage() {
  if (isStreaming) return;
  const input = document.getElementById('user-input');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  input.style.height = 'auto';

  // Add user message
  messages.push({ role: 'user', content: text });
  renderAll();

  // Add AI typing placeholder
  const aiIdx = messages.length;
  const c = document.getElementById('messages-container');
  const typingRow = document.createElement('div');
  typingRow.className = 'message-row assistant';
  typingRow.id = 'typing-row';
  typingRow.innerHTML = `
    <div class="avatar ai">IB</div>
    <div class="bubble-wrap">
      <div class="typing"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>
    </div>`;
  c.appendChild(typingRow);
  scrollToBottom();
  setStreaming(true);

  try {
    // Start inference
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: messages.slice(0, -1).concat({role:'user',content:text}), max_tokens: 512 }),
    });
    const { stream_id, error, iluminaty_connected } = await res.json();
    if (error) throw new Error(error);
    if (!iluminaty_connected) {
      showToast('⚠ Sin pantalla — iniciá ILUMINATY para contexto completo', 3500);
    }

    // Stream tokens via SSE
    const evtSource = new EventSource(`/api/stream/${stream_id}`);
    let responseText = '';
    let elapsed = null, tps = null;

    // Replace typing with empty bubble
    typingRow.remove();
    const aiMsg = { role: 'assistant', content: '' };
    messages.push(aiMsg);
    const row = renderMessage(aiMsg, aiIdx);
    c.appendChild(row);
    const bubble = document.getElementById(`bubble-${aiIdx}`);

    evtSource.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.done) {
        elapsed = data.elapsed;
        tps = data.tps;
        evtSource.close();

        // Update message object
        messages[aiIdx].content = responseText;
        messages[aiIdx].elapsed = elapsed;
        messages[aiIdx].tps = tps;

        // Agent mode: try to execute any JSON action in the response
        if (agentMode) {
          const action = tryExtractAction(responseText);
          if (action) {
            messages[aiIdx].executedAction = action;
            bubble.innerHTML = escHtml(responseText) +
              `<div style="margin-top:8px;padding:8px;background:rgba(63,185,80,.1);border:1px solid var(--green);border-radius:8px;font-size:12px;font-family:monospace;color:var(--green)">` +
              `⚡ Ejecutando: ${JSON.stringify(action)}</div>`;
            executeAction(action).then(result => {
              const ok = result.success !== false;
              const statusColor = ok ? 'var(--green)' : 'var(--red)';
              const statusIcon = ok ? '✓' : '✗';
              bubble.querySelector('div').innerHTML =
                `⚡ <span style="color:${statusColor}">${statusIcon}</span> ${JSON.stringify(action)}` +
                (result.error ? ` — <span style="color:var(--red)">${result.error}</span>` : '');
            });
          }
        }

        // Re-render to add rating buttons
        row.remove();
        c.appendChild(renderMessage(messages[aiIdx], aiIdx));
        scrollToBottom();
        setStreaming(false);
        return;
      }
      if (data.token) {
        responseText += data.token;
        bubble.textContent = responseText;
        scrollToBottom();
      }
    };

    evtSource.onerror = () => {
      evtSource.close();
      if (!messages[aiIdx] || !messages[aiIdx].content) {
        messages[aiIdx] = { role: 'assistant', content: '[Error de conexión]' };
      }
      renderAll();
      setStreaming(false);
    };

  } catch (err) {
    typingRow.remove();
    messages.push({ role: 'assistant', content: `[Error: ${err.message}]` });
    renderAll();
    setStreaming(false);
  }
}

// ── Rating ────────────────────────────────────────────────────────────────
async function rate(idx, type) {
  const msg = messages[idx];
  if (!msg || msg.role !== 'assistant') return;

  // Find user message before this
  const userMsg = messages.slice(0, idx).reverse().find(m => m.role === 'user');

  // Toggle
  if (type === 'up') {
    msg.ratingUp = !msg.ratingUp;
    msg.ratingDown = false;
  } else {
    msg.ratingDown = !msg.ratingDown;
    msg.ratingUp = false;
  }

  const rating = msg.ratingUp ? 5 : msg.ratingDown ? 1 : null;

  if (rating !== null) {
    await fetch('/api/rating', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_msg: userMsg?.content || '',
        response: msg.content,
        rating,
        session: sessionName,
      }),
    });
    msg.ratingSaved = true;
    showToast(rating === 5 ? '👍 Respuesta positiva guardada' : '👎 Respuesta negativa guardada');
    updateStats();
  } else {
    msg.ratingSaved = false;
  }

  // Re-render just this row
  const c = document.getElementById('messages-container');
  const existing = c.querySelector(`[data-idx="${idx}"]`);
  if (existing) {
    const newRow = renderMessage(msg, idx);
    existing.replaceWith(newRow);
  }
}

// ── Session management ────────────────────────────────────────────────────
async function saveSession() {
  await fetch(`/api/sessions/${sessionName}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  });
  showToast(`💾 Sesión guardada: ${sessionName}`);
  loadSessionList();
}

async function loadSession(name) {
  const res = await fetch(`/api/sessions/${name}`);
  const data = await res.json();
  if (data.error) { showToast('Error al cargar sesión'); return; }
  messages = data.messages || [];
  sessionName = name;
  document.getElementById('chat-title').textContent = name;
  renderAll();
  highlightActiveSession(name);
}

function newChat() {
  messages = [];
  sessionName = genSessionName();
  document.getElementById('chat-title').textContent = 'Nueva conversación';
  renderAll();
  highlightActiveSession(null);
  document.getElementById('user-input').focus();
}

function clearChat() {
  messages = [];
  renderAll();
}

function highlightActiveSession(name) {
  document.querySelectorAll('.session-item').forEach(el => {
    el.classList.toggle('active', el.dataset.name === name);
  });
}

async function loadSessionList() {
  const res = await fetch('/api/sessions');
  const sessions = await res.json();
  const list = document.getElementById('session-list');
  list.innerHTML = '';
  sessions.forEach(s => {
    const div = document.createElement('div');
    div.className = 'session-item';
    div.dataset.name = s.name;
    div.title = s.name;
    div.textContent = s.name.replace(/^brain_/, '').replace(/_/g, ' ');
    div.onclick = () => loadSession(s.name);
    list.appendChild(div);
  });
  highlightActiveSession(sessionName);
}

// ── Status ────────────────────────────────────────────────────────────────
async function updateStats() {
  try {
    const res = await fetch('/api/status');
    const s = await res.json();
    const dot = document.getElementById('status-dot');
    dot.className = `dot ${s.loaded ? '' : 'off'}`;
    document.getElementById('footer-model').textContent = s.model || '?';
    document.getElementById('footer-backend').textContent = `Backend: ${s.backend || '?'}`;

    const ipaEl = document.getElementById('footer-ipa');
    const screenEl = document.getElementById('footer-screen');
    if (s.iluminaty_connected) {
      ipaEl.innerHTML = `<span style="color:var(--green)">● IPA conectado</span>`;
      screenEl.textContent = `App: ${s.screen_surface || '?'} · ${s.screen_phase || '?'}`;
    } else {
      ipaEl.innerHTML = `<span style="color:var(--text2)">○ IPA offline</span> <span style="font-size:10px;color:var(--text2)">(sin pantalla)</span>`;
      screenEl.textContent = 'Iniciá ILUMINATY para ver la pantalla';
    }

    document.getElementById('footer-samples').textContent = `Training: ${s.training_samples || 0} muestras`;
  } catch {}
}

// ── Agent mode ────────────────────────────────────────────────────────────
function toggleAgentMode() {
  agentMode = !agentMode;
  const btn = document.getElementById('agent-toggle');
  const lbl = document.getElementById('agent-label');
  lbl.textContent = agentMode ? 'ON' : 'OFF';
  btn.style.borderColor = agentMode ? 'var(--green)' : '';
  btn.style.color = agentMode ? 'var(--green)' : '';
  showToast(agentMode
    ? '🤖 Modo agente ON — el modelo puede ejecutar acciones'
    : '⏹ Modo agente OFF');
}

function tryExtractAction(text) {
  // Find first JSON object in text that looks like an action
  const match = text.match(/\{(?:[^{}]|\{[^{}]*\})*\}/);
  if (!match) return null;
  try {
    const obj = JSON.parse(match[0]);
    if (obj.action) return obj;
  } catch {}
  return null;
}

async function executeAction(action) {
  const res = await fetch('/api/execute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
  return await res.json();
}

// ── Init ────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadSessionList();
  updateStats();
  setInterval(updateStats, 10000);
  document.getElementById('user-input').focus();
});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTMLResponse(_HTML)


# ─── Server entry point ────────────────────────────────────────────────────

def main():
    global _brain, _model_name, _backend

    parser = argparse.ArgumentParser(
        description="IluminatyBrain Web Server — chat UI en http://localhost:8421"
    )
    parser.add_argument(
        "--model", default="Qwen/Qwen2.5-3B-Instruct",
        help="HuggingFace model ID (default: Qwen/Qwen2.5-3B-Instruct)",
    )
    parser.add_argument(
        "--4bit", action="store_true", dest="load_4bit",
        help="Cargar en INT4 (menos VRAM, recomendado para GPUs < 8GB)",
    )
    parser.add_argument(
        "--gguf", default=None, metavar="PATH",
        help="Cargar desde GGUF local",
    )
    parser.add_argument(
        "--checkpoint", default=None,
        help="Cargar checkpoint fine-tuneado",
    )
    parser.add_argument(
        "--port", type=int, default=8421,
        help="Puerto (default: 8421)",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="No abrir browser automaticamente",
    )
    args = parser.parse_args()

    from iluminaty.brain_engine import BrainEngine

    if args.gguf:
        _model_name = pathlib.Path(args.gguf).name
        print(f"[IluminatyBrain] Cargando GGUF: {args.gguf}")
        _brain = BrainEngine.from_gguf(args.gguf)
    elif args.checkpoint:
        _model_name = args.checkpoint
        print(f"[IluminatyBrain] Cargando checkpoint: {args.checkpoint}")
        _brain = BrainEngine.from_checkpoint(args.checkpoint)
    else:
        _model_name = args.model
        print(f"[IluminatyBrain] Cargando {args.model} desde HuggingFace...")
        if args.load_4bit:
            print("[IluminatyBrain] (INT4 — primera vez descarga el modelo, luego queda cacheado)")
        _brain = BrainEngine.from_huggingface(args.model, load_in_4bit=args.load_4bit)

    _backend = _brain._backend
    print(f"[IluminatyBrain] Modelo listo — backend={_backend}")
    print(f"[IluminatyBrain] Abriendo http://localhost:{args.port}")

    if not args.no_browser:
        import threading, webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
