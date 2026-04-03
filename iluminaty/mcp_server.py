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
import http.client

import os

# ILUMINATY API base URL - configurable via env var
API_BASE = os.environ.get("ILUMINATY_API_URL", "http://127.0.0.1:8420")
# Optional API key when ILUMINATY server auth is enabled
API_KEY = os.environ.get("ILUMINATY_API_KEY", "")
try:
    API_TIMEOUT_S = float(os.environ.get("ILUMINATY_MCP_TIMEOUT_S", "12"))
except Exception:
    API_TIMEOUT_S = 12.0
API_TIMEOUT_S = max(3.0, min(60.0, API_TIMEOUT_S))

# ILUMINATY license key - gates MCP tools to free/pro plan
ILUMINATY_KEY = os.environ.get("ILUMINATY_KEY", "")

# Free tier tools — available without license
FREE_MCP_TOOLS = {
    "act",
    "see_screen", "see_changes", "read_screen_text", "perception",
    "screen_status", "get_context",
    "action_precheck", "verify_action",
    "perception_world", "perception_trace", "set_operating_mode",
    "domain_pack_list", "domain_pack_override",
    "vision_query",
    "window_minimize", "window_maximize", "window_close",
    "move_window", "drag_screen", "spatial_state",
    "workers_status", "workers_monitor",
    "workers_claim_action", "workers_release_action",
    "workers_schedule", "workers_set_subgoal", "workers_clear_subgoal", "workers_route",
    "behavior_stats", "behavior_recent", "behavior_suggest",
    "runtime_profile",
    "host_telemetry",
    "os_notifications", "os_tray", "os_dialog_status", "os_dialog_resolve",
    "audio_interrupt_status", "audio_interrupt_ack",
    "get_audio_level",
    "token_status", "set_token_mode", "set_token_budget",
}

# All tools — available with Pro license
ALL_MCP_TOOLS = {
    "see_screen", "see_changes", "annotate_screen", "read_screen_text", "perception",
    "perception_world", "perception_trace",
    "screen_status", "get_context", "get_audio_level",
    "action_precheck", "verify_action",
    "set_operating_mode", "domain_pack_list", "domain_pack_override",
    "vision_query",
    "click_element", "type_text", "run_command",
    "list_windows", "find_ui_element", "read_file", "write_file",
    "window_minimize", "window_maximize", "window_close",
    "move_window", "drag_screen", "spatial_state",
    "workers_status", "workers_monitor",
    "workers_claim_action", "workers_release_action",
    "workers_schedule", "workers_set_subgoal", "workers_clear_subgoal", "workers_route",
    "behavior_stats", "behavior_recent", "behavior_suggest",
    "runtime_profile",
    "host_telemetry",
    "os_notifications", "os_tray", "os_dialog_status", "os_dialog_resolve",
    "audio_interrupt_status", "audio_interrupt_ack",
    "get_clipboard", "agent_status",
    # Human-like navigation
    "watch_screen", "focus_window", "browser_navigate", "browser_tabs",
    "act", "click_screen", "keyboard", "scroll",
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


# ─── Persistent HTTP connection pool (keep-alive, no new TCP per call) ─────
# urllib.request creates a new socket per call. We use http.client directly
# so we reuse the same TCP connection across all MCP tool calls in a session.
# This removes ~1-3ms of TCP + TLS handshake overhead per MCP round-trip.

_HOST = None
_PORT = 8420
_CONN: dict = {"conn": None, "ts": 0.0}  # {conn: http.client.HTTPConnection, ts: last_used}
_CONN_TTL_S = 30.0  # recycle connection after 30s idle

def _parse_api_base() -> tuple[str, int]:
    """Extract host and port from API_BASE."""
    url = API_BASE.rstrip("/")
    if "://" in url:
        url = url.split("://", 1)[1]
    if ":" in url:
        h, p = url.rsplit(":", 1)
        try:
            return h, int(p)
        except ValueError:
            return h, 8420
    return url, 8420

def _get_conn() -> http.client.HTTPConnection:
    """Return a live HTTPConnection, recycling if still warm."""
    global _HOST, _PORT
    if _HOST is None:
        _HOST, _PORT = _parse_api_base()
    now = time.monotonic()
    c = _CONN.get("conn")
    if c is not None and (now - _CONN["ts"]) < _CONN_TTL_S:
        return c
    # Create fresh connection (or reconnect after TTL/error)
    conn = http.client.HTTPConnection(_HOST, _PORT, timeout=API_TIMEOUT_S)
    _CONN["conn"] = conn
    _CONN["ts"] = now
    return conn

def _api_request(method: str, path: str, body: dict | None = None) -> dict:
    """Single keep-alive request. Falls back to new connection on error."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if API_KEY:
        headers["x-api-key"] = API_KEY
    raw_body: bytes | None = None
    if body is not None:
        raw_body = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(raw_body))
    for attempt in range(2):
        try:
            conn = _get_conn()
            conn.request(method, path, body=raw_body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            _CONN["ts"] = time.monotonic()
            if not data or not data.strip():
                return {"error": f"Empty response (HTTP {resp.status})", "status": resp.status}
            return json.loads(data.decode())
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON: {e}", "raw": data.decode()[:200] if data else ""}
        except Exception:
            # Force connection reset on next call
            _CONN["conn"] = None
            _CONN["ts"] = 0.0
            if attempt == 1:
                raise

def _api_get(path: str) -> dict:
    """GET request to ILUMINATY API (keep-alive)."""
    return _api_request("GET", path)

def _api_post(path: str, body: dict | None = None) -> dict:
    """POST request to ILUMINATY API (keep-alive)."""
    return _api_request("POST", path, body)


# ─── Human-like Operation Helpers ───

_BROWSER_HINTS = ("brave", "chrome", "edge", "firefox", "opera", "vivaldi", "browser")
_BROWSER_NAME_MAP = {
    "auto": ("brave", "chrome", "edge", "firefox"),
    "brave": ("brave", "chrome", "edge", "firefox"),
    "chrome": ("chrome", "brave", "edge", "firefox"),
    "edge": ("edge", "chrome", "brave", "firefox"),
    "firefox": ("firefox", "chrome", "brave", "edge"),
}
_BROWSER_PID_CACHE = {"ts": 0.0, "pids": set()}
_WINDOW_QUERY_ALIASES = {
    "notepad": ["bloc de notas", "notepad"],
    "bloc de notas": ["bloc de notas", "notepad"],
    "explorer": ["explorer", "file explorer", "explorador de archivos"],
    "file explorer": ["explorer", "file explorer", "explorador de archivos"],
    "browser": ["browser", "brave", "chrome", "edge", "firefox", "navegador"],
}


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "://" in u:
        return u
    if u.startswith(("localhost", "127.", "0.0.0.0")):
        return f"http://{u}"
    return f"https://{u}"


def _window_text(win: dict) -> str:
    return f"{win.get('title', '')} {win.get('app_name', '')}".lower()


def _looks_like_browser_window(win: dict) -> bool:
    text = _window_text(win)
    if any(h in text for h in _BROWSER_HINTS):
        return True
    # Browser pages frequently include domains in the title.
    return ".com" in text or ".ai" in text or ".org" in text or ".io" in text


def _browser_preference(preferred: str) -> tuple[str, ...]:
    pref = (preferred or "auto").strip().lower()
    return _BROWSER_NAME_MAP.get(pref, _BROWSER_NAME_MAP["auto"])


def _browser_pid_set(force_refresh: bool = False) -> set[int]:
    now = time.time()
    if (not force_refresh) and (now - _BROWSER_PID_CACHE["ts"] < 3.0):
        return set(_BROWSER_PID_CACHE["pids"])

    names = ("brave", "chrome", "msedge", "firefox", "opera", "vivaldi")
    pids: set[int] = set()
    for name in names:
        try:
            found = _api_get(f"/process/find?name={urllib.parse.quote(name)}").get("matches", [])
        except Exception:
            continue
        for proc in found:
            pid = proc.get("pid")
            if isinstance(pid, int):
                pids.add(pid)

    _BROWSER_PID_CACHE["ts"] = now
    _BROWSER_PID_CACHE["pids"] = set(pids)
    return pids


def _get_windows_context() -> tuple[dict, list[dict]]:
    active = {}
    windows: list[dict] = []
    try:
        active = _api_get("/windows/active") or {}
    except Exception:
        active = {}
    try:
        windows = _api_get("/windows/list?visible_only=true&exclude_minimized=true&exclude_system=true").get("windows", [])
    except Exception:
        try:
            windows = _api_get("/windows/list?visible_only=true&exclude_minimized=true").get("windows", [])
        except Exception:
            windows = []
    return active, windows


def _window_match_score(query: str, win: dict, active: dict, prefer_active_monitor: bool) -> int:
    q = (query or "").strip().lower()
    if not q:
        return 0
    text = _window_text(win)
    title = str(win.get("title", "")).strip().lower()
    tokens = [t for t in q.replace("-", " ").split() if t]
    expanded_queries = [q]
    expanded_queries.extend(_WINDOW_QUERY_ALIASES.get(q, []))
    for token in list(tokens):
        expanded_queries.extend(_WINDOW_QUERY_ALIASES.get(token, []))
    # preserve insertion order + uniqueness
    dedup = []
    seen = set()
    for item in expanded_queries:
        item = str(item).strip().lower()
        if item and item not in seen:
            seen.add(item)
            dedup.append(item)
    expanded_queries = dedup
    score = 0

    for qq in expanded_queries:
        if title == qq:
            score += 220
            break
    else:
        for qq in expanded_queries:
            if qq in title:
                score += 160
                break
        for qq in expanded_queries:
            if qq in text:
                score += 120
                break

    for token in tokens:
        if token in text:
            score += 16
    if tokens and all(token in text for token in tokens):
        score += 38

    if win.get("is_visible", True):
        score += 6
    if not win.get("is_minimized", False):
        score += 8

    try:
        active_handle = int(active.get("handle", 0) or 0)
        if active_handle and int(win.get("handle", 0) or 0) == active_handle:
            score += 20
    except Exception:
        pass  # noqa: suppressed Exception

    if prefer_active_monitor:
        try:
            active_monitor = int(active.get("monitor_id", 0) or 0)
            win_monitor = int(win.get("monitor_id", 0) or 0)
            if active_monitor and win_monitor and win_monitor == active_monitor:
                score += 18
        except Exception:
            pass  # noqa: suppressed Exception

    return score


def _resolve_window_by_query(title_query: str, prefer_active_monitor: bool = True) -> dict | None:
    active, windows = _get_windows_context()
    if not windows:
        return None

    best = None
    best_score = -1
    for win in windows:
        score = _window_match_score(title_query, win, active, prefer_active_monitor)
        if score > best_score:
            best = win
            best_score = score
    if best is not None and best_score >= 48:
        return best
    return None


def _select_browser_window(active: dict, windows: list[dict], preferred: str = "auto") -> tuple[dict | None, str]:
    if active and _looks_like_browser_window(active):
        return active, "active_browser_window"

    preferred_names = _browser_preference(preferred)
    browser_pids = _browser_pid_set(force_refresh=False)

    best = None
    best_score = -1
    for win in windows:
        text = _window_text(win)
        score = 0
        if _looks_like_browser_window(win):
            score += 60
        pid = win.get("pid")
        if isinstance(pid, int) and pid in browser_pids:
            score += 140
        for idx, name in enumerate(preferred_names):
            if name in text:
                score += 100 - (idx * 12)
                break
        if win.get("is_visible", True):
            score += 5
        if not win.get("is_minimized", False):
            score += 8
        if score > best_score:
            best = win
            best_score = score

    if best is not None and best_score >= 70:
        return best, "best_visible_browser_window"
    return None, "no_browser_window_match"


def _navigate_with_keyboard(url: str, focus_handle: int | None, new_tab: bool) -> tuple[bool, str]:
    suffix = f"&focus_handle={int(focus_handle)}" if focus_handle is not None else ""
    try:
        if new_tab:
            _api_post(f"/action/hotkey?keys=ctrl%2Bt{suffix}")
            time.sleep(0.10)
        _api_post(f"/action/hotkey?keys=ctrl%2Bl{suffix}")
        time.sleep(0.08)
        typed = _api_post(
            f"/action/type?text={urllib.parse.quote(url)}&interval=0.008{suffix}"
        )
        _api_post(f"/action/hotkey?keys=enter{suffix}")
        ok = bool(typed.get("success", True))
        return ok, "keyboard_address_bar"
    except Exception as e:
        return False, f"keyboard_failed: {e}"


def _launch_browser_or_url(url: str, preferred: str) -> tuple[bool, str]:
    pref = (preferred or "auto").strip().lower()
    launch_candidates = []
    if pref in {"auto", "brave"}:
        launch_candidates += [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            "brave.exe",
            "brave",
        ]
    if pref in {"auto", "chrome"}:
        launch_candidates += [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "chrome.exe",
            "chrome",
        ]
    if pref in {"auto", "edge"}:
        launch_candidates += ["msedge.exe", "msedge"]
    if pref in {"auto", "firefox"}:
        launch_candidates += ["firefox.exe", "firefox"]

    # Last candidate: open URL with system handler.
    launch_candidates.append(url)

    for cmd in launch_candidates:
        try:
            res = _api_post(f"/process/launch?command={urllib.parse.quote(cmd)}")
            if res.get("success"):
                return True, f"process_launch:{cmd}"
        except Exception:
            continue
    return False, "process_launch_failed"


def _execute_cycle_action(action: dict, focus_handle: int | None, monitor_hint: int | None) -> tuple[str, dict]:
    kind = str(action.get("kind") or action.get("type") or "").strip().lower()
    if not kind:
        return "none", {"success": False, "message": "missing action kind"}

    focus_suffix = f"&focus_handle={int(focus_handle)}" if focus_handle is not None else ""
    monitor = action.get("monitor")
    if monitor is None:
        monitor = monitor_hint

    if kind == "click":
        x = int(action.get("x", 0))
        y = int(action.get("y", 0))
        button = urllib.parse.quote(str(action.get("button", "left")))
        coord_space = str(action.get("coord_space", "global")).strip().lower()
        query = f"/action/click?x={x}&y={y}&button={button}{focus_suffix}"
        if monitor is not None:
            query += f"&monitor_id={int(monitor)}"
        if coord_space in {"monitor", "local", "monitor_local"}:
            query += "&relative_to_monitor=true"
        return kind, _api_post(query)

    if kind == "type":
        text = urllib.parse.quote(str(action.get("text", "")))
        interval = float(action.get("interval", 0.01))
        query = f"/action/type?text={text}&interval={interval}{focus_suffix}"
        return kind, _api_post(query)

    if kind == "hotkey":
        keys = urllib.parse.quote(str(action.get("keys", "")))
        query = f"/action/hotkey?keys={keys}{focus_suffix}"
        return kind, _api_post(query)

    if kind == "scroll":
        amount = int(action.get("amount", 3))
        coord_space = str(action.get("coord_space", "global")).strip().lower()
        query = f"/action/scroll?amount={amount}{focus_suffix}"
        if "x" in action and action.get("x") is not None:
            query += f"&x={int(action.get('x'))}"
        if "y" in action and action.get("y") is not None:
            query += f"&y={int(action.get('y'))}"
        if monitor is not None:
            query += f"&monitor_id={int(monitor)}"
        if coord_space in {"monitor", "local", "monitor_local"}:
            query += "&relative_to_monitor=true"
        return kind, _api_post(query)

    if kind == "drag":
        sx = int(action.get("start_x", 0))
        sy = int(action.get("start_y", 0))
        ex = int(action.get("end_x", 0))
        ey = int(action.get("end_y", 0))
        duration = float(action.get("duration", 0.35))
        coord_space = str(action.get("coord_space", "global")).strip().lower()
        query = (
            f"/action/drag?start_x={sx}&start_y={sy}&end_x={ex}&end_y={ey}"
            f"&duration={duration}{focus_suffix}"
        )
        if monitor is not None:
            query += f"&monitor_id={int(monitor)}"
        if coord_space in {"monitor", "local", "monitor_local"}:
            query += "&relative_to_monitor=true"
        return kind, _api_post(query)

    return kind, {"success": False, "message": f"unsupported action kind: {kind}"}


def _extract_dialog_affordances(ocr_text: str) -> list[str]:
    text = str(ocr_text or "")
    candidates: list[str] = []
    seen = set()
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if len(s) > 20:
            continue
        if len(s.split()) > 3:
            continue
        low = s.lower()
        if "http" in low or "www." in low or "/" in s or "\\" in s:
            continue
        if ":" in s and len(s) > 8:
            continue
        digit_count = sum(1 for ch in s if ch.isdigit())
        if digit_count > 2:
            continue
        # Buttons/toggles are often short imperative tokens.
        key = low
        if key not in seen:
            seen.add(key)
            candidates.append(s)
    return candidates[:8]


def _detect_blocking_interrupt(ocr_text: str, active_title: str, target_title: str) -> dict:
    text = str(ocr_text or "")
    low_text = text.lower()
    active = (active_title or "").lower()
    target = (target_title or "").lower()
    affordances = _extract_dialog_affordances(text)

    # Generic dialog-like cues (not tied to specific button words).
    line_count = len([ln for ln in text.splitlines() if ln.strip()])
    short_panel = 1 <= line_count <= 22 and len(text) <= 1400
    has_punctuation_prompt = ("?" in text) or ("!" in text)
    title_dialogish = any(tok in active for tok in ("dialog", "message", "mensaje", "alert", "warning", "error", "confirm"))
    title_deviation = bool(target) and bool(active) and (target not in active)

    score = 0
    if title_deviation:
        score += 2
    if title_dialogish:
        score += 2
    if short_panel:
        score += 1
    if has_punctuation_prompt:
        score += 1
    if len(affordances) >= 1:
        score += 2
    if len(affordances) >= 2:
        score += 1

    detected = score >= 4 or (title_deviation and len(affordances) >= 1)

    # Conservative hint only; resolution does not depend on exact labels.
    low_aff = " ".join(a.lower() for a in affordances)
    has_accept_hint = any(t in low_aff for t in ("ok", "yes", "si", "accept", "aceptar", "continue", "continuar"))
    has_dismiss_hint = any(t in low_aff for t in ("cancel", "cancelar", "no", "close", "cerrar"))

    return {
        "detected": bool(detected),
        "has_accept": bool(has_accept_hint),
        "has_dismiss": bool(has_dismiss_hint),
        "title_deviation": bool(title_deviation),
        "signal": f"structural_score={score}",
        "affordances": affordances,
    }


def _resolve_blocking_interrupt(strategy: str, focus_handle: int | None, detect: dict) -> dict:
    mode = (strategy or "accept_first").strip().lower()
    if mode not in {"accept_first", "dismiss_first", "none"}:
        mode = "accept_first"
    if mode == "none":
        return {"resolved": False, "method": "disabled"}

    suffix = f"&focus_handle={int(focus_handle)}" if focus_handle is not None else ""
    orders = []
    if mode == "dismiss_first":
        orders = ["esc", "enter", "space"]
    else:
        orders = ["enter", "space", "esc"]

    # If we can infer no dismiss path, prioritize accept-like keys.
    if detect.get("has_accept") and not detect.get("has_dismiss"):
        orders = ["enter", "space", "esc"]
    if detect.get("has_dismiss") and not detect.get("has_accept") and mode == "dismiss_first":
        orders = ["esc", "enter", "space"]

    for key in orders:
        try:
            res = _api_post(f"/action/hotkey?keys={urllib.parse.quote(key)}{suffix}")
            if res.get("success"):
                return {"resolved": True, "method": f"hotkey:{key}"}
        except Exception:
            continue
    return {"resolved": False, "method": "no_hotkey_succeeded"}


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
                "monitor": {
                    "type": "integer",
                    "description": "Optional monitor id. If omitted, backend uses active monitor.",
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
                "monitor": {
                    "type": "integer",
                    "description": "Optional monitor id (1..N). If omitted, includes all monitors.",
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
                "monitor": {"type": "integer", "description": "Optional monitor id (1..N)"},
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
        "name": "action_intent",
        "description": (
            "Execute a high-level natural language intent via ILUMINATY's intent classifier "
            "and closed-loop action pipeline."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "Natural language instruction"},
                "mode": {"type": "string", "enum": ["SAFE", "RAW", "HYBRID"], "description": "Optional operating mode override"},
                "verify": {"type": "boolean", "description": "Run verifier/recovery after action", "default": True},
                "context_tick_id": {"type": "integer", "description": "Optional expected world tick id"},
                "max_staleness_ms": {"type": "integer", "description": "Optional max context age in ms"},
                "use_grounding": {"type": "boolean", "description": "Enable grounding support for target selection", "default": False},
                "target_query": {"type": "string", "description": "Optional grounding query"},
                "target_role": {"type": "string", "description": "Optional grounding role hint"},
                "monitor_id": {"type": "integer", "description": "Optional monitor id for grounding"},
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
        "name": "operate_cycle",
        "description": (
            "Global human-like operation cycle for any app/window: "
            "orientation -> localization -> focus -> read -> action -> verification."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "Short objective description"},
                "target_window": {"type": "string", "description": "Optional target window/app query"},
                "monitor": {"type": "integer", "description": "Optional monitor bias (1..N)"},
                "include_ocr": {"type": "boolean", "description": "Read OCR for comprehension step", "default": True},
                "resolve_interrupts": {
                    "type": "boolean",
                    "description": "Auto-handle blocking dialogs/modals before action (default true).",
                    "default": True,
                },
                "interrupt_strategy": {
                    "type": "string",
                    "enum": ["accept_first", "dismiss_first", "none"],
                    "description": "How to resolve blocking dialogs when detected.",
                    "default": "accept_first",
                },
                "action": {
                    "type": "object",
                    "description": (
                        "Optional action descriptor. "
                        "Supported kinds: click, type, hotkey, scroll, drag."
                    ),
                },
                "verify_contains": {"type": "string", "description": "Optional text expected after action"},
            },
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
            "List windows with handle, title, position, size, and monitor_id. "
            "Use handle-based targeting for reliable control."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "monitor": {"type": "integer", "description": "Optional monitor id filter"},
                "title_contains": {"type": "string", "description": "Optional title substring filter"},
                "exclude_minimized": {"type": "boolean", "description": "Hide minimized windows", "default": True},
            },
        },
    },
    {
        "name": "window_minimize",
        "description": "Minimize a window by handle or title.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title (partial match accepted by backend)"},
                "handle": {"type": "integer", "description": "Exact window handle (preferred when available)"},
            },
        },
    },
    {
        "name": "window_maximize",
        "description": "Maximize a window by handle or title.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title (partial match accepted by backend)"},
                "handle": {"type": "integer", "description": "Exact window handle (preferred when available)"},
            },
        },
    },
    {
        "name": "window_close",
        "description": "Close a window by handle or title.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title (partial match accepted by backend)"},
                "handle": {"type": "integer", "description": "Exact window handle (preferred when available)"},
            },
        },
    },
    {
        "name": "move_window",
        "description": (
            "Move/resize a window to specific desktop coordinates. "
            "Useful to relocate windows between monitors quickly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title (partial match)"},
                "handle": {"type": "integer", "description": "Exact window handle (preferred)"},
                "x": {"type": "integer", "description": "Target x (virtual desktop)"},
                "y": {"type": "integer", "description": "Target y (virtual desktop)"},
                "width": {"type": "integer", "description": "Optional width (default keep)", "default": -1},
                "height": {"type": "integer", "description": "Optional height (default keep)", "default": -1},
                "monitor": {"type": "integer", "description": "Optional monitor id for monitor-local coordinates"},
                "coord_space": {
                    "type": "string",
                    "enum": ["global", "monitor"],
                    "description": "global=virtual desktop coordinates, monitor=coords relative to selected monitor",
                    "default": "global",
                },
            },
            "required": ["x", "y"],
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
            "Switch to a window by title/handle with contextual ranking (active monitor + best match). "
            "Example: focus_window('Chrome') switches to Chrome. "
            "Use list_windows first to see available windows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title (partial match)"},
                "handle": {"type": "integer", "description": "Exact window handle (preferred when available)"},
                "prefer_active_monitor": {
                    "type": "boolean",
                    "description": "Bias match toward active monitor window when title is ambiguous (default true).",
                    "default": True,
                },
            },
        },
    },
    {
        "name": "browser_navigate",
        "description": (
            "Navigate to a URL using human-like context reasoning. "
            "By default it preserves current user context: reuse existing browser window, "
            "open a new tab, then navigate from the address bar. "
            "Falls back to CDP/process launch only if needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "new_tab": {
                    "type": "boolean",
                    "description": "Open in a new tab (default true) to preserve current page context.",
                    "default": True,
                },
                "preserve_context": {
                    "type": "boolean",
                    "description": "Prefer existing browser window/session before any fallback (default true).",
                    "default": True,
                },
                "browser": {
                    "type": "string",
                    "enum": ["auto", "brave", "chrome", "edge", "firefox"],
                    "description": "Preferred browser family when selecting/fallback launching.",
                    "default": "auto",
                },
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
        "name": "act",
        "description": (
            "Direct action executor — no middleware, no grounding, no safety gates. "
            "Claude sees the screen, decides what to do, and ILUMINATY executes exactly. "
            "Actions: click, double_click, type, key, scroll, focus, move_mouse. "
            "This is the PRIMARY action tool. Use see_screen first to know WHERE to act."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["click", "double_click", "type", "key", "scroll", "focus", "move_mouse"],
                    "description": "Action to perform",
                },
                "x": {"type": "integer", "description": "X coordinate (for click/double_click/scroll/move_mouse)"},
                "y": {"type": "integer", "description": "Y coordinate (for click/double_click/scroll/move_mouse)"},
                "button": {"type": "string", "description": "Mouse button: left/right/middle (for click)", "default": "left"},
                "text": {"type": "string", "description": "Text to type (for type action)"},
                "keys": {"type": "string", "description": "Keys to press (for key action). Examples: enter, ctrl+s, win+r, alt+f4"},
                "amount": {"type": "integer", "description": "Scroll amount: positive=down, negative=up (for scroll)", "default": 3},
                "title": {"type": "string", "description": "Window title to focus (for focus action)"},
                "handle": {"type": "integer", "description": "Window handle to focus (for focus action)"},
                "monitor": {"type": "integer", "description": "Monitor id for monitor-local coordinates"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "click_screen",
        "description": (
            "Click at a specific position on screen using REAL screen coordinates (not image coordinates). "
            "For multi-monitor setups, coordinates span the full virtual desktop by default. "
            "If you used see_monitor, pass monitor + coord_space='monitor' to auto-translate monitor-local coords."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate (real screen pixels)"},
                "y": {"type": "integer", "description": "Y coordinate (real screen pixels)"},
                "button": {"type": "string", "description": "Mouse button: left, right, middle", "default": "left"},
                "monitor": {"type": "integer", "description": "Optional monitor id when using monitor-local coordinates"},
                "coord_space": {
                    "type": "string",
                    "enum": ["global", "monitor"],
                    "description": "global=virtual desktop coordinates, monitor=coords relative to selected monitor",
                    "default": "global",
                },
                "focus_title": {"type": "string", "description": "Optional window title to focus before clicking."},
                "focus_handle": {"type": "integer", "description": "Optional window handle to focus before clicking."},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "drag_screen",
        "description": (
            "Drag from start to end coordinates. "
            "Supports global coordinates or monitor-local coordinates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_x": {"type": "integer", "description": "Drag start X"},
                "start_y": {"type": "integer", "description": "Drag start Y"},
                "end_x": {"type": "integer", "description": "Drag end X"},
                "end_y": {"type": "integer", "description": "Drag end Y"},
                "duration": {"type": "number", "description": "Drag duration seconds", "default": 0.35},
                "monitor": {"type": "integer", "description": "Optional monitor id for monitor-local coordinates"},
                "coord_space": {
                    "type": "string",
                    "enum": ["global", "monitor"],
                    "description": "global=virtual desktop coordinates, monitor=coords relative to selected monitor",
                    "default": "global",
                },
                "focus_title": {"type": "string", "description": "Optional window title to focus before dragging."},
                "focus_handle": {"type": "integer", "description": "Optional window handle to focus before dragging."},
            },
            "required": ["start_x", "start_y", "end_x", "end_y"],
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
                "focus_title": {"type": "string", "description": "Optional window title to focus before keypress."},
                "focus_handle": {"type": "integer", "description": "Optional window handle to focus before keypress."},
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
                "x": {"type": "integer", "description": "Optional x coordinate for scroll target"},
                "y": {"type": "integer", "description": "Optional y coordinate for scroll target"},
                "monitor": {"type": "integer", "description": "Optional monitor id for monitor-local coordinates"},
                "coord_space": {
                    "type": "string",
                    "enum": ["global", "monitor"],
                    "description": "global=virtual desktop coordinates, monitor=coords relative to selected monitor",
                    "default": "global",
                },
                "focus_title": {"type": "string", "description": "Optional window title to focus before scroll."},
                "focus_handle": {"type": "integer", "description": "Optional window handle to focus before scroll."},
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
        "name": "domain_pack_list",
        "description": (
            "List built-in and custom Domain Packs, including active selection and override state."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "domain_pack_override",
        "description": (
            "Force a specific Domain Pack (e.g. trading, coding) or set name=auto to return to automatic selection."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Pack name or auto",
                    "default": "auto",
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
    {
        "name": "spatial_state",
        "description": (
            "Get unified desktop spatial map: monitors, active monitor/window, cursor, and windows grouped by monitor."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_windows": {"type": "boolean", "description": "Include full windows list", "default": True},
            },
        },
    },
    {
        "name": "workers_status",
        "description": (
            "Get Workers Sys v1 status: monitor digests, spatial/fusion state, action arbiter lease, "
            "intent timeline, and verification timeline."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "workers_monitor",
        "description": (
            "Get a single monitor worker digest (scene, readiness, staleness, attention, visual facts)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "monitor": {"type": "integer", "description": "Monitor id (1..N)"},
            },
            "required": ["monitor"],
        },
    },
    {
        "name": "workers_claim_action",
        "description": (
            "Claim the single-writer action lease from Workers Arbiter. "
            "Use before multi-step execution loops when coordinating multiple agents."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Lease owner id/name", "default": "mcp-executor"},
                "ttl_ms": {"type": "integer", "description": "Optional lease TTL in ms", "default": 2500},
                "force": {"type": "boolean", "description": "Force lease takeover", "default": False},
            },
        },
    },
    {
        "name": "workers_release_action",
        "description": (
            "Release the single-writer action lease back to the arbiter."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Lease owner id/name", "default": "mcp-executor"},
                "success": {"type": "boolean", "description": "Whether last action succeeded", "default": True},
                "message": {"type": "string", "description": "Optional result message"},
            },
        },
    },
    {
        "name": "workers_schedule",
        "description": "Get Workers v2 attention schedule (budget share per monitor + recommended monitor).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "workers_set_subgoal",
        "description": "Set a monitor-local subgoal with priority/risk/deadline for Workers scheduler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "monitor_id": {"type": "integer", "description": "Monitor id (1..N)"},
                "goal": {"type": "string", "description": "Subgoal summary"},
                "priority": {"type": "number", "description": "0.0..1.0", "default": 0.5},
                "risk": {"type": "string", "description": "low|normal|high|critical", "default": "normal"},
                "deadline_ms": {"type": "integer", "description": "Optional unix epoch ms deadline"},
            },
            "required": ["monitor_id", "goal"],
        },
    },
    {
        "name": "workers_clear_subgoal",
        "description": "Clear/complete a workers subgoal by id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subgoal_id": {"type": "string", "description": "Subgoal id"},
                "completed": {"type": "boolean", "description": "Mark as completed", "default": True},
            },
            "required": ["subgoal_id"],
        },
    },
    {
        "name": "workers_route",
        "description": "Route a query/objective to the best monitor using scheduler + digest context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Intent/query to route"},
                "preferred_monitor_id": {"type": "integer", "description": "Optional preferred monitor"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "behavior_stats",
        "description": "Get persistent app behavior cache stats (Phase C).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "behavior_recent",
        "description": "Get recent behavior outcomes from persistent cache.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Entries to return (1-200)", "default": 20},
            },
        },
    },
    {
        "name": "behavior_suggest",
        "description": "Ask behavior cache for execution hints on action/app/window.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "app_name": {"type": "string"},
                "window_title": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "runtime_profile",
        "description": "Get or set runtime profile (standard|enterprise|lab).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "set": {"type": "string", "description": "Optional target profile"},
            },
        },
    },
    {
        "name": "host_telemetry",
        "description": "Get host telemetry snapshot (CPU/RAM/temps/GPU when available).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "os_notifications",
        "description": "Read merged OS notifications feed (watchdog + audio interrupts).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max items to return", "default": 20},
            },
        },
    },
    {
        "name": "os_tray",
        "description": "Inspect OS tray/taskbar surface status (Windows-first).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "os_dialog_status",
        "description": "Detect likely blocking native dialog and available affordances.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "monitor_id": {"type": "integer", "description": "Optional monitor id for dialog probe"},
            },
        },
    },
    {
        "name": "os_dialog_resolve",
        "description": "Attempt to resolve a blocking dialog by label or coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Preferred dialog button label (e.g. OK, Cancel)"},
                "x": {"type": "integer", "description": "Optional absolute x coordinate"},
                "y": {"type": "integer", "description": "Optional absolute y coordinate"},
                "monitor_id": {"type": "integer", "description": "Optional monitor id"},
                "mode": {"type": "string", "enum": ["SAFE", "RAW", "HYBRID"], "description": "Optional mode override"},
                "verify": {"type": "boolean", "description": "Run post-action verification", "default": True},
            },
        },
    },
    {
        "name": "audio_interrupt_status",
        "description": "Get operational audio interrupt status and recent interrupt events.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "audio_interrupt_ack",
        "description": "Acknowledge/clear current audio interrupt guard.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ─── Tool Handlers ───

def handle_see_screen(args: dict) -> dict:
    mode = args.get("mode", VISION_MODE)
    monitor = args.get("monitor")
    query = f"/vision/smart?mode={mode}"
    if monitor is not None:
        query += f"&monitor_id={int(monitor)}"
    data = _api_get(query)

    if data.get("error") == "token_budget_exceeded":
        return [{"type": "text", "text": (
            f"TOKEN BUDGET EXCEEDED. Used: {data['used']}/{data['budget']}. "
            f"Switch to text_only mode or increase budget with set_token_budget."
        )}]

    monitor_info = f"monitor={data.get('monitor_id', monitor if monitor is not None else 'auto')}"
    tokens_info = (
        f"\n\n---\n[{monitor_info} | Token mode: {mode} | "
        f"~{data.get('token_estimate', '?')} tokens | Total used: {data.get('tokens_used_total', '?')}]"
    )

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
            f"- Domain: {world.get('domain_pack', 'general')} (conf={world.get('domain_confidence', 0)})",
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


def handle_domain_pack_list(args: dict) -> list:
    try:
        data = _api_get("/domain-packs")
    except Exception as e:
        return [{"type": "text", "text": f"Domain packs unavailable: {e}"}]

    packs = data.get("packs", [])
    active = data.get("active", {}) or {}
    override = data.get("override")
    lines = [
        "## Domain Packs",
        f"- Active: {active.get('domain_pack', 'general')} (conf={active.get('domain_confidence', 0)})",
        f"- Override: {override if override else 'auto'}",
        f"- Available: {len(packs)}",
    ]
    for pack in packs[:12]:
        lines.append(
            f"- {pack.get('name')} [{pack.get('source', 'builtin')}] "
            f"priority={pack.get('priority', '?')} "
            f"stale={((pack.get('staleness_policy') or {}).get('safe', '?'))}ms safe"
        )
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_domain_pack_override(args: dict) -> list:
    name = (args.get("name") or "auto").strip()
    try:
        data = _api_post("/domain-packs/override", body={"name": name})
    except Exception as e:
        return [{"type": "text", "text": f"Domain pack override failed: {e}"}]
    return [{
        "type": "text",
        "text": (
            f"Domain override: {'OK' if data.get('ok') else 'FAILED'} | "
            f"override={data.get('override')} | reason={data.get('reason', 'n/a')}"
        ),
    }]


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
    path = f"/frames?last={n}&include_images=true"
    if monitor is not None:
        path += f"&monitor_id={int(monitor)}"
    data = _api_get(path)
    frames = data.get("frames", [])

    if not frames:
        return [{"type": "text", "text": "No frames in buffer yet."}]

    result = []
    scope = f"monitor {monitor}" if monitor is not None else "all monitors"
    result.append({
        "type": "text",
        "text": f"## Screen Replay ({len(frames)} frames, {scope})\nOldest → Newest. Watch for changes between frames.",
    })

    for i, f in enumerate(frames):
        ts = f.get("timestamp_iso", "?")
        change = f.get("change_score", 0)
        img_b64 = f.get("image_base64")

        label = f"**Frame {i+1}/{len(frames)}** — {ts} | change: {change}"

        if img_b64:
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
    monitor = args.get("monitor")
    # Get frames WITH images to actually see what changed
    path = f"/frames?seconds={seconds}&include_images=true"
    if monitor is not None:
        path += f"&monitor_id={int(monitor)}"
    data = _api_get(path)
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

    scope = f"monitor {monitor}" if monitor is not None else "all monitors"
    result = [{"type": "text", "text": f"## Screen Changes ({scope}, last {seconds}s) — {count} total frames, showing {len(significant)} key frames"}]

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
    if "monitor" in args and args["monitor"] is not None:
        params.append(f"monitor_id={int(args['monitor'])}")
    for key in ["region_x", "region_y", "region_w", "region_h"]:
        if key in args and args[key] is not None:
            params.append(f"{key}={args[key]}")

    query = "&".join(params) if params else ""
    path = f"/vision/ocr?{query}" if query else "/vision/ocr"
    try:
        data = _api_get(path)
    except Exception:
        # Fallback: use cached snapshot OCR if fresh OCR times out (VLM contention)
        try:
            fallback_path = f"/vision/snapshot?ocr=true&include_image=false"
            if "monitor_id" in query:
                fallback_path += f"&{query}"
            data = _api_get(fallback_path)
            data["text"] = data.get("ocr_text", "")
            data["confidence"] = 70
        except Exception:
            return [{"type": "text", "text": "Screen text unavailable (server busy with VLM inference). Try again in a few seconds."}]

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


def handle_action_intent(args: dict) -> list:
    instruction = args.get("instruction", "")
    if not instruction:
        return [{"type": "text", "text": "Error: instruction is required"}]
    payload = {"instruction": instruction}
    for key in (
        "mode",
        "verify",
        "context_tick_id",
        "max_staleness_ms",
        "use_grounding",
        "target_query",
        "target_role",
        "monitor_id",
    ):
        if key in args and args[key] is not None:
            payload[key] = args[key]
    data = _api_post("/action/intent", body=payload)
    result = data.get("result", {})
    precheck = data.get("precheck", {})
    lines = [
        f"Intent mode: {precheck.get('mode', '?')} | blocked: {precheck.get('blocked', False)}",
        f"Execution: {'SUCCESS' if result.get('success') else 'FAILED'} - {result.get('message', '')}",
    ]
    if precheck.get("grounding_applies"):
        grd = precheck.get("grounding_check", {})
        lines.append(
            f"Grounding: {'OK' if grd.get('allowed') else 'BLOCKED'} "
            f"reason={grd.get('reason', 'n/a')} conf={grd.get('confidence', 0)}"
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


def handle_operate_cycle(args: dict) -> list:
    goal = (args.get("goal") or "").strip()
    target_window = (args.get("target_window") or "").strip()
    monitor = args.get("monitor")
    include_ocr = _as_bool(args.get("include_ocr"), True)
    resolve_interrupts = _as_bool(args.get("resolve_interrupts"), True)
    interrupt_strategy = str(args.get("interrupt_strategy", "accept_first")).strip().lower()
    action = args.get("action") if isinstance(args.get("action"), dict) else None
    verify_contains = (args.get("verify_contains") or "").strip()

    # 1) ORIENTATION
    spatial = {}
    try:
        spatial = _api_get("/spatial/state?include_windows=true")
    except Exception:
        spatial = {}
    active = spatial.get("active_window", {}) or {}
    active_monitor = spatial.get("active_monitor_id")
    monitor_count = int(spatial.get("monitor_count", 0) or 0)

    # 2) LOCALIZATION
    target = None
    locate_strategy = "active_window"
    if target_window:
        target = _resolve_window_by_query(target_window, prefer_active_monitor=True)
        locate_strategy = "context_ranked_query"
    elif active:
        target = active
    if target is None and monitor is not None:
        for w in (spatial.get("windows") or []):
            if int(w.get("monitor_id", 0) or 0) == int(monitor):
                target = w
                locate_strategy = "first_visible_on_monitor"
                break

    # 3) FOCUS
    focus_ok = False
    focus_handle = None
    focus_error = ""
    focus_strategy = "direct_focus_endpoint"
    if target is not None:
        try:
            focus_handle = int(target.get("handle", 0) or 0) or None
        except Exception:
            focus_handle = None
    if focus_handle is not None:
        try:
            fr = _api_post(f"/windows/focus?handle={focus_handle}")
            focus_ok = bool(fr.get("success"))
        except Exception as e:
            focus_ok = False
            focus_error = str(e)
    # Fallback when focus endpoint is unavailable (e.g., restricted plan):
    # click center of target window to emulate natural human focus acquisition.
    if (not focus_ok) and isinstance(target, dict):
        try:
            tx = int(target.get("x", 0) or 0)
            ty = int(target.get("y", 0) or 0)
            tw = int(target.get("width", 0) or 0)
            th = int(target.get("height", 0) or 0)
            if tw > 4 and th > 4:
                cx = tx + max(2, tw // 2)
                cy = ty + max(2, th // 2)
                fr2 = _api_post(f"/action/click?x={cx}&y={cy}&button=left")
                if fr2.get("success"):
                    focus_ok = True
                    focus_strategy = "click_center_fallback"
                    focus_error = ""
        except Exception as e:
            if not focus_error:
                focus_error = str(e)

    # 4) READING / COMPREHENSION
    window_info = {}
    try:
        window_info = _api_get("/vision/window")
    except Exception:
        window_info = {}
    ocr_data = {}
    ocr_text = ""
    ocr_monitor = monitor
    if ocr_monitor is None and target is not None:
        try:
            ocr_monitor = int(target.get("monitor_id", 0) or 0) or None
        except Exception:
            ocr_monitor = None
    if include_ocr:
        try:
            query = "/vision/ocr"
            if ocr_monitor is not None:
                query += f"?monitor_id={int(ocr_monitor)}"
            ocr_data = _api_get(query)
            ocr_text = str(ocr_data.get("text", "") or "")
        except Exception:
            ocr_data = {}
            ocr_text = ""

    # 4.5) INTERRUPT HANDLING
    interrupt_detect = _detect_blocking_interrupt(
        ocr_text=ocr_text,
        active_title=str(window_info.get("title", "") or ""),
        target_title=str(target.get("title", "") if isinstance(target, dict) else ""),
    )
    interrupt_resolution = {"resolved": False, "method": "not_attempted"}
    if interrupt_detect.get("detected") and resolve_interrupts:
        interrupt_resolution = _resolve_blocking_interrupt(
            strategy=interrupt_strategy,
            focus_handle=focus_handle,
            detect=interrupt_detect,
        )
        if interrupt_resolution.get("resolved"):
            # Re-read context after resolving modal.
            time.sleep(0.16)
            try:
                window_info = _api_get("/vision/window")
            except Exception:
                pass  # noqa: suppressed Exception
            if include_ocr:
                try:
                    query = "/vision/ocr"
                    if ocr_monitor is not None:
                        query += f"?monitor_id={int(ocr_monitor)}"
                    ocr_data = _api_get(query)
                    ocr_text = str(ocr_data.get("text", "") or "")
                except Exception:
                    pass  # noqa: suppressed Exception
            # Re-localize intended target window after interrupt was handled.
            if target_window:
                target2 = _resolve_window_by_query(target_window, prefer_active_monitor=True)
                if isinstance(target2, dict):
                    target = target2
                    try:
                        focus_handle = int(target.get("handle", 0) or 0) or focus_handle
                    except Exception:
                        pass  # noqa: suppressed Exception

    # 5) ACTION
    action_kind = "none"
    action_result = None
    if action is not None and (not interrupt_detect.get("detected") or interrupt_resolution.get("resolved") or (not resolve_interrupts)):
        action_kind, action_result = _execute_cycle_action(action, focus_handle, monitor_hint=ocr_monitor)
    elif action is not None and interrupt_detect.get("detected") and resolve_interrupts and (not interrupt_resolution.get("resolved")):
        action_kind = str(action.get("kind") or action.get("type") or "none")
        action_result = {"success": False, "message": "blocking_interrupt_unresolved"}

    # 6) VERIFICATION
    verified = None
    verify_reason = "not_requested"
    if verify_contains:
        if not ocr_text:
            try:
                query = "/vision/ocr"
                if ocr_monitor is not None:
                    query += f"?monitor_id={int(ocr_monitor)}"
                ocr_data = _api_get(query)
                ocr_text = str(ocr_data.get("text", "") or "")
            except Exception:
                ocr_text = ""
        verified = verify_contains.lower() in ocr_text.lower()
        verify_reason = "ocr_contains"
        if not verified:
            # Fallback: verify against current target/visible window titles.
            vc = verify_contains.lower()
            try:
                if isinstance(target, dict):
                    ttitle = str(target.get("title", "")).lower()
                    if vc in ttitle:
                        verified = True
                        verify_reason = "target_window_title_contains"
                if not verified:
                    windows_now = _api_get("/windows/list?visible_only=true&exclude_minimized=true").get("windows", [])
                    for w in windows_now:
                        if vc in str(w.get("title", "")).lower():
                            verified = True
                            verify_reason = "visible_window_title_contains"
                            break
            except Exception:
                pass  # noqa: suppressed Exception
    elif action_result is not None:
        verified = bool(action_result.get("success"))
        verify_reason = "action_success_flag"

    target_title = (target.get("title", "")[:90] if isinstance(target, dict) else "")
    read_excerpt = (ocr_text or "").replace("\n", " ").strip()[:220]
    lines = [
        "## Operate Cycle",
        f"Goal: {goal or 'n/a'}",
        f"1) Orientation: monitors={monitor_count} active_monitor={active_monitor} active_window='{active.get('title', '')[:90]}'",
        (
            f"2) Localization: strategy={locate_strategy} "
            f"target_handle={focus_handle if focus_handle is not None else '?'} "
            f"target_monitor={target.get('monitor_id', '?') if isinstance(target, dict) else '?'} "
            f"target_title='{target_title or 'n/a'}'"
        ),
        (
            f"3) Focus: {'OK' if focus_ok else 'SKIPPED/FAILED'} "
            f"(strategy={focus_strategy})"
            f"{(' (' + focus_error + ')') if focus_error else ''}"
        ),
        (
            f"4) Read: window='{window_info.get('title', '')[:90]}' "
            f"ocr_chars={len(ocr_text)} excerpt='{read_excerpt}'"
        ),
        (
            f"4.5) Interrupt: detected={interrupt_detect.get('detected')} "
            f"resolved={interrupt_resolution.get('resolved')} "
            f"method={interrupt_resolution.get('method', 'n/a')} "
            f"strategy={interrupt_strategy if resolve_interrupts else 'disabled'}"
        ),
        (
            f"5) Action: kind={action_kind} "
            f"status={('OK' if (action_result and action_result.get('success')) else ('SKIPPED' if action_result is None else 'FAILED'))} "
            f"msg='{(action_result or {}).get('message', '')[:120] if isinstance(action_result, dict) else ''}'"
        ),
        f"6) Verification: {verified if verified is not None else 'n/a'} via {verify_reason}",
    ]
    return [{"type": "text", "text": "\n".join(lines)}]


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
    monitor = args.get("monitor")
    title_contains = (args.get("title_contains") or "").strip()
    exclude_minimized = bool(args.get("exclude_minimized", True))
    query = "/windows/list?visible_only=true"
    if monitor is not None:
        query += f"&monitor_id={int(monitor)}"
    if title_contains:
        query += f"&title_contains={urllib.parse.quote(title_contains)}"
    if exclude_minimized:
        query += "&exclude_minimized=true"
    data = _api_get(query)
    windows = data.get("windows", [])
    lines = [f"## Windows ({len(windows)})"]
    for w in windows[:30]:
        lines.append(
            f"- h={w.get('handle')} | m={w.get('monitor_id', '?')} | "
            f"**{w.get('title', '?')[:80]}** "
            f"({w.get('x')},{w.get('y')}, {w.get('width')}x{w.get('height')}, "
            f"min={w.get('is_minimized', False)})"
        )
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_window_minimize(args: dict) -> list:
    handle = args.get("handle")
    title = (args.get("title") or "").strip()
    if handle is None and not title:
        return [{"type": "text", "text": "Error: handle or title is required"}]
    if handle is not None:
        data = _api_post(f"/windows/minimize?handle={int(handle)}")
        target = f"handle={int(handle)}"
    else:
        data = _api_post(f"/windows/minimize?title={urllib.parse.quote(title)}")
        target = f"title='{title}'"
    return [{"type": "text", "text": f"Minimize {target}: {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_window_maximize(args: dict) -> list:
    handle = args.get("handle")
    title = (args.get("title") or "").strip()
    if handle is None and not title:
        return [{"type": "text", "text": "Error: handle or title is required"}]
    if handle is not None:
        data = _api_post(f"/windows/maximize?handle={int(handle)}")
        target = f"handle={int(handle)}"
    else:
        data = _api_post(f"/windows/maximize?title={urllib.parse.quote(title)}")
        target = f"title='{title}'"
    return [{"type": "text", "text": f"Maximize {target}: {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_window_close(args: dict) -> list:
    handle = args.get("handle")
    title = (args.get("title") or "").strip()
    if handle is None and not title:
        return [{"type": "text", "text": "Error: handle or title is required"}]
    if handle is not None:
        data = _api_post(f"/windows/close?handle={int(handle)}")
        target = f"handle={int(handle)}"
    else:
        data = _api_post(f"/windows/close?title={urllib.parse.quote(title)}")
        target = f"title='{title}'"
    return [{"type": "text", "text": f"Close {target}: {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_move_window(args: dict) -> list:
    handle = args.get("handle")
    title = (args.get("title") or "").strip()
    if handle is None and not title:
        return [{"type": "text", "text": "Error: handle or title is required"}]
    x = int(args.get("x", 0))
    y = int(args.get("y", 0))
    width = int(args.get("width", -1))
    height = int(args.get("height", -1))
    monitor = args.get("monitor")
    coord_space = str(args.get("coord_space", "global")).strip().lower()
    if handle is not None:
        path = f"/windows/move?handle={int(handle)}&x={x}&y={y}&width={width}&height={height}"
        target = f"handle={int(handle)}"
    else:
        path = (
            f"/windows/move?title={urllib.parse.quote(title)}"
            f"&x={x}&y={y}&width={width}&height={height}"
        )
        target = f"title='{title}'"
    if monitor is not None:
        path += f"&monitor_id={int(monitor)}"
    if coord_space in {"monitor", "local", "monitor_local"}:
        path += "&relative_to_monitor=true"
    data = _api_post(path)
    return [{
        "type": "text",
        "text": (
            f"Move {target} -> ({x},{y}) {width}x{height}: "
            f"{'SUCCESS' if data.get('success') else 'FAILED'} "
            f"(space={coord_space}, monitor={monitor if monitor is not None else 'auto'})"
        ),
    }]


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
    handle = args.get("handle")
    title = (args.get("title") or "").strip()
    prefer_active_monitor = _as_bool(args.get("prefer_active_monitor"), True)
    if handle is None and not title:
        return [{"type": "text", "text": "Error: handle or title is required"}]

    if handle is not None:
        data = _api_post(f"/windows/focus?handle={int(handle)}")
        if data.get("success"):
            return [{"type": "text", "text": f"Focused window handle={int(handle)}"}]
        return [{"type": "text", "text": f"Could not focus window handle={int(handle)}."}]

    # Global contextual ranking first (active monitor + fuzzy title).
    resolved = _resolve_window_by_query(title, prefer_active_monitor=prefer_active_monitor)
    if resolved is not None:
        h = int(resolved.get("handle", 0) or 0)
        if h:
            data = _api_post(f"/windows/focus?handle={h}")
            if data.get("success"):
                return [{
                    "type": "text",
                    "text": (
                        f"Focused window: '{resolved.get('title', title)}' "
                        f"(handle={h}, monitor={resolved.get('monitor_id', '?')}, "
                        f"strategy=context_ranked)"
                    ),
                }]

    # Fallback: backend partial/exact match by title.
    data = _api_post(f"/windows/focus?title={urllib.parse.quote(title)}")
    if data.get("success"):
        return [{"type": "text", "text": f"Focused window: '{title}' (strategy=backend_match)"}]

    return [{"type": "text", "text": f"Could not focus window '{title}'. Use list_windows to inspect candidates."}]


def handle_browser_navigate(args: dict) -> list:
    raw_url = args.get("url", "")
    url = _normalize_url(str(raw_url))
    if not url:
        return [{"type": "text", "text": "Error: url is required"}]

    new_tab = _as_bool(args.get("new_tab"), True)
    preserve_context = _as_bool(args.get("preserve_context"), True)
    preferred_browser = str(args.get("browser", "auto")).strip().lower()
    if preferred_browser not in {"auto", "brave", "chrome", "edge", "firefox"}:
        preferred_browser = "auto"

    started = time.time()
    method = "none"
    notes = []

    # Path A: human-like path (minimal impact) using current desktop context.
    if preserve_context:
        active, windows = _get_windows_context()
        target, reason = _select_browser_window(active, windows, preferred_browser)
        notes.append(f"context={reason}")
        if target is not None:
            handle = int(target.get("handle", 0) or 0) or None
            title = (target.get("title") or "?")[:80]
            if handle is not None:
                try:
                    _api_post(f"/windows/focus?handle={handle}")
                    time.sleep(0.08)
                except Exception as e:
                    notes.append(f"focus_warn={e}")
            kb_ok, kb_method = _navigate_with_keyboard(url, handle, new_tab)
            if kb_ok:
                method = f"context_preserving_{kb_method}"
                elapsed_ms = int((time.time() - started) * 1000)
                return [{
                    "type": "text",
                    "text": (
                        f"Navigated to: {url}\n"
                        f"Method: {method}\n"
                        f"Target window: h={handle if handle is not None else '?'} title='{title}'\n"
                        f"Elapsed: {elapsed_ms}ms\n"
                        f"Notes: {', '.join(notes)}"
                    ),
                }]
            notes.append(kb_method)

    # Path B: CDP direct tab creation/navigate.
    if new_tab:
        try:
            data = _api_post(f"/browser/new_tab?url={urllib.parse.quote(url)}")
            if data.get("success"):
                method = "cdp_new_tab"
                elapsed_ms = int((time.time() - started) * 1000)
                return [{
                    "type": "text",
                    "text": (
                        f"Navigated to: {url}\n"
                        f"Method: {method}\n"
                        f"Elapsed: {elapsed_ms}ms\n"
                        f"Notes: {', '.join(notes) if notes else 'n/a'}"
                    ),
                }]
            notes.append("cdp_new_tab_unsuccessful")
        except Exception as e:
            notes.append(f"cdp_new_tab_error={e}")

    try:
        data = _api_post(f"/browser/navigate?url={urllib.parse.quote(url)}")
        if data.get("success") or data.get("status") == "ok":
            method = "cdp_navigate"
            elapsed_ms = int((time.time() - started) * 1000)
            return [{
                "type": "text",
                "text": (
                    f"Navigated to: {url}\n"
                    f"Method: {method}\n"
                    f"Elapsed: {elapsed_ms}ms\n"
                    f"Notes: {', '.join(notes) if notes else 'n/a'}"
                ),
            }]
        notes.append("cdp_navigate_unsuccessful")
    except Exception as e:
        notes.append(f"cdp_navigate_error={e}")

    # Path C: launch browser/URL, then try keyboard fallback.
    launched, launch_method = _launch_browser_or_url(url, preferred_browser)
    notes.append(launch_method)
    if launched:
        time.sleep(1.2)
        active2, windows2 = _get_windows_context()
        target2, reason2 = _select_browser_window(active2, windows2, preferred_browser)
        notes.append(f"post_launch_context={reason2}")
        handle2 = None
        if target2 is not None:
            handle2 = int(target2.get("handle", 0) or 0) or None
            if handle2 is not None:
                try:
                    _api_post(f"/windows/focus?handle={handle2}")
                    time.sleep(0.10)
                except Exception as e:
                    notes.append(f"post_launch_focus_warn={e}")
        kb_ok, kb_method = _navigate_with_keyboard(url, handle2, new_tab=False)
        notes.append(kb_method)
        if kb_ok:
            method = "launch_then_keyboard"
            elapsed_ms = int((time.time() - started) * 1000)
            return [{
                "type": "text",
                "text": (
                    f"Navigated to: {url}\n"
                    f"Method: {method}\n"
                    f"Elapsed: {elapsed_ms}ms\n"
                    f"Notes: {', '.join(notes)}"
                ),
            }]

    elapsed_ms = int((time.time() - started) * 1000)
    return [{
        "type": "text",
        "text": (
            f"Failed to navigate: {url}\n"
            f"Method: all_fallbacks_exhausted\n"
            f"Elapsed: {elapsed_ms}ms\n"
            f"Notes: {', '.join(notes) if notes else 'n/a'}"
        ),
    }]


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
    monitor = args.get("monitor")
    coord_space = str(args.get("coord_space", "global")).strip().lower()
    focus_title = (args.get("focus_title") or "").strip()
    focus_handle = args.get("focus_handle")
    query = f"/action/click?x={x}&y={y}&button={urllib.parse.quote(str(button))}"
    if monitor is not None:
        query += f"&monitor_id={int(monitor)}"
    if coord_space in {"monitor", "local", "monitor_local"}:
        query += "&relative_to_monitor=true"
    if focus_handle is not None:
        query += f"&focus_handle={int(focus_handle)}"
    elif focus_title:
        query += f"&focus_title={urllib.parse.quote(focus_title)}"
    data = _api_post(query)
    rx = data.get("resolved_x", x)
    ry = data.get("resolved_y", y)
    return [{
        "type": "text",
        "text": (
            f"Clicked at ({x},{y}) {button}: {'SUCCESS' if data.get('success') else 'FAILED'} "
            f"(resolved=({rx},{ry}), space={coord_space}, monitor={monitor if monitor is not None else 'auto'}, "
            f"focus={'handle='+str(focus_handle) if focus_handle is not None else (focus_title or 'none')})"
        ),
    }]


def handle_drag_screen(args: dict) -> list:
    start_x = int(args.get("start_x", 0))
    start_y = int(args.get("start_y", 0))
    end_x = int(args.get("end_x", 0))
    end_y = int(args.get("end_y", 0))
    duration = float(args.get("duration", 0.35))
    monitor = args.get("monitor")
    coord_space = str(args.get("coord_space", "global")).strip().lower()
    focus_title = (args.get("focus_title") or "").strip()
    focus_handle = args.get("focus_handle")
    query = (
        f"/action/drag?start_x={start_x}&start_y={start_y}"
        f"&end_x={end_x}&end_y={end_y}&duration={duration}"
    )
    if monitor is not None:
        query += f"&monitor_id={int(monitor)}"
    if coord_space in {"monitor", "local", "monitor_local"}:
        query += "&relative_to_monitor=true"
    if focus_handle is not None:
        query += f"&focus_handle={int(focus_handle)}"
    elif focus_title:
        query += f"&focus_title={urllib.parse.quote(focus_title)}"
    data = _api_post(query)
    return [{
        "type": "text",
        "text": (
            f"Dragged ({start_x},{start_y}) -> ({end_x},{end_y}): "
            f"{'SUCCESS' if data.get('success') else 'FAILED'} "
            f"(space={coord_space}, monitor={monitor if monitor is not None else 'auto'}, "
            f"focus={'handle='+str(focus_handle) if focus_handle is not None else (focus_title or 'none')})"
        ),
    }]


def handle_keyboard(args: dict) -> list:
    keys = args.get("keys", "")
    focus_title = (args.get("focus_title") or "").strip()
    focus_handle = args.get("focus_handle")
    if not keys:
        return [{"type": "text", "text": "Error: keys is required"}]
    query = f"/action/hotkey?keys={urllib.parse.quote(keys)}"
    if focus_handle is not None:
        query += f"&focus_handle={int(focus_handle)}"
    elif focus_title:
        query += f"&focus_title={urllib.parse.quote(focus_title)}"
    data = _api_post(query)
    return [{
        "type": "text",
        "text": (
            f"Pressed {keys}: {'SUCCESS' if data.get('success') else 'FAILED'} "
            f"(focus={'handle='+str(focus_handle) if focus_handle is not None else (focus_title or 'none')})"
        ),
    }]


def handle_act(args: dict) -> list:
    """
    Direct action executor — no middleware, no grounding, no intent classifier.
    Claude sees the screen, decides what to do, and tells ILUMINATY exactly.
    Supports: click, type, key, scroll, focus, move_mouse
    """
    action = str(args.get("action", "")).strip().lower()
    if not action:
        return [{"type": "text", "text": "Error: action is required (click/type/key/scroll/focus/move_mouse)"}]

    try:
        if action == "click":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            button = args.get("button", "left")
            query = f"/action/click?x={x}&y={y}&button={urllib.parse.quote(str(button))}"
            if args.get("monitor") is not None:
                query += f"&monitor_id={int(args['monitor'])}&relative_to_monitor=true"
            data = _api_post(query)
            ok = data.get("success", False)
            return [{"type": "text", "text": f"click ({x},{y}) {button}: {'OK' if ok else 'FAIL'} {data.get('message','')}"}]

        elif action == "double_click":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            data = _api_post(f"/action/double_click?x={x}&y={y}")
            ok = data.get("success", False)
            return [{"type": "text", "text": f"double_click ({x},{y}): {'OK' if ok else 'FAIL'}"}]

        elif action == "type":
            text = str(args.get("text", ""))
            if not text:
                return [{"type": "text", "text": "Error: text is required"}]
            data = _api_post(f"/action/type?text={urllib.parse.quote(text)}")
            ok = data.get("success", False)
            return [{"type": "text", "text": f"typed {len(text)} chars: {'OK' if ok else 'FAIL'}"}]

        elif action == "key":
            keys = str(args.get("keys", ""))
            if not keys:
                return [{"type": "text", "text": "Error: keys is required (e.g. 'enter', 'ctrl+s', 'win+r')"}]
            data = _api_post(f"/action/hotkey?keys={urllib.parse.quote(keys)}")
            ok = data.get("success", False)
            return [{"type": "text", "text": f"key {keys}: {'OK' if ok else 'FAIL'}"}]

        elif action == "scroll":
            amount = int(args.get("amount", 3))
            query = f"/action/scroll?amount={amount}"
            if args.get("x") is not None and args.get("y") is not None:
                query += f"&x={int(args['x'])}&y={int(args['y'])}"
            data = _api_post(query)
            ok = data.get("success", False)
            return [{"type": "text", "text": f"scroll {'down' if amount > 0 else 'up'} {abs(amount)}: {'OK' if ok else 'FAIL'}"}]

        elif action == "focus":
            title = str(args.get("title", ""))
            handle = args.get("handle")
            if handle is not None:
                data = _api_post(f"/windows/focus?handle={int(handle)}")
            elif title:
                data = _api_post(f"/windows/focus?title={urllib.parse.quote(title)}")
            else:
                return [{"type": "text", "text": "Error: title or handle required"}]
            ok = data.get("success", False)
            return [{"type": "text", "text": f"focus '{title or handle}': {'OK' if ok else 'FAIL'}"}]

        elif action == "move_mouse":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            data = _api_post(f"/action/move?x={x}&y={y}")
            ok = data.get("success", False)
            return [{"type": "text", "text": f"move_mouse ({x},{y}): {'OK' if ok else 'FAIL'}"}]

        else:
            return [{"type": "text", "text": f"Unknown action: {action}. Use: click, double_click, type, key, scroll, focus, move_mouse"}]

    except Exception as e:
        return [{"type": "text", "text": f"act failed: {e}"}]


def handle_scroll(args: dict) -> list:
    amount = args.get("amount", 3)
    x = args.get("x")
    y = args.get("y")
    monitor = args.get("monitor")
    coord_space = str(args.get("coord_space", "global")).strip().lower()
    focus_title = (args.get("focus_title") or "").strip()
    focus_handle = args.get("focus_handle")
    query = f"/action/scroll?amount={amount}"
    if x is not None:
        query += f"&x={int(x)}"
    if y is not None:
        query += f"&y={int(y)}"
    if monitor is not None:
        query += f"&monitor_id={int(monitor)}"
    if coord_space in {"monitor", "local", "monitor_local"}:
        query += "&relative_to_monitor=true"
    if focus_handle is not None:
        query += f"&focus_handle={int(focus_handle)}"
    elif focus_title:
        query += f"&focus_title={urllib.parse.quote(focus_title)}"
    data = _api_post(query)
    direction = "down" if amount > 0 else "up"
    return [{
        "type": "text",
        "text": (
            f"Scrolled {direction} ({abs(amount)}): {'SUCCESS' if data.get('success') else 'FAILED'} "
            f"(space={coord_space}, monitor={monitor if monitor is not None else 'auto'}, "
            f"focus={'handle='+str(focus_handle) if focus_handle is not None else (focus_title or 'none')})"
        ),
    }]


def handle_monitor_info(args: dict) -> list:
    try:
        monitors = _api_get("/monitors/info")
        lines = ["## Monitor Layout"]
        for m in monitors.get("monitors", []):
            lines.append(
                f"- **Monitor {m.get('id')}**: {m.get('resolution')} at {m.get('position')} "
                f"(active={m.get('active')}, primary={m.get('primary')})"
            )

        windows = _api_get("/windows/list?visible_only=true&exclude_minimized=true")
        items = windows.get("windows", [])
        if items:
            lines.append("\n## Windows by Monitor")
            for w in items[:80]:
                lines.append(
                    f"- m{w.get('monitor_id', '?')} | h={w.get('handle')} | "
                    f"**{w.get('title', '?')[:70]}** "
                    f"({w.get('x')},{w.get('y')} {w.get('width')}x{w.get('height')})"
                )
        return [{"type": "text", "text": "\n".join(lines)}]
    except Exception as e:
        return [{"type": "text", "text": f"Error getting monitor info: {e}"}]


def handle_see_monitor(args: dict) -> list:
    monitor = args.get("monitor", 1)
    mode = args.get("mode", "medium_res")
    try:
        data = _api_get(f"/vision/smart?mode={urllib.parse.quote(str(mode))}&monitor_id={int(monitor)}")
        monitors = _api_get("/monitors/info")
        mon_meta = next((m for m in monitors.get("monitors", []) if int(m.get("id", -1)) == int(monitor)), None)
        active = _api_get("/windows/active") or {}
        text = (
            f"## Monitor {monitor}\n"
            f"**Resolution**: {mon_meta.get('resolution', '?') if mon_meta else '?'}\n"
            f"**Position**: {mon_meta.get('position', '?') if mon_meta else '?'}\n"
            f"**Active Window**: {active.get('title', '?')}\n"
            f"**Captured monitor_id**: {data.get('monitor_id', '?')}\n"
            f"**Click Mapping**: use `click_screen(x, y, monitor={monitor}, coord_space='monitor')`.\n"
        )
        if "image_base64" not in data:
            return [{"type": "text", "text": text + "\nNo image payload in this mode. Use mode=low_res|medium_res|full_res."}]
        return [
            {"type": "text", "text": text},
            {"type": "image", "data": data["image_base64"], "mimeType": "image/webp"},
        ]
    except Exception as e:
        return [{"type": "text", "text": f"Error capturing monitor {monitor}: {e}"}]


def handle_spatial_state(args: dict) -> list:
    include_windows = bool(args.get("include_windows", True))
    try:
        data = _api_get(f"/spatial/state?include_windows={'true' if include_windows else 'false'}")
    except Exception as e:
        return [{"type": "text", "text": f"Spatial state unavailable: {e}"}]
    lines = [
        "## Spatial State",
        f"- Monitor count: {data.get('monitor_count', 0)}",
        f"- Active monitor: {data.get('active_monitor_id', '?')}",
    ]
    active = data.get("active_window", {}) or {}
    if active:
        lines.append(
            f"- Active window: h={active.get('handle')} m={active.get('monitor_id', '?')} "
            f"{active.get('title', '')[:100]}"
        )
    cursor = data.get("cursor", {}) or {}
    if cursor:
        lines.append(f"- Cursor: ({cursor.get('x')},{cursor.get('y')})")
    for mon in data.get("monitors", []):
        lines.append(
            f"- Monitor {mon.get('id')}: ({mon.get('left')},{mon.get('top')}) "
            f"{mon.get('width')}x{mon.get('height')} active={mon.get('is_active')}"
        )
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_workers_status(args: dict) -> list:
    _ = args
    try:
        data = _api_get("/workers/status")
    except Exception as e:
        return [{"type": "text", "text": f"Workers status unavailable: {e}"}]

    workers = data.get("workers", {}) or {}
    arbiter = data.get("arbiter", {}) or {}
    lines = [
        "## Workers Status",
        f"- Enabled: {data.get('enabled', False)}",
        f"- Active monitor: {data.get('active_monitor_id', 0)}",
        f"- Monitor count: {data.get('monitor_count', 0)}",
        f"- Arbiter owner: {arbiter.get('owner')}",
        f"- Arbiter denied: {arbiter.get('denied_count', 0)}",
        f"- Arbiter lease remaining: {arbiter.get('lease_remaining_ms', 0)}ms",
    ]
    for name, info in workers.items():
        lines.append(
            f"- Worker[{name}]: processed={info.get('processed', 0)} "
            f"errors={info.get('errors', 0)} "
            f"avg_ms={info.get('avg_latency_ms', 0)} "
            f"staleness_ms={info.get('staleness_ms', 0)}"
        )
    monitors = data.get("monitors", []) or []
    if monitors:
        lines.append("### Monitor Digests")
        for mon in monitors[:8]:
            lines.append(
                f"- m{mon.get('monitor_id')}: scene={mon.get('scene_state')} "
                f"phase={mon.get('task_phase')} ready={mon.get('readiness')} "
                f"stale={mon.get('staleness_ms')}ms"
            )
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_workers_monitor(args: dict) -> list:
    monitor = args.get("monitor")
    if monitor is None:
        return [{"type": "text", "text": "Error: monitor is required"}]
    try:
        mid = int(monitor)
    except Exception:
        return [{"type": "text", "text": "Error: monitor must be an integer"}]

    try:
        data = _api_get(f"/workers/monitor/{mid}")
    except Exception as e:
        return [{"type": "text", "text": f"Worker monitor {mid} unavailable: {e}"}]

    lines = [
        f"## Worker Monitor {mid}",
        f"- Tick: {data.get('tick_id', 0)}",
        f"- Scene: {data.get('scene_state', 'unknown')} (conf={data.get('scene_confidence', 0)})",
        f"- Change: {data.get('change_score', 0)}",
        f"- Direction: {data.get('dominant_direction', 'none')}",
        f"- Phase: {data.get('task_phase', 'unknown')}",
        f"- Surface: {data.get('active_surface', 'unknown')}",
        f"- Readiness: {data.get('readiness', False)} | uncertainty={data.get('uncertainty', 1.0)}",
        f"- Staleness: {data.get('staleness_ms', 0)}ms",
    ]
    targets = data.get("attention_targets", []) or []
    if targets:
        lines.append(f"- Attention: {', '.join([str(t) for t in targets[:6]])}")
    vf = data.get("visual_facts", []) or []
    if vf:
        lines.append(f"- Visual facts: {len(vf)}")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_workers_claim_action(args: dict) -> list:
    owner = str(args.get("owner") or "mcp-executor").strip() or "mcp-executor"
    body = {
        "owner": owner,
        "ttl_ms": args.get("ttl_ms"),
        "force": bool(args.get("force", False)),
    }
    try:
        data = _api_post("/workers/action/claim", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Workers claim failed: {e}"}]
    return [{
        "type": "text",
        "text": (
            f"Workers claim owner={owner}: {'GRANTED' if data.get('granted') else 'DENIED'} "
            f"(held_by={data.get('held_by')}, ttl_ms={data.get('ttl_ms')}, reason={data.get('reason')})"
        ),
    }]


def handle_workers_release_action(args: dict) -> list:
    owner = str(args.get("owner") or "mcp-executor").strip() or "mcp-executor"
    body = {
        "owner": owner,
        "success": bool(args.get("success", True)),
        "message": str(args.get("message") or ""),
    }
    try:
        data = _api_post("/workers/action/release", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Workers release failed: {e}"}]
    return [{
        "type": "text",
        "text": (
            f"Workers release owner={owner}: {'OK' if data.get('released') else 'FAILED'} "
            f"(success={data.get('success')}, msg={data.get('message', '')})"
        ),
    }]


def handle_workers_schedule(args: dict) -> list:
    _ = args
    try:
        data = _api_get("/workers/schedule")
    except Exception as e:
        return [{"type": "text", "text": f"Workers schedule unavailable: {e}"}]

    lines = [
        "## Workers Schedule",
        f"- Active monitor: {data.get('active_monitor_id', 0)}",
        f"- Recommended monitor: {data.get('recommended_monitor_id', 0)}",
        f"- Reason: {data.get('reason', 'n/a')}",
    ]
    for row in (data.get("budgets") or [])[:8]:
        lines.append(
            f"- m{row.get('monitor_id')}: share={row.get('share')} score={row.get('score')}"
        )
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_workers_set_subgoal(args: dict) -> list:
    monitor_id = args.get("monitor_id")
    goal = str(args.get("goal") or "").strip()
    if monitor_id is None or not goal:
        return [{"type": "text", "text": "Error: monitor_id and goal are required"}]
    body = {
        "monitor_id": int(monitor_id),
        "goal": goal,
        "priority": float(args.get("priority", 0.5)),
        "risk": str(args.get("risk", "normal")),
    }
    if args.get("deadline_ms") is not None:
        body["deadline_ms"] = int(args.get("deadline_ms"))
    try:
        data = _api_post("/workers/subgoals", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Set workers subgoal failed: {e}"}]
    return [{"type": "text", "text": f"Workers subgoal set: {data.get('subgoal_id', 'unknown')} monitor={data.get('monitor_id')} goal={data.get('goal', '')}"}]


def handle_workers_clear_subgoal(args: dict) -> list:
    subgoal_id = str(args.get("subgoal_id") or "").strip()
    if not subgoal_id:
        return [{"type": "text", "text": "Error: subgoal_id is required"}]
    completed = _as_bool(args.get("completed"), True)
    try:
        data = _api_request("DELETE", f"/workers/subgoals/{urllib.parse.quote(subgoal_id)}?completed={'true' if completed else 'false'}")
    except Exception as e:
        return [{"type": "text", "text": f"Clear workers subgoal failed: {e}"}]
    return [{"type": "text", "text": f"Workers subgoal cleared: {subgoal_id} (completed={completed})"}]


def handle_workers_route(args: dict) -> list:
    query = str(args.get("query") or "").strip()
    if not query:
        return [{"type": "text", "text": "Error: query is required"}]
    body = {"query": query}
    if args.get("preferred_monitor_id") is not None:
        body["preferred_monitor_id"] = int(args.get("preferred_monitor_id"))
    try:
        data = _api_post("/workers/route", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Workers route failed: {e}"}]
    return [{"type": "text", "text": f"Workers route => monitor {data.get('monitor_id')} (score={data.get('score')})"}]


def handle_behavior_stats(args: dict) -> list:
    _ = args
    try:
        data = _api_get("/behavior/stats")
    except Exception as e:
        return [{"type": "text", "text": f"Behavior stats unavailable: {e}"}]
    return [{
        "type": "text",
        "text": (
            "## Behavior Cache\n"
            f"- Enabled: {data.get('enabled', False)}\n"
            f"- Entries: {data.get('entries', 0)}\n"
            f"- Success rate: {data.get('success_rate', 0)}\n"
            f"- Recovery rate: {data.get('recovery_rate', 0)}\n"
            f"- Distinct apps: {data.get('distinct_apps', 0)}"
        ),
    }]


def handle_behavior_recent(args: dict) -> list:
    limit = int(args.get("limit", 20))
    try:
        data = _api_get(f"/behavior/recent?limit={max(1, min(200, limit))}")
    except Exception as e:
        return [{"type": "text", "text": f"Behavior recent unavailable: {e}"}]
    entries = data.get("entries", []) if isinstance(data, dict) else []
    lines = ["## Behavior Recent"]
    for row in entries[:12]:
        lines.append(
            f"- {row.get('action')} | {row.get('app_name')} | success={row.get('success')} "
            f"| method={row.get('method_used')} | reason={str(row.get('reason', ''))[:90]}"
        )
    if len(lines) == 1:
        lines.append("- No entries")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_behavior_suggest(args: dict) -> list:
    action = str(args.get("action") or "").strip()
    if not action:
        return [{"type": "text", "text": "Error: action is required"}]
    body = {
        "action": action,
        "app_name": str(args.get("app_name") or "unknown"),
        "window_title": str(args.get("window_title") or ""),
    }
    try:
        data = _api_post("/behavior/suggest", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Behavior suggest failed: {e}"}]
    return [{"type": "text", "text": f"Behavior suggest: found={data.get('found')} success_rate={data.get('success_rate')} retries={data.get('recommended_retries')} pre_delay_ms={data.get('recommended_pre_delay_ms')} focus_before_action={data.get('focus_before_action')}"}]


def handle_runtime_profile(args: dict) -> list:
    target = args.get("set")
    try:
        if target is None:
            data = _api_get("/runtime/profile")
        else:
            data = _api_post("/runtime/profile", body={"profile": str(target)})
    except Exception as e:
        return [{"type": "text", "text": f"Runtime profile API failed: {e}"}]
    return [{"type": "text", "text": f"Runtime profile: {data.get('profile')} | policy={data.get('policy')}"}]


def handle_host_telemetry(args: dict) -> list:
    _ = args
    try:
        data = _api_get("/system/telemetry")
    except Exception as e:
        return [{"type": "text", "text": f"Host telemetry unavailable: {e}"}]
    if not bool(data.get("available", False)):
        return [{"type": "text", "text": "Host telemetry: unavailable"}]
    gpu = data.get("gpu") or {}
    temps = data.get("temperatures") or {}
    return [{
        "type": "text",
        "text": (
            "## Host Telemetry\n"
            f"- CPU: {data.get('cpu_percent')}%\n"
            f"- Memory: {data.get('memory_percent')}%\n"
            f"- Swap: {data.get('swap_percent')}%\n"
            f"- Disk: {data.get('disk_percent')}%\n"
            f"- Temp max: {temps.get('max_c')}\n"
            f"- GPU util: {gpu.get('utilization_percent')}\n"
            f"- Overloaded: {data.get('overloaded', False)} {data.get('overload_reasons', [])}"
        ),
    }]


def handle_os_notifications(args: dict) -> list:
    limit = int(args.get("limit", 20))
    limit = max(1, min(200, limit))
    try:
        data = _api_get(f"/os/notifications?limit={limit}")
    except Exception as e:
        return [{"type": "text", "text": f"OS notifications unavailable: {e}"}]
    lines = [
        "## OS Notifications",
        f"- Count: {data.get('count', 0)}",
        f"- Sources: {', '.join(data.get('sources', [])) if data.get('sources') else 'none'}",
    ]
    for item in (data.get("items") or [])[:12]:
        lines.append(
            f"- [{item.get('source')}] {item.get('severity', 'info')} "
            f"{item.get('timestamp_ms', 0)}: {str(item.get('message', ''))[:120]}"
        )
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_os_tray(args: dict) -> list:
    _ = args
    try:
        data = _api_get("/os/tray")
    except Exception as e:
        return [{"type": "text", "text": f"OS tray unavailable: {e}"}]
    lines = [
        "## OS Tray",
        f"- Available: {data.get('available', False)}",
        f"- Supported: {data.get('supported', False)}",
        f"- Detected: {data.get('detected', False)}",
        f"- Platform: {data.get('platform', 'unknown')}",
    ]
    for win in (data.get("windows") or [])[:8]:
        lines.append(
            f"- {win.get('class_name')}: h={win.get('handle')} "
            f"({win.get('x')},{win.get('y')}) {win.get('width')}x{win.get('height')}"
        )
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_os_dialog_status(args: dict) -> list:
    monitor_id = args.get("monitor_id")
    path = "/os/dialog/status"
    if monitor_id is not None:
        path += f"?monitor_id={int(monitor_id)}"
    try:
        data = _api_get(path)
    except Exception as e:
        return [{"type": "text", "text": f"OS dialog status unavailable: {e}"}]
    return [{
        "type": "text",
        "text": (
            f"OS dialog detected={data.get('detected', False)} conf={data.get('confidence', 0)} "
            f"monitor={data.get('monitor_id')} affordances={data.get('affordances', [])} "
            f"title={str(data.get('active_title', ''))[:80]}"
        ),
    }]


def handle_os_dialog_resolve(args: dict) -> list:
    body = {}
    for key in ("label", "x", "y", "monitor_id", "mode", "verify", "context_tick_id", "max_staleness_ms"):
        if key in args and args[key] is not None:
            body[key] = args[key]
    if not body:
        return [{"type": "text", "text": "Error: provide label and/or x,y coordinates"}]
    try:
        data = _api_post("/os/dialog/resolve", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"OS dialog resolve failed: {e}"}]
    return [{
        "type": "text",
        "text": (
            f"OS dialog resolve: {'SUCCESS' if data.get('resolved') else 'FAILED'} "
            f"reason={data.get('reason', 'n/a')}"
        ),
    }]


def handle_audio_interrupt_status(args: dict) -> list:
    _ = args
    try:
        data = _api_get("/audio/interrupt/status")
    except Exception as e:
        return [{"type": "text", "text": f"Audio interrupt status unavailable: {e}"}]
    return [{"type": "text", "text": f"Audio interrupt: blocked={data.get('blocked')} reason={data.get('reason')} remaining_ms={data.get('remaining_ms', 0)} events={data.get('events_count', 0)}"}]


def handle_audio_interrupt_ack(args: dict) -> list:
    _ = args
    try:
        data = _api_post("/audio/interrupt/ack", body={})
    except Exception as e:
        return [{"type": "text", "text": f"Audio interrupt ack failed: {e}"}]
    return [{"type": "text", "text": f"Audio interrupt acknowledged={data.get('acknowledged', False)}"}]


HANDLERS = {
    "see_screen": handle_see_screen,
    "perception": handle_perception,
    "perception_world": handle_perception_world,
    "perception_trace": handle_perception_trace,
    "domain_pack_list": handle_domain_pack_list,
    "domain_pack_override": handle_domain_pack_override,
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
    "action_intent": handle_action_intent,
    "raw_action": handle_raw_action,
    "action_precheck": handle_action_precheck,
    "verify_action": handle_verify_action,
    "operate_cycle": handle_operate_cycle,
    "set_operating_mode": handle_set_operating_mode,
    "click_element": handle_click_element,
    "type_text": handle_type_text,
    "run_command": handle_run_command,
    "list_windows": handle_list_windows,
    "window_minimize": handle_window_minimize,
    "window_maximize": handle_window_maximize,
    "window_close": handle_window_close,
    "move_window": handle_move_window,
    "find_ui_element": handle_find_ui_element,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "get_clipboard": handle_get_clipboard,
    "agent_status": handle_agent_status,
    # Human-like navigation
    "focus_window": handle_focus_window,
    "browser_navigate": handle_browser_navigate,
    "browser_tabs": handle_browser_tabs,
    "act": handle_act,
    "click_screen": handle_click_screen,
    "drag_screen": handle_drag_screen,
    "keyboard": handle_keyboard,
    "scroll": handle_scroll,
    "monitor_info": handle_monitor_info,
    "see_monitor": handle_see_monitor,
    "spatial_state": handle_spatial_state,
    "workers_status": handle_workers_status,
    "workers_monitor": handle_workers_monitor,
    "workers_claim_action": handle_workers_claim_action,
    "workers_release_action": handle_workers_release_action,
    "workers_schedule": handle_workers_schedule,
    "workers_set_subgoal": handle_workers_set_subgoal,
    "workers_clear_subgoal": handle_workers_clear_subgoal,
    "workers_route": handle_workers_route,
    "behavior_stats": handle_behavior_stats,
    "behavior_recent": handle_behavior_recent,
    "behavior_suggest": handle_behavior_suggest,
    "runtime_profile": handle_runtime_profile,
    "host_telemetry": handle_host_telemetry,
    "os_notifications": handle_os_notifications,
    "os_tray": handle_os_tray,
    "os_dialog_status": handle_os_dialog_status,
    "os_dialog_resolve": handle_os_dialog_resolve,
    "audio_interrupt_status": handle_audio_interrupt_status,
    "audio_interrupt_ack": handle_audio_interrupt_ack,
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
