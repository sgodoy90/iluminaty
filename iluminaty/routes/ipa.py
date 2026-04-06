"""Route module — ipa."""
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

# ─── IPA v3 endpoints ────────────────────────────────────────────────────────

@router.get("/ipa/status")
async def ipa_status(x_api_key: Optional[str] = Header(None)):
    """IPA v3 bridge status and engine stats."""
    _srv._check_auth(x_api_key)
    if not _srv._state.ipa_bridge:
        return {"running": False, "reason": "not_initialized"}
    return _srv._state.ipa_bridge.stats()


@router.get("/ipa/context")
async def ipa_context(
    seconds: float = Query(30.0),
    x_api_key: Optional[str] = Header(None),
):
    """IPA v3 visual context: motion, scene state, gate events, OCR hint."""
    _srv._check_auth(x_api_key)
    if not _srv._state.ipa_bridge:
        return {"error": "ipa_bridge_not_initialized"}

    ctx = _srv._state.ipa_bridge.visual_context(seconds=seconds) or {}
    gate = _srv._state.ipa_bridge.gate_event(max_age_s=seconds)
    motion = _srv._state.ipa_bridge.motion_now(seconds=5.0) or {}

    # OCR hint from perception engine
    ocr_hint = ""
    try:
        if _srv._state.perception:
            events = _srv._state.perception.get_events(seconds=10)
            for evt in reversed(events):
                txt = evt.details.get("ocr_text", "") if evt.details else ""
                if txt:
                    ocr_hint = txt[:300]
                    break
    except Exception:
        pass

    return {
        **ctx,
        "gate_event": gate.__dict__ if gate else None,
        "motion": motion,
        "ocr_hint": ocr_hint,
        "bridge_stats": _srv._state.ipa_bridge.stats(),
    }


@router.get("/ipa/events")
async def ipa_events(
    seconds: float = Query(30.0),
    x_api_key: Optional[str] = Header(None),
):
    """Recent IPA v3 gate events — significant visual changes."""
    _srv._check_auth(x_api_key)
    if not _srv._state.ipa_bridge:
        return {"error": "ipa_bridge_not_initialized", "events": []}
    events = _srv._state.ipa_bridge.recent_events(seconds=seconds)
    return {
        "events": [e.__dict__ for e in events],
        "count": len(events),
        "seconds": seconds,
    }


    x_api_key: Optional[str] = Header(None),



