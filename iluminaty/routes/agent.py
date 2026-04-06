"""Route module — agent."""
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

# ─── Capa 6: Agent / Orchestration ───

@router.post("/agent/do")
async def agent_do(
    instruction: str = Query(..., description="Natural language instruction"),
    x_api_key: Optional[str] = Header(None),
):
    """
    Intent-based action: la IA interpreta y ejecuta.
    "guarda el archivo" → clasifica → resuelve → verifica
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.intent or not _srv._state.resolver:
        raise HTTPException(503, "Orchestration not initialized")
    intent = _srv._state.intent.classify_or_default(instruction)
    return await _srv._execute_intent(intent, mode=_srv._state.operating_mode, verify=True)





@router.get("/agent/status")
async def agent_status(x_api_key: Optional[str] = Header(None)):
    """Estado completo del agente."""
    _srv._check_auth(x_api_key)
    return {
        "operating_mode": {"mode": _srv._state.operating_mode},
        "runtime_profile": {
            "profile": _srv._normalize_runtime_profile(_srv._state.runtime_profile),
            "policy": _srv._runtime_profile_policy(_srv._state.runtime_profile),
        },
        "actions": _srv._state.actions.stats if _srv._state.actions else {},
        "safety": _srv._state.safety.stats if _srv._state.safety else {},
        "resolver": _srv._state.resolver.stats if _srv._state.resolver else {},
        "intent": _srv._state.intent.stats if _srv._state.intent else {},
        "recovery": _srv._state.recovery.stats if _srv._state.recovery else {},
        "grounding": _srv._state.grounding.status() if _srv._state.grounding else {},
        "behavior_cache": _srv._state.behavior_cache.stats() if _srv._state.behavior_cache else {"enabled": False},
        "audio_interrupt": _srv._state.audio_interrupt.status() if _srv._state.audio_interrupt else {"enabled": False},
        "workers_schedule": _srv._state.perception.get_workers_schedule() if (_srv._state.perception and hasattr(_srv._state.perception, "get_workers_schedule")) else {},
        "perception_readiness": _srv._state.perception.get_readiness() if _srv._state.perception else {},
        "bootstrap_warnings": list(_srv._state.bootstrap_warnings),
    }


