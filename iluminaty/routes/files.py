"""Route module — files."""
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

# ─── Capa 5: File System ───

@router.get("/files/read")
async def files_read(path: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.filesystem:
        return {"success": False}
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _srv._state.filesystem.read_file(path))


class _FileWriteBody(BaseModel):
    path: str
    content: str

@router.post("/files/write")
async def files_write(
    body: _FileWriteBody,
    x_api_key: Optional[str] = Header(None),
):
    """M-2 fix: content in request body (not URL query param) to avoid log exposure."""
    _srv._check_auth(x_api_key)
    if not _srv._state.filesystem:
        return {"success": False}
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _srv._state.filesystem.write_file(body.path, body.content))


@router.get("/files/list")
async def files_list(
    path: str = Query("."),
    pattern: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.filesystem:
        return {"success": False}
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _srv._state.filesystem.list_dir(path, pattern))


@router.get("/files/search")
async def files_search(
    pattern: str = Query("*"),
    contains: Optional[str] = Query(None),
    path: str = Query("."),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.filesystem:
        return {"success": False}
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _srv._state.filesystem.search_files(pattern, contains, path))


@router.delete("/files/delete")
async def files_delete(path: str = Query(...), x_api_key: Optional[str] = Header(None)):
    """DESTRUCTIVE: elimina un archivo."""
    _srv._check_auth(x_api_key)
    return _srv._state.filesystem.delete_file(path) if _srv._state.filesystem else {"success": False}


