"""Route module — windows."""
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

# ─── Capa 1: Windows ───

@router.get("/windows/list")
async def windows_list(
    monitor_id: Optional[int] = Query(None, description="Optional monitor filter"),
    visible_only: bool = Query(True),
    exclude_minimized: bool = Query(False),
    exclude_system: bool = Query(True),
    title_contains: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.windows:
        return {"windows": []}
    items = []
    title_filter = (title_contains or "").strip().lower()
    for w in _srv._state.windows.list_windows():
        if visible_only and not bool(getattr(w, "is_visible", True)):
            continue
        if exclude_minimized and bool(getattr(w, "is_minimized", False)):
            continue
        if title_filter and title_filter not in str(getattr(w, "title", "")).lower():
            continue
        payload = w.to_dict()
        mid = _srv._monitor_id_for_rect(w.x, w.y, w.width, w.height)
        if mid is not None:
            payload["monitor_id"] = int(mid)
        if exclude_system and _srv._is_system_noise_window(payload):
            continue
        items.append(payload)

    if monitor_id is not None:
        try:
            target_mid = int(monitor_id)
            items = [w for w in items if int(w.get("monitor_id", -1)) == target_mid]
        except Exception:
            items = []

    items.sort(
        key=lambda w: (
            not bool(w.get("is_visible", True)),
            bool(w.get("is_minimized", False)),
            -int(max(0, int(w.get("width", 0))) * max(0, int(w.get("height", 0)))),
        )
    )
    return {"windows": items}


@router.get("/windows/active")
async def windows_active(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.windows:
        return {}
    win = _srv._state.windows.get_active_window()
    if not win:
        return {}
    payload = win.to_dict()
    monitor_id = _srv._monitor_id_for_rect(win.x, win.y, win.width, win.height)
    if monitor_id is not None:
        payload["monitor_id"] = int(monitor_id)
    return payload


@router.post("/windows/focus")
async def windows_focus(
    title: Optional[str] = Query(None),
    handle: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.windows:
        raise HTTPException(503, "Not initialized")
    if handle is None and not (title and title.strip()):
        raise HTTPException(400, "Either title or handle is required")
    return {"success": _srv._state.windows.focus_window(title=title, handle=handle)}


@router.post("/windows/minimize")
async def windows_minimize(
    title: Optional[str] = Query(None),
    handle: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if handle is None and not (title and title.strip()):
        raise HTTPException(400, "Either title or handle is required")
    return {"success": _srv._state.windows.minimize_window(title=title, handle=handle) if _srv._state.windows else False}


@router.post("/windows/maximize")
async def windows_maximize(
    title: Optional[str] = Query(None),
    handle: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if handle is None and not (title and title.strip()):
        raise HTTPException(400, "Either title or handle is required")
    return {"success": _srv._state.windows.maximize_window(title=title, handle=handle) if _srv._state.windows else False}


@router.post("/windows/close")
async def windows_close(
    title: Optional[str] = Query(None),
    handle: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """DESTRUCTIVE: cierra una ventana."""
    _srv._check_auth(x_api_key)
    if handle is None and not (title and title.strip()):
        raise HTTPException(400, "Either title or handle is required")
    return {"success": _srv._state.windows.close_window(title=title, handle=handle) if _srv._state.windows else False}


@router.post("/windows/move")
async def windows_move(
    title: Optional[str] = Query(None),
    handle: Optional[int] = Query(None),
    x: int = Query(...), y: int = Query(...),
    width: int = Query(-1), height: int = Query(-1),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for monitor-local x/y"),
    relative_to_monitor: bool = Query(False, description="If true, x/y are relative to monitor origin"),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if handle is None and not (title and title.strip()):
        raise HTTPException(400, "Either title or handle is required")
    rx, ry = _srv._translate_click_coords(int(x), int(y), monitor_id, bool(relative_to_monitor))
    success = _srv._state.windows.move_window(rx, ry, width, height, title=title, handle=handle) if _srv._state.windows else False
    return {
        "success": bool(success),
        "requested_x": int(x),
        "requested_y": int(y),
        "resolved_x": int(rx),
        "resolved_y": int(ry),
        "monitor_id": int(monitor_id) if monitor_id is not None else None,
        "relative_to_monitor": bool(relative_to_monitor),
    }


