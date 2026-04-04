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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Header, HTTPException
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .ring_buffer import RingBuffer, FrameSlot
from .capture import ScreenCapture, CaptureConfig
from .vision import VisionIntelligence, Annotation
from .dashboard import DASHBOARD_HTML
from .smart_diff import SmartDiff
from .audio import AudioRingBuffer, AudioCapture, TranscriptionEngine, AudioInterruptDetector
from .context import ContextEngine
from .plugin_system import PluginManager
from .monitors import MonitorManager
from .memory import TemporalMemory
from .watchdog import Watchdog
from .router import AIRouter
from .ipa_bridge import IPABridge

# v1.0 imports: Computer Use capas
from .actions import ActionBridge
from .windows import WindowManager
from .clipboard import ClipboardManager
from .process_mgr import ProcessManager
from .ui_tree import UITree
from .vscode import VSCodeBridge
from .terminal import TerminalManager
from .git_ops import GitOps
from .browser import BrowserBridge
from .filesystem import FileSystemSandbox
from .resolver import ActionResolver
from .intent import IntentClassifier, Intent
from .planner import TaskPlanner
from .verifier import ActionVerifier
from .recovery import ErrorRecovery
from .safety import SafetySystem
from .autonomy import AutonomyManager, AutonomyLevel
from .audit import AuditLog
from .licensing import LicenseManager, get_license, init_license
from .grounding import GroundingEngine
from .smart_locate import SmartLocateEngine, LocateResult
from .ocr_worker import OCRWorker, init_ocr_worker, get_ocr_worker
from .watch_engine import WatchEngine, WatchResult
from .visual_memory import VisualMemory, SessionMemory
from .ui_semantics import UISemanticsEngine
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
        self.context: Optional[ContextEngine] = None
        self.plugin_mgr: Optional[PluginManager] = None
        self.monitor_mgr: Optional[MonitorManager] = None
        self.memory: Optional[TemporalMemory] = None
        self.watchdog: Optional[Watchdog] = None
        self.router: Optional[AIRouter] = None
        self.ipa_bridge: Optional[IPABridge] = None
        self.ws_clients: set = set()
        # v1.0: Computer Use capas
        self.actions: Optional[ActionBridge] = None
        self.windows: Optional[WindowManager] = None
        self.clipboard: Optional[ClipboardManager] = None
        self.process_mgr: Optional[ProcessManager] = None
        self.ui_tree: Optional[UITree] = None
        self.vscode: Optional[VSCodeBridge] = None
        self.terminal: Optional[TerminalManager] = None
        self.git_ops: Optional[GitOps] = None
        self.browser: Optional[BrowserBridge] = None
        self.filesystem: Optional[FileSystemSandbox] = None
        self.resolver: Optional[ActionResolver] = None
        self.intent: Optional[IntentClassifier] = None
        self.planner: Optional[TaskPlanner] = None
        self.verifier: Optional[ActionVerifier] = None
        self.recovery: Optional[ErrorRecovery] = None
        self.safety: Optional[SafetySystem] = None
        self.autonomy: Optional[AutonomyManager] = None
        self.audit: Optional[AuditLog] = None
        self.license: Optional[LicenseManager] = None
        self.perception = None  # PerceptionEngine (lazy import)
        self.grounding: Optional[GroundingEngine] = None
        self.smart_locator: Optional[SmartLocateEngine] = None
        self.ocr_worker: Optional[OCRWorker] = None
        self.watch_engine: Optional[WatchEngine] = None
        self.visual_memory: Optional[VisualMemory] = None
        self.ui_semantics: Optional[UISemanticsEngine] = None
        self.host_telemetry: Optional[HostTelemetry] = None
        self.os_surface: Optional[OSSurfaceSignals] = None
        self.behavior_cache: Optional[AppBehaviorCache] = None
        self.audio_interrupt: Optional[AudioInterruptDetector] = None
        self.agent_coordinator = None  # IPA v2: Multi-Agent Workbench
        self.cursor_tracker: Optional[CursorTracker] = None
        self.action_watcher: Optional[ActionCompletionWatcher] = None
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


def _check_auth(api_key: Optional[str]):
    if _state.api_key and api_key != _state.api_key:
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
        x, y = int(params.get("x")), int(params.get("y"))
        if not (-16384 <= x <= 32768) or not (-16384 <= y <= 32768):
            return None, None
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
    monitor_id = target_check.get("monitor_id") if isinstance(target_check, dict) else None
    if not _state.ui_semantics:
        return {"allowed": True, "applies": True, "reason": "ui_semantics_unavailable"}
    try:
        return _state.ui_semantics.evaluate_target(
            x=int(x),
            y=int(y),
            monitor_id=int(monitor_id) if monitor_id is not None else None,
            action=action,
            mode=mode,
            task_phase=task_phase,
        )
    except Exception as e:
        logger.debug("UI semantics check failed: %s", e)
        return {"allowed": True, "applies": True, "reason": "ui_semantics_error"}


def _ocr_policy(task_phase: Optional[str], criticality: Optional[str], action: Optional[str] = None) -> dict:
    if not _state.ui_semantics:
        return {"zoom_factor": 1.0, "native_preferred": False, "reason": "ui_semantics_unavailable"}
    try:
        policy = _state.ui_semantics.ocr_policy(
            task_phase=task_phase,
            criticality=criticality,
            action=action,
        )
        if hasattr(policy, "to_dict"):
            return policy.to_dict()
        return dict(policy)
    except Exception as e:
        logger.debug("OCR policy resolve failed: %s", e)
        return {"zoom_factor": 1.0, "native_preferred": False, "reason": f"policy_error:{e}"}


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
            precheck.get("grounding_check", {}).get("reason", "grounding_blocked")
        )
        if _state.audit:
            _state.audit.log(
                intent.action,
                intent.category,
                intent.params,
                "blocked",
                blocked_reason,
                _state.autonomy.default_level if _state.autonomy else "unknown",
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

        if verify and _state.verifier and result.success:
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
                if verify and _state.verifier and result.success:
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
            _state.autonomy.default_level if _state.autonomy else "unknown",
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
    # Background task: reap stale agent sessions every 60s
    async def _reap_agents_loop():
        while True:
            await asyncio.sleep(60)
            try:
                if _state.agent_coordinator:
                    reaped = _state.agent_coordinator.reap_stale_sessions()
                    if reaped:
                        import logging as _log
                        _log.getLogger(__name__).info(
                            "Reaped %d stale agent session(s): %s", len(reaped), reaped
                        )
            except Exception:
                pass

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

    reap_task = asyncio.create_task(_reap_agents_loop())
    watchdog_task = asyncio.create_task(_watchdog_loop())
    yield
    reap_task.cancel()
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

    # Auto-save visual memory on clean shutdown
    if _state.visual_memory:
        try:
            state = {}
            if _state.monitor_mgr:
                state["spatial"] = {
                    "monitor_count":     _state.monitor_mgr.count,
                    "active_monitor_id": getattr(_state.monitor_mgr, "_active_monitor_id", 0),
                    "monitors": [
                        {"id": m.id, "zone": "?", "width": m.width, "height": m.height}
                        for m in _state.monitor_mgr.monitors
                    ],
                }
            if _state.ipa_bridge:
                try:
                    events = _state.ipa_bridge.recent_events(seconds=1800)
                    state["ipa_events"] = [
                        {"event_type": e.event_type, "description": e.description, "timestamp": e.timestamp}
                        for e in events
                    ]
                except Exception:
                    pass
            if _state.perception and _state.monitor_mgr:
                ocr_map = {}
                for mon in _state.monitor_mgr.monitors:
                    ms = _state.perception._get_monitor_state(mon.id)
                    if ms and getattr(ms, "last_ocr_text", None):
                        ocr_map[mon.id] = ms.last_ocr_text[:500]
                state["ocr_by_monitor"] = ocr_map
            _state.visual_memory.save(state)
        except Exception as _e:
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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "x-api-key"],
)


# ─── License Gate Middleware ───

class LicenseGateMiddleware(BaseHTTPMiddleware):
    """Blocks Pro-only endpoints for Free plan users.

    NOTE: uses cached singleton — no I/O per request.
    License is validated once at startup in init_license().
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


# ─── Endpoints ───

@app.get("/health")
async def health():
    return {
        "status": "alive",
        "capture_running": _state.capture.is_running if _state.capture else False,
        "buffer_slots": _state.buffer.size if _state.buffer else 0,
    }


@app.get("/", response_class=Response)
async def dashboard(x_api_key: Optional[str] = Header(None), token: Optional[str] = Query(None)):
    """Dashboard web en vivo. Auth via header or ?token= query param."""
    # Allow dashboard access via query param too (for browser URL bar)
    if _state.api_key:
        provided = x_api_key or token
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
<form onsubmit="location.href='/?token='+document.getElementById('k').value;return false">
<input id="k" type="password" placeholder="Enter API key" autofocus/>
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
async def ws_stream(ws: WebSocket):
    """
    WebSocket que envía frames en real-time.
    Cada mensaje es JSON con base64 del frame.
    La IA se conecta aquí para "ver" en vivo.
    """
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
            fps = _state.capture.current_fps if _state.capture else 1.0
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
):
    """
    IPA v2 semantic stream for external AI agents.
    Sends WorldState snapshots (+ optional recent events) in real time.
    """
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
        _state.context = ContextEngine()

        _state.plugin_mgr = PluginManager()
        _state.plugin_mgr.load_from_directory()
        _state.monitor_mgr = MonitorManager()
        _state.monitor_mgr.refresh()

        # Perception Engine — continuous vision processing (the AI's visual cortex)
        # Wired AFTER monitor_mgr/diff/context are initialized (IPA Phase 1.3)
        try:
            from .perception import PerceptionEngine
            _state.perception = PerceptionEngine(
                monitor_mgr=_state.monitor_mgr,
                smart_diff=_state.diff,
                context=_state.context,
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

        # IPA v2: Multi-Agent Workbench
        try:
            from .agents import AgentCoordinator
            _state.agent_coordinator = AgentCoordinator()
            if _state.perception:
                _state.perception._agent_coordinator = _state.agent_coordinator
        except Exception as e:
            _state.agent_coordinator = None
            _state.bootstrap_warnings.append(f"agent_coordinator_init_failed: {e}")

        _state.memory = TemporalMemory(enabled=False)
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
        _state.router = AIRouter()
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

        # Capa 7: Safety (primero, todo pasa por aqui)
        _state.safety = SafetySystem()
        _state.autonomy = AutonomyManager()
        _state.autonomy.set_level(autonomy_level)
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

        # OCR Worker subprocess — runs OCR in a separate process (no GIL blocking)
        try:
            _state.ocr_worker = init_ocr_worker()
            if _state.ocr_worker.available:
                logger.info("OCR worker subprocess started")
            else:
                logger.warning("OCR worker subprocess unavailable -- falling back to in-process OCR")
                _state.ocr_worker = None
        except Exception as _e:
            logger.warning("OCR worker init failed: %s", _e)
            _state.ocr_worker = None

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

        _state.watch_engine = WatchEngine(
            ipa_bridge=_state.ipa_bridge,
            ocr_fn=_ocr_text_for_watch,
            ui_tree_fn=_element_found_for_watch,
        )

        # Visual Memory — persist context between AI sessions
        _state.visual_memory = VisualMemory()
        _state.vscode = VSCodeBridge()
        _state.terminal = TerminalManager()
        _state.git_ops = GitOps()

        # Capa 4: Web
        _state.browser = BrowserBridge(debug_port=browser_debug_port)

        # Capa 5: File System
        _state.filesystem = FileSystemSandbox(
            allowed_paths=file_sandbox_paths or ["."],
        )

        # Capa 6: Orchestration (conecta todas las capas)
        _state.resolver = ActionResolver()
        _state.resolver.set_layers(
            actions=_state.actions,
            ui_tree=_state.ui_tree,
            vscode=_state.vscode,
            browser=_state.browser,
            filesystem=_state.filesystem,
        )
        _state.intent = IntentClassifier()
        _state.planner = TaskPlanner()
        if _state.behavior_cache and hasattr(_state.planner, "set_behavior_cache"):
            try:
                _state.planner.set_behavior_cache(_state.behavior_cache)
            except Exception as e:
                _state.bootstrap_warnings.append(f"planner_behavior_cache_set_failed: {e}")
        _state.verifier = ActionVerifier()
        _state.verifier.set_layers(
            filesystem=_state.filesystem,
            ui_tree=_state.ui_tree,
            browser=_state.browser,
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
        _state.ui_semantics = UISemanticsEngine()
        _state.ui_semantics.set_layers(
            ui_tree=_state.ui_tree,
            vision=_state.vision,
            monitor_mgr=_state.monitor_mgr,
            buffer=_state.buffer,
        )


# ─── Vision / AI-ready endpoints ───

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
        # Legacy behavior: region over buffered frame.
        result = _state.vision.ocr.extract_region(
            slot.frame_bytes,
            int(region_x or 0),
            int(region_y or 0),
            int(region_w or 0),
            int(region_h or 0),
            zoom_factor=zoom_factor,
        )
        source = "buffer_region"
    else:
        result = _state.vision.ocr.extract_text(ocr_bytes)

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


# ─── Audio endpoints ───

@app.get("/audio/stats")
async def audio_stats(x_api_key: Optional[str] = Header(None)):
    """Stats del buffer de audio."""
    _check_auth(x_api_key)
    if not _state.audio_buffer:
        return {"status": "disabled", "mode": "off"}
    stats = _state.audio_buffer.stats
    stats["capture_running"] = _state.audio_capture.is_running if _state.audio_capture else False
    stats["mode"] = _state.audio_capture.mode if _state.audio_capture else "off"
    stats["transcription_engine"] = _state.transcriber.engine if _state.transcriber else "none"
    if _state.audio_interrupt:
        stats["interrupt"] = _state.audio_interrupt.status()
    return stats


@app.get("/audio/level")
async def audio_level(x_api_key: Optional[str] = Header(None)):
    """Nivel de audio actual (para VU meter en el dashboard)."""
    _check_auth(x_api_key)
    if not _state.audio_buffer:
        return {"level": 0.0, "is_speech": False}
    chunks = _state.audio_buffer.get_latest(1.0)
    if not chunks:
        return {"level": 0.0, "is_speech": False}
    latest = chunks[-1]
    if _state.audio_interrupt:
        try:
            _state.audio_interrupt.ingest_level(
                float(latest.rms_level),
                is_speech=bool(latest.is_speech),
                source="audio_level",
            )
        except Exception as e:
            logger.debug("Audio interrupt level ingest failed: %s", e)
    return {"level": latest.rms_level, "is_speech": latest.is_speech}


@app.get("/audio/transcribe")
async def audio_transcribe(
    seconds: float = Query(10.0, ge=1, le=120),
    x_api_key: Optional[str] = Header(None),
):
    """
    Transcribe los ultimos N segundos de audio.
    Usa Whisper local si esta disponible.
    """
    _check_auth(x_api_key)
    if not _state.audio_buffer or not _state.transcriber:
        raise HTTPException(503, "Audio not enabled or transcription unavailable")

    if not _state.transcriber.available:
        return {"text": "", "error": "No transcription engine. Install: pip install faster-whisper"}

    chunks = _state.audio_buffer.get_latest(seconds)
    speech_chunks = [c for c in chunks if c.is_speech]

    if not speech_chunks:
        return {"text": "", "note": "No speech detected in last " + str(seconds) + "s"}

    result = _state.transcriber.transcribe_chunks(speech_chunks)
    if _state.audio_interrupt:
        try:
            text = str(result.get("text", "") or "")
            if text.strip():
                result["interrupt_signal"] = _state.audio_interrupt.ingest_transcript(
                    text=text,
                    source="audio_transcribe",
                )
        except Exception as e:
            logger.debug("Audio interrupt transcript ingest failed: %s", e)
    return result


@app.get("/audio/wav")
async def audio_wav(
    seconds: float = Query(10.0, ge=1, le=60),
    x_api_key: Optional[str] = Header(None),
):
    """Retorna audio WAV de los ultimos N segundos (para debug/playback)."""
    _check_auth(x_api_key)
    if not _state.audio_buffer:
        raise HTTPException(503, "Audio not enabled")

    wav_data = _state.audio_buffer.get_audio_wav(seconds)
    if not wav_data:
        raise HTTPException(404, "No audio in buffer")

    return Response(content=wav_data, media_type="audio/wav")


@app.get("/audio/devices")
async def audio_devices(x_api_key: Optional[str] = Header(None)):
    """Lista dispositivos de audio disponibles."""
    _check_auth(x_api_key)
    if not _state.audio_capture:
        return {"devices": []}
    return {"devices": _state.audio_capture.get_devices()}


@app.get("/audio/interrupt/status")
async def audio_interrupt_status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.audio_interrupt:
        return {"enabled": False, "blocked": False, "reason": "audio_interrupt_unavailable"}
    status = _state.audio_interrupt.status()
    status["enabled"] = True
    status["recent_events"] = _state.audio_interrupt.recent_events(limit=12)
    return status


@app.post("/audio/interrupt/ack")
async def audio_interrupt_ack(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.audio_interrupt:
        return {"acknowledged": False, "reason": "audio_interrupt_unavailable"}
    return _state.audio_interrupt.acknowledge()


@app.post("/audio/interrupt/feed")
async def audio_interrupt_feed(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Manual interrupt feed for testing/integration.
    Body: {text, confidence?, source?}
    """
    _check_auth(x_api_key)
    if not _state.audio_interrupt:
        raise HTTPException(503, "Audio interrupt detector not initialized")
    text = str(request_body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    confidence = float(request_body.get("confidence", 0.8))
    source = str(request_body.get("source") or "manual")
    result = _state.audio_interrupt.ingest_transcript(
        text=text,
        confidence=confidence,
        source=source,
    )
    return {
        "ok": True,
        "result": result,
        "status": _state.audio_interrupt.status(),
    }


# ─── Context Engine (F08) ───

@app.get("/context/state")
async def context_state(x_api_key: Optional[str] = Header(None)):
    """Estado actual del workflow: que esta haciendo el usuario."""
    _check_auth(x_api_key)
    if not _state.context:
        raise HTTPException(503, "Context engine not initialized")

    # Update context with current window
    from .vision import get_active_window_info
    win = get_active_window_info()
    _state.context.update(
        win.get("name", win.get("app_name", "unknown")),
        win.get("window_title", win.get("title", "")),
    )

    state = _state.context.get_state()
    return {
        "workflow": state.current_workflow,
        "confidence": state.confidence,
        "app": state.current_app,
        "title": state.current_title[:100],
        "time_in_workflow_seconds": state.time_in_workflow,
        "time_in_app_seconds": state.time_in_app,
        "switches_5min": state.switch_count_last_5min,
        "is_focused": state.is_focused,
        "summary": state.context_summary,
    }


@app.get("/context/apps")
async def context_apps(x_api_key: Optional[str] = Header(None)):
    """Stats de tiempo por app."""
    _check_auth(x_api_key)
    if not _state.context:
        raise HTTPException(503, "Not initialized")
    return _state.context.get_app_stats()


@app.get("/context/workflows")
async def context_workflows(x_api_key: Optional[str] = Header(None)):
    """Stats de tiempo por workflow."""
    _check_auth(x_api_key)
    if not _state.context:
        raise HTTPException(503, "Not initialized")
    return _state.context.get_workflow_stats()


@app.get("/context/timeline")
async def context_timeline(
    minutes: int = Query(30, ge=1, le=480),
    x_api_key: Optional[str] = Header(None),
):
    """Timeline de actividad."""
    _check_auth(x_api_key)
    if not _state.context:
        raise HTTPException(503, "Not initialized")
    return {"timeline": _state.context.get_timeline(minutes)}


# ─── Perception (Real-Time Vision) ───

@app.get("/perception")
async def perception_summary(
    seconds: float = Query(30, ge=1, le=300),
    x_api_key: Optional[str] = Header(None),
):
    """Get real-time perception events — what happened on screen."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return {
        "summary": _state.perception.get_summary(seconds),
        "event_count": _state.perception.get_event_count(),
        "running": _state.perception.is_running,
    }


@app.get("/perception/events")
async def perception_events(
    seconds: float = Query(30, ge=1, le=300),
    min_importance: float = Query(0.0, ge=0.0, le=1.0),
    x_api_key: Optional[str] = Header(None),
):
    """Get raw perception events."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    events = _state.perception.get_events(seconds, min_importance)
    return {
        "events": [
            {
                "timestamp": e.timestamp,
                "type": e.event_type,
                "description": e.description,
                "importance": e.importance,
                "uncertainty": e.uncertainty,
                "monitor": e.monitor,
                "details": e.details,
            }
            for e in events
        ]
    }


# ─── IPA State (Phase 4.3) ───

@app.get("/perception/state")
async def perception_state(x_api_key: Optional[str] = Header(None)):
    """Full IPA introspection — scene states, attention, ROIs, predictor."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return _state.perception.get_state()


@app.get("/perception/attention")
async def perception_attention(x_api_key: Optional[str] = Header(None)):
    """Attention heatmap grid (8x6 float values) for dashboard visualization."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return {
        "grid": _state.perception.get_attention_heatmap(),
        "rows": 6,
        "cols": 8,
        "hot_zones": _state.perception.get_state().get("attention_hot_zones", []),
    }


@app.get("/perception/world")
async def perception_world(x_api_key: Optional[str] = Header(None)):
    """IPA v2 semantic snapshot (WorldState)."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return _state.perception.get_world_state()


@app.get("/perception/trace")
async def perception_trace(
    seconds: float = Query(90, ge=1, le=600),
    x_api_key: Optional[str] = Header(None),
):
    """Compressed semantic transitions kept in RAM."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    bundle = _state.perception.get_world_trace_bundle(seconds=seconds)
    trace = bundle.get("trace", [])
    temporal = bundle.get("temporal", {})
    return {
        "trace": trace,
        "temporal": temporal,
        "count": len(trace),
        "seconds": seconds,
    }


@app.get("/perception/readiness")
async def perception_readiness(x_api_key: Optional[str] = Header(None)):
    """Whether perception has enough context to execute actions safely."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    return _state.perception.get_readiness()


@app.get("/domain-packs")
async def domain_packs_list(x_api_key: Optional[str] = Header(None)):
    """List available domain packs, active selection, and override state."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    if not hasattr(_state.perception, "list_domain_packs"):
        raise HTTPException(501, "Domain packs are not available in this build")
    return _state.perception.list_domain_packs()


@app.post("/domain-packs/reload")
async def domain_packs_reload(x_api_key: Optional[str] = Header(None)):
    """Reload custom domain packs from configured directory."""
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    if not hasattr(_state.perception, "reload_domain_packs"):
        raise HTTPException(501, "Domain packs are not available in this build")
    return _state.perception.reload_domain_packs()


@app.post("/domain-packs/override")
async def domain_packs_override(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Force domain pack selection or switch back to auto mode.
    Body: {"name": "trading"} or {"name":"auto"}.
    """
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    if not hasattr(_state.perception, "set_domain_override"):
        raise HTTPException(501, "Domain packs are not available in this build")
    name = request_body.get("name")
    result = _state.perception.set_domain_override(name)
    if not bool(result.get("ok", False)):
        raise HTTPException(400, result.get("reason", "invalid_domain_pack"))
    return result


@app.post("/perception/query")
async def perception_query(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Query temporal visual context.
    Body: {question, at_ms?, window_seconds?, monitor_id?}
    """
    _check_auth(x_api_key)
    if not _state.perception:
        raise HTTPException(503, "Perception engine not initialized")
    question = (request_body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question is required")
    at_ms = request_body.get("at_ms")
    window_seconds = float(request_body.get("window_seconds", 30))
    monitor_id = request_body.get("monitor_id")
    return _state.perception.query_visual(
        question=question,
        at_ms=at_ms,
        window_seconds=window_seconds,
        monitor_id=monitor_id,
    )


# ─── Grounding (Hybrid UI+Text Targeting) ───

@app.get("/grounding/status")
async def grounding_status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.grounding:
        raise HTTPException(503, "Grounding engine not initialized")
    return _state.grounding.status()


@app.post("/grounding/resolve")
async def grounding_resolve(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.grounding:
        raise HTTPException(503, "Grounding engine not initialized")
    query = (request_body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query is required")
    role = request_body.get("role")
    monitor_id = request_body.get("monitor_id")
    mode = request_body.get("mode") or _state.operating_mode
    category = request_body.get("category") or "normal"
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = request_body.get("max_staleness_ms")
    top_k = int(request_body.get("top_k", 5))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _state.grounding.resolve,
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


@app.post("/grounding/click")
async def grounding_click(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.grounding:
        raise HTTPException(503, "Grounding engine not initialized")
    query = (request_body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query is required")
    mode = request_body.get("mode") or _state.operating_mode
    category = request_body.get("category") or "normal"
    verify = bool(request_body.get("verify", True))
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = int(request_body.get("max_staleness_ms", 1500))
    resolved = _state.grounding.resolve(
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
    execution = await _execute_intent(
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


@app.post("/grounding/type")
async def grounding_type(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.grounding:
        raise HTTPException(503, "Grounding engine not initialized")
    query = (request_body.get("query") or "").strip()
    text = request_body.get("text")
    if not query:
        raise HTTPException(400, "query is required")
    if text is None:
        raise HTTPException(400, "text is required")
    mode = request_body.get("mode") or _state.operating_mode
    verify = bool(request_body.get("verify", True))
    category = request_body.get("category") or "normal"
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = int(request_body.get("max_staleness_ms", 1500))

    resolved = _state.grounding.resolve(
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

    click_exec = await _execute_intent(
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

    type_exec = await _execute_intent(
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


# ─── Multi-Agent Workbench (IPA v2) ───

@app.post("/agents/register")
async def agent_register(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """Register a new agent with a role."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")

    name = request_body.get("name", "unnamed")
    role = request_body.get("role", "observer")
    autonomy = request_body.get("autonomy", "suggest")
    monitors = request_body.get("monitors", [])
    metadata = request_body.get("metadata", {})

    try:
        session = _state.agent_coordinator.register(
            name=name, role=role, autonomy=autonomy,
            monitors=monitors, metadata=metadata,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    return session.to_dict()


@app.get("/agents")
async def agent_list(x_api_key: Optional[str] = Header(None)):
    """List all registered agents."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    return _state.agent_coordinator.to_dict()


@app.get("/agents/{agent_id}")
async def agent_details(agent_id: str, x_api_key: Optional[str] = Header(None)):
    """Get details for a specific agent."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    session = _state.agent_coordinator.get_session(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return {
        **session.to_dict(),
        "allowed_tools": list(_state.agent_coordinator.get_allowed_tools(agent_id)),
        "pending_messages": _state.agent_coordinator._message_bus.pending_count(agent_id),
    }


@app.delete("/agents/{agent_id}")
async def agent_unregister(agent_id: str, x_api_key: Optional[str] = Header(None)):
    """Unregister an agent."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    if not _state.agent_coordinator.unregister(agent_id):
        raise HTTPException(404, f"Agent {agent_id} not found")
    return {"status": "unregistered", "agent_id": agent_id}


@app.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(agent_id: str, x_api_key: Optional[str] = Header(None)):
    """Update agent heartbeat."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    if not _state.agent_coordinator.heartbeat(agent_id):
        raise HTTPException(404, f"Agent {agent_id} not found")
    return {"status": "ok"}


@app.get("/agents/{agent_id}/perception")
async def agent_perception(
    agent_id: str,
    max_count: int = Query(20, ge=1, le=100),
    x_api_key: Optional[str] = Header(None),
):
    """Get perception events filtered for this agent's role and monitors."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    session = _state.agent_coordinator.get_session(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")

    events = _state.agent_coordinator.get_perception_events(agent_id, max_count)
    return {
        "agent_id": agent_id,
        "role": session.role.value,
        "events": [
            {
                "timestamp": e.timestamp,
                "type": e.event_type,
                "description": e.description,
                "importance": e.importance,
                "uncertainty": e.uncertainty,
                "monitor": e.monitor,
            }
            for e in events
        ],
    }


@app.post("/agents/{agent_id}/message")
async def agent_send_message(
    agent_id: str,
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """Send inter-agent message."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")

    to_id = request_body.get("to", "*")
    msg_type = request_body.get("type", "message")
    payload = request_body.get("payload", {})

    msg = _state.agent_coordinator.send_message(agent_id, to_id, msg_type, payload)
    if not msg:
        raise HTTPException(400, "Failed to send message (invalid agent IDs)")
    return msg.to_dict()


@app.get("/agents/{agent_id}/messages")
async def agent_get_messages(
    agent_id: str,
    max_count: int = Query(10, ge=1, le=50),
    x_api_key: Optional[str] = Header(None),
):
    """Poll messages for an agent."""
    _check_auth(x_api_key)
    if not _state.agent_coordinator:
        raise HTTPException(503, "Agent coordinator not initialized")
    messages = _state.agent_coordinator.get_messages(agent_id, max_count)
    return {
        "agent_id": agent_id,
        "messages": [m.to_dict() for m in messages],
    }


# ─── Monitors (F10) ───

@app.get("/monitors")
async def monitors_info(x_api_key: Optional[str] = Header(None)):
    """Info de todos los monitores."""
    _check_auth(x_api_key)
    if not _state.monitor_mgr:
        raise HTTPException(503, "Not initialized")
    _state.monitor_mgr.refresh()
    return _state.monitor_mgr.to_dict()


@app.get("/monitors/info")
async def monitors_info_alias(x_api_key: Optional[str] = Header(None)):
    """Alias compat para monitor info."""
    return await monitors_info(x_api_key=x_api_key)


@app.get("/spatial/state")
async def spatial_state(
    include_windows: bool = Query(True),
    x_api_key: Optional[str] = Header(None),
):
    """
    Unified desktop spatial snapshot for 1..N monitors.
    Coordinates are always in virtual-desktop space.
    """
    _check_auth(x_api_key)
    monitors = _monitor_layout_snapshot()
    active_monitor_id = _resolve_active_monitor_id()
    active_window = await windows_active(x_api_key=x_api_key)
    windows_payload = {"windows": []}
    if include_windows:
        windows_payload = await windows_list(
            monitor_id=None,
            visible_only=True,
            exclude_minimized=False,
            exclude_system=True,
            title_contains=None,
            x_api_key=x_api_key,
        )
    windows = windows_payload.get("windows", [])
    grouped: dict[str, list[dict]] = {}
    for w in windows:
        grouped.setdefault(str(w.get("monitor_id", 0)), []).append(w)

    cursor = {}
    if _state.actions:
        try:
            cursor = _state.actions.get_mouse_position()
        except Exception:
            cursor = {}

    return {
        "timestamp_ms": int(time.time() * 1000),
        "monitor_count": len(monitors),
        "active_monitor_id": int(active_monitor_id) if active_monitor_id is not None else None,
        "monitors": monitors,
        "active_window": active_window or {},
        "cursor": cursor,
        "windows": windows,
        "windows_by_monitor": grouped,
    }


# ─── Workers System (v1) ───

@app.get("/workers/status")
async def workers_status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.perception or not hasattr(_state.perception, "get_workers_status"):
        raise HTTPException(503, "Workers system not initialized")
    return _state.perception.get_workers_status()


@app.get("/workers/monitor/{monitor_id}")
async def workers_monitor(monitor_id: int, x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.perception or not hasattr(_state.perception, "get_worker_monitor"):
        raise HTTPException(503, "Workers system not initialized")
    payload = _state.perception.get_worker_monitor(int(monitor_id))
    if not payload:
        raise HTTPException(404, f"Monitor {int(monitor_id)} has no worker digest yet")
    return payload


@app.post("/workers/action/claim")
async def workers_action_claim(request_body: dict, x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.perception or not hasattr(_state.perception, "claim_action_lease"):
        raise HTTPException(503, "Workers system not initialized")
    owner = str(request_body.get("owner") or "external-executor")
    ttl_ms = request_body.get("ttl_ms")
    force = bool(request_body.get("force", False))
    return _state.perception.claim_action_lease(owner=owner, ttl_ms=ttl_ms, force=force)


@app.post("/workers/action/release")
async def workers_action_release(request_body: dict, x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.perception or not hasattr(_state.perception, "release_action_lease"):
        raise HTTPException(503, "Workers system not initialized")
    owner = str(request_body.get("owner") or "external-executor")
    success = bool(request_body.get("success", True))
    message = str(request_body.get("message") or "")
    return _state.perception.release_action_lease(owner=owner, success=success, message=message)


@app.get("/workers/schedule")
async def workers_schedule(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.perception or not hasattr(_state.perception, "get_workers_schedule"):
        raise HTTPException(503, "Workers scheduler not initialized")
    return _state.perception.get_workers_schedule()


@app.get("/workers/subgoals")
async def workers_subgoals(
    include_completed: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.perception or not hasattr(_state.perception, "list_worker_subgoals"):
        raise HTTPException(503, "Workers scheduler not initialized")
    items = _state.perception.list_worker_subgoals(include_completed=include_completed)
    return {"subgoals": items, "count": len(items)}


@app.post("/workers/subgoals")
async def workers_set_subgoal(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.perception or not hasattr(_state.perception, "set_worker_subgoal"):
        raise HTTPException(503, "Workers scheduler not initialized")
    monitor_id = request_body.get("monitor_id")
    goal = str(request_body.get("goal") or "").strip()
    if monitor_id is None:
        raise HTTPException(400, "monitor_id is required")
    if not goal:
        raise HTTPException(400, "goal is required")
    return _state.perception.set_worker_subgoal(
        monitor_id=int(monitor_id),
        goal=goal,
        priority=float(request_body.get("priority", 0.5)),
        risk=str(request_body.get("risk", "normal")),
        deadline_ms=request_body.get("deadline_ms"),
        metadata=request_body.get("metadata") or {},
    )


@app.delete("/workers/subgoals/{subgoal_id}")
async def workers_clear_subgoal(
    subgoal_id: str,
    completed: bool = Query(True),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.perception or not hasattr(_state.perception, "clear_worker_subgoal"):
        raise HTTPException(503, "Workers scheduler not initialized")
    result = _state.perception.clear_worker_subgoal(subgoal_id, completed=completed)
    if not bool(result.get("ok", False)):
        raise HTTPException(404, result.get("reason", "subgoal_not_found"))
    return result


@app.post("/workers/route")
async def workers_route(request_body: dict, x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.perception or not hasattr(_state.perception, "route_worker_query"):
        raise HTTPException(503, "Workers scheduler not initialized")
    query = str(request_body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query is required")
    preferred_monitor_id = request_body.get("preferred_monitor_id")
    return _state.perception.route_worker_query(query, preferred_monitor_id=preferred_monitor_id)


@app.get("/behavior/stats")
async def behavior_stats(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.behavior_cache:
        return {"enabled": False, "reason": "behavior_cache_unavailable"}
    payload = _state.behavior_cache.stats()
    payload["enabled"] = True
    return payload


@app.get("/behavior/recent")
async def behavior_recent(
    limit: int = Query(20, ge=1, le=200),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.behavior_cache:
        return {"enabled": False, "entries": []}
    return {
        "enabled": True,
        "entries": _state.behavior_cache.recent(limit=limit),
    }


@app.post("/behavior/suggest")
async def behavior_suggest(request_body: dict, x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.behavior_cache:
        raise HTTPException(503, "Behavior cache not initialized")
    action = str(request_body.get("action") or "").strip()
    if not action:
        raise HTTPException(400, "action is required")
    app_name = str(request_body.get("app_name") or "unknown")
    window_title = str(request_body.get("window_title") or "")
    return _state.behavior_cache.suggest(
        action=action,
        app_name=app_name,
        window_title=window_title,
    )


@app.get("/runtime/profile")
async def runtime_profile_status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    profile = _normalize_runtime_profile(_state.runtime_profile)
    return {
        "profile": profile,
        "policy": _runtime_profile_policy(profile),
    }


@app.post("/runtime/profile")
async def runtime_profile_set(request_body: dict, x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    requested = request_body.get("profile")
    profile = _normalize_runtime_profile(requested)
    _state.runtime_profile = profile
    return {
        "ok": True,
        "profile": profile,
        "policy": _runtime_profile_policy(profile),
    }


@app.get("/runtime/cursor")
async def runtime_cursor_status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.cursor_tracker:
        raise HTTPException(503, "Cursor tracker not initialized")
    return _state.cursor_tracker.status()


@app.get("/runtime/action-watcher")
async def runtime_action_watcher_status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.action_watcher:
        raise HTTPException(503, "Action watcher not initialized")
    return _state.action_watcher.stats()


# ─── OS Surface ───

@app.get("/os/notifications")
async def os_notifications(
    limit: int = Query(20, ge=1, le=200),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.os_surface:
        return {"available": False, "count": 0, "items": [], "sources": []}
    payload = _state.os_surface.notifications(limit=limit)
    payload["available"] = True
    return payload


@app.get("/os/tray")
async def os_tray(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.os_surface:
        return {"available": False, "supported": False, "detected": False, "windows": []}
    payload = _state.os_surface.tray_state()
    payload["available"] = True
    return payload


@app.get("/os/dialog/status")
async def os_dialog_status(
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for dialog probe."),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _os_dialog_status_snapshot(monitor_id=monitor_id)


@app.post("/os/dialog/resolve")
async def os_dialog_resolve(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Attempt to resolve an active dialog by clicking a target label/coordinate.
    Body: {label?, x?, y?, monitor_id?, mode?, verify?}
    """
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Action bridge not initialized")

    monitor_id = request_body.get("monitor_id")
    status = _os_dialog_status_snapshot(monitor_id=monitor_id)
    if not bool(status.get("detected", False)):
        return {
            "resolved": False,
            "reason": "dialog_not_detected",
            "dialog": status,
            "execution": None,
        }

    label = str(request_body.get("label") or "").strip()
    x = request_body.get("x")
    y = request_body.get("y")
    chosen_target = {"x": None, "y": None, "label": label or None}
    mode = request_body.get("mode") or _state.operating_mode
    verify = bool(request_body.get("verify", True))
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = request_body.get("max_staleness_ms")
    if max_staleness_ms is not None:
        max_staleness_ms = int(max_staleness_ms)

    if x is None or y is None:
        if not label:
            affordances = status.get("affordances") or []
            if affordances:
                label = str(affordances[0]).strip()
                chosen_target["label"] = label
        if label and _state.grounding:
            try:
                resolved = _state.grounding.resolve(
                    query=label,
                    role="button",
                    monitor_id=monitor_id,
                    mode=mode,
                    category=str(request_body.get("category") or "normal"),
                    context_tick_id=context_tick_id,
                    max_staleness_ms=max_staleness_ms,
                    top_k=5,
                )
            except Exception:
                resolved = {}
            target = (resolved or {}).get("target") or {}
            center = target.get("center_xy") or []
            if isinstance(center, (list, tuple)) and len(center) == 2:
                x = int(center[0])
                y = int(center[1])

    if x is None or y is None:
        return {
            "resolved": False,
            "reason": "dialog_target_not_resolved",
            "dialog": status,
            "target": chosen_target,
            "execution": None,
        }

    chosen_target["x"] = int(x)
    chosen_target["y"] = int(y)
    intent = Intent(
        action="click",
        params={
            "x": int(x),
            "y": int(y),
            "button": str(request_body.get("button") or "left"),
        },
        confidence=1.0,
        raw_input=f"dialog_resolve:{chosen_target.get('label') or 'coords'}",
        category=str(request_body.get("category") or "normal"),
    )
    execution = await _execute_intent(
        intent,
        mode=mode,
        verify=verify,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
    )
    success = bool((execution.get("result") or {}).get("success", False))
    return {
        "resolved": success,
        "reason": "ok" if success else str((execution.get("result") or {}).get("message", "dialog_resolve_failed")),
        "dialog": status,
        "target": chosen_target,
        "execution": execution,
    }


# ─── Plugins (F09) ───

@app.get("/plugins")
async def plugins_list(x_api_key: Optional[str] = Header(None)):
    """Lista de plugins cargados."""
    _check_auth(x_api_key)
    if not _state.plugin_mgr:
        return {"plugins": []}
    return {
        "plugins": _state.plugin_mgr.get_info(),
        "event_log": _state.plugin_mgr.get_event_log(20),
    }


# ─── Memory (F11) ───

@app.get("/memory/recent")
async def memory_recent(
    minutes: int = Query(30, ge=1, le=480),
    x_api_key: Optional[str] = Header(None),
):
    """Entradas de memoria recientes."""
    _check_auth(x_api_key)
    if not _state.memory:
        return {"entries": [], "enabled": False}
    return {"entries": _state.memory.get_recent(minutes), "enabled": _state.memory.enabled}


@app.get("/memory/search")
async def memory_search(
    q: str = Query(..., min_length=1),
    x_api_key: Optional[str] = Header(None),
):
    """Buscar en la memoria temporal."""
    _check_auth(x_api_key)
    if not _state.memory or not _state.memory.enabled:
        return {"results": [], "enabled": False}
    return {"results": _state.memory.search(q), "query": q}


@app.post("/memory/toggle")
async def memory_toggle(
    enabled: bool = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Habilitar/deshabilitar memoria temporal."""
    _check_auth(x_api_key)
    if not _state.memory:
        raise HTTPException(503, "Not initialized")
    _state.memory.enabled = enabled
    return {"enabled": _state.memory.enabled}


# ─── Annotations (lapiz/marcador) ───

@app.post("/annotations/add")
async def add_annotation(
    type: str = Query(..., description="circle, rect, arrow, text, freehand"),
    x: int = Query(...),
    y: int = Query(...),
    width: int = Query(50),
    height: int = Query(50),
    color: str = Query("#FF0000"),
    thickness: int = Query(3),
    text: str = Query(""),
    x_api_key: Optional[str] = Header(None),
):
    """
    Agrega una anotacion visual (lapiz/marcador).
    La IA vera el overlay dibujado + la descripcion textual.
    Tipos: circle, rect, arrow, text, freehand
    """
    _check_auth(x_api_key)
    if not _state.vision:
        raise HTTPException(503, "Not initialized")

    import uuid
    ann = Annotation(
        id=str(uuid.uuid4())[:8],
        type=type,
        x=x, y=y,
        width=width, height=height,
        color=color,
        thickness=thickness,
        text=text,
    )
    _state.vision.annotations.add(ann)
    return {"id": ann.id, "type": type, "position": f"({x},{y})", "status": "added"}


@app.get("/annotations/list")
async def list_annotations(x_api_key: Optional[str] = Header(None)):
    """Lista todas las anotaciones activas."""
    _check_auth(x_api_key)
    if not _state.vision:
        raise HTTPException(503, "Not initialized")
    return {
        "count": len(_state.vision.annotations.annotations),
        "annotations": _state.vision.annotations.to_description(),
    }


@app.delete("/annotations/{annotation_id}")
async def remove_annotation(annotation_id: str, x_api_key: Optional[str] = Header(None)):
    """Elimina una anotacion por ID."""
    _check_auth(x_api_key)
    if not _state.vision:
        raise HTTPException(503, "Not initialized")
    removed = _state.vision.annotations.remove(annotation_id)
    return {"removed": removed, "id": annotation_id}


@app.post("/annotations/clear")
async def clear_annotations(x_api_key: Optional[str] = Header(None)):
    """Borra todas las anotaciones."""
    _check_auth(x_api_key)
    if not _state.vision:
        raise HTTPException(503, "Not initialized")
    _state.vision.annotations.clear()
    return {"status": "cleared"}


@app.get("/frame/annotated")
async def frame_annotated(x_api_key: Optional[str] = Header(None)):
    """Frame actual con las anotaciones dibujadas encima. Devuelve JPEG."""
    _check_auth(x_api_key)
    if not _state.buffer or not _state.vision:
        raise HTTPException(503, "Not initialized")

    slot = _state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    rendered = _state.vision.annotations.render_overlay(slot.frame_bytes)
    return Response(content=rendered, media_type="image/jpeg")


# ─── AI Provider Endpoints ───

@app.post("/ai/ask")
async def ai_ask(
    provider: str = Query(..., description="gemini, openai, claude, generic"),
    prompt: str = Query("Describe what you see on this screen."),
    api_key_param: Optional[str] = Query(None, alias="provider_api_key"),
    model: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """
    Envia el frame actual a un provider de IA y retorna la respuesta.
    La IA VE tu pantalla y responde.
    
    Ejemplo: /ai/ask?provider=gemini&provider_api_key=AIza...&prompt=What bug do you see?
    """
    _check_auth(x_api_key)
    if not _state.buffer:
        raise HTTPException(503, "Not initialized")

    slot = _state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    if not api_key_param:
        raise HTTPException(400, "provider_api_key is required")

    from .adapters import create_adapter
    try:
        kwargs = {}
        if model:
            kwargs["model"] = model
        adapter = create_adapter(provider, api_key_param, **kwargs)
        adapter.connect()

        # Build enriched prompt
        enriched = _state.vision.enrich_frame(slot, run_ocr=False) if _state.vision else None
        full_prompt = prompt
        if enriched:
            full_prompt = enriched.to_ai_prompt() + "\n\nUser question: " + prompt

        response = adapter.send_frame(slot.frame_bytes, full_prompt, slot.mime_type)
        adapter.disconnect()

        if response:
            return {
                "text": response.text,
                "provider": response.provider,
                "model": response.model,
                "latency_ms": response.latency_ms,
                "tokens_used": response.tokens_used,
            }
        return {"text": "", "error": "No response from provider"}

    except Exception as e:
        raise HTTPException(500, f"AI provider error: {e}")


# ─── Watchdog (E01) ───

@app.get("/watchdog/alerts")
async def watchdog_alerts(
    count: int = Query(20, ge=1, le=100),
    unacknowledged: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    """Get watchdog alerts."""
    _check_auth(x_api_key)
    if not _state.watchdog:
        return {"alerts": []}
    return {"alerts": _state.watchdog.get_alerts(count, unacknowledged)}


@app.get("/watchdog/triggers")
async def watchdog_triggers(x_api_key: Optional[str] = Header(None)):
    """List all watchdog triggers."""
    _check_auth(x_api_key)
    if not _state.watchdog:
        return {"triggers": []}
    return {"triggers": _state.watchdog.get_triggers(), "stats": _state.watchdog.stats}


@app.post("/watchdog/scan")
async def watchdog_scan(x_api_key: Optional[str] = Header(None)):
    """Manually trigger a watchdog scan on current screen."""
    _check_auth(x_api_key)
    if not _state.watchdog or not _state.buffer:
        raise HTTPException(503, "Not initialized")

    slot = _state.buffer.get_latest()
    if not slot:
        return {"alerts": [], "note": "No frames in buffer"}

    # Get OCR text and window title for scanning
    from .vision import get_active_window_info
    win = get_active_window_info()
    ocr_text = ""
    if _state.vision and _state.vision.ocr.available:
        ocr_result = _state.vision.ocr.extract_text(slot.frame_bytes, frame_hash=slot.phash)
        ocr_text = ocr_result.get("text", "")

    alerts = _state.watchdog.scan(ocr_text=ocr_text, window_title=win.get("title", ""))
    return {
        "new_alerts": [a.to_dict() for a in alerts],
        "total_alerts": _state.watchdog.stats["total_alerts"],
    }


@app.post("/watchdog/acknowledge/{alert_id}")
async def watchdog_ack(alert_id: str, x_api_key: Optional[str] = Header(None)):
    """Acknowledge an alert."""
    _check_auth(x_api_key)
    if not _state.watchdog:
        raise HTTPException(503, "Not initialized")
    return {"acknowledged": _state.watchdog.acknowledge(alert_id)}


# ─── AI Router (F16) ───

@app.post("/ai/route")
async def ai_route(
    prompt: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Route a prompt to the optimal AI model (cheapest that works)."""
    _check_auth(x_api_key)
    if not _state.router:
        raise HTTPException(503, "Not initialized")

    decision = _state.router.route(prompt)
    return {
        "route": decision.route,
        "reason": decision.reason,
        "estimated_cost": decision.estimated_cost,
        "model_suggestion": decision.model_suggestion,
        "needs_image": decision.needs_image,
        "needs_audio": decision.needs_audio,
    }


@app.get("/ai/router/stats")
async def ai_router_stats(x_api_key: Optional[str] = Header(None)):
    """Router cost savings stats."""
    _check_auth(x_api_key)
    if not _state.router:
        return {}
    return _state.router.stats


# ─── IPA v3 endpoints ────────────────────────────────────────────────────────

@app.get("/ipa/status")
async def ipa_status(x_api_key: Optional[str] = Header(None)):
    """IPA v3 bridge status and engine stats."""
    _check_auth(x_api_key)
    if not _state.ipa_bridge:
        return {"running": False, "reason": "not_initialized"}
    return _state.ipa_bridge.stats()


@app.get("/ipa/context")
async def ipa_context(
    seconds: float = Query(30.0),
    x_api_key: Optional[str] = Header(None),
):
    """IPA v3 visual context: motion, scene state, gate events, OCR hint."""
    _check_auth(x_api_key)
    if not _state.ipa_bridge:
        return {"error": "ipa_bridge_not_initialized"}

    ctx = _state.ipa_bridge.visual_context(seconds=seconds) or {}
    gate = _state.ipa_bridge.gate_event(max_age_s=seconds)
    motion = _state.ipa_bridge.motion_now(seconds=5.0) or {}

    # OCR hint from perception engine
    ocr_hint = ""
    try:
        if _state.perception:
            events = _state.perception.get_events(seconds=10)
            for evt in reversed(events):
                txt = evt.details.get("ocr_text", "") if evt.details else ""
                if txt:
                    ocr_hint = txt[:300]
                    break
    except Exception:
        pass

    return {
        **ctx,
        "gate_event": gate.__dict__ if gate else None,
        "motion": motion,
        "ocr_hint": ocr_hint,
        "bridge_stats": _state.ipa_bridge.stats(),
    }


@app.get("/ipa/events")
async def ipa_events(
    seconds: float = Query(30.0),
    x_api_key: Optional[str] = Header(None),
):
    """Recent IPA v3 gate events — significant visual changes."""
    _check_auth(x_api_key)
    if not _state.ipa_bridge:
        return {"error": "ipa_bridge_not_initialized", "events": []}
    events = _state.ipa_bridge.recent_events(seconds=seconds)
    return {
        "events": [e.__dict__ for e in events],
        "count": len(events),
        "seconds": seconds,
    }


    x_api_key: Optional[str] = Header(None),



# ─── Watch Engine endpoints ───

@app.post("/watch/notify")
async def watch_and_notify(
    condition: str = Query(..., description="Condition to watch for"),
    timeout: float = Query(30.0),
    text: Optional[str] = Query(None),
    window_title: Optional[str] = Query(None),
    element: Optional[str] = Query(None),
    idle_seconds: float = Query(3.0),
    monitor_id: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """Wait for a screen condition without consuming tokens.
    Runs synchronously — returns when condition met or timeout expires.
    """
    _check_auth(x_api_key)
    if not _state.watch_engine:
        return {"triggered": False, "reason": "watch_engine_not_initialized"}

    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _state.watch_engine.wait(
            condition=condition, timeout=timeout,
            text=text, window_title=window_title,
            element=element, idle_seconds=idle_seconds,
            monitor_id=monitor_id,
        )
    )
    return result.to_dict()


@app.post("/watch/until")
async def monitor_until(
    condition: str = Query(...),
    timeout: float = Query(120.0),
    text: Optional[str] = Query(None),
    element: Optional[str] = Query(None),
    monitor_id: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """Alias for watch/notify with longer default timeout."""
    _check_auth(x_api_key)
    if not _state.watch_engine:
        return {"triggered": False, "reason": "watch_engine_not_initialized"}
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _state.watch_engine.wait(
            condition=condition, timeout=timeout,
            text=text, element=element, monitor_id=monitor_id,
        )
    )
    return result.to_dict()


# ─── Visual Memory endpoints ───

@app.post("/memory/save")
async def memory_save(x_api_key: Optional[str] = Header(None)):
    """Save current session state to visual memory."""
    _check_auth(x_api_key)
    if not _state.visual_memory:
        return {"saved": False, "reason": "visual_memory_not_initialized"}

    # Collect state snapshot
    state = {}
    try:
        spatial_data = {}
        if _state.monitor_mgr:
            from .smart_locate import SmartLocateEngine
            # Reuse spatial context logic
            spatial_data = {
                "monitor_count":  _state.monitor_mgr.count,
                "active_monitor_id": _state.monitor_mgr._active_monitor_id,
                "monitors": [
                    {"id": m.id, "zone": "?", "width": m.width, "height": m.height}
                    for m in _state.monitor_mgr.monitors
                ],
            }
            win_info = getattr(_state.perception, "_last_window_info", {}) if _state.perception else {}
            if win_info:
                spatial_data["active_window"] = win_info
        state["spatial"] = spatial_data

        if _state.context:
            ctx = _state.context.get_state()
            state["context"] = {"workflow": ctx.get("workflow"), "task_phase": "active"}

        if _state.ipa_bridge:
            events = _state.ipa_bridge.recent_events(seconds=1800)  # last 30min
            state["ipa_events"] = [e.__dict__ for e in events]

        if _state.perception and _state.monitor_mgr:
            ocr_map = {}
            for mon in _state.monitor_mgr.monitors:
                ms = _state.perception._get_monitor_state(mon.id)
                if ms and ms.last_ocr_text:
                    ocr_map[mon.id] = ms.last_ocr_text[:500]
            state["ocr_by_monitor"] = ocr_map

        if _state.perception:
            win_info = getattr(_state.perception, "_last_window_info", {})
            state["window_history"] = [win_info.get("window_title", "")] if win_info else []

    except Exception as e:
        pass

    ok = _state.visual_memory.save(state)
    stats = _state.visual_memory.stats()
    return {"saved": ok, "stats": stats}


@app.get("/memory/load")
async def memory_load(
    max_age_hours: float = Query(48.0),
    x_api_key: Optional[str] = Header(None),
):
    """Load most recent session memory."""
    _check_auth(x_api_key)
    if not _state.visual_memory:
        return {"found": False}
    mem = _state.visual_memory.load(max_age_hours)
    if not mem:
        return {"found": False}
    return {"found": True, **mem.to_dict(), "session_prompt": mem.to_session_prompt()}


@app.get("/memory/prompt")
async def memory_prompt(
    max_age_hours: float = Query(48.0),
    x_api_key: Optional[str] = Header(None),
):
    """Get session context as a ready-to-inject system prompt."""
    _check_auth(x_api_key)
    if not _state.visual_memory:
        return {"prompt": "", "found": False}
    mem = _state.visual_memory.load(max_age_hours)
    if not mem:
        return {"prompt": "", "found": False}
    return {"prompt": mem.to_session_prompt(), "found": True, "age_hours": round(mem.age_hours(), 1)}


@app.get("/memory/stats")
async def memory_stats(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.visual_memory:
        return {}
    return _state.visual_memory.stats()


# ═══════════════════════════════════════════════════════════════
# v1.0 ENDPOINTS: Computer Use — 7 Capas
# ═══════════════════════════════════════════════════════════════


# ─── Capa 7: Safety / Autonomy / Audit ───

@app.get("/safety/status")
async def safety_status(x_api_key: Optional[str] = Header(None)):
    """Estado del sistema de seguridad."""
    _check_auth(x_api_key)
    return {
        "safety": _state.safety.stats if _state.safety else {},
        "autonomy": _state.autonomy.stats if _state.autonomy else {},
        "audit": _state.audit.stats if _state.audit else {},
    }


@app.post("/safety/kill")
async def safety_kill(x_api_key: Optional[str] = Header(None)):
    """KILL SWITCH: detiene toda actividad del agente."""
    _check_auth(x_api_key)
    if _state.safety:
        _state.safety.kill()
    if _state.actions:
        _state.actions.disable()
    return {"killed": True}


@app.post("/safety/resume")
async def safety_resume(x_api_key: Optional[str] = Header(None)):
    """Reactiva el agente despues de un kill."""
    _check_auth(x_api_key)
    if _state.safety:
        _state.safety.resume()
    return {"killed": False}


@app.get("/safety/whitelist")
async def safety_whitelist(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"whitelist": _state.safety.get_whitelist() if _state.safety else []}


@app.get("/operating/mode")
async def get_operating_mode(x_api_key: Optional[str] = Header(None)):
    """Current operating mode: SAFE, RAW, HYBRID."""
    _check_auth(x_api_key)
    return {"mode": _state.operating_mode}


@app.post("/operating/mode")
async def set_operating_mode(
    mode: str = Query(..., description="SAFE | RAW | HYBRID"),
    x_api_key: Optional[str] = Header(None),
):
    """Set operating mode and propagate risk mode into IPA WorldState."""
    _check_auth(x_api_key)
    mode_norm = _normalize_operating_mode(mode)
    _state.operating_mode = mode_norm
    if _state.perception:
        _state.perception.set_risk_mode(mode_norm.lower())
    return {"mode": _state.operating_mode}


@app.post("/autonomy/level")
async def set_autonomy_level(
    level: str = Query(..., description="suggest, confirm, auto"),
    x_api_key: Optional[str] = Header(None),
):
    """Cambia el nivel de autonomia."""
    _check_auth(x_api_key)
    if not _state.autonomy:
        raise HTTPException(503, "Not initialized")
    _state.autonomy.set_level(AutonomyLevel(level))
    return {"level": level}


@app.get("/audit/recent")
async def audit_recent(
    count: int = Query(20, ge=1, le=100),
    x_api_key: Optional[str] = Header(None),
):
    """Ultimas entradas del audit log."""
    _check_auth(x_api_key)
    if not _state.audit:
        return {"entries": []}
    return {"entries": _state.audit.get_recent(count)}


@app.get("/audit/failures")
async def audit_failures(
    count: int = Query(20, ge=1, le=100),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"entries": _state.audit.get_failures(count) if _state.audit else []}


# ─── Capa 1: Actions ───

@app.get("/action/status")
async def action_status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.actions.stats if _state.actions else {"enabled": False}


@app.post("/action/enable")
async def action_enable(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _state.actions:
        _state.actions.enable()
    return {"enabled": True}


@app.post("/action/disable")
async def action_disable(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _state.actions:
        _state.actions.disable()
    return {"enabled": False}


@app.post("/action/click")
async def action_click(
    x: int = Query(...), y: int = Query(...),
    button: str = Query("left"),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for monitor-local coordinates"),
    relative_to_monitor: bool = Query(False, description="If true, x/y are relative to monitor origin"),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.08)  # non-blocking: yield event loop during focus settle
    rx, ry = _translate_click_coords(int(x), int(y), monitor_id, bool(relative_to_monitor))
    result = _state.actions.click(rx, ry, button)
    payload = result.to_dict()
    payload["requested_x"] = int(x)
    payload["requested_y"] = int(y)
    payload["resolved_x"] = int(rx)
    payload["resolved_y"] = int(ry)
    payload["monitor_id"] = int(monitor_id) if monitor_id is not None else None
    payload["relative_to_monitor"] = bool(relative_to_monitor)
    return payload


@app.post("/action/double_click")
async def action_double_click(
    x: int = Query(...), y: int = Query(...),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for monitor-local coordinates"),
    relative_to_monitor: bool = Query(False, description="If true, x/y are relative to monitor origin"),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.08)  # non-blocking: yield event loop during focus settle
    rx, ry = _translate_click_coords(int(x), int(y), monitor_id, bool(relative_to_monitor))
    result = _state.actions.double_click(rx, ry).to_dict()
    result["requested_x"] = int(x)
    result["requested_y"] = int(y)
    result["resolved_x"] = int(rx)
    result["resolved_y"] = int(ry)
    result["monitor_id"] = int(monitor_id) if monitor_id is not None else None
    result["relative_to_monitor"] = bool(relative_to_monitor)
    return result


@app.post("/action/type")
async def action_type(
    text: str = Query(...),
    interval: float = Query(0.02),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.08)  # non-blocking: yield event loop during focus settle
    return _state.actions.type_text(text, interval).to_dict()


@app.post("/action/hotkey")
async def action_hotkey(
    keys: str = Query(..., description="Keys separated by + (e.g. ctrl+s)"),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.08)  # non-blocking: yield event loop during focus settle
    key_list = [k.strip() for k in keys.split("+")]
    return _state.actions.hotkey(*key_list).to_dict()


@app.post("/action/scroll")
async def action_scroll(
    amount: int = Query(...),
    x: Optional[int] = Query(None), y: Optional[int] = Query(None),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for monitor-local x/y"),
    relative_to_monitor: bool = Query(False, description="If true, x/y are relative to monitor origin"),
    focus_handle: Optional[int] = Query(None),
    focus_title: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.08)  # non-blocking: yield event loop during focus settle
    rx = None if x is None else int(x)
    ry = None if y is None else int(y)
    if rx is not None and ry is not None:
        rx, ry = _translate_click_coords(rx, ry, monitor_id, bool(relative_to_monitor))
    result = _state.actions.scroll(amount, rx, ry).to_dict()
    result["requested_x"] = int(x) if x is not None else None
    result["requested_y"] = int(y) if y is not None else None
    result["resolved_x"] = int(rx) if rx is not None else None
    result["resolved_y"] = int(ry) if ry is not None else None
    result["monitor_id"] = int(monitor_id) if monitor_id is not None else None
    result["relative_to_monitor"] = bool(relative_to_monitor)
    return result


@app.post("/action/move")
async def action_move(
    x: int = Query(...), y: int = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Move mouse to coordinates without clicking."""
    _check_auth(x_api_key)
    if not _state.actions or not _state.actions.available:
        return {"action": "move_mouse", "success": False, "message": "Action bridge not available"}
    result = _state.actions.move_mouse(x, y)
    return {"action": "move_mouse", "success": result.success, "message": result.message, "x": x, "y": y}


@app.post("/action/drag")
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
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    if _state.windows and (focus_handle is not None or (focus_title and focus_title.strip())):
        _state.windows.focus_window(title=focus_title, handle=focus_handle)
        await asyncio.sleep(0.08)  # non-blocking: yield event loop during focus settle
    sx, sy = _translate_click_coords(int(start_x), int(start_y), monitor_id, bool(relative_to_monitor))
    ex, ey = _translate_click_coords(int(end_x), int(end_y), monitor_id, bool(relative_to_monitor))
    result = _state.actions.drag_drop(sx, sy, ex, ey, duration).to_dict()
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


@app.get("/action/mouse")
async def action_mouse(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.actions:
        return {"x": 0, "y": 0}
    pos = _state.actions.get_mouse_position()
    mid = _monitor_id_for_rect(int(pos.get("x", 0)), int(pos.get("y", 0)), 1, 1)
    if mid is not None:
        pos["monitor_id"] = int(mid)
    return pos


@app.get("/action/log")
async def action_log(
    count: int = Query(20),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"log": _state.actions.get_action_log(count) if _state.actions else []}


@app.post("/action/precheck")
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
    _check_auth(x_api_key)
    intent = _intent_from_payload(request_body)
    grounding_request = _grounding_request_from_payload(request_body, intent)
    mode = request_body.get("mode") or _state.operating_mode
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = request_body.get("max_staleness_ms")
    if max_staleness_ms is not None:
        max_staleness_ms = int(max_staleness_ms)
    return _build_precheck(
        intent,
        mode,
        include_readiness=True,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
        grounding_request=grounding_request,
    )


@app.post("/action/intent")
async def action_intent(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    High-level action primitive.
    Accepts natural language instruction and executes it through the same
    closed-loop path as /action/execute.
    """
    _check_auth(x_api_key)
    if not _state.intent or not _state.resolver:
        raise HTTPException(503, "Orchestration not initialized")

    instruction = str(request_body.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "instruction is required")

    intent = _state.intent.classify_or_default(instruction)
    mode = request_body.get("mode") or _state.operating_mode
    verify = bool(request_body.get("verify", True))
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = request_body.get("max_staleness_ms")
    if max_staleness_ms is not None:
        max_staleness_ms = int(max_staleness_ms)
    grounding_request = _grounding_request_from_payload(request_body, intent)
    return await _execute_intent(
        intent,
        mode=mode,
        verify=verify,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
        grounding_request=grounding_request,
    )


@app.post("/action/execute")
async def action_execute(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Execute action through current operating mode (SAFE/RAW/HYBRID).
    SAFE applies all guards. HYBRID guards destructive actions only.
    """
    _check_auth(x_api_key)
    intent = _intent_from_payload(request_body)
    grounding_request = _grounding_request_from_payload(request_body, intent)
    mode = request_body.get("mode") or _state.operating_mode
    verify = bool(request_body.get("verify", True))
    context_tick_id = request_body.get("context_tick_id")
    max_staleness_ms = request_body.get("max_staleness_ms")
    if max_staleness_ms is not None:
        max_staleness_ms = int(max_staleness_ms)
    return await _execute_intent(
        intent,
        mode=mode,
        verify=verify,
        context_tick_id=context_tick_id,
        max_staleness_ms=max_staleness_ms,
        grounding_request=grounding_request,
    )


@app.post("/action/raw")
async def action_raw(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """
    Raw execution path (0 guardrails except kill switch).
    Intended for expert setups where external AI handles all safety.
    """
    _check_auth(x_api_key)
    intent = _intent_from_payload(request_body)
    verify = bool(request_body.get("verify", False))
    return await _execute_intent(intent, mode="RAW", verify=verify)


@app.post("/action/verify")
async def action_verify(
    request_body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """Run post-action verification without executing new actions."""
    _check_auth(x_api_key)
    if not _state.verifier:
        raise HTTPException(503, "Verifier not initialized")
    action = (request_body.get("action") or "").strip()
    if not action:
        raise HTTPException(400, "action is required")
    params = request_body.get("params") or {}
    pre_state = request_body.get("pre_state")
    result = _state.verifier.verify(action, params, pre_state)
    return result.to_dict()


# ─── Capa 1: Windows ───

@app.get("/windows/list")
async def windows_list(
    monitor_id: Optional[int] = Query(None, description="Optional monitor filter"),
    visible_only: bool = Query(True),
    exclude_minimized: bool = Query(False),
    exclude_system: bool = Query(True),
    title_contains: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.windows:
        return {"windows": []}
    items = []
    title_filter = (title_contains or "").strip().lower()
    for w in _state.windows.list_windows():
        if visible_only and not bool(getattr(w, "is_visible", True)):
            continue
        if exclude_minimized and bool(getattr(w, "is_minimized", False)):
            continue
        if title_filter and title_filter not in str(getattr(w, "title", "")).lower():
            continue
        payload = w.to_dict()
        mid = _monitor_id_for_rect(w.x, w.y, w.width, w.height)
        if mid is not None:
            payload["monitor_id"] = int(mid)
        if exclude_system and _is_system_noise_window(payload):
            continue
        items.append(payload)

    if monitor_id is not None:
        try:
            target_mid = int(monitor_id)
            items = [w for w in items if int(w.get("monitor_id", -1)) == target_mid]
        except Exception:
            items = []

    items.sort(
        key=lambda w: (
            not bool(w.get("is_visible", True)),
            bool(w.get("is_minimized", False)),
            -int(max(0, int(w.get("width", 0))) * max(0, int(w.get("height", 0)))),
        )
    )
    return {"windows": items}


@app.get("/windows/active")
async def windows_active(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.windows:
        return {}
    win = _state.windows.get_active_window()
    if not win:
        return {}
    payload = win.to_dict()
    monitor_id = _monitor_id_for_rect(win.x, win.y, win.width, win.height)
    if monitor_id is not None:
        payload["monitor_id"] = int(monitor_id)
    return payload


@app.post("/windows/focus")
async def windows_focus(
    title: Optional[str] = Query(None),
    handle: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.windows:
        raise HTTPException(503, "Not initialized")
    if handle is None and not (title and title.strip()):
        raise HTTPException(400, "Either title or handle is required")
    return {"success": _state.windows.focus_window(title=title, handle=handle)}


@app.post("/windows/minimize")
async def windows_minimize(
    title: Optional[str] = Query(None),
    handle: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if handle is None and not (title and title.strip()):
        raise HTTPException(400, "Either title or handle is required")
    return {"success": _state.windows.minimize_window(title=title, handle=handle) if _state.windows else False}


@app.post("/windows/maximize")
async def windows_maximize(
    title: Optional[str] = Query(None),
    handle: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if handle is None and not (title and title.strip()):
        raise HTTPException(400, "Either title or handle is required")
    return {"success": _state.windows.maximize_window(title=title, handle=handle) if _state.windows else False}


@app.post("/windows/close")
async def windows_close(
    title: Optional[str] = Query(None),
    handle: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """DESTRUCTIVE: cierra una ventana."""
    _check_auth(x_api_key)
    if handle is None and not (title and title.strip()):
        raise HTTPException(400, "Either title or handle is required")
    return {"success": _state.windows.close_window(title=title, handle=handle) if _state.windows else False}


@app.post("/windows/move")
async def windows_move(
    title: Optional[str] = Query(None),
    handle: Optional[int] = Query(None),
    x: int = Query(...), y: int = Query(...),
    width: int = Query(-1), height: int = Query(-1),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id for monitor-local x/y"),
    relative_to_monitor: bool = Query(False, description="If true, x/y are relative to monitor origin"),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if handle is None and not (title and title.strip()):
        raise HTTPException(400, "Either title or handle is required")
    rx, ry = _translate_click_coords(int(x), int(y), monitor_id, bool(relative_to_monitor))
    success = _state.windows.move_window(rx, ry, width, height, title=title, handle=handle) if _state.windows else False
    return {
        "success": bool(success),
        "requested_x": int(x),
        "requested_y": int(y),
        "resolved_x": int(rx),
        "resolved_y": int(ry),
        "monitor_id": int(monitor_id) if monitor_id is not None else None,
        "relative_to_monitor": bool(relative_to_monitor),
    }


# ─── Capa 1: Clipboard ───

@app.get("/clipboard/read")
async def clipboard_read(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.clipboard:
        return {"text": ""}
    return {"text": _state.clipboard.read()}


@app.post("/clipboard/write")
async def clipboard_write(text: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"success": _state.clipboard.write(text) if _state.clipboard else False}


@app.get("/clipboard/history")
async def clipboard_history(count: int = Query(20), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"history": _state.clipboard.get_history(count) if _state.clipboard else []}


# ─── Capa 1: Process Manager ───

@app.get("/process/list")
async def process_list(
    sort_by: str = Query("memory"),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"processes": _state.process_mgr.list_processes(sort_by) if _state.process_mgr else []}


@app.get("/process/find")
async def process_find(name: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"matches": _state.process_mgr.find_process(name) if _state.process_mgr else []}


@app.post("/process/launch")
async def process_launch(command: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.process_mgr.launch(command) if _state.process_mgr else {"success": False}


@app.post("/process/terminate")
async def process_terminate(
    name: Optional[str] = Query(None),
    pid: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """DESTRUCTIVE: termina un proceso."""
    _check_auth(x_api_key)
    return _state.process_mgr.terminate(pid=pid, name=name) if _state.process_mgr else {"success": False}


# ─── Capa 2: UI Tree ───

@app.get("/ui/elements")
async def ui_elements(
    pid: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"elements": _state.ui_tree.get_elements(pid=pid) if _state.ui_tree else []}


@app.get("/ui/find")
async def ui_find(
    name: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.ui_tree:
        return {"element": None}
    return {"element": _state.ui_tree.find_element(name=name, role=role)}


@app.get("/ui/find_all")
async def ui_find_all(
    name: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return {"elements": _state.ui_tree.find_all(name=name, role=role) if _state.ui_tree else []}


@app.get("/locate")
async def smart_locate_endpoint(
    query: str = Query(..., description="Natural language target description"),
    monitor_id: Optional[int] = Query(None),
    role: Optional[str] = Query(None, description="Role hint: button|edit|link|checkbox|combobox"),
    x_api_key: Optional[str] = Header(None),
):
    """Smart coordinate resolver — UITree + OCR fusion.

    Returns exact (x,y) coordinates for any visible UI element without
    asking the AI to guess. Used internally by act() when target= is provided.

    Latency: UITree ~2ms, OCR ~5ms (uses cached last frame, no new capture).
    Response: {found, x, y, w, h, source, confidence, label, method_detail}
    """
    _check_auth(x_api_key)
    if not _state.smart_locator:
        return {"found": False, "reason": "smart_locator_not_initialized"}

    # Refresh monitor bounds
    if _state.monitor_mgr:
        _state.smart_locator.update_monitor_bounds({
            m.id: {"left": m.left, "top": m.top, "width": m.width, "height": m.height}
            for m in _state.monitor_mgr.monitors
        })

    # Get active window PID for UITree filtering
    active_pid = None
    try:
        if _state.perception:
            win_info = _state.perception._last_window_info or {}
            active_pid = win_info.get("pid") or None
    except Exception:
        pass

    # Inject OCR blocks from perception pipeline — ZERO extra OCR computation
    # Perception runs OCR every few seconds in background; we just read the results
    try:
        if _state.perception and _state.monitor_mgr:
            import time as _t
            now = _t.perf_counter()
            for mon in _state.monitor_mgr.monitors:
                mid = mon.id
                mon_state = _state.perception._get_monitor_state(mid)
                if not mon_state:
                    continue
                blocks = list(getattr(mon_state, 'last_ocr_blocks', []))
                if not blocks:
                    continue  # no OCR yet — will find via UITree or return not-found
                # Translate from monitor-relative to global desktop coords
                global_blocks = []
                for b in blocks:
                    gb = dict(b)
                    gb["x"] = b.get("x", 0) + mon.left
                    gb["y"] = b.get("y", 0) + mon.top
                    global_blocks.append(gb)
                _state.smart_locator._ocr_cache[mid] = {
                    "ts": now,
                    "blocks": global_blocks,
                    "text": mon_state.last_ocr_text,
                }
    except Exception:
        pass

    # Execute locate synchronously
    # UITree is disabled if no OCR blocks available to keep latency <50ms
    has_ocr = bool(_state.smart_locator._ocr_cache)
    result = _state.smart_locator.locate(
        query=query,
        monitor_id=monitor_id,
        prefer_role=role,
        # Pass "skip_tree" when OCR not ready to avoid blocking PowerShell spawn
        active_window_pid=active_pid if has_ocr else "skip_tree",
    )

    if result is None:
        # If no OCR data yet, include a hint so the AI knows to try again later
        hint = "" if has_ocr else " OCR not ready yet — retry after 10s or use see_now."
        return {"found": False, "reason": "not_found", "query": query, "hint": hint}

    return {"found": True, **result.to_dict()}


@app.post("/ui/click")
async def ui_click(
    name: str = Query(...),
    role: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    return _state.actions.click_element(name, role).to_dict()


@app.post("/ui/type")
async def ui_type(
    field: str = Query(...),
    text: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.actions:
        raise HTTPException(503, "Actions not initialized")
    return _state.actions.type_in_field(field, text).to_dict()


# ─── Capa 3: VS Code ───

@app.post("/vscode/command")
async def vscode_command(cmd: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.vscode.execute_command(cmd) if _state.vscode else {"success": False}


@app.post("/vscode/open")
async def vscode_open(
    path: str = Query(...),
    line: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _state.vscode.open_file(path, line) if _state.vscode else {"success": False}


# ─── Capa 3: Terminal ───

@app.post("/terminal/exec")
async def terminal_exec(
    cmd: str = Query(...),
    cwd: Optional[str] = Query(None),
    timeout: float = Query(30),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if not _state.terminal:
        raise HTTPException(503, "Not initialized")
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _state.terminal.run_command(cmd, cwd=cwd, timeout=timeout)
    )
    return result.to_dict()


@app.post("/terminal/background")
async def terminal_background(
    cmd: str = Query(...), name: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _state.terminal.run_background(cmd, name) if _state.terminal else {"success": False}


@app.get("/terminal/background/{name}")
async def terminal_bg_status(name: str, x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.terminal.get_background_status(name) if _state.terminal else {}


@app.get("/terminal/history")
async def terminal_history(count: int = Query(20), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"history": _state.terminal.get_history(count) if _state.terminal else []}


# ─── Capa 3: Git ───

@app.get("/git/status")
async def git_status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.git_ops.status() if _state.git_ops else {"success": False}


@app.get("/git/log")
async def git_log(count: int = Query(10), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.git_ops.log(count) if _state.git_ops else {"success": False}


@app.get("/git/diff")
async def git_diff(staged: bool = Query(False), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.git_ops.diff(staged) if _state.git_ops else {"success": False}


@app.post("/git/commit")
async def git_commit(message: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.git_ops:
        raise HTTPException(503, "Not initialized")
    add_result = _state.git_ops.add()
    commit_result = _state.git_ops.commit(message)
    return commit_result


@app.post("/git/push")
async def git_push(x_api_key: Optional[str] = Header(None)):
    """DESTRUCTIVE: push al remoto."""
    _check_auth(x_api_key)
    return _state.git_ops.push() if _state.git_ops else {"success": False}


@app.post("/git/pull")
async def git_pull(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.git_ops.pull() if _state.git_ops else {"success": False}


# ─── Capa 4: Browser ───

@app.get("/browser/tabs")
async def browser_tabs(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"tabs": _state.browser.list_tabs() if _state.browser else []}


@app.post("/browser/new_tab")
async def browser_new_tab(url: str = Query("about:blank"), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.browser.new_tab(url) if _state.browser else {"success": False}


@app.post("/browser/activate")
async def browser_activate(tab_id: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.browser.activate_tab(tab_id) if _state.browser else {"success": False}


@app.post("/browser/navigate")
async def browser_navigate(url: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.browser.navigate(url) if _state.browser else {"success": False}


@app.post("/browser/click")
async def browser_click(selector: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.browser.click_selector(selector) if _state.browser else {"success": False}


@app.post("/browser/fill")
async def browser_fill(selector: str = Query(...), value: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.browser.fill_input(selector, value) if _state.browser else {"success": False}


@app.get("/browser/text")
async def browser_text(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"text": _state.browser.get_page_text() if _state.browser else ""}


@app.post("/browser/eval")
async def browser_eval(expression: str = Query(...), x_api_key: Optional[str] = Header(None)):
    """WARNING: Executes arbitrary JavaScript in the browser. Use with caution."""
    _check_auth(x_api_key)
    # Safety: block dangerous patterns
    dangerous = ["document.cookie", "localStorage", "sessionStorage", "eval(", "Function("]
    for pattern in dangerous:
        if pattern in expression:
            return {"error": f"Blocked: expression contains dangerous pattern '{pattern}'"}
    return _state.browser.evaluate(expression) if _state.browser else {}


# ─── Capa 5: File System ───

@app.get("/files/read")
async def files_read(path: str = Query(...), x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return _state.filesystem.read_file(path) if _state.filesystem else {"success": False}


@app.post("/files/write")
async def files_write(
    path: str = Query(...), content: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _state.filesystem.write_file(path, content) if _state.filesystem else {"success": False}


@app.get("/files/list")
async def files_list(
    path: str = Query("."),
    pattern: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _state.filesystem.list_dir(path, pattern) if _state.filesystem else {"success": False}


@app.get("/files/search")
async def files_search(
    pattern: str = Query("*"),
    contains: Optional[str] = Query(None),
    path: str = Query("."),
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    return _state.filesystem.search_files(pattern, contains, path) if _state.filesystem else {"success": False}


@app.delete("/files/delete")
async def files_delete(path: str = Query(...), x_api_key: Optional[str] = Header(None)):
    """DESTRUCTIVE: elimina un archivo."""
    _check_auth(x_api_key)
    return _state.filesystem.delete_file(path) if _state.filesystem else {"success": False}


# ─── Capa 6: Agent / Orchestration ───

@app.post("/agent/do")
async def agent_do(
    instruction: str = Query(..., description="Natural language instruction"),
    x_api_key: Optional[str] = Header(None),
):
    """
    Intent-based action: la IA interpreta y ejecuta.
    "guarda el archivo" → clasifica → resuelve → verifica
    """
    _check_auth(x_api_key)
    if not _state.intent or not _state.resolver:
        raise HTTPException(503, "Orchestration not initialized")
    intent = _state.intent.classify_or_default(instruction)
    return await _execute_intent(intent, mode=_state.operating_mode, verify=True)


@app.post("/agent/plan")
async def agent_plan(
    description: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Crea un plan de ejecucion (dry run)."""
    _check_auth(x_api_key)
    if not _state.planner:
        raise HTTPException(503, "Not initialized")
    plan = _state.planner.create_plan(description)
    return plan.to_dict()


@app.get("/agent/plans")
async def agent_plans(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    return {"plans": _state.planner.list_plans() if _state.planner else []}


@app.get("/agent/status")
async def agent_status(x_api_key: Optional[str] = Header(None)):
    """Estado completo del agente."""
    _check_auth(x_api_key)
    return {
        "operating_mode": {"mode": _state.operating_mode},
        "runtime_profile": {
            "profile": _normalize_runtime_profile(_state.runtime_profile),
            "policy": _runtime_profile_policy(_state.runtime_profile),
        },
        "actions": _state.actions.stats if _state.actions else {},
        "safety": _state.safety.stats if _state.safety else {},
        "autonomy": _state.autonomy.stats if _state.autonomy else {},
        "resolver": _state.resolver.stats if _state.resolver else {},
        "intent": _state.intent.stats if _state.intent else {},
        "planner": _state.planner.stats if _state.planner else {},
        "recovery": _state.recovery.stats if _state.recovery else {},
        "grounding": _state.grounding.status() if _state.grounding else {},
        "behavior_cache": _state.behavior_cache.stats() if _state.behavior_cache else {"enabled": False},
        "audio_interrupt": _state.audio_interrupt.status() if _state.audio_interrupt else {"enabled": False},
        "workers_schedule": _state.perception.get_workers_schedule() if (_state.perception and hasattr(_state.perception, "get_workers_schedule")) else {},
        "perception_readiness": _state.perception.get_readiness() if _state.perception else {},
        "bootstrap_warnings": list(_state.bootstrap_warnings),
    }


# ─── System overview ───

@app.get("/system/telemetry")
async def system_telemetry(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if not _state.host_telemetry:
        return {"available": False, "reason": "telemetry_unavailable"}
    snapshot = _state.host_telemetry.snapshot()
    snapshot["available"] = bool(snapshot.get("available", True))
    return snapshot


@app.get("/system/gpu")
async def system_gpu(x_api_key: Optional[str] = Header(None)):
    """Report GPU/CUDA availability and VRAM for VLM device selection."""
    _check_auth(x_api_key)
    result = {
        "cuda_available": False,
        "gpu_name": None,
        "vram_total_mb": None,
        "vram_free_mb": None,
        "torch_version": None,
        "cuda_version": None,
    }
    try:
        import torch
        result["torch_version"] = torch.__version__
        if torch.cuda.is_available():
            result["cuda_available"] = True
            result["gpu_name"] = torch.cuda.get_device_name(0)
            result["cuda_version"] = torch.version.cuda
            vram_total = torch.cuda.get_device_properties(0).total_mem
            vram_free = vram_total - torch.cuda.memory_allocated(0)
            result["vram_total_mb"] = round(vram_total / 1024 / 1024)
            result["vram_free_mb"] = round(vram_free / 1024 / 1024)
    except ImportError:
        pass
    except Exception as e:
        logger.debug("GPU detection failed: %s", e)
    return result


@app.get("/system/overview")
async def system_overview(x_api_key: Optional[str] = Header(None)):
    """Complete system overview - all components status."""
    _check_auth(x_api_key)
    overview = {
        "version": "1.0.0",
        "operating_mode": _state.operating_mode,
        "runtime_profile": {
            "profile": _normalize_runtime_profile(_state.runtime_profile),
            "policy": _runtime_profile_policy(_state.runtime_profile),
        },
        "capture": {
            "running": _state.capture.is_running if _state.capture else False,
            "fps": _state.capture.current_fps if _state.capture else 0,
        },
        "buffer": _state.buffer.stats if _state.buffer else {},
        "audio": {
            "enabled": _state.audio_capture is not None and _state.audio_capture.is_running if _state.audio_capture else False,
            "stats": _state.audio_buffer.stats if _state.audio_buffer else {},
            "interrupt": _state.audio_interrupt.status() if _state.audio_interrupt else {"enabled": False},
        },
        "host_telemetry": _state.host_telemetry.snapshot() if _state.host_telemetry else {"available": False},
        "os_surface": {
            "available": _state.os_surface is not None,
            "tray": _state.os_surface.tray_state() if _state.os_surface else {"supported": False, "detected": False},
            "notifications": _state.os_surface.notifications(limit=5) if _state.os_surface else {"count": 0, "items": []},
        },
        "ocr": {
            "engine": _state.vision.ocr.engine if _state.vision else "none",
            "available": _state.vision.ocr.available if _state.vision else False,
        },
        "monitors": _state.monitor_mgr.to_dict() if _state.monitor_mgr else {},
        "watchdog": _state.watchdog.stats if _state.watchdog else {},
        "router": _state.router.stats if _state.router else {},
        "ipa_bridge": _state.ipa_bridge.stats() if _state.ipa_bridge else {},
        "memory": _state.memory.stats if _state.memory else {},
        "plugins": len(_state.plugin_mgr.loaded) if _state.plugin_mgr else 0,
        # v1.0: Computer Use
        "actions": _state.actions.stats if _state.actions else {},
        "windows": _state.windows.stats if _state.windows else {},
        "clipboard": _state.clipboard.stats if _state.clipboard else {},
        "process_mgr": _state.process_mgr.stats if _state.process_mgr else {},
        "ui_tree": _state.ui_tree.stats if _state.ui_tree else {},
        "vscode": _state.vscode.stats if _state.vscode else {},
        "terminal": _state.terminal.stats if _state.terminal else {},
        "git": _state.git_ops.stats if _state.git_ops else {},
        "browser": _state.browser.stats if _state.browser else {},
        "filesystem": _state.filesystem.stats if _state.filesystem else {},
        "safety": _state.safety.stats if _state.safety else {},
        "autonomy": _state.autonomy.stats if _state.autonomy else {},
        "resolver": _state.resolver.stats if _state.resolver else {},
        "grounding": _state.grounding.status() if _state.grounding else {},
        "license": {
            "plan": _state.license.plan.value if _state.license else "free",
            "is_pro": _state.license.is_pro if _state.license else False,
            "user": _state.license.user if _state.license else {},
        },
        "behavior_cache": _state.behavior_cache.stats() if _state.behavior_cache else {"enabled": False},
        "perception": {
            "readiness": _state.perception.get_readiness() if _state.perception else {},
            "world": _state.perception.get_world_state() if _state.perception else {},
            "workers_schedule": _state.perception.get_workers_schedule() if (_state.perception and hasattr(_state.perception, "get_workers_schedule")) else {},
        },
        "bootstrap_warnings": list(_state.bootstrap_warnings),
    }
    return overview


@app.get("/license/status")
async def license_status():
    """Current license plan and available features."""
    lic = get_license()
    return {
        "plan": lic.plan.value,
        "is_pro": lic.is_pro,
        "user": lic.user,
        "actions": {
            "available": sorted(lic.available_mcp_tools),
            "total": len(lic.available_mcp_tools),
            "max": len(lic.available_mcp_tools) if lic.is_pro else 7,
        },
        "mcp_tools": {
            "available": sorted(lic.available_mcp_tools),
            "total": len(lic.available_mcp_tools),
            "max": len(lic.available_mcp_tools),
        },
        "upgrade_url": None if lic.is_pro else "https://iluminaty.dev/#pricing",
    }


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


@app.get("/tokens/status")
async def tokens_status(x_api_key: Optional[str] = Header(None)):
    """Current token usage and budget."""
    _check_auth(x_api_key)
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


@app.post("/tokens/mode")
async def tokens_set_mode(
    mode: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Set response mode: text_only, low_res, medium_res, full_res."""
    _check_auth(x_api_key)
    if mode not in TOKEN_COSTS:
        raise HTTPException(400, f"Invalid mode. Choose: {list(TOKEN_COSTS.keys())}")
    _tokens.mode = mode
    return {"mode": mode, "estimated_tokens_per_call": TOKEN_COSTS[mode]["tokens"]}


@app.post("/tokens/budget")
async def tokens_set_budget(
    limit: int = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Set token budget. 0 = unlimited."""
    _check_auth(x_api_key)
    _tokens.budget = max(0, limit)
    return {"budget": _tokens.budget, "used": _tokens.used, "remaining": _tokens.budget_remaining()}


@app.post("/tokens/reset")
async def tokens_reset(x_api_key: Optional[str] = Header(None)):
    """Reset token counter."""
    _check_auth(x_api_key)
    _tokens.used = 0
    _tokens.history.clear()
    return {"reset": True, "used": 0}


@app.get("/vision/smart")
async def vision_smart(
    mode: Optional[str] = Query(None),
    monitor_id: Optional[int] = Query(None, description="Optional monitor id. Defaults to active monitor."),
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
    _check_auth(x_api_key)
    if not _state.buffer or not _state.vision:
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

    slot, resolved_mid = _latest_slot_for_monitor(monitor_id)
    if not slot:
        _raise_no_frame_available(monitor_id)

    result = {}

    if active_mode == "text_only":
        enriched = _state.vision.enrich_frame(slot, run_ocr=True)
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

        enriched = _state.vision.enrich_frame(slot, run_ocr=True)
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

    # Track tokens
    est = _tokens.estimate(active_mode)
    _tokens.record(f"vision/smart ({active_mode})", est)
    result["token_estimate"] = est
    result["tokens_used_total"] = _tokens.used

    return JSONResponse(result)
