"""Route module — process."""
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

# ─── Capa 1: Process Manager ───

@router.get("/process/list")
async def process_list(
    sort_by: str = Query("memory"),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    return {"processes": _srv._state.process_mgr.list_processes(sort_by) if _srv._state.process_mgr else []}


@router.get("/process/find")
async def process_find(name: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    return {"matches": _srv._state.process_mgr.find_process(name) if _srv._state.process_mgr else []}


@router.post("/process/launch")
async def process_launch(command: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    return _srv._state.process_mgr.launch(command) if _srv._state.process_mgr else {"success": False}


@router.post("/process/terminate")
async def process_terminate(
    name: Optional[str] = Query(None),
    pid: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """DESTRUCTIVE: termina un proceso."""
    _srv._check_auth(x_api_key)
    return _srv._state.process_mgr.terminate(pid=pid, name=name) if _srv._state.process_mgr else {"success": False}


