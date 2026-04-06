"""Route module — system."""
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

# ─── System overview ───

@router.get("/system/telemetry")
async def system_telemetry(x_api_key: Optional[str] = Header(None)):
    _srv._check_auth(x_api_key)
    if not _srv._state.host_telemetry:
        return {"available": False, "reason": "telemetry_unavailable"}
    snapshot = _srv._state.host_telemetry.snapshot()
    snapshot["available"] = bool(snapshot.get("available", True))
    return snapshot


@router.get("/system/gpu")
async def system_gpu(x_api_key: Optional[str] = Header(None)):
    """Report GPU/CUDA availability and VRAM for VLM device selection."""
    _srv._check_auth(x_api_key)
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
        _srv.logger.debug("GPU detection failed: %s", e)
    return result


@router.get("/system/overview")
async def system_overview(x_api_key: Optional[str] = Header(None)):
    """Complete system overview - all components status."""
    _srv._check_auth(x_api_key)
    overview = {
        "version": "1.0.0",
        "operating_mode": _srv._state.operating_mode,
        "runtime_profile": {
            "profile": _srv._normalize_runtime_profile(_srv._state.runtime_profile),
            "policy": _srv._runtime_profile_policy(_srv._state.runtime_profile),
        },
        "capture": {
            "running": _srv._state.capture.is_running if _srv._state.capture else False,
            "fps": _srv._state.capture.current_fps if _srv._state.capture else 0,
        },
        "buffer": _srv._state.buffer.stats if _srv._state.buffer else {},
        "audio": {
            "enabled": _srv._state.audio_capture is not None and _srv._state.audio_capture.is_running if _srv._state.audio_capture else False,
            "stats": _srv._state.audio_buffer.stats if _srv._state.audio_buffer else {},
            "interrupt": _srv._state.audio_interrupt.status() if _srv._state.audio_interrupt else {"enabled": False},
        },
        "host_telemetry": _srv._state.host_telemetry.snapshot() if _srv._state.host_telemetry else {"available": False},
        "os_surface": {
            "available": _srv._state.os_surface is not None,
            "tray": _srv._state.os_surface.tray_state() if _srv._state.os_surface else {"supported": False, "detected": False},
            "notifications": _srv._state.os_surface.notifications(limit=5) if _srv._state.os_surface else {"count": 0, "items": []},
        },
        "ocr": {
            "engine": _srv._state.vision.ocr.engine if _srv._state.vision else "none",
            "available": _srv._state.vision.ocr.available if _srv._state.vision else False,
        },
        "monitors": _srv._state.monitor_mgr.to_dict() if _srv._state.monitor_mgr else {},
        "watchdog": _srv._state.watchdog.stats if _srv._state.watchdog else {},
        "ipa_bridge": _srv._state.ipa_bridge.stats() if _srv._state.ipa_bridge else {},
        # v1.0: Computer Use
        "actions": _srv._state.actions.stats if _srv._state.actions else {},
        "windows": _srv._state.windows.stats if _srv._state.windows else {},
        "clipboard": _srv._state.clipboard.stats if _srv._state.clipboard else {},
        "process_mgr": _srv._state.process_mgr.stats if _srv._state.process_mgr else {},
        "ui_tree": _srv._state.ui_tree.stats if _srv._state.ui_tree else {},
        "filesystem": _srv._state.filesystem.stats if _srv._state.filesystem else {},
        "safety": _srv._state.safety.stats if _srv._state.safety else {},
        "resolver": _srv._state.resolver.stats if _srv._state.resolver else {},
        "grounding": _srv._state.grounding.status() if _srv._state.grounding else {},
        "license": {
            "plan": _srv._state.license.plan.value if _srv._state.license else "free",
            "is_pro": _srv._state.license.is_pro if _srv._state.license else False,
            "user": _srv._state.license.user if _srv._state.license else {},
        },
        "behavior_cache": _srv._state.behavior_cache.stats() if _srv._state.behavior_cache else {"enabled": False},
        "perception": {
            "readiness": _srv._state.perception.get_readiness() if _srv._state.perception else {},
            "world": _srv._state.perception.get_world_state() if _srv._state.perception else {},
            "workers_schedule": _srv._state.perception.get_workers_schedule() if (_srv._state.perception and hasattr(_srv._state.perception, "get_workers_schedule")) else {},
        },
        "bootstrap_warnings": list(_srv._state.bootstrap_warnings),
    }
    return overview


@router.get("/license/status")
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


