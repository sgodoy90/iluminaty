"""
ILUMINATY - Open Source, No License Gates
==========================================
All tools are available to everyone. No registration, no API key required.

In the future, a "custom" tier may be introduced for enterprise use cases
(SLA, dedicated support, team management). That's it.

For now: clone, install, run. All 38 tools available immediately.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class Plan(str, Enum):
    FREE = "free"
    CUSTOM = "custom"   # reserved for future enterprise tier


# All MCP tools — no gates, no tiers
ALL_MCP_TOOLS = {
    # Vision
    "see_screen", "see_now", "what_changed", "see_changes", "see_monitor",
    "read_screen_text", "vision_query",
    # Perception / context
    "perception", "perception_world", "get_context",
    "get_spatial_context", "spatial_state", "refresh_monitors", "see_region",
    # Watch engine
    "watch_and_notify", "monitor_until",
    # Memory
    "get_session_memory", "save_session_memory",
    # Computer use
    "do_action", "operate_cycle", "set_operating_mode", "act", "drag_screen",
    # Windows
    "list_windows", "focus_window", "window_minimize", "window_maximize",
    "window_close", "move_window",
    # Browser
    "browser_navigate", "browser_tabs",
    # Files / system
    "run_command", "read_file", "write_file", "get_clipboard",
    # Pipeline / workspace
    "find_on_screen",
    "open_path",
    "open_on_monitor",
    # Recording
    "screen_record",
    # Multi-agent coordination
    "agent_dispatch", "agent_inbox", "agent_report",
    # Status
    "screen_status", "agent_status", "get_audio_level",
    "os_dialog_status", "os_dialog_resolve",
}

# Aliases for backward compat (code that imports these)
UNREGISTERED_MCP_TOOLS = ALL_MCP_TOOLS
FREE_MCP_TOOLS = ALL_MCP_TOOLS
PRO_MCP_TOOLS = ALL_MCP_TOOLS
CUSTOM_MCP_TOOLS = ALL_MCP_TOOLS


class LicenseManager:
    """
    Stub license manager — always returns free plan with all tools.
    Kept for API compatibility with server.py internals.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or "").strip()
        self.plan: Plan = Plan.FREE
        self.user: dict = {}
        self.limits: dict = {}
        self.validated = True

    def validate(self) -> bool:
        return True

    @property
    def is_registered(self) -> bool:
        return True

    @property
    def is_pro(self) -> bool:
        return True

    @property
    def is_custom(self) -> bool:
        return self.plan == Plan.CUSTOM

    @property
    def available_mcp_tools(self) -> set:
        return ALL_MCP_TOOLS

    def is_endpoint_allowed(self, path: str) -> bool:
        return True

    def status(self) -> dict:
        return {
            "plan": "free",
            "registered": True,
            "pro": True,
            "custom": False,
            "validated": True,
            "user": {},
            "tools_count": len(ALL_MCP_TOOLS),
            "note": "Open source — all tools available, no registration required.",
        }


# Global instance
_license: Optional[LicenseManager] = None


def get_license() -> Optional[LicenseManager]:
    return _license


def init_license(api_key: Optional[str] = None) -> LicenseManager:
    global _license
    _license = LicenseManager(api_key)
    return _license
