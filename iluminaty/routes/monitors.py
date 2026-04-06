"""Route module — monitors."""
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

# ─── Monitors (F10) ───

@router.get("/monitors")
async def monitors_info(x_api_key: Optional[str] = Header(None)):
    """Info de todos los monitores."""
    _srv._check_auth(x_api_key)
    if not _srv._state.monitor_mgr:
        raise HTTPException(503, "Not initialized")
    _srv._state.monitor_mgr.refresh()
    return _srv._state.monitor_mgr.to_dict()


@router.get("/monitors/info")
async def monitors_info_alias(x_api_key: Optional[str] = Header(None)):
    """Alias compat para monitor info."""
    return await monitors_info(x_api_key=x_api_key)


@router.post("/monitors/refresh")
async def monitors_refresh(x_api_key: Optional[str] = Header(None)):
    """
    Force re-detection of monitor layout. Call this after:
    - Plugging/unplugging a monitor (Linux/Mac — Windows auto-detects via WM_DISPLAYCHANGE)
    - Changing resolution or orientation in display settings
    - Connecting via remote desktop or virtual machine
    Returns the new monitor layout immediately.
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.monitor_mgr:
        raise HTTPException(503, "Not initialized")
    _srv._state.monitor_mgr.refresh()
    if _srv._state.perception:
        try:
            _srv._state.perception.reinitialize_monitors()
        except Exception:
            pass
    return {
        "refreshed": True,
        "monitors": _srv._state.monitor_mgr.to_dict().get("monitors", []),
        "count": len(_srv._state.monitor_mgr._monitors),
    }


@router.get("/spatial/state")
async def spatial_state(
    include_windows: bool = Query(True),
    x_api_key: Optional[str] = Header(None),
):
    """
    Unified desktop spatial snapshot for 1..N monitors.
    Coordinates are always in virtual-desktop space.
    """
    _srv._check_auth(x_api_key)
    monitors = _srv._monitor_layout_snapshot()
    active_monitor_id = _srv._resolve_active_monitor_id()
    active_window = await windows_active(x_api_key=x_api_key)
    windows_payload = {"windows": []}
    if include_windows:
        windows_payload = await windows_list(
            monitor_id=None,
            visible_only=True,
            exclude_minimized=False,
            exclude_system=True,
            title_contains=None,
            x_api_key=x_api_key,
        )
    windows = windows_payload.get("windows", [])
    grouped: dict[str, list[dict]] = {}
    for w in windows:
        grouped.setdefault(str(w.get("monitor_id", 0)), []).append(w)

    cursor = {}
    if _srv._state.actions:
        try:
            cursor = _srv._state.actions.get_mouse_position()
        except Exception:
            cursor = {}

    return {
        "timestamp_ms": int(time.time() * 1000),
        "monitor_count": len(monitors),
        "active_monitor_id": int(active_monitor_id) if active_monitor_id is not None else None,
        "monitors": monitors,
        "active_window": active_window or {},
        "cursor": cursor,
        "windows": windows,
        "windows_by_monitor": grouped,
    }


