"""
ILUMINATY - IPA v3 Bridge
==========================
Connects IPA v3 (SigLIP semantic stream) to the iluminaty server pipeline.

Responsibilities:
  1. Feed frames from the iluminaty RingBuffer into IPAEngine
  2. Run as a background thread alongside perception.py's fast loop
  3. Expose gate_event() — the most significant recent visual event
     for MCP tools (see_now, what_changed)
  4. Provide OCR-augmented context when OCR text is available

IPA v3 operates at 3fps by default (configurable). The fast semantic loop
in perception.py continues at 10Hz for motion/scene classification.
IPA v3 adds semantic patch embeddings on top — enabling real similarity
search and change detection at the embedding level.

Hardware levels (auto-detected, no config needed):
  Level 0 — imagehash only (no torch) — instant load, ~0ms/frame
  Level 1 — SigLIP on CPU            — ~2-5s load, ~50-200ms/frame
  Level 2 — SigLIP on GPU            — ~1-2s load, ~5-30ms/frame
"""
from __future__ import annotations

import io
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .ring_buffer import RingBuffer

log = logging.getLogger("iluminaty.ipa_bridge")


@dataclass
class GateEvent:
    """A visual event significant enough to wake the LLM."""
    timestamp: float
    event_type: str          # scene_change | motion_start | motion_end | content_loaded | idle
    description: str         # human-readable for LLM context
    monitor_id: int = 1
    motion_type: str = "static"
    n_changed_patches: int = 0
    scene_state: str = "unknown"
    confidence: float = 0.0
    frame_ref: str = ""      # ref_id into temporal_store for image retrieval
    ocr_text: str = ""       # OCR text available at this moment


class IPABridge:
    """Feeds iluminaty frames into IPA v3 and tracks significant visual events.

    Usage (from server.py startup):
        bridge = IPABridge(ring_buffer)
        bridge.start()

    Then from MCP handlers:
        event = bridge.gate_event()          # latest significant event
        ctx   = bridge.visual_context(30)    # last 30s of context
        frame = bridge.latest_frame_b64()    # current frame as base64
    """

    # Minimum seconds between gate events of the same type (debounce)
    _GATE_DEBOUNCE: dict[str, float] = {
        "scene_change":    2.0,
        "motion_start":    1.0,
        "motion_end":      1.5,
        "content_loaded":  3.0,
        "idle":           10.0,
    }

    def __init__(
        self,
        ring_buffer: "RingBuffer",
        fps: float = 3.0,
        monitor_id: int = 1,
    ):
        self._buffer = ring_buffer
        self._fps = max(0.5, min(10.0, fps))
        self._monitor_id = monitor_id
        self._interval = 1.0 / self._fps

        # IPA engine — lazy init on first frame
        self._engine = None
        self._engine_lock = threading.Lock()
        self._engine_ready = False

        # Gate event state
        self._gate_events: list[GateEvent] = []
        self._last_gate_by_type: dict[str, float] = {}
        self._last_motion_type: str = "static"
        self._last_scene: str = "unknown"
        self._lock = threading.Lock()

        # Thread
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._frame_count = 0
        self._error_count = 0

    def start(self) -> None:
        """Start the IPA processing loop."""
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="ipa-bridge",
        )
        self._thread.start()
        log.info("IPA bridge started (%.1ffps, monitor=%d)", self._fps, self._monitor_id)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    # ── Public API ────────────────────────────────────────────────────────────

    def gate_event(self, max_age_s: float = 30.0) -> Optional[GateEvent]:
        """Return the most recent significant visual event within max_age_s."""
        with self._lock:
            if not self._gate_events:
                return None
            cutoff = time.time() - max_age_s
            recent = [e for e in self._gate_events if e.timestamp >= cutoff]
            return recent[-1] if recent else None

    def recent_events(self, seconds: float = 30.0) -> list[GateEvent]:
        """Return all gate events in the last N seconds."""
        with self._lock:
            cutoff = time.time() - seconds
            return [e for e in self._gate_events if e.timestamp >= cutoff]

    def visual_context(self, seconds: float = 30.0) -> Optional[dict]:
        """Get IPA v3 visual context dict for MCP response."""
        with self._engine_lock:
            if not self._engine_ready or self._engine is None:
                return None
            try:
                ctx = self._engine.context(seconds=seconds)
                return ctx.to_dict()
            except Exception as e:
                log.debug("visual_context error: %s", e)
                return None

    def motion_now(self, seconds: float = 5.0) -> Optional[dict]:
        """Get current motion field from IPA v3."""
        with self._engine_lock:
            if not self._engine_ready or self._engine is None:
                return None
            try:
                return self._engine.motion(seconds=seconds).to_dict()
            except Exception as e:
                log.debug("motion_now error: %s", e)
                return None

    def latest_frame_b64(self) -> Optional[str]:
        """Get the latest captured frame as base64 webp from the ring buffer."""
        try:
            slot = self._buffer.get_latest()
            if slot is None:
                return None
            import base64
            return base64.b64encode(slot.frame_bytes).decode()
        except Exception:
            return None

    def stats(self) -> dict:
        with self._engine_lock:
            eng_stats = {}
            if self._engine is not None:
                try:
                    eng_stats = self._engine.status()
                except Exception:
                    pass
        with self._lock:
            n_events = len(self._gate_events)
        return {
            "running": self._running,
            "fps": self._fps,
            "monitor_id": self._monitor_id,
            "frames_processed": self._frame_count,
            "errors": self._error_count,
            "engine_ready": self._engine_ready,
            "gate_events_buffered": n_events,
            "engine": eng_stats,
        }

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _get_engine(self):
        """Lazy-init IPA engine on first frame (model load is slow)."""
        with self._engine_lock:
            if self._engine is not None:
                return self._engine
            try:
                import sys
                # IPA v3 lives at <project_root>/ipa/
                root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if root not in sys.path:
                    sys.path.insert(0, root)

                from ipa.engine import IPAEngine

                fps = float(os.environ.get("ILUMINATY_IPA_FPS", str(self._fps)))
                device = os.environ.get("ILUMINATY_IPA_DEVICE", "auto")
                max_frames = int(os.environ.get("ILUMINATY_IPA_MAX_FRAMES", "3600"))

                self._engine = IPAEngine(config={
                    "device": device,
                    "int8": True,
                    "max_frames": max_frames,
                    "keyframe_interval_s": 10.0,
                    "bits": 3,
                    "similarity_threshold": 0.92,
                })
                self._engine_ready = True
                log.info("IPA v3 engine initialized (device=%s, max_frames=%d)", device, max_frames)
            except Exception as e:
                log.warning("IPA v3 engine init failed: %s — bridge will retry", e)
                self._error_count += 1
            return self._engine

    def _loop(self) -> None:
        last_ts = 0.0
        next_frame = time.time()

        while self._running:
            now = time.time()
            if now < next_frame:
                time.sleep(max(0, next_frame - now - 0.001))
                continue
            next_frame = now + self._interval

            try:
                self._process_frame()
            except Exception as e:
                log.debug("IPA bridge loop error: %s", e)
                self._error_count += 1
                time.sleep(0.5)

    def _process_frame(self) -> None:
        """Fetch latest frame from ring buffer and feed to IPA engine."""
        # Multi-monitor: try per-monitor first, fallback to latest
        try:
            slot = self._buffer.get_latest_for_monitor(self._monitor_id)
        except AttributeError:
            slot = None

        if slot is None:
            try:
                slot = self._buffer.get_latest()
            except Exception:
                slot = None

        if slot is None:
            return

        engine = self._get_engine()
        if engine is None:
            return

        # Convert frame bytes to PIL Image
        try:
            from PIL import Image as PILImage
            img = PILImage.open(io.BytesIO(slot.frame_bytes)).convert("RGB")
        except Exception as e:
            log.debug("Frame decode failed: %s", e)
            return

        # Get OCR text if available on the slot
        ocr_text = getattr(slot, "ocr_text", "") or ""

        # Feed to IPA engine
        metadata = {
            "monitor_id": self._monitor_id,
            "window_name": getattr(slot, "window_name", ""),
            "scene_hint": getattr(slot, "scene_hint", ""),
        }

        try:
            frame = engine.feed(img, timestamp=slot.timestamp, metadata=metadata)
        except Exception as e:
            log.debug("IPA engine.feed failed: %s", e)
            self._error_count += 1
            return

        self._frame_count += 1

        # Get motion and context for gate evaluation
        motion = engine.motion(seconds=3.0)
        self._evaluate_gate(frame, motion, ocr_text)

    def _evaluate_gate(self, frame, motion, ocr_text: str) -> None:
        """Decide if this frame represents a significant event worth flagging."""
        now = time.time()
        current_motion = motion.motion_type
        current_scene = getattr(frame, "metadata", {}).get("scene_hint", "unknown")

        events_to_emit = []

        # Motion state transition
        if current_motion != self._last_motion_type:
            old = self._last_motion_type
            new = current_motion

            if old not in ("static", "idle") and new in ("static", "idle"):
                events_to_emit.append(GateEvent(
                    timestamp=now,
                    event_type="motion_end",
                    description=f"{old} ended — screen settled",
                    monitor_id=self._monitor_id,
                    motion_type=new,
                    n_changed_patches=frame.n_changed,
                    scene_state=current_scene,
                    confidence=0.85,
                    ocr_text=ocr_text[:500],
                ))
            elif old in ("static", "idle") and new not in ("static", "idle"):
                events_to_emit.append(GateEvent(
                    timestamp=now,
                    event_type="motion_start",
                    description=f"{new} detected — {motion.detail}",
                    monitor_id=self._monitor_id,
                    motion_type=new,
                    n_changed_patches=frame.n_changed,
                    scene_state=current_scene,
                    confidence=0.8,
                    ocr_text=ocr_text[:500],
                ))

            self._last_motion_type = current_motion

        # Large burst of changes (content load, page change)
        if frame.n_changed > 100 and current_motion not in ("video",):
            events_to_emit.append(GateEvent(
                timestamp=now,
                event_type="content_loaded",
                description=f"Large content change: {frame.n_changed} patches ({current_motion})",
                monitor_id=self._monitor_id,
                motion_type=current_motion,
                n_changed_patches=frame.n_changed,
                scene_state=current_scene,
                confidence=0.75,
                ocr_text=ocr_text[:500],
            ))

        # Emit debounced events
        with self._lock:
            for evt in events_to_emit:
                last = self._last_gate_by_type.get(evt.event_type, 0.0)
                debounce = self._GATE_DEBOUNCE.get(evt.event_type, 1.0)
                if (now - last) >= debounce:
                    self._gate_events.append(evt)
                    self._last_gate_by_type[evt.event_type] = now
                    log.debug("Gate event: %s — %s", evt.event_type, evt.description)

            # Keep last 200 events
            if len(self._gate_events) > 200:
                self._gate_events = self._gate_events[-100:]
