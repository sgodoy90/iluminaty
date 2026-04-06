"""Route module — watch."""
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

# ─── Watch Engine endpoints ───

@router.post("/watch/notify")
async def watch_and_notify(
    condition: str = Query(..., description="Condition to watch for"),
    timeout: float = Query(30.0),
    text: Optional[str] = Query(None),
    window_title: Optional[str] = Query(None),
    element: Optional[str] = Query(None),
    idle_seconds: float = Query(3.0),
    monitor_id: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """Wait for a screen condition without consuming tokens.
    Runs synchronously — returns when condition met or timeout expires.
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.watch_engine:
        return {"triggered": False, "reason": "watch_engine_not_initialized"}

    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _srv._state.watch_engine.wait(
            condition=condition, timeout=timeout,
            text=text, window_title=window_title,
            element=element, idle_seconds=idle_seconds,
            monitor_id=monitor_id,
        )
    )
    return result.to_dict()


@router.post("/watch/until")
async def monitor_until(
    condition: str = Query(...),
    timeout: float = Query(120.0),
    text: Optional[str] = Query(None),
    window_title: Optional[str] = Query(None),   # Bug 3 fix: explicit window_title param
    element: Optional[str] = Query(None),
    monitor_id: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """Wait until a condition is met. Returns immediately when triggered.

    Conditions: window_opened, window_closed, text_appeared, text_disappeared,
                page_loaded, motion_started, motion_stopped, idle, build_passed,
                build_failed, element_visible

    window_opened / window_closed: use window_title= param (title substring match).
    text_appeared / text_disappeared: use text= param (OCR-based).
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.watch_engine:
        return {"triggered": False, "reason": "watch_engine_not_initialized"}
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _srv._state.watch_engine.wait(
            condition=condition, timeout=timeout,
            text=text, window_title=window_title,
            element=element, monitor_id=monitor_id,
        )
    )
    return result.to_dict()


