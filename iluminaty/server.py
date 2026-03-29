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


# ─── Estado global (vive solo en RAM del proceso) ───
_buffer: Optional[RingBuffer] = None
_capture: Optional[ScreenCapture] = None
_api_key: Optional[str] = None
_vision: Optional[VisionIntelligence] = None
_diff: Optional[SmartDiff] = None
_audio_buffer: Optional[AudioRingBuffer] = None
_audio_capture: Optional[AudioCapture] = None
_transcriber: Optional[TranscriptionEngine] = None
_ws_clients: set[WebSocket] = set()


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
    if _api_key and api_key != _api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ─── Lifespan ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ya se inicializa desde main.py
    yield
    # Cleanup
    if _capture and _capture.is_running:
        _capture.stop()
    if _buffer:
        _buffer.flush()


# ─── App ───
app = FastAPI(
    title="ILUMINATY",
    description="Real-time visual perception for AI. Zero-disk, RAM-only.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Endpoints ───

@app.get("/health")
async def health():
    return {
        "status": "alive",
        "capture_running": _capture.is_running if _capture else False,
        "buffer_slots": _buffer.size if _buffer else 0,
    }


@app.get("/", response_class=Response)
async def dashboard():
    """Dashboard web en vivo."""
    return Response(content=DASHBOARD_HTML, media_type="text/html")


@app.get("/buffer/stats")
async def buffer_stats(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _buffer:
        raise HTTPException(503, "Buffer not initialized")
    stats = _buffer.stats
    stats["capture_running"] = _capture.is_running if _capture else False
    stats["current_fps"] = _capture.current_fps if _capture else 0
    stats["ws_clients"] = len(_ws_clients)
    return stats


@app.get("/frame/latest")
async def frame_latest(
    base64_encode: bool = Query(False, alias="base64"),
    x_api_key: Optional[str] = Header(None),
):
    """Último frame. Sin base64 devuelve JPEG raw. Con base64 devuelve JSON."""
    _check_auth(x_api_key)
    if not _buffer:
        raise HTTPException(503, "Buffer not initialized")
    
    slot = _buffer.get_latest()
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
    if not _buffer:
        raise HTTPException(503, "Buffer not initialized")
    
    if seconds is not None:
        slots = _buffer.get_since(seconds)
    elif last is not None:
        slots = _buffer.get_latest_n(last)
    else:
        slots = _buffer.get_latest_n(5)
    
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
    if not _capture:
        raise HTTPException(503, "Capture not initialized")
    
    changed = []
    if fps is not None:
        _capture.config.fps = fps
        _capture._current_fps = fps
        changed.append(f"fps={fps}")
    if quality is not None:
        _capture.config.quality = max(10, min(95, quality))
        changed.append(f"quality={quality}")
    if image_format is not None and image_format in ("jpeg", "webp", "png"):
        _capture.config.image_format = image_format
        changed.append(f"image_format={image_format}")
    if max_width is not None:
        _capture.config.max_width = max(320, min(3840, max_width))
        changed.append(f"max_width={max_width}")
    if skip_unchanged is not None:
        _capture.config.skip_unchanged = skip_unchanged
        changed.append(f"skip_unchanged={skip_unchanged}")
    if smart_quality is not None:
        _capture.config.smart_quality = smart_quality
        changed.append(f"smart_quality={smart_quality}")
    
    return {"updated": changed}


@app.post("/buffer/flush")
async def flush_buffer(x_api_key: Optional[str] = Header(None)):
    """Destruir todo el contenido visual del buffer. Irreversible."""
    _check_auth(x_api_key)
    if _buffer:
        _buffer.flush()
    return {"status": "flushed", "slots": 0}


@app.post("/capture/start")
async def start_capture(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _capture and not _capture.is_running:
        _capture.start()
    return {"status": "running"}


@app.post("/capture/stop")
async def stop_capture(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _capture and _capture.is_running:
        _capture.stop()
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
    _ws_clients.add(ws)
    
    try:
        last_hash = None
        while True:
            if not _buffer:
                await asyncio.sleep(1)
                continue
                
            slot = _buffer.get_latest()
            if slot and slot.phash != last_hash:
                await ws.send_json(_frame_to_json(slot, include_base64=True))
                last_hash = slot.phash
            
            # Enviar al ritmo del FPS actual
            fps = _capture.current_fps if _capture else 1.0
            await asyncio.sleep(1.0 / fps)
            
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


def init_server(
    buffer: RingBuffer,
    capture: ScreenCapture,
    api_key: Optional[str] = None,
    audio_buffer: Optional[AudioRingBuffer] = None,
    audio_capture: Optional[AudioCapture] = None,
):
    """Inyecta las dependencias al módulo del server."""
    global _buffer, _capture, _api_key, _vision, _diff
    global _audio_buffer, _audio_capture, _transcriber
    _buffer = buffer
    _capture = capture
    _api_key = api_key
    _vision = VisionIntelligence()
    _diff = SmartDiff(grid_cols=8, grid_rows=6)
    _audio_buffer = audio_buffer
    _audio_capture = audio_capture
    _transcriber = TranscriptionEngine()


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
    if not _buffer or not _vision:
        raise HTTPException(503, "Not initialized")

    slot = _buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    enriched = _vision.enrich_frame(slot, run_ocr=ocr)
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
    if not _buffer or not _vision:
        raise HTTPException(503, "Not initialized")

    slot = _buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    if all(v is not None for v in [region_x, region_y, region_w, region_h]):
        result = _vision.ocr.extract_region(slot.frame_bytes, region_x, region_y, region_w, region_h)
    else:
        result = _vision.ocr.extract_text(slot.frame_bytes)

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
    if not _buffer or not _diff:
        raise HTTPException(503, "Not initialized")

    slot = _buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    diff = _diff.compare(slot.frame_bytes)

    result = {
        "changed": diff.changed,
        "change_percentage": diff.change_percentage,
        "changed_cells": diff.changed_cells,
        "total_cells": diff.total_cells,
        "heatmap": diff.heatmap,
        "description": _diff.diff_to_description(diff, slot.width, slot.height),
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
        result["deltas"] = _diff.get_delta_regions(slot.frame_bytes, diff)

    return result


# ─── Audio endpoints ───

@app.get("/audio/stats")
async def audio_stats(x_api_key: Optional[str] = Header(None)):
    """Stats del buffer de audio."""
    _check_auth(x_api_key)
    if not _audio_buffer:
        return {"status": "disabled", "mode": "off"}
    stats = _audio_buffer.stats
    stats["capture_running"] = _audio_capture.is_running if _audio_capture else False
    stats["mode"] = _audio_capture.mode if _audio_capture else "off"
    stats["transcription_engine"] = _transcriber.engine if _transcriber else "none"
    return stats


@app.get("/audio/level")
async def audio_level(x_api_key: Optional[str] = Header(None)):
    """Nivel de audio actual (para VU meter en el dashboard)."""
    _check_auth(x_api_key)
    if not _audio_buffer:
        return {"level": 0.0, "is_speech": False}
    chunks = _audio_buffer.get_latest(1.0)
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
    if not _audio_buffer or not _transcriber:
        raise HTTPException(503, "Audio not enabled or transcription unavailable")

    if not _transcriber.available:
        return {"text": "", "error": "No transcription engine. Install: pip install faster-whisper"}

    chunks = _audio_buffer.get_latest(seconds)
    speech_chunks = [c for c in chunks if c.is_speech]

    if not speech_chunks:
        return {"text": "", "note": "No speech detected in last " + str(seconds) + "s"}

    result = _transcriber.transcribe_chunks(speech_chunks)
    return result


@app.get("/audio/wav")
async def audio_wav(
    seconds: float = Query(10.0, ge=1, le=60),
    x_api_key: Optional[str] = Header(None),
):
    """Retorna audio WAV de los ultimos N segundos (para debug/playback)."""
    _check_auth(x_api_key)
    if not _audio_buffer:
        raise HTTPException(503, "Audio not enabled")

    wav_data = _audio_buffer.get_audio_wav(seconds)
    if not wav_data:
        raise HTTPException(404, "No audio in buffer")

    return Response(content=wav_data, media_type="audio/wav")


@app.get("/audio/devices")
async def audio_devices(x_api_key: Optional[str] = Header(None)):
    """Lista dispositivos de audio disponibles."""
    _check_auth(x_api_key)
    if not _audio_capture:
        return {"devices": []}
    return {"devices": _audio_capture.get_devices()}


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
    if not _vision:
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
    _vision.annotations.add(ann)
    return {"id": ann.id, "type": type, "position": f"({x},{y})", "status": "added"}


@app.get("/annotations/list")
async def list_annotations(x_api_key: Optional[str] = Header(None)):
    """Lista todas las anotaciones activas."""
    _check_auth(x_api_key)
    if not _vision:
        raise HTTPException(503, "Not initialized")
    return {
        "count": len(_vision.annotations.annotations),
        "annotations": _vision.annotations.to_description(),
    }


@app.delete("/annotations/{annotation_id}")
async def remove_annotation(annotation_id: str, x_api_key: Optional[str] = Header(None)):
    """Elimina una anotacion por ID."""
    _check_auth(x_api_key)
    if not _vision:
        raise HTTPException(503, "Not initialized")
    removed = _vision.annotations.remove(annotation_id)
    return {"removed": removed, "id": annotation_id}


@app.post("/annotations/clear")
async def clear_annotations(x_api_key: Optional[str] = Header(None)):
    """Borra todas las anotaciones."""
    _check_auth(x_api_key)
    if not _vision:
        raise HTTPException(503, "Not initialized")
    _vision.annotations.clear()
    return {"status": "cleared"}


@app.get("/frame/annotated")
async def frame_annotated(x_api_key: Optional[str] = Header(None)):
    """Frame actual con las anotaciones dibujadas encima. Devuelve JPEG."""
    _check_auth(x_api_key)
    if not _buffer or not _vision:
        raise HTTPException(503, "Not initialized")

    slot = _buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    rendered = _vision.annotations.render_overlay(slot.frame_bytes)
    return Response(content=rendered, media_type="image/jpeg")
