"""
ILUMINATY Python Client SDK
=============================
Zero-dependency client for ILUMINATY local server.

pip install iluminaty-client

Quick start:
    from iluminaty_client import Iluminaty

    eye = Iluminaty(api_key="your-key")   # connects to localhost:8420
    print(eye.see_now())                  # snapshot + IPA context
    print(eye.read(monitor=1))            # OCR text from M1
    eye.act("click", target="Save")       # click by name
    eye.watch_until("page_loaded")        # wait for event, zero tokens

Multi-monitor:
    ctx = eye.spatial_context()           # all monitors + windows
    frame = eye.see_now(monitor=2)        # specific monitor

Agents:
    agent = eye.register_agent("mybot", role="executor", monitors=[1])
    eye.dispatch_task(agent.id, "click the submit button", monitor=1)
    msgs = eye.inbox(agent.id)

Recording:
    rec = eye.start_recording(monitors=[1,2])
    # ... do stuff ...
    path = eye.stop_recording(rec["id"])
"""

from __future__ import annotations

import json
import time
import base64
from dataclasses import dataclass, field
from typing import Optional, Iterator, Any
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote
from urllib.error import URLError, HTTPError

__version__ = "0.3.0"
__all__ = ["Iluminaty", "IluminatyError", "Frame", "OCRResult", "AgentSession"]


# ── Exceptions ────────────────────────────────────────────────────────────────

class IluminatyError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"[{status}] {message}")


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class Frame:
    timestamp: float
    width: int
    height: int
    monitor_id: int = 0
    change_score: float = 0.0
    mime_type: str = "image/webp"
    image_base64: Optional[str] = None

    @property
    def image_bytes(self) -> Optional[bytes]:
        if self.image_base64:
            return base64.b64decode(self.image_base64)
        return None

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    def save(self, path: str) -> None:
        """Save frame image to disk."""
        data = self.image_bytes
        if not data:
            raise ValueError("No image data in frame")
        with open(path, "wb") as f:
            f.write(data)


@dataclass
class OCRResult:
    text: str
    blocks: list = field(default_factory=list)
    confidence: float = 0.0
    engine: str = "none"
    monitor_id: int = 0
    latency_ms: float = 0.0

    def find(self, query: str) -> list[dict]:
        """Find blocks containing query text (case-insensitive)."""
        q = query.lower()
        return [b for b in self.blocks if q in str(b.get("text", "")).lower()]


@dataclass
class AgentSession:
    agent_id: str
    name: str
    role: str
    autonomy: str = "suggest"
    monitors: list = field(default_factory=list)
    allowed_tools: Any = None

    def __str__(self):
        return f"Agent({self.name}, role={self.role}, id={self.agent_id[:12]})"


@dataclass
class WatchResult:
    triggered: bool
    condition: str
    elapsed_s: float
    reason: str = ""
    evidence: str = ""
    monitor_id: int = 0
    timed_out: bool = False


# ── Client ────────────────────────────────────────────────────────────────────

class Iluminaty:
    """
    ILUMINATY Python client.

    Connects to a running ILUMINATY server (default localhost:8420).
    All methods are synchronous. Zero external dependencies.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8420",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # ── HTTP internals ─────────────────────────────────────────────────────

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _get(self, path: str, **params) -> dict:
        clean = {k: v for k, v in params.items() if v is not None}
        url = self.base_url + path
        if clean:
            url += "?" + urlencode(clean)
        req = Request(url, headers=self._headers())
        try:
            with urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                detail = json.loads(body).get("detail", body)
            except Exception:
                detail = body
            raise IluminatyError(e.code, detail)
        except URLError as e:
            raise IluminatyError(0, f"Connection failed: {e.reason}")

    def _post(self, path: str, body: Optional[dict] = None, **params) -> dict:
        clean = {k: v for k, v in params.items() if v is not None}
        url = self.base_url + path
        if clean:
            url += "?" + urlencode(clean)
        data = json.dumps(body or {}).encode()
        req = Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            body_str = e.read().decode(errors="replace")
            try:
                detail = json.loads(body_str).get("detail", body_str)
            except Exception:
                detail = body_str
            raise IluminatyError(e.code, detail)
        except URLError as e:
            raise IluminatyError(0, f"Connection failed: {e.reason}")

    def _patch(self, path: str, body: dict) -> dict:
        url = self.base_url + path
        data = json.dumps(body).encode()
        req = Request(url, data=data, headers=self._headers(), method="PATCH")
        try:
            with urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            raise IluminatyError(e.code, e.read().decode(errors="replace"))
        except URLError as e:
            raise IluminatyError(0, str(e.reason))

    def _delete(self, path: str) -> dict:
        req = Request(self.base_url + path, headers=self._headers(), method="DELETE")
        try:
            with urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            raise IluminatyError(e.code, e.read().decode(errors="replace"))
        except URLError as e:
            raise IluminatyError(0, str(e.reason))

    # ── Vision ─────────────────────────────────────────────────────────────

    def see_now(self, monitor: Optional[int] = None, mode: str = "low_res") -> dict:
        """
        Current screen image + IPA context.
        Primary tool — use at session start and when you need to see.

        Args:
            monitor: specific monitor (1, 2, 3...) or None for active
            mode: "low_res" (~5K tokens) or "high_res"
        Returns raw dict from server (includes image_base64, scene, events, ocr_snippets)
        """
        params: dict = {"mode": mode}
        if monitor is not None:
            params["monitor"] = monitor
        return self._get("/mcp/see_now", **params)

    def see(self, monitor: Optional[int] = None, ocr: bool = True) -> Frame:
        """Get current frame as a Frame object."""
        params: dict = {"include_image": "true", "ocr": str(ocr).lower()}
        if monitor is not None:
            params["monitor_id"] = monitor
        d = self._get("/vision/snapshot", **params)
        return Frame(
            timestamp=d.get("timestamp", time.time()),
            width=d.get("width", 0),
            height=d.get("height", 0),
            monitor_id=d.get("monitor_id", 0),
            change_score=d.get("change_score", 0.0),
            mime_type=d.get("mime_type", "image/webp"),
            image_base64=d.get("image_base64"),
        )

    def read(self, monitor: Optional[int] = None,
             region: Optional[tuple] = None) -> OCRResult:
        """
        OCR text from screen or region.

        Args:
            monitor: monitor ID (None = active)
            region: (x, y, w, h) to scope OCR to a specific area
        """
        t0 = time.monotonic()
        params: dict = {}
        if monitor is not None:
            params["monitor_id"] = monitor
        if region:
            params.update(region_x=region[0], region_y=region[1],
                         region_w=region[2], region_h=region[3])
        d = self._get("/vision/ocr", **params)
        return OCRResult(
            text=d.get("text", d.get("ocr_text", "")),
            blocks=d.get("blocks", []),
            confidence=d.get("confidence", 0.0),
            engine=d.get("engine", "none"),
            monitor_id=d.get("monitor_id", monitor or 0),
            latency_ms=(time.monotonic() - t0) * 1000,
        )

    def what_changed(self, seconds: float = 10.0,
                     monitor: Optional[int] = None) -> dict:
        """What changed on screen in the last N seconds."""
        params: dict = {"seconds": seconds}
        if monitor is not None:
            params["monitor"] = monitor
        return self._get("/mcp/what_changed", **params)

    def spatial_context(self) -> dict:
        """
        Full workspace map: all monitors, windows, cursor position.
        Run at session start (~50 tokens).
        """
        return self._get("/spatial/state", include_windows="true")

    def perception(self) -> dict:
        """Raw IPA state: scene, motion, change_score, attention targets."""
        return self._get("/perception/state")

    def world_state(self) -> dict:
        """WorldState: task phase, affordances, readiness, uncertainty."""
        return self._get("/perception/world")

    # ── Actions ────────────────────────────────────────────────────────────

    def act(self, action: str, target: Optional[str] = None,
            x: Optional[int] = None, y: Optional[int] = None,
            text: Optional[str] = None, key: Optional[str] = None,
            monitor: Optional[int] = None) -> dict:
        """
        Execute a single OS action.

        Args:
            action: click | double_click | right_click | type | key | scroll | move_mouse
            target: element name for smart_locate ("Save button", "username field")
            x, y: explicit coordinates (if target not given)
            text: text to type (for action="type")
            key: key combo (for action="key", e.g. "ctrl+s")
            monitor: monitor ID
        """
        body: dict = {"action": action}
        if target:
            body["target"] = target
        if x is not None:
            body["x"] = x
        if y is not None:
            body["y"] = y
        if text:
            body["text"] = text
        if key:
            body["key"] = key
        if monitor is not None:
            body["monitor"] = monitor
        return self._post("/actions/act", body)

    def click(self, target: str, monitor: Optional[int] = None) -> dict:
        """Click an element by name."""
        return self.act("click", target=target, monitor=monitor)

    def type_text(self, text: str, target: Optional[str] = None) -> dict:
        """Type text, optionally into a named field."""
        return self.act("type", target=target, text=text)

    def press(self, key: str) -> dict:
        """Press a key or combo (e.g. 'ctrl+s', 'escape', 'enter')."""
        return self.act("key", key=key)

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> dict:
        """Drag from (x1,y1) to (x2,y2)."""
        return self._post("/actions/drag",
                         {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    # ── Windows ────────────────────────────────────────────────────────────

    def list_windows(self, monitor: Optional[int] = None,
                     title_contains: Optional[str] = None) -> list[dict]:
        """List visible windows."""
        params: dict = {}
        if monitor is not None:
            params["monitor_id"] = monitor
        if title_contains:
            params["title_contains"] = title_contains
        d = self._get("/windows/list", **params)
        return d.get("windows", [])

    def close_window(self, handle: int) -> bool:
        """Close window by OS handle."""
        d = self._post(f"/windows/close?handle={handle}")
        return bool(d.get("success"))

    def focus_window(self, title: str) -> bool:
        """Bring window with matching title to front."""
        d = self._post(f"/windows/focus?title={quote(title)}")
        return bool(d.get("success"))

    # ── Watch / Wait ───────────────────────────────────────────────────────

    def watch_until(self, condition: str, timeout: float = 30.0,
                    text: Optional[str] = None,
                    monitor: Optional[int] = None) -> WatchResult:
        """
        Wait for a screen condition. Zero tokens consumed while waiting.

        Conditions: page_loaded | motion_stopped | motion_started |
                    text_appeared | text_disappeared | window_opened |
                    window_closed | build_passed | build_failed |
                    element_visible | idle

        Args:
            condition: one of the above
            timeout: max seconds to wait
            text: required for text_appeared/text_disappeared
            monitor: monitor to watch (None = active)
        """
        body: dict = {"condition": condition, "timeout": timeout}
        if text:
            body["text"] = text
        if monitor is not None:
            body["monitor"] = monitor
        d = self._post("/watch/notify", body)
        return WatchResult(
            triggered=d.get("triggered", False),
            condition=d.get("condition", condition),
            elapsed_s=d.get("elapsed_s", 0.0),
            reason=d.get("reason", ""),
            evidence=d.get("evidence", ""),
            monitor_id=d.get("monitor_id", monitor or 0),
            timed_out=d.get("timed_out", False),
        )

    # ── Session memory ─────────────────────────────────────────────────────

    def get_memory(self) -> dict:
        """Load context from previous session."""
        return self._get("/memory/session/latest")

    def save_memory(self) -> dict:
        """Save current context for next session."""
        return self._post("/memory/session/save")

    # ── Agents ─────────────────────────────────────────────────────────────

    def register_agent(self, name: str, role: str = "executor",
                       monitors: Optional[list] = None,
                       autonomy: str = "suggest") -> AgentSession:
        """
        Register this client as a named agent with a role.

        Roles: observer | planner | executor | verifier | any
        Monitors: [] = all monitors
        """
        d = self._post("/agents/register", {
            "name": name, "role": role,
            "monitors": monitors or [], "autonomy": autonomy,
        })
        return AgentSession(
            agent_id=d["agent_id"], name=d["name"],
            role=d["role"], autonomy=d["autonomy"],
            monitors=d.get("monitors", []),
            allowed_tools=d.get("allowed_tools"),
        )

    def update_agent(self, agent_id: str, role: Optional[str] = None,
                     monitors: Optional[list] = None,
                     custom_tools: Optional[list] = None) -> dict:
        """Update agent role/monitors/tools at runtime."""
        body: dict = {}
        if role:
            body["role"] = role
        if monitors is not None:
            body["monitors"] = monitors
        if custom_tools is not None:
            body["custom_tools"] = custom_tools
        return self._patch(f"/agents/{agent_id}", body)

    def remove_agent(self, agent_id: str) -> bool:
        d = self._delete(f"/agents/{agent_id}")
        return d.get("status") == "unregistered"

    def list_agents(self) -> list[AgentSession]:
        d = self._get("/agents")
        return [
            AgentSession(
                agent_id=a["agent_id"], name=a["name"],
                role=a["role"], autonomy=a["autonomy"],
                monitors=a.get("monitors", []),
            )
            for a in d.get("agents", [])
        ]

    def dispatch_task(self, agent_id: str, task: str,
                      monitor: int = 1, priority: float = 0.5) -> dict:
        """Dispatch a task to a specific agent (PLANNER→EXECUTOR)."""
        return self._post("/agents/dispatch", {
            "to_agent": agent_id, "task": task,
            "monitor": monitor, "priority": priority,
        })

    def inbox(self, agent_id: str, max_count: int = 10) -> list[dict]:
        """Poll pending messages for an agent."""
        d = self._get(f"/agents/{agent_id}/messages", max_count=max_count)
        return d.get("messages", [])

    def report(self, agent_id: str, status: str, result: str,
               to_agent: Optional[str] = None) -> dict:
        """Report task result to another agent or broadcast."""
        return self._post("/agents/report", {
            "from_agent": agent_id, "status": status,
            "result": result, "to_agent": to_agent or "*",
        })

    # ── Recording ──────────────────────────────────────────────────────────

    def start_recording(self, monitors: Optional[list] = None,
                        max_seconds: int = 300,
                        fmt: str = "webm") -> dict:
        """
        Start recording screen to disk (opt-in, zero-disk by default).

        Args:
            monitors: [1, 2, 3] or None for all
            max_seconds: auto-stop after N seconds (default 5 min)
            fmt: "webm" or "gif"
        Returns: {"id": recording_id, "path": output_path}
        """
        return self._post("/recording/start", {
            "monitors": monitors or [],
            "max_seconds": max_seconds,
            "format": fmt,
        })

    def stop_recording(self, recording_id: str) -> dict:
        """Stop recording. Returns {"path": ..., "duration_s": ..., "size_mb": ...}"""
        return self._post(f"/recording/stop/{recording_id}")

    def recording_status(self) -> dict:
        """Get current recording state."""
        return self._get("/recording/status")

    # ── Shell / Files ──────────────────────────────────────────────────────

    def run(self, command: str, timeout: float = 30.0) -> dict:
        """Run shell command. Returns {stdout, stderr, return_code, duration_ms}."""
        return self._post(f"/terminal/exec?cmd={quote(command)}&timeout={timeout}")

    def read_file(self, path: str) -> str:
        """Read file contents."""
        d = self._get("/files/read", path=path)
        return d.get("content", "")

    def write_file(self, path: str, content: str) -> bool:
        """Write file (auto-backup)."""
        d = self._post("/files/write", {"path": path, "content": content})
        return bool(d.get("success"))

    # ── System ─────────────────────────────────────────────────────────────

    def health(self) -> dict:
        return self._get("/health")

    def status(self) -> dict:
        return self._get("/buffer/stats")

    def monitors(self) -> list[dict]:
        d = self._get("/spatial/state")
        return d.get("monitors", [])

    # ── Streaming ──────────────────────────────────────────────────────────

    def stream(self, fps: float = 2.0,
               monitor: Optional[int] = None) -> Iterator[Frame]:
        """
        Yield frames continuously at given FPS.
        Only yields frames that actually changed (change_score > 0).
        """
        interval = 1.0 / fps
        last_hash = None
        while True:
            try:
                params: dict = {"include_image": "true"}
                if monitor is not None:
                    params["monitor_id"] = monitor
                d = self._get("/vision/snapshot", **params)
                h = d.get("phash")
                if h != last_hash:
                    last_hash = h
                    yield Frame(
                        timestamp=d.get("timestamp", time.time()),
                        width=d.get("width", 0),
                        height=d.get("height", 0),
                        monitor_id=d.get("monitor_id", 0),
                        change_score=d.get("change_score", 0.0),
                        mime_type=d.get("mime_type", "image/webp"),
                        image_base64=d.get("image_base64"),
                    )
            except IluminatyError:
                pass
            time.sleep(interval)

    # ── Context manager ────────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __repr__(self):
        return f"Iluminaty('{self.base_url}')"
