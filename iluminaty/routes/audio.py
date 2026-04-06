"""Route module — audio."""
from __future__ import annotations
from typing import Optional
import asyncio
import base64
import io as _io
import json
import logging
import os
import time

from fastapi import APIRouter, Query, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

# _state and helpers are resolved at import time via server module globals
import iluminaty.server as _srv

router = APIRouter()

def _get_state():
    return _srv._state

def _auth(k):
    return _srv._check_auth(k)

# ─── Audio endpoints ───

@router.get("/audio/stats")
async def audio_stats(x_api_key: Optional[str] = Header(None)):
    """Stats del buffer de audio."""
    _srv._check_auth(x_api_key)
    if not _srv._state.audio_buffer:
        return {"status": "disabled", "mode": "off"}
    stats = _srv._state.audio_buffer.stats
    stats["capture_running"] = _srv._state.audio_capture.is_running if _srv._state.audio_capture else False
    stats["mode"] = _srv._state.audio_capture.mode if _srv._state.audio_capture else "off"
    stats["transcription_engine"] = _srv._state.transcriber.engine if _srv._state.transcriber else "none"
    if _srv._state.audio_interrupt:
        stats["interrupt"] = _srv._state.audio_interrupt.status()
    return stats


@router.get("/audio/level")
async def audio_level(x_api_key: Optional[str] = Header(None)):
    """Nivel de audio actual (para VU meter en el dashboard)."""
    _srv._check_auth(x_api_key)
    if not _srv._state.audio_buffer:
        return {"level": 0.0, "is_speech": False}
    chunks = _srv._state.audio_buffer.get_latest(1.0)
    if not chunks:
        return {"level": 0.0, "is_speech": False}
    latest = chunks[-1]
    if _srv._state.audio_interrupt:
        try:
            _srv._state.audio_interrupt.ingest_level(
                float(latest.rms_level),
                is_speech=bool(latest.is_speech),
                source="audio_level",
            )
        except Exception as e:
            _srv.logger.debug("Audio interrupt level ingest failed: %s", e)
    return {"level": latest.rms_level, "is_speech": latest.is_speech}


@router.get("/audio/transcribe")
async def audio_transcribe(
    seconds: float = Query(10.0, ge=1, le=120),
    x_api_key: Optional[str] = Header(None),
):
    """
    Transcribe los ultimos N segundos de audio.
    Usa Whisper local si esta disponible.
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.audio_buffer or not _srv._state.transcriber:
        raise HTTPException(503, "Audio not enabled or transcription unavailable")

    if not _srv._state.transcriber.available:
        return {"text": "", "error": "No transcription engine. Install: pip install faster-whisper"}

    chunks = _srv._state.audio_buffer.get_latest(seconds)
    speech_chunks = [c for c in chunks if c.is_speech]

    if not speech_chunks:
        return {"text": "", "note": "No speech detected in last " + str(seconds) + "s"}

    result = _srv._state.transcriber.transcribe_chunks(speech_chunks)
    if _srv._state.audio_interrupt:
        try:
            text = str(result.get("text", "") or "")
            if text.strip():
                result["interrupt_signal"] = _srv._state.audio_interrupt.ingest_transcript(
                    text=text,
                    source="audio_transcribe",
                )
        except Exception as e:
            _srv.logger.debug("Audio interrupt transcript ingest failed: %s", e)
    return result


@router.get("/audio/wav")
async def audio_wav(
    seconds: float = Query(10.0, ge=1, le=60),
    x_api_key: Optional[str] = Header(None),
):
    """Retorna audio WAV de los ultimos N segundos (para debug/playback)."""
    _srv._check_auth(x_api_key)
    if not _srv._state.audio_buffer:
        raise HTTPException(503, "Audio not enabled")

    wav_data = _srv._state.audio_buffer.get_audio_wav(seconds)
    if not wav_data:
        raise HTTPException(404, "No audio in buffer")

    return Response(content=wav_data, media_type="audio/wav")


@router.get("/audio/devices")
async def audio_devices(x_api_key: Optional[str] = Header(None)):
    """Lista dispositivos de audio disponibles."""
    _srv._check_auth(x_api_key)
    if not _srv._state.audio_capture:
        return {"devices": []}
    return {"devices": _srv._state.audio_capture.get_devices()}


@router.get("/audio/interrupt/status")
async def audio_interrupt_status(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.audio_interrupt:
        return {"enabled": False, "blocked": False, "reason": "audio_interrupt_unavailable"}
    status = _srv._state.audio_interrupt.status()
    status["enabled"] = True
    status["recent_events"] = _srv._state.audio_interrupt.recent_events(limit=12)
    return status


@router.post("/audio/interrupt/ack")
async def audio_interrupt_ack(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.audio_interrupt:
        return {"acknowledged": False, "reason": "audio_interrupt_unavailable"}
    return _srv._state.audio_interrupt.acknowledge()


@router.post("/audio/interrupt/feed")
async def audio_interrupt_feed(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Manual interrupt feed for testing/integration.
    Body: {text, confidence?, source?}
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.audio_interrupt:
        raise HTTPException(503, "Audio interrupt detector not initialized")
    text = str(request_body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    confidence = float(request_body.get("confidence", 0.8))
    source = str(request_body.get("source") or "manual")
    result = _srv._state.audio_interrupt.ingest_transcript(
        text=text,
        confidence=confidence,
        source=source,
    )
    return {
        "ok": True,
        "result": result,
        "status": _srv._state.audio_interrupt.status(),
    }


