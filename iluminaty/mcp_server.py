"""
ILUMINATY - MCP Server
========================
Model Context Protocol server para que coding agents
(Claude Code, Cursor, etc.) puedan "ver" la pantalla.

La IA llama a estas tools cuando necesita ver:
- see_screen    → snapshot enriquecido actual
- see_region    → zoom a una region especifica
- see_changes   → que cambio en los ultimos N segundos
- annotate      → marcar algo en la pantalla
- read_text     → OCR de la pantalla o region

Uso:
  claude mcp add iluminaty -- python -m iluminaty.mcp

O como servidor standalone:
  python -m iluminaty.mcp --port 8421
"""

import json
import sys
import base64
import time
import urllib.request
import urllib.parse

import os

# ILUMINATY API base URL - configurable via env var
API_BASE = os.environ.get("ILUMINATY_API_URL", "http://127.0.0.1:8420")
# Optional API key when ILUMINATY server auth is enabled
API_KEY = os.environ.get("ILUMINATY_API_KEY", "")

# ILUMINATY license key - gates MCP tools to free/pro plan
ILUMINATY_KEY = os.environ.get("ILUMINATY_KEY", "")

# Free tier tools (10) — available without license
FREE_MCP_TOOLS = {
    "see_screen", "see_changes", "read_screen_text", "perception",
    "screen_status", "get_context", "do_action", "raw_action",
    "action_precheck", "verify_action",
    "perception_world", "perception_trace", "set_operating_mode",
    "vision_query",
    "grounding_status", "grounding_resolve", "click_grounded", "type_grounded",
    "window_minimize", "window_maximize", "window_close",
    "get_audio_level",
    "token_status", "set_token_mode", "set_token_budget",
}

# All tools (28) — available with Pro license
ALL_MCP_TOOLS = {
    "see_screen", "see_changes", "annotate_screen", "read_screen_text", "perception",
    "perception_world", "perception_trace",
    "screen_status", "get_context", "get_audio_level",
    "do_action", "raw_action", "action_precheck", "verify_action",
    "set_operating_mode",
    "vision_query",
    "grounding_status", "grounding_resolve", "click_grounded", "type_grounded",
    "click_element", "type_text", "run_command",
    "list_windows", "find_ui_element", "read_file", "write_file",
    "window_minimize", "window_maximize", "window_close",
    "get_clipboard", "agent_status",
    # Human-like navigation
    "watch_screen", "focus_window", "browser_navigate", "browser_tabs",
    "click_screen", "keyboard", "scroll",
    "monitor_info", "see_monitor",
    # Token management
    "token_status", "set_token_mode", "set_token_budget",
}


def _get_plan() -> str:
    """Check license plan by calling the local server or auth API."""
    if not ILUMINATY_KEY:
        return "free"
    try:
        url = API_BASE + "/license/status"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return data.get("plan", "free")
    except Exception:
        # Fallback: check key prefix
        if ILUMINATY_KEY.startswith("ILUM-pro") or ILUMINATY_KEY.startswith("ILUM-dev"):
            return "pro"
        return "free"


def _get_allowed_tools() -> set:
    """Return set of tool names allowed for current plan."""
    plan = _get_plan()
    if plan in ("pro", "enterprise"):
        return ALL_MCP_TOOLS
    return FREE_MCP_TOOLS


def _api_get(path: str) -> dict:
    """GET request to ILUMINATY API."""
    url = API_BASE + path
    headers = {}
    if API_KEY:
        headers["x-api-key"] = API_KEY
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _api_post(path: str, body: dict | None = None) -> dict:
    """POST request to ILUMINATY API."""
    url = API_BASE + path
    headers = {}
    if API_KEY:
        headers["x-api-key"] = API_KEY
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, method="POST", data=data, headers=headers)
    else:
        req = urllib.request.Request(url, method="POST", data=b"", headers=headers)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


# ─── Token Mode (default: cheapest) ───
VISION_MODE = os.environ.get("ILUMINATY_VISION_MODE", "text_only")

# ─── MCP Tool Definitions ───

TOOLS = [
    {
        "name": "see_screen",
        "description": (
            "See what is currently on the user's screen. "
            "Uses smart token mode to control costs. Modes: "
            "text_only (~200 tokens), low_res (~5K), medium_res (~15K), full_res (~30K). "
            "Default is text_only. Use text_only for most tasks, only use image modes "
            "when you truly need to SEE the screen layout or colors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["text_only", "low_res", "medium_res", "full_res"],
                    "description": "Vision mode. text_only=cheapest, full_res=expensive. Default: text_only",
                    "default": "text_only",
                },
            },
        },
    },
    {
        "name": "token_status",
        "description": (
            "Check current token usage, budget, and mode. "
            "Use this to monitor how many tokens ILUMINATY vision has consumed."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_token_mode",
        "description": (
            "Set the default vision mode to control token costs. "
            "text_only (~200 tokens/call), low_res (~5K), medium_res (~15K), full_res (~30K)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["text_only", "low_res", "medium_res", "full_res"],
                    "description": "Vision mode to set as default",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "set_token_budget",
        "description": (
            "Set a token budget limit. ILUMINATY will refuse vision requests "
            "when budget is exceeded. Set to 0 for unlimited."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max tokens to spend. 0 = unlimited",
                },
            },
            "required": ["limit"],
        },
    },
    {
        "name": "see_changes",
        "description": (
            "See what changed on screen in the last N seconds. "
            "Returns multiple frames showing the progression. "
            "Use this when the user says 'what just happened' or "
            "'did you see that'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "How many seconds back to look (default 10)",
                    "default": 10,
                },
            },
        },
    },
    {
        "name": "annotate_screen",
        "description": (
            "Draw an annotation on the screen to mark an area for discussion. "
            "Types: rect (rectangle), circle, arrow, text. "
            "Use this when you want to point at something specific."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["rect", "circle", "arrow", "text"],
                    "description": "Annotation type",
                },
                "x": {"type": "integer", "description": "X position"},
                "y": {"type": "integer", "description": "Y position"},
                "width": {"type": "integer", "description": "Width", "default": 100},
                "height": {"type": "integer", "description": "Height", "default": 100},
                "color": {"type": "string", "description": "Color hex", "default": "#FF0000"},
                "text": {"type": "string", "description": "Text for text annotations", "default": ""},
            },
            "required": ["type", "x", "y"],
        },
    },
    {
        "name": "read_screen_text",
        "description": (
            "Read all visible text on the screen using OCR. "
            "Optionally read only a specific region. "
            "Use this when you need to read text that is on screen."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "region_x": {"type": "integer", "description": "Region X (optional)"},
                "region_y": {"type": "integer", "description": "Region Y (optional)"},
                "region_w": {"type": "integer", "description": "Region width (optional)"},
                "region_h": {"type": "integer", "description": "Region height (optional)"},
            },
        },
    },
    {
        "name": "screen_status",
        "description": (
            "Get ILUMINATY system status: buffer stats, capture state, "
            "memory usage, FPS, active window, workflow, audio level."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_context",
        "description": (
            "Get the user's current context: what app they're using, "
            "what workflow they're in (coding, browsing, meeting, etc.), "
            "how focused they are, and how long they've been at it. "
            "Use this to understand what the user is doing before helping them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_audio_level",
        "description": (
            "Get the current audio level and whether speech is detected. "
            "Use this to know if the user is talking or in a call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    # ─── v1.0: Computer Use Tools ───
    {
        "name": "do_action",
        "description": (
            "Execute an action using SAFE/HYBRID control loop (precheck -> execute -> verify -> recover). "
            "Use this as the default action tool for reliable operation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Natural language instruction (e.g. 'save the file', 'click Submit')",
                },
                "context_tick_id": {
                    "type": "integer",
                    "description": "Optional world tick id to enforce freshness in SAFE/HYBRID",
                },
                "max_staleness_ms": {
                    "type": "integer",
                    "description": "Optional max context age in ms (default 1500)",
                },
                "use_grounding": {
                    "type": "boolean",
                    "description": "Enable hybrid grounding for target resolution before execute",
                    "default": False,
                },
                "target_query": {
                    "type": "string",
                    "description": "Optional grounding target query (e.g. 'Save button')",
                },
                "target_role": {
                    "type": "string",
                    "description": "Optional role hint for grounding",
                },
                "monitor_id": {
                    "type": "integer",
                    "description": "Optional monitor id for grounding",
                },
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "raw_action",
        "description": (
            "Execute an action in RAW mode (0 guardrails except kill switch). "
            "Use only when the external AI handles all safety."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "Natural language instruction"},
                "action": {"type": "string", "description": "Direct action name (optional if instruction provided)"},
                "params": {"type": "object", "description": "Direct action params"},
                "verify": {"type": "boolean", "description": "Run verifier after execution", "default": False},
            },
        },
    },
    {
        "name": "action_precheck",
        "description": (
            "Validate readiness + mode + safety before taking an action. "
            "Returns whether execution would be blocked."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "Natural language instruction"},
                "action": {"type": "string", "description": "Direct action name"},
                "params": {"type": "object", "description": "Direct action params"},
                "category": {"type": "string", "description": "safe|normal|destructive", "default": "normal"},
                "mode": {"type": "string", "enum": ["SAFE", "RAW", "HYBRID"], "description": "Optional override mode"},
                "context_tick_id": {"type": "integer", "description": "Optional expected world tick id"},
                "max_staleness_ms": {"type": "integer", "description": "Optional max context age in ms"},
                "use_grounding": {"type": "boolean", "description": "Enable hybrid grounding checks", "default": False},
                "target_query": {"type": "string", "description": "Optional grounding target query"},
                "target_role": {"type": "string", "description": "Optional grounding role hint"},
                "monitor_id": {"type": "integer", "description": "Optional monitor id for grounding"},
            },
        },
    },
    {
        "name": "verify_action",
        "description": (
            "Run post-action verification for an action/params pair without executing a new action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action name to verify"},
                "params": {"type": "object", "description": "Action params used during execution"},
                "pre_state": {"type": "object", "description": "Optional captured pre-state"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "set_operating_mode",
        "description": (
            "Set ILUMINATY operating mode: SAFE (default), RAW, or HYBRID."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["SAFE", "RAW", "HYBRID"],
                    "description": "Target operating mode",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "vision_query",
        "description": (
            "Ask a temporal visual question over IPA memory. "
            "Supports point-in-time (`at_ms`) or windowed (`window_seconds`) reasoning."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Natural language visual question"},
                "at_ms": {"type": "integer", "description": "Optional target timestamp in ms"},
                "window_seconds": {"type": "number", "description": "Lookback window in seconds", "default": 30},
                "monitor_id": {"type": "integer", "description": "Optional monitor id"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "grounding_status",
        "description": (
            "Get hybrid grounding engine status and performance metrics."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "grounding_resolve",
        "description": (
            "Resolve an actionable UI target using hybrid grounding (UI tree + OCR + visual hints). "
            "Returns ranked candidates with confidence and selected target."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Target query (e.g. 'Save button')"},
                "role": {"type": "string", "description": "Optional role hint (button, textfield, etc.)"},
                "monitor_id": {"type": "integer", "description": "Optional monitor id"},
                "mode": {"type": "string", "enum": ["SAFE", "RAW", "HYBRID"], "default": "SAFE"},
                "category": {"type": "string", "description": "safe|normal|destructive", "default": "normal"},
                "top_k": {"type": "integer", "description": "Max candidates", "default": 5},
                "context_tick_id": {"type": "integer", "description": "Optional expected world tick id"},
                "max_staleness_ms": {"type": "integer", "description": "Optional context max age in ms"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "click_grounded",
        "description": (
            "Resolve a target via grounding and click it with SAFE/HYBRID/RAW policy."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to click"},
                "role": {"type": "string", "description": "Optional role hint"},
                "monitor_id": {"type": "integer", "description": "Optional monitor id"},
                "button": {"type": "string", "description": "Mouse button", "default": "left"},
                "mode": {"type": "string", "enum": ["SAFE", "RAW", "HYBRID"], "default": "SAFE"},
                "category": {"type": "string", "description": "safe|normal|destructive", "default": "normal"},
                "verify": {"type": "boolean", "default": True},
                "context_tick_id": {"type": "integer"},
                "max_staleness_ms": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "type_grounded",
        "description": (
            "Resolve a text field via grounding, focus it, and type text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Field target query"},
                "text": {"type": "string", "description": "Text to type"},
                "role": {"type": "string", "description": "Optional role hint", "default": "textfield"},
                "monitor_id": {"type": "integer", "description": "Optional monitor id"},
                "mode": {"type": "string", "enum": ["SAFE", "RAW", "HYBRID"], "default": "SAFE"},
                "category": {"type": "string", "description": "safe|normal|destructive", "default": "normal"},
                "verify": {"type": "boolean", "default": True},
                "context_tick_id": {"type": "integer"},
                "max_staleness_ms": {"type": "integer"},
            },
            "required": ["query", "text"],
        },
    },
    {
        "name": "click_element",
        "description": (
            "Click on a UI element by name using the accessibility tree. "
            "No coordinates needed - finds the element automatically. "
            "Example: click_element('Save') clicks the Save button."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Element name or label"},
                "role": {"type": "string", "description": "Element role (button, textfield, etc)", "default": ""},
            },
            "required": ["name"],
        },
    },
    {
        "name": "type_text",
        "description": (
            "Type text using the keyboard. Supports unicode. "
            "The text is typed at the current cursor position."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Execute a shell command and return the output. "
            "Example: 'npm test', 'python script.py', 'git status'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "number", "description": "Timeout in seconds (default 30)", "default": 30},
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_windows",
        "description": (
            "List all visible windows on the desktop with their titles, positions, and sizes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "window_minimize",
        "description": "Minimize a window by title.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title (partial match accepted by backend)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "window_maximize",
        "description": "Maximize a window by title.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title (partial match accepted by backend)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "window_close",
        "description": "Close a window by title.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title (partial match accepted by backend)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "find_ui_element",
        "description": (
            "Find a UI element on screen using the accessibility tree. "
            "Returns element info including position, size, and state. "
            "Use this before clicking to verify the element exists."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Element name to search for"},
                "role": {"type": "string", "description": "Element role filter (button, textfield, etc)", "default": ""},
            },
            "required": ["name"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file from the filesystem (sandboxed). "
            "Returns the file content as text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file (sandboxed, auto-backup). "
            "Creates parent directories if needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "get_clipboard",
        "description": "Read the current clipboard content.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "agent_status",
        "description": (
            "Get the full agent status: actions enabled, safety state, "
            "autonomy level, available capabilities, and recent action log."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "focus_window",
        "description": (
            "Switch to a window by title (partial match). Like a human clicking on a window. "
            "Example: focus_window('Chrome') switches to Chrome. "
            "Use list_windows first to see available windows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title (partial match)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "browser_navigate",
        "description": (
            "Navigate the browser to a URL. Opens the URL in the active browser window. "
            "Example: browser_navigate('https://github.com')"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_tabs",
        "description": "List all open browser tabs with their titles and URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "click_screen",
        "description": (
            "Click at a specific position on screen using REAL screen coordinates (not image coordinates). "
            "For multi-monitor setups, coordinates span the full virtual desktop. "
            "Use list_windows to see window positions and calculate where to click."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate (real screen pixels)"},
                "y": {"type": "integer", "description": "Y coordinate (real screen pixels)"},
                "button": {"type": "string", "description": "Mouse button: left, right, middle", "default": "left"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "keyboard",
        "description": (
            "Press keyboard keys or shortcuts. Like a human pressing keys. "
            "Examples: 'enter', 'tab', 'ctrl+s', 'ctrl+shift+t', 'alt+tab', 'ctrl+l', 'f5'. "
            "For typing text use type_text instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "Key or key combo (e.g. 'ctrl+s', 'enter', 'alt+tab')"},
            },
            "required": ["keys"],
        },
    },
    {
        "name": "scroll",
        "description": (
            "Scroll in the active window. Positive = down, negative = up. "
            "Like a human using the mouse wheel."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Scroll amount. Positive=down, negative=up. Default=3", "default": 3},
            },
        },
    },
    {
        "name": "perception",
        "description": (
            "Get real-time perception of what's happening on screen — like having eyes that never blink. "
            "Instead of taking a screenshot (1 frozen moment), this returns a STREAM of events that the "
            "perception engine detected continuously: window switches, page loads, text changes, motion, "
            "loading spinners, content stabilization. Costs ~200 tokens (text only, no images). "
            "Use this FIRST before any action to understand current state. "
            "Use this AFTER any action to see what happened without needing a screenshot."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "How many seconds back to look (default 30)",
                    "default": 30,
                },
            },
        },
    },
    {
        "name": "perception_world",
        "description": (
            "Get IPA v2 semantic WorldState snapshot (task phase, affordances, uncertainty, readiness)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "perception_trace",
        "description": (
            "Get compressed semantic transitions from RAM-only episodic trace."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "Trace window in seconds (default 90)",
                    "default": 90,
                },
            },
        },
    },
    {
        "name": "watch_screen",
        "description": (
            "See the last N frames as a sequence — like watching a video replay. "
            "Use this instead of see_screen when you need to understand what JUST happened: "
            "animations, loading spinners, transitions, popups appearing/disappearing. "
            "Returns the most recent frames with images so you see the flow, not just a snapshot. "
            "Default: last 3 frames (~0.6s at 5 FPS). Max: 5 frames."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "frames": {
                    "type": "integer",
                    "description": "Number of recent frames to return (1-5, default 3)",
                    "default": 3,
                },
                "monitor": {
                    "type": "integer",
                    "description": "Specific monitor (1,2,3). Omit for all monitors.",
                },
            },
        },
    },
    {
        "name": "monitor_info",
        "description": (
            "Get information about all connected monitors: positions, sizes, and layout. "
            "Essential for understanding the multi-monitor setup before interacting."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "see_monitor",
        "description": (
            "Capture a specific monitor (1, 2, 3...) instead of all monitors combined. "
            "This gives much better resolution for reading content on that monitor. "
            "Use monitor_info first to know which monitor number to target."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "monitor": {"type": "integer", "description": "Monitor number (1=primary, 2, 3...)"},
                "mode": {"type": "string", "enum": ["low_res", "medium_res", "full_res"], "default": "medium_res"},
            },
            "required": ["monitor"],
        },
    },
]


# ─── Tool Handlers ───

def handle_see_screen(args: dict) -> dict:
    mode = args.get("mode", VISION_MODE)
    data = _api_get(f"/vision/smart?mode={mode}")

    if data.get("error") == "token_budget_exceeded":
        return [{"type": "text", "text": (
            f"TOKEN BUDGET EXCEEDED. Used: {data['used']}/{data['budget']}. "
            f"Switch to text_only mode or increase budget with set_token_budget."
        )}]

    tokens_info = f"\n\n---\n[Token mode: {mode} | ~{data.get('token_estimate', '?')} tokens | Total used: {data.get('tokens_used_total', '?')}]"

    # text_only mode: just return text
    if mode == "text_only" or "image_base64" not in data:
        text = data.get("ai_prompt", data.get("ocr_text", "No screen data"))
        if data.get("ocr_text"):
            text += f"\n\n### OCR Text\n{data['ocr_text']}"
        return [{"type": "text", "text": text + tokens_info}]

    # Image modes: return image + text
    return [
        {"type": "text", "text": data.get("ai_prompt", "") + tokens_info},
        {
            "type": "image",
            "data": data["image_base64"],
            "mimeType": "image/webp",
        },
    ]


def handle_perception(args: dict) -> list:
    """Get real-time perception events — the AI's visual cortex."""
    seconds = args.get("seconds", 30)
    try:
        data = _api_get(f"/perception?seconds={seconds}")
        summary = data.get("summary", "Perception engine not available")
        event_count = data.get("event_count", 0)
        running = data.get("running", False)

        status = "ACTIVE" if running else "OFFLINE"
        return [{"type": "text", "text": f"[Perception: {status} | {event_count} events buffered]\n\n{summary}"}]
    except Exception as e:
        return [{"type": "text", "text": f"Perception not available: {e}. Use see_screen as fallback."}]


def handle_perception_world(args: dict) -> list:
    try:
        world = _api_get("/perception/world")
        lines = [
            "## IPA WorldState",
            f"- Tick: {world.get('tick_id', 0)}",
            f"- Phase: {world.get('task_phase', 'unknown')}",
            f"- Surface: {world.get('active_surface', 'unknown')}",
            f"- Readiness: {world.get('readiness', False)}",
            f"- Uncertainty: {world.get('uncertainty', 1.0)}",
            f"- Staleness: {world.get('staleness_ms', 0)}ms",
            f"- Risk mode: {world.get('risk_mode', 'safe')}",
        ]
        entities = world.get("entities", [])
        if entities:
            lines.append(f"- Entities: {', '.join(entities[:8])}")
        affordances = world.get("affordances", [])
        if affordances:
            lines.append(f"- Affordances: {', '.join(affordances[:8])}")
        visual_facts = world.get("visual_facts", [])
        if visual_facts:
            lines.append(f"- Visual facts: {len(visual_facts)}")
        return [{"type": "text", "text": "\n".join(lines)}]
    except Exception as e:
        return [{"type": "text", "text": f"WorldState not available: {e}"}]


def handle_perception_trace(args: dict) -> list:
    seconds = args.get("seconds", 90)
    try:
        data = _api_get(f"/perception/trace?seconds={seconds}")
        trace = data.get("trace", [])
        temporal = data.get("temporal", {})
        frame_refs = temporal.get("frame_refs", [])
        if not trace:
            return [{"type": "text", "text": "No semantic trace entries in the requested window."}]
        lines = [f"## IPA Trace ({len(trace)} entries / {seconds}s, frame_refs={len(frame_refs)})"]
        for item in trace[-20:]:
            ts = item.get("timestamp_ms", 0)
            summary = item.get("summary", "")
            reason = item.get("boundary_reason", "")
            lines.append(f"- [{ts}] {summary} ({reason})")
        return [{"type": "text", "text": "\n".join(lines)}]
    except Exception as e:
        return [{"type": "text", "text": f"Trace not available: {e}"}]


def handle_vision_query(args: dict) -> list:
    question = (args.get("question") or "").strip()
    if not question:
        return [{"type": "text", "text": "Error: question is required"}]
    payload = {"question": question}
    for key in ("at_ms", "window_seconds", "monitor_id"):
        if key in args and args[key] is not None:
            payload[key] = args[key]
    try:
        data = _api_post("/perception/query", body=payload)
    except Exception as e:
        return [{"type": "text", "text": f"Vision query failed: {e}"}]
    refs = data.get("evidence_refs", [])
    frames = data.get("frame_refs", [])
    lines = [
        "## Vision Query",
        f"Q: {question}",
        f"A: {data.get('answer', '')}",
        f"Confidence: {data.get('confidence', 0)}",
    ]
    if refs:
        lines.append(f"Evidence refs: {', '.join(refs[:6])}")
    if frames:
        lines.append(f"Frame refs: {len(frames)}")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_grounding_status(args: dict) -> list:
    try:
        data = _api_get("/grounding/status")
        stats = data.get("stats", {})
        lines = [
            "## Grounding Status",
            f"Mode: {data.get('mode', 'hybrid_ui_text')}",
            f"Profile: {data.get('profile', 'balanced')}",
            f"Resolves: {stats.get('resolves', 0)}",
            f"Success rate: {stats.get('success_rate_pct', 0)}%",
            f"Blocked rate: {stats.get('blocked_rate_pct', 0)}%",
            f"Avg latency: {stats.get('avg_latency_ms', 0)}ms",
            f"Last reason: {stats.get('last_reason', 'n/a')}",
        ]
        return [{"type": "text", "text": "\n".join(lines)}]
    except Exception as e:
        return [{"type": "text", "text": f"Grounding status unavailable: {e}"}]


def handle_grounding_resolve(args: dict) -> list:
    query = (args.get("query") or "").strip()
    if not query:
        return [{"type": "text", "text": "Error: query is required"}]
    body = {
        "query": query,
        "role": args.get("role"),
        "monitor_id": args.get("monitor_id"),
        "mode": args.get("mode", "SAFE"),
        "category": args.get("category", "normal"),
        "top_k": args.get("top_k", 5),
        "context_tick_id": args.get("context_tick_id"),
        "max_staleness_ms": args.get("max_staleness_ms"),
    }
    try:
        data = _api_post("/grounding/resolve", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Grounding resolve failed: {e}"}]

    target = data.get("target")
    lines = [
        "## Grounding Resolve",
        f"Query: {query}",
        f"Success: {data.get('success', False)}",
        f"Blocked: {data.get('blocked', False)}",
        f"Reason: {data.get('reason', 'unknown')}",
    ]
    if target:
        lines.append(
            f"Target: {target.get('name', '')} ({target.get('role', '')}) "
            f"at {target.get('center_xy')} conf={target.get('confidence', 0)}"
        )
    lines.append(f"Candidates: {len(data.get('candidates', []))}")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_click_grounded(args: dict) -> list:
    query = (args.get("query") or "").strip()
    if not query:
        return [{"type": "text", "text": "Error: query is required"}]
    body = {
        "query": query,
        "role": args.get("role"),
        "monitor_id": args.get("monitor_id"),
        "button": args.get("button", "left"),
        "mode": args.get("mode", "SAFE"),
        "category": args.get("category", "normal"),
        "verify": bool(args.get("verify", True)),
        "context_tick_id": args.get("context_tick_id"),
        "max_staleness_ms": args.get("max_staleness_ms"),
        "top_k": args.get("top_k", 5),
    }
    try:
        data = _api_post("/grounding/click", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Grounded click failed: {e}"}]
    lines = [
        "## Grounded Click",
        f"Query: {query}",
        f"Success: {data.get('success', False)}",
    ]
    grd = data.get("grounding", {})
    lines.append(f"Grounding reason: {grd.get('reason', 'n/a')}")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_type_grounded(args: dict) -> list:
    query = (args.get("query") or "").strip()
    text = args.get("text")
    if not query:
        return [{"type": "text", "text": "Error: query is required"}]
    if text is None:
        return [{"type": "text", "text": "Error: text is required"}]
    body = {
        "query": query,
        "text": str(text),
        "role": args.get("role", "textfield"),
        "monitor_id": args.get("monitor_id"),
        "mode": args.get("mode", "SAFE"),
        "category": args.get("category", "normal"),
        "verify": bool(args.get("verify", True)),
        "context_tick_id": args.get("context_tick_id"),
        "max_staleness_ms": args.get("max_staleness_ms"),
        "top_k": args.get("top_k", 5),
    }
    try:
        data = _api_post("/grounding/type", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Grounded type failed: {e}"}]
    lines = [
        "## Grounded Type",
        f"Query: {query}",
        f"Chars: {len(str(text))}",
        f"Success: {data.get('success', False)}",
    ]
    grd = data.get("grounding", {})
    lines.append(f"Grounding reason: {grd.get('reason', 'n/a')}")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_watch_screen(args: dict) -> list:
    """Return last N frames as images — like watching a video replay."""
    n = min(args.get("frames", 3), 5)
    monitor = args.get("monitor")

    data = _api_get(f"/frames?last={n}&include_images=true")
    frames = data.get("frames", [])

    if not frames:
        return [{"type": "text", "text": "No frames in buffer yet."}]

    result = []
    result.append({
        "type": "text",
        "text": f"## Screen Replay ({len(frames)} frames)\nOldest → Newest. Watch for changes between frames.",
    })

    for i, f in enumerate(frames):
        ts = f.get("timestamp_iso", "?")
        change = f.get("change_score", 0)
        img_b64 = f.get("image_base64")

        label = f"**Frame {i+1}/{len(frames)}** — {ts} | change: {change}"

        if img_b64:
            # If monitor specified, we'd need per-monitor crop — for now send full frame
            result.append({"type": "text", "text": label})
            result.append({
                "type": "image",
                "data": img_b64,
                "mimeType": f.get("mime_type", "image/webp"),
            })
        else:
            result.append({"type": "text", "text": label + " (no image data)"})

    return result


def handle_token_status(args: dict) -> dict:
    data = _api_get("/tokens/status")
    lines = [
        "## ILUMINATY Token Status",
        f"**Mode**: {data['mode']} (~{data['mode_cost']['tokens']} tokens/call)",
        f"**Used**: {data['used']} tokens",
        f"**Budget**: {'unlimited' if data['budget'] == 0 else data['budget']}",
        f"**Remaining**: {'unlimited' if data['remaining'] == -1 else data['remaining']}",
        "",
        "### Available Modes",
    ]
    for name, info in data["all_modes"].items():
        marker = " <<<" if name == data["mode"] else ""
        lines.append(f"  - **{name}**: ~{info['tokens']} tokens — {info['desc']}{marker}")
    if data["last_5"]:
        lines.append("\n### Recent Usage")
        for entry in data["last_5"]:
            lines.append(f"  - {entry['action']}: {entry['tokens']} tokens")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_set_token_mode(args: dict) -> dict:
    mode = args.get("mode", "text_only")
    data = _api_post(f"/tokens/mode?mode={urllib.parse.quote(str(mode))}")
    return [{"type": "text", "text": f"Vision mode set to **{data['mode']}** (~{data['estimated_tokens_per_call']} tokens/call)"}]


def handle_set_token_budget(args: dict) -> dict:
    limit = args.get("limit", 0)
    data = _api_post(f"/tokens/budget?limit={limit}")
    return [{"type": "text", "text": f"Token budget: {data['budget']} | Used: {data['used']} | Remaining: {'unlimited' if data['budget']==0 else data['remaining']}"}]


def handle_see_changes(args: dict) -> list:
    seconds = args.get("seconds", 10)
    # Get frames WITH images to actually see what changed
    data = _api_get(f"/frames?seconds={seconds}&include_images=true")
    count = data.get("count", 0)
    frames = data.get("frames", [])

    if not frames:
        return [{"type": "text", "text": f"No changes in the last {seconds}s."}]

    # Only show frames with significant changes (change_score > 0.01) + first and last
    significant = []
    for i, f in enumerate(frames):
        if i == 0 or i == len(frames) - 1 or f.get("change_score", 0) > 0.01:
            significant.append(f)

    # Cap at 5 frames to avoid token explosion
    if len(significant) > 5:
        step = len(significant) / 5
        significant = [significant[int(i * step)] for i in range(5)]

    result = [{"type": "text", "text": f"## Screen Changes (last {seconds}s) — {count} total frames, showing {len(significant)} key frames"}]

    for i, f in enumerate(significant):
        ts = f.get("timestamp_iso", "?")
        change = f.get("change_score", 0)
        img_b64 = f.get("image_base64")

        result.append({"type": "text", "text": f"**Frame {i+1}** — {ts} | change: {change:.3f}"})
        if img_b64:
            result.append({
                "type": "image",
                "data": img_b64,
                "mimeType": f.get("mime_type", "image/webp"),
            })

    return result


def handle_annotate(args: dict) -> dict:
    ann_type = args.get("type", "rect")
    x = args.get("x", 0)
    y = args.get("y", 0)
    w = args.get("width", 100)
    h = args.get("height", 100)
    color = args.get("color", "#FF0000")
    text = args.get("text", "")

    params = f"type={ann_type}&x={x}&y={y}&width={w}&height={h}&color={color}&text={text}"
    data = _api_post(f"/annotations/add?{params}")

    return [{"type": "text", "text": f"Annotation added: {ann_type} at ({x},{y}) id={data.get('id', '?')}"}]


def handle_read_text(args: dict) -> dict:
    params = []
    for key in ["region_x", "region_y", "region_w", "region_h"]:
        if key in args and args[key] is not None:
            params.append(f"{key}={args[key]}")

    query = "&".join(params) if params else ""
    path = f"/vision/ocr?{query}" if query else "/vision/ocr"
    data = _api_get(path)

    text = data.get("text", "")
    if not text:
        return [{"type": "text", "text": "No readable text found on screen. OCR may not be available (install tesseract)."}]

    return [{"type": "text", "text": f"## Screen Text (OCR)\n```\n{text[:3000]}\n```\nConfidence: {data.get('confidence', 0)}%"}]


def handle_status(args: dict) -> dict:
    stats = _api_get("/buffer/stats")
    window = _api_get("/vision/window")

    lines = [
        "## ILUMINATY Status",
        f"- Capture: {'running' if stats.get('capture_running') else 'stopped'}",
        f"- Buffer: {stats.get('slots_used')}/{stats.get('slots_max')} slots",
        f"- RAM: {stats.get('memory_mb')} MB",
        f"- FPS: {stats.get('current_fps')}",
        f"- Frames captured: {stats.get('total_frames_captured')}",
        f"- Frames dropped (no change): {stats.get('frames_dropped_no_change')}",
        f"- Active window: {window.get('title', 'unknown')}",
    ]
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_context(args: dict) -> dict:
    try:
        ctx = _api_get("/context/state")
        lines = [
            "## User Context",
            f"- Workflow: **{ctx.get('workflow', 'unknown')}** (confidence: {ctx.get('confidence', 0)})",
            f"- App: {ctx.get('app', 'unknown')}",
            f"- Window: {ctx.get('title', '')[:80]}",
            f"- Focus: {'HIGH' if ctx.get('is_focused') else 'LOW'} ({ctx.get('switches_5min', 0)} app switches in 5min)",
            f"- Time in workflow: {ctx.get('time_in_workflow_seconds', 0):.0f}s",
            "",
            ctx.get("summary", ""),
        ]
        return [{"type": "text", "text": "\n".join(lines)}]
    except Exception:
        return [{"type": "text", "text": "Context engine not available."}]


def handle_audio_level(args: dict) -> dict:
    try:
        level = _api_get("/audio/level")
        speech = "YES - user is speaking" if level.get("is_speech") else "No speech detected"
        return [{"type": "text", "text": f"Audio level: {level.get('level', 0):.3f} | Speech: {speech}"}]
    except Exception:
        return [{"type": "text", "text": "Audio not enabled. Start with --audio mic"}]


# ─── v1.0: Computer Use Handlers ───

def handle_do_action(args: dict) -> list:
    instruction = args.get("instruction", "")
    if not instruction:
        return [{"type": "text", "text": "Error: instruction is required"}]
    payload = {"instruction": instruction, "verify": True}
    for key in (
        "context_tick_id",
        "max_staleness_ms",
        "use_grounding",
        "target_query",
        "target_role",
        "monitor_id",
    ):
        if key in args and args[key] is not None:
            payload[key] = args[key]
    data = _api_post("/action/execute", body=payload)
    result = data.get("result", {})
    precheck = data.get("precheck", {})
    verification = data.get("verification") or {}
    lines = [
        f"Mode: {precheck.get('mode', '?')} | blocked: {precheck.get('blocked', False)}",
        f"Action: {'SUCCESS' if result.get('success') else 'FAILED'} - {result.get('message', '')}",
    ]
    grounding = precheck.get("grounding_check") or {}
    if precheck.get("grounding_applies"):
        lines.append(
            f"Grounding: {'OK' if grounding.get('allowed') else 'BLOCKED'} "
            f"reason={grounding.get('reason', 'n/a')} conf={grounding.get('confidence', 0)}"
        )
    if verification:
        lines.append(
            f"Verification: {'OK' if verification.get('verified') else 'FAILED'} "
            f"({verification.get('method', 'n/a')})"
        )
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_raw_action(args: dict) -> list:
    payload = {}
    if args.get("instruction"):
        payload["instruction"] = args.get("instruction")
    if args.get("action"):
        payload["action"] = args.get("action")
    if args.get("params") is not None:
        payload["params"] = args.get("params")
    payload["verify"] = bool(args.get("verify", False))
    if not payload:
        return [{"type": "text", "text": "Error: provide instruction or action"}]

    data = _api_post("/action/raw", body=payload)
    result = data.get("result", {})
    return [{
        "type": "text",
        "text": (
            f"RAW action: {'SUCCESS' if result.get('success') else 'FAILED'} - "
            f"{result.get('message', '')}"
        ),
    }]


def handle_action_precheck(args: dict) -> list:
    payload = {}
    for key in (
        "instruction",
        "action",
        "params",
        "category",
        "mode",
        "context_tick_id",
        "max_staleness_ms",
        "use_grounding",
        "target_query",
        "target_role",
        "monitor_id",
    ):
        if key in args and args[key] is not None:
            payload[key] = args[key]
    if not payload:
        return [{"type": "text", "text": "Error: provide instruction or action"}]

    data = _api_post("/action/precheck", body=payload)
    readiness = data.get("readiness", {})
    safety = data.get("safety_check", {})
    lines = [
        f"Mode: {data.get('mode', '?')} | blocked: {data.get('blocked', False)}",
        f"Readiness: {readiness.get('readiness')} | uncertainty: {readiness.get('uncertainty')}",
        f"Safety: {safety.get('reason', 'n/a')} (applies={data.get('safety_applies')})",
    ]
    if data.get("grounding_applies"):
        grd = data.get("grounding_check", {})
        lines.append(
            f"Grounding: {'OK' if grd.get('allowed') else 'BLOCKED'} "
            f"reason={grd.get('reason', 'n/a')} conf={grd.get('confidence', 0)}"
        )
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_verify_action(args: dict) -> list:
    action = args.get("action", "")
    if not action:
        return [{"type": "text", "text": "Error: action is required"}]
    data = _api_post("/action/verify", body={
        "action": action,
        "params": args.get("params", {}),
        "pre_state": args.get("pre_state"),
    })
    return [{
        "type": "text",
        "text": (
            f"Verify {action}: {'OK' if data.get('verified') else 'FAILED'} - "
            f"{data.get('message', '')}"
        ),
    }]


def handle_set_operating_mode(args: dict) -> list:
    mode = args.get("mode", "SAFE")
    data = _api_post(f"/operating/mode?mode={urllib.parse.quote(str(mode))}")
    return [{"type": "text", "text": f"Operating mode set to {data.get('mode', 'SAFE')}"}]


def handle_click_element(args: dict) -> list:
    name = args.get("name", "")
    role = args.get("role", "")
    query = f"name={urllib.parse.quote(name)}"
    if role:
        query += f"&role={urllib.parse.quote(role)}"
    data = _api_post(f"/ui/click?{query}")
    return [{"type": "text", "text": f"Click element '{name}': {'SUCCESS' if data.get('success') else 'FAILED'} - {data.get('message', '')}"}]


def handle_type_text(args: dict) -> list:
    text = args.get("text", "")
    data = _api_post(f"/action/type?text={urllib.parse.quote(text)}")
    return [{"type": "text", "text": f"Typed {len(text)} chars: {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_run_command(args: dict) -> list:
    cmd = args.get("command", "")
    timeout = args.get("timeout", 30)
    data = _api_post(f"/terminal/exec?cmd={urllib.parse.quote(cmd)}&timeout={timeout}")
    lines = [
        f"## Command: `{cmd}`",
        f"**Status**: {'SUCCESS' if data.get('success') else 'FAILED'} (exit: {data.get('return_code', '?')}, {data.get('duration_ms', 0):.0f}ms)",
    ]
    stdout = data.get("stdout", "")
    stderr = data.get("stderr", "")
    if stdout:
        lines.append(f"\n**stdout**:\n```\n{stdout[:3000]}\n```")
    if stderr:
        lines.append(f"\n**stderr**:\n```\n{stderr[:1000]}\n```")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_list_windows(args: dict) -> list:
    data = _api_get("/windows/list")
    windows = data.get("windows", [])
    lines = [f"## Windows ({len(windows)})"]
    for w in windows[:30]:
        lines.append(f"- **{w.get('title', '?')[:60]}** (pid:{w.get('pid')}, {w.get('width')}x{w.get('height')})")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_window_minimize(args: dict) -> list:
    title = (args.get("title") or "").strip()
    if not title:
        return [{"type": "text", "text": "Error: title is required"}]
    data = _api_post(f"/windows/minimize?title={urllib.parse.quote(title)}")
    return [{"type": "text", "text": f"Minimize '{title}': {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_window_maximize(args: dict) -> list:
    title = (args.get("title") or "").strip()
    if not title:
        return [{"type": "text", "text": "Error: title is required"}]
    data = _api_post(f"/windows/maximize?title={urllib.parse.quote(title)}")
    return [{"type": "text", "text": f"Maximize '{title}': {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_window_close(args: dict) -> list:
    title = (args.get("title") or "").strip()
    if not title:
        return [{"type": "text", "text": "Error: title is required"}]
    data = _api_post(f"/windows/close?title={urllib.parse.quote(title)}")
    return [{"type": "text", "text": f"Close '{title}': {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_find_ui_element(args: dict) -> list:
    name = args.get("name", "")
    role = args.get("role", "")
    query = f"name={urllib.parse.quote(name)}"
    if role:
        query += f"&role={urllib.parse.quote(role)}"
    data = _api_get(f"/ui/find?{query}")
    el = data.get("element")
    if el:
        return [{"type": "text", "text": f"Found: **{el.get('name')}** ({el.get('role')}) at ({el.get('x')},{el.get('y')}) {el.get('width')}x{el.get('height')} enabled={el.get('is_enabled')}"}]
    return [{"type": "text", "text": f"Element '{name}' not found on screen."}]


def handle_read_file(args: dict) -> list:
    path = args.get("path", "")
    data = _api_get(f"/files/read?path={urllib.parse.quote(path)}")
    if data.get("success"):
        content = data.get("content", "")
        return [{"type": "text", "text": f"## {path} ({data.get('lines', 0)} lines, {data.get('size', 0)}B)\n```\n{content[:5000]}\n```"}]
    return [{"type": "text", "text": f"Failed to read {path}: {data.get('error', 'unknown')}"}]


def handle_write_file(args: dict) -> list:
    path = args.get("path", "")
    content = args.get("content", "")
    data = _api_post(f"/files/write?path={urllib.parse.quote(path)}", body={"content": content})
    if data.get("success"):
        return [{"type": "text", "text": f"Written {data.get('size', 0)}B to {path}"}]
    return [{"type": "text", "text": f"Failed to write {path}: {data.get('error', 'unknown')}"}]


def handle_get_clipboard(args: dict) -> list:
    data = _api_get("/clipboard/read")
    text = data.get("text", "")
    return [{"type": "text", "text": f"Clipboard ({len(text)} chars):\n```\n{text[:2000]}\n```" if text else "Clipboard is empty."}]


def handle_agent_status(args: dict) -> list:
    data = _api_get("/agent/status")
    lines = ["## Agent Status"]
    for section, info in data.items():
        if isinstance(info, dict):
            lines.append(f"\n**{section}**:")
            for k, v in list(info.items())[:10]:
                lines.append(f"  - {k}: {v}")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_focus_window(args: dict) -> list:
    title = args.get("title", "")
    if not title:
        return [{"type": "text", "text": "Error: title is required"}]
    # Try exact match first, then partial
    data = _api_post(f"/windows/focus?title={urllib.parse.quote(title)}")
    if data.get("success"):
        return [{"type": "text", "text": f"Focused window: '{title}'"}]
    # Try partial match via windows list
    windows = _api_get("/windows/list")
    if windows and "windows" in windows:
        for w in windows["windows"]:
            if title.lower() in w.get("title", "").lower():
                # Use hotkey approach - cycle alt+tab
                # Or try direct focus by setting foreground
                data2 = _api_post(f"/windows/focus?title={urllib.parse.quote(w['title'])}")
                if data2.get("success"):
                    return [{"type": "text", "text": f"Focused window: '{w['title']}'"}]
    return [{"type": "text", "text": f"Could not focus window '{title}'. Use list_windows to see available windows."}]


def handle_browser_navigate(args: dict) -> list:
    url = args.get("url", "")
    if not url:
        return [{"type": "text", "text": "Error: url is required"}]
    data = _api_post(f"/browser/navigate?url={urllib.parse.quote(url)}")
    if data.get("success") or data.get("status") == "ok":
        return [{"type": "text", "text": f"Navigated to: {url}"}]
    # Fallback: use keyboard to navigate
    _api_post("/action/hotkey?keys=ctrl%2Bl")
    import time; time.sleep(0.3)
    _api_post(f"/action/type?text={urllib.parse.quote(url)}")
    time.sleep(0.2)
    _api_post("/action/hotkey?keys=enter")
    return [{"type": "text", "text": f"Navigated to: {url} (via keyboard)"}]


def handle_browser_tabs(args: dict) -> list:
    data = _api_get("/browser/tabs")
    if data and isinstance(data, list):
        lines = ["## Open Browser Tabs"]
        for i, tab in enumerate(data):
            lines.append(f"{i+1}. **{tab.get('title', '?')}** — {tab.get('url', '?')}")
        return [{"type": "text", "text": "\n".join(lines)}]
    elif data and "tabs" in data:
        lines = ["## Open Browser Tabs"]
        for i, tab in enumerate(data["tabs"]):
            lines.append(f"{i+1}. **{tab.get('title', '?')}** — {tab.get('url', '?')}")
        return [{"type": "text", "text": "\n".join(lines)}]
    return [{"type": "text", "text": "Could not get browser tabs. Is Chrome running with --remote-debugging-port=9222?"}]


def handle_click_screen(args: dict) -> list:
    x = args.get("x", 0)
    y = args.get("y", 0)
    button = args.get("button", "left")
    data = _api_post(f"/action/click?x={x}&y={y}&button={button}")
    return [{"type": "text", "text": f"Clicked at ({x},{y}) {button}: {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_keyboard(args: dict) -> list:
    keys = args.get("keys", "")
    if not keys:
        return [{"type": "text", "text": "Error: keys is required"}]
    data = _api_post(f"/action/hotkey?keys={urllib.parse.quote(keys)}")
    return [{"type": "text", "text": f"Pressed {keys}: {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_scroll(args: dict) -> list:
    amount = args.get("amount", 3)
    data = _api_post(f"/action/scroll?amount={amount}")
    direction = "down" if amount > 0 else "up"
    return [{"type": "text", "text": f"Scrolled {direction} ({abs(amount)}): {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_monitor_info(args: dict) -> list:
    try:
        import mss
        with mss.mss() as sct:
            lines = ["## Monitor Layout"]
            for i, m in enumerate(sct.monitors):
                if i == 0:
                    lines.append(f"- **Virtual Desktop**: {m['width']}x{m['height']} at ({m['left']},{m['top']})")
                else:
                    lines.append(f"- **Monitor {i}**: {m['width']}x{m['height']} at ({m['left']},{m['top']})")
            # Also show which windows are on which monitor
            windows = _api_get("/windows/list")
            if windows and "windows" in windows:
                lines.append("\n## Windows by Monitor")
                for w in windows["windows"]:
                    if not w.get("is_visible") or w.get("is_minimized"):
                        continue
                    wx = w.get("x", 0)
                    wy = w.get("y", 0)
                    mon = "?"
                    for j, m in enumerate(sct.monitors):
                        if j == 0:
                            continue
                        if m["left"] <= wx < m["left"] + m["width"] and m["top"] <= wy < m["top"] + m["height"]:
                            mon = str(j)
                            break
                    lines.append(f"  - Monitor {mon}: **{w['title'][:50]}** at ({wx},{wy}) {w.get('width',0)}x{w.get('height',0)}")
            return [{"type": "text", "text": "\n".join(lines)}]
    except Exception as e:
        return [{"type": "text", "text": f"Error getting monitor info: {e}"}]


def handle_see_monitor(args: dict) -> list:
    monitor = args.get("monitor", 1)
    mode = args.get("mode", "medium_res")
    try:
        import mss, io, base64
        from PIL import Image
        with mss.mss() as sct:
            if monitor >= len(sct.monitors):
                return [{"type": "text", "text": f"Monitor {monitor} not found. Max: {len(sct.monitors)-1}"}]
            mon = sct.monitors[monitor]
            img = sct.grab(mon)
            pil_img = Image.frombytes("RGB", (img.width, img.height), img.rgb)

            # Resize based on mode
            max_w = {"low_res": 640, "medium_res": 1280, "full_res": 1920}.get(mode, 1280)
            if pil_img.width > max_w:
                ratio = max_w / pil_img.width
                pil_img = pil_img.resize((max_w, int(pil_img.height * ratio)), Image.LANCZOS)

            buf = io.BytesIO()
            pil_img.save(buf, format="WEBP", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode()

            # Get active window info
            active = _api_get("/windows/active") or {}
            text = (
                f"## Monitor {monitor} ({mon['width']}x{mon['height']})\n"
                f"**Active Window**: {active.get('title', '?')}\n"
                f"**Position**: ({mon['left']},{mon['top']})\n"
            )
            return [
                {"type": "text", "text": text},
                {"type": "image", "data": b64, "mimeType": "image/webp"},
            ]
    except Exception as e:
        return [{"type": "text", "text": f"Error capturing monitor {monitor}: {e}"}]


HANDLERS = {
    "see_screen": handle_see_screen,
    "perception": handle_perception,
    "perception_world": handle_perception_world,
    "perception_trace": handle_perception_trace,
    "vision_query": handle_vision_query,
    "grounding_status": handle_grounding_status,
    "grounding_resolve": handle_grounding_resolve,
    "click_grounded": handle_click_grounded,
    "type_grounded": handle_type_grounded,
    "watch_screen": handle_watch_screen,
    "see_changes": handle_see_changes,
    "annotate_screen": handle_annotate,
    "read_screen_text": handle_read_text,
    "screen_status": handle_status,
    "get_context": handle_context,
    "get_audio_level": handle_audio_level,
    # v1.0: Computer Use
    "do_action": handle_do_action,
    "raw_action": handle_raw_action,
    "action_precheck": handle_action_precheck,
    "verify_action": handle_verify_action,
    "set_operating_mode": handle_set_operating_mode,
    "click_element": handle_click_element,
    "type_text": handle_type_text,
    "run_command": handle_run_command,
    "list_windows": handle_list_windows,
    "window_minimize": handle_window_minimize,
    "window_maximize": handle_window_maximize,
    "window_close": handle_window_close,
    "find_ui_element": handle_find_ui_element,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "get_clipboard": handle_get_clipboard,
    "agent_status": handle_agent_status,
    # Human-like navigation
    "focus_window": handle_focus_window,
    "browser_navigate": handle_browser_navigate,
    "browser_tabs": handle_browser_tabs,
    "click_screen": handle_click_screen,
    "keyboard": handle_keyboard,
    "scroll": handle_scroll,
    "monitor_info": handle_monitor_info,
    "see_monitor": handle_see_monitor,
    # Token management
    "token_status": handle_token_status,
    "set_token_mode": handle_set_token_mode,
    "set_token_budget": handle_set_token_budget,
}


# ─── MCP Protocol (stdio) ───

def run_mcp_stdio():
    """
    Run as MCP server over stdio.
    Reads JSON-RPC from stdin, writes to stdout.
    """
    import sys

    # Debug log to file
    _logf = open(os.path.join(os.path.dirname(__file__), "..", "mcp_debug.log"), "w")
    def _log(msg):
        _logf.write(f"{msg}\n")
        _logf.flush()

    def send(msg: dict):
        data = json.dumps(msg)
        sys.stdout.write(data + "\n")
        sys.stdout.flush()

    _log(f"MCP server starting. Python: {sys.executable}")

    def read_message() -> dict:
        """Read a JSON-RPC message. Supports both raw JSON lines and LSP Content-Length framing."""
        line = sys.stdin.readline()
        if line == "":
            _log("EOF detected, exiting")
            sys.exit(0)
        line = line.strip()
        if not line:
            return {}
        _log(f"read: {line[:120]}")

        # If line starts with '{', it's raw JSON (Claude Code style)
        if line.startswith("{"):
            return json.loads(line)

        # Otherwise it's an LSP header (Content-Length: N)
        headers = {}
        if ":" in line:
            key, val = line.split(":", 1)
            headers[key.strip()] = val.strip()
        # Read remaining headers until blank line
        while True:
            h = sys.stdin.readline()
            if h == "":
                sys.exit(0)
            h = h.strip()
            if h == "":
                break
            if ":" in h:
                key, val = h.split(":", 1)
                headers[key.strip()] = val.strip()

        content_length = int(headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        body = sys.stdin.read(content_length)
        if not body:
            sys.exit(0)
        return json.loads(body)

    # MCP handshake loop
    while True:
        try:
            msg = read_message()
            if not msg:
                continue

            method = msg.get("method", "")
            msg_id = msg.get("id")

            if method == "initialize":
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "iluminaty",
                            "version": "1.0.0",
                        },
                    },
                })

            elif method == "notifications/initialized":
                pass  # No response needed

            elif method == "tools/list":
                allowed = _get_allowed_tools()
                filtered_tools = [t for t in TOOLS if t["name"] in allowed]
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": filtered_tools},
                })

            elif method == "tools/call":
                tool_name = msg.get("params", {}).get("name", "")
                tool_args = msg.get("params", {}).get("arguments", {})

                # License gate: block pro-only tools for free users
                allowed = _get_allowed_tools()
                if tool_name not in allowed:
                    send({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{
                                "type": "text",
                                "text": f"Tool '{tool_name}' requires ILUMINATY Pro ($29/mo).\n"
                                        f"Upgrade at: https://iluminaty.dev/#pricing\n"
                                        f"Set your ILUMINATY_KEY env var after subscribing.",
                            }],
                            "isError": True,
                        },
                    })
                    continue

                handler = HANDLERS.get(tool_name)
                if handler:
                    try:
                        content = handler(tool_args)
                        send({
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {"content": content},
                        })
                    except Exception as e:
                        send({
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {
                                "content": [{"type": "text", "text": f"Error: {e}"}],
                                "isError": True,
                            },
                        })
                else:
                    send({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                            "isError": True,
                        },
                    })

            elif method == "ping":
                send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            sys.stderr.write(f"MCP error: {e}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    run_mcp_stdio()
