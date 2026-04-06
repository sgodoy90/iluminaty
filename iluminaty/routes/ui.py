"""Route module — ui."""
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

# ─── Capa 2: UI Tree ───

@router.get("/ui/elements")
async def ui_elements(
    pid: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    return {"elements": _srv._state.ui_tree.get_elements(pid=pid) if _srv._state.ui_tree else []}


@router.get("/ui/find")
async def ui_find(
    name: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.ui_tree:
        return {"element": None}
    import asyncio
    loop = asyncio.get_event_loop()
    element = await loop.run_in_executor(None, lambda: _srv._state.ui_tree.find_element(name=name, role=role))
    return {"element": element}


@router.get("/ui/find_all")
async def ui_find_all(
    name: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.ui_tree:
        return {"elements": []}
    import asyncio
    loop = asyncio.get_event_loop()
    elements = await loop.run_in_executor(None, lambda: _srv._state.ui_tree.find_all(name=name, role=role))
    return {"elements": elements}


@router.get("/locate")
async def smart_locate_endpoint(
    query: str = Query(..., description="Natural language target description"),
    monitor_id: Optional[int] = Query(None),
    role: Optional[str] = Query(None, description="Role hint: button|edit|link|checkbox|combobox"),
    x_api_key: Optional[str] = Header(None),
):
    """Smart coordinate resolver — UITree + OCR fusion.

    Returns exact (x,y) coordinates for any visible UI element without
    asking the AI to guess. Used internally by act() when target= is provided.

    Latency: UITree ~2ms, OCR ~5ms (uses cached last frame, no new capture).
    Response: {found, x, y, w, h, source, confidence, label, method_detail}
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.smart_locator:
        return {"found": False, "reason": "smart_locator_not_initialized"}

    # Refresh monitor bounds
    if _srv._state.monitor_mgr:
        _srv._state.smart_locator.update_monitor_bounds({
            m.id: {"left": m.left, "top": m.top, "width": m.width, "height": m.height}
            for m in _srv._state.monitor_mgr.monitors
        })

    # Get active window PID for UITree filtering
    active_pid = None
    try:
        if _srv._state.perception:
            win_info = _srv._state.perception._last_window_info or {}
            active_pid = win_info.get("pid") or None
    except Exception:
        pass

    # Inject OCR blocks from perception pipeline — ZERO extra OCR computation
    # Perception runs OCR every few seconds in background; we just read the results
    try:
        if _srv._state.perception and _srv._state.monitor_mgr:
            import time as _t
            now = _t.perf_counter()
            for mon in _srv._state.monitor_mgr.monitors:
                mid = mon.id
                mon_state = _srv._state.perception._get_monitor_state(mid)
                if not mon_state:
                    continue
                blocks = list(getattr(mon_state, 'last_ocr_blocks', []))
                if not blocks:
                    continue  # no OCR yet — will find via UITree or return not-found
                # Translate from monitor-relative to global desktop coords
                global_blocks = []
                for b in blocks:
                    gb = dict(b)
                    gb["x"] = b.get("x", 0) + mon.left
                    gb["y"] = b.get("y", 0) + mon.top
                    global_blocks.append(gb)
                _srv._state.smart_locator._ocr_cache[mid] = {
                    "ts": now,
                    "blocks": global_blocks,
                    "text": mon_state.last_ocr_text,
                }
    except Exception:
        pass

    # Execute locate synchronously
    # UITree is disabled if no OCR blocks available to keep latency <50ms
    has_ocr = bool(_srv._state.smart_locator._ocr_cache)
    result = _srv._state.smart_locator.locate(
        query=query,
        monitor_id=monitor_id,
        prefer_role=role,
        # Pass "skip_tree" when OCR not ready to avoid blocking PowerShell spawn
        active_window_pid=active_pid if has_ocr else "skip_tree",
    )

    if result is None:
        # If no OCR data yet, include a hint so the AI knows to try again later
        hint = "" if has_ocr else " OCR not ready yet — retry after 10s or use see_now."
        return {"found": False, "reason": "not_found", "query": query, "hint": hint}

    return {"found": True, **result.to_dict()}


@router.post("/ui/click")
async def ui_click(
    name: str = Query(...),
    role: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        raise HTTPException(503, "Actions not initialized")
    return _srv._state.actions.click_element(name, role).to_dict()


@router.post("/ui/type")
async def ui_type(
    field: str = Query(...),
    text: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        raise HTTPException(503, "Actions not initialized")
    return _srv._state.actions.type_in_field(field, text).to_dict()


