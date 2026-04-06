"""Route module — clipboard."""
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

# ─── Capa 1: Clipboard ───

@router.get("/clipboard/read")
async def clipboard_read(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.clipboard:
        return {"text": ""}
    import asyncio
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, _srv._state.clipboard.read)
    return {"text": text}


@router.post("/clipboard/write")
async def clipboard_write(text: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.clipboard:
        return {"success": False}
    import asyncio
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, lambda: _srv._state.clipboard.write(text))
    return {"success": ok}


@router.get("/clipboard/history")
async def clipboard_history(count: int = Query(20), x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.clipboard:
        return {"history": []}
    import asyncio
    loop = asyncio.get_event_loop()
    history = await loop.run_in_executor(None, lambda: _srv._state.clipboard.get_history(count))
    return {"history": history}


