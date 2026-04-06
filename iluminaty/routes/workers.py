"""Route module — workers."""
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

# ─── Workers System (v1) ───

@router.get("/workers/status")
async def workers_status(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.perception or not hasattr(_srv._state.perception, "get_workers_status"):
        raise HTTPException(503, "Workers system not initialized")
    return _srv._state.perception.get_workers_status()


@router.get("/workers/monitor/{monitor_id}")
async def workers_monitor(monitor_id: int, x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.perception or not hasattr(_srv._state.perception, "get_worker_monitor"):
        raise HTTPException(503, "Workers system not initialized")
    payload = _srv._state.perception.get_worker_monitor(int(monitor_id))
    if not payload:
        raise HTTPException(404, f"Monitor {int(monitor_id)} has no worker digest yet")
    return payload


@router.post("/workers/action/claim")
async def workers_action_claim(request_body: dict, x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.perception or not hasattr(_srv._state.perception, "claim_action_lease"):
        raise HTTPException(503, "Workers system not initialized")
    owner = str(request_body.get("owner") or "external-executor")
    ttl_ms = request_body.get("ttl_ms")
    force = bool(request_body.get("force", False))
    return _srv._state.perception.claim_action_lease(owner=owner, ttl_ms=ttl_ms, force=force)


@router.post("/workers/action/release")
async def workers_action_release(request_body: dict, x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.perception or not hasattr(_srv._state.perception, "release_action_lease"):
        raise HTTPException(503, "Workers system not initialized")
    owner = str(request_body.get("owner") or "external-executor")
    success = bool(request_body.get("success", True))
    message = str(request_body.get("message") or "")
    return _srv._state.perception.release_action_lease(owner=owner, success=success, message=message)


@router.get("/workers/schedule")
async def workers_schedule(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.perception or not hasattr(_srv._state.perception, "get_workers_schedule"):
        raise HTTPException(503, "Workers scheduler not initialized")
    return _srv._state.perception.get_workers_schedule()


@router.get("/workers/subgoals")
async def workers_subgoals(
    include_completed: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.perception or not hasattr(_srv._state.perception, "list_worker_subgoals"):
        raise HTTPException(503, "Workers scheduler not initialized")
    items = _srv._state.perception.list_worker_subgoals(include_completed=include_completed)
    return {"subgoals": items, "count": len(items)}


@router.post("/workers/subgoals")
async def workers_set_subgoal(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.perception or not hasattr(_srv._state.perception, "set_worker_subgoal"):
        raise HTTPException(503, "Workers scheduler not initialized")
    monitor_id = request_body.get("monitor_id")
    goal = str(request_body.get("goal") or "").strip()
    if monitor_id is None:
        raise HTTPException(400, "monitor_id is required")
    if not goal:
        raise HTTPException(400, "goal is required")
    return _srv._state.perception.set_worker_subgoal(
        monitor_id=int(monitor_id),
        goal=goal,
        priority=float(request_body.get("priority", 0.5)),
        risk=str(request_body.get("risk", "normal")),
        deadline_ms=request_body.get("deadline_ms"),
        metadata=request_body.get("metadata") or {},
    )


@router.delete("/workers/subgoals/{subgoal_id}")
async def workers_clear_subgoal(
    subgoal_id: str,
    completed: bool = Query(True),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.perception or not hasattr(_srv._state.perception, "clear_worker_subgoal"):
        raise HTTPException(503, "Workers scheduler not initialized")
    result = _srv._state.perception.clear_worker_subgoal(subgoal_id, completed=completed)
    if not bool(result.get("ok", False)):
        raise HTTPException(404, result.get("reason", "subgoal_not_found"))
    return result


@router.post("/workers/route")
async def workers_route(request_body: dict, x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.perception or not hasattr(_srv._state.perception, "route_worker_query"):
        raise HTTPException(503, "Workers scheduler not initialized")
    query = str(request_body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query is required")
    preferred_monitor_id = request_body.get("preferred_monitor_id")
    return _srv._state.perception.route_worker_query(query, preferred_monitor_id=preferred_monitor_id)


@router.get("/behavior/stats")
async def behavior_stats(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.behavior_cache:
        return {"enabled": False, "reason": "behavior_cache_unavailable"}
    payload = _srv._state.behavior_cache.stats()
    payload["enabled"] = True
    return payload


@router.get("/behavior/recent")
async def behavior_recent(
    limit: int = Query(20, ge=1, le=200),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.behavior_cache:
        return {"enabled": False, "entries": []}
    return {
        "enabled": True,
        "entries": _srv._state.behavior_cache.recent(limit=limit),
    }


@router.post("/behavior/suggest")
async def behavior_suggest(request_body: dict, x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.behavior_cache:
        raise HTTPException(503, "Behavior cache not initialized")
    action = str(request_body.get("action") or "").strip()
    if not action:
        raise HTTPException(400, "action is required")
    app_name = str(request_body.get("app_name") or "unknown")
    window_title = str(request_body.get("window_title") or "")
    return _srv._state.behavior_cache.suggest(
        action=action,
        app_name=app_name,
        window_title=window_title,
    )


@router.get("/runtime/profile")
async def runtime_profile_status(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    profile = _srv._normalize_runtime_profile(_srv._state.runtime_profile)
    return {
        "profile": profile,
        "policy": _srv._runtime_profile_policy(profile),
    }


@router.post("/runtime/profile")
async def runtime_profile_set(request_body: dict, x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    requested = request_body.get("profile")
    profile = _srv._normalize_runtime_profile(requested)
    _srv._state.runtime_profile = profile
    return {
        "ok": True,
        "profile": profile,
        "policy": _srv._runtime_profile_policy(profile),
    }


@router.get("/runtime/cursor")
async def runtime_cursor_status(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.cursor_tracker:
        raise HTTPException(503, "Cursor tracker not initialized")
    return _srv._state.cursor_tracker.status()


@router.get("/runtime/action-watcher")
async def runtime_action_watcher_status(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.action_watcher:
        raise HTTPException(503, "Action watcher not initialized")
    return _srv._state.action_watcher.stats()


