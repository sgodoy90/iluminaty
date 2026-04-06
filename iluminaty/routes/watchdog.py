"""Route module — watchdog."""
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

# ─── Watchdog (E01) ───

@router.get("/watchdog/alerts")
async def watchdog_alerts(
    count: int = Query(20, ge=1, le=100),
    unacknowledged: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    """Get watchdog alerts."""
    _srv._check_auth(x_api_key)
    if not _srv._state.watchdog:
        return {"alerts": []}
    return {"alerts": _srv._state.watchdog.get_alerts(count, unacknowledged)}


@router.get("/watchdog/triggers")
async def watchdog_triggers(x_api_key: Optional[str] = Header(None)):
    """List all watchdog triggers."""
    _srv._check_auth(x_api_key)
    if not _srv._state.watchdog:
        return {"triggers": []}
    return {"triggers": _srv._state.watchdog.get_triggers(), "stats": _srv._state.watchdog.stats}


@router.post("/watchdog/scan")
async def watchdog_scan(x_api_key: Optional[str] = Header(None)):
    """Manually trigger a watchdog scan on current screen."""
    _srv._check_auth(x_api_key)
    if not _srv._state.watchdog or not _srv._state.buffer:
        raise HTTPException(503, "Not initialized")

    slot = _srv._state.buffer.get_latest()
    if not slot:
        return {"alerts": [], "note": "No frames in buffer"}

    # Get OCR text and window title for scanning
    from .vision import get_active_window_info
    win = get_active_window_info()
    ocr_text = ""
    if _srv._state.vision and _srv._state.vision.ocr.available:
        ocr_result = _srv._state.vision.ocr.extract_text(
            slot.frame_bytes, frame_hash=slot.phash,
            monitor_id=getattr(slot, "monitor_id", None),
        )
        ocr_text = ocr_result.get("text", "")

    alerts = _srv._state.watchdog.scan(ocr_text=ocr_text, window_title=win.get("title", ""))
    return {
        "new_alerts": [a.to_dict() for a in alerts],
        "total_alerts": _srv._state.watchdog.stats["total_alerts"],
    }


@router.post("/watchdog/acknowledge/{alert_id}")
async def watchdog_ack(alert_id: str, x_api_key: Optional[str] = Header(None)):
    """Acknowledge an alert."""
    _srv._check_auth(x_api_key)
    if not _srv._state.watchdog:
        raise HTTPException(503, "Not initialized")
    return {"acknowledged": _srv._state.watchdog.acknowledge(alert_id)}


