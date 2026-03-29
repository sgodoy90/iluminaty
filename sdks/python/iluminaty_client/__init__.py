"""
ILUMINATY Python Client SDK
=============================
pip install iluminaty-client

Usage:
    from iluminaty_client import Iluminaty

    eye = Iluminaty()
    frame = eye.see()              # what's on screen right now
    text = eye.read()              # OCR text
    diff = eye.what_changed()      # visual diff
    context = eye.what_doing()     # user workflow
    eye.mark(100, 200, "Bug here") # annotate screen
    
    # Stream
    for frame in eye.watch():
        print(f"Frame: {frame.width}x{frame.height}")
    
    # Ask AI
    answer = eye.ask("gemini", "What bug do you see?", api_key="...")
"""

import json
import time
import base64
from typing import Optional, Iterator
from dataclasses import dataclass
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import URLError


@dataclass
class Frame:
    """A captured screen frame."""
    timestamp: float
    width: int
    height: int
    size_bytes: int
    format: str
    change_score: float
    image_base64: Optional[str] = None
    image_bytes: Optional[bytes] = None

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))


@dataclass
class OCRResult:
    """OCR text extraction result."""
    text: str
    blocks: list
    confidence: float
    engine: str
    block_count: int = 0


@dataclass
class DiffResult:
    """Visual diff result."""
    changed: bool
    change_percentage: float
    changed_cells: int
    total_cells: int
    description: str
    regions: list
    heatmap: Optional[list] = None


@dataclass
class ContextState:
    """User context/workflow state."""
    workflow: str
    confidence: float
    app: str
    title: str
    focus_level: str
    time_in_workflow: float
    switches_5min: int
    summary: str


@dataclass 
class AudioState:
    """Audio state."""
    level: float
    is_speech: bool


@dataclass
class AIResponse:
    """Response from an AI provider."""
    text: str
    provider: str
    model: str
    latency_ms: float
    tokens_used: int = 0


@dataclass
class Alert:
    """Watchdog alert."""
    id: str
    trigger: str
    severity: str
    message: str
    time: str
    acknowledged: bool


class IluminatyError(Exception):
    """Error from ILUMINATY API."""
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"[{status}] {message}")


class Iluminaty:
    """
    ILUMINATY Python Client.
    
    Give any AI eyes on your screen.
    
    Usage:
        eye = Iluminaty()                    # connect to local daemon
        eye = Iluminaty("http://host:8420")  # connect to remote
        eye = Iluminaty(api_key="secret")    # with auth
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8420", api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _request(self, method: str, path: str, params: Optional[dict] = None) -> dict:
        """Make HTTP request to ILUMINATY API."""
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        data = b"" if method == "POST" else None
        req = Request(url, data=data, headers=headers, method=method)

        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except URLError as e:
            if hasattr(e, 'code'):
                raise IluminatyError(e.code, str(e.reason))
            raise IluminatyError(0, f"Connection failed: {e.reason}")

    def _get(self, path: str, **params) -> dict:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", path, clean or None)

    def _post(self, path: str, **params) -> dict:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._request("POST", path, clean or None)

    # ─── Core: See ───

    def see(self, include_image: bool = True, ocr: bool = False) -> dict:
        """
        See what's on the screen right now.
        Returns enriched snapshot with image, OCR, context, AI prompt.
        """
        return self._get("/vision/snapshot",
                        include_image=str(include_image).lower(),
                        ocr=str(ocr).lower())

    def see_frame(self) -> Frame:
        """Get the latest frame metadata."""
        data = self._get("/frame/latest", base64="true")
        return Frame(
            timestamp=data["timestamp"],
            width=data["width"],
            height=data["height"],
            size_bytes=data["size_bytes"],
            format=data.get("format", "image/webp"),
            change_score=data.get("change_score", 0),
            image_base64=data.get("image_base64"),
        )

    def see_raw(self) -> bytes:
        """Get the latest frame as raw image bytes."""
        url = self.base_url + "/frame/latest"
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        req = Request(url, headers=headers)
        with urlopen(req, timeout=10) as resp:
            return resp.read()

    # ─── Core: Read ───

    def read(self, region: Optional[tuple] = None) -> OCRResult:
        """
        Read text from the screen using OCR.
        Optionally read only a specific region: (x, y, width, height)
        """
        params = {}
        if region:
            params = {"region_x": region[0], "region_y": region[1],
                     "region_w": region[2], "region_h": region[3]}
        data = self._get("/vision/ocr", **params)
        return OCRResult(
            text=data.get("text", ""),
            blocks=data.get("blocks", []),
            confidence=data.get("confidence", 0),
            engine=data.get("engine", "none"),
            block_count=data.get("block_count", 0),
        )

    # ─── Core: Diff ───

    def what_changed(self) -> DiffResult:
        """See what changed on screen since last check."""
        data = self._get("/vision/diff")
        return DiffResult(
            changed=data["changed"],
            change_percentage=data["change_percentage"],
            changed_cells=data["changed_cells"],
            total_cells=data["total_cells"],
            description=data.get("description", ""),
            regions=data.get("regions", []),
            heatmap=data.get("heatmap"),
        )

    # ─── Core: Context ───

    def what_doing(self) -> ContextState:
        """Get what the user is doing right now."""
        data = self._get("/context/state")
        return ContextState(
            workflow=data["workflow"],
            confidence=data["confidence"],
            app=data["app"],
            title=data.get("title", ""),
            focus_level="HIGH" if data.get("is_focused") else "LOW",
            time_in_workflow=data.get("time_in_workflow_seconds", 0),
            switches_5min=data.get("switches_5min", 0),
            summary=data.get("summary", ""),
        )

    # ─── Core: Audio ───

    def hear(self) -> AudioState:
        """Get current audio level and speech detection."""
        data = self._get("/audio/level")
        return AudioState(
            level=data.get("level", 0),
            is_speech=data.get("is_speech", False),
        )

    def transcribe(self, seconds: float = 10) -> str:
        """Transcribe recent audio."""
        data = self._get("/audio/transcribe", seconds=seconds)
        return data.get("text", "")

    # ─── Core: Annotate ───

    def mark(self, x: int, y: int, text: str = "", 
             type: str = "rect", width: int = 100, height: int = 50,
             color: str = "#FF0000") -> str:
        """
        Draw an annotation on the screen.
        Returns annotation ID.
        """
        data = self._post("/annotations/add",
                         type=type, x=x, y=y,
                         width=width, height=height,
                         color=color, text=text)
        return data.get("id", "")

    def clear_marks(self):
        """Clear all annotations."""
        self._post("/annotations/clear")

    # ─── Core: AI ───

    def ask(self, provider: str, prompt: str, api_key: str,
            model: Optional[str] = None) -> AIResponse:
        """
        Send current screen to an AI and get a response.
        
        provider: "gemini", "openai", "claude"
        """
        params = {"provider": provider, "prompt": prompt,
                 "provider_api_key": api_key}
        if model:
            params["model"] = model
        data = self._post("/ai/ask", **params)
        return AIResponse(
            text=data.get("text", ""),
            provider=data.get("provider", provider),
            model=data.get("model", ""),
            latency_ms=data.get("latency_ms", 0),
            tokens_used=data.get("tokens_used", 0),
        )

    # ─── Streaming ───

    def watch(self, fps: float = 1.0) -> Iterator[Frame]:
        """
        Stream frames in real-time.
        Yields Frame objects at the specified FPS.
        """
        interval = 1.0 / fps
        while True:
            try:
                frame = self.see_frame()
                yield frame
            except Exception:
                pass
            time.sleep(interval)

    # ─── System ───

    def status(self) -> dict:
        """Get ILUMINATY system status."""
        return self._get("/buffer/stats")

    def health(self) -> dict:
        """Health check."""
        return self._get("/health")

    def monitors(self) -> dict:
        """Get monitor info."""
        return self._get("/monitors")

    def alerts(self, unacknowledged_only: bool = False) -> list:
        """Get watchdog alerts."""
        data = self._get("/watchdog/alerts")
        return data.get("alerts", [])

    def config(self, **kwargs) -> dict:
        """Change ILUMINATY config on the fly."""
        return self._post("/config", **kwargs)

    def flush(self):
        """Destroy all visual data in buffer."""
        self._post("/buffer/flush")

    # ─── Context manager ───

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __repr__(self):
        return f"Iluminaty({self.base_url})"
