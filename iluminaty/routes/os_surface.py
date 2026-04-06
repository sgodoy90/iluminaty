"""Route module — os_surface."""
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

# ─── OS Surface ───

@router.get("/os/notifications")
async def os_notifications(
    limit: int = Query(20, ge=1, le=200),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.os_surface:
        return {"available": False, "count": 0, "items": [], "sources": []}
    payload = _srv._state.os_surface.notifications(limit=limit)
    payload["available"] = True
    return payload


@router.get("/os/tray")
async def os_tray(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.os_surface:
        return {"available": False, "supported": False, "detected": False, "windows": []}
    payload = _srv._state.os_surface.tray_state()
    payload["available"] = True
    return payload


@router.get("/os/dialog/status")
async def os_dialog_status(
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for dialog probe."),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    return _srv._os_dialog_status_snapshot(monitor_id=monitor_id)


@router.post("/os/dialog/resolve")
async def os_dialog_resolve(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Attempt to resolve an active dialog by clicking a target label/coordinate.
    Body: {label?, x?, y?, monitor_id?, mode?, verify?}
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        raise HTTPException(503, "Action bridge not initialized")

    monitor_id = request_body.get("monitor_id")
    status = _srv._os_dialog_status_snapshot(monitor_id=monitor_id)
    if not bool(status.get("detected", False)):
        return {
            "resolved": False,
            "reason": "dialog_not_detected",
            "dialog": status,
            "execution": None,
        }

    label = str(request_body.get("label") or "").strip()
    x = request_body.get("x")
    y = request_body.get("y")
    chosen_target = {"x": None, "y": None, "label": label or None}
    mode = request_body.get("mode") or _srv._state.operating_mode
    verify = bool(request_body.get("verify", True))
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = request_body.get("max_staleness_ms")
    if max_staleness_ms is not None:
        max_staleness_ms = int(max_staleness_ms)

    if x is None or y is None:
        if not label:
            affordances = status.get("affordances") or []
            if affordances:
                label = str(affordances[0]).strip()
                chosen_target["label"] = label
        if label and _srv._state.grounding:
            try:
                resolved = _srv._state.grounding.resolve(
                    query=label,
                    role="button",
                    monitor_id=monitor_id,
                    mode=mode,
                    category=str(request_body.get("category") or "normal"),
                    context_tick_id=context_tick_id,
                    max_staleness_ms=max_staleness_ms,
                    top_k=5,
                )
            except Exception:
                resolved = {}
            target = (resolved or {}).get("target") or {}
            center = target.get("center_xy") or []
            if isinstance(center, (list, tuple)) and len(center) == 2:
                x = int(center[0])
                y = int(center[1])

    if x is None or y is None:
        return {
            "resolved": False,
            "reason": "dialog_target_not_resolved",
            "dialog": status,
            "target": chosen_target,
            "execution": None,
        }

    chosen_target["x"] = int(x)
    chosen_target["y"] = int(y)
    intent = Intent(
        action="click",
        params={
            "x": int(x),
            "y": int(y),
            "button": str(request_body.get("button") or "left"),
        },
        confidence=1.0,
        raw_input=f"dialog_resolve:{chosen_target.get('label') or 'coords'}",
        category=str(request_body.get("category") or "normal"),
    )
    execution = await _srv._execute_intent(
        intent,
        mode=mode,
        verify=verify,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
    )
    success = bool((execution.get("result") or {}).get("success", False))
    return {
        "resolved": success,
        "reason": "ok" if success else str((execution.get("result") or {}).get("message", "dialog_resolve_failed")),
        "dialog": status,
        "target": chosen_target,
        "execution": execution,
    }


