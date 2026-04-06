"""Route module — safety."""
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

# ─── Capa 7: Safety / Autonomy / Audit ───

@router.get("/safety/status")
async def safety_status(x_api_key: Optional[str] = Header(None)):
    """Estado del sistema de seguridad."""
    _srv._check_auth(x_api_key)
    return {
        "safety": _srv._state.safety.stats if _srv._state.safety else {},
        "audit": _srv._state.audit.stats if _srv._state.audit else {},
    }


@router.post("/safety/kill")
async def safety_kill(x_api_key: Optional[str] = Header(None)):
    """KILL SWITCH: detiene toda actividad del agente."""
    _srv._check_auth(x_api_key)
    if _srv._state.safety:
        _srv._state.safety.kill()
    if _srv._state.actions:
        _srv._state.actions.disable()
    return {"killed": True}


@router.post("/safety/resume")
async def safety_resume(x_api_key: Optional[str] = Header(None)):
    """Reactiva el agente despues de un kill."""
    _srv._check_auth(x_api_key)
    if _srv._state.safety:
        _srv._state.safety.resume()
    return {"killed": False}


@router.get("/safety/whitelist")
async def safety_whitelist(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    return {"whitelist": _srv._state.safety.get_whitelist() if _srv._state.safety else []}


@router.get("/audit/recent")
async def audit_recent(
    count: int = Query(20, ge=1, le=100),
    x_api_key: Optional[str] = Header(None),
):
    """Ultimas entradas del audit log."""
    _srv._check_auth(x_api_key)
    if not _srv._state.audit:
        return {"entries": []}
    return {"entries": _srv._state.audit.get_recent(count)}


@router.get("/audit/failures")
async def audit_failures(
    count: int = Query(20, ge=1, le=100),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    return {"entries": _srv._state.audit.get_failures(count) if _srv._state.audit else []}


