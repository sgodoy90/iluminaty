"""Route module — actions."""
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

# ─── Capa 1: Actions ───

@router.get("/action/status")
async def action_status(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    return _srv._state.actions.stats if _srv._state.actions else {"enabled": False}


@router.post("/action/enable")
async def action_enable(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if _srv._state.actions:
        _srv._state.actions.enable()
    return {"enabled": True}


@router.post("/action/disable")
async def action_disable(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if _srv._state.actions:
        _srv._state.actions.disable()
    return {"enabled": False}


@router.post("/action/click")
async def action_click(
    x: int = Query(...), y: int = Query(...),
    button: str = Query("left"),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for monitor-local coordinates"),
    relative_to_monitor: bool = Query(False, description="If true, x/y are relative to monitor origin"),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        raise HTTPException(503, "Actions not initialized")

    # ── Auto-focus: find and focus the window under the target coords ─────────
    # Problem: when focus is on a different monitor (e.g. M3/pi), clicking on M1
    # will first "activate" the window (consuming the click) instead of hitting
    # the target element. Fix: always focus the window at the target coords first.
    rx, ry = _srv._translate_click_coords(int(x), int(y), monitor_id, bool(relative_to_monitor))

    if _srv._state.windows:
        # Explicit handle/title takes priority
        if focus_handle is not None or (focus_title and focus_title.strip()):
            _srv._state.windows.focus_window(title=focus_title, handle=focus_handle)
            await asyncio.sleep(0.15)
        else:
            # Auto: find window at global coords and focus it
            try:
                import ctypes as _c
                hwnd_at = _c.windll.user32.WindowFromPoint(_c.wintypes.POINT(rx, ry))
                if hwnd_at:
                    # Walk up to the top-level window
                    parent = _c.windll.user32.GetAncestor(hwnd_at, 2)  # GA_ROOT=2
                    top_hwnd = parent if parent else hwnd_at
                    fg_hwnd = _c.windll.user32.GetForegroundWindow()
                    if int(top_hwnd) != int(fg_hwnd):
                        # Window at target is not in focus — focus it first
                        _srv._state.windows.focus_window(handle=int(top_hwnd))
                        await asyncio.sleep(0.15)  # wait for focus to settle
            except Exception:
                pass  # auto-focus best-effort; don't block click

    result = _srv._state.actions.click(rx, ry, button)
    payload = result.to_dict()
    payload["requested_x"] = int(x)
    payload["requested_y"] = int(y)
    payload["resolved_x"] = int(rx)
    payload["resolved_y"] = int(ry)
    payload["monitor_id"] = int(monitor_id) if monitor_id is not None else None
    payload["relative_to_monitor"] = bool(relative_to_monitor)
    return payload


@router.post("/action/double_click")
async def action_double_click(
    x: int = Query(...), y: int = Query(...),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for monitor-local coordinates"),
    relative_to_monitor: bool = Query(False, description="If true, x/y are relative to monitor origin"),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _srv._state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _srv._state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.08)  # non-blocking: yield event loop during focus settle
    rx, ry = _srv._translate_click_coords(int(x), int(y), monitor_id, bool(relative_to_monitor))
    result = _srv._state.actions.double_click(rx, ry).to_dict()
    result["requested_x"] = int(x)
    result["requested_y"] = int(y)
    result["resolved_x"] = int(rx)
    result["resolved_y"] = int(ry)
    result["monitor_id"] = int(monitor_id) if monitor_id is not None else None
    result["relative_to_monitor"] = bool(relative_to_monitor)
    return result


@router.post("/action/type")
async def action_type(
    text: str = Query(...),
    interval: float = Query(0.02),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _srv._state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _srv._state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.08)  # non-blocking: yield event loop during focus settle
    return _srv._state.actions.type_text(text, interval).to_dict()


@router.post("/action/hotkey")
async def action_hotkey(
    keys: str = Query(..., description="Keys separated by + (e.g. ctrl+s)"),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _srv._state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _srv._state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.15)  # give OS time to switch focus
    key_list = [k.strip() for k in keys.split("+")]
    # For single-key submit (enter/tab/escape) when a focus_handle is given,
    # use PostMessage directly — guarantees delivery regardless of system focus
    if focus_handle and len(key_list) == 1 and key_list[0].lower() in ("enter", "tab", "escape", "return"):
        import ctypes as _ct, ctypes.wintypes as _cwt
        VK = {"enter": 0x0D, "return": 0x0D, "tab": 0x09, "escape": 0x1B}
        vk = VK[key_list[0].lower()]
        # SetForegroundWindow then SendInput — works with Chromium's input pipeline
        _ct.windll.user32.SetForegroundWindow(int(focus_handle))
        import time as _t; _t.sleep(0.1)
        # INPUT struct for keyboard event
        class _KEYBDINPUT(_ct.Structure):
            _fields_ = [("wVk", _ct.c_ushort), ("wScan", _ct.c_ushort),
                        ("dwFlags", _ct.c_ulong), ("time", _ct.c_ulong),
                        ("dwExtraInfo", _ct.POINTER(_ct.c_ulong))]
        class _INPUT(_ct.Structure):
            class _I(_ct.Union):
                _fields_ = [("ki", _KEYBDINPUT)]
            _anonymous_ = ("_i",)
            _fields_ = [("type", _ct.c_ulong), ("_i", _I)]
        KEYEVENTF_KEYUP = 0x0002
        inputs = (_INPUT * 2)(
            _INPUT(type=1, ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)),
            _INPUT(type=1, ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)),
        )
        _ct.windll.user32.SendInput(2, inputs, _ct.sizeof(_INPUT))
        return {"action": "hotkey", "success": True, "message": f"SendInput {key_list[0]} to hwnd={focus_handle}"}
    return _srv._state.actions.hotkey(*key_list).to_dict()


@router.post("/action/scroll")
async def action_scroll(
    amount: int = Query(...),
    x: Optional[int] = Query(None), y: Optional[int] = Query(None),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for monitor-local x/y"),
    relative_to_monitor: bool = Query(False, description="If true, x/y are relative to monitor origin"),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _srv._state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _srv._state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.08)  # non-blocking: yield event loop during focus settle
    rx = None if x is None else int(x)
    ry = None if y is None else int(y)
    if rx is not None and ry is not None:
        rx, ry = _srv._translate_click_coords(rx, ry, monitor_id, bool(relative_to_monitor))
    result = _srv._state.actions.scroll(amount, rx, ry).to_dict()
    result["requested_x"] = int(x) if x is not None else None
    result["requested_y"] = int(y) if y is not None else None
    result["resolved_x"] = int(rx) if rx is not None else None
    result["resolved_y"] = int(ry) if ry is not None else None
    result["monitor_id"] = int(monitor_id) if monitor_id is not None else None
    result["relative_to_monitor"] = bool(relative_to_monitor)
    return result


@router.post("/action/move")
async def action_move(
    x: int = Query(...), y: int = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Move mouse to coordinates without clicking."""
    _srv._check_auth(x_api_key)
    if not _srv._state.actions or not _srv._state.actions.available:
        return {"action": "move_mouse", "success": False, "message": "Action bridge not available"}
    result = _srv._state.actions.move_mouse(x, y)
    return {"action": "move_mouse", "success": result.success, "message": result.message, "x": x, "y": y}


@router.post("/action/key_down")
async def action_key_down(
    key: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Press and hold a key (for modifier combos and hold_key sequences)."""
    _srv._check_auth(x_api_key)
    if not _srv._state.actions or not _srv._state.actions.available:
        return {"success": False, "message": "Action bridge not available"}
    result = _srv._state.actions.hold_key(key)
    return {"success": result.success, "key": key, "message": result.message}


@router.post("/action/key_up")
async def action_key_up(
    key: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Release a previously held key."""
    _srv._check_auth(x_api_key)
    if not _srv._state.actions or not _srv._state.actions.available:
        return {"success": False, "message": "Action bridge not available"}
    result = _srv._state.actions.release_key(key)
    return {"success": result.success, "key": key, "message": result.message}


@router.post("/action/mouse_down")
async def action_mouse_down(
    x: int = Query(...), y: int = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Press and hold the left mouse button at (x, y). Use /action/mouse_up to release."""
    _srv._check_auth(x_api_key)
    if not _srv._state.actions or not _srv._state.actions.available:
        return {"success": False, "message": "Action bridge not available"}
    try:
        import pyautogui as _pag
        _pag.mouseDown(x=x, y=y, button="left")
        return {"success": True, "x": x, "y": y, "message": f"Mouse down at ({x},{y})"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/action/mouse_up")
async def action_mouse_up(
    x: int = Query(...), y: int = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Release the left mouse button at (x, y)."""
    _srv._check_auth(x_api_key)
    if not _srv._state.actions or not _srv._state.actions.available:
        return {"success": False, "message": "Action bridge not available"}
    try:
        import pyautogui as _pag
        _pag.mouseUp(x=x, y=y, button="left")
        return {"success": True, "x": x, "y": y, "message": f"Mouse up at ({x},{y})"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/action/drag")
async def action_drag(
    start_x: int = Query(...), start_y: int = Query(...),
    end_x: int = Query(...), end_y: int = Query(...),
    duration: float = Query(0.5),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for monitor-local coordinates"),
    relative_to_monitor: bool = Query(False, description="If true, coordinates are relative to monitor origin"),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _srv._state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _srv._state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.08)  # non-blocking: yield event loop during focus settle
    sx, sy = _srv._translate_click_coords(int(start_x), int(start_y), monitor_id, bool(relative_to_monitor))
    ex, ey = _srv._translate_click_coords(int(end_x), int(end_y), monitor_id, bool(relative_to_monitor))
    result = _srv._state.actions.drag_drop(sx, sy, ex, ey, duration).to_dict()
    result["requested_start_x"] = int(start_x)
    result["requested_start_y"] = int(start_y)
    result["requested_end_x"] = int(end_x)
    result["requested_end_y"] = int(end_y)
    result["resolved_start_x"] = int(sx)
    result["resolved_start_y"] = int(sy)
    result["resolved_end_x"] = int(ex)
    result["resolved_end_y"] = int(ey)
    result["monitor_id"] = int(monitor_id) if monitor_id is not None else None
    result["relative_to_monitor"] = bool(relative_to_monitor)
    return result


@router.get("/action/mouse")
async def action_mouse(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        return {"x": 0, "y": 0}
    pos = _srv._state.actions.get_mouse_position()
    mid = _srv._monitor_id_for_rect(int(pos.get("x", 0)), int(pos.get("y", 0)), 1, 1)
    if mid is not None:
        pos["monitor_id"] = int(mid)
    return pos


@router.get("/action/log")
async def action_log(
    count: int = Query(20),
    x_api_key: Optional[str] = Header(None),
):
    _srv._check_auth(x_api_key)
    return {"log": _srv._state.actions.get_action_log(count) if _srv._state.actions else []}


@router.post("/action/precheck")
async def action_precheck(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Validate if an action is executable with current context/mode.
    Accepts either:
      {"instruction":"save file"}
    or
      {"action":"click","params":{"x":100,"y":200},"category":"normal"}
    """
    _srv._check_auth(x_api_key)
    intent = _srv._intent_from_payload(request_body)
    grounding_request = _srv._grounding_request_from_payload(request_body, intent)
    mode = request_body.get("mode") or _srv._state.operating_mode
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = request_body.get("max_staleness_ms")
    if max_staleness_ms is not None:
        max_staleness_ms = int(max_staleness_ms)
    return _srv._build_precheck(
        intent,
        mode,
        include_readiness=True,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
        grounding_request=grounding_request,
    )


@router.post("/action/intent")
async def action_intent(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    High-level action primitive.
    Accepts natural language instruction and executes it through the same
    closed-loop path as /action/execute.
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.intent or not _srv._state.resolver:
        raise HTTPException(503, "Orchestration not initialized")

    instruction = str(request_body.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "instruction is required")

    intent = _srv._state.intent.classify_or_default(instruction)
    mode = request_body.get("mode") or _srv._state.operating_mode
    verify = bool(request_body.get("verify", True))
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = request_body.get("max_staleness_ms")
    if max_staleness_ms is not None:
        max_staleness_ms = int(max_staleness_ms)
    grounding_request = _srv._grounding_request_from_payload(request_body, intent)
    return await _srv._execute_intent(
        intent,
        mode=mode,
        verify=verify,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
        grounding_request=grounding_request,
    )


@router.post("/action/execute")
async def action_execute(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Execute action through current operating mode (SAFE/RAW/HYBRID).
    SAFE applies all guards. HYBRID guards destructive actions only.
    """
    _srv._check_auth(x_api_key)
    intent = _srv._intent_from_payload(request_body)
    grounding_request = _srv._grounding_request_from_payload(request_body, intent)
    mode = request_body.get("mode") or _srv._state.operating_mode
    verify = bool(request_body.get("verify", True))
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = request_body.get("max_staleness_ms")
    if max_staleness_ms is not None:
        max_staleness_ms = int(max_staleness_ms)
    return await _srv._execute_intent(
        intent,
        mode=mode,
        verify=verify,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
        grounding_request=grounding_request,
    )


@router.post("/action/raw")
async def action_raw(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Raw execution path (0 guardrails except kill switch).
    Intended for expert setups where external AI handles all safety.
    """
    _srv._check_auth(x_api_key)
    intent = _srv._intent_from_payload(request_body)
    verify = bool(request_body.get("verify", False))
    return await _srv._execute_intent(intent, mode="RAW", verify=verify)


@router.post("/actions/act")
async def actions_act_alias(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Simple action endpoint for dashboard and SDK.
    Accepts flat body: {action, target?, text?, key?, x?, y?, monitor?}
    Routes to the correct low-level endpoint internally.

    action=click  + target="Save button" → smart_locate → click
    action=click  + x=100 + y=200        → direct click
    action=type   + text="hello"         → type_text
    action=key    + key="ctrl+s"         → hotkey
    action=scroll + x=960 + y=540        → scroll
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.actions:
        raise HTTPException(503, "Actions not initialized")

    action  = str(request_body.get("action") or "click").strip().lower()
    target  = str(request_body.get("target") or "").strip()
    text    = str(request_body.get("text") or "").strip()
    key     = str(request_body.get("key") or "").strip()
    x       = request_body.get("x")
    y       = request_body.get("y")
    monitor = request_body.get("monitor")

    try:
        if action in ("type", "type_text"):
            if not text:
                return {"success": False, "message": "text is required for type action"}
            result = _srv._state.actions.type_text(text)
            return result.to_dict()

        if action in ("key", "hotkey", "press"):
            if not key:
                return {"success": False, "message": "key is required for key action"}
            import pyautogui as _pag
            _pag.hotkey(*key.replace(" ", "").split("+"))
            return {"success": True, "message": f"key: {key}"}

        if action in ("scroll",):
            sx = int(x or 0)
            sy = int(y or 0)
            # Translate monitor-relative coords to global
            if monitor is not None:
                _mid = int(monitor)
                _mons = (_srv._state.monitor_mgr.monitors
                         if _srv._state.monitor_mgr else [])
                _mon = next((m for m in _mons if int(m.id) == _mid), None)
                if _mon:
                    sx += _mon.left
                    sy += _mon.top
            clicks = int(request_body.get("clicks", -3))
            import pyautogui as _pag
            _pag.scroll(clicks, x=sx, y=sy)
            return {"success": True, "message": f"scroll {clicks} at ({sx},{sy})"}

        # click / double_click / right_click — resolve target or use coords
        if target and not (x and y):
            # Smart locate by name
            if not _srv._state.smart_locator:
                return {"success": False, "message": "smart_locate not available — provide x,y"}
            monitor_id = int(monitor) if monitor is not None else None
            loc = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _srv._state.smart_locator.locate(target, monitor_id=monitor_id)
            )
            if not loc:
                return {"success": False, "message": f"'{target}' not found on screen"}
            cx, cy = loc.x, loc.y
        elif x is not None and y is not None:
            cx, cy = int(x), int(y)
            # Translate monitor-relative coords to global when monitor= is given
            if monitor is not None:
                monitor_id = int(monitor)
                monitors_info = (_srv._state.monitor_mgr.monitors
                                 if _srv._state.monitor_mgr else [])
                mon = next((m for m in monitors_info if int(m.id) == monitor_id), None)
                if mon:
                    cx = cx + mon.left
                    cy = cy + mon.top
        else:
            return {"success": False, "message": "Provide target name or x,y coordinates"}

        if action == "double_click":
            result = _srv._state.actions.double_click(cx, cy)
        elif action == "right_click":
            result = _srv._state.actions.click(cx, cy, "right")
        elif action == "move":
            import pyautogui as _pag
            _pag.moveTo(cx, cy, duration=0.1)
            result_dict = {"action": "click", "success": True,
                           "message": f"Moved to ({cx},{cy})",
                           "duration_ms": 100}
            d = result_dict
            d["resolved_x"] = cx
            d["resolved_y"] = cy
            if target:
                d["target"] = target
            return d
        else:
            result = _srv._state.actions.click(cx, cy)

        d = result.to_dict()
        d["resolved_x"] = cx
        d["resolved_y"] = cy
        if target:
            d["target"] = target
        return d

    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/action/verify")
async def action_verify(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """Run post-action verification without executing new actions."""
    _srv._check_auth(x_api_key)
    if not _srv._state.verifier:
        raise HTTPException(503, "Verifier not initialized")
    action = (request_body.get("action") or "").strip()
    if not action:
        raise HTTPException(400, "action is required")
    params = request_body.get("params") or {}
    pre_state = request_body.get("pre_state")
    result = _srv._state.verifier.verify(action, params, pre_state)
    return result.to_dict()


