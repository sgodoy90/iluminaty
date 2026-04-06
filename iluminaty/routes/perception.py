"""Route module — perception."""
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

# ─── Perception (Real-Time Vision) ───

@router.get("/perception")
async def perception_summary(
    seconds: float = Query(30, ge=1, le=300),
    x_api_key: Optional[str] = Header(None),
):
    """Get real-time perception events — what happened on screen."""
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return {
        "summary": _srv._state.perception.get_summary(seconds),
        "event_count": _srv._state.perception.get_event_count(),
        "running": _srv._state.perception.is_running,
    }


@router.get("/perception/events")
async def perception_events(
    seconds: float = Query(30, ge=1, le=300),
    min_importance: float = Query(0.0, ge=0.0, le=1.0),
    x_api_key: Optional[str] = Header(None),
):
    """Get raw perception events."""
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    events = _srv._state.perception.get_events(seconds, min_importance)
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

@router.get("/perception/state")
async def perception_state(x_api_key: Optional[str] = Header(None)):
    """Full IPA introspection — scene states, attention, ROIs, predictor."""
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return _srv._state.perception.get_state()


@router.get("/perception/attention")
async def perception_attention(x_api_key: Optional[str] = Header(None)):
    """Attention heatmap grid (8x6 float values) for dashboard visualization."""
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return {
        "grid": _srv._state.perception.get_attention_heatmap(),
        "rows": 6,
        "cols": 8,
        "hot_zones": _srv._state.perception.get_state().get("attention_hot_zones", []),
    }


@router.get("/perception/world")
async def perception_world(x_api_key: Optional[str] = Header(None)):
    """IPA v2 semantic snapshot (WorldState)."""
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return _srv._state.perception.get_world_state()


@router.get("/perception/trace")
async def perception_trace(
    seconds: float = Query(90, ge=1, le=600),
    x_api_key: Optional[str] = Header(None),
):
    """Compressed semantic transitions kept in RAM."""
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    bundle = _srv._state.perception.get_world_trace_bundle(seconds=seconds)
    trace = bundle.get("trace", [])
    temporal = bundle.get("temporal", {})
    return {
        "trace": trace,
        "temporal": temporal,
        "count": len(trace),
        "seconds": seconds,
    }


@router.get("/perception/readiness")
async def perception_readiness(x_api_key: Optional[str] = Header(None)):
    """Whether perception has enough context to execute actions safely."""
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return _srv._state.perception.get_readiness()


@router.get("/domain-packs")
async def domain_packs_list(x_api_key: Optional[str] = Header(None)):
    """List available domain packs, active selection, and override state."""
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    if not hasattr(_srv._state.perception, "list_domain_packs"):
        raise HTTPException(501, "Domain packs are not available in this build")
    return _srv._state.perception.list_domain_packs()


@router.post("/domain-packs/reload")
async def domain_packs_reload(x_api_key: Optional[str] = Header(None)):
    """Reload custom domain packs from configured directory."""
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    if not hasattr(_srv._state.perception, "reload_domain_packs"):
        raise HTTPException(501, "Domain packs are not available in this build")
    return _srv._state.perception.reload_domain_packs()


@router.post("/domain-packs/override")
async def domain_packs_override(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Force domain pack selection or switch back to auto mode.
    Body: {"name": "trading"} or {"name":"auto"}.
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    if not hasattr(_srv._state.perception, "set_domain_override"):
        raise HTTPException(501, "Domain packs are not available in this build")
    name = request_body.get("name")
    result = _srv._state.perception.set_domain_override(name)
    if not bool(result.get("ok", False)):
        raise HTTPException(400, result.get("reason", "invalid_domain_pack"))
    return result


@router.post("/perception/query")
async def perception_query(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Query temporal visual context.
    Body: {question, at_ms?, window_seconds?, monitor_id?}
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    question = (request_body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question is required")
    at_ms = request_body.get("at_ms")
    window_seconds = float(request_body.get("window_seconds", 30))
    monitor_id = request_body.get("monitor_id")
    return _srv._state.perception.query_visual(
        question=question,
        at_ms=at_ms,
        window_seconds=window_seconds,
        monitor_id=monitor_id,
    )


