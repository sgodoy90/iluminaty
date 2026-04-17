"""
ILUMINATY - API Server
=======================
Servidor local que expone el ring buffer visual a cualquier IA.

Endpoints:
  GET  /frame/latest        → último frame (JPEG)
  GET  /frame/latest?base64  → último frame en base64 (para APIs de IA)
  GET  /frames?last=5        → últimos N frames
  GET  /frames?seconds=10    → frames de los últimos N segundos
  GET  /buffer/stats         → estadísticas del buffer
  POST /config               → cambiar configuración en caliente
  WS   /ws/stream            → WebSocket stream de frames en vivo
  GET  /health               → health check

Headers de seguridad:
  X-API-Key: requerido si se configura una key
"""

import asyncio
import base64
import io
import json
import logging
import os
import pathlib
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Header, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .ring_buffer import RingBuffer, FrameSlot
from .capture import ScreenCapture, CaptureConfig
from .vision import VisionIntelligence, Annotation
from .dashboard import DASHBOARD_HTML
from .smart_diff import SmartDiff
from .audio import AudioRingBuffer, AudioCapture, TranscriptionEngine, AudioInterruptDetector
from .voice import VoiceSynth, AutoNarrator
from .monitors import MonitorManager
from .watchdog import Watchdog
from .ipa_bridge import IPABridge

# v1.0 imports: Computer Use capas
from .actions import ActionBridge
from .windows import WindowManager
from .clipboard import ClipboardManager
from .process_mgr import ProcessManager
from .ui_tree import UITree
from .filesystem import FileSystemSandbox
from .resolver import ActionResolver
from .intent import IntentClassifier, Intent
from .verifier import ActionVerifier
from .recovery import ErrorRecovery
from .safety import SafetySystem
from .audit import AuditLog
from .licensing import LicenseManager, get_license, init_license
from .grounding import GroundingEngine
from .smart_locate import SmartLocateEngine, LocateResult
from .fast_ocr import ocr_image as _fast_ocr_image, engine_name as _fast_ocr_engine
from .watch_engine import WatchEngine, WatchResult
from .host_telemetry import HostTelemetry
from .os_surface import OSSurfaceSignals
from .cursor_tracker import CursorTracker
from .action_watchers import ActionCompletionWatcher
from .app_behavior_cache import AppBehaviorCache

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

logger = logging.getLogger(__name__)


def _normalize_operating_mode(mode) -> str:
    """Normalize operating mode string. Defined early so _ServerState can use it."""
    value = (mode or "SAFE").strip().upper()
    if value not in {"SAFE", "RAW", "HYBRID"}:
        value = "SAFE"
    return value


# ─── Server State (BUG-005 fix: single state object with lock) ───
import threading as _threading

class _ServerState:
    """Thread-safe server state container."""
    def __init__(self):
        self.lock = _threading.Lock()
        # Core (v0.5)
        self.buffer: Optional[RingBuffer] = None
        self.capture: Optional[ScreenCapture] = None
        self.api_key: Optional[str] = None
        self.vision: Optional[VisionIntelligence] = None
        self.diff: Optional[SmartDiff] = None
        self.audio_buffer: Optional[AudioRingBuffer] = None
        self.audio_capture: Optional[AudioCapture] = None
        self.transcriber: Optional[TranscriptionEngine] = None
        self.monitor_mgr: Optional[MonitorManager] = None
        self.watchdog: Optional[Watchdog] = None
        self.ipa_bridge: Optional[IPABridge] = None
        self.ws_clients: set = set()
        # Computer Use capas
        self.actions: Optional[ActionBridge] = None
        self.windows: Optional[WindowManager] = None
        self.clipboard: Optional[ClipboardManager] = None
        self.process_mgr: Optional[ProcessManager] = None
        self.ui_tree: Optional[UITree] = None
        self.filesystem: Optional[FileSystemSandbox] = None
        self.resolver: Optional[ActionResolver] = None
        self.intent: Optional[IntentClassifier] = None
        self.verifier: Optional[ActionVerifier] = None
        self.recovery: Optional[ErrorRecovery] = None
        self.safety: Optional[SafetySystem] = None
        self.audit: Optional[AuditLog] = None
        self.license: Optional[LicenseManager] = None
        self.perception = None  # PerceptionEngine (lazy import)
        self.grounding: Optional[GroundingEngine] = None
        self.smart_locator: Optional[SmartLocateEngine] = None
        self.watch_engine: Optional[WatchEngine] = None
        self.host_telemetry: Optional[HostTelemetry] = None
        self.os_surface: Optional[OSSurfaceSignals] = None
        self.behavior_cache: Optional[AppBehaviorCache] = None
        self.audio_interrupt: Optional[AudioInterruptDetector] = None
        self.recording_engine = None   # Opt-in recording (disabled by default)
        self.cursor_tracker: Optional[CursorTracker] = None
        self.action_watcher: Optional[ActionCompletionWatcher] = None
        self.trading_engine = None   # TradingEngine (lazy init on first /trading/ call)
        self.voice: Optional[VoiceSynth] = None
        self.auto_narrator: Optional[AutoNarrator] = None
        self.operating_mode: str = _normalize_operating_mode(os.environ.get("ILUMINATY_OPERATING_MODE"))  # SAFE | RAW | HYBRID
        self.runtime_profile: str = os.environ.get("ILUMINATY_RUNTIME_PROFILE", "standard").strip().lower() or "standard"
        self.bootstrap_warnings: list[str] = []

_state = _ServerState()

# Aliases for backward compat in endpoint functions
def _get_buffer(): return _state.buffer
def _get_capture(): return _state.capture
def _get_vision(): return _state.vision
def _get_diff(): return _state.diff


def _frame_to_json(slot: FrameSlot, include_base64: bool = False) -> dict:
    """Serializa un FrameSlot a JSON. Los bytes solo van si se pide base64."""
    result = {
        "timestamp": slot.timestamp,
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(slot.timestamp)),
        "width": slot.width,
        "height": slot.height,
        "size_bytes": len(slot.frame_bytes),
        "format": slot.mime_type,
        "change_score": slot.change_score,
        "region": slot.region,
        "monitor_id": int(getattr(slot, "monitor_id", 0)),
    }
    if include_base64:
        result["image_base64"] = base64.b64encode(slot.frame_bytes).decode("ascii")
        result["image_url"] = f"data:{slot.mime_type};base64,{result['image_base64']}"
    return result


_NO_AUTH = os.environ.get("ILUMINATY_NO_AUTH", "0") == "1"
if _NO_AUTH:
    import warnings as _warnings
    _warnings.warn(
        "ILUMINATY_NO_AUTH=1: ALL authentication is DISABLED. "
        "Never use this in production or on a shared machine.",
        stacklevel=1,
    )

def _check_auth(api_key: Optional[str]):
    if _NO_AUTH:
        return
    # C-1 fix: reject requests when no API key is configured (was silently open)
    if not _state.api_key:
        raise HTTPException(
            status_code=503,
            detail="Server not configured: start with --api-key <key> or set ILUMINATY_KEY env var."
        )
    if api_key != _state.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _raise_no_frame_available(monitor_id: Optional[int] = None) -> None:
    if monitor_id is not None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "monitor_frame_not_available",
                "monitor_id": int(monitor_id),
            },
        )
    raise HTTPException(status_code=404, detail={"error": "no_frames_in_buffer"})


def _resolve_active_monitor_id() -> Optional[int]:
    """Best-effort active monitor id for multi-monitor setups."""
    # Prefer capture scheduler active monitor when available.
    if _state.capture is not None and hasattr(_state.capture, "active_monitor_id"):
        try:
            active_id = int(getattr(_state.capture, "active_monitor_id"))
            if active_id > 0:
                return active_id
        except Exception:
            pass

    if not _state.monitor_mgr:
        return None

    # Detect from active window center.
    try:
        from .vision import get_active_window_info
        win = get_active_window_info() or {}
        bounds = win.get("bounds") or {}
        if bounds:
            active_id = int(_state.monitor_mgr.detect_active_from_window(bounds))
            if active_id > 0:
                return active_id
    except Exception as e:
        logger.debug("Active monitor detection from window failed: %s", e)

    # Fallback to monitor manager active monitor.
    try:
        active = _state.monitor_mgr.get_active_monitor()
        if active:
            return int(active.id)
    except Exception:
        pass
    return None


def _monitor_offset(monitor_id: int) -> Optional[tuple[int, int]]:
    mid = int(monitor_id)
    if mid <= 0:
        return None

    if _state.monitor_mgr:
        try:
            _state.monitor_mgr.refresh()
            mon = _state.monitor_mgr.get_monitor(mid)
            if mon:
                return int(mon.left), int(mon.top)
        except Exception as e:
            logger.debug("MonitorManager offset lookup failed for monitor %s: %s", mid, e)

    # Fallback if monitor manager is unavailable or stale.
    try:
        import mss
        with mss.mss() as sct:
            if 0 <= mid < len(sct.monitors):
                mon = sct.monitors[mid]
                return int(mon["left"]), int(mon["top"])
    except Exception as e:
        logger.debug("mss offset lookup failed for monitor %s: %s", mid, e)
    return None


def _translate_click_coords(
    x: int,
    y: int,
    monitor_id: Optional[int],
    relative_to_monitor: bool,
) -> tuple[int, int]:
    if not relative_to_monitor or monitor_id is None:
        return int(x), int(y)
    offset = _monitor_offset(int(monitor_id))
    if not offset:
        return int(x), int(y)
    ox, oy = offset
    return int(ox + x), int(oy + y)


def _latest_slot_for_monitor(
    monitor_id: Optional[int] = None,
) -> tuple[Optional[FrameSlot], Optional[int]]:
    if not _state.buffer:
        return None, None

    resolved_mid: Optional[int] = None
    monitor_was_requested = monitor_id is not None
    if monitor_id is not None:
        try:
            resolved_mid = int(monitor_id)
        except Exception:
            resolved_mid = None

    if resolved_mid is None:
        resolved_mid = _resolve_active_monitor_id()

    if resolved_mid is not None and hasattr(_state.buffer, "get_latest_for_monitor"):
        slot = _state.buffer.get_latest_for_monitor(int(resolved_mid))
        if slot:
            return slot, int(getattr(slot, "monitor_id", resolved_mid) or resolved_mid)
        # Strict monitor isolation: when a monitor was explicitly requested,
        # do not fall back to a global latest frame from another monitor.
        if monitor_was_requested:
            return None, resolved_mid

    # Legacy/global fallback only when monitor is not explicitly requested.
    slot = _state.buffer.get_latest()
    if slot:
        return slot, int(getattr(slot, "monitor_id", resolved_mid or 0) or (resolved_mid or 0))
    return None, resolved_mid


def _monitor_layout_snapshot() -> list[dict]:
    if _state.monitor_mgr:
        try:
            _state.monitor_mgr.refresh()
            return [
                {
                    "id": int(m.id),
                    "left": int(m.left),
                    "top": int(m.top),
                    "width": int(m.width),
                    "height": int(m.height),
                    "right": int(m.left + m.width),
                    "bottom": int(m.top + m.height),
                    "is_primary": bool(m.is_primary),
                    "is_active": bool(m.is_active),
                }
                for m in _state.monitor_mgr.monitors
            ]
        except Exception as e:
            logger.debug("Monitor layout snapshot via MonitorManager failed: %s", e)

    # Fallback to mss if monitor manager is unavailable.
    try:
        import mss
        with mss.mss() as sct:
            layout = []
            for i, mon in enumerate(sct.monitors):
                if i == 0:
                    continue
                left = int(mon["left"])
                top = int(mon["top"])
                width = int(mon["width"])
                height = int(mon["height"])
                layout.append(
                    {
                        "id": i,
                        "left": left,
                        "top": top,
                        "width": width,
                        "height": height,
                        "right": left + width,
                        "bottom": top + height,
                        "is_primary": i == 1,
                        "is_active": False,
                    }
                )
            return layout
    except Exception as e:
        logger.debug("Monitor layout snapshot via mss failed: %s", e)
        return []


def _monitor_geometry(monitor_id: Optional[int]) -> Optional[dict]:
    if monitor_id is None:
        return None
    try:
        target = int(monitor_id)
    except Exception:
        return None
    if target <= 0:
        return None
    for mon in _monitor_layout_snapshot():
        try:
            if int(mon.get("id", 0)) == target:
                return mon
        except Exception:
            continue
    return None


def _map_slot_region_to_monitor_native(
    *,
    slot_width: int,
    slot_height: int,
    monitor_width: int,
    monitor_height: int,
    region_x: int,
    region_y: int,
    region_w: int,
    region_h: int,
) -> tuple[int, int, int, int]:
    """Map region coordinates from slot space (possibly downscaled) to native monitor space."""
    sw = max(1, int(slot_width))
    sh = max(1, int(slot_height))
    mw = max(1, int(monitor_width))
    mh = max(1, int(monitor_height))

    rx = int(region_x)
    ry = int(region_y)
    rw = int(region_w)
    rh = int(region_h)

    scale_x = float(mw) / float(sw)
    scale_y = float(mh) / float(sh)

    nx = int(round(rx * scale_x))
    ny = int(round(ry * scale_y))
    nw = int(round(rw * scale_x))
    nh = int(round(rh * scale_y))

    nx = max(0, min(nx, mw - 1))
    ny = max(0, min(ny, mh - 1))
    nw = max(1, min(nw, mw - nx))
    nh = max(1, min(nh, mh - ny))
    return nx, ny, nw, nh


def _native_capture_rect_bytes(left: int, top: int, width: int, height: int) -> Optional[bytes]:
    w = max(1, int(width))
    h = max(1, int(height))
    l = int(left)
    t = int(top)
    try:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            raw = sct.grab({"left": l, "top": t, "width": w, "height": h})
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return buf.getvalue()
    except Exception as e:
        logger.debug("Native rect capture failed (%s,%s %sx%s): %s", l, t, w, h, e)
        return None


def _native_capture_monitor_bytes(monitor_id: Optional[int]) -> Optional[bytes]:
    mon = _monitor_geometry(monitor_id)
    if not mon:
        return None
    return _native_capture_rect_bytes(
        left=int(mon.get("left", 0)),
        top=int(mon.get("top", 0)),
        width=int(mon.get("width", 1)),
        height=int(mon.get("height", 1)),
    )


def _native_capture_active_window_bytes(preferred_monitor_id: Optional[int] = None) -> tuple[Optional[bytes], Optional[int]]:
    from .vision import get_active_window_info

    info = get_active_window_info() or {}
    bounds = info.get("bounds") or {}
    try:
        left = int(bounds.get("left", 0))
        top = int(bounds.get("top", 0))
        width = int(bounds.get("width", 0))
        height = int(bounds.get("height", 0))
    except Exception:
        return None, None
    if width <= 0 or height <= 0:
        return None, None

    win_mid = _monitor_id_for_rect(left, top, width, height)
    if preferred_monitor_id is not None and win_mid is not None and int(win_mid) != int(preferred_monitor_id):
        return None, win_mid

    payload = _native_capture_rect_bytes(left=left, top=top, width=width, height=height)
    return payload, win_mid


def _native_capture_region_from_slot(
    *,
    slot: FrameSlot,
    monitor_id: Optional[int],
    region_x: int,
    region_y: int,
    region_w: int,
    region_h: int,
) -> tuple[Optional[bytes], Optional[dict]]:
    mon = _monitor_geometry(monitor_id)
    if not mon:
        return None, None

    nx, ny, nw, nh = _map_slot_region_to_monitor_native(
        slot_width=int(getattr(slot, "width", 0) or 0),
        slot_height=int(getattr(slot, "height", 0) or 0),
        monitor_width=int(mon.get("width", 0) or 0),
        monitor_height=int(mon.get("height", 0) or 0),
        region_x=int(region_x),
        region_y=int(region_y),
        region_w=int(region_w),
        region_h=int(region_h),
    )
    gx = int(mon.get("left", 0)) + nx
    gy = int(mon.get("top", 0)) + ny
    payload = _native_capture_rect_bytes(left=gx, top=gy, width=nw, height=nh)
    if not payload:
        return None, None
    return payload, {
        "slot_region": {"x": int(region_x), "y": int(region_y), "w": int(region_w), "h": int(region_h)},
        "native_monitor_region": {"x": nx, "y": ny, "w": nw, "h": nh},
        "native_desktop_region": {"x": gx, "y": gy, "w": nw, "h": nh},
    }


def _intersection_area(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0
    return int((ix2 - ix1) * (iy2 - iy1))


def _monitor_id_for_rect(left: int, top: int, width: int, height: int) -> Optional[int]:
    layout = _monitor_layout_snapshot()
    if not layout:
        return None

    w = max(1, int(width))
    h = max(1, int(height))
    rect = (int(left), int(top), int(left) + w, int(top) + h)
    cx = int(left) + (w // 2)
    cy = int(top) + (h // 2)

    # Fast path: window center in monitor bounds.
    for mon in layout:
        if mon["left"] <= cx < mon["right"] and mon["top"] <= cy < mon["bottom"]:
            return int(mon["id"])

    # Fallback: max intersection area.
    best_id = None
    best_area = 0
    for mon in layout:
        area = _intersection_area(
            rect,
            (mon["left"], mon["top"], mon["right"], mon["bottom"]),
        )
        if area > best_area:
            best_area = area
            best_id = int(mon["id"])
    if best_id is not None:
        return best_id

    # Last fallback: nearest monitor by center distance.
    best_dist = None
    for mon in layout:
        mcx = int(mon["left"]) + int(mon["width"]) // 2
        mcy = int(mon["top"]) + int(mon["height"]) // 2
        dist = abs(mcx - cx) + abs(mcy - cy)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_id = int(mon["id"])
    return best_id


def _is_system_noise_window(payload: dict) -> bool:
    title = str(payload.get("title", "")).strip().lower()
    if not title:
        return True
    noisy_titles = (
        "program manager",
        "nvidia geforce overlay",
        "experiencia de entrada de windows",
        "windows input experience",
        "msctfime ui",
    )
    for marker in noisy_titles:
        if marker in title:
            return True
    return False


# _normalize_operating_mode defined near top of file (before _ServerState)


def _mode_requires_safety(mode: str, category: str) -> bool:
    mode_norm = _normalize_operating_mode(mode)
    cat = (category or "normal").lower()
    if mode_norm == "RAW":
        return False
    if mode_norm == "HYBRID":
        return cat == "destructive"
    return True


def _normalize_runtime_profile(profile: Optional[str]) -> str:
    value = str(profile or "standard").strip().lower()
    if value not in {"standard", "enterprise", "lab"}:
        return "standard"
    return value


def _runtime_profile_policy(profile: Optional[str]) -> dict:
    p = _normalize_runtime_profile(profile)
    if p == "enterprise":
        return {
            "profile": p,
            "require_verify_destructive": True,
            "raw_requires_explicit_ack": True,
            "strict_audit": True,
        }
    if p == "lab":
        return {
            "profile": p,
            "require_verify_destructive": False,
            "raw_requires_explicit_ack": False,
            "strict_audit": False,
        }
    return {
        "profile": "standard",
        "require_verify_destructive": False,
        "raw_requires_explicit_ack": False,
        "strict_audit": False,
    }


def _active_app_context() -> dict:
    app_name = "unknown"
    window_title = ""
    active = _active_window_snapshot()
    if active:
        window_title = str(active.get("title", "") or "")[:220]
    if _state.windows:
        try:
            win = _state.windows.get_active_window()
            if win:
                app_name = str(getattr(win, "app_name", "") or getattr(win, "process_name", "") or "").strip() or app_name
                if not window_title:
                    window_title = str(getattr(win, "title", "") or "")[:220]
        except Exception:
            pass
    if _state.perception:
        try:
            world = _state.perception.get_world_state() or {}
            active_surface = str(world.get("active_surface", "") or "")
            if active_surface:
                # Format is usually "app :: title".
                if "::" in active_surface:
                    left, right = active_surface.split("::", 1)
                    if (not app_name) or app_name == "unknown":
                        app_name = left.strip() or app_name
                    if not window_title:
                        window_title = right.strip()[:220]
                elif (not app_name) or app_name == "unknown":
                    app_name = active_surface[:96]
        except Exception:
            pass
    return {
        "app_name": str(app_name or "unknown")[:96],
        "window_title": str(window_title or "")[:220],
    }


def _behavior_hint(intent: Intent) -> dict:
    if not _state.behavior_cache:
        return {
            "found": False,
            "reason": "behavior_cache_unavailable",
            "action": str(intent.action or "unknown"),
        }
    context = _active_app_context()
    try:
        hint = _state.behavior_cache.suggest(
            action=str(intent.action or "unknown"),
            app_name=context.get("app_name", "unknown"),
            window_title=context.get("window_title", ""),
        )
        hint["context"] = context
        return hint
    except Exception as e:
        return {
            "found": False,
            "reason": f"behavior_cache_error:{e}",
            "action": str(intent.action or "unknown"),
            "context": context,
        }


def _audio_interrupt_check(intent: Intent, mode: str) -> dict:
    mode_norm = _normalize_operating_mode(mode)
    category = str(intent.category or "normal").strip().lower()
    applies = mode_norm in {"SAFE", "HYBRID"} and category in {"normal", "destructive", "system"}
    if not applies:
        return {"allowed": True, "applies": False, "reason": "skipped"}
    if not _state.audio_interrupt:
        return {"allowed": True, "applies": True, "reason": "audio_guard_unavailable"}
    status = _state.audio_interrupt.status()
    if not bool(status.get("blocked", False)):
        return {
            "allowed": True,
            "applies": True,
            "reason": "audio_clear",
            "status": status,
        }
    return {
        "allowed": False,
        "applies": True,
        "reason": "audio_interrupt_blocked",
        "status": status,
    }


def _runtime_profile_check(intent: Intent, mode: str) -> dict:
    profile = _normalize_runtime_profile(_state.runtime_profile)
    policy = _runtime_profile_policy(profile)
    mode_norm = _normalize_operating_mode(mode)
    category = str(intent.category or "normal").strip().lower()
    if profile != "enterprise":
        return {
            "allowed": True,
            "applies": False,
            "reason": "standard_profile",
            "profile": profile,
            "policy": policy,
        }
    if (
        mode_norm == "RAW"
        and policy.get("raw_requires_explicit_ack", False)
        and category in {"destructive", "system"}
    ):
        params = intent.params or {}
        ack = bool(params.get("enterprise_raw_ack", False))
        if not ack:
            return {
                "allowed": False,
                "applies": True,
                "reason": "enterprise_raw_requires_ack",
                "profile": profile,
                "policy": policy,
            }
    return {
        "allowed": True,
        "applies": True,
        "reason": "enterprise_policy_ok",
        "profile": profile,
        "policy": policy,
    }


def _host_telemetry_check(intent: Intent, mode: str) -> dict:
    mode_norm = _normalize_operating_mode(mode)
    category = str(intent.category or "normal").strip().lower()
    applies = mode_norm in {"SAFE", "HYBRID"} and category in {"normal", "destructive", "system"}
    if not applies:
        return {
            "allowed": True,
            "applies": False,
            "reason": "skipped",
        }
    if not _state.host_telemetry:
        return {
            "allowed": True,
            "applies": True,
            "reason": "telemetry_unavailable",
        }
    try:
        result = _state.host_telemetry.policy_check(action_category=category, mode=mode_norm)
    except Exception as e:
        logger.debug("Host telemetry check failed: %s", e)
        return {
            "allowed": True,
            "applies": True,
            "reason": "telemetry_error",
        }
    return {
        "allowed": bool(result.get("allowed", True)),
        "applies": True,
        "reason": str(result.get("reason", "host_ok")),
        "severity": str(result.get("severity", "normal")),
        "signals": result.get("signals", []) or [],
        "snapshot": result.get("snapshot", {}) or {},
    }


def _is_click_like_action(action: str) -> bool:
    return str(action or "").strip().lower() in {
        "click",
        "double_click",
        "right_click",
        "click_screen",
    }


def _extract_target_xy(intent: Intent) -> tuple[Optional[int], Optional[int]]:
    params = intent.params or {}
    if "x" not in params or "y" not in params:
        return None, None
    try:
        # Keep raw coordinates as provided by caller.
        # Bounds are validated later by _target_check against real monitor layout.
        x, y = int(params.get("x")), int(params.get("y"))
        return x, y
    except Exception:
        return None, None


def _target_check(intent: Intent) -> dict:
    action = (intent.action or "").strip().lower()
    if not _is_click_like_action(action):
        return {
            "allowed": True,
            "applies": False,
            "reason": "not_click_like",
            "x": None,
            "y": None,
        }
    x, y = _extract_target_xy(intent)
    if x is None or y is None:
        return {
            "allowed": True,
            "applies": False,
            "reason": "no_coordinates",
            "x": None,
            "y": None,
        }
    layout = _monitor_layout_snapshot()
    if not layout:
        return {
            "allowed": True,
            "applies": True,
            "reason": "layout_unavailable",
            "x": int(x),
            "y": int(y),
        }
    for mon in layout:
        left = int(mon.get("left", 0))
        top = int(mon.get("top", 0))
        right = int(mon.get("right", left + int(mon.get("width", 0))))
        bottom = int(mon.get("bottom", top + int(mon.get("height", 0))))
        if left <= int(x) < right and top <= int(y) < bottom:
            return {
                "allowed": True,
                "applies": True,
                "reason": "inside_monitor",
                "x": int(x),
                "y": int(y),
                "monitor_id": int(mon.get("id", 0)),
            }
    return {
        "allowed": False,
        "applies": True,
        "reason": "target_out_of_bounds",
        "x": int(x),
        "y": int(y),
    }


def _orientation_check(intent: Intent, mode: str, target_check: Optional[dict] = None) -> dict:
    mode_norm = _normalize_operating_mode(mode)
    category = (intent.category or "normal").strip().lower()
    applies = mode_norm != "RAW" and category in {"destructive", "system"}
    if not applies:
        return {
            "allowed": True,
            "applies": False,
            "reason": "skipped",
            "mode": mode_norm,
            "category": category,
        }

    active = _active_window_snapshot()
    active_monitor = active.get("monitor_id")
    if active_monitor is None:
        active_monitor = _resolve_active_monitor_id()
    if active.get("handle") is None and not str(active.get("title", "")).strip():
        return {
            "allowed": False,
            "applies": True,
            "reason": "orientation_active_window_unknown",
            "mode": mode_norm,
            "category": category,
            "active_window": active,
            "active_monitor_id": int(active_monitor) if active_monitor is not None else None,
        }
    if active_monitor is None:
        return {
            "allowed": False,
            "applies": True,
            "reason": "orientation_monitor_unknown",
            "mode": mode_norm,
            "category": category,
            "active_window": active,
            "active_monitor_id": None,
        }

    target_monitor = None
    if isinstance(target_check, dict):
        target_monitor = target_check.get("monitor_id")
    if target_monitor is not None:
        try:
            if int(target_monitor) != int(active_monitor):
                return {
                    "allowed": False,
                    "applies": True,
                    "reason": "orientation_monitor_mismatch",
                    "mode": mode_norm,
                    "category": category,
                    "active_window": active,
                    "active_monitor_id": int(active_monitor),
                    "target_monitor_id": int(target_monitor),
                }
        except Exception:
            pass

    return {
        "allowed": True,
        "applies": True,
        "reason": "orientation_ok",
        "mode": mode_norm,
        "category": category,
        "active_window": active,
        "active_monitor_id": int(active_monitor),
        "target_monitor_id": int(target_monitor) if target_monitor is not None else None,
    }


def _ui_semantics_check(intent: Intent, mode: str, task_phase: Optional[str] = None) -> dict:
    action = (intent.action or "").strip().lower()
    if not _is_click_like_action(action):
        return {"allowed": True, "applies": False, "reason": "not_click_like"}
    x, y = _extract_target_xy(intent)
    if x is None or y is None:
        return {"allowed": True, "applies": False, "reason": "no_coordinates"}
    target_check = _target_check(intent)
    # ui_semantics module removed — always return permissive fallback
    return {"allowed": True, "applies": True, "reason": "ui_semantics_unavailable"}


def _ocr_policy(task_phase: Optional[str], criticality: Optional[str], action: Optional[str] = None) -> dict:
    # ui_semantics module removed — return neutral policy
    return {"zoom_factor": 1.0, "native_preferred": False, "reason": "ui_semantics_unavailable"}


def _cursor_drift_check(intent: Intent) -> dict:
    action = (intent.action or "").strip().lower()
    if not _is_click_like_action(action):
        return {"allowed": True, "applies": False, "reason": "not_click_like"}
    params = intent.params or {}
    if "expected_cursor_x" not in params or "expected_cursor_y" not in params:
        return {"allowed": True, "applies": False, "reason": "no_expected_cursor"}
    try:
        expected_x = int(params.get("expected_cursor_x"))
        expected_y = int(params.get("expected_cursor_y"))
    except Exception:
        return {"allowed": False, "applies": True, "reason": "invalid_expected_cursor"}
    try:
        threshold = int(params.get("max_cursor_drift_px", 20))
    except Exception:
        threshold = 20
    threshold = max(3, min(500, threshold))

    cursor = _cursor_snapshot()
    cursor_source = str(cursor.get("source", "none"))
    if cursor_source == "none":
        return {
            "allowed": True,
            "applies": True,
            "reason": "cursor_unavailable",
            "expected": {"x": expected_x, "y": expected_y},
            "actual": None,
            "distance_px": None,
            "threshold_px": int(threshold),
        }
    dx = abs(int(cursor.get("x", 0)) - expected_x)
    dy = abs(int(cursor.get("y", 0)) - expected_y)
    distance = (dx * dx + dy * dy) ** 0.5
    allowed = bool(distance <= threshold)
    return {
        "allowed": allowed,
        "applies": True,
        "reason": "cursor_drift_ok" if allowed else "cursor_drift_exceeded",
        "expected": {"x": expected_x, "y": expected_y},
        "actual": {"x": int(cursor.get("x", 0)), "y": int(cursor.get("y", 0))},
        "distance_px": round(float(distance), 2),
        "threshold_px": int(threshold),
    }


def _user_activity_check(intent: Intent, mode: str) -> dict:
    """
    Guard: block actions that would interrupt the user's active work.

    Rules (SAFE mode only):
    1. If the user is TYPING on the active monitor AND the action involves
       type/key → block with reason="user_typing_active"
       Rationale: typing text into a terminal/editor while the AI also types
       produces garbled output (exactly what happened with the terminal bug).

    2. If the action targets the SAME monitor the user is actively using
       (active monitor) AND safe_mode is not explicitly opted out → require
       the action to specify a non-active target monitor.
       Rationale: respects user's workspace without interfering.

    Only applies in SAFE mode. RAW / HYBRID bypass this check.
    Does NOT apply to read-only actions (scroll, move_mouse, screenshots).
    """
    mode_norm = _normalize_operating_mode(mode)
    if mode_norm == "RAW":
        return {"allowed": True, "applies": False, "reason": "raw_mode_bypass"}

    action = (intent.action or "").strip().lower()
    params = intent.params or {}

    # Read-only actions never interrupt the user
    READ_ONLY = {"scroll", "move_mouse", "screenshot_region", "get_mouse_position",
                 "screenshot", "read_text", "get_elements", "find_element"}
    if action in READ_ONLY:
        return {"allowed": True, "applies": False, "reason": "read_only_action"}

    # Get current perception state
    scene_state = "unknown"
    active_monitor_id = None
    try:
        if _state.perception:
            readiness = _state.perception.get_readiness()
            scene_state = str(readiness.get("scene_state") or readiness.get("task_phase") or "unknown")
            active_monitor_id = _resolve_active_monitor_id()
    except Exception:
        pass

    # Also read scene directly from perception state
    try:
        if _state.perception and hasattr(_state.perception, "_monitor_states"):
            if active_monitor_id and active_monitor_id in _state.perception._monitor_states:
                mon = _state.perception._monitor_states[active_monitor_id]
                if hasattr(mon, "scene") and mon.scene:
                    scene_state = str(mon.scene.state.value if hasattr(mon.scene.state, "value") else mon.scene.state)
    except Exception:
        pass

    is_typing = scene_state in ("typing", "TYPING")

    # Rule 1: user is typing + action wants to type/key
    TYPE_ACTIONS = {"type", "type_text", "key", "hotkey", "press_key"}
    if is_typing and action in TYPE_ACTIONS:
        # Check if a safe_interrupt override was explicitly requested
        if not bool(params.get("safe_interrupt_ok", False)):
            return {
                "allowed": False,
                "applies": True,
                "reason": "user_typing_active",
                "scene_state": scene_state,
                "active_monitor_id": active_monitor_id,
                "hint": (
                    "User is currently typing. Pass safe_interrupt_ok=true to override, "
                    "or target a different monitor with the 'monitor' param."
                ),
            }

    # Rule 2: action targets the user's active monitor (writing actions only)
    WRITING_ACTIONS = {"type", "type_text", "key", "hotkey", "click", "double_click"}
    if action in WRITING_ACTIONS and active_monitor_id is not None:
        action_monitor = params.get("monitor_id") or params.get("monitor")
        if action_monitor is not None:
            try:
                action_monitor_int = int(action_monitor)
                if action_monitor_int == int(active_monitor_id) and is_typing:
                    if not bool(params.get("safe_interrupt_ok", False)):
                        return {
                            "allowed": False,
                            "applies": True,
                            "reason": "action_targets_active_monitor_while_typing",
                            "scene_state": scene_state,
                            "active_monitor_id": int(active_monitor_id),
                            "action_monitor_id": action_monitor_int,
                            "hint": (
                                f"User is typing on M{active_monitor_id}. "
                                f"Target a different monitor or pass safe_interrupt_ok=true."
                            ),
                        }
            except Exception:
                pass

    return {
        "allowed": True,
        "applies": True,
        "reason": "user_not_interrupted",
        "scene_state": scene_state,
        "active_monitor_id": active_monitor_id,
    }


def _cursor_snapshot() -> dict:
    if _state.cursor_tracker:
        try:
            snap = _state.cursor_tracker.sample_once()
            if isinstance(snap, dict):
                return {
                    "x": int(snap.get("x", 0)),
                    "y": int(snap.get("y", 0)),
                    "timestamp_ms": int(snap.get("timestamp_ms", int(time.time() * 1000))),
                    "source": "tracker",
                }
        except Exception:
            pass
    if _state.actions:
        try:
            pos = _state.actions.get_mouse_position()
            return {
                "x": int(pos.get("x", 0)),
                "y": int(pos.get("y", 0)),
                "timestamp_ms": int(time.time() * 1000),
                "source": "actions",
            }
        except Exception:
            pass
    return {
        "x": 0,
        "y": 0,
        "timestamp_ms": int(time.time() * 1000),
        "source": "none",
    }


def _active_window_snapshot() -> dict:
    if not _state.windows:
        return {"handle": None, "title": "", "monitor_id": None}
    try:
        win = _state.windows.get_active_window()
        if not win:
            return {"handle": None, "title": "", "monitor_id": None}
        mid = _monitor_id_for_rect(win.x, win.y, win.width, win.height)
        return {
            "handle": int(getattr(win, "handle", 0) or 0) or None,
            "title": str(getattr(win, "title", "") or "")[:180],
            "monitor_id": int(mid) if mid is not None else None,
        }
    except Exception:
        return {"handle": None, "title": "", "monitor_id": None}


def _os_dialog_status_snapshot(monitor_id: Optional[int] = None) -> dict:
    if not _state.os_surface:
        return {
            "available": False,
            "detected": False,
            "reason": "os_surface_unavailable",
            "monitor_id": int(monitor_id) if monitor_id is not None else None,
        }
    slot, resolved_mid = _latest_slot_for_monitor(monitor_id)
    active = _active_window_snapshot()
    title = str(active.get("title", "") or "")
    try:
        result = _state.os_surface.detect_dialog(
            slot=slot,
            vision=_state.vision,
            active_title=title,
        )
    except Exception as e:
        logger.debug("OS dialog status detection failed: %s", e)
        result = {
            "detected": False,
            "confidence": 0.0,
            "active_title": title[:180],
            "title_hit": False,
            "keyword_hits": [],
            "affordances": [],
            "ocr_preview": "",
            "ocr_block_count": 0,
            "reason": "detect_failed",
        }
    result["available"] = True
    result["monitor_id"] = int(resolved_mid) if resolved_mid is not None else None
    result["active_window"] = active
    return result


def _runtime_pre_action_snapshot(intent: Intent) -> dict:
    slot, slot_monitor = _latest_slot_for_monitor(None)
    return {
        "timestamp_ms": int(time.time() * 1000),
        "action": str(intent.action or ""),
        "target": {"x": _extract_target_xy(intent)[0], "y": _extract_target_xy(intent)[1]},
        "cursor": _cursor_snapshot(),
        "active_window": _active_window_snapshot(),
        "frame_timestamp": float(getattr(slot, "timestamp", 0.0) or 0.0),
        "frame_monitor_id": int(slot_monitor) if slot_monitor is not None else None,
    }


def _runtime_post_action_check(intent: Intent, pre_snapshot: dict) -> dict:
    action = (intent.action or "").strip().lower()
    if not _is_click_like_action(action):
        return {
            "applies": False,
            "passed": True,
            "reason": "not_click_like",
        }
    tx, ty = _extract_target_xy(intent)
    if tx is None or ty is None:
        return {
            "applies": False,
            "passed": True,
            "reason": "no_coordinates",
        }

    cursor = _cursor_snapshot()
    cursor_source = str(cursor.get("source", "none"))
    cursor_available = cursor_source != "none"
    if cursor_available:
        dx = abs(int(cursor.get("x", 0)) - int(tx))
        dy = abs(int(cursor.get("y", 0)) - int(ty))
        distance = (dx * dx + dy * dy) ** 0.5
    else:
        distance = 0.0
    try:
        tolerance_px = int(float(os.environ.get("ILUMINATY_CLICK_POSTCHECK_TOLERANCE_PX", "28")))
    except Exception:
        tolerance_px = 28
    tolerance_px = max(8, min(200, tolerance_px))
    cursor_ok = bool(distance <= tolerance_px) if cursor_available else True

    active_now = _active_window_snapshot()
    active_before = pre_snapshot.get("active_window", {}) if isinstance(pre_snapshot, dict) else {}
    foreground_known = active_now.get("handle") is not None
    if foreground_known:
        foreground_ok = True
    else:
        foreground_ok = active_before.get("handle") is None

    passed = bool(cursor_ok and foreground_ok)
    if passed:
        reason = "ok"
    elif not cursor_available:
        reason = "cursor_unavailable"
    elif not cursor_ok:
        reason = "cursor_not_at_target"
    else:
        reason = "foreground_unknown"

    return {
        "applies": True,
        "passed": passed,
        "reason": reason,
        "target": {"x": int(tx), "y": int(ty)},
        "cursor": cursor,
        "cursor_source": cursor_source,
        "cursor_available": bool(cursor_available),
        "distance_px": round(float(distance), 2),
        "tolerance_px": int(tolerance_px),
        "foreground_before": active_before,
        "foreground_after": active_now,
        "cursor_ok": bool(cursor_ok),
        "foreground_ok": bool(foreground_ok),
    }


def _intent_from_payload(payload: dict) -> Intent:
    if _state.intent is None:
        raise HTTPException(status_code=503, detail="Orchestration not initialized")
    instruction = (payload.get("instruction") or "").strip()
    if instruction:
        return _state.intent.classify_or_default(instruction)

    action = (payload.get("action") or "").strip()
    if not action:
        raise HTTPException(status_code=400, detail="Provide 'instruction' or 'action'")
    params = payload.get("params") or {}
    category = (payload.get("category") or "normal").strip().lower()
    if category not in {"safe", "normal", "destructive", "system"}:
        category = "normal"
    return Intent(
        action=action,
        params=params,
        confidence=1.0,
        raw_input=instruction or action,
        category=category,
    )


def _grounding_request_from_payload(payload: dict, intent: Intent) -> Optional[dict]:
    use_grounding = bool(payload.get("use_grounding", False))
    if not use_grounding:
        return None
    params = payload.get("params") or {}
    query = (
        payload.get("target_query")
        or payload.get("name")
        or params.get("name")
        or params.get("target")
        or intent.raw_input
        or ""
    )
    role = payload.get("target_role") or params.get("role")
    monitor_id = payload.get("monitor_id")
    top_k = int(payload.get("grounding_top_k", 5))
    return {
        "query": str(query).strip(),
        "role": str(role).strip() if role else None,
        "monitor_id": monitor_id,
        "top_k": max(1, min(10, top_k)),
        "inject_coordinates": bool(payload.get("inject_coordinates", True)),
    }


def _enrich_intent_with_grounding(intent: Intent, grounding_check: dict) -> Intent:
    target = grounding_check.get("target") or {}
    center = target.get("center_xy")
    if not center or not isinstance(center, (list, tuple)) or len(center) != 2:
        return intent
    try:
        gx = int(center[0])
        gy = int(center[1])
    except Exception:
        return intent

    action = (intent.action or "").strip().lower()
    click_like = {"click", "double_click", "right_click", "click_screen"}
    if action not in click_like:
        return intent

    params = dict(intent.params or {})
    params.setdefault("x", gx)
    params.setdefault("y", gy)
    if action == "right_click":
        params.setdefault("button", "right")
        action_name = "click"
    else:
        action_name = "click"
    return Intent(
        action=action_name,
        params=params,
        confidence=intent.confidence,
        raw_input=intent.raw_input,
        category=intent.category,
    )


def _build_navigation_cycle_precheck(precheck: dict) -> dict:
    orientation_check = precheck.get("orientation_check", {}) if isinstance(precheck, dict) else {}
    target_check = precheck.get("target_check", {}) if isinstance(precheck, dict) else {}
    grounding_check = precheck.get("grounding_check", {}) if isinstance(precheck, dict) else {}
    readiness_check = precheck.get("readiness_check", {}) if isinstance(precheck, dict) else {}
    context_check = precheck.get("context_check", {}) if isinstance(precheck, dict) else {}

    orient_allowed = bool(orientation_check.get("allowed", True))
    locate_allowed = bool(target_check.get("allowed", True) and grounding_check.get("allowed", True))
    read_allowed = bool(readiness_check.get("allowed", True) and context_check.get("allowed", True))
    blocked = bool(precheck.get("blocked", False))

    return {
        "phase_order": ["orient", "locate", "focus", "read", "act", "verify"],
        "orient": {
            "state": "ok" if orient_allowed else "blocked",
            "ok": orient_allowed,
            "reason": orientation_check.get("reason", "skipped"),
        },
        "locate": {
            "state": "ok" if locate_allowed else "blocked",
            "ok": locate_allowed,
            "reason": (
                target_check.get("reason")
                if not target_check.get("allowed", True)
                else grounding_check.get("reason", "ok")
            ),
        },
        "focus": {
            "state": "pending",
            "ok": None,
            "reason": "awaiting_execution",
        },
        "read": {
            "state": "ok" if read_allowed else "blocked",
            "ok": read_allowed,
            "reason": (
                readiness_check.get("reason")
                if not readiness_check.get("allowed", True)
                else context_check.get("reason", "ok")
            ),
        },
        "act": {
            "state": "blocked" if blocked else "ready",
            "ok": False if blocked else None,
            "reason": "precheck_blocked" if blocked else "precheck_passed",
        },
        "verify": {
            "state": "pending",
            "ok": None,
            "reason": "awaiting_execution",
        },
    }


def _build_navigation_cycle_execution(
    precheck: dict,
    runtime_checks: Optional[dict],
    result_payload: Optional[dict],
    verification_payload: Optional[dict],
) -> dict:
    cycle = _build_navigation_cycle_precheck(precheck)
    rt = runtime_checks or {}
    result_payload = result_payload or {}
    verification_payload = verification_payload or {}

    active_window = ((rt.get("pre_action") or {}).get("active_window") or {})
    focus_ok = bool(active_window.get("handle") is not None or str(active_window.get("title", "")).strip())
    cycle["focus"] = {
        "state": "ok" if focus_ok else "degraded",
        "ok": focus_ok,
        "reason": "focus_acquired" if focus_ok else "focus_unknown",
    }

    act_ok = bool(result_payload.get("success", False))
    cycle["act"] = {
        "state": "ok" if act_ok else "failed",
        "ok": act_ok,
        "reason": str(result_payload.get("message", "action_result_unknown")),
    }

    verify_ok = None
    verify_reason = "verification_skipped"
    if "success" in verification_payload:
        verify_ok = bool(verification_payload.get("success"))
        verify_reason = str(verification_payload.get("message", "verification_result"))
    else:
        post_check = rt.get("post_action") or {}
        if post_check.get("applies"):
            verify_ok = bool(post_check.get("passed", False))
            verify_reason = str(post_check.get("reason", "post_action_check"))

    if verify_ok is None:
        cycle["verify"] = {"state": "skipped", "ok": None, "reason": verify_reason}
    else:
        cycle["verify"] = {
            "state": "ok" if verify_ok else "failed",
            "ok": verify_ok,
            "reason": verify_reason,
        }
    return cycle


def _build_precheck(
    intent: Intent,
    mode: str,
    include_readiness: bool = True,
    context_tick_id: Optional[int] = None,
    max_staleness_ms: Optional[int] = None,
    grounding_request: Optional[dict] = None,
) -> dict:
    mode_norm = _normalize_operating_mode(mode)
    readiness = {
        "readiness": False,
        "uncertainty": 1.0,
        "reasons": ["perception_unavailable"],
        "task_phase": "unknown",
        "active_surface": "unknown",
        "risk_mode": mode_norm.lower(),
    }
    if include_readiness and _state.perception:
        try:
            readiness = _state.perception.get_readiness()
        except Exception as e:
            logger.debug("Failed to read perception readiness during precheck: %s", e)

    domain_policy = readiness.get("domain_policy") or {}
    policy_staleness = {}
    if isinstance(domain_policy, dict):
        policy_staleness = domain_policy.get("max_staleness_ms") or {}
    mode_key = mode_norm.lower()
    default_staleness = 1500
    if isinstance(policy_staleness, dict):
        try:
            candidate = int(policy_staleness.get(mode_key, default_staleness))
            default_staleness = max(1, min(60000, candidate))
        except Exception:
            default_staleness = 1500
    requested_staleness = None
    if max_staleness_ms is not None:
        try:
            requested_staleness = int(max_staleness_ms)
        except Exception:
            requested_staleness = None
    effective_max_staleness_ms = max(1, min(60000, requested_staleness if requested_staleness is not None else default_staleness))

    kill_check = {"allowed": True, "reason": "ok"}
    if _state.safety and _state.safety.is_killed:
        kill_check = {"allowed": False, "reason": "killed"}

    safety_applies = _mode_requires_safety(mode_norm, intent.category)
    safety_check = {"allowed": True, "reason": "skipped"}
    if kill_check["allowed"] and safety_applies and _state.safety:
        safety_check = _state.safety.check_action(intent.action, intent.category)

    readiness_applies = include_readiness and safety_applies and mode_norm != "RAW"
    readiness_check = {"allowed": True, "reason": "skipped"}
    if readiness_applies:
        if readiness.get("readiness"):
            readiness_check = {"allowed": True, "reason": "ready"}
        else:
            reasons = readiness.get("reasons") or ["insufficient_context"]
            reason_txt = ", ".join(str(r) for r in reasons[:3])
            readiness_check = {"allowed": False, "reason": reason_txt}

    context_applies = include_readiness and safety_applies and mode_norm != "RAW"
    context_check = {"allowed": True, "reason": "skipped", "latest_tick_id": readiness.get("tick_id"), "staleness_ms": readiness.get("staleness_ms", 0)}
    if context_applies and _state.perception:
        try:
            if hasattr(_state.perception, "check_context_freshness"):
                context_check = _state.perception.check_context_freshness(
                    context_tick_id=context_tick_id,
                    max_staleness_ms=effective_max_staleness_ms,
                )
            else:
                staleness = int(readiness.get("staleness_ms", 0))
                latest_tick = readiness.get("tick_id")
                if staleness > int(effective_max_staleness_ms):
                    context_check = {
                        "allowed": False,
                        "reason": "context_stale",
                        "latest_tick_id": latest_tick,
                        "staleness_ms": staleness,
                    }
                else:
                    context_check = {
                        "allowed": True,
                        "reason": "fresh",
                        "latest_tick_id": latest_tick,
                        "staleness_ms": staleness,
                    }
        except Exception as e:
            logger.debug("Failed context freshness check during precheck: %s", e)
            context_check = {"allowed": True, "reason": "context_check_unavailable", "latest_tick_id": readiness.get("tick_id"), "staleness_ms": readiness.get("staleness_ms", 0)}

    grounding_applies = bool(grounding_request)
    grounding_check = {
        "allowed": True,
        "reason": "skipped",
        "target": None,
        "confidence": 0.0,
        "candidates": [],
    }
    if grounding_applies:
        if not _state.grounding:
            grounding_check = {
                "allowed": False,
                "reason": "grounding_unavailable",
                "target": None,
                "confidence": 0.0,
                "candidates": [],
            }
        elif not grounding_request.get("query"):
            grounding_check = {
                "allowed": False,
                "reason": "grounding_query_required",
                "target": None,
                "confidence": 0.0,
                "candidates": [],
            }
        else:
            try:
                result = _state.grounding.resolve(
                    query=grounding_request.get("query"),
                    role=grounding_request.get("role"),
                    monitor_id=grounding_request.get("monitor_id"),
                    mode=mode_norm,
                    category=intent.category,
                    context_tick_id=context_tick_id,
                    max_staleness_ms=effective_max_staleness_ms,
                    top_k=grounding_request.get("top_k", 5),
                )
                target = result.get("target")
                grounding_check = {
                    "allowed": not bool(result.get("blocked", False)),
                    "reason": result.get("reason", "grounding_blocked"),
                    "target": target,
                    "confidence": float((target or {}).get("confidence", 0.0)),
                    "candidates": result.get("candidates", []),
                }
            except Exception as e:
                logger.debug("Grounding resolve failed during precheck: %s", e)
                grounding_check = {
                    "allowed": False,
                    "reason": "grounding_error",
                    "target": None,
                    "confidence": 0.0,
                    "candidates": [],
                }

    target_check = _target_check(intent)
    behavior_hint = _behavior_hint(intent)
    profile_check = _runtime_profile_check(intent, mode_norm)
    telemetry_check = _host_telemetry_check(intent, mode_norm)
    audio_interrupt_check = _audio_interrupt_check(intent, mode_norm)
    ui_semantics_check = _ui_semantics_check(
        intent,
        mode_norm,
        task_phase=str(readiness.get("task_phase", "unknown")),
    )
    orientation_check = _orientation_check(intent, mode_norm, target_check=target_check)
    cursor_drift_check = _cursor_drift_check(intent)
    user_activity_check = _user_activity_check(intent, mode_norm)

    blocked = (
        not kill_check["allowed"]
        or not safety_check["allowed"]
        or not readiness_check["allowed"]
        or not context_check["allowed"]
        or not grounding_check["allowed"]
        or not profile_check["allowed"]
        or not telemetry_check["allowed"]
        or not audio_interrupt_check["allowed"]
        or not orientation_check["allowed"]
        or not ui_semantics_check["allowed"]
        or not target_check["allowed"]
        or not cursor_drift_check["allowed"]
        or not user_activity_check["allowed"]
    )
    payload = {
        "mode": mode_norm,
        "intent": intent.to_dict(),
        "readiness": readiness,
        "kill_check": kill_check,
        "safety_applies": safety_applies,
        "safety_check": safety_check,
        "readiness_applies": readiness_applies,
        "readiness_check": readiness_check,
        "context_applies": context_applies,
        "context_tick_id": context_tick_id,
        "max_staleness_ms": int(effective_max_staleness_ms),
        "requested_max_staleness_ms": requested_staleness,
        "context_check": context_check,
        "grounding_applies": grounding_applies,
        "grounding_check": grounding_check,
        "profile_check": profile_check,
        "telemetry_check": telemetry_check,
        "audio_interrupt_check": audio_interrupt_check,
        "behavior_hint": behavior_hint,
        "orientation_check": orientation_check,
        "ui_semantics_check": ui_semantics_check,
        "target_check": target_check,
        "cursor_drift_check": cursor_drift_check,
        "user_activity_check": user_activity_check,
        "blocked": blocked,
    }
    payload["navigation_cycle"] = _build_navigation_cycle_precheck(payload)
    return payload


async def _execute_intent(
    intent: Intent,
    mode: str,
    verify: bool = True,
    context_tick_id: Optional[int] = None,
    max_staleness_ms: Optional[int] = None,
    grounding_request: Optional[dict] = None,
) -> dict:
    if not _state.resolver:
        raise HTTPException(status_code=503, detail="Resolver not initialized")

    worker_intent_id = None
    if _state.perception and hasattr(_state.perception, "register_worker_intent"):
        try:
            worker_intent = _state.perception.register_worker_intent(
                intent.to_dict(),
                source="action_execute",
            )
            worker_intent_id = worker_intent.get("intent_id")
        except Exception as e:
            logger.debug("Workers intent registration failed: %s", e)

    precheck = _build_precheck(
        intent,
        mode,
        include_readiness=True,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
        grounding_request=grounding_request,
    )
    if precheck["blocked"]:
        blocked_reason = (
            precheck["kill_check"]["reason"] if not precheck["kill_check"]["allowed"] else
            precheck["safety_check"]["reason"] if not precheck["safety_check"]["allowed"] else
            precheck.get("readiness_check", {}).get("reason") if not precheck.get("readiness_check", {}).get("allowed", True) else
            precheck.get("context_check", {}).get("reason") if not precheck.get("context_check", {}).get("allowed", True) else
            precheck.get("profile_check", {}).get("reason") if not precheck.get("profile_check", {}).get("allowed", True) else
            precheck.get("telemetry_check", {}).get("reason") if not precheck.get("telemetry_check", {}).get("allowed", True) else
            precheck.get("audio_interrupt_check", {}).get("reason") if not precheck.get("audio_interrupt_check", {}).get("allowed", True) else
            precheck.get("orientation_check", {}).get("reason") if not precheck.get("orientation_check", {}).get("allowed", True) else
            precheck.get("ui_semantics_check", {}).get("reason") if not precheck.get("ui_semantics_check", {}).get("allowed", True) else
            precheck.get("cursor_drift_check", {}).get("reason") if not precheck.get("cursor_drift_check", {}).get("allowed", True) else
            precheck.get("target_check", {}).get("reason") if not precheck.get("target_check", {}).get("allowed", True) else
            precheck.get("user_activity_check", {}).get("reason") if not precheck.get("user_activity_check", {}).get("allowed", True) else
            precheck.get("grounding_check", {}).get("reason", "grounding_blocked")
        )
        if _state.audit:
            _state.audit.log(
                intent.action,
                intent.category,
                intent.params,
                "blocked",
                blocked_reason,
                "suggest",
            )
        if _state.perception and hasattr(_state.perception, "record_worker_verification"):
            try:
                _state.perception.record_worker_verification(
                    intent_id=worker_intent_id,
                    action=intent.action,
                    success=False,
                    reason=blocked_reason,
                    monitor_id=None,
                )
            except Exception as e:
                logger.debug("Workers verification record failed (blocked): %s", e)
        return {
            "precheck": precheck,
            "intent": intent.to_dict(),
            "worker_intent_id": worker_intent_id,
            "runtime_checks": {
                "pre_action": None,
                "completion": None,
                "post_action": None,
            },
            "result": {
                "action": intent.action,
                "success": False,
                "message": blocked_reason,
                "method_used": "blocked",
                "attempts": [],
                "total_ms": 0.0,
            },
            "verification": None,
            "recovery": None,
            "navigation_cycle": _build_navigation_cycle_precheck(precheck),
        }

    profile_policy = _runtime_profile_policy(_state.runtime_profile)
    if (
        bool(profile_policy.get("require_verify_destructive", False))
        and str(intent.category or "normal").strip().lower() in {"destructive", "system"}
        and not bool(verify)
    ):
        reason = "enterprise_verify_required"
        return {
            "precheck": precheck,
            "intent": intent.to_dict(),
            "worker_intent_id": worker_intent_id,
            "runtime_checks": {
                "pre_action": None,
                "completion": None,
                "post_action": None,
            },
            "result": {
                "action": intent.action,
                "success": False,
                "message": reason,
                "method_used": "enterprise_policy",
                "attempts": [],
                "total_ms": 0.0,
            },
            "verification": None,
            "recovery": None,
            "navigation_cycle": _build_navigation_cycle_precheck(precheck),
        }

    if grounding_request and grounding_request.get("inject_coordinates", True):
        intent = _enrich_intent_with_grounding(intent, precheck.get("grounding_check", {}))

    action_owner = "server-action-executor"
    lease = None
    if _state.perception and hasattr(_state.perception, "claim_action_lease"):
        try:
            lease = _state.perception.claim_action_lease(owner=action_owner, ttl_ms=2500, force=False)
        except Exception as e:
            lease = None
            logger.debug("Workers action lease claim failed: %s", e)

    if lease is not None and not lease.get("granted", False):
        busy_reason = str(lease.get("reason") or "arbiter_busy")
        if _state.perception and hasattr(_state.perception, "record_worker_verification"):
            try:
                _state.perception.record_worker_verification(
                    intent_id=worker_intent_id,
                    action=intent.action,
                    success=False,
                    reason=busy_reason,
                    monitor_id=None,
                )
            except Exception as e:
                logger.debug("Workers verification record failed (lease denied): %s", e)
        return {
            "precheck": precheck,
            "intent": intent.to_dict(),
            "worker_intent_id": worker_intent_id,
            "lease": lease,
            "runtime_checks": {
                "pre_action": None,
                "completion": None,
                "post_action": None,
            },
            "result": {
                "action": intent.action,
                "success": False,
                "message": busy_reason,
                "method_used": "arbiter",
                "attempts": [],
                "total_ms": 0.0,
            },
            "verification": None,
            "recovery": None,
            "navigation_cycle": _build_navigation_cycle_precheck(precheck),
        }

    runtime_checks = {
        "pre_action": _runtime_pre_action_snapshot(intent),
        "completion": None,
        "post_action": None,
    }

    pre_state = _state.verifier.capture_pre_state(intent.action, intent.params) if (_state.verifier and verify) else None
    result = None
    verification = None
    recovery = None
    behavior_hint = dict(precheck.get("behavior_hint") or {})
    behavior_context = dict(behavior_hint.get("context") or _active_app_context())
    behavior_pre_delay_ms = int(max(0, min(5000, int(behavior_hint.get("recommended_pre_delay_ms", 0) or 0))))
    behavior_retries = int(max(0, min(3, int(behavior_hint.get("recommended_retries", 0) or 0))))
    recovery_used = False
    recovery_strategy = ""
    try:
        if behavior_pre_delay_ms > 0:
            await asyncio.sleep(float(behavior_pre_delay_ms) / 1000.0)  # non-blocking
        result = _state.resolver.resolve(intent.action, intent.params)
        while result and (not result.success) and behavior_retries > 0:
            behavior_retries -= 1
            result = _state.resolver.resolve(intent.action, intent.params)

        if result and result.success and _state.action_watcher:
            try:
                monitor_hint = runtime_checks.get("pre_action", {}).get("frame_monitor_id")
                if monitor_hint is None:
                    monitor_hint = _resolve_active_monitor_id()
                since_ts = float(runtime_checks.get("pre_action", {}).get("frame_timestamp", 0.0) or 0.0)
                runtime_checks["completion"] = _state.action_watcher.wait_for_settle(
                    monitor_id=int(monitor_hint) if monitor_hint is not None else None,
                    since_timestamp=since_ts,
                    timeout_ms=int(float(os.environ.get("ILUMINATY_ACTION_WATCH_TIMEOUT_MS", "1200"))),
                    settle_ms=int(float(os.environ.get("ILUMINATY_ACTION_WATCH_SETTLE_MS", "180"))),
                    poll_ms=int(float(os.environ.get("ILUMINATY_ACTION_WATCH_POLL_MS", "30"))),
                )
            except Exception as e:
                runtime_checks["completion"] = {
                    "completed": False,
                    "reason": f"watcher_error:{e}",
                }

        if verify and _state.verifier and result is not None and result.success:
            verification = _state.verifier.verify(intent.action, intent.params, pre_state)

        if result and result.success:
            post_check = _runtime_post_action_check(intent, runtime_checks.get("pre_action") or {})
            runtime_checks["post_action"] = post_check
            strict_postcheck = os.environ.get("ILUMINATY_STRICT_CLICK_POSTCHECK", "0") == "1"
            mode_norm = _normalize_operating_mode(mode)
            if (
                post_check.get("applies")
                and not post_check.get("passed", True)
                and strict_postcheck
                and mode_norm != "RAW"
            ):
                result.success = False
                result.message = f"post_click_check_failed:{post_check.get('reason', 'unknown')}"
                result.method_used = f"{result.method_used}+postcheck"

        if not result.success and _state.recovery:
            recovery = _state.recovery.recover(intent.action, intent.params, result.message)
            if recovery.recovered:
                recovery_used = True
                attempts = getattr(recovery, "attempts", None)
                if attempts:
                    try:
                        recovery_strategy = str(attempts[-1].strategy.value)
                    except Exception:
                        recovery_strategy = "unknown"
                result = _state.resolver.resolve(intent.action, intent.params)
                if verify and _state.verifier and result is not None and result.success:
                    verification = _state.verifier.verify(intent.action, intent.params, pre_state)
            else:
                attempts = getattr(recovery, "attempts", None)
                if attempts:
                    try:
                        recovery_strategy = str(attempts[-1].strategy.value)
                    except Exception:
                        recovery_strategy = "unknown"
    finally:
        if lease is not None and _state.perception and hasattr(_state.perception, "release_action_lease"):
            try:
                _state.perception.release_action_lease(
                    owner=action_owner,
                    success=bool(getattr(result, "success", False)),
                    message=str(getattr(result, "message", "resolver_exception")),
                )
            except Exception as e:
                logger.debug("Workers action lease release failed: %s", e)

    if result is None:
        raise HTTPException(status_code=500, detail="Resolver returned no result")

    if _state.audit:
        _state.audit.log(
            intent.action,
            intent.category,
            intent.params,
            "success" if result.success else "failed",
            result.message,
            "suggest",
            duration_ms=result.total_ms,
        )

    if _state.perception:
        try:
            _state.perception.record_action_feedback(
                action=intent.action,
                success=result.success,
                message=result.message,
            )
        except Exception as e:
            logger.debug("Failed to record action feedback into perception trace: %s", e)
        if hasattr(_state.perception, "record_worker_verification"):
            try:
                verify_reason = result.message
                verify_success = bool(result.success)
                if verification and hasattr(verification, "success"):
                    verify_success = bool(verification.success)
                    verify_reason = str(getattr(verification, "message", verify_reason))
                _state.perception.record_worker_verification(
                    intent_id=worker_intent_id,
                    action=intent.action,
                    success=verify_success,
                    reason=verify_reason,
                    monitor_id=None,
                )
            except Exception as e:
                logger.debug("Workers verification record failed: %s", e)

    if _state.behavior_cache:
        try:
            _state.behavior_cache.record_outcome(
                app_name=str(behavior_context.get("app_name", "unknown")),
                window_title=str(behavior_context.get("window_title", "")),
                action=str(intent.action or "unknown"),
                params=intent.params or {},
                success=bool(result.success),
                reason=str(result.message or ""),
                method_used=str(getattr(result, "method_used", "") or ""),
                recovery_used=bool(recovery_used),
                recovery_strategy=str(recovery_strategy or ""),
                duration_ms=float(getattr(result, "total_ms", 0.0) or 0.0),
            )
        except Exception as e:
            logger.debug("Failed to persist app behavior outcome: %s", e)

    result_payload = result.to_dict()
    verification_payload = verification.to_dict() if verification else None
    navigation_cycle = _build_navigation_cycle_execution(
        precheck=precheck,
        runtime_checks=runtime_checks,
        result_payload=result_payload,
        verification_payload=verification_payload,
    )

    return {
        "precheck": precheck,
        "intent": intent.to_dict(),
        "worker_intent_id": worker_intent_id,
        "lease": lease,
        "runtime_checks": runtime_checks,
        "result": result_payload,
        "verification": verification_payload,
        "recovery": recovery.to_dict() if recovery else None,
        "behavior": {
            "hint": behavior_hint,
            "context": behavior_context,
            "pre_delay_ms_applied": behavior_pre_delay_ms,
        },
        "navigation_cycle": navigation_cycle,
    }


# ─── Lifespan ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Watchdog: detect if capture stalls and restart it
    _watchdog_log = logging.getLogger("iluminaty.watchdog")
    async def _watchdog_loop():
        last_frame_count = 0
        stall_count = 0
        while True:
            await asyncio.sleep(30)
            try:
                if not _state.buffer:
                    continue
                current = getattr(_state.buffer, '_frame_count', 0)
                if current == last_frame_count and last_frame_count > 0:
                    stall_count += 1
                    _watchdog_log.warning(
                        "Capture stall detected (%dx) — frame_count=%d", stall_count, current
                    )
                    if stall_count >= 3:  # stalled for 90s
                        _watchdog_log.error("Capture stalled 90s — attempting restart")
                        try:
                            if _state.capture and hasattr(_state.capture, 'stop'):
                                _state.capture.stop()
                            await asyncio.sleep(2)
                            if _state.capture and hasattr(_state.capture, 'start'):
                                _state.capture.start()
                            stall_count = 0
                            _watchdog_log.info("Capture restarted by watchdog")
                        except Exception as e:
                            _watchdog_log.error("Watchdog restart failed: %s", e)
                else:
                    stall_count = 0
                last_frame_count = current
            except Exception:
                pass

    watchdog_task = asyncio.create_task(_watchdog_loop())
    yield
    watchdog_task.cancel()
    # Cleanup
    if _state.cursor_tracker:
        try:
            _state.cursor_tracker.stop()
        except Exception:
            pass
    if _state.capture and _state.capture.is_running:
        _state.capture.stop()
    if _state.buffer:
        _state.buffer.flush()
    if _state.behavior_cache:
        try:
            _state.behavior_cache.close()
        except Exception:
            pass




# ─── App ───
app = FastAPI(
    title="ILUMINATY",
    description="Real-time visual perception for AI. Zero-disk, RAM-only.",
    version="1.0.0",
    lifespan=lifespan,
)

def _get_cors_origins() -> list[str]:
    """Build CORS origins list including configured host:port."""
    origins = ["http://127.0.0.1:8420", "http://localhost:8420", "tauri://localhost"]
    # Add dynamically configured host:port if different
    if _state.capture and hasattr(_state.capture, 'config'):
        cfg = _state.capture.config
        host = getattr(cfg, 'host', '127.0.0.1')
        port = getattr(cfg, 'port', 8420)
        dynamic = f"http://{host}:{port}"
        if dynamic not in origins:
            origins.append(dynamic)
    return origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # H-4: added DELETE + PUT
    allow_headers=["Content-Type", "x-api-key", "authorization"],
)


# ─── License Gate Middleware ───

class LicenseGateMiddleware(BaseHTTPMiddleware):
    """Blocks Pro-only endpoints for Free plan users.

    NOTE: uses cached singleton — no I/O per request.
    License is validated once at startup in init_license().

    M-3 SECURITY NOTE: In the open-source build, LicenseManager.is_endpoint_allowed()
    always returns True — this middleware is effectively a NO-OP gate.
    Do NOT rely on it for security enforcement. Auth is handled by _check_auth().
    """
    async def dispatch(self, request: StarletteRequest, call_next):
        # Fast path: skip check for public/health endpoints
        path = request.url.path
        if path in ("/health", "/", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        lic = get_license()
        if lic and not lic.is_endpoint_allowed(path):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "pro_required",
                    "message": "This endpoint requires ILUMINATY Pro ($29/mo).",
                    "endpoint": path,
                    "current_plan": lic.plan.value,
                    "upgrade_url": "https://iluminaty.dev/#pricing",
                },
            )
        return await call_next(request)

app.add_middleware(LicenseGateMiddleware)

# Static files — viewer UI
import pathlib as _pathlib
_static_dir = _pathlib.Path(__file__).parent / "static"
if _static_dir.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/ui", StaticFiles(directory=str(_static_dir), html=True), name="static")


# ─── Endpoints ───

@app.get("/health")
async def health():
    return {
        "status": "alive",
        "capture_running": _state.capture.is_running if _state.capture else False,
        "buffer_slots": _state.buffer.size if _state.buffer else 0,
    }


@app.post("/auth/login", response_class=Response)
async def auth_login(request: StarletteRequest):
    """H-2 fix: POST login sets HttpOnly cookie instead of exposing key in URL."""
    form = await request.form()
    token = str(form.get("token", ""))
    if _state.api_key and token != _state.api_key:
        return Response(content="Invalid API key", status_code=401)
    resp = Response(status_code=303, headers={"Location": "/"})
    resp.set_cookie(
        "iluminaty_auth", token,
        httponly=True, samesite="strict", max_age=86400 * 7,
    )
    return resp


@app.get("/", response_class=Response)
async def dashboard(
    request: StarletteRequest,
    x_api_key: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
):
    """Dashboard web en vivo. Auth via header, cookie, or ?token= query param."""
    # H-2 fix: also accept HttpOnly cookie set by /auth/login
    cookie_token = request.cookies.get("iluminaty_auth")
    if _state.api_key:
        provided = x_api_key or token or cookie_token
        if provided != _state.api_key:
            # Return a simple auth page instead of 401
            auth_html = '''<!DOCTYPE html>
<html><head><title>ILUMINATY</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a12;color:#e4e4ee;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh}
.box{text-align:center;max-width:360px}
h1{color:#00ff88;font-size:20px;letter-spacing:3px;margin-bottom:24px}
input{background:#12121e;border:1px solid #1a1a30;color:#e4e4ee;padding:10px 16px;border-radius:6px;width:100%;font-size:14px;margin-bottom:12px;outline:none}
input:focus{border-color:#00ff88}
button{background:transparent;border:1px solid #00ff88;color:#00ff88;padding:10px 32px;border-radius:6px;cursor:pointer;font-size:14px;width:100%}
button:hover{background:rgba(0,255,136,0.1)}
p{color:#555;font-size:12px;margin-top:16px}
</style></head><body>
<div class="box">
<h1>ILUMINATY</h1>
<form method="POST" action="/auth/login">
<input id="k" name="token" type="password" placeholder="Enter API key" autofocus/>
<button type="submit">ACCESS</button>
</form>
<p>Authentication required to access the dashboard.</p>
</div></body></html>'''
            return Response(content=auth_html, media_type="text/html")
    return Response(content=DASHBOARD_HTML, media_type="text/html")


@app.get("/buffer/stats")
async def buffer_stats(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.buffer:
        raise HTTPException(503, "Buffer not initialized")
    stats = _state.buffer.stats
    stats["capture_running"] = _state.capture.is_running if _state.capture else False
    stats["current_fps"] = _state.capture.current_fps if _state.capture else 0
    stats["ws_clients"] = len(_state.ws_clients)
    return stats


@app.get("/frame/latest")
async def frame_latest(
    base64_encode: bool = Query(False, alias="base64"),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id. Defaults to latest global frame."),
    x_api_key: Optional[str] = Header(None),
):
    """Último frame. Sin base64 devuelve JPEG raw. Con base64 devuelve JSON."""
    _check_auth(x_api_key)
    if not _state.buffer:
        raise HTTPException(503, "Buffer not initialized")
    
    if monitor_id is not None and hasattr(_state.buffer, "get_latest_for_monitor"):
        slot = _state.buffer.get_latest_for_monitor(int(monitor_id))
    else:
        slot = _state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")
    
    if base64_encode:
        return JSONResponse(_frame_to_json(slot, include_base64=True))
    else:
        return Response(
            content=slot.frame_bytes,
            media_type=slot.mime_type,
            headers={
                "X-Timestamp": str(slot.timestamp),
                "X-Change-Score": str(slot.change_score),
                "X-Frame-Width": str(slot.width),
                "X-Frame-Height": str(slot.height),
                "X-Format": slot.mime_type,
                "X-Monitor-Id": str(getattr(slot, "monitor_id", 0)),
            },
        )


@app.get("/frames")
async def frames(
    last: Optional[int] = Query(None, ge=1, le=100),
    seconds: Optional[float] = Query(None, ge=0.1, le=300),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id filter."),
    include_images: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    """
    Múltiples frames.
    - ?last=5 → últimos 5 frames
    - ?seconds=10 → frames de los últimos 10 segundos
    - ?include_images=true → incluye base64 (cuidado: puede ser grande)
    """
    _check_auth(x_api_key)
    if not _state.buffer:
        raise HTTPException(503, "Buffer not initialized")
    
    if seconds is not None:
        slots = _state.buffer.get_since(seconds)
    elif last is not None:
        slots = _state.buffer.get_latest_n(last)
    else:
        slots = _state.buffer.get_latest_n(5)

    if monitor_id is not None:
        try:
            mid = int(monitor_id)
            slots = [s for s in slots if int(getattr(s, "monitor_id", 0)) == mid]
        except Exception:
            slots = []

    if last is not None and monitor_id is not None and len(slots) > int(last):
        slots = slots[-int(last):]

    return {
        "count": len(slots),
        "monitor_id": int(monitor_id) if monitor_id is not None else None,
        "frames": [_frame_to_json(s, include_base64=include_images) for s in slots],
    }


@app.post("/config")
async def update_config(
    fps: Optional[float] = None,
    quality: Optional[int] = None,
    image_format: Optional[str] = None,
    max_width: Optional[int] = None,
    skip_unchanged: Optional[bool] = None,
    smart_quality: Optional[bool] = None,
    x_api_key: Optional[str] = Header(None),
):
    """Cambiar configuracion en caliente sin reiniciar."""
    _check_auth(x_api_key)
    if not _state.capture:
        raise HTTPException(503, "Capture not initialized")
    
    changed = []
    # BUG-006 fix: lock during config update
    with _state.lock:
        if fps is not None:
            _state.capture.config.fps = fps
            _state.capture._current_fps = fps
            changed.append(f"fps={fps}")
        if quality is not None:
            _state.capture.config.quality = max(10, min(95, quality))
            changed.append(f"quality={quality}")
        if image_format is not None and image_format in ("jpeg", "webp", "png"):
            _state.capture.config.image_format = image_format
            changed.append(f"image_format={image_format}")
        if max_width is not None:
            _state.capture.config.max_width = max(320, min(3840, max_width))
            changed.append(f"max_width={max_width}")
        if skip_unchanged is not None:
            _state.capture.config.skip_unchanged = skip_unchanged
            changed.append(f"skip_unchanged={skip_unchanged}")
        if smart_quality is not None:
            _state.capture.config.smart_quality = smart_quality
            changed.append(f"smart_quality={smart_quality}")
    
    return {"updated": changed}


_CONFIG_FILE = pathlib.Path(__file__).parent.parent / "iluminaty_config.json"

@app.get("/config/vlm")
async def get_vlm_config(x_api_key: Optional[str] = Header(None)):
    """Lee configuracion VLM (GPU/CPU, modelo, etc.)"""
    _check_auth(x_api_key)
    try:
        if _CONFIG_FILE.exists():
            return json.loads(_CONFIG_FILE.read_text())
        return {"vlm_device": "auto", "vlm_enabled": True}
    except Exception as e:
        return {"error": str(e)}


@app.post("/config/vlm")
async def set_vlm_config(
    vlm_device: Optional[str] = None,
    vlm_enabled: Optional[bool] = None,
    x_api_key: Optional[str] = Header(None),
):
    """Guarda configuracion VLM. Requiere reinicio para aplicar cambio de GPU/CPU."""
    _check_auth(x_api_key)
    try:
        cfg = {}
        if _CONFIG_FILE.exists():
            cfg = json.loads(_CONFIG_FILE.read_text())
        if vlm_device is not None and vlm_device in ("auto", "cuda", "cpu"):
            cfg["vlm_device"] = vlm_device
        if vlm_enabled is not None:
            cfg["vlm_enabled"] = vlm_enabled
        _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        return {"ok": True, "config": cfg, "restart_required": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/buffer/flush")
async def flush_buffer(x_api_key: Optional[str] = Header(None)):
    """Destruir todo el contenido visual del buffer. Irreversible."""
    _check_auth(x_api_key)
    if _state.buffer:
        _state.buffer.flush()
    return {"status": "flushed", "slots": 0}


@app.post("/capture/start")
async def start_capture(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _state.capture and not _state.capture.is_running:
        _state.capture.start()
    return {"status": "running"}


@app.post("/capture/stop")
async def stop_capture(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _state.capture and _state.capture.is_running:
        _state.capture.stop()
    return {"status": "stopped"}


# ─── WebSocket Stream ───

@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket, token: Optional[str] = Query(None)):
    """
    WebSocket que envía frames en real-time.
    Cada mensaje es JSON con base64 del frame.
    Auth: pass ?token=<key> or X-API-Key header.
    """
    # C-2 fix: authenticate before accepting
    if not _NO_AUTH:
        provided = token or ws.headers.get("x-api-key") or ws.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            _check_auth(provided)
        except HTTPException as e:
            code = 4401 if int(getattr(e, "status_code", 401) or 401) == 401 else 4403
            await ws.close(code=code, reason="Unauthorized: invalid/missing API key or server not configured")
            return
    await ws.accept()
    _state.ws_clients.add(ws)
    
    try:
        last_hash = None
        while True:
            if not _state.buffer:
                await asyncio.sleep(1)
                continue
                
            slot = _state.buffer.get_latest()
            if slot and slot.phash != last_hash:
                await ws.send_json(_frame_to_json(slot, include_base64=True))
                last_hash = slot.phash
            
            # Enviar al ritmo del FPS actual
            fps = max(_state.capture.current_fps if _state.capture else 1.0, 0.1)
            await asyncio.sleep(1.0 / fps)
            
    except WebSocketDisconnect:
        logger.debug("Client disconnected from /ws/stream")
    finally:
        _state.ws_clients.discard(ws)


@app.websocket("/perception/stream")
async def perception_stream(
    ws: WebSocket,
    interval_ms: int = Query(300, ge=100, le=2000),
    include_events: bool = Query(True),
    token: Optional[str] = Query(None),
):
    """
    IPA v2 semantic stream for external AI agents.
    Sends WorldState snapshots (+ optional recent events) in real time.
    Auth: pass ?token=<key> or X-API-Key header.
    """
    # C-2 fix: authenticate before accepting
    if not _NO_AUTH:
        provided = token or ws.headers.get("x-api-key") or ws.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            _check_auth(provided)
        except HTTPException as e:
            code = 4401 if int(getattr(e, "status_code", 401) or 401) == 401 else 4403
            await ws.close(code=code, reason="Unauthorized: invalid/missing API key or server not configured")
            return
    await ws.accept()
    try:
        last_world_ts = 0
        last_event_ts = 0.0
        last_visual_ts = int(time.time() * 1000)
        while True:
            if not _state.perception:
                await ws.send_json({
                    "type": "perception_world",
                    "status": "offline",
                    "timestamp_ms": int(time.time() * 1000),
                })
                await asyncio.sleep(interval_ms / 1000)
                continue

            world = _state.perception.get_world_state()
            world_ts = world.get("timestamp_ms", 0)
            if world_ts != last_world_ts:
                payload = {
                    "type": "perception_world",
                    "status": "active",
                    "tick_id": world.get("tick_id"),
                    "world": world,
                    "readiness": _state.perception.get_readiness(),
                }
                if include_events:
                    raw_events = _state.perception.get_events(last_seconds=3, min_importance=0.1)
                    events = []
                    for e in raw_events:
                        if e.timestamp <= last_event_ts:
                            continue
                        events.append({
                            "timestamp": e.timestamp,
                            "type": e.event_type,
                            "description": e.description,
                            "importance": e.importance,
                            "uncertainty": e.uncertainty,
                            "monitor": e.monitor,
                        })
                    if events:
                        last_event_ts = events[-1]["timestamp"]
                    payload["events"] = events
                visual_delta = _state.perception.get_visual_facts_delta(last_visual_ts)
                if visual_delta:
                    payload["visual_facts_delta"] = visual_delta
                    latest = max(int(v.get("timestamp_ms", last_visual_ts)) for v in visual_delta)
                    last_visual_ts = max(last_visual_ts, latest)
                await ws.send_json(payload)
                last_world_ts = world_ts

            await asyncio.sleep(interval_ms / 1000)
    except WebSocketDisconnect:
        logger.debug("Client disconnected from /perception/stream")


def init_server(
    buffer: RingBuffer,
    capture: ScreenCapture,
    api_key: Optional[str] = None,
    audio_buffer: Optional[AudioRingBuffer] = None,
    audio_capture: Optional[AudioCapture] = None,
    enable_actions: bool = False,
    autonomy_level: str = "suggest",
    browser_debug_port: int = 9222,
    file_sandbox_paths: Optional[list[str]] = None,
    iluminaty_key: Optional[str] = None,
    visual_profile: str = "core_ram",
    vision_plus_disk: bool = False,
    deep_loop_hz: float = 1.0,
    fast_loop_hz: float = 10.0,
    enable_voice: bool = False,
    voice_model: Optional[str] = None,
):
    """Inyecta las dependencias al modulo del server (thread-safe)."""
    with _state.lock:
        _state.bootstrap_warnings = []
        # ─── License validation ───
        # init_license() already calls validate() internally — no asyncio needed
        _state.license = init_license(api_key=iluminaty_key)
        try:
            plan_value = str(_state.license.plan.value if _state.license else "free")
            if plan_value == "enterprise":
                _state.runtime_profile = "enterprise"
            else:
                _state.runtime_profile = _normalize_runtime_profile(_state.runtime_profile)
        except Exception:
            _state.runtime_profile = _normalize_runtime_profile(_state.runtime_profile)
        # ─── Core (v0.5) ───
        _state.buffer = buffer
        _state.capture = capture
        _state.api_key = api_key
        _state.vision = VisionIntelligence()
        _state.diff = SmartDiff(grid_cols=8, grid_rows=6)
        _state.audio_buffer = audio_buffer
        _state.audio_capture = audio_capture
        if _state.behavior_cache:
            try:
                _state.behavior_cache.close()
            except Exception:
                pass
        _state.transcriber = TranscriptionEngine()
        try:
            _state.audio_interrupt = AudioInterruptDetector(
                hold_ms=int(float(os.environ.get("ILUMINATY_AUDIO_INTERRUPT_HOLD_MS", "12000"))),
                alert_level_threshold=float(os.environ.get("ILUMINATY_AUDIO_ALERT_THRESHOLD", "0.55")),
            )
        except Exception as e:
            _state.audio_interrupt = None
            _state.bootstrap_warnings.append(f"audio_interrupt_init_failed: {e}")
        try:
            cache_path = os.environ.get("ILUMINATY_APP_BEHAVIOR_DB", "").strip() or None
            _state.behavior_cache = AppBehaviorCache(db_path=cache_path)
        except Exception as e:
            _state.behavior_cache = None
            _state.bootstrap_warnings.append(f"app_behavior_cache_init_failed: {e}")
        # ─── Voice TTS (optional) ───
        _voice_enabled = enable_voice or os.environ.get("ILUMINATY_VOICE", "0") == "1"
        if _voice_enabled:
            try:
                _force = (voice_model or os.environ.get("ILUMINATY_VOICE_ENGINE") or "").lower() or None
                _state.voice = VoiceSynth(force_engine=_force)
                _state.auto_narrator = AutoNarrator(voice_synth=_state.voice)
                if _state.voice.available:
                    logger.info("Voice engine ready: %s", _state.voice.engine_name)
                else:
                    _state.bootstrap_warnings.append(
                        "voice: no TTS engine found. Install: pip install edge-tts"
                    )
            except Exception as e:
                _state.voice = None
                _state.auto_narrator = None
                _state.bootstrap_warnings.append(f"voice_init_failed: {e}")

        _state.monitor_mgr = MonitorManager()
        _state.monitor_mgr.refresh()

        # Monitor change listener — zero-polling, event-driven.
        # Windows: hooks WM_DISPLAYCHANGE via a minimal hidden window message loop.
        # Linux/Mac: falls back to a one-time registration (manual refresh via /monitors/refresh).
        # Triggers on: plug/unplug, resolution change, orientation change, DPI change.
        # RAM cost: ~0 (one hidden HWND + message pump thread, no polling).
        import threading as _thr
        import sys as _sys

        def _start_display_change_listener():
            if _sys.platform != "win32":
                return  # Linux/Mac: /monitors/refresh endpoint handles it

            try:
                import ctypes, ctypes.wintypes
                user32 = ctypes.windll.user32

                WM_DISPLAYCHANGE = 0x007E
                WM_DESTROY       = 0x0002
                WS_OVERLAPPED    = 0x00000000
                HWND_MESSAGE     = ctypes.wintypes.HWND(-3)

                # Callback: called by Windows on every display change
                def wnd_proc(hwnd, msg, wparam, lparam):
                    if msg == WM_DISPLAYCHANGE:
                        logger.info(
                            "WM_DISPLAYCHANGE received — refreshing monitor layout."
                        )
                        try:
                            _state.monitor_mgr.refresh()
                            if _state.perception:
                                _state.perception.reinitialize_monitors()
                            # Mark spatial context stale so next POST-ACTION STATE warns agent
                            _post_action_context._last_n_monitors = None
                        except Exception as _e:
                            logger.warning("Monitor reinit failed: %s", _e)
                    elif msg == WM_DESTROY:
                        user32.PostQuitMessage(0)
                    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

                WndProc = ctypes.WINFUNCTYPE(
                    ctypes.c_long, ctypes.wintypes.HWND,
                    ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
                )
                wnd_proc_cb = WndProc(wnd_proc)

                WNDCLASSEX = type("WNDCLASSEX", (ctypes.Structure,), {"_fields_": [
                    ("cbSize",        ctypes.c_uint),
                    ("style",         ctypes.c_uint),
                    ("lpfnWndProc",   WndProc),
                    ("cbClsExtra",    ctypes.c_int),
                    ("cbWndExtra",    ctypes.c_int),
                    ("hInstance",     ctypes.wintypes.HINSTANCE),
                    ("hIcon",         ctypes.wintypes.HICON),
                    ("hCursor",       ctypes.wintypes.HANDLE),
                    ("hbrBackground", ctypes.wintypes.HBRUSH),
                    ("lpszMenuName",  ctypes.wintypes.LPCWSTR),
                    ("lpszClassName", ctypes.wintypes.LPCWSTR),
                    ("hIconSm",       ctypes.wintypes.HICON),
                ]})

                cls = WNDCLASSEX()
                cls.cbSize = ctypes.sizeof(WNDCLASSEX)
                cls.lpfnWndProc = wnd_proc_cb
                cls.lpszClassName = "IluminatyDisplayWatcher"
                cls.hInstance = user32.GetModuleHandleW(None)

                if not user32.RegisterClassExW(ctypes.byref(cls)):
                    return  # class may already be registered — non-fatal

                hwnd = user32.CreateWindowExW(
                    0, "IluminatyDisplayWatcher", "IluminatyDisplayWatcher",
                    WS_OVERLAPPED, 0, 0, 0, 0,
                    HWND_MESSAGE, None, cls.hInstance, None
                )
                if not hwnd:
                    return

                # Run message loop until process exits
                msg = ctypes.wintypes.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

            except Exception as _e:
                logger.debug("Display change listener failed to start: %s", _e)

        _thr.Thread(
            target=_start_display_change_listener,
            daemon=True,
            name="display-change-listener"
        ).start()

        # Perception Engine — continuous vision processing (the AI's visual cortex)
        # Wired AFTER monitor_mgr/diff/context are initialized (IPA Phase 1.3)
        try:
            from .perception import PerceptionEngine
            _state.perception = PerceptionEngine(
                monitor_mgr=_state.monitor_mgr,
                smart_diff=_state.diff,
                visual_profile=visual_profile,
                enable_disk_spool=vision_plus_disk,
                deep_loop_hz=deep_loop_hz,
                fast_loop_hz=fast_loop_hz,
            )
            _state.perception.start(buffer)
            if hasattr(_state.perception, "set_capture_controller"):
                _state.perception.set_capture_controller(_state.capture)
            _state.perception.set_risk_mode(_state.operating_mode.lower())
        except Exception as e:
            _state.perception = None
            _state.bootstrap_warnings.append(f"perception_init_failed: {e}")

        _state.watchdog = Watchdog()
        try:
            _state.host_telemetry = HostTelemetry()
        except Exception as e:
            _state.host_telemetry = None
            _state.bootstrap_warnings.append(f"host_telemetry_init_failed: {e}")
        try:
            _state.os_surface = OSSurfaceSignals(
                watchdog=_state.watchdog,
                audio_interrupt=_state.audio_interrupt,
            )
        except Exception as e:
            _state.os_surface = None
            _state.bootstrap_warnings.append(f"os_surface_init_failed: {e}")
        # IPA v3 bridge — connect to ring buffer after capture is initialized
        try:
            if _state.buffer is not None:
                import os as _os
                fps = float(_os.environ.get('ILUMINATY_IPA_FPS', '3.0'))
                _state.ipa_bridge = IPABridge(_state.buffer, fps=fps)
                _state.ipa_bridge.start()
        except Exception as _e:
            _state.bootstrap_warnings.append(f'ipa_bridge_init_failed: {_e}')

        # ─── v1.0: Computer Use capas ───

        # Capa 7: Safety
        _state.safety = SafetySystem()
        _state.audit = AuditLog()

        # Capa 1: OS Control
        _state.actions = ActionBridge(enabled=enable_actions)
        _state.windows = WindowManager()
        _state.clipboard = ClipboardManager()
        _state.process_mgr = ProcessManager()
        try:
            if _state.cursor_tracker:
                _state.cursor_tracker.stop()
            cursor_poll_ms = int(float(os.environ.get("ILUMINATY_CURSOR_POLL_MS", "20")))
            _state.cursor_tracker = CursorTracker(
                actions=_state.actions,
                poll_ms=max(5, min(1000, cursor_poll_ms)),
            )
            _state.cursor_tracker.start()
        except Exception as e:
            _state.cursor_tracker = None
            _state.bootstrap_warnings.append(f"cursor_tracker_init_failed: {e}")
        try:
            _state.action_watcher = ActionCompletionWatcher(buffer=_state.buffer)
        except Exception as e:
            _state.action_watcher = None
            _state.bootstrap_warnings.append(f"action_watcher_init_failed: {e}")

        # Capa 2: UI Intelligence
        _state.ui_tree = UITree()
        # Conectar UI Tree al ActionBridge
        if _state.ui_tree.available:
            _state.actions.set_ui_tree(_state.ui_tree)

        # SmartLocateEngine — fusiona UITree + OCR para coordenadas exactas
        def _ocr_blocks_for_monitor(monitor_id=None):
            """OCR callback: devuelve bloques con coords para smart_locate."""
            try:
                slot, resolved_mid = _latest_slot_for_monitor(monitor_id)
                if not slot or not _state.vision:
                    return []
                if not _state.vision.ocr.available:
                    return []
                result = _state.vision.ocr.extract_text(slot.frame_bytes)
                blocks = result.get("blocks", []) if result else []
                # Translate coords to global desktop space using monitor bounds
                if monitor_id is not None and _state.monitor_mgr:
                    mon = _state.monitor_mgr.get_monitor(monitor_id)
                    if mon:
                        for b in blocks:
                            b["x"] = b.get("x", 0) + mon.left
                            b["y"] = b.get("y", 0) + mon.top
                return blocks
            except Exception:
                return []

        # Build monitor bounds dict for coordinate validation
        def _monitor_bounds_dict():
            if not _state.monitor_mgr:
                return {}
            return {
                m.id: {"left": m.left, "top": m.top, "width": m.width, "height": m.height}
                for m in _state.monitor_mgr.monitors
            }

        _state.smart_locator = SmartLocateEngine(
            ui_tree=_state.ui_tree,
            ocr_fn=_ocr_blocks_for_monitor,
            monitor_bounds=_monitor_bounds_dict(),
        )

        # fast_ocr replaces the subprocess OCR worker — no subprocess needed
        logger.info("fast_ocr engine: %s", _fast_ocr_engine())

        # Watch Engine — active monitoring without token consumption
        def _ocr_text_for_watch(monitor_id=None):
            """Fast OCR text for watch conditions — uses perception cache."""
            try:
                if _state.perception and _state.monitor_mgr:
                    for mon in _state.monitor_mgr.monitors:
                        if monitor_id is None or mon.id == monitor_id:
                            ms = _state.perception._get_monitor_state(mon.id)
                            if ms and ms.last_ocr_text:
                                return ms.last_ocr_text
            except Exception:
                pass
            return ""

        def _element_found_for_watch(query):
            """Check if element is visible via smart_locate cache."""
            try:
                if _state.smart_locator:
                    result = _state.smart_locator.locate(query)
                    return result is not None
            except Exception:
                pass
            return False

        # windows_fn: returns list of visible windows for window_opened/closed detection
        def _windows_for_watch():
            if _state.windows:
                return [w.__dict__ if hasattr(w, '__dict__') else w
                        for w in _state.windows.list_windows()]
            return []

        _state.watch_engine = WatchEngine(
            perception=_state.perception,   # push-based: wakes on new events
            ocr_fn=_ocr_text_for_watch,
            ui_tree_fn=_element_found_for_watch,
            windows_fn=_windows_for_watch,
        )

        # Recording Engine — opt-in local recording (disabled by default)
        try:
            from .recording import RecordingEngine
            _state.recording_engine = RecordingEngine(_state.buffer)
        except Exception as _rec_err:
            _state.recording_engine = None
            _state.bootstrap_warnings.append(f"recording_engine_init_failed: {_rec_err}")

        # Capa 5: File System
        # Block sensitive config paths from filesystem access
        _appdata = os.environ.get("APPDATA", "")
        _home = os.path.expanduser("~")
        _blocked = [
            # MCP/Claude config — prevent write-based hijacking
            os.path.join(_appdata, "Claude"),
            os.path.join(_appdata, "Code"),
            os.path.join(_home, ".config", "claude"),
            os.path.join(_home, ".mcp.json"),
            # Credentials / SSH
            os.path.join(_home, ".ssh"),
            os.path.join(_home, ".gnupg"),
            os.path.join(_home, ".aws"),
            # Windows sensitive
            "C:\\Windows\\System32",
            "C:\\Windows\\SysWOW64",
        ]
        _state.filesystem = FileSystemSandbox(
            # M-1 fix: default to user home workspace, not project root "."
            # "." would allow reading .env, source code, venv, etc.
            allowed_paths=file_sandbox_paths or [
                str(pathlib.Path.home() / "iluminaty-workspace"),
                str(pathlib.Path.home() / "Documents"),
                str(pathlib.Path.home() / "Desktop"),
            ],
            blocked_paths=_blocked,
        )

        # Capa 6: Orchestration (conecta todas las capas)
        _state.resolver = ActionResolver()
        _state.resolver.set_layers(
            actions=_state.actions,
            ui_tree=_state.ui_tree,
            filesystem=_state.filesystem,
        )
        _state.intent = IntentClassifier()
        _state.verifier = ActionVerifier()
        _state.verifier.set_layers(
            filesystem=_state.filesystem,
            ui_tree=_state.ui_tree,
            actions=_state.actions,
        )
        _state.recovery = ErrorRecovery()
        _state.recovery.set_resolver(_state.resolver)
        if hasattr(_state.recovery, "set_reporter"):
            def _recovery_reporter(result):
                try:
                    if _state.perception:
                        _state.perception.record_action_feedback(
                            action=f"recovery:{result.original_action}",
                            success=bool(result.recovered),
                            message=str(result.final_message),
                        )
                except Exception:
                    pass
            _state.recovery.set_reporter(_recovery_reporter)
        # Grounding disabled — replaced by direct `act` tool (Claude decides coordinates)
        # _state.grounding = GroundingEngine()
        # _state.grounding.set_layers(ui_tree, vision, perception, buffer)


# ─── Vision / AI-ready endpoints ───

@app.post("/vision/click_at")
async def vision_click_at(
    request: Request,
    x_api_key: Optional[str] = Header(None),
):
    """
    Computer-Use style: click at image coordinates.

    Send a screenshot coordinate (from /vision/smart image) and this endpoint
    converts it to real monitor coordinates and executes the click.

    Body:
      {
        "x": 197,          // pixel x in the image returned by /vision/smart
        "y": 101,          // pixel y in the image returned by /vision/smart
        "monitor_id": 1,   // which monitor the image came from
        "image_w": 1456,   // width of the image (from vision/smart)
        "image_h": 816,    // height of the image
        "button": "left",  // left | right | middle
        "double": false
      }

    The endpoint maps image coords → monitor-relative coords → global coords
    using the monitor's actual resolution and offset.
    """
    _check_auth(x_api_key)
    body = await request.json()

    img_x = int(body.get("x", 0))
    img_y = int(body.get("y", 0))
    monitor_id = body.get("monitor_id", 1)
    image_w = body.get("image_w")
    image_h = body.get("image_h")
    button = body.get("button", "left")
    double = bool(body.get("double", False))

    # Get monitor geometry
    mon_geo = _monitor_geometry(monitor_id)
    if not mon_geo:
        raise HTTPException(404, f"Monitor {monitor_id} not found")

    mon_w = mon_geo["width"]
    mon_h = mon_geo["height"]
    mon_left = mon_geo["left"]
    mon_top = mon_geo["top"]

    # If image dimensions provided, scale from image space → monitor space
    # If not provided, assume image is 1:1 with monitor (full_res capture)
    if image_w and image_h and image_w > 0 and image_h > 0:
        scale_x = mon_w / image_w
        scale_y = mon_h / image_h
        mon_x = round(img_x * scale_x)
        mon_y = round(img_y * scale_y)
    else:
        mon_x = img_x
        mon_y = img_y

    # Clamp to monitor bounds
    mon_x = max(0, min(mon_x, mon_w - 1))
    mon_y = max(0, min(mon_y, mon_h - 1))

    # Convert to global coords
    global_x = mon_left + mon_x
    global_y = mon_top + mon_y

    # Execute click
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")

    import pyautogui as _pag
    _pag.FAILSAFE = False

    if double:
        _pag.doubleClick(global_x, global_y, button=button)
    else:
        _pag.click(global_x, global_y, button=button)

    return {
        "clicked": True,
        "image_coords": {"x": img_x, "y": img_y},
        "monitor_coords": {"x": mon_x, "y": mon_y},
        "global_coords": {"x": global_x, "y": global_y},
        "monitor_id": monitor_id,
        "scale": {"x": round(mon_w / image_w, 4) if image_w else 1.0,
                  "y": round(mon_h / image_h, 4) if image_h else 1.0},
    }

@app.get("/vision/stream")
async def vision_stream(
    request: Request,
    monitor_id: Optional[int] = Query(None),
    fps: float = Query(5.0, ge=0.1, le=30.0),
    quality: int = Query(80, ge=10, le=100),
    token: Optional[str] = Query(None),  # api key via query for browser <img src>
    x_api_key: Optional[str] = Header(None),
):
    """
    MJPEG stream directly from RAM buffer — no disk I/O.
    Open in browser: http://localhost:8420/vision/stream?monitor_id=1&token=ILUM-dev-local
    Works with browser_navigate() for real-time visual verification.
    """
    from fastapi.responses import StreamingResponse
    import asyncio, io as _io
    from PIL import Image as _Image

    _check_auth(x_api_key or token)

    async def _generate():
        interval = 1.0 / fps
        last_phash = None
        while True:
            try:
                slot, _ = _latest_slot_for_monitor(monitor_id)
                if slot and slot.frame_bytes:
                    # Only send if frame changed (phash diff)
                    if slot.phash != last_phash:
                        last_phash = slot.phash
                        # Convert to JPEG in memory — no disk
                        img = _Image.open(_io.BytesIO(slot.frame_bytes))
                        buf = _io.BytesIO()
                        img.convert("RGB").save(buf, format="JPEG", quality=quality)
                        frame = buf.getvalue()
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n\r\n"
                            + frame +
                            b"\r\n"
                        )
            except Exception:
                pass
            await asyncio.sleep(interval)

    return StreamingResponse(
        _generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/vision/zoom")
async def vision_zoom(
    request: Request,
    x_api_key: Optional[str] = Header(None),
):
    """
    Computer-Use style ZOOM — inspect a region at full resolution.

    Like Anthropic's 'zoom' action: when you see something small in a screenshot
    and need pixel-perfect coordinates, zoom into that region first.

    Body:
      {
        "x1": 100,         // left edge in image coords
        "y1": 50,          // top edge in image coords
        "x2": 300,         // right edge in image coords
        "y2": 150,         // bottom edge in image coords
        "monitor_id": 1,   // which monitor
        "image_w": 1280,   // width of the reference image (from see_now)
        "image_h": 720,    // height of the reference image
        "save_to": null    // optional save path (.webp)
      }

    Returns the zoomed region as a base64 WebP image.
    Coords in the returned image are 1:1 with monitor pixels in that region.
    Use click_at() with the zoomed image coords + the region offset to click precisely.
    """
    _check_auth(x_api_key)
    body = await request.json()

    x1 = int(body.get("x1", 0))
    y1 = int(body.get("y1", 0))
    x2 = int(body.get("x2", 200))
    y2 = int(body.get("y2", 100))
    monitor_id = body.get("monitor_id", 1)
    image_w = body.get("image_w")
    image_h = body.get("image_h")
    save_to = body.get("save_to")

    mon_geo = _monitor_geometry(monitor_id)
    if not mon_geo:
        raise HTTPException(404, f"Monitor {monitor_id} not found")

    mon_w = mon_geo["width"]
    mon_h = mon_geo["height"]
    mon_left = mon_geo["left"]
    mon_top = mon_geo["top"]

    # Scale image coords → monitor coords
    if image_w and image_h and image_w > 0 and image_h > 0:
        sx = mon_w / image_w
        sy = mon_h / image_h
        mx1 = max(0, round(x1 * sx))
        my1 = max(0, round(y1 * sy))
        mx2 = min(mon_w, round(x2 * sx))
        my2 = min(mon_h, round(y2 * sy))
    else:
        mx1, my1, mx2, my2 = x1, y1, x2, y2

    # Capture only the region using mss
    grid = body.get("grid", True)   # draw coordinate grid by default
    grid_step = int(body.get("grid_step", 50))

    loop = asyncio.get_event_loop()
    def _capture_region():
        import mss, io
        from PIL import Image, ImageDraw
        with mss.mss() as sct:
            region = {
                "left": mon_left + mx1,
                "top": mon_top + my1,
                "width": max(1, mx2 - mx1),
                "height": max(1, my2 - my1),
            }
            shot = sct.grab(region)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

            # Draw coordinate grid so AI can read exact pixel positions visually
            if grid and img.width > 10 and img.height > 10:
                draw = ImageDraw.Draw(img)
                step = grid_step
                for gx in range(0, img.width, step):
                    draw.line([(gx, 0), (gx, img.height)], fill=(255, 0, 0), width=1)
                    if gx > 0:
                        draw.text((gx + 2, 2), str(gx), fill=(255, 255, 0))
                for gy in range(0, img.height, step):
                    draw.line([(0, gy), (img.width, gy)], fill=(255, 0, 0), width=1)
                    if gy > 0:
                        draw.text((2, gy + 2), str(gy), fill=(255, 255, 0))
                # Always label origin
                draw.text((2, 2), "0", fill=(255, 255, 0))

            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=95)
            return buf.getvalue(), img.size

    img_bytes, (rw, rh) = await loop.run_in_executor(None, _capture_region)

    # Optionally save
    saved_path = None
    if save_to:
        try:
            import pathlib
            p = pathlib.Path(save_to)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(img_bytes)
            saved_path = str(p)
        except Exception:
            pass

    import base64
    b64 = base64.b64encode(img_bytes).decode()

    return {
        "zoomed": True,
        "monitor_id": monitor_id,
        "image_region": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "monitor_region": {"x1": mx1, "y1": my1, "x2": mx2, "y2": my2},
        "region_size": {"width": rw, "height": rh},
        "image_w": rw,
        "image_h": rh,
        "region_offset": {"x": mx1, "y": my1},
        "saved_path": saved_path,
        "image_b64": b64,
        "usage": (
            f"Coords in this zoomed image are 1:1 with monitor pixels. "
            f"To click at zoomed(zx,zy): use click_at(x=zx+{mx1}, y=zy+{my1}, "
            f"monitor_id={monitor_id}, image_w={mon_w}, image_h={mon_h})"
        ),
    }


@app.get("/vision/view")
async def vision_view(
    monitor_id: Optional[int] = Query(None),
    fps: float = Query(5.0),
    quality: int = Query(80),
    token: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """HTML viewer — embeds MJPEG stream. Open in browser for real-time view."""
    from fastapi.responses import HTMLResponse
    _check_auth(x_api_key or token)
    t = token or x_api_key or ""
    mid = monitor_id or 1
    html = f"""<!DOCTYPE html>
<html><head><title>ILUMINATY M{mid}</title>
<style>body{{margin:0;background:#000;display:flex;align-items:center;justify-content:center;height:100vh}}
img{{max-width:100%;max-height:100vh;object-fit:contain}}</style></head>
<body><img src="/vision/stream?monitor_id={mid}&fps={fps}&quality={quality}&token={t}"
     onerror="setTimeout(()=>location.reload(),2000)"/></body></html>"""
    return HTMLResponse(html)


@app.get("/vision/snapshot")
async def vision_snapshot(
    ocr: bool = Query(True),
    include_image: bool = Query(True),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id. Defaults to active monitor."),
    monitor: Optional[int] = Query(None, description="Alias for monitor_id (MCP compatibility)."),
    x_api_key: Optional[str] = Header(None),
):
    """
    Frame enriquecido listo para IA:
    - Imagen (base64)
    - OCR text extraido
    - Anotaciones del usuario
    - Ventana activa
    - Prompt estructurado en ingles

    Este es EL endpoint que cualquier IA consume.
    """
    _check_auth(x_api_key)
    if not _state.buffer or not _state.vision:
        raise HTTPException(503, "Not initialized")

    # Accept both ?monitor_id= and ?monitor= (MCP sends ?monitor=)
    effective_monitor_id = monitor_id if monitor_id is not None else monitor
    slot, resolved_mid = _latest_slot_for_monitor(effective_monitor_id)
    if not slot:
        _raise_no_frame_available(effective_monitor_id)

    enriched = _state.vision.enrich_frame(slot, run_ocr=ocr)
    payload = enriched.to_dict(include_image=include_image)
    payload["monitor_id"] = int(getattr(slot, "monitor_id", resolved_mid or 0) or (resolved_mid or 0))
    return JSONResponse(payload)


@app.post("/vision/describe")
async def vision_describe(
    monitor_id: Optional[int] = Query(None, description="Monitor to describe. Defaults to active."),
    x_api_key: Optional[str] = Header(None),
):
    """On-demand VLM description. Model stays idle until this is called."""
    _check_auth(x_api_key)
    if not _state.perception or not _state.perception._visual:
        raise HTTPException(503, "Visual engine not initialized")
    slot, mid = _latest_slot_for_monitor(monitor_id)
    if not slot:
        raise HTTPException(404, "No frames in buffer")
    win = _active_window_snapshot()
    result = _state.perception._visual.describe(
        frame_bytes=slot.frame_bytes,
        monitor_id=int(mid or 0),
        app_name=str(win.get("app_name") or win.get("name") or ""),
        window_title=str(win.get("title") or win.get("window_title") or ""),
    )
    return result


@app.get("/vision/ocr")
async def vision_ocr(
    region_x: Optional[int] = Query(None),
    region_y: Optional[int] = Query(None),
    region_w: Optional[int] = Query(None),
    region_h: Optional[int] = Query(None),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id. Defaults to active monitor."),
    native: bool = Query(False, description="Use native-resolution capture for OCR when possible."),
    native_window: bool = Query(False, description="With native=true and no region, OCR active window at native resolution."),
    adaptive_zoom: bool = Query(True, description="Apply OCR zoom policy based on phase/criticality."),
    task_phase: Optional[str] = Query(None, description="Optional task phase override for OCR policy."),
    criticality: Optional[str] = Query(None, description="Optional OCR criticality: low|normal|high|critical."),
    x_api_key: Optional[str] = Header(None),
):
    """
    Solo OCR del frame actual.
    Opcionalmente de una region especifica: ?region_x=100&region_y=200&region_w=400&region_h=300
    """
    _check_auth(x_api_key)
    if not _state.buffer or not _state.vision:
        raise HTTPException(503, "Not initialized")

    slot, resolved_mid = _latest_slot_for_monitor(monitor_id)
    if not slot:
        _raise_no_frame_available(monitor_id)

    has_region = all(v is not None for v in [region_x, region_y, region_w, region_h])
    native_used = False
    source = "buffer_frame"
    region_mapping = None
    ocr_bytes = slot.frame_bytes
    resolved_task_phase = str(task_phase or "")
    if not resolved_task_phase and _state.perception:
        try:
            world = _state.perception.get_world_state()
            resolved_task_phase = str(world.get("task_phase", "unknown"))
        except Exception:
            resolved_task_phase = "unknown"
    if not resolved_task_phase:
        resolved_task_phase = "unknown"
    resolved_criticality = str(criticality or "normal")
    ocr_policy = _ocr_policy(resolved_task_phase, resolved_criticality, action="read_screen_text")
    zoom_factor = float(ocr_policy.get("zoom_factor", 1.0) or 1.0) if adaptive_zoom else 1.0

    if native:
        try:
            if has_region:
                payload, mapping = _native_capture_region_from_slot(
                    slot=slot,
                    monitor_id=resolved_mid,
                    region_x=int(region_x or 0),
                    region_y=int(region_y or 0),
                    region_w=int(region_w or 0),
                    region_h=int(region_h or 0),
                )
                if payload:
                    ocr_bytes = payload
                    native_used = True
                    source = "native_region"
                    region_mapping = mapping
            elif native_window:
                payload, win_mid = _native_capture_active_window_bytes(preferred_monitor_id=resolved_mid)
                if payload:
                    ocr_bytes = payload
                    native_used = True
                    source = "native_window"
                    if win_mid is not None:
                        resolved_mid = int(win_mid)
            else:
                payload = _native_capture_monitor_bytes(resolved_mid)
                if payload:
                    ocr_bytes = payload
                    native_used = True
                    source = "native_monitor"
        except Exception as e:
            logger.debug("Native OCR capture path failed: %s", e)

    if (not native) and has_region and adaptive_zoom and bool(ocr_policy.get("native_preferred", False)):
        try:
            payload, mapping = _native_capture_region_from_slot(
                slot=slot,
                monitor_id=resolved_mid,
                region_x=int(region_x or 0),
                region_y=int(region_y or 0),
                region_w=int(region_w or 0),
                region_h=int(region_h or 0),
            )
            if payload:
                ocr_bytes = payload
                native_used = True
                source = "native_region_policy"
                region_mapping = mapping
        except Exception as e:
            logger.debug("Native OCR policy path failed: %s", e)

    if has_region and not native_used:
        # Crop region from frame bytes
        try:
            from PIL import Image
            import io as _io
            img = Image.open(_io.BytesIO(slot.frame_bytes))
            rx, ry, rw, rh = int(region_x or 0), int(region_y or 0), int(region_w or 0), int(region_h or 0)
            cropped = img.crop((rx, ry, rx + rw, ry + rh))
            buf = _io.BytesIO(); cropped.save(buf, format='PNG'); ocr_bytes = buf.getvalue()
        except Exception:
            ocr_bytes = slot.frame_bytes
        source = "buffer_region"

    ocr_r = _fast_ocr_image(ocr_bytes, phash=getattr(slot, 'phash', None))
    result = {"text": ocr_r.text, "blocks": ocr_r.blocks, "latency_ms": ocr_r.latency_ms, "engine": ocr_r.engine}

    if isinstance(result, dict):
        result.setdefault(
            "monitor_id",
            int(getattr(slot, "monitor_id", resolved_mid or 0) or (resolved_mid or 0)),
        )
        result["native_requested"] = bool(native)
        result["native_used"] = bool(native_used)
        result["ocr_source"] = source
        result["ocr_task_phase"] = resolved_task_phase
        result["ocr_criticality"] = resolved_criticality
        result["ocr_zoom_factor"] = round(float(zoom_factor), 2)
        result["ocr_policy"] = dict(ocr_policy)
        if region_mapping is not None:
            result["region_mapping"] = region_mapping
    return result


@app.get("/vision/window")
async def vision_window(x_api_key: Optional[str] = Header(None)):
    """Info de la ventana activa actual."""
    _check_auth(x_api_key)
    from .vision import get_active_window_info
    info = get_active_window_info() or {}
    bounds = info.get("bounds") or {}
    if bounds:
        monitor_id = _monitor_id_for_rect(
            int(bounds.get("left", 0)),
            int(bounds.get("top", 0)),
            int(bounds.get("width", 0)),
            int(bounds.get("height", 0)),
        )
        if monitor_id is not None:
            info["monitor_id"] = int(monitor_id)
    return info


@app.get("/vision/diff")
async def vision_diff(
    include_deltas: bool = Query(False),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id. Defaults to active monitor."),
    x_api_key: Optional[str] = Header(None),
):
    """
    Smart visual diff: que cambio y donde en la pantalla.
    Returns: changed regions, heatmap, change percentage.
    Con include_deltas=true incluye mini-images de las regiones cambiadas.
    """
    _check_auth(x_api_key)
    if not _state.buffer or not _state.diff:
        raise HTTPException(503, "Not initialized")

    slot, resolved_mid = _latest_slot_for_monitor(monitor_id)
    if not slot:
        _raise_no_frame_available(monitor_id)

    diff = _state.diff.compare(slot.frame_bytes)

    result = {
        "changed": diff.changed,
        "change_percentage": diff.change_percentage,
        "changed_cells": diff.changed_cells,
        "total_cells": diff.total_cells,
        "heatmap": diff.heatmap,
        "monitor_id": int(getattr(slot, "monitor_id", resolved_mid or 0) or (resolved_mid or 0)),
        "description": _state.diff.diff_to_description(diff, slot.width, slot.height),
        "regions": [
            {
                "grid": f"({r.grid_x},{r.grid_y})",
                "pixel_x": r.pixel_x,
                "pixel_y": r.pixel_y,
                "pixel_w": r.pixel_w,
                "pixel_h": r.pixel_h,
                "intensity": r.change_intensity,
            }
            for r in diff.changed_regions
        ],
    }

    if include_deltas and diff.changed_regions:
        result["deltas"] = _state.diff.get_delta_regions(slot.frame_bytes, diff)

    return result


# ─── open_on_monitor — launch app on specific monitor ───

@app.post("/windows/open_on_monitor")
async def open_on_monitor(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Launch an application and move it to a specific monitor.

    This is the key tool for pipeline-aware execution:
    the AI can open a browser/terminal/editor on a non-active monitor
    without interfering with the user's current workspace.

    Body:
      app         : app path or name (e.g. "notepad.exe", "C:\\Program Files\\...\\brave.exe")
      monitor_id  : target monitor (1, 2, 3...)
      title_hint  : partial title to identify the window after launch (optional)
      wait_s      : seconds to wait for window to appear (default: 8)
      url         : if provided, navigates browser to this URL after launch
      x, y, w, h  : explicit position/size override (optional, defaults to monitor bounds)
    """
    _check_auth(x_api_key)
    if not _state.windows or not _state.actions:
        raise HTTPException(503, "Window manager or actions not initialized")

    app_path   = str(request_body.get("app") or "").strip()
    monitor_id = int(request_body.get("monitor_id", 1) or 1)
    title_hint = str(request_body.get("title_hint") or "").strip()
    wait_s     = min(float(request_body.get("wait_s", 8) or 8), 30.0)
    url        = str(request_body.get("url") or "").strip()
    override_x = request_body.get("x")
    override_y = request_body.get("y")
    override_w = request_body.get("w")
    override_h = request_body.get("h")

    if not app_path:
        raise HTTPException(400, "app is required")

    # Resolve target monitor bounds
    target_mon = None
    if _state.monitor_mgr:
        target_mon = _state.monitor_mgr.get_monitor(monitor_id)

    if target_mon is None:
        raise HTTPException(404, f"Monitor {monitor_id} not found")

    # Extract app stem for matching (notepad.exe → notepad)
    app_stem = app_path.split("\\")[-1].split("/")[-1].lower()
    if app_stem.endswith(".exe"):
        app_stem = app_stem[:-4]

    _APP_ALIASES = {
        "notepad": ["notepad", "bloc de notas", "untitled", "sin t"],
        "code": ["visual studio code", "code"],
        "brave": ["brave"],
        "chrome": ["chrome", "google chrome"],
        "explorer": ["explorer", "file explorer", "explorador"],
    }
    stem_variants = _APP_ALIASES.get(app_stem, [app_stem])

    # 0. Check if a window of this app is already open — reuse it instead of spawning new
    already_open = None
    if _state.windows:
        for w in _state.windows.list_windows():
            title_l   = str(w.title or "").lower()
            appname_l = str(w.app_name or "").lower()
            if any(v in title_l or v in appname_l for v in stem_variants):
                # Skip windows already on the target monitor (already placed)
                already_open = w
                break

    # 1. Launch the app only if no existing window found
    import subprocess as _sp
    if already_open is None:
        try:
            CREATE_NEW_CONSOLE = 0x00000010
            launch_cmd = f'"{app_path}"' if " " in app_path and not app_path.startswith('"') else app_path
            _sp.Popen(launch_cmd, shell=True, creationflags=CREATE_NEW_CONSOLE, close_fds=True)
        except Exception as e:
            return {"success": False, "step": "launch", "error": str(e)}

    # 2. Find window — reuse existing if already on target monitor, else wait for new one
    found_handle = None
    if already_open is not None and getattr(already_open, "monitor_id", 0) == monitor_id:
        # Already on the right monitor — reuse it
        found_handle = already_open.handle
    elif already_open is not None:
        # Exists but on wrong monitor — will move it after this block
        found_handle = already_open.handle
    else:
        # New window launched — poll until it appears
        deadline = time.time() + wait_s
        while time.time() < deadline:
            await asyncio.sleep(0.5)
            windows = _state.windows.list_windows()
            for w in reversed(windows):
                title_l   = str(w.title or "").lower()
                appname_l = str(w.app_name or "").lower()
                if title_hint and title_hint.lower() not in title_l:
                    continue
                if any(v in title_l or v in appname_l for v in stem_variants):
                    found_handle = w.handle
                    break
            if found_handle:
                break

    if not found_handle:
        return {
            "success": False,
            "step": "wait_for_window",
            "error": f"Window did not appear within {wait_s}s",
            "monitor_id": monitor_id,
        }

    await asyncio.sleep(0.2)  # brief settle before moving

    # 3. Move window to target monitor
    # Default: 90% of monitor size, centered
    mon_x = int(getattr(target_mon, "left", 0))
    mon_y = int(getattr(target_mon, "top", 0))
    mon_w = int(getattr(target_mon, "width", 1920))
    mon_h = int(getattr(target_mon, "height", 1080))

    dest_x = int(override_x) if override_x is not None else mon_x + int(mon_w * 0.05)
    dest_y = int(override_y) if override_y is not None else mon_y + int(mon_h * 0.05)
    dest_w = int(override_w) if override_w is not None else int(mon_w * 0.90)
    dest_h = int(override_h) if override_h is not None else int(mon_h * 0.90)

    move_ok = _state.windows.move_window(
        handle=found_handle,
        x=dest_x, y=dest_y,
        width=dest_w, height=dest_h,
    )

    return {
        "success": True,
        "handle": found_handle,
        "monitor_id": monitor_id,
        "position": {"x": dest_x, "y": dest_y, "w": dest_w, "h": dest_h},
        "move_ok": move_ok,
    }


# ─── Recording (opt-in, zero-disk by default) ───

@app.post("/recording/start")
async def recording_start(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """Start screen recording to local disk.

    Recording is disabled by default (zero-disk principle).
    This endpoint enables it explicitly per-session.

    Body:
      monitors    : [1, 2, 3] or [] for all (default: all)
      max_seconds : auto-stop after N seconds, max 600 (default: 300)
      format      : "gif" | "webm" | "mp4" (default: "gif")
      fps         : capture rate 0.5-10 (default: 2.0)
    """
    _check_auth(x_api_key)
    if not _state.recording_engine:
        raise HTTPException(503, "Recording engine not available")

    monitors    = request_body.get("monitors") or []
    max_seconds = int(request_body.get("max_seconds", 300) or 300)
    fmt         = str(request_body.get("format", "gif") or "gif")
    fps         = float(request_body.get("fps", 2.0) or 2.0)

    session = _state.recording_engine.start(
        monitors=monitors or None,
        max_seconds=max_seconds,
        fmt=fmt,
        fps=fps,
    )
    return session.to_dict()


@app.post("/recording/stop/{session_id}")
async def recording_stop(
    session_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Stop a recording session. Returns final state with output paths."""
    _check_auth(x_api_key)
    if not _state.recording_engine:
        raise HTTPException(503, "Recording engine not available")

    session = _state.recording_engine.stop(session_id)
    if not session:
        raise HTTPException(404, f"Recording session {session_id} not found")
    return session.to_dict()


@app.get("/recording/status")
async def recording_status(x_api_key: Optional[str] = Header(None)):
    """Get current recording state (active sessions + recent completed)."""
    _check_auth(x_api_key)
    if not _state.recording_engine:
        return {"active": [], "recent": [], "output_dir": None, "enabled": False}
    return _state.recording_engine.status()


# ═══════════════════════════════════════════════════════════════
# v1.0 ENDPOINTS: Computer Use — 7 Capas
# ═══════════════════════════════════════════════════════════════


# ─── Capa 3: Terminal ───

# Commands that are never allowed through terminal/exec regardless of auth
_TERMINAL_BLOCKED_PATTERNS = [
    # Filesystem destruction
    "rm -rf", "rm  -rf",            # double-space bypass
    "del /f /s /q", "del /s /f",
    "rmdir /s", "rmdir /q",
    "remove-item -recurse", "remove-item -force",
    "format ", "mkfs",
    # Disk/partition destruction
    "> /dev/sda", "dd if=", "diskpart", "cipher /w",
    # System control
    "shutdown", "reboot", "halt", "poweroff",
    "stop-computer", "restart-computer",
    # User/privilege escalation
    "net user", "net localgroup", "net group",
    "add-localgroup", "add-localgroupmember",
    # Registry destruction
    "reg delete", "reg add", "reg import",
    "remove-item hk", "set-itemproperty hk",
    # Boot/system config
    "bcdedit", "bootrec",
    # SQL destruction
    "drop database", "drop table", "truncate table",
    # Disk overwrite
    # Network/firewall disable
    "netsh advfirewall set", "netsh firewall set",
    "set-netfirewallprofile",
    # Data exfiltration patterns
    "curl evil", "wget evil",
    # PowerShell arbitrary execution bypasses
    "powershell -enc", "powershell -e ", "powershell -nop",
    "invoke-expression", "iex(", "& (", "invoke-webrequest",
    "downloadstring", "downloadfile",
    "start-process -verb runas",
    # Fork bomb
    ":(){ :|:& };:",
    # WMI process manipulation
    "wmic process delete", "wmic process call create",
    "stop-process -name *", "stop-process -id *",
]


def _check_terminal_cmd(cmd: str) -> Optional[str]:
    """Returns a block reason string if the command is not allowed, else None.
    C-4 fix: expanded from 9 to 38+ patterns covering known bypasses.
    Note: denylist is a defense-in-depth layer. For full security, run in a sandbox.
    """
    cmd_lower = cmd.lower().replace("  ", " ")  # normalize double-spaces
    for pattern in _TERMINAL_BLOCKED_PATTERNS:
        if pattern.lower() in cmd_lower:
            return f"Blocked pattern: '{pattern}'"
    return None



# ─── Route modules ───
from iluminaty.routes import (
    audio, perception, grounding, monitors, workers,
    os_surface, annotations, watchdog, ipa, watch,
    safety, actions, windows, clipboard, process,
    ui, files, agent, system, tokens, trading, operating,
    voice,
)
for _mod in (
    audio, perception, grounding, monitors, workers,
    os_surface, annotations, watchdog, ipa, watch,
    safety, actions, windows, clipboard, process,
    ui, files, agent, system, tokens, trading, operating,
    voice,
):
    app.include_router(_mod.router)


# ─── verify_action: visual confirmation that an action had effect ───

class _VerifyBody(BaseModel):
    action_description: str = "action"
    monitor_id: Optional[int] = None
    wait_ms: int = 800          # ms to wait after action before capture
    save_evidence: bool = True


@app.post("/action/verify_visual")
async def action_verify(
    body: _VerifyBody,
    x_api_key: Optional[str] = Header(None),
):
    """Verify that a recent action had a visual effect.

    Compares the current screen state against the last known state using:
    - pHash distance (fast: did ANYTHING change?)
    - OCR diff (what text appeared or disappeared?)
    - Scene state (did the scene transition?)

    Returns:
        success: True if a meaningful change was detected
        confidence: 0.0-1.0
        what_changed: human description of the change
        evidence_path: path to the after-screenshot (use Read tool to view)
    """
    _check_auth(x_api_key)
    import asyncio, base64, io, pathlib, time as _t
    loop = asyncio.get_event_loop()

    monitor = body.monitor_id

    # Wait for screen to settle after action
    if body.wait_ms > 0:
        await asyncio.sleep(min(body.wait_ms, 3000) / 1000.0)

    async def _capture_now():
        """Capture current screen state with pHash + OCR."""
        slot, mid = _latest_slot_for_monitor(monitor)
        if slot is None:
            return None, None, "", 0
        # Force fresh capture
        snap_path = str(pathlib.Path.home() / "Desktop" / f"VERIFY_{int(_t.time())}.webp")
        result = await loop.run_in_executor(None, lambda: _capture_and_save(slot, snap_path))
        ocr_text = ""
        if _state.vision and _state.vision.ocr:
            try:
                ocr_result = await loop.run_in_executor(
                    None,
                    lambda: _state.vision.ocr.extract_text(slot.frame_bytes, frame_hash=slot.phash)
                )
                ocr_text = ocr_result.get("text", "") if isinstance(ocr_result, dict) else ""
            except Exception:
                pass
        phash = getattr(slot, "phash", None)
        return slot, snap_path, ocr_text, phash

    def _capture_and_save(slot, path: str) -> str:
        try:
            pathlib.Path(path).write_bytes(slot.frame_bytes)
            return path
        except Exception:
            return ""

    # Get world state from perception for context
    scene_info = ""
    if _state.perception:
        try:
            world = _state.perception.get_world_state()
            scene_info = f"{world.get('task_phase','?')} | {world.get('active_surface','?')}"
        except Exception:
            pass

    # Capture after-state
    slot_after, evidence_path, ocr_after, phash_after = await _capture_now()

    if slot_after is None:
        return JSONResponse({
            "success": False,
            "confidence": 0.0,
            "what_changed": "No screen data available",
            "evidence_path": None,
            "scene_info": scene_info,
        })

    # Compare with previous state using change_score from buffer
    change_score = float(getattr(slot_after, "change_score", 0.0) or 0.0)

    # Also get recent perception events as evidence
    recent_changes = []
    if _state.perception:
        events = _state.perception.get_events(last_seconds=3.0, min_importance=0.2)
        recent_changes = [e.description for e in events[-5:]]

    # Determine success and confidence
    success = change_score > 0.03 or bool(recent_changes)
    confidence = min(1.0, change_score * 5 + (0.3 if recent_changes else 0.0))

    what_changed = "No visible change detected"
    if recent_changes:
        what_changed = "; ".join(recent_changes[-2:])
    elif change_score > 0.1:
        what_changed = f"Screen changed significantly (score={change_score:.2f})"
    elif change_score > 0.03:
        what_changed = f"Minor screen change detected (score={change_score:.2f})"

    return JSONResponse({
        "success": success,
        "confidence": round(confidence, 3),
        "what_changed": what_changed,
        "evidence_path": evidence_path or None,
        "change_score": round(change_score, 4),
        "recent_events": recent_changes,
        "scene_info": scene_info,
        "action_description": body.action_description,
    })
