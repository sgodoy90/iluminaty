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
# API key for server auth — read from ILUMINATY_KEY (primary) or ILUMINATY_API_KEY (legacy)
API_KEY = os.environ.get("ILUMINATY_KEY") or os.environ.get("ILUMINATY_API_KEY", "")
try:
    API_TIMEOUT_S = float(os.environ.get("ILUMINATY_MCP_TIMEOUT_S", "12"))
except Exception:
    API_TIMEOUT_S = 12.0
API_TIMEOUT_S = max(3.0, min(60.0, API_TIMEOUT_S))

# All tools available — no license gates in open source release
from .licensing import ALL_MCP_TOOLS


def _get_allowed_tools() -> set:
    """All tools available to everyone."""
    return ALL_MCP_TOOLS


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
    # ── Vision — IPA v3 + real frames ────────────────────────────────────────
    {
        "name": "see_now",
        "description": (
            "PRIMARY VISION TOOL. See the current screen right now. "
            "Returns the actual screen image (Claude/GPT-4o sees it directly) "
            "plus IPA v3 real-time context: motion type, scene state, recent events. "
            "Use this before acting and after acting to verify results. "
            "Modes: low_res (~5K tokens), medium_res (~15K), full_res (~30K)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["low_res", "medium_res", "full_res"],
                    "description": "Image resolution. low_res recommended for most tasks.",
                    "default": "low_res",
                },
                "monitor": {
                    "type": "integer",
                    "description": "Monitor id (1..N). Omit for active monitor.",
                },
            },
        },
    },
    {
        "name": "what_changed",
        "description": (
            "What changed on screen in the last N seconds? "
            "Returns IPA v3 gate events (significant motion/content changes) "
            "plus an image of the most significant frame. "
            "Use: after an action to verify it worked, or when resuming work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "Lookback window in seconds (default 15)",
                    "default": 15,
                },
                "monitor": {
                    "type": "integer",
                    "description": "Optional monitor id filter",
                },
            },
        },
    },
    {
        "name": "see_screen",
        "description": (
            "See the screen. Returns image + perception context. "
            "Prefer see_now for RT agent loops. Use see_screen for text_only mode "
            "(~200 tokens, no image) when you just need OCR/context without the image."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["text_only", "low_res", "medium_res", "full_res"],
                    "description": "text_only=cheapest (~200t), full_res=expensive (~30K t). Default: text_only",
                    "default": "text_only",
                },
                "monitor": {"type": "integer", "description": "Optional monitor id."},
            },
        },
    },
    {
        "name": "see_changes",
        "description": (
            "See what changed in the last N seconds — multiple frames showing progression. "
            "Use when you need the full temporal sequence, not just the key moment."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Lookback seconds (default 10)", "default": 10},
                "monitor": {"type": "integer", "description": "Optional monitor id"},
            },
        },
    },
    {
        "name": "see_monitor",
        "description": "See a specific monitor with its layout context and click coordinate mapping.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "monitor": {"type": "integer", "description": "Monitor id (1..N)", "default": 1},
                "mode": {
                    "type": "string",
                    "enum": ["low_res", "medium_res", "full_res"],
                    "default": "medium_res",
                },
            },
            "required": ["monitor"],
        },
    },
    {
        "name": "read_screen_text",
        "description": "OCR all visible text on screen or in a region. Use when you need to read text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "monitor": {"type": "integer"},
                "region_x": {"type": "integer"}, "region_y": {"type": "integer"},
                "region_w": {"type": "integer"}, "region_h": {"type": "integer"},
            },
        },
    },
    {
        "name": "vision_query",
        "description": "Ask a visual question over IPA memory (e.g. 'what was on screen 30s ago?').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Natural language visual question"},
                "at_ms": {"type": "integer", "description": "Target timestamp in ms"},
                "window_seconds": {"type": "number", "description": "Lookback window", "default": 30},
                "monitor_id": {"type": "integer"},
            },
            "required": ["question"],
        },
    },
    # ── Perception / context ──────────────────────────────────────────────────
    {
        "name": "get_context",
        "description": "User's current context: app, workflow, focus level, time in workflow.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "perception",
        "description": "Raw IPA v2 perception events stream (scene state, motion, OCR events).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Lookback seconds (default 30)", "default": 30},
            },
        },
    },
    {
        "name": "perception_world",
        "description": "IPA v2 WorldState snapshot (task phase, affordances, uncertainty, readiness).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_spatial_context",
        "description": (
            "ONE-SHOT SESSION CONTEXT. Call this FIRST at the start of every session. "
            "Returns: physical monitor layout with human labels (LEFT/CENTER/RIGHT/TOP), "
            "window inventory per monitor, active user window, user activity state, "
            "and auto-inferred safety rules (e.g. 'user is on Facebook M2 — open new tab'). "
            "~400-600 tokens. Re-call only if monitors change. "
            "For dynamic state (windows changing) use see_now + spatial_state."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "spatial_state",
        "description": "Active monitor, cursor position, window layout across all monitors.",
        "inputSchema": {
            "type": "object",
            "properties": {"include_windows": {"type": "boolean", "default": True}},
        },
    },
    # ── Watch Engine ──────────────────────────────────────────────────────────
    {
        "name": "watch_and_notify",
        "description": (
            "Wait for a screen condition WITHOUT consuming tokens while waiting. "
            "The AI delegates monitoring to ILUMINATY and gets notified when done. "
            "Use instead of polling loops.\n\n"
            "Conditions: page_loaded, motion_stopped, motion_started, "
            "text_appeared, text_disappeared, build_passed, build_failed, "
            "idle, element_visible, window_opened\n\n"
            "Examples:\n"
            "  watch_and_notify('page_loaded', timeout=30)  — wait for page to load\n"
            "  watch_and_notify('text_appeared', text='Upload complete', timeout=120)\n"
            "  watch_and_notify('build_passed', timeout=60)  — wait for build"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "condition":    {"type": "string", "description": "Condition to watch for"},
                "timeout":      {"type": "number",  "description": "Max seconds to wait (default 30)", "default": 30},
                "text":         {"type": "string",  "description": "Text to look for (text_appeared/disappeared)"},
                "element":      {"type": "string",  "description": "Element name to look for (element_visible)"},
                "window_title": {"type": "string",  "description": "Window title (window_opened/closed)"},
                "idle_seconds": {"type": "number",  "description": "Seconds of no activity for idle condition", "default": 3},
                "monitor":      {"type": "integer", "description": "Monitor id to watch (default: active)"},
            },
            "required": ["condition"],
        },
    },
    {
        "name": "monitor_until",
        "description": (
            "Wait up to N seconds until a condition is met — for long-running tasks. "
            "Use for uploads, downloads, builds, deployments. "
            "Returns immediately when done, not after full timeout.\n\n"
            "Same conditions as watch_and_notify but with longer default timeout (120s)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "condition": {"type": "string",  "description": "Condition to wait for"},
                "timeout":   {"type": "number",  "description": "Max seconds (default 120)", "default": 120},
                "text":      {"type": "string",  "description": "Text to look for"},
                "element":   {"type": "string",  "description": "Element name to look for"},
                "monitor":   {"type": "integer", "description": "Monitor id"},
            },
            "required": ["condition"],
        },
    },
    # ── Visual Memory ──────────────────────────────────────────────────────────
    {
        "name": "get_session_memory",
        "description": (
            "Load visual context from the PREVIOUS session. "
            "Call at the start of every session to know what the user was working on. "
            "Returns: monitor layout, active windows, recent events, OCR context (~300 tokens). "
            "No images — pure semantic context. "
            "The AI resumes work with full context without the user re-explaining anything."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_age_hours": {"type": "number", "description": "Max session age to load (default 48h)", "default": 48},
            },
        },
    },
    {
        "name": "save_session_memory",
        "description": (
            "Save current visual context for the NEXT session. "
            "Call before ending a session. "
            "Saves: monitor layout, active windows, recent IPA events, OCR snippets. "
            "Storage: ~/.iluminaty/memory/ (gzipped JSON, ~10-50KB). No images stored."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ── Computer Use ──────────────────────────────────────────────────────────
    {
        "name": "do_action",
        "description": (
            "Execute any action via SAFE control loop (precheck → execute → verify). "
            "Natural language instruction. Use for most actions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "What to do (e.g. 'click Save')"},
                "use_grounding": {"type": "boolean", "default": False},
                "target_query": {"type": "string"},
                "monitor_id": {"type": "integer"},
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "operate_cycle",
        "description": (
            "Full human-like operation cycle: orient → locate → focus → read → act → verify. "
            "Handles dialogs/modals automatically. Best for complex multi-step tasks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "target_window": {"type": "string"},
                "monitor": {"type": "integer"},
                "include_ocr": {"type": "boolean", "default": True},
                "resolve_interrupts": {"type": "boolean", "default": True},
                "interrupt_strategy": {"type": "string", "enum": ["accept_first", "dismiss_first", "none"], "default": "accept_first"},
                "action": {"type": "object", "description": "Action descriptor {kind, x, y, text, keys, ...}"},
                "verify_contains": {"type": "string"},
            },
        },
    },
    {
        "name": "act",
        "description": (
            "Direct action executor. Actions: click, double_click, type, key, scroll, focus, move_mouse.\n"
            "SMART LOCATE: pass target= to resolve coordinates automatically via UITree+OCR — no guessing.\n"
            "  act(action='click', target='Save button')           — finds element, clicks exactly\n"
            "  act(action='type', target='email field', text='x')  — finds field, clicks, types\n"
            "  act(action='click', x=500, y=300)                   — direct coords from see_now"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["click", "double_click", "type", "key", "scroll", "focus", "move_mouse"]},
                "target": {"type": "string", "description": "Natural language element name. Smart-locates via UITree+OCR. Takes priority over x,y."},
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "text": {"type": "string"},
                "keys": {"type": "string"},
                "button": {"type": "string", "default": "left"},
                "amount": {"type": "integer"},
                "role": {"type": "string", "description": "Role hint: button|edit|link|checkbox|combobox"},
                "title": {"type": "string"},
                "handle": {"type": "integer"},
                "monitor": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "set_operating_mode",
        "description": "Set operating mode: SAFE (guardrails on), RAW (no guardrails), HYBRID.",
        "inputSchema": {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["SAFE", "RAW", "HYBRID"]}},
            "required": ["mode"],
        },
    },
    {
        "name": "drag_screen",
        "description": "Drag from (start_x, start_y) to (end_x, end_y).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_x": {"type": "integer"}, "start_y": {"type": "integer"},
                "end_x": {"type": "integer"}, "end_y": {"type": "integer"},
                "duration": {"type": "number", "default": 0.35},
                "monitor": {"type": "integer"},
                "coord_space": {"type": "string", "enum": ["global", "monitor"], "default": "global"},
            },
            "required": ["start_x", "start_y", "end_x", "end_y"],
        },
    },
    # ── Windows ───────────────────────────────────────────────────────────────
    {
        "name": "list_windows",
        "description": "List all visible windows with handle, title, position, size, monitor_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "monitor": {"type": "integer"},
                "title_contains": {"type": "string"},
                "exclude_minimized": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "focus_window",
        "description": "Bring a window to the front by title or handle.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "handle": {"type": "integer"},
                "prefer_active_monitor": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "window_minimize",
        "description": "Minimize a window.",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "handle": {"type": "integer"}},
        },
    },
    {
        "name": "window_maximize",
        "description": "Maximize a window.",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "handle": {"type": "integer"}},
        },
    },
    {
        "name": "window_close",
        "description": "Close a window.",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "handle": {"type": "integer"}},
        },
    },
    {
        "name": "move_window",
        "description": "Move/resize a window to specific coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"}, "handle": {"type": "integer"},
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "width": {"type": "integer", "default": -1},
                "height": {"type": "integer", "default": -1},
                "monitor": {"type": "integer"},
                "coord_space": {"type": "string", "enum": ["global", "monitor"], "default": "global"},
            },
            "required": ["x", "y"],
        },
    },
    # ── Browser ───────────────────────────────────────────────────────────────
    {
        "name": "browser_navigate",
        "description": "Navigate to a URL. Preserves existing browser context (reuses tab by default).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "new_tab": {"type": "boolean", "default": True},
                "browser": {"type": "string", "enum": ["auto", "brave", "chrome", "edge", "firefox"], "default": "auto"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_tabs",
        "description": "List all open browser tabs with titles and URLs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ── Files / system ────────────────────────────────────────────────────────
    {
        "name": "run_command",
        "description": "Execute a shell command and return output.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number", "default": 30},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the filesystem.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "get_clipboard",
        "description": "Read the current clipboard content.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ── Status ────────────────────────────────────────────────────────────────
    {
        "name": "screen_status",
        "description": "System status: buffer stats, capture state, FPS, active window.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agent_status",
        "description": "Full agent status: actions enabled, safety state, autonomy level.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_audio_level",
        "description": "Current audio level and whether speech is detected.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "os_dialog_status",
        "description": "Detect if a system dialog/modal is blocking the screen.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "os_dialog_resolve",
        "description": "Resolve a blocking system dialog (accept or dismiss).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "strategy": {"type": "string", "enum": ["accept_first", "dismiss_first"], "default": "accept_first"},
            },
        },
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


def handle_describe_screen(args: dict) -> list:
    monitor = args.get("monitor")
    path = "/vision/describe"
    if monitor is not None:
        path += f"?monitor_id={int(monitor)}"
    try:
        data = _api_post(path)
    except Exception as e:
        return [{"type": "text", "text": f"Describe failed: {e}"}]
    facts = data.get("facts", [])
    vlm_fact = next((f for f in facts if f.get("kind") == "image_caption"), None)
    caption = vlm_fact["text"] if vlm_fact else data.get("summary", "No VLM description available")
    return [{"type": "text", "text": f"## Screen Description\n{caption}\n\nConfidence: {data.get('confidence', 0)}\nMonitor: {data.get('monitor', '?')}\nLatency: {data.get('latency_ms', '?')}ms"}]


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


def handle_act(args: dict) -> list:
    """
    Direct action executor. Claude sees screen via see_now, decides what to do.
    Supports: click, double_click, type, key, scroll, focus, move_mouse

    COORDINATE RESOLUTION:
    - If target= is provided (e.g. target="Save button"), smart_locate resolves
      exact coordinates via UITree + OCR. No guessing needed.
    - If x,y are provided, uses them directly.
    - target= takes priority over x,y.
    """
    action = str(args.get("action", "")).strip().lower()
    if not action:
        return [{"type": "text", "text": "Error: action is required (click/type/key/scroll/focus/move_mouse)"}]

    # ── Smart locate: resolve target name → exact coordinates ────────────────
    target = (args.get("target") or "").strip()
    if target and action in ("click", "double_click", "type"):
        monitor = args.get("monitor")
        role_hint = args.get("role")
        locate_query = f"/locate?query={urllib.parse.quote(target)}"
        if monitor is not None:
            locate_query += f"&monitor_id={int(monitor)}"
        if role_hint:
            locate_query += f"&role={urllib.parse.quote(str(role_hint))}"
        try:
            loc = _api_get(locate_query)
            if loc.get("found"):
                # Inject resolved coordinates
                args = dict(args)
                args["x"] = loc["x"]
                args["y"] = loc["y"]
                source = loc.get("source", "?")
                conf   = loc.get("confidence", 0)
                label  = loc.get("label", target)
                loc_note = f" [located via {source} conf={conf:.0%}: {label}]"
            else:
                loc_note = f" [WARNING: '{target}' not found via smart_locate — using x,y fallback]"
        except Exception as e:
            loc_note = f" [smart_locate error: {e}]"
    else:
        loc_note = ""

    try:
        if action == "click":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            button = args.get("button", "left")
            query = f"/action/click?x={x}&y={y}&button={urllib.parse.quote(str(button))}"
            if args.get("monitor") is not None:
                query += f"&monitor_id={int(args['monitor'])}&relative_to_monitor=true"
            data = _api_post(query)
            ok = data.get("success", False)
            return [{"type": "text", "text": f"click ({x},{y}) {button}: {'OK' if ok else 'FAIL'}{loc_note} {data.get('message','')}"}]

        elif action == "double_click":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            data = _api_post(f"/action/double_click?x={x}&y={y}")
            ok = data.get("success", False)
            return [{"type": "text", "text": f"double_click ({x},{y}): {'OK' if ok else 'FAIL'}{loc_note}"}]

        elif action == "type":
            text = str(args.get("text", ""))
            if not text:
                return [{"type": "text", "text": "Error: text is required"}]
            # If target resolved a field, click it first to focus
            if target and args.get("x") and args.get("y"):
                x, y = int(args["x"]), int(args["y"])
                _api_post(f"/action/click?x={x}&y={y}&button=left")
            data = _api_post(f"/action/type?text={urllib.parse.quote(text)}")
            ok = data.get("success", False)
            return [{"type": "text", "text": f"typed {len(text)} chars: {'OK' if ok else 'FAIL'}{loc_note}"}]

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


def _spatial_zone(left: int, top: int, width: int, height: int,
                  all_monitors: list) -> str:
    """Convert absolute monitor coordinates to human-readable spatial zone.

    Compares this monitor's center against all others to produce
    a natural label: CENTER, LEFT, RIGHT, TOP-LEFT, etc.
    Works for any number of monitors in any physical arrangement.
    """
    if len(all_monitors) <= 1:
        return "MAIN"

    cx = left + width // 2
    cy = top + height // 2

    # Compute centers of all monitors
    centers = [(m.get("left", 0) + m.get("width", 1) // 2,
                m.get("top", 0) + m.get("height", 1) // 2)
               for m in all_monitors]

    avg_x = sum(c[0] for c in centers) / len(centers)
    avg_y = sum(c[1] for c in centers) / len(centers)

    # X: how far from average horizontal center (normalized)
    x_range = max(abs(c[0] - avg_x) for c in centers) or 1
    y_range = max(abs(c[1] - avg_y) for c in centers) or 1

    rel_x = (cx - avg_x) / x_range   # -1 = leftmost, +1 = rightmost
    rel_y = (cy - avg_y) / y_range   # -1 = topmost,  +1 = bottommost

    h = "LEFT" if rel_x < -0.3 else ("RIGHT" if rel_x > 0.3 else "CENTER")
    v = "TOP" if rel_y < -0.3 else ("BOTTOM" if rel_y > 0.3 else "")

    if v and h == "CENTER":
        return v
    if v:
        return f"{v}-{h}"
    return h


def handle_get_spatial_context(args: dict) -> list:
    """Build a one-shot spatial context block for session start.

    Combines monitor layout (static), window inventory per monitor (dynamic),
    user activity state, and inferred safety rules into a compact narrative
    (~400-600 tokens) that lets the AI understand the full environment
    without making multiple round-trip calls.

    Call once at session start. Re-call if user adds/removes a monitor.
    For dynamic changes (windows opening/closing) use see_now + spatial_state.
    """
    # ── 1. Monitor layout ────────────────────────────────────────────────────
    try:
        spatial = _api_get("/spatial/state?include_windows=true")
    except Exception as e:
        return [{"type": "text", "text": f"Spatial context unavailable: {e}"}]

    monitors_raw = spatial.get("monitors", []) or []
    active_monitor_id = int(spatial.get("active_monitor_id") or 1)
    active_window = spatial.get("active_window", {}) or {}
    cursor = spatial.get("cursor", {}) or {}
    n_monitors = len(monitors_raw)

    # ── 2. Window inventory per monitor ─────────────────────────────────────
    try:
        win_data = _api_get("/windows/list?visible_only=true&exclude_minimized=true&exclude_system=true")
        all_windows = win_data.get("windows", []) or []
    except Exception:
        all_windows = []

    # Group windows by monitor
    wins_by_monitor: dict[int, list[dict]] = {}
    for w in all_windows:
        mid = int(w.get("monitor_id") or 0)
        wins_by_monitor.setdefault(mid, []).append(w)

    # ── 3. User activity context ──────────────────────────────────────────────
    try:
        ctx = _api_get("/context/state")
        workflow = ctx.get("workflow", "unknown")
        app = ctx.get("app", "unknown")
        focus = ctx.get("is_focused", False)
        time_in_workflow = int(ctx.get("time_in_workflow_seconds", 0) or 0)
    except Exception:
        workflow = app = "unknown"
        focus = False
        time_in_workflow = 0

    # ── 4. Build narrative ────────────────────────────────────────────────────
    lines = [
        "# SPATIAL CONTEXT — Session Start",
        f"Monitors: {n_monitors} | Active: M{active_monitor_id} | "
        f"Cursor: ({cursor.get('x', '?')},{cursor.get('y', '?')})",
        "",
        "## Monitor Layout",
    ]

    monitor_zones = {}
    for m in monitors_raw:
        mid = int(m.get("id", 0))
        left = int(m.get("left", 0))
        top = int(m.get("top", 0))
        w = int(m.get("width", 1920))
        h = int(m.get("height", 1080))
        zone = _spatial_zone(left, top, w, h, monitors_raw)
        monitor_zones[mid] = zone
        is_active = (mid == active_monitor_id)

        lines.append(
            f"  M{mid} [{zone}] {w}x{h} at ({left},{top})"
            + (" ← ACTIVE" if is_active else "")
        )

        # Windows on this monitor
        wins = wins_by_monitor.get(mid, [])
        if wins:
            for win in wins[:5]:
                title = (win.get("title") or "").strip()[:70]
                handle = win.get("handle")
                is_aw = (handle == active_window.get("handle"))
                marker = " ★ USER ACTIVE" if is_aw else ""
                lines.append(f"    • [{title}] h={handle}{marker}")
        else:
            lines.append("    • (no visible windows)")

    # Active window summary
    lines.append("")
    lines.append("## Active Window")
    if active_window:
        aw_title = (active_window.get("title") or "").strip()[:80]
        aw_app = (active_window.get("app_name") or "").strip()
        aw_mon = int(active_window.get("monitor_id") or active_monitor_id)
        aw_zone = monitor_zones.get(aw_mon, "?")
        lines.append(f"  App: {aw_app or aw_title}")
        lines.append(f"  Title: {aw_title}")
        lines.append(f"  Monitor: M{aw_mon} [{aw_zone}]")
        lines.append(f"  Handle: {active_window.get('handle')}")
    else:
        lines.append("  Unknown")

    # User activity
    lines.append("")
    lines.append("## User Activity")
    if workflow != "unknown":
        mins = time_in_workflow // 60
        secs = time_in_workflow % 60
        duration = f"{mins}m{secs}s" if mins else f"{secs}s"
        lines.append(f"  Workflow: {workflow} | App: {app} | Duration: {duration}")
        lines.append(f"  Focus: {'HIGH' if focus else 'LOW'}")
    else:
        lines.append("  Not detected yet — call again after 10s of capture")

    # ── 5. Inferred safety rules ──────────────────────────────────────────────
    lines.append("")
    lines.append("## Safety Rules (auto-inferred)")

    rules = []

    # Rule 1: active monitor has user content → preserve
    if active_window:
        aw_title_low = (active_window.get("title") or "").lower()
        aw_app_low = (active_window.get("app_name") or "").lower()
        blob = aw_title_low + " " + aw_app_low
        # Detect if user is actively consuming content
        content_apps = ("brave", "chrome", "firefox", "edge", "youtube", "netflix",
                        "spotify", "vlc", "teams", "zoom", "slack", "discord")
        if any(k in blob for k in content_apps):
            aw_zone = monitor_zones.get(active_monitor_id, "ACTIVE")
            rules.append(
                f"  ⚠ M{active_monitor_id} [{aw_zone}] has active user content "
                f"({(active_window.get('app_name') or 'app').strip()}) — "
                f"DO NOT navigate/close existing tabs. Open new tab or use another monitor."
            )

    # Rule 2: prefer inactive monitors for agent tasks
    inactive = [mid for mid in monitor_zones if mid != active_monitor_id]
    if inactive:
        zones = ", ".join(f"M{mid} [{monitor_zones[mid]}]" for mid in inactive)
        rules.append(f"  ✓ Prefer {zones} for agent tasks to avoid disrupting user.")

    # Rule 3: multiple monitors — always specify monitor when clicking
    if n_monitors > 1:
        rules.append(
            f"  ✓ {n_monitors} monitors detected — always pass monitor= param "
            f"to act/click to target the correct display."
        )

    # Rule 4: new_tab default
    rules.append(
        "  ✓ browser_navigate: always new_tab=True unless explicitly told to replace current page."
    )

    if rules:
        lines.extend(rules)
    else:
        lines.append("  No special rules inferred.")

    # ── 6. Quick reference ────────────────────────────────────────────────────
    lines.append("")
    lines.append("## Quick Reference")
    lines.append("  Before acting:  call see_now to get current screen state")
    lines.append("  After acting:   call what_changed to verify result")
    lines.append("  To re-orient:   call get_spatial_context again")
    lines.append(f"  Total windows visible: {len(all_windows)}")

    return [{"type": "text", "text": "\n".join(lines)}]


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


def handle_see_now(args: dict) -> list:
    """Current frame as image + IPA v3 motion/scene context.

    This is the primary vision tool for RT agent loops.
    Returns the actual screen image so Claude/GPT-4o can SEE it directly,
    plus a compact IPA v3 narrative (~100 tokens) of what's happening.
    """
    monitor = args.get("monitor")
    mode = args.get("mode", "low_res")   # low_res default — ~5K tokens with real image

    # Get frame image from server
    query = f"/vision/smart?mode={mode}"
    if monitor is not None:
        query += f"&monitor_id={int(monitor)}"
    try:
        data = _api_get(query)
    except Exception as e:
        return [{"type": "text", "text": f"see_now failed: {e}"}]

    result = []

    # IPA v3 context from bridge (if running)
    ipa_context = ""
    try:
        ipa_data = _api_get("/ipa/context")
        if ipa_data and not ipa_data.get("error"):
            motion = ipa_data.get("motion", {}) or {}
            scene = ipa_data.get("scene_state", "unknown")
            gate = ipa_data.get("gate_event") or {}
            parts = [f"[Scene: {scene}]"]
            if motion.get("motion_type") and motion["motion_type"] != "static":
                parts.append(f"[Motion: {motion['motion_type']} | {motion.get('detail', '')}]")
            if gate.get("description"):
                import time as _t
                age = round(_t.time() - gate.get("timestamp", _t.time()), 1)
                parts.append(f"[Event {age}s ago: {gate['description']}]")
            if ipa_data.get("ocr_hint"):
                parts.append(f"[OCR: {ipa_data['ocr_hint'][:200]}]")
            ipa_context = " ".join(parts)
    except Exception:
        pass

    # Perception context fallback
    perception_text = data.get("ai_prompt", "")
    header = ipa_context or perception_text or "Screen capture"

    result.append({"type": "text", "text": header})

    if "image_base64" in data:
        result.append({
            "type": "image",
            "data": data["image_base64"],
            "mimeType": data.get("mime_type", "image/webp"),
        })
    else:
        # text_only fallback — include OCR
        if data.get("ocr_text"):
            result.append({"type": "text", "text": f"OCR:\n{data['ocr_text'][:2000]}"})

    return result


def handle_what_changed(args: dict) -> list:
    """What changed on screen since last time + image of the key moment.

    Combines IPA v3 gate events with a frame image of the most significant change.
    Use after actions to verify the result, or when resuming after a pause.
    """
    seconds = float(args.get("seconds", 15))
    monitor = args.get("monitor")

    result_parts = []

    # IPA v3 gate events
    try:
        ipa_data = _api_get(f"/ipa/events?seconds={seconds}")
        events = ipa_data.get("events", []) if ipa_data else []
        if events:
            lines = [f"## What changed (last {seconds:.0f}s)"]
            import time as _t
            now = _t.time()
            for evt in events[-8:]:
                age = round(now - evt.get("timestamp", now), 1)
                lines.append(f"• [{age}s ago] {evt.get('description', '')} "
                             f"(patches={evt.get('n_changed_patches', 0)}, "
                             f"motion={evt.get('motion_type', '?')})")
            result_parts.append({"type": "text", "text": "\n".join(lines)})
        else:
            result_parts.append({"type": "text", "text": f"No significant events in last {seconds:.0f}s."})
    except Exception as e:
        result_parts.append({"type": "text", "text": f"IPA events unavailable: {e}"})

    # Frame image of the most recent significant moment
    try:
        query = f"/frames?seconds={min(seconds, 30)}&include_images=true"
        if monitor is not None:
            query += f"&monitor_id={int(monitor)}"
        frames_data = _api_get(query)
        frames = frames_data.get("frames", [])
        # Pick frame with highest change_score
        if frames:
            best = max(frames, key=lambda f: f.get("change_score", 0))
            if best.get("image_base64"):
                change = best.get("change_score", 0)
                ts = best.get("timestamp_iso", "?")
                result_parts.append({"type": "text", "text": f"Key frame — {ts} (change={change:.3f})"})
                result_parts.append({
                    "type": "image",
                    "data": best["image_base64"],
                    "mimeType": "image/webp",
                })
    except Exception:
        pass

    return result_parts if result_parts else [{"type": "text", "text": f"No changes detected in last {seconds:.0f}s."}]


# ─── Watch Engine handlers ────────────────────────────────────────────────────

def handle_watch_and_notify(args: dict) -> list:
    """Wait for a screen condition without consuming tokens.

    The AI delegates monitoring to ILUMINATY and gets notified when done.
    Frees the AI from polling loops — zero tokens while waiting.
    """
    condition = (args.get("condition") or "").strip()
    if not condition:
        return [{"type": "text", "text": "Error: condition is required. Available: page_loaded, motion_stopped, motion_started, text_appeared, text_disappeared, build_passed, build_failed, idle, element_visible, window_opened"}]

    timeout = float(args.get("timeout", 30))
    text    = args.get("text")
    element = args.get("element")
    window  = args.get("window_title")
    idle_s  = float(args.get("idle_seconds", 3.0))
    monitor = args.get("monitor")

    path = f"/watch/notify?condition={urllib.parse.quote(condition)}&timeout={timeout}&idle_seconds={idle_s}"
    if text:    path += f"&text={urllib.parse.quote(str(text))}"
    if element: path += f"&element={urllib.parse.quote(str(element))}"
    if window:  path += f"&window_title={urllib.parse.quote(str(window))}"
    if monitor: path += f"&monitor_id={int(monitor)}"

    try:
        data = _api_post(path)
    except Exception as e:
        return [{"type": "text", "text": f"watch_and_notify failed: {e}"}]

    triggered = data.get("triggered", False)
    elapsed   = data.get("elapsed_s", 0)
    reason    = data.get("reason", "")
    evidence  = data.get("evidence", "")
    timed_out = data.get("timed_out", False)

    if timed_out:
        return [{"type": "text", "text": f"TIMEOUT: Condition '{condition}' not met after {elapsed:.0f}s. {reason}"}]

    lines = [
        f"TRIGGERED: {condition}",
        f"Time: {elapsed:.1f}s",
        f"Reason: {reason}",
    ]
    if evidence:
        lines.append(f"Evidence: {evidence[:200]}")

    return [{"type": "text", "text": "\n".join(lines)}]


def handle_monitor_until(args: dict) -> list:
    """Wait up to N seconds until a condition is met — for long-running tasks.

    Use this when the task may take minutes: uploads, builds, downloads.
    Returns immediately when done, not after the full timeout.
    """
    condition = (args.get("condition") or "").strip()
    if not condition:
        return [{"type": "text", "text": "Error: condition is required"}]

    timeout = float(args.get("timeout", 120))
    text    = args.get("text")
    element = args.get("element")
    monitor = args.get("monitor")

    path = f"/watch/until?condition={urllib.parse.quote(condition)}&timeout={timeout}"
    if text:    path += f"&text={urllib.parse.quote(str(text))}"
    if element: path += f"&element={urllib.parse.quote(str(element))}"
    if monitor: path += f"&monitor_id={int(monitor)}"

    try:
        data = _api_post(path)
    except Exception as e:
        return [{"type": "text", "text": f"monitor_until failed: {e}"}]

    triggered = data.get("triggered", False)
    elapsed   = data.get("elapsed_s", 0)
    reason    = data.get("reason", "")
    timed_out = data.get("timed_out", False)

    if timed_out:
        return [{"type": "text", "text": f"TIMEOUT after {elapsed:.0f}s: condition '{condition}' not met. {reason}"}]

    return [{"type": "text", "text": f"DONE in {elapsed:.1f}s: {reason}"}]


# ─── Visual Memory handlers ───────────────────────────────────────────────────

def handle_get_session_memory(args: dict) -> list:
    """Load visual context from the previous session.

    Call at the start of every session to know what the user was working on.
    Returns a ready-to-use context description (~200-400 tokens).
    No images — just the semantic context of where work was left off.
    """
    max_age = float(args.get("max_age_hours", 48.0))
    try:
        data = _api_get(f"/memory/prompt?max_age_hours={max_age}")
    except Exception as e:
        return [{"type": "text", "text": f"Memory unavailable: {e}"}]

    if not data.get("found"):
        return [{"type": "text", "text": "No previous session memory found. This appears to be a fresh start."}]

    age   = data.get("age_hours", 0)
    prompt = data.get("prompt", "")

    return [{"type": "text", "text": f"## Previous Session (saved {age:.1f}h ago)\n\n{prompt}"}]


def handle_save_session_memory(args: dict) -> list:
    """Save current visual context for the next session.

    Call before ending a session so the AI can resume with full context.
    Saves monitor layout, active windows, recent events — no images.
    """
    try:
        data = _api_post("/memory/save")
    except Exception as e:
        return [{"type": "text", "text": f"Memory save failed: {e}"}]

    saved = data.get("saved", False)
    stats = data.get("stats", {})
    sessions = stats.get("sessions_saved", 0)
    kb = stats.get("total_kb", 0)

    if saved:
        return [{"type": "text", "text": f"Session memory saved. Total: {sessions} sessions, {kb:.0f}KB"}]
    return [{"type": "text", "text": "Memory save failed — will retry on server shutdown."}]


def handle_screen_record(args: dict) -> list:
    """Record screen to local disk (opt-in). Zero-disk by default.

    Args:
      action   : "start" | "stop" | "status"
      session_id: required for action="stop"
      monitors : [1,2,3] or omit for all
      duration : max seconds (default 30 for quick capture, max 600)
      format   : "gif" | "webm" | "mp4" (default: "gif")
      fps      : capture rate (default: 2)
    """
    action = str(args.get("action", "start")).strip().lower()

    if action == "status":
        try:
            data = _api_get("/recording/status")
            active = data.get("active", [])
            lines = ["## Recording Status"]
            lines.append(f"Output dir: {data.get('output_dir','~/.iluminaty/recordings/')}")
            if active:
                for s in active:
                    lines.append(f"- [{s['id'][:8]}] M{s['monitors']} {s['format']} {s['duration_s']}s {s['frames']} frames")
            else:
                lines.append("No active recordings.")
            return [{"type": "text", "text": "\n".join(lines)}]
        except Exception as e:
            return [{"type": "text", "text": f"Recording status failed: {e}"}]

    if action == "stop":
        session_id = str(args.get("session_id") or "").strip()
        if not session_id:
            return [{"type": "text", "text": "Error: session_id required to stop recording"}]
        try:
            data = _api_post(f"/recording/stop/{session_id}")
        except Exception as e:
            return [{"type": "text", "text": f"Stop recording failed: {e}"}]
        paths = list(data.get("paths", {}).values())
        return [{"type": "text", "text": (
            f"Recording stopped: {data.get('duration_s','?')}s, "
            f"{data.get('frames','?')} frames, {data.get('size_mb','?')}MB\n"
            f"Saved to: {', '.join(paths)}"
        )}]

    # action == "start"
    body = {
        "monitors":    args.get("monitors") or [],
        "max_seconds": int(args.get("duration", 30) or 30),
        "format":      str(args.get("format", "gif") or "gif"),
        "fps":         float(args.get("fps", 2.0) or 2.0),
    }
    try:
        data = _api_post("/recording/start", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Start recording failed: {e}"}]

    return [{"type": "text", "text": (
        f"Recording started: id={data.get('id','?')[:12]} "
        f"monitors={data.get('monitors','?')} fmt={data.get('format','?')} "
        f"max={data.get('max_seconds','?')}s\n"
        f"Stop with: screen_record(action='stop', session_id='{data.get('id','?')}')"
    )}]


def handle_agent_dispatch(args: dict) -> list:
    """Dispatch a task from this agent to another agent.

    Use when you have role=planner and want to assign work to an executor.
    The executor will receive the task in their next inbox() call.
    """
    to_agent   = str(args.get("to_agent") or "*").strip()
    task       = str(args.get("task") or "").strip()
    monitor    = args.get("monitor", 1)
    priority   = float(args.get("priority", 0.5))
    from_agent = str(args.get("from_agent") or "planner").strip()

    if not task:
        return [{"type": "text", "text": "Error: task is required"}]

    body = {"to_agent": to_agent, "task": task, "monitor": monitor,
            "priority": priority, "from_agent": from_agent}
    try:
        data = _api_post("/agents/dispatch", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Dispatch failed: {e}"}]

    return [{"type": "text", "text": (
        f"Task dispatched to {to_agent}: '{task[:80]}' "
        f"(msg_id={data.get('msg_id','?')}, monitor={monitor}, priority={priority})"
    )}]


def handle_agent_inbox(args: dict) -> list:
    """Check pending messages in this agent's inbox.

    Returns tasks dispatched by a planner, reports from executors,
    or verification results from verifiers.
    """
    agent_id  = str(args.get("agent_id") or "").strip()
    max_count = int(args.get("max_count", 10))

    if not agent_id:
        return [{"type": "text", "text": "Error: agent_id is required"}]

    try:
        data = _api_get(f"/agents/{agent_id}/messages?max_count={max_count}")
    except Exception as e:
        return [{"type": "text", "text": f"Inbox failed: {e}"}]

    messages = data.get("messages", [])
    if not messages:
        return [{"type": "text", "text": f"Inbox empty for {agent_id}"}]

    lines = [f"## Inbox ({len(messages)} messages)"]
    for m in messages:
        lines.append(
            f"\n**[{m.get('type','?').upper()}]** from={m.get('from','?')} "
            f"id={m.get('id','?')[:10]}"
        )
        payload = m.get("payload", {})
        if "task" in payload:
            lines.append(f"  Task: {payload['task'][:120]}")
            lines.append(f"  Monitor: {payload.get('monitor','?')} | Priority: {payload.get('priority','?')}")
        elif "status" in payload:
            lines.append(f"  Status: {payload['status']} | Result: {str(payload.get('result',''))[:120]}")
        else:
            lines.append(f"  Payload: {str(payload)[:120]}")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_agent_report(args: dict) -> list:
    """Report task completion or verification result to another agent.

    Use after finishing a task (executor→planner) or after verification (verifier→planner).
    """
    from_agent = str(args.get("from_agent") or "").strip()
    to_agent   = str(args.get("to_agent") or "*").strip()
    status     = str(args.get("status") or "done").strip()
    result     = str(args.get("result") or "").strip()

    if not from_agent:
        return [{"type": "text", "text": "Error: from_agent is required"}]

    body = {"from_agent": from_agent, "to_agent": to_agent,
            "status": status, "result": result}
    try:
        data = _api_post("/agents/report", body=body)
    except Exception as e:
        return [{"type": "text", "text": f"Report failed: {e}"}]

    return [{"type": "text", "text": (
        f"Report sent from {from_agent} to {to_agent}: "
        f"status={status} (msg_id={data.get('msg_id','?')})"
    )}]


HANDLERS = {
    # ── Vision (IPA v3 + OCR) ──
    "see_screen": handle_see_screen,
    "see_now": handle_see_now,
    "what_changed": handle_what_changed,
    "see_changes": handle_see_changes,
    "see_monitor": handle_see_monitor,
    "read_screen_text": handle_read_text,
    "vision_query": handle_vision_query,
    # ── Perception / context ──
    "perception": handle_perception,
    "perception_world": handle_perception_world,
    "get_context": handle_context,
    "get_spatial_context": handle_get_spatial_context,
    "spatial_state": handle_spatial_state,
    # ── Watch Engine ──
    "watch_and_notify":    handle_watch_and_notify,
    "monitor_until":       handle_monitor_until,
    # ── Visual Memory ──
    "get_session_memory":  handle_get_session_memory,
    "save_session_memory": handle_save_session_memory,
    # ── Grounding ──
    # ── Computer Use ──
    "do_action": handle_do_action,
    "operate_cycle": handle_operate_cycle,
    "set_operating_mode": handle_set_operating_mode,
    "act": handle_act,
    "drag_screen": handle_drag_screen,
    # ── Windows ──
    "list_windows": handle_list_windows,
    "focus_window": handle_focus_window,
    "window_minimize": handle_window_minimize,
    "window_maximize": handle_window_maximize,
    "window_close": handle_window_close,
    "move_window": handle_move_window,
    # ── Browser ──
    "browser_navigate": handle_browser_navigate,
    "browser_tabs": handle_browser_tabs,
    # ── Files / system ──
    "run_command": handle_run_command,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "get_clipboard": handle_get_clipboard,
    # ── Recording (opt-in) ──
    "screen_record":    handle_screen_record,
    # ── Multi-agent coordination ──
    "agent_dispatch":   handle_agent_dispatch,
    "agent_inbox":      handle_agent_inbox,
    "agent_report":     handle_agent_report,
    # ── Status ──
    "screen_status": handle_status,
    "agent_status": handle_agent_status,
    "get_audio_level": handle_audio_level,
    "os_dialog_status": handle_os_dialog_status,
    "os_dialog_resolve": handle_os_dialog_resolve,
}


# ─── MCP Protocol (stdio) ───

def run_mcp_stdio():
    """
    Run as MCP server over stdio.
    Reads JSON-RPC from stdin, writes to stdout.
    """
    import sys

    # Debug log — only enabled when ILUMINATY_MCP_DEBUG=1
    _debug_enabled = os.environ.get("ILUMINATY_MCP_DEBUG", "0") == "1"
    _logf = None
    if _debug_enabled:
        _logf = open(os.path.join(os.path.dirname(__file__), "..", "mcp_debug.log"), "a")

    def _log(msg):
        if _logf:
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
