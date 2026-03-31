"""
ILUMINATY - API Server
=======================
Servidor local que expone el ring buffer visual a cualquier IA.

Endpoints:
  GET  /frame/latest        → último frame (JPEG)
  GET  /frame/latest?base64  → último frame en base64 (para APIs de IA)
  GET  /frames?last=5        → últimos N frames
  GET  /frames?seconds=10    → frames de los últimos N segundos
  GET  /buffer/stats         → estadísticas del buffer
  POST /config               → cambiar configuración en caliente
  WS   /ws/stream            → WebSocket stream de frames en vivo
  GET  /health               → health check

Headers de seguridad:
  X-API-Key: requerido si se configura una key
"""

import asyncio
import base64
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Header, HTTPException
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .ring_buffer import RingBuffer, FrameSlot
from .capture import ScreenCapture, CaptureConfig
from .vision import VisionIntelligence, Annotation
from .dashboard import DASHBOARD_HTML
from .smart_diff import SmartDiff
from .audio import AudioRingBuffer, AudioCapture, TranscriptionEngine
from .context import ContextEngine
from .plugin_system import PluginManager
from .monitors import MonitorManager
from .memory import TemporalMemory
from .watchdog import Watchdog
from .router import AIRouter
from .collab import CollaborativeManager

# v1.0 imports: Computer Use capas
from .actions import ActionBridge
from .windows import WindowManager
from .clipboard import ClipboardManager
from .process_mgr import ProcessManager
from .ui_tree import UITree
from .vscode import VSCodeBridge
from .terminal import TerminalManager
from .git_ops import GitOps
from .browser import BrowserBridge
from .filesystem import FileSystemSandbox
from .resolver import ActionResolver
from .intent import IntentClassifier, Intent
from .planner import TaskPlanner
from .verifier import ActionVerifier
from .recovery import ErrorRecovery
from .safety import SafetySystem
from .autonomy import AutonomyManager, AutonomyLevel
from .audit import AuditLog
from .licensing import LicenseManager, get_license, init_license

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

logger = logging.getLogger(__name__)


# ─── Server State (BUG-005 fix: single state object with lock) ───
import threading as _threading

class _ServerState:
    """Thread-safe server state container."""
    def __init__(self):
        self.lock = _threading.Lock()
        # Core (v0.5)
        self.buffer: Optional[RingBuffer] = None
        self.capture: Optional[ScreenCapture] = None
        self.api_key: Optional[str] = None
        self.vision: Optional[VisionIntelligence] = None
        self.diff: Optional[SmartDiff] = None
        self.audio_buffer: Optional[AudioRingBuffer] = None
        self.audio_capture: Optional[AudioCapture] = None
        self.transcriber: Optional[TranscriptionEngine] = None
        self.context: Optional[ContextEngine] = None
        self.plugin_mgr: Optional[PluginManager] = None
        self.monitor_mgr: Optional[MonitorManager] = None
        self.memory: Optional[TemporalMemory] = None
        self.watchdog: Optional[Watchdog] = None
        self.router: Optional[AIRouter] = None
        self.collab: Optional[CollaborativeManager] = None
        self.ws_clients: set = set()
        # v1.0: Computer Use capas
        self.actions: Optional[ActionBridge] = None
        self.windows: Optional[WindowManager] = None
        self.clipboard: Optional[ClipboardManager] = None
        self.process_mgr: Optional[ProcessManager] = None
        self.ui_tree: Optional[UITree] = None
        self.vscode: Optional[VSCodeBridge] = None
        self.terminal: Optional[TerminalManager] = None
        self.git_ops: Optional[GitOps] = None
        self.browser: Optional[BrowserBridge] = None
        self.filesystem: Optional[FileSystemSandbox] = None
        self.resolver: Optional[ActionResolver] = None
        self.intent: Optional[IntentClassifier] = None
        self.planner: Optional[TaskPlanner] = None
        self.verifier: Optional[ActionVerifier] = None
        self.recovery: Optional[ErrorRecovery] = None
        self.safety: Optional[SafetySystem] = None
        self.autonomy: Optional[AutonomyManager] = None
        self.audit: Optional[AuditLog] = None
        self.license: Optional[LicenseManager] = None
        self.perception = None  # PerceptionEngine (lazy import)
        self.agent_coordinator = None  # IPA v2: Multi-Agent Workbench
        self.operating_mode: str = "SAFE"  # SAFE | RAW | HYBRID
        self.bootstrap_warnings: list[str] = []

_state = _ServerState()

# Aliases for backward compat in endpoint functions
def _get_buffer(): return _state.buffer
def _get_capture(): return _state.capture
def _get_vision(): return _state.vision
def _get_diff(): return _state.diff


def _frame_to_json(slot: FrameSlot, include_base64: bool = False) -> dict:
    """Serializa un FrameSlot a JSON. Los bytes solo van si se pide base64."""
    result = {
        "timestamp": slot.timestamp,
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(slot.timestamp)),
        "width": slot.width,
        "height": slot.height,
        "size_bytes": len(slot.frame_bytes),
        "format": slot.mime_type,
        "change_score": slot.change_score,
        "region": slot.region,
    }
    if include_base64:
        result["image_base64"] = base64.b64encode(slot.frame_bytes).decode("ascii")
        result["image_url"] = f"data:{slot.mime_type};base64,{result['image_base64']}"
    return result


def _check_auth(api_key: Optional[str]):
    if _state.api_key and api_key != _state.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _normalize_operating_mode(mode: Optional[str]) -> str:
    value = (mode or "SAFE").strip().upper()
    if value not in {"SAFE", "RAW", "HYBRID"}:
        value = "SAFE"
    return value


def _mode_requires_safety(mode: str, category: str) -> bool:
    mode_norm = _normalize_operating_mode(mode)
    cat = (category or "normal").lower()
    if mode_norm == "RAW":
        return False
    if mode_norm == "HYBRID":
        return cat == "destructive"
    return True


def _intent_from_payload(payload: dict) -> Intent:
    if _state.intent is None:
        raise HTTPException(status_code=503, detail="Brain not initialized")
    instruction = (payload.get("instruction") or "").strip()
    if instruction:
        return _state.intent.classify_or_default(instruction)

    action = (payload.get("action") or "").strip()
    if not action:
        raise HTTPException(status_code=400, detail="Provide 'instruction' or 'action'")
    params = payload.get("params") or {}
    category = (payload.get("category") or "normal").strip().lower()
    if category not in {"safe", "normal", "destructive", "system"}:
        category = "normal"
    return Intent(
        action=action,
        params=params,
        confidence=1.0,
        raw_input=instruction or action,
        category=category,
    )


def _build_precheck(
    intent: Intent,
    mode: str,
    include_readiness: bool = True,
    context_tick_id: Optional[int] = None,
    max_staleness_ms: int = 1500,
) -> dict:
    mode_norm = _normalize_operating_mode(mode)
    readiness = {
        "readiness": False,
        "uncertainty": 1.0,
        "reasons": ["perception_unavailable"],
        "task_phase": "unknown",
        "active_surface": "unknown",
        "risk_mode": mode_norm.lower(),
    }
    if include_readiness and _state.perception:
        try:
            readiness = _state.perception.get_readiness()
        except Exception as e:
            logger.debug("Failed to read perception readiness during precheck: %s", e)

    kill_check = {"allowed": True, "reason": "ok"}
    if _state.safety and _state.safety.is_killed:
        kill_check = {"allowed": False, "reason": "killed"}

    safety_applies = _mode_requires_safety(mode_norm, intent.category)
    safety_check = {"allowed": True, "reason": "skipped"}
    if kill_check["allowed"] and safety_applies and _state.safety:
        safety_check = _state.safety.check_action(intent.action, intent.category)

    readiness_applies = include_readiness and safety_applies and mode_norm != "RAW"
    readiness_check = {"allowed": True, "reason": "skipped"}
    if readiness_applies:
        if readiness.get("readiness"):
            readiness_check = {"allowed": True, "reason": "ready"}
        else:
            reasons = readiness.get("reasons") or ["insufficient_context"]
            reason_txt = ", ".join(str(r) for r in reasons[:3])
            readiness_check = {"allowed": False, "reason": reason_txt}

    context_applies = include_readiness and safety_applies and mode_norm != "RAW"
    context_check = {"allowed": True, "reason": "skipped", "latest_tick_id": readiness.get("tick_id"), "staleness_ms": readiness.get("staleness_ms", 0)}
    if context_applies and _state.perception:
        try:
            if hasattr(_state.perception, "check_context_freshness"):
                context_check = _state.perception.check_context_freshness(
                    context_tick_id=context_tick_id,
                    max_staleness_ms=max_staleness_ms,
                )
            else:
                staleness = int(readiness.get("staleness_ms", 0))
                latest_tick = readiness.get("tick_id")
                if staleness > int(max_staleness_ms):
                    context_check = {
                        "allowed": False,
                        "reason": "context_stale",
                        "latest_tick_id": latest_tick,
                        "staleness_ms": staleness,
                    }
                else:
                    context_check = {
                        "allowed": True,
                        "reason": "fresh",
                        "latest_tick_id": latest_tick,
                        "staleness_ms": staleness,
                    }
        except Exception as e:
            logger.debug("Failed context freshness check during precheck: %s", e)
            context_check = {"allowed": True, "reason": "context_check_unavailable", "latest_tick_id": readiness.get("tick_id"), "staleness_ms": readiness.get("staleness_ms", 0)}

    blocked = (
        not kill_check["allowed"]
        or not safety_check["allowed"]
        or not readiness_check["allowed"]
        or not context_check["allowed"]
    )
    return {
        "mode": mode_norm,
        "intent": intent.to_dict(),
        "readiness": readiness,
        "kill_check": kill_check,
        "safety_applies": safety_applies,
        "safety_check": safety_check,
        "readiness_applies": readiness_applies,
        "readiness_check": readiness_check,
        "context_applies": context_applies,
        "context_tick_id": context_tick_id,
        "max_staleness_ms": int(max_staleness_ms),
        "context_check": context_check,
        "blocked": blocked,
    }


def _execute_intent(
    intent: Intent,
    mode: str,
    verify: bool = True,
    context_tick_id: Optional[int] = None,
    max_staleness_ms: int = 1500,
) -> dict:
    if not _state.resolver:
        raise HTTPException(status_code=503, detail="Resolver not initialized")

    precheck = _build_precheck(
        intent,
        mode,
        include_readiness=True,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
    )
    if precheck["blocked"]:
        blocked_reason = (
            precheck["kill_check"]["reason"] if not precheck["kill_check"]["allowed"] else
            precheck["safety_check"]["reason"] if not precheck["safety_check"]["allowed"] else
            precheck.get("readiness_check", {}).get("reason") if not precheck.get("readiness_check", {}).get("allowed", True) else
            precheck.get("context_check", {}).get("reason", "stale_context")
        )
        if _state.audit:
            _state.audit.log(
                intent.action,
                intent.category,
                intent.params,
                "blocked",
                blocked_reason,
                _state.autonomy.current_level if _state.autonomy else "unknown",
            )
        return {
            "precheck": precheck,
            "intent": intent.to_dict(),
            "result": {
                "action": intent.action,
                "success": False,
                "message": blocked_reason,
                "method_used": "blocked",
                "attempts": [],
                "total_ms": 0.0,
            },
            "verification": None,
            "recovery": None,
        }

    pre_state = _state.verifier.capture_pre_state(intent.action, intent.params) if (_state.verifier and verify) else None
    result = _state.resolver.resolve(intent.action, intent.params)

    verification = None
    if verify and _state.verifier and result.success:
        verification = _state.verifier.verify(intent.action, intent.params, pre_state)

    recovery = None
    if not result.success and _state.recovery:
        recovery = _state.recovery.recover(intent.action, intent.params, result.message)
        if recovery.recovered:
            result = _state.resolver.resolve(intent.action, intent.params)
            if verify and _state.verifier and result.success:
                verification = _state.verifier.verify(intent.action, intent.params, pre_state)

    if _state.audit:
        _state.audit.log(
            intent.action,
            intent.category,
            intent.params,
            "success" if result.success else "failed",
            result.message,
            _state.autonomy.current_level if _state.autonomy else "unknown",
            duration_ms=result.total_ms,
        )

    if _state.perception:
        try:
            _state.perception.record_action_feedback(
                action=intent.action,
                success=result.success,
                message=result.message,
            )
        except Exception as e:
            logger.debug("Failed to record action feedback into perception trace: %s", e)

    return {
        "precheck": precheck,
        "intent": intent.to_dict(),
        "result": result.to_dict(),
        "verification": verification.to_dict() if verification else None,
        "recovery": recovery.to_dict() if recovery else None,
    }


# ─── Lifespan ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ya se inicializa desde main.py
    yield
    # Cleanup
    if _state.capture and _state.capture.is_running:
        _state.capture.stop()
    if _state.buffer:
        _state.buffer.flush()


# ─── App ───
app = FastAPI(
    title="ILUMINATY",
    description="Real-time visual perception for AI. Zero-disk, RAM-only.",
    version="1.0.0",
    lifespan=lifespan,
)

def _get_cors_origins() -> list[str]:
    """Build CORS origins list including configured host:port."""
    origins = ["http://127.0.0.1:8420", "http://localhost:8420", "tauri://localhost"]
    # Add dynamically configured host:port if different
    if _state.capture and hasattr(_state.capture, 'config'):
        cfg = _state.capture.config
        host = getattr(cfg, 'host', '127.0.0.1')
        port = getattr(cfg, 'port', 8420)
        dynamic = f"http://{host}:{port}"
        if dynamic not in origins:
            origins.append(dynamic)
    return origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── License Gate Middleware ───

class LicenseGateMiddleware(BaseHTTPMiddleware):
    """Blocks Pro-only endpoints for Free plan users."""
    async def dispatch(self, request: StarletteRequest, call_next):
        lic = get_license()
        if lic and not lic.is_endpoint_allowed(request.url.path):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "pro_required",
                    "message": "This endpoint requires ILUMINATY Pro ($29/mo).",
                    "endpoint": request.url.path,
                    "current_plan": lic.plan.value,
                    "upgrade_url": "https://iluminaty.dev/#pricing",
                },
            )
        return await call_next(request)

app.add_middleware(LicenseGateMiddleware)


# ─── Endpoints ───

@app.get("/health")
async def health():
    return {
        "status": "alive",
        "capture_running": _state.capture.is_running if _state.capture else False,
        "buffer_slots": _state.buffer.size if _state.buffer else 0,
    }


@app.get("/", response_class=Response)
async def dashboard(x_api_key: Optional[str] = Header(None), token: Optional[str] = Query(None)):
    """Dashboard web en vivo. Auth via header or ?token= query param."""
    # Allow dashboard access via query param too (for browser URL bar)
    if _state.api_key:
        provided = x_api_key or token
        if provided != _state.api_key:
            # Return a simple auth page instead of 401
            auth_html = '''<!DOCTYPE html>
<html><head><title>ILUMINATY</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a12;color:#e4e4ee;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh}
.box{text-align:center;max-width:360px}
h1{color:#00ff88;font-size:20px;letter-spacing:3px;margin-bottom:24px}
input{background:#12121e;border:1px solid #1a1a30;color:#e4e4ee;padding:10px 16px;border-radius:6px;width:100%;font-size:14px;margin-bottom:12px;outline:none}
input:focus{border-color:#00ff88}
button{background:transparent;border:1px solid #00ff88;color:#00ff88;padding:10px 32px;border-radius:6px;cursor:pointer;font-size:14px;width:100%}
button:hover{background:rgba(0,255,136,0.1)}
p{color:#555;font-size:12px;margin-top:16px}
</style></head><body>
<div class="box">
<h1>ILUMINATY</h1>
<form onsubmit="location.href='/?token='+document.getElementById('k').value;return false">
<input id="k" type="password" placeholder="Enter API key" autofocus/>
<button type="submit">ACCESS</button>
</form>
<p>Authentication required to access the dashboard.</p>
</div></body></html>'''
            return Response(content=auth_html, media_type="text/html")
    return Response(content=DASHBOARD_HTML, media_type="text/html")


@app.get("/buffer/stats")
async def buffer_stats(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.buffer:
        raise HTTPException(503, "Buffer not initialized")
    stats = _state.buffer.stats
    stats["capture_running"] = _state.capture.is_running if _state.capture else False
    stats["current_fps"] = _state.capture.current_fps if _state.capture else 0
    stats["ws_clients"] = len(_state.ws_clients)
    return stats


@app.get("/frame/latest")
async def frame_latest(
    base64_encode: bool = Query(False, alias="base64"),
    x_api_key: Optional[str] = Header(None),
):
    """Último frame. Sin base64 devuelve JPEG raw. Con base64 devuelve JSON."""
    _check_auth(x_api_key)
    if not _state.buffer:
        raise HTTPException(503, "Buffer not initialized")
    
    slot = _state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")
    
    if base64_encode:
        return JSONResponse(_frame_to_json(slot, include_base64=True))
    else:
        return Response(
            content=slot.frame_bytes,
            media_type=slot.mime_type,
            headers={
                "X-Timestamp": str(slot.timestamp),
                "X-Change-Score": str(slot.change_score),
                "X-Frame-Width": str(slot.width),
                "X-Frame-Height": str(slot.height),
                "X-Format": slot.mime_type,
            },
        )


@app.get("/frames")
async def frames(
    last: Optional[int] = Query(None, ge=1, le=100),
    seconds: Optional[float] = Query(None, ge=0.1, le=300),
    include_images: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    """
    Múltiples frames.
    - ?last=5 → últimos 5 frames
    - ?seconds=10 → frames de los últimos 10 segundos
    - ?include_images=true → incluye base64 (cuidado: puede ser grande)
    """
    _check_auth(x_api_key)
    if not _state.buffer:
        raise HTTPException(503, "Buffer not initialized")
    
    if seconds is not None:
        slots = _state.buffer.get_since(seconds)
    elif last is not None:
        slots = _state.buffer.get_latest_n(last)
    else:
        slots = _state.buffer.get_latest_n(5)
    
    return {
        "count": len(slots),
        "frames": [_frame_to_json(s, include_base64=include_images) for s in slots],
    }


@app.post("/config")
async def update_config(
    fps: Optional[float] = None,
    quality: Optional[int] = None,
    image_format: Optional[str] = None,
    max_width: Optional[int] = None,
    skip_unchanged: Optional[bool] = None,
    smart_quality: Optional[bool] = None,
    x_api_key: Optional[str] = Header(None),
):
    """Cambiar configuracion en caliente sin reiniciar."""
    _check_auth(x_api_key)
    if not _state.capture:
        raise HTTPException(503, "Capture not initialized")
    
    changed = []
    # BUG-006 fix: lock during config update
    with _state.lock:
        if fps is not None:
            _state.capture.config.fps = fps
            _state.capture._current_fps = fps
            changed.append(f"fps={fps}")
        if quality is not None:
            _state.capture.config.quality = max(10, min(95, quality))
            changed.append(f"quality={quality}")
        if image_format is not None and image_format in ("jpeg", "webp", "png"):
            _state.capture.config.image_format = image_format
            changed.append(f"image_format={image_format}")
        if max_width is not None:
            _state.capture.config.max_width = max(320, min(3840, max_width))
            changed.append(f"max_width={max_width}")
        if skip_unchanged is not None:
            _state.capture.config.skip_unchanged = skip_unchanged
            changed.append(f"skip_unchanged={skip_unchanged}")
        if smart_quality is not None:
            _state.capture.config.smart_quality = smart_quality
            changed.append(f"smart_quality={smart_quality}")
    
    return {"updated": changed}


@app.post("/buffer/flush")
async def flush_buffer(x_api_key: Optional[str] = Header(None)):
    """Destruir todo el contenido visual del buffer. Irreversible."""
    _check_auth(x_api_key)
    if _state.buffer:
        _state.buffer.flush()
    return {"status": "flushed", "slots": 0}


@app.post("/capture/start")
async def start_capture(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _state.capture and not _state.capture.is_running:
        _state.capture.start()
    return {"status": "running"}


@app.post("/capture/stop")
async def stop_capture(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _state.capture and _state.capture.is_running:
        _state.capture.stop()
    return {"status": "stopped"}


# ─── WebSocket Stream ───

@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket):
    """
    WebSocket que envía frames en real-time.
    Cada mensaje es JSON con base64 del frame.
    La IA se conecta aquí para "ver" en vivo.
    """
    await ws.accept()
    _state.ws_clients.add(ws)
    
    try:
        last_hash = None
        while True:
            if not _state.buffer:
                await asyncio.sleep(1)
                continue
                
            slot = _state.buffer.get_latest()
            if slot and slot.phash != last_hash:
                await ws.send_json(_frame_to_json(slot, include_base64=True))
                last_hash = slot.phash
            
            # Enviar al ritmo del FPS actual
            fps = _state.capture.current_fps if _state.capture else 1.0
            await asyncio.sleep(1.0 / fps)
            
    except WebSocketDisconnect:
        logger.debug("Client disconnected from /ws/stream")
    finally:
        _state.ws_clients.discard(ws)


@app.websocket("/perception/stream")
async def perception_stream(
    ws: WebSocket,
    interval_ms: int = Query(300, ge=100, le=2000),
    include_events: bool = Query(True),
):
    """
    IPA v2 semantic stream for external AI brains.
    Sends WorldState snapshots (+ optional recent events) in real time.
    """
    await ws.accept()
    try:
        last_world_ts = 0
        last_event_ts = 0.0
        last_visual_ts = int(time.time() * 1000)
        while True:
            if not _state.perception:
                await ws.send_json({
                    "type": "perception_world",
                    "status": "offline",
                    "timestamp_ms": int(time.time() * 1000),
                })
                await asyncio.sleep(interval_ms / 1000)
                continue

            world = _state.perception.get_world_state()
            world_ts = world.get("timestamp_ms", 0)
            if world_ts != last_world_ts:
                payload = {
                    "type": "perception_world",
                    "status": "active",
                    "tick_id": world.get("tick_id"),
                    "world": world,
                    "readiness": _state.perception.get_readiness(),
                }
                if include_events:
                    raw_events = _state.perception.get_events(last_seconds=3, min_importance=0.1)
                    events = []
                    for e in raw_events:
                        if e.timestamp <= last_event_ts:
                            continue
                        events.append({
                            "timestamp": e.timestamp,
                            "type": e.event_type,
                            "description": e.description,
                            "importance": e.importance,
                            "uncertainty": e.uncertainty,
                            "monitor": e.monitor,
                        })
                    if events:
                        last_event_ts = events[-1]["timestamp"]
                    payload["events"] = events
                visual_delta = _state.perception.get_visual_facts_delta(last_visual_ts)
                if visual_delta:
                    payload["visual_facts_delta"] = visual_delta
                    latest = max(int(v.get("timestamp_ms", last_visual_ts)) for v in visual_delta)
                    last_visual_ts = max(last_visual_ts, latest)
                await ws.send_json(payload)
                last_world_ts = world_ts

            await asyncio.sleep(interval_ms / 1000)
    except WebSocketDisconnect:
        logger.debug("Client disconnected from /perception/stream")


def init_server(
    buffer: RingBuffer,
    capture: ScreenCapture,
    api_key: Optional[str] = None,
    audio_buffer: Optional[AudioRingBuffer] = None,
    audio_capture: Optional[AudioCapture] = None,
    enable_actions: bool = False,
    autonomy_level: str = "suggest",
    browser_debug_port: int = 9222,
    file_sandbox_paths: Optional[list[str]] = None,
    iluminaty_key: Optional[str] = None,
    visual_profile: str = "core_ram",
    vision_plus_disk: bool = False,
    deep_loop_hz: float = 1.0,
    fast_loop_hz: float = 10.0,
):
    """Inyecta las dependencias al modulo del server (thread-safe)."""
    with _state.lock:
        _state.bootstrap_warnings = []
        # ─── License validation ───
        import asyncio
        _state.license = init_license(api_key=iluminaty_key)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_state.license.validate())
            else:
                loop.run_until_complete(_state.license.validate())
        except RuntimeError:
            asyncio.run(_state.license.validate())
        # ─── Core (v0.5) ───
        _state.buffer = buffer
        _state.capture = capture
        _state.api_key = api_key
        _state.vision = VisionIntelligence()
        _state.diff = SmartDiff(grid_cols=8, grid_rows=6)
        _state.audio_buffer = audio_buffer
        _state.audio_capture = audio_capture
        _state.transcriber = TranscriptionEngine()
        _state.context = ContextEngine()

        _state.plugin_mgr = PluginManager()
        _state.plugin_mgr.load_from_directory()
        _state.monitor_mgr = MonitorManager()
        _state.monitor_mgr.refresh()

        # Perception Engine — continuous vision processing (the AI's visual cortex)
        # Wired AFTER monitor_mgr/diff/context are initialized (IPA Phase 1.3)
        try:
            from .perception import PerceptionEngine
            _state.perception = PerceptionEngine(
                monitor_mgr=_state.monitor_mgr,
                smart_diff=_state.diff,
                context=_state.context,
                visual_profile=visual_profile,
                enable_disk_spool=vision_plus_disk,
                deep_loop_hz=deep_loop_hz,
                fast_loop_hz=fast_loop_hz,
            )
            _state.perception.start(buffer)
            _state.perception.set_risk_mode(_state.operating_mode.lower())
        except Exception as e:
            _state.perception = None
            _state.bootstrap_warnings.append(f"perception_init_failed: {e}")

        # IPA v2: Multi-Agent Workbench
        try:
            from .agents import AgentCoordinator
            _state.agent_coordinator = AgentCoordinator()
            if _state.perception:
                _state.perception._agent_coordinator = _state.agent_coordinator
        except Exception as e:
            _state.agent_coordinator = None
            _state.bootstrap_warnings.append(f"agent_coordinator_init_failed: {e}")

        _state.memory = TemporalMemory(enabled=False)
        _state.watchdog = Watchdog()
        _state.router = AIRouter()
        _state.collab = CollaborativeManager()

        # ─── v1.0: Computer Use capas ───

        # Capa 7: Safety (primero, todo pasa por aqui)
        _state.safety = SafetySystem()
        _state.autonomy = AutonomyManager()
        _state.autonomy.set_level(autonomy_level)
        _state.audit = AuditLog()

        # Capa 1: OS Control
        _state.actions = ActionBridge(enabled=enable_actions)
        _state.windows = WindowManager()
        _state.clipboard = ClipboardManager()
        _state.process_mgr = ProcessManager()

        # Capa 2: UI Intelligence
        _state.ui_tree = UITree()
        # Conectar UI Tree al ActionBridge
        if _state.ui_tree.available:
            _state.actions.set_ui_tree(_state.ui_tree)

        # Capa 3: App Control
        _state.vscode = VSCodeBridge()
        _state.terminal = TerminalManager()
        _state.git_ops = GitOps()

        # Capa 4: Web
        _state.browser = BrowserBridge(debug_port=browser_debug_port)

        # Capa 5: File System
        _state.filesystem = FileSystemSandbox(
            allowed_paths=file_sandbox_paths or ["."],
        )

        # Capa 6: Brain (conecta todas las capas)
        _state.resolver = ActionResolver()
        _state.resolver.set_layers(
            actions=_state.actions,
            ui_tree=_state.ui_tree,
            vscode=_state.vscode,
            browser=_state.browser,
            filesystem=_state.filesystem,
        )
        _state.intent = IntentClassifier()
        _state.planner = TaskPlanner()
        _state.verifier = ActionVerifier()
        _state.verifier.set_layers(
            filesystem=_state.filesystem,
            ui_tree=_state.ui_tree,
            browser=_state.browser,
            actions=_state.actions,
        )
        _state.recovery = ErrorRecovery()
        _state.recovery.set_resolver(_state.resolver)


# ─── Vision / AI-ready endpoints ───

@app.get("/vision/snapshot")
async def vision_snapshot(
    ocr: bool = Query(True),
    include_image: bool = Query(True),
    x_api_key: Optional[str] = Header(None),
):
    """
    Frame enriquecido listo para IA:
    - Imagen (base64)
    - OCR text extraido
    - Anotaciones del usuario
    - Ventana activa
    - Prompt estructurado en ingles

    Este es EL endpoint que cualquier IA consume.
    """
    _check_auth(x_api_key)
    if not _state.buffer or not _state.vision:
        raise HTTPException(503, "Not initialized")

    slot = _state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    enriched = _state.vision.enrich_frame(slot, run_ocr=ocr)
    return JSONResponse(enriched.to_dict(include_image=include_image))


@app.get("/vision/ocr")
async def vision_ocr(
    region_x: Optional[int] = Query(None),
    region_y: Optional[int] = Query(None),
    region_w: Optional[int] = Query(None),
    region_h: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """
    Solo OCR del frame actual.
    Opcionalmente de una region especifica: ?region_x=100&region_y=200&region_w=400&region_h=300
    """
    _check_auth(x_api_key)
    if not _state.buffer or not _state.vision:
        raise HTTPException(503, "Not initialized")

    slot = _state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    if all(v is not None for v in [region_x, region_y, region_w, region_h]):
        result = _state.vision.ocr.extract_region(slot.frame_bytes, region_x, region_y, region_w, region_h)
    else:
        result = _state.vision.ocr.extract_text(slot.frame_bytes)

    return result


@app.get("/vision/window")
async def vision_window(x_api_key: Optional[str] = Header(None)):
    """Info de la ventana activa actual."""
    _check_auth(x_api_key)
    from .vision import get_active_window_info
    return get_active_window_info()


@app.get("/vision/diff")
async def vision_diff(
    include_deltas: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    """
    Smart visual diff: que cambio y donde en la pantalla.
    Returns: changed regions, heatmap, change percentage.
    Con include_deltas=true incluye mini-images de las regiones cambiadas.
    """
    _check_auth(x_api_key)
    if not _state.buffer or not _state.diff:
        raise HTTPException(503, "Not initialized")

    slot = _state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    diff = _state.diff.compare(slot.frame_bytes)

    result = {
        "changed": diff.changed,
        "change_percentage": diff.change_percentage,
        "changed_cells": diff.changed_cells,
        "total_cells": diff.total_cells,
        "heatmap": diff.heatmap,
        "description": _state.diff.diff_to_description(diff, slot.width, slot.height),
        "regions": [
            {
                "grid": f"({r.grid_x},{r.grid_y})",
                "pixel_x": r.pixel_x,
                "pixel_y": r.pixel_y,
                "pixel_w": r.pixel_w,
                "pixel_h": r.pixel_h,
                "intensity": r.change_intensity,
            }
            for r in diff.changed_regions
        ],
    }

    if include_deltas and diff.changed_regions:
        result["deltas"] = _state.diff.get_delta_regions(slot.frame_bytes, diff)

    return result


# ─── Audio endpoints ───

@app.get("/audio/stats")
async def audio_stats(x_api_key: Optional[str] = Header(None)):
    """Stats del buffer de audio."""
    _check_auth(x_api_key)
    if not _state.audio_buffer:
        return {"status": "disabled", "mode": "off"}
    stats = _state.audio_buffer.stats
    stats["capture_running"] = _state.audio_capture.is_running if _state.audio_capture else False
    stats["mode"] = _state.audio_capture.mode if _state.audio_capture else "off"
    stats["transcription_engine"] = _state.transcriber.engine if _state.transcriber else "none"
    return stats


@app.get("/audio/level")
async def audio_level(x_api_key: Optional[str] = Header(None)):
    """Nivel de audio actual (para VU meter en el dashboard)."""
    _check_auth(x_api_key)
    if not _state.audio_buffer:
        return {"level": 0.0, "is_speech": False}
    chunks = _state.audio_buffer.get_latest(1.0)
    if not chunks:
        return {"level": 0.0, "is_speech": False}
    latest = chunks[-1]
    return {"level": latest.rms_level, "is_speech": latest.is_speech}


@app.get("/audio/transcribe")
async def audio_transcribe(
    seconds: float = Query(10.0, ge=1, le=120),
    x_api_key: Optional[str] = Header(None),
):
    """
    Transcribe los ultimos N segundos de audio.
    Usa Whisper local si esta disponible.
    """
    _check_auth(x_api_key)
    if not _state.audio_buffer or not _state.transcriber:
        raise HTTPException(503, "Audio not enabled or transcription unavailable")

    if not _state.transcriber.available:
        return {"text": "", "error": "No transcription engine. Install: pip install faster-whisper"}

    chunks = _state.audio_buffer.get_latest(seconds)
    speech_chunks = [c for c in chunks if c.is_speech]

    if not speech_chunks:
        return {"text": "", "note": "No speech detected in last " + str(seconds) + "s"}

    result = _state.transcriber.transcribe_chunks(speech_chunks)
    return result


@app.get("/audio/wav")
async def audio_wav(
    seconds: float = Query(10.0, ge=1, le=60),
    x_api_key: Optional[str] = Header(None),
):
    """Retorna audio WAV de los ultimos N segundos (para debug/playback)."""
    _check_auth(x_api_key)
    if not _state.audio_buffer:
        raise HTTPException(503, "Audio not enabled")

    wav_data = _state.audio_buffer.get_audio_wav(seconds)
    if not wav_data:
        raise HTTPException(404, "No audio in buffer")

    return Response(content=wav_data, media_type="audio/wav")


@app.get("/audio/devices")
async def audio_devices(x_api_key: Optional[str] = Header(None)):
    """Lista dispositivos de audio disponibles."""
    _check_auth(x_api_key)
    if not _state.audio_capture:
        return {"devices": []}
    return {"devices": _state.audio_capture.get_devices()}


# ─── Context Engine (F08) ───

@app.get("/context/state")
async def context_state(x_api_key: Optional[str] = Header(None)):
    """Estado actual del workflow: que esta haciendo el usuario."""
    _check_auth(x_api_key)
    if not _state.context:
        raise HTTPException(503, "Context engine not initialized")

    # Update context with current window
    from .vision import get_active_window_info
    win = get_active_window_info()
    _state.context.update(
        win.get("name", win.get("app_name", "unknown")),
        win.get("window_title", win.get("title", "")),
    )

    state = _state.context.get_state()
    return {
        "workflow": state.current_workflow,
        "confidence": state.confidence,
        "app": state.current_app,
        "title": state.current_title[:100],
        "time_in_workflow_seconds": state.time_in_workflow,
        "time_in_app_seconds": state.time_in_app,
        "switches_5min": state.switch_count_last_5min,
        "is_focused": state.is_focused,
        "summary": state.context_summary,
    }


@app.get("/context/apps")
async def context_apps(x_api_key: Optional[str] = Header(None)):
    """Stats de tiempo por app."""
    _check_auth(x_api_key)
    if not _state.context:
        raise HTTPException(503, "Not initialized")
    return _state.context.get_app_stats()


@app.get("/context/workflows")
async def context_workflows(x_api_key: Optional[str] = Header(None)):
    """Stats de tiempo por workflow."""
    _check_auth(x_api_key)
    if not _state.context:
        raise HTTPException(503, "Not initialized")
    return _state.context.get_workflow_stats()


@app.get("/context/timeline")
async def context_timeline(
    minutes: int = Query(30, ge=1, le=480),
    x_api_key: Optional[str] = Header(None),
):
    """Timeline de actividad."""
    _check_auth(x_api_key)
    if not _state.context:
        raise HTTPException(503, "Not initialized")
    return {"timeline": _state.context.get_timeline(minutes)}


# ─── Perception (Real-Time Vision) ───

@app.get("/perception")
async def perception_summary(
    seconds: float = Query(30, ge=1, le=300),
    x_api_key: Optional[str] = Header(None),
):
    """Get real-time perception events — what happened on screen."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return {
        "summary": _state.perception.get_summary(seconds),
        "event_count": _state.perception.get_event_count(),
        "running": _state.perception.is_running,
    }


@app.get("/perception/events")
async def perception_events(
    seconds: float = Query(30, ge=1, le=300),
    min_importance: float = Query(0.0, ge=0.0, le=1.0),
    x_api_key: Optional[str] = Header(None),
):
    """Get raw perception events."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    events = _state.perception.get_events(seconds, min_importance)
    return {
        "events": [
            {
                "timestamp": e.timestamp,
                "type": e.event_type,
                "description": e.description,
                "importance": e.importance,
                "uncertainty": e.uncertainty,
                "monitor": e.monitor,
                "details": e.details,
            }
            for e in events
        ]
    }


# ─── IPA State (Phase 4.3) ───

@app.get("/perception/state")
async def perception_state(x_api_key: Optional[str] = Header(None)):
    """Full IPA introspection — scene states, attention, ROIs, predictor."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return _state.perception.get_state()


@app.get("/perception/attention")
async def perception_attention(x_api_key: Optional[str] = Header(None)):
    """Attention heatmap grid (8x6 float values) for dashboard visualization."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return {
        "grid": _state.perception.get_attention_heatmap(),
        "rows": 6,
        "cols": 8,
        "hot_zones": _state.perception.get_state().get("attention_hot_zones", []),
    }


@app.get("/perception/world")
async def perception_world(x_api_key: Optional[str] = Header(None)):
    """IPA v2 semantic snapshot (WorldState)."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return _state.perception.get_world_state()


@app.get("/perception/trace")
async def perception_trace(
    seconds: float = Query(90, ge=1, le=600),
    x_api_key: Optional[str] = Header(None),
):
    """Compressed semantic transitions kept in RAM."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    bundle = _state.perception.get_world_trace_bundle(seconds=seconds)
    trace = bundle.get("trace", [])
    temporal = bundle.get("temporal", {})
    return {
        "trace": trace,
        "temporal": temporal,
        "count": len(trace),
        "seconds": seconds,
    }


@app.get("/perception/readiness")
async def perception_readiness(x_api_key: Optional[str] = Header(None)):
    """Whether perception has enough context to execute actions safely."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return _state.perception.get_readiness()


@app.post("/perception/query")
async def perception_query(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Query temporal visual context.
    Body: {question, at_ms?, window_seconds?, monitor_id?}
    """
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    question = (request_body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question is required")
    at_ms = request_body.get("at_ms")
    window_seconds = float(request_body.get("window_seconds", 30))
    monitor_id = request_body.get("monitor_id")
    return _state.perception.query_visual(
        question=question,
        at_ms=at_ms,
        window_seconds=window_seconds,
        monitor_id=monitor_id,
    )


# ─── Multi-Agent Workbench (IPA v2) ───

@app.post("/agents/register")
async def agent_register(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """Register a new agent with a role."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")

    name = request_body.get("name", "unnamed")
    role = request_body.get("role", "observer")
    autonomy = request_body.get("autonomy", "suggest")
    monitors = request_body.get("monitors", [])
    metadata = request_body.get("metadata", {})

    try:
        session = _state.agent_coordinator.register(
            name=name, role=role, autonomy=autonomy,
            monitors=monitors, metadata=metadata,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    return session.to_dict()


@app.get("/agents")
async def agent_list(x_api_key: Optional[str] = Header(None)):
    """List all registered agents."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    return _state.agent_coordinator.to_dict()


@app.get("/agents/{agent_id}")
async def agent_details(agent_id: str, x_api_key: Optional[str] = Header(None)):
    """Get details for a specific agent."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    session = _state.agent_coordinator.get_session(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return {
        **session.to_dict(),
        "allowed_tools": list(_state.agent_coordinator.get_allowed_tools(agent_id)),
        "pending_messages": _state.agent_coordinator._message_bus.pending_count(agent_id),
    }


@app.delete("/agents/{agent_id}")
async def agent_unregister(agent_id: str, x_api_key: Optional[str] = Header(None)):
    """Unregister an agent."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    if not _state.agent_coordinator.unregister(agent_id):
        raise HTTPException(404, f"Agent {agent_id} not found")
    return {"status": "unregistered", "agent_id": agent_id}


@app.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(agent_id: str, x_api_key: Optional[str] = Header(None)):
    """Update agent heartbeat."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    if not _state.agent_coordinator.heartbeat(agent_id):
        raise HTTPException(404, f"Agent {agent_id} not found")
    return {"status": "ok"}


@app.get("/agents/{agent_id}/perception")
async def agent_perception(
    agent_id: str,
    max_count: int = Query(20, ge=1, le=100),
    x_api_key: Optional[str] = Header(None),
):
    """Get perception events filtered for this agent's role and monitors."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    session = _state.agent_coordinator.get_session(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")

    events = _state.agent_coordinator.get_perception_events(agent_id, max_count)
    return {
        "agent_id": agent_id,
        "role": session.role.value,
        "events": [
            {
                "timestamp": e.timestamp,
                "type": e.event_type,
                "description": e.description,
                "importance": e.importance,
                "uncertainty": e.uncertainty,
                "monitor": e.monitor,
            }
            for e in events
        ],
    }


@app.post("/agents/{agent_id}/message")
async def agent_send_message(
    agent_id: str,
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """Send inter-agent message."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")

    to_id = request_body.get("to", "*")
    msg_type = request_body.get("type", "message")
    payload = request_body.get("payload", {})

    msg = _state.agent_coordinator.send_message(agent_id, to_id, msg_type, payload)
    if not msg:
        raise HTTPException(400, "Failed to send message (invalid agent IDs)")
    return msg.to_dict()


@app.get("/agents/{agent_id}/messages")
async def agent_get_messages(
    agent_id: str,
    max_count: int = Query(10, ge=1, le=50),
    x_api_key: Optional[str] = Header(None),
):
    """Poll messages for an agent."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    messages = _state.agent_coordinator.get_messages(agent_id, max_count)
    return {
        "agent_id": agent_id,
        "messages": [m.to_dict() for m in messages],
    }


# ─── Monitors (F10) ───

@app.get("/monitors")
async def monitors_info(x_api_key: Optional[str] = Header(None)):
    """Info de todos los monitores."""
    _check_auth(x_api_key)
    if not _state.monitor_mgr:
        raise HTTPException(503, "Not initialized")
    _state.monitor_mgr.refresh()
    return _state.monitor_mgr.to_dict()


# ─── Plugins (F09) ───

@app.get("/plugins")
async def plugins_list(x_api_key: Optional[str] = Header(None)):
    """Lista de plugins cargados."""
    _check_auth(x_api_key)
    if not _state.plugin_mgr:
        return {"plugins": []}
    return {
        "plugins": _state.plugin_mgr.get_info(),
        "event_log": _state.plugin_mgr.get_event_log(20),
    }


# ─── Memory (F11) ───

@app.get("/memory/recent")
async def memory_recent(
    minutes: int = Query(30, ge=1, le=480),
    x_api_key: Optional[str] = Header(None),
):
    """Entradas de memoria recientes."""
    _check_auth(x_api_key)
    if not _state.memory:
        return {"entries": [], "enabled": False}
    return {"entries": _state.memory.get_recent(minutes), "enabled": _state.memory.enabled}


@app.get("/memory/search")
async def memory_search(
    q: str = Query(..., min_length=1),
    x_api_key: Optional[str] = Header(None),
):
    """Buscar en la memoria temporal."""
    _check_auth(x_api_key)
    if not _state.memory or not _state.memory.enabled:
        return {"results": [], "enabled": False}
    return {"results": _state.memory.search(q), "query": q}


@app.post("/memory/toggle")
async def memory_toggle(
    enabled: bool = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Habilitar/deshabilitar memoria temporal."""
    _check_auth(x_api_key)
    if not _state.memory:
        raise HTTPException(503, "Not initialized")
    _state.memory.enabled = enabled
    return {"enabled": _state.memory.enabled}


# ─── Annotations (lapiz/marcador) ───

@app.post("/annotations/add")
async def add_annotation(
    type: str = Query(..., description="circle, rect, arrow, text, freehand"),
    x: int = Query(...),
    y: int = Query(...),
    width: int = Query(50),
    height: int = Query(50),
    color: str = Query("#FF0000"),
    thickness: int = Query(3),
    text: str = Query(""),
    x_api_key: Optional[str] = Header(None),
):
    """
    Agrega una anotacion visual (lapiz/marcador).
    La IA vera el overlay dibujado + la descripcion textual.
    Tipos: circle, rect, arrow, text, freehand
    """
    _check_auth(x_api_key)
    if not _state.vision:
        raise HTTPException(503, "Not initialized")

    import uuid
    ann = Annotation(
        id=str(uuid.uuid4())[:8],
        type=type,
        x=x, y=y,
        width=width, height=height,
        color=color,
        thickness=thickness,
        text=text,
    )
    _state.vision.annotations.add(ann)
    return {"id": ann.id, "type": type, "position": f"({x},{y})", "status": "added"}


@app.get("/annotations/list")
async def list_annotations(x_api_key: Optional[str] = Header(None)):
    """Lista todas las anotaciones activas."""
    _check_auth(x_api_key)
    if not _state.vision:
        raise HTTPException(503, "Not initialized")
    return {
        "count": len(_state.vision.annotations.annotations),
        "annotations": _state.vision.annotations.to_description(),
    }


@app.delete("/annotations/{annotation_id}")
async def remove_annotation(annotation_id: str, x_api_key: Optional[str] = Header(None)):
    """Elimina una anotacion por ID."""
    _check_auth(x_api_key)
    if not _state.vision:
        raise HTTPException(503, "Not initialized")
    removed = _state.vision.annotations.remove(annotation_id)
    return {"removed": removed, "id": annotation_id}


@app.post("/annotations/clear")
async def clear_annotations(x_api_key: Optional[str] = Header(None)):
    """Borra todas las anotaciones."""
    _check_auth(x_api_key)
    if not _state.vision:
        raise HTTPException(503, "Not initialized")
    _state.vision.annotations.clear()
    return {"status": "cleared"}


@app.get("/frame/annotated")
async def frame_annotated(x_api_key: Optional[str] = Header(None)):
    """Frame actual con las anotaciones dibujadas encima. Devuelve JPEG."""
    _check_auth(x_api_key)
    if not _state.buffer or not _state.vision:
        raise HTTPException(503, "Not initialized")

    slot = _state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    rendered = _state.vision.annotations.render_overlay(slot.frame_bytes)
    return Response(content=rendered, media_type="image/jpeg")


# ─── AI Provider Endpoints ───

@app.post("/ai/ask")
async def ai_ask(
    provider: str = Query(..., description="gemini, openai, claude, generic"),
    prompt: str = Query("Describe what you see on this screen."),
    api_key_param: Optional[str] = Query(None, alias="provider_api_key"),
    model: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """
    Envia el frame actual a un provider de IA y retorna la respuesta.
    La IA VE tu pantalla y responde.
    
    Ejemplo: /ai/ask?provider=gemini&provider_api_key=AIza...&prompt=What bug do you see?
    """
    _check_auth(x_api_key)
    if not _state.buffer:
        raise HTTPException(503, "Not initialized")

    slot = _state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    if not api_key_param:
        raise HTTPException(400, "provider_api_key is required")

    from .adapters import create_adapter
    try:
        kwargs = {}
        if model:
            kwargs["model"] = model
        adapter = create_adapter(provider, api_key_param, **kwargs)
        adapter.connect()

        # Build enriched prompt
        enriched = _state.vision.enrich_frame(slot, run_ocr=False) if _state.vision else None
        full_prompt = prompt
        if enriched:
            full_prompt = enriched.to_ai_prompt() + "\n\nUser question: " + prompt

        response = adapter.send_frame(slot.frame_bytes, full_prompt, slot.mime_type)
        adapter.disconnect()

        if response:
            return {
                "text": response.text,
                "provider": response.provider,
                "model": response.model,
                "latency_ms": response.latency_ms,
                "tokens_used": response.tokens_used,
            }
        return {"text": "", "error": "No response from provider"}

    except Exception as e:
        raise HTTPException(500, f"AI provider error: {e}")


# ─── Watchdog (E01) ───

@app.get("/watchdog/alerts")
async def watchdog_alerts(
    count: int = Query(20, ge=1, le=100),
    unacknowledged: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    """Get watchdog alerts."""
    _check_auth(x_api_key)
    if not _state.watchdog:
        return {"alerts": []}
    return {"alerts": _state.watchdog.get_alerts(count, unacknowledged)}


@app.get("/watchdog/triggers")
async def watchdog_triggers(x_api_key: Optional[str] = Header(None)):
    """List all watchdog triggers."""
    _check_auth(x_api_key)
    if not _state.watchdog:
        return {"triggers": []}
    return {"triggers": _state.watchdog.get_triggers(), "stats": _state.watchdog.stats}


@app.post("/watchdog/scan")
async def watchdog_scan(x_api_key: Optional[str] = Header(None)):
    """Manually trigger a watchdog scan on current screen."""
    _check_auth(x_api_key)
    if not _state.watchdog or not _state.buffer:
        raise HTTPException(503, "Not initialized")

    slot = _state.buffer.get_latest()
    if not slot:
        return {"alerts": [], "note": "No frames in buffer"}

    # Get OCR text and window title for scanning
    from .vision import get_active_window_info
    win = get_active_window_info()
    ocr_text = ""
    if _state.vision and _state.vision.ocr.available:
        ocr_result = _state.vision.ocr.extract_text(slot.frame_bytes, frame_hash=slot.phash)
        ocr_text = ocr_result.get("text", "")

    alerts = _state.watchdog.scan(ocr_text=ocr_text, window_title=win.get("title", ""))
    return {
        "new_alerts": [a.to_dict() for a in alerts],
        "total_alerts": _state.watchdog.stats["total_alerts"],
    }


@app.post("/watchdog/acknowledge/{alert_id}")
async def watchdog_ack(alert_id: str, x_api_key: Optional[str] = Header(None)):
    """Acknowledge an alert."""
    _check_auth(x_api_key)
    if not _state.watchdog:
        raise HTTPException(503, "Not initialized")
    return {"acknowledged": _state.watchdog.acknowledge(alert_id)}


# ─── AI Router (F16) ───

@app.post("/ai/route")
async def ai_route(
    prompt: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Route a prompt to the optimal AI model (cheapest that works)."""
    _check_auth(x_api_key)
    if not _state.router:
        raise HTTPException(503, "Not initialized")

    decision = _state.router.route(prompt)
    return {
        "route": decision.route,
        "reason": decision.reason,
        "estimated_cost": decision.estimated_cost,
        "model_suggestion": decision.model_suggestion,
        "needs_image": decision.needs_image,
        "needs_audio": decision.needs_audio,
    }


@app.get("/ai/router/stats")
async def ai_router_stats(x_api_key: Optional[str] = Header(None)):
    """Router cost savings stats."""
    _check_auth(x_api_key)
    if not _state.router:
        return {}
    return _state.router.stats


# ─── Collaborative (F17) ───

@app.post("/collab/create")
async def collab_create(
    host_name: str = Query(...),
    room_name: str = Query(""),
    x_api_key: Optional[str] = Header(None),
):
    """Create a collaborative room."""
    _check_auth(x_api_key)
    if not _state.collab:
        raise HTTPException(503, "Not initialized")
    return _state.collab.create_room(host_name, room_name)


@app.post("/collab/join")
async def collab_join(
    room_id: str = Query(...),
    viewer_name: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Join a collaborative room as viewer."""
    _check_auth(x_api_key)
    if not _state.collab:
        raise HTTPException(503, "Not initialized")
    result = _state.collab.join_room(room_id, viewer_name)
    if not result:
        raise HTTPException(404, "Room not found or full")
    return result


@app.get("/collab/room/{room_id}")
async def collab_room(room_id: str, x_api_key: Optional[str] = Header(None)):
    """Get room info."""
    _check_auth(x_api_key)
    if not _state.collab:
        raise HTTPException(503, "Not initialized")
    room = _state.collab.get_room(room_id)
    if not room:
        raise HTTPException(404, "Room not found")
    return room


@app.get("/collab/rooms")
async def collab_rooms(x_api_key: Optional[str] = Header(None)):
    """List active rooms."""
    _check_auth(x_api_key)
    if not _state.collab:
        return {"rooms": []}
    return {"rooms": _state.collab.list_rooms(), "stats": _state.collab.stats}


@app.post("/collab/annotate")
async def collab_annotate(
    room_id: str = Query(...),
    author_id: str = Query(...),
    type: str = Query("rect"),
    x: int = Query(...),
    y: int = Query(...),
    width: int = Query(100),
    height: int = Query(50),
    text: str = Query(""),
    x_api_key: Optional[str] = Header(None),
):
    """Add shared annotation in a collab room."""
    _check_auth(x_api_key)
    if not _state.collab:
        raise HTTPException(503, "Not initialized")
    result = _state.collab.add_annotation(room_id, author_id, type, x, y, width, height, text)
    if not result:
        raise HTTPException(404, "Room or author not found")
    return result


# ═══════════════════════════════════════════════════════════════
# v1.0 ENDPOINTS: Computer Use — 7 Capas
# ═══════════════════════════════════════════════════════════════


# ─── Capa 7: Safety / Autonomy / Audit ───

@app.get("/safety/status")
async def safety_status(x_api_key: Optional[str] = Header(None)):
    """Estado del sistema de seguridad."""
    _check_auth(x_api_key)
    return {
        "safety": _state.safety.stats if _state.safety else {},
        "autonomy": _state.autonomy.stats if _state.autonomy else {},
        "audit": _state.audit.stats if _state.audit else {},
    }


@app.post("/safety/kill")
async def safety_kill(x_api_key: Optional[str] = Header(None)):
    """KILL SWITCH: detiene toda actividad del agente."""
    _check_auth(x_api_key)
    if _state.safety:
        _state.safety.kill()
    if _state.actions:
        _state.actions.disable()
    return {"killed": True}


@app.post("/safety/resume")
async def safety_resume(x_api_key: Optional[str] = Header(None)):
    """Reactiva el agente despues de un kill."""
    _check_auth(x_api_key)
    if _state.safety:
        _state.safety.resume()
    return {"killed": False}


@app.get("/safety/whitelist")
async def safety_whitelist(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"whitelist": _state.safety.get_whitelist() if _state.safety else []}


@app.get("/operating/mode")
async def get_operating_mode(x_api_key: Optional[str] = Header(None)):
    """Current operating mode: SAFE, RAW, HYBRID."""
    _check_auth(x_api_key)
    return {"mode": _state.operating_mode}


@app.post("/operating/mode")
async def set_operating_mode(
    mode: str = Query(..., description="SAFE | RAW | HYBRID"),
    x_api_key: Optional[str] = Header(None),
):
    """Set operating mode and propagate risk mode into IPA WorldState."""
    _check_auth(x_api_key)
    mode_norm = _normalize_operating_mode(mode)
    _state.operating_mode = mode_norm
    if _state.perception:
        _state.perception.set_risk_mode(mode_norm.lower())
    return {"mode": _state.operating_mode}


@app.post("/autonomy/level")
async def set_autonomy_level(
    level: str = Query(..., description="suggest, confirm, auto"),
    x_api_key: Optional[str] = Header(None),
):
    """Cambia el nivel de autonomia."""
    _check_auth(x_api_key)
    if not _state.autonomy:
        raise HTTPException(503, "Not initialized")
    _state.autonomy.set_level(AutonomyLevel(level))
    return {"level": level}


@app.get("/audit/recent")
async def audit_recent(
    count: int = Query(20, ge=1, le=100),
    x_api_key: Optional[str] = Header(None),
):
    """Ultimas entradas del audit log."""
    _check_auth(x_api_key)
    if not _state.audit:
        return {"entries": []}
    return {"entries": _state.audit.get_recent(count)}


@app.get("/audit/failures")
async def audit_failures(
    count: int = Query(20, ge=1, le=100),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"entries": _state.audit.get_failures(count) if _state.audit else []}


# ─── Capa 1: Actions ───

@app.get("/action/status")
async def action_status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.actions.stats if _state.actions else {"enabled": False}


@app.post("/action/enable")
async def action_enable(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _state.actions:
        _state.actions.enable()
    return {"enabled": True}


@app.post("/action/disable")
async def action_disable(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _state.actions:
        _state.actions.disable()
    return {"enabled": False}


@app.post("/action/click")
async def action_click(
    x: int = Query(...), y: int = Query(...),
    button: str = Query("left"),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    result = _state.actions.click(x, y, button)
    return result.to_dict()


@app.post("/action/double_click")
async def action_double_click(
    x: int = Query(...), y: int = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    return _state.actions.double_click(x, y).to_dict()


@app.post("/action/type")
async def action_type(
    text: str = Query(...),
    interval: float = Query(0.02),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    return _state.actions.type_text(text, interval).to_dict()


@app.post("/action/hotkey")
async def action_hotkey(
    keys: str = Query(..., description="Keys separated by + (e.g. ctrl+s)"),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    key_list = [k.strip() for k in keys.split("+")]
    return _state.actions.hotkey(*key_list).to_dict()


@app.post("/action/scroll")
async def action_scroll(
    amount: int = Query(...),
    x: Optional[int] = Query(None), y: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    return _state.actions.scroll(amount, x, y).to_dict()


@app.post("/action/drag")
async def action_drag(
    start_x: int = Query(...), start_y: int = Query(...),
    end_x: int = Query(...), end_y: int = Query(...),
    duration: float = Query(0.5),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    return _state.actions.drag_drop(start_x, start_y, end_x, end_y, duration).to_dict()


@app.get("/action/mouse")
async def action_mouse(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.actions:
        return {"x": 0, "y": 0}
    return _state.actions.get_mouse_position()


@app.get("/action/log")
async def action_log(
    count: int = Query(20),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"log": _state.actions.get_action_log(count) if _state.actions else []}


@app.post("/action/precheck")
async def action_precheck(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Validate if an action is executable with current context/mode.
    Accepts either:
      {"instruction":"save file"}
    or
      {"action":"click","params":{"x":100,"y":200},"category":"normal"}
    """
    _check_auth(x_api_key)
    intent = _intent_from_payload(request_body)
    mode = request_body.get("mode") or _state.operating_mode
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = int(request_body.get("max_staleness_ms", 1500))
    return _build_precheck(
        intent,
        mode,
        include_readiness=True,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
    )


@app.post("/action/execute")
async def action_execute(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Execute action through current operating mode (SAFE/RAW/HYBRID).
    SAFE applies all guards. HYBRID guards destructive actions only.
    """
    _check_auth(x_api_key)
    intent = _intent_from_payload(request_body)
    mode = request_body.get("mode") or _state.operating_mode
    verify = bool(request_body.get("verify", True))
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = int(request_body.get("max_staleness_ms", 1500))
    return _execute_intent(
        intent,
        mode=mode,
        verify=verify,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
    )


@app.post("/action/raw")
async def action_raw(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Raw execution path (0 guardrails except kill switch).
    Intended for expert setups where external AI handles all safety.
    """
    _check_auth(x_api_key)
    intent = _intent_from_payload(request_body)
    verify = bool(request_body.get("verify", False))
    return _execute_intent(intent, mode="RAW", verify=verify)


@app.post("/action/verify")
async def action_verify(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """Run post-action verification without executing new actions."""
    _check_auth(x_api_key)
    if not _state.verifier:
        raise HTTPException(503, "Verifier not initialized")
    action = (request_body.get("action") or "").strip()
    if not action:
        raise HTTPException(400, "action is required")
    params = request_body.get("params") or {}
    pre_state = request_body.get("pre_state")
    result = _state.verifier.verify(action, params, pre_state)
    return result.to_dict()


# ─── Capa 1: Windows ───

@app.get("/windows/list")
async def windows_list(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.windows:
        return {"windows": []}
    return {"windows": [w.to_dict() for w in _state.windows.list_windows()]}


@app.get("/windows/active")
async def windows_active(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.windows:
        return {}
    win = _state.windows.get_active_window()
    return win.to_dict() if win else {}


@app.post("/windows/focus")
async def windows_focus(
    title: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.windows:
        raise HTTPException(503, "Not initialized")
    return {"success": _state.windows.focus_window(title=title)}


@app.post("/windows/minimize")
async def windows_minimize(title: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"success": _state.windows.minimize_window(title=title) if _state.windows else False}


@app.post("/windows/maximize")
async def windows_maximize(title: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"success": _state.windows.maximize_window(title=title) if _state.windows else False}


@app.post("/windows/close")
async def windows_close(title: str = Query(...), x_api_key: Optional[str] = Header(None)):
    """DESTRUCTIVE: cierra una ventana."""
    _check_auth(x_api_key)
    return {"success": _state.windows.close_window(title=title) if _state.windows else False}


@app.post("/windows/move")
async def windows_move(
    title: str = Query(...),
    x: int = Query(...), y: int = Query(...),
    width: int = Query(-1), height: int = Query(-1),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"success": _state.windows.move_window(x, y, width, height, title=title) if _state.windows else False}


# ─── Capa 1: Clipboard ───

@app.get("/clipboard/read")
async def clipboard_read(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.clipboard:
        return {"text": ""}
    return {"text": _state.clipboard.read()}


@app.post("/clipboard/write")
async def clipboard_write(text: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"success": _state.clipboard.write(text) if _state.clipboard else False}


@app.get("/clipboard/history")
async def clipboard_history(count: int = Query(20), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"history": _state.clipboard.get_history(count) if _state.clipboard else []}


# ─── Capa 1: Process Manager ───

@app.get("/process/list")
async def process_list(
    sort_by: str = Query("memory"),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"processes": _state.process_mgr.list_processes(sort_by) if _state.process_mgr else []}


@app.get("/process/find")
async def process_find(name: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"matches": _state.process_mgr.find_process(name) if _state.process_mgr else []}


@app.post("/process/launch")
async def process_launch(command: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.process_mgr.launch(command) if _state.process_mgr else {"success": False}


@app.post("/process/terminate")
async def process_terminate(
    name: Optional[str] = Query(None),
    pid: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """DESTRUCTIVE: termina un proceso."""
    _check_auth(x_api_key)
    return _state.process_mgr.terminate(pid=pid, name=name) if _state.process_mgr else {"success": False}


# ─── Capa 2: UI Tree ───

@app.get("/ui/elements")
async def ui_elements(
    pid: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"elements": _state.ui_tree.get_elements(pid=pid) if _state.ui_tree else []}


@app.get("/ui/find")
async def ui_find(
    name: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.ui_tree:
        return {"element": None}
    return {"element": _state.ui_tree.find_element(name=name, role=role)}


@app.get("/ui/find_all")
async def ui_find_all(
    name: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"elements": _state.ui_tree.find_all(name=name, role=role) if _state.ui_tree else []}


@app.post("/ui/click")
async def ui_click(
    name: str = Query(...),
    role: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    return _state.actions.click_element(name, role).to_dict()


@app.post("/ui/type")
async def ui_type(
    field: str = Query(...),
    text: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    return _state.actions.type_in_field(field, text).to_dict()


# ─── Capa 3: VS Code ───

@app.post("/vscode/command")
async def vscode_command(cmd: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.vscode.execute_command(cmd) if _state.vscode else {"success": False}


@app.post("/vscode/open")
async def vscode_open(
    path: str = Query(...),
    line: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _state.vscode.open_file(path, line) if _state.vscode else {"success": False}


# ─── Capa 3: Terminal ───

@app.post("/terminal/exec")
async def terminal_exec(
    cmd: str = Query(...),
    cwd: Optional[str] = Query(None),
    timeout: float = Query(30),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.terminal:
        raise HTTPException(503, "Not initialized")
    return _state.terminal.run_command(cmd, cwd=cwd, timeout=timeout).to_dict()


@app.post("/terminal/background")
async def terminal_background(
    cmd: str = Query(...), name: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _state.terminal.run_background(cmd, name) if _state.terminal else {"success": False}


@app.get("/terminal/background/{name}")
async def terminal_bg_status(name: str, x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.terminal.get_background_status(name) if _state.terminal else {}


@app.get("/terminal/history")
async def terminal_history(count: int = Query(20), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"history": _state.terminal.get_history(count) if _state.terminal else []}


# ─── Capa 3: Git ───

@app.get("/git/status")
async def git_status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.git_ops.status() if _state.git_ops else {"success": False}


@app.get("/git/log")
async def git_log(count: int = Query(10), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.git_ops.log(count) if _state.git_ops else {"success": False}


@app.get("/git/diff")
async def git_diff(staged: bool = Query(False), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.git_ops.diff(staged) if _state.git_ops else {"success": False}


@app.post("/git/commit")
async def git_commit(message: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.git_ops:
        raise HTTPException(503, "Not initialized")
    add_result = _state.git_ops.add()
    commit_result = _state.git_ops.commit(message)
    return commit_result


@app.post("/git/push")
async def git_push(x_api_key: Optional[str] = Header(None)):
    """DESTRUCTIVE: push al remoto."""
    _check_auth(x_api_key)
    return _state.git_ops.push() if _state.git_ops else {"success": False}


@app.post("/git/pull")
async def git_pull(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.git_ops.pull() if _state.git_ops else {"success": False}


# ─── Capa 4: Browser ───

@app.get("/browser/tabs")
async def browser_tabs(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"tabs": _state.browser.list_tabs() if _state.browser else []}


@app.post("/browser/navigate")
async def browser_navigate(url: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.browser.navigate(url) if _state.browser else {"success": False}


@app.post("/browser/click")
async def browser_click(selector: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.browser.click_selector(selector) if _state.browser else {"success": False}


@app.post("/browser/fill")
async def browser_fill(selector: str = Query(...), value: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.browser.fill_input(selector, value) if _state.browser else {"success": False}


@app.get("/browser/text")
async def browser_text(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"text": _state.browser.get_page_text() if _state.browser else ""}


@app.post("/browser/eval")
async def browser_eval(expression: str = Query(...), x_api_key: Optional[str] = Header(None)):
    """WARNING: Executes arbitrary JavaScript in the browser. Use with caution."""
    _check_auth(x_api_key)
    # Safety: block dangerous patterns
    dangerous = ["document.cookie", "localStorage", "sessionStorage", "eval(", "Function("]
    for pattern in dangerous:
        if pattern in expression:
            return {"error": f"Blocked: expression contains dangerous pattern '{pattern}'"}
    return _state.browser.evaluate(expression) if _state.browser else {}


# ─── Capa 5: File System ───

@app.get("/files/read")
async def files_read(path: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.filesystem.read_file(path) if _state.filesystem else {"success": False}


@app.post("/files/write")
async def files_write(
    path: str = Query(...), content: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _state.filesystem.write_file(path, content) if _state.filesystem else {"success": False}


@app.get("/files/list")
async def files_list(
    path: str = Query("."),
    pattern: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _state.filesystem.list_dir(path, pattern) if _state.filesystem else {"success": False}


@app.get("/files/search")
async def files_search(
    pattern: str = Query("*"),
    contains: Optional[str] = Query(None),
    path: str = Query("."),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _state.filesystem.search_files(pattern, contains, path) if _state.filesystem else {"success": False}


@app.delete("/files/delete")
async def files_delete(path: str = Query(...), x_api_key: Optional[str] = Header(None)):
    """DESTRUCTIVE: elimina un archivo."""
    _check_auth(x_api_key)
    return _state.filesystem.delete_file(path) if _state.filesystem else {"success": False}


# ─── Capa 6: Agent / Brain ───

@app.post("/agent/do")
async def agent_do(
    instruction: str = Query(..., description="Natural language instruction"),
    x_api_key: Optional[str] = Header(None),
):
    """
    Intent-based action: la IA interpreta y ejecuta.
    "guarda el archivo" → clasifica → resuelve → verifica
    """
    _check_auth(x_api_key)
    if not _state.intent or not _state.resolver:
        raise HTTPException(503, "Brain not initialized")
    intent = _state.intent.classify_or_default(instruction)
    return _execute_intent(intent, mode=_state.operating_mode, verify=True)


@app.post("/agent/plan")
async def agent_plan(
    description: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Crea un plan de ejecucion (dry run)."""
    _check_auth(x_api_key)
    if not _state.planner:
        raise HTTPException(503, "Not initialized")
    plan = _state.planner.create_plan(description)
    return plan.to_dict()


@app.get("/agent/plans")
async def agent_plans(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"plans": _state.planner.list_plans() if _state.planner else []}


@app.get("/agent/status")
async def agent_status(x_api_key: Optional[str] = Header(None)):
    """Estado completo del agente."""
    _check_auth(x_api_key)
    return {
        "operating_mode": {"mode": _state.operating_mode},
        "actions": _state.actions.stats if _state.actions else {},
        "safety": _state.safety.stats if _state.safety else {},
        "autonomy": _state.autonomy.stats if _state.autonomy else {},
        "resolver": _state.resolver.stats if _state.resolver else {},
        "intent": _state.intent.stats if _state.intent else {},
        "planner": _state.planner.stats if _state.planner else {},
        "recovery": _state.recovery.stats if _state.recovery else {},
        "perception_readiness": _state.perception.get_readiness() if _state.perception else {},
        "bootstrap_warnings": list(_state.bootstrap_warnings),
    }


# ─── System overview ───

@app.get("/system/overview")
async def system_overview(x_api_key: Optional[str] = Header(None)):
    """Complete system overview - all components status."""
    _check_auth(x_api_key)
    overview = {
        "version": "1.0.0",
        "operating_mode": _state.operating_mode,
        "capture": {
            "running": _state.capture.is_running if _state.capture else False,
            "fps": _state.capture.current_fps if _state.capture else 0,
        },
        "buffer": _state.buffer.stats if _state.buffer else {},
        "audio": {
            "enabled": _state.audio_capture is not None and _state.audio_capture.is_running if _state.audio_capture else False,
            "stats": _state.audio_buffer.stats if _state.audio_buffer else {},
        },
        "ocr": {
            "engine": _state.vision.ocr.engine if _state.vision else "none",
            "available": _state.vision.ocr.available if _state.vision else False,
        },
        "monitors": _state.monitor_mgr.to_dict() if _state.monitor_mgr else {},
        "watchdog": _state.watchdog.stats if _state.watchdog else {},
        "router": _state.router.stats if _state.router else {},
        "collab": _state.collab.stats if _state.collab else {},
        "memory": _state.memory.stats if _state.memory else {},
        "plugins": len(_state.plugin_mgr.loaded) if _state.plugin_mgr else 0,
        # v1.0: Computer Use
        "actions": _state.actions.stats if _state.actions else {},
        "windows": _state.windows.stats if _state.windows else {},
        "clipboard": _state.clipboard.stats if _state.clipboard else {},
        "process_mgr": _state.process_mgr.stats if _state.process_mgr else {},
        "ui_tree": _state.ui_tree.stats if _state.ui_tree else {},
        "vscode": _state.vscode.stats if _state.vscode else {},
        "terminal": _state.terminal.stats if _state.terminal else {},
        "git": _state.git_ops.stats if _state.git_ops else {},
        "browser": _state.browser.stats if _state.browser else {},
        "filesystem": _state.filesystem.stats if _state.filesystem else {},
        "safety": _state.safety.stats if _state.safety else {},
        "autonomy": _state.autonomy.stats if _state.autonomy else {},
        "resolver": _state.resolver.stats if _state.resolver else {},
        "license": {
            "plan": _state.license.plan.value if _state.license else "free",
            "is_pro": _state.license.is_pro if _state.license else False,
            "user": _state.license.user if _state.license else {},
        },
        "perception": {
            "readiness": _state.perception.get_readiness() if _state.perception else {},
            "world": _state.perception.get_world_state() if _state.perception else {},
        },
        "bootstrap_warnings": list(_state.bootstrap_warnings),
    }
    return overview


@app.get("/license/status")
async def license_status():
    """Current license plan and available features."""
    lic = get_license()
    return {
        "plan": lic.plan.value,
        "is_pro": lic.is_pro,
        "user": lic.user,
        "actions": {
            "available": sorted(lic.available_actions),
            "total": len(lic.available_actions),
            "max": len(lic.available_actions) if lic.is_pro else 7,
        },
        "mcp_tools": {
            "available": sorted(lic.available_mcp_tools),
            "total": len(lic.available_mcp_tools),
            "max": len(lic.available_mcp_tools) if lic.is_pro else 7,
        },
        "upgrade_url": None if lic.is_pro else "https://iluminaty.dev/#pricing",
    }


# ─── Token Economy ─────────────────────────────────────────────

# Approximate token costs per response mode
TOKEN_COSTS = {
    "text_only":  {"tokens": 200,   "desc": "OCR text + metadata, no image"},
    "low_res":    {"tokens": 5000,  "desc": "Image at 320px width + metadata"},
    "medium_res": {"tokens": 15000, "desc": "Image at 768px width + metadata"},
    "full_res":   {"tokens": 30000, "desc": "Image at 1280px width + metadata"},
}


class _TokenTracker:
    """Tracks token usage per session."""
    def __init__(self):
        self.mode: str = "text_only"  # Default: cheapest mode
        self.budget: int = 0          # 0 = unlimited
        self.used: int = 0
        self.history: list = []       # Last 50 actions
        self.max_history = 50

    def estimate(self, mode: str = None) -> int:
        return TOKEN_COSTS.get(mode or self.mode, TOKEN_COSTS["text_only"])["tokens"]

    def record(self, action: str, tokens: int):
        self.used += tokens
        entry = {"action": action, "tokens": tokens, "time": time.time()}
        self.history.append(entry)
        if len(self.history) > self.max_history:
            self.history.pop(0)

    def budget_remaining(self) -> int:
        if self.budget == 0:
            return -1  # unlimited
        return max(0, self.budget - self.used)

    def is_over_budget(self) -> bool:
        return self.budget > 0 and self.used >= self.budget


_tokens = _TokenTracker()


@app.get("/tokens/status")
async def tokens_status(x_api_key: Optional[str] = Header(None)):
    """Current token usage and budget."""
    _check_auth(x_api_key)
    return {
        "mode": _tokens.mode,
        "mode_cost": TOKEN_COSTS[_tokens.mode],
        "all_modes": TOKEN_COSTS,
        "used": _tokens.used,
        "budget": _tokens.budget,
        "remaining": _tokens.budget_remaining(),
        "over_budget": _tokens.is_over_budget(),
        "history_count": len(_tokens.history),
        "last_5": _tokens.history[-5:],
    }


@app.post("/tokens/mode")
async def tokens_set_mode(
    mode: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Set response mode: text_only, low_res, medium_res, full_res."""
    _check_auth(x_api_key)
    if mode not in TOKEN_COSTS:
        raise HTTPException(400, f"Invalid mode. Choose: {list(TOKEN_COSTS.keys())}")
    _tokens.mode = mode
    return {"mode": mode, "estimated_tokens_per_call": TOKEN_COSTS[mode]["tokens"]}


@app.post("/tokens/budget")
async def tokens_set_budget(
    limit: int = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Set token budget. 0 = unlimited."""
    _check_auth(x_api_key)
    _tokens.budget = max(0, limit)
    return {"budget": _tokens.budget, "used": _tokens.used, "remaining": _tokens.budget_remaining()}


@app.post("/tokens/reset")
async def tokens_reset(x_api_key: Optional[str] = Header(None)):
    """Reset token counter."""
    _check_auth(x_api_key)
    _tokens.used = 0
    _tokens.history.clear()
    return {"reset": True, "used": 0}


@app.get("/vision/smart")
async def vision_smart(
    mode: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """
    Smart vision endpoint — adapts response based on token mode.

    Modes:
      text_only   → OCR text + window info (~200 tokens)
      low_res     → 320px image + text (~5K tokens)
      medium_res  → 768px image + text (~15K tokens)
      full_res    → 1280px image + text (~30K tokens)
    """
    _check_auth(x_api_key)
    if not _state.buffer or not _state.vision:
        raise HTTPException(503, "Not initialized")

    # Check budget
    active_mode = mode or _tokens.mode
    if _tokens.is_over_budget():
        return JSONResponse({
            "error": "token_budget_exceeded",
            "used": _tokens.used,
            "budget": _tokens.budget,
            "suggestion": "Switch to text_only mode or increase budget",
        }, status_code=429)

    slot = _state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    result = {}

    if active_mode == "text_only":
        enriched = _state.vision.enrich_frame(slot, run_ocr=True)
        result = {
            "mode": "text_only",
            "timestamp": enriched.timestamp,
            "ocr_text": enriched.ocr_text,
            "active_window": enriched.active_window,
            "ai_prompt": enriched.to_ai_prompt(),
            "change_score": enriched.change_score,
        }
    else:
        # Resize image based on mode
        from PIL import Image
        import io

        enriched = _state.vision.enrich_frame(slot, run_ocr=True)
        d = enriched.to_dict(include_image=True)

        if active_mode in ("low_res", "medium_res") and d.get("image_base64"):
            import base64
            target_width = 320 if active_mode == "low_res" else 768
            img_bytes = base64.b64decode(d["image_base64"])
            img = Image.open(io.BytesIO(img_bytes))
            ratio = target_width / img.width
            new_size = (target_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=60)
            d["image_base64"] = base64.b64encode(buf.getvalue()).decode()
            d["width"] = new_size[0]
            d["height"] = new_size[1]

        d["mode"] = active_mode
        result = d

    # Track tokens
    est = _tokens.estimate(active_mode)
    _tokens.record(f"vision/smart ({active_mode})", est)
    result["token_estimate"] = est
    result["tokens_used_total"] = _tokens.used

    return JSONResponse(result)
