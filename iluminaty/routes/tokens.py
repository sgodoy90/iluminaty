"""Route module — tokens."""
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

# ─── Token Economy ─────────────────────────────────────────────

# Approximate token costs per response mode
TOKEN_COSTS = {
    "text_only":  {"tokens": 200,   "desc": "OCR text + metadata, no image"},
    "low_res":    {"tokens": 5000,  "desc": "Image at 320px width + metadata"},
    "medium_res": {"tokens": 15000, "desc": "Image at 768px width + metadata"},
    "full_res":   {"tokens": 30000, "desc": "Image at 1280px width + metadata"},
}


class _TokenTracker:
    """Tracks token usage per session."""
    def __init__(self):
        self.mode: str = "text_only"  # Default: cheapest mode
        self.budget: int = 0          # 0 = unlimited
        self.used: int = 0
        self.history: list = []       # Last 50 actions
        self.max_history = 50

    def estimate(self, mode: str = None) -> int:
        return TOKEN_COSTS.get(mode or self.mode, TOKEN_COSTS["text_only"])["tokens"]

    def record(self, action: str, tokens: int):
        self.used += tokens
        entry = {"action": action, "tokens": tokens, "time": time.time()}
        self.history.append(entry)
        if len(self.history) > self.max_history:
            self.history.pop(0)

    def budget_remaining(self) -> int:
        if self.budget == 0:
            return -1  # unlimited
        return max(0, self.budget - self.used)

    def is_over_budget(self) -> bool:
        return self.budget > 0 and self.used >= self.budget


_tokens = _TokenTracker()


@router.get("/tokens/status")
async def tokens_status(x_api_key: Optional[str] = Header(None)):
    """Current token usage and budget."""
    _srv._check_auth(x_api_key)
    return {
        "mode": _tokens.mode,
        "mode_cost": TOKEN_COSTS[_tokens.mode],
        "all_modes": TOKEN_COSTS,
        "used": _tokens.used,
        "budget": _tokens.budget,
        "remaining": _tokens.budget_remaining(),
        "over_budget": _tokens.is_over_budget(),
        "history_count": len(_tokens.history),
        "last_5": _tokens.history[-5:],
    }


@router.post("/tokens/mode")
async def tokens_set_mode(
    mode: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Set response mode: text_only, low_res, medium_res, full_res."""
    _srv._check_auth(x_api_key)
    if mode not in TOKEN_COSTS:
        raise HTTPException(400, f"Invalid mode. Choose: {list(TOKEN_COSTS.keys())}")
    _tokens.mode = mode
    return {"mode": mode, "estimated_tokens_per_call": TOKEN_COSTS[mode]["tokens"]}


@router.post("/tokens/budget")
async def tokens_set_budget(
    limit: int = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Set token budget. 0 = unlimited."""
    _srv._check_auth(x_api_key)
    _tokens.budget = max(0, limit)
    return {"budget": _tokens.budget, "used": _tokens.used, "remaining": _tokens.budget_remaining()}


@router.post("/tokens/reset")
async def tokens_reset(x_api_key: Optional[str] = Header(None)):
    """Reset token counter."""
    _srv._check_auth(x_api_key)
    _tokens.used = 0
    _tokens.history.clear()
    return {"reset": True, "used": 0}


@router.get("/vision/smart")
async def vision_smart(
    mode: Optional[str] = Query(None),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id. Defaults to active monitor."),
    save_to: Optional[str] = Query(None, description="Save screenshot to this file path (e.g. C:/Users/jgodo/Desktop/snap.webp)"),
    x_api_key: Optional[str] = Header(None),
):
    """
    Smart vision endpoint — adapts response based on token mode.

    Modes:
      text_only   → OCR text + window info (~200 tokens)
      low_res     → 320px image + text (~5K tokens)
      medium_res  → 768px image + text (~15K tokens)
      full_res    → 1280px image + text (~30K tokens)
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.buffer or not _srv._state.vision:
        raise HTTPException(503, "Not initialized")

    # Check budget
    active_mode = mode or _tokens.mode
    if _tokens.is_over_budget():
        return JSONResponse({
            "error": "token_budget_exceeded",
            "used": _tokens.used,
            "budget": _tokens.budget,
            "suggestion": "Switch to text_only mode or increase budget",
        }, status_code=429)

    slot, resolved_mid = _srv._latest_slot_for_monitor(monitor_id)

    # Staleness guard: if the slot is older than 2s, force a live mss capture.
    # skip_if_unchanged keeps the same slot when screen is static — the timestamp
    # stops updating even though the frame is correct. For agents calling see_now
    # to verify an action, a 2s-old frame is stale enough to cause wrong decisions.
    import time as _t
    _STALE_THRESHOLD_S = 2.0
    if slot is not None:
        slot_age = _t.time() - float(getattr(slot, "timestamp", 0) or 0)
        if slot_age > _STALE_THRESHOLD_S:
            slot = None  # force on-demand capture below

    if not slot:
        # On-demand capture: buffer empty OR frame stale (>2s old on static screen).
        # Always take a live mss screenshot rather than serving stale content.
        _mid_for_snap = int(monitor_id) if monitor_id is not None else (resolved_mid or 1)
        try:
            import mss as _mss, asyncio as _aio, base64 as _b64
            monitors_info = (_srv._state.monitor_mgr.monitors
                             if _srv._state.monitor_mgr else [])
            _mon = next((m for m in monitors_info
                         if int(m.id) == _mid_for_snap), None)

            # Fallback: resolve monitor geometry from mss directly
            if _mon is None:
                with _mss.mss() as _s:
                    _mss_mons = _s.monitors  # [0]=virtual, [1..N]=real
                    if 1 <= _mid_for_snap < len(_mss_mons):
                        class _M:
                            pass
                        _mon = _M()
                        _mon.left   = _mss_mons[_mid_for_snap]["left"]
                        _mon.top    = _mss_mons[_mid_for_snap]["top"]
                        _mon.width  = _mss_mons[_mid_for_snap]["width"]
                        _mon.height = _mss_mons[_mid_for_snap]["height"]

            if _mon:
                _mon_left, _mon_top = _mon.left, _mon.top
                _mon_w, _mon_h = _mon.width, _mon.height

                def _snap_sync():
                    import io as _io
                    from PIL import Image as _Img
                    with _mss.mss() as _s:
                        raw = _s.grab({"left": _mon_left, "top": _mon_top,
                                       "width": _mon_w, "height": _mon_h})
                        img = _Img.frombytes("RGB", raw.size,
                                             raw.bgra, "raw", "BGRX")
                        # Resize to requested mode resolution
                        if active_mode == "low_res":
                            ratio = 320 / img.width
                            img = img.resize((320, int(img.height * ratio)),
                                             _Img.LANCZOS)
                        elif active_mode == "medium_res":
                            ratio = 768 / img.width
                            img = img.resize((768, int(img.height * ratio)),
                                             _Img.LANCZOS)
                        buf = _io.BytesIO()
                        img.save(buf, format="WEBP",
                                 quality=75 if active_mode != "full_res" else 90)
                        return buf.getvalue()

                webp = await _aio.get_running_loop().run_in_executor(None, _snap_sync)
                if webp:
                    # Save to disk if requested (lets agents read it via Read tool)
                    saved_path = None
                    if save_to:
                        try:
                            import pathlib as _pl
                            _pl.Path(save_to).write_bytes(webp)
                            saved_path = save_to
                        except Exception as _se:
                            log.warning(f"save_to failed: {_se}")
                    return JSONResponse({
                        "mode": active_mode,
                        "monitor_id": _mid_for_snap,
                        "image_base64": _b64.b64encode(webp).decode(),
                        "mime_type": "image/webp",
                        "timestamp": _t.time(),
                        "active_window": "",
                        "ocr_text": "",
                        "ai_prompt": (
                            f"[Live snapshot M{_mid_for_snap} — "
                            "captured on-demand, always fresh]"
                        ),
                        "on_demand": True,
                        "saved_path": saved_path,
                    })
        except Exception as _ode:
            log.warning(f"on-demand snapshot M{_mid_for_snap} failed: {_ode}")
    if not slot:
        _srv._raise_no_frame_available(monitor_id)

    result = {}

    if active_mode == "text_only":
        enriched = _srv._state.vision.enrich_frame(slot, run_ocr=True)
        result = {
            "mode": "text_only",
            "timestamp": enriched.timestamp,
            "ocr_text": enriched.ocr_text,
            "active_window": enriched.active_window,
            "ai_prompt": enriched.to_ai_prompt(),
            "change_score": enriched.change_score,
            "monitor_id": int(getattr(slot, "monitor_id", resolved_mid or 0) or (resolved_mid or 0)),
        }
    else:
        # Resize image based on mode
        from PIL import Image
        import io

        enriched = _srv._state.vision.enrich_frame(slot, run_ocr=True)
        d = enriched.to_dict(include_image=True)

        if active_mode in ("low_res", "medium_res") and d.get("image_base64"):
            import base64
            target_width = 320 if active_mode == "low_res" else 768
            img_bytes = base64.b64decode(d["image_base64"])
            img = Image.open(io.BytesIO(img_bytes))
            ratio = target_width / img.width
            new_size = (target_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=60)
            d["image_base64"] = base64.b64encode(buf.getvalue()).decode()
            d["width"] = new_size[0]
            d["height"] = new_size[1]

        d["mode"] = active_mode
        d["monitor_id"] = int(getattr(slot, "monitor_id", resolved_mid or 0) or (resolved_mid or 0))
        result = d

    # Save to disk if requested
    if save_to and result.get("image_base64"):
        try:
            import pathlib as _pl, base64 as _b64s
            _pl.Path(save_to).write_bytes(_b64s.b64decode(result["image_base64"]))
            result["saved_path"] = save_to
        except Exception as _se:
            log.warning(f"save_to failed: {_se}")

    # Track tokens
    est = _tokens.estimate(active_mode)
    _tokens.record(f"vision/smart ({active_mode})", est)
    result["token_estimate"] = est
    result["tokens_used_total"] = _tokens.used

    return JSONResponse(result)


