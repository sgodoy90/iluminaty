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


# ─── Server State (BUG-005 fix: single state object with lock) ───
import threading as _threading

class _ServerState:
    """Thread-safe server state container."""
    def __init__(self):
        self.lock = _threading.Lock()
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
        result["image_url"] = f"data:image/jpeg;base64,{result['image_base64']}"
    return result


def _check_auth(api_key: Optional[str]):
    if _state.api_key and api_key != _state.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


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
    version="0.5.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8420", "http://localhost:8420", "tauri://localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        pass
    finally:
        _state.ws_clients.discard(ws)


def init_server(
    buffer: RingBuffer,
    capture: ScreenCapture,
    api_key: Optional[str] = None,
    audio_buffer: Optional[AudioRingBuffer] = None,
    audio_capture: Optional[AudioCapture] = None,
):
    """Inyecta las dependencias al modulo del server (thread-safe)."""
    with _state.lock:
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
        _state.memory = TemporalMemory(enabled=False)
        _state.watchdog = Watchdog()
        _state.router = AIRouter()
        _state.collab = CollaborativeManager()


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
    _state.context.update(win.get("title", "unknown"), win.get("title", ""))

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


# ─── System overview ───

@app.get("/system/overview")
async def system_overview(x_api_key: Optional[str] = Header(None)):
    """Complete system overview - all components status."""
    _check_auth(x_api_key)
    overview = {
        "version": "0.5.0",
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
    }
    return overview
