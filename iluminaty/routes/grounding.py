"""Route module — grounding."""
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

# ─── Grounding (Hybrid UI+Text Targeting) ───

@router.get("/grounding/status")
async def grounding_status(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.grounding:
        raise HTTPException(503, "Grounding engine not initialized")
    return _srv._state.grounding.status()


@router.post("/grounding/resolve")
async def grounding_resolve(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.grounding:
        raise HTTPException(503, "Grounding engine not initialized")
    query = (request_body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query is required")
    role = request_body.get("role")
    monitor_id = request_body.get("monitor_id")
    mode = request_body.get("mode") or _srv._state.operating_mode
    category = request_body.get("category") or "normal"
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = request_body.get("max_staleness_ms")
    top_k = int(request_body.get("top_k", 5))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _srv._state.grounding.resolve,
            query=query,
            role=role,
            monitor_id=monitor_id,
            mode=mode,
            category=category,
            context_tick_id=context_tick_id,
            max_staleness_ms=max_staleness_ms,
            top_k=top_k,
        )
        try:
            return future.result(timeout=8.0)
        except concurrent.futures.TimeoutError:
            return {
                "success": False,
                "blocked": True,
                "reason": "grounding_timeout",
                "target": None,
                "candidates": [],
                "world_ref": {},
            }


@router.post("/grounding/click")
async def grounding_click(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.grounding:
        raise HTTPException(503, "Grounding engine not initialized")
    query = (request_body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query is required")
    mode = request_body.get("mode") or _srv._state.operating_mode
    category = request_body.get("category") or "normal"
    verify = bool(request_body.get("verify", True))
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = int(request_body.get("max_staleness_ms", 1500))
    resolved = _srv._state.grounding.resolve(
        query=query,
        role=request_body.get("role"),
        monitor_id=request_body.get("monitor_id"),
        mode=mode,
        category=category,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
        top_k=int(request_body.get("top_k", 5)),
    )
    if resolved.get("blocked"):
        return {
            "grounding": resolved,
            "execution": None,
            "success": False,
            "message": resolved.get("reason", "grounding_blocked"),
        }

    target = resolved.get("target") or {}
    center = target.get("center_xy") or []
    if not isinstance(center, (list, tuple)) or len(center) != 2:
        return {
            "grounding": resolved,
            "execution": None,
            "success": False,
            "message": "grounding_target_missing_coordinates",
        }

    intent = Intent(
        action="click",
        params={
            "x": int(center[0]),
            "y": int(center[1]),
            "button": request_body.get("button", "left"),
        },
        confidence=1.0,
        raw_input=query,
        category=(category or "normal"),
    )
    execution = await _srv._execute_intent(
        intent,
        mode=mode,
        verify=verify,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
    )
    return {
        "grounding": resolved,
        "execution": execution,
        "success": bool(execution.get("result", {}).get("success")),
    }


@router.post("/grounding/type")
async def grounding_type(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.grounding:
        raise HTTPException(503, "Grounding engine not initialized")
    query = (request_body.get("query") or "").strip()
    text = request_body.get("text")
    if not query:
        raise HTTPException(400, "query is required")
    if text is None:
        raise HTTPException(400, "text is required")
    mode = request_body.get("mode") or _srv._state.operating_mode
    verify = bool(request_body.get("verify", True))
    category = request_body.get("category") or "normal"
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = int(request_body.get("max_staleness_ms", 1500))

    resolved = _srv._state.grounding.resolve(
        query=query,
        role=request_body.get("role") or "textfield",
        monitor_id=request_body.get("monitor_id"),
        mode=mode,
        category=category,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
        top_k=int(request_body.get("top_k", 5)),
    )
    if resolved.get("blocked"):
        return {
            "grounding": resolved,
            "click_execution": None,
            "type_execution": None,
            "success": False,
            "message": resolved.get("reason", "grounding_blocked"),
        }

    target = resolved.get("target") or {}
    center = target.get("center_xy") or []
    if not isinstance(center, (list, tuple)) or len(center) != 2:
        return {
            "grounding": resolved,
            "click_execution": None,
            "type_execution": None,
            "success": False,
            "message": "grounding_target_missing_coordinates",
        }

    click_exec = await _srv._execute_intent(
        Intent(
            action="click",
            params={"x": int(center[0]), "y": int(center[1]), "button": "left"},
            confidence=1.0,
            raw_input=query,
            category=(category or "normal"),
        ),
        mode=mode,
        verify=verify,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
    )
    if not click_exec.get("result", {}).get("success"):
        return {
            "grounding": resolved,
            "click_execution": click_exec,
            "type_execution": None,
            "success": False,
            "message": click_exec.get("result", {}).get("message", "click_failed"),
        }

    type_exec = await _srv._execute_intent(
        Intent(
            action="type_text",
            params={"text": str(text)},
            confidence=1.0,
            raw_input=f"type:{query}",
            category=(category or "normal"),
        ),
        mode=mode,
        verify=verify,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
    )
    return {
        "grounding": resolved,
        "click_execution": click_exec,
        "type_execution": type_exec,
        "success": bool(type_exec.get("result", {}).get("success")),
    }


