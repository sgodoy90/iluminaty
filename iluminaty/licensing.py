"""
ILUMINATY - Licensing & Plan Gate
==================================
Validates ILUMINATY API keys against api.iluminaty.dev
and gates features based on the user's plan (free/pro).

Usage:
  from .licensing import LicenseManager, requires_pro

  license = LicenseManager(api_key="ILUM-pro-xxxxxxx")
  await license.validate()

  @requires_pro
  async def browser_action(request):
      ...
"""

import json
import os
import time
import urllib.request
import urllib.error
import logging
from pathlib import Path
from typing import Optional
from functools import wraps
from enum import Enum

from fastapi import HTTPException

logger = logging.getLogger("iluminaty.licensing")

AUTH_API_URL = os.environ.get("ILUMINATY_AUTH_URL", "https://api.iluminaty.dev")
CACHE_FILE = Path.home() / ".iluminaty" / "license_cache.json"
CACHE_TTL = 86400  # 24 hours

# Developer keys — bypass remote validation, always Pro
DEV_KEYS = {
    "ILUM-dev-godo-master-key-2026",
}


class Plan(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# ─── Free vs Pro feature definitions ───

FREE_ACTIONS = {
    "click", "type_text", "press_key", "hotkey",
    "screenshot_region", "get_mouse_position", "scroll",
}

PRO_ACTIONS = {
    "double_click", "right_click", "move_mouse", "drag_drop",
    "hold_key", "release_key", "click_element", "type_in_field",
    "select_option", "get_action_log",
}

ALL_ACTIONS = FREE_ACTIONS | PRO_ACTIONS

FREE_MCP_TOOLS = {
    "see_screen", "see_changes", "read_screen_text",
    "screen_status", "get_context", "do_action", "get_audio_level",
}

PRO_MCP_TOOLS = {
    "annotate_screen", "click_element", "type_text",
    "run_command", "list_windows", "find_ui_element",
    "read_file", "write_file", "get_clipboard", "agent_status",
}

ALL_MCP_TOOLS = FREE_MCP_TOOLS | PRO_MCP_TOOLS

# Free tier endpoint paths (vision + basic actions)
FREE_ENDPOINTS = {
    # Vision (all free)
    "/frame/latest", "/frames", "/buffer/stats", "/health",
    "/vision/snapshot", "/vision/changes", "/vision/status",
    "/vision/annotate", "/vision/ocr",
    "/context", "/context/update",
    "/audio/level", "/audio/transcript",
    # Basic actions
    "/action/click", "/action/type", "/action/key",
    "/action/hotkey", "/action/scroll",
    "/action/screenshot", "/action/mouse_position",
    # Dashboard
    "/", "/dashboard",
    # Agent (limited in free)
    "/agent/do", "/agent/status",
}

# Endpoints that require Pro
PRO_ENDPOINTS = {
    # Advanced actions
    "/action/double_click", "/action/right_click",
    "/action/move", "/action/drag",
    "/action/hold_key", "/action/release_key",
    # UI Tree
    "/ui/click", "/ui/type", "/ui/select", "/ui/find", "/ui/tree",
    # Windows
    "/windows", "/windows/focus", "/windows/resize",
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


class LicenseManager:
    """Manages ILUMINATY license validation and plan gating."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ILUMINATY_KEY", "")
        self.plan: Plan = Plan.FREE
        self.user: dict = {}
        self.limits: dict = {}
        self.validated = False
        self._last_validated: float = 0

    async def validate(self) -> bool:
        """Validate the API key against auth server. Uses cache if available."""
        if not self.api_key:
            logger.info("No ILUMINATY key provided — running in Free mode")
            self.plan = Plan.FREE
            self.validated = True
            return True

        # Developer keys — instant Pro, no remote call
        if self.api_key in DEV_KEYS:
            logger.info("Developer key detected — Pro mode unlocked")
            self.plan = Plan.PRO
            self.user = {"name": "Developer", "email": "dev@iluminaty.dev"}
            self.validated = True
            return True

        # Check cache first
        cached = self._read_cache()
        if cached:
            self.plan = Plan(cached["plan"])
            self.user = cached.get("user", {})
            self.limits = cached.get("limits", {})
            self.validated = True
            logger.info(f"License validated from cache — plan: {self.plan.value}")
            return True

        # Validate against API
        try:
            result = await self._validate_remote()
            if result.get("valid"):
                self.plan = Plan(result["plan"])
                self.user = result.get("user", {})
                self.limits = result.get("limits", {})
                self.validated = True
                self._last_validated = time.time()
                self._write_cache(result)
                logger.info(f"License validated remotely — plan: {self.plan.value}")
                return True
            else:
                logger.warning(f"License validation failed: {result}")
                self.plan = Plan.FREE
                self.validated = True
                return False
        except Exception as e:
            logger.warning(f"Could not reach auth server: {e} — using cached/free plan")
            self.plan = Plan.FREE
            self.validated = True
            return True  # Don't block usage if auth server is down

    async def _validate_remote(self) -> dict:
        """Call api.iluminaty.dev/auth/validate."""
        url = f"{AUTH_API_URL}/auth/validate"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.api_key}",
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def _read_cache(self) -> Optional[dict]:
        """Read cached license validation."""
        try:
            if CACHE_FILE.exists():
                data = json.loads(CACHE_FILE.read_text())
                if data.get("api_key") == self.api_key:
                    if time.time() - data.get("cached_at", 0) < CACHE_TTL:
                        return data
        except Exception as e:
            logger.debug("Failed reading license cache: %s", e)
        return None

    def _write_cache(self, result: dict):
        """Cache license validation result."""
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            cache = {**result, "api_key": self.api_key, "cached_at": time.time()}
            CACHE_FILE.write_text(json.dumps(cache))
        except Exception as e:
            logger.debug(f"Could not write license cache: {e}")

    @property
    def is_pro(self) -> bool:
        return self.plan in (Plan.PRO, Plan.ENTERPRISE)

    @property
    def available_actions(self) -> set:
        return ALL_ACTIONS if self.is_pro else FREE_ACTIONS

    @property
    def available_mcp_tools(self) -> set:
        return ALL_MCP_TOOLS if self.is_pro else FREE_MCP_TOOLS

    def is_endpoint_allowed(self, path: str) -> bool:
        """Check if an endpoint path is allowed for the current plan."""
        if self.is_pro:
            return True
        # Strip query params
        clean_path = path.split("?")[0].rstrip("/")
        if clean_path in FREE_ENDPOINTS or clean_path == "":
            return True
        # Check if it's a pro endpoint
        if clean_path in PRO_ENDPOINTS:
            return False
        # Allow unknown endpoints (future-proof)
        return True

    def check_action(self, action_name: str):
        """Raise if action is not available in current plan."""
        if action_name in self.available_actions:
            return
        raise HTTPException(
            status_code=403,
            detail={
                "error": "pro_required",
                "message": f"Action '{action_name}' requires ILUMINATY Pro.",
                "upgrade_url": "https://iluminaty.dev/#pricing",
                "current_plan": self.plan.value,
            },
        )

    def check_mcp_tool(self, tool_name: str):
        """Raise if MCP tool is not available in current plan."""
        if tool_name in self.available_mcp_tools:
            return
        raise HTTPException(
            status_code=403,
            detail={
                "error": "pro_required",
                "message": f"Tool '{tool_name}' requires ILUMINATY Pro.",
                "upgrade_url": "https://iluminaty.dev/#pricing",
            },
        )


# ─── Global license instance ───
_license: Optional[LicenseManager] = None


def get_license() -> LicenseManager:
    """Get the global license manager."""
    global _license
    if _license is None:
        _license = LicenseManager()
    return _license


def init_license(api_key: Optional[str] = None) -> LicenseManager:
    """Initialize the global license manager."""
    global _license
    _license = LicenseManager(api_key=api_key)
    return _license


# ─── Decorators for FastAPI endpoints ───

def requires_pro(func):
    """Decorator that blocks Free plan users from accessing an endpoint."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        lic = get_license()
        if not lic.is_pro:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "pro_required",
                    "message": f"This feature requires ILUMINATY Pro ($29/mo).",
                    "upgrade_url": "https://iluminaty.dev/#pricing",
                    "current_plan": lic.plan.value,
                },
            )
        return await func(*args, **kwargs)
    return wrapper


def requires_plan(min_plan: Plan):
    """Decorator that requires at least a certain plan level."""
    plan_order = {Plan.FREE: 0, Plan.PRO: 1, Plan.ENTERPRISE: 2}
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            lic = get_license()
            if plan_order.get(lic.plan, 0) < plan_order[min_plan]:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "plan_required",
                        "message": f"This feature requires ILUMINATY {min_plan.value}.",
                        "upgrade_url": "https://iluminaty.dev/#pricing",
                    },
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator
