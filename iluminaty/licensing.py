"""
ILUMINATY - Licensing & Tier System
======================================
Open source. Registration required for full access.

Tiers:
  unregistered — basic perception only (demo mode)
  free         — registered via iluminaty.dev, full perception + basic actions
  pro          — registered, all tools unlocked (free of charge)
  custom       — golden key via email, everything + no rate limits + priority support

All plans except custom are free. Registration generates an API key
that authenticates the connection. Without a valid key, only demo tools work.
"""

from __future__ import annotations

import json
import logging
import os
import time
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

AUTH_API = os.environ.get(
    "ILUMINATY_AUTH_API", "https://api.iluminaty.dev/auth/validate"
)
CACHE_FILE = Path(os.environ.get("ILUMINATY_LICENSE_CACHE", ""))
if not CACHE_FILE.name:
    _base = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME") or "."
    CACHE_FILE = Path(_base) / "ILUMINATY" / ".license_cache.json"
CACHE_TTL_S = 86400  # 24 hours

# Dev/test keys (always Pro)
DEV_KEYS = {
    "ILUM-dev-local",
    "ILUM-dev-local-test",
}

CONTACT_EMAIL = "custom@iluminaty.dev"


class Plan(str, Enum):
    UNREGISTERED = "unregistered"
    FREE = "free"
    PRO = "pro"
    CUSTOM = "custom"


# ─── Tier feature definitions ────────────────────────────────────────────────

# Unregistered: demo mode — perception only, no actions
UNREGISTERED_MCP_TOOLS = {
    "see_screen", "see_now", "get_spatial_context", "read_screen_text", "perception",
    "perception_world", "spatial_state", "screen_status",
    "get_context",
}

# Free (registered): full perception + basic act
FREE_MCP_TOOLS = UNREGISTERED_MCP_TOOLS | {
    "act", "what_changed", "watch_and_notify", "monitor_until", "get_session_memory", "save_session_memory",
    "see_changes", "see_monitor",
 "vision_query",
    "set_operating_mode",
    "window_minimize", "window_maximize", "window_close",
    "move_window", "drag_screen",
    "get_audio_level",
}

# Pro (registered): everything
PRO_MCP_TOOLS = FREE_MCP_TOOLS | {
    "see_now",
    "os_dialog_status", "os_dialog_resolve",
    "focus_window", "browser_navigate", "browser_tabs",
 "run_command",
    "list_windows",
 "read_file", "write_file",
    "get_clipboard", "agent_status",
    "do_action", "operate_cycle",
}

# Custom: everything (same as Pro but with golden key validation)
CUSTOM_MCP_TOOLS = PRO_MCP_TOOLS

ALL_MCP_TOOLS = CUSTOM_MCP_TOOLS

# ─── HTTP endpoint gating ────────────────────────────────────────────────────

FREE_ENDPOINTS = {
    # Always accessible
    "/health", "/", "/dashboard",
    "/frame/latest", "/frames", "/buffer/stats",
    "/vision/snapshot", "/vision/changes", "/vision/status",
    "/vision/annotate", "/vision/ocr", "/vision/describe",
    "/system/gpu",
    "/context", "/context/update",
    "/audio/level", "/audio/transcript",
    # Basic actions (needed by act tool)
    "/action/click", "/action/type", "/action/key",
    "/action/hotkey", "/action/scroll",
    "/action/screenshot", "/action/mouse_position",
    "/action/double_click", "/action/right_click",
    "/action/move", "/action/drag",
    "/windows/focus",
    # Agent
    "/agent/do", "/agent/status",
    # Perception
    "/perception", "/perception/events", "/perception/state",
    "/perception/world", "/perception/trace",
    "/perception/readiness", "/perception/query",
    "/perception/stream",
    # Monitors
    "/monitors", "/monitors/info",
    # Operating mode
    "/operating/mode",
}

PRO_ENDPOINTS = {
    # Advanced actions
    "/action/hold_key", "/action/release_key",
    # UI Tree
    "/ui/click", "/ui/type", "/ui/select", "/ui/find", "/ui/tree",
    # Windows (advanced)
    "/windows", "/windows/resize",
    # Clipboard
    "/clipboard", "/clipboard/set",
    # Process
    "/process/list", "/process/kill",
    # Browser
    "/browser/navigate", "/browser/click", "/browser/type",
    "/browser/eval", "/browser/tabs",
    # Terminal
    "/terminal/exec", "/terminal/sessions",
    # Git
    "/git/status", "/git/commit", "/git/diff",
    # Filesystem
    "/files/read", "/files/write", "/files/list",
    # Planner / Autonomy
    "/planner/plan", "/autonomy/level",
}


# ─── License Manager ─────────────────────────────────────────────────────────

class LicenseManager:
    """
    Validates API keys against iluminaty.dev and determines plan tier.
    Caches validation results for 24 hours.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or os.environ.get("ILUMINATY_KEY", "")).strip()
        self.plan: Plan = Plan.UNREGISTERED
        self.user: dict = {}
        self.limits: dict = {}
        self.validated = False
        self._last_validated: float = 0

    def validate(self) -> bool:
        """Validate key and determine plan. Always returns True (fails safe to UNREGISTERED)."""
        if not self.api_key:
            self.plan = Plan.UNREGISTERED
            self.validated = True
            return True

        # Dev keys → PRO instantly
        if self.api_key in DEV_KEYS:
            self.plan = Plan.PRO
            self.validated = True
            return True

        # Golden key prefix → CUSTOM
        if self.api_key.startswith("ILUM-custom-"):
            self.plan = Plan.CUSTOM
            self.validated = True
            return True

        # Regular key → check cache first
        cached = self._read_cache()
        if cached:
            self.plan = Plan(cached.get("plan", "free"))
            self.user = cached.get("user", {})
            self.limits = cached.get("limits", {})
            self.validated = True
            return True

        # Remote validation
        result = self._validate_remote()
        if result:
            self.plan = Plan(result.get("plan", "free"))
            self.user = result.get("user", {})
            self.limits = result.get("limits", {})
            self._write_cache(result)
        else:
            # Server unreachable — if key looks valid, give PRO benefit of doubt
            if self.api_key.startswith("ILUM-"):
                self.plan = Plan.PRO
            else:
                self.plan = Plan.FREE

        self.validated = True
        self._last_validated = time.time()
        return True

    def _validate_remote(self) -> Optional[dict]:
        try:
            import urllib.request
            req = urllib.request.Request(
                AUTH_API,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": "ILUMINATY/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                if data.get("valid"):
                    return data
        except Exception as e:
            logger.debug("License validation failed: %s", e)
        return None

    def _read_cache(self) -> Optional[dict]:
        try:
            if CACHE_FILE.exists():
                data = json.loads(CACHE_FILE.read_text())
                if data.get("key") == self.api_key:
                    age = time.time() - data.get("ts", 0)
                    if age < CACHE_TTL_S:
                        return data
        except Exception:
            pass
        return None

    def _write_cache(self, data: dict):
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            cache = {**data, "key": self.api_key, "ts": time.time()}
            CACHE_FILE.write_text(json.dumps(cache))
        except Exception:
            pass

    # ─── Properties ───

    @property
    def is_registered(self) -> bool:
        return self.plan in (Plan.FREE, Plan.PRO, Plan.CUSTOM)

    @property
    def is_pro(self) -> bool:
        return self.plan in (Plan.PRO, Plan.CUSTOM)

    @property
    def is_custom(self) -> bool:
        return self.plan == Plan.CUSTOM

    @property
    def available_mcp_tools(self) -> set:
        if self.is_custom:
            return CUSTOM_MCP_TOOLS
        if self.is_pro:
            return PRO_MCP_TOOLS
        if self.is_registered:
            return FREE_MCP_TOOLS
        return UNREGISTERED_MCP_TOOLS

    def is_endpoint_allowed(self, path: str) -> bool:
        if self.is_pro:
            return True
        clean_path = path.split("?")[0].rstrip("/")
        if clean_path in FREE_ENDPOINTS or clean_path == "":
            return True
        if clean_path in PRO_ENDPOINTS:
            return False
        return True  # Unknown endpoints allowed (future-proof)

    def status(self) -> dict:
        return {
            "plan": self.plan.value,
            "registered": self.is_registered,
            "pro": self.is_pro,
            "custom": self.is_custom,
            "validated": self.validated,
            "user": self.user,
            "tools_count": len(self.available_mcp_tools),
            "contact": CONTACT_EMAIL,
        }


# ─── Global instance ─────────────────────────────────────────────────────────

_license: Optional[LicenseManager] = None


def get_license() -> Optional[LicenseManager]:
    return _license


def init_license(api_key: Optional[str] = None) -> LicenseManager:
    global _license
    _license = LicenseManager(api_key)
    _license.validate()
    return _license
