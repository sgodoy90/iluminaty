"""
ILUMINATY - Recording Engine
==============================
Opt-in screen recording to local disk. Zero-disk principle unchanged —
recording is explicitly disabled by default and never runs unless started
by the user via dashboard toggle, MCP tool, or API call.

Architecture:
  RingBuffer (RAM, unchanged) ──poll──▶ RecordingEngine ──encode──▶ ~/.iluminaty/recordings/

The ring buffer is never modified. RecordingEngine reads existing slots
independently via a background thread.

Formats:
  webm  — cv2.VideoWriter (requires opencv-python, already in [ocr] deps)
  gif   — Pillow (already in core deps)
  mp4   — cv2.VideoWriter with mp4v codec

Storage:
  ~/.iluminaty/recordings/
  rec_YYYYMMDD_HHMMSS_M{monitor}.{ext}
  Max 10 recordings kept (oldest auto-deleted on overflow)
"""
from __future__ import annotations

import io
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .ring_buffer import RingBuffer

log = logging.getLogger("iluminaty.recording")

DEFAULT_RECORDING_DIR = Path.home() / ".iluminaty" / "recordings"
MAX_RECORDINGS_KEPT = 10
MAX_RECORDING_SECONDS = 600   # hard cap — 10 minutes


@dataclass
class RecordingSession:
    id: str
    monitors: list[int]           # [] = all
    fmt: str                       # webm | gif | mp4
    max_seconds: int
    fps: float
    started_at: float
    output_paths: dict[int, str]   # monitor_id → path
    active: bool = True
    stopped_at: Optional[float] = None
    frames_written: int = 0
    error: Optional[str] = None

    def duration_s(self) -> float:
        end = self.stopped_at or time.time()
        return round(end - self.started_at, 1)

    def size_mb(self) -> float:
        total = 0
        for p in self.output_paths.values():
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        return round(total / 1_048_576, 2)

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "monitors":     self.monitors,
            "format":       self.fmt,
            "max_seconds":  self.max_seconds,
            "started_at":   self.started_at,
            "duration_s":   self.duration_s(),
            "active":       self.active,
            "frames":       self.frames_written,
            "size_mb":      self.size_mb(),
            "paths":        self.output_paths,
            "error":        self.error,
        }


class RecordingEngine:
    """
    Reads frames from RingBuffer and encodes them to disk.
    Does not modify the ring buffer — read-only consumer.
    """

    def __init__(self, ring_buffer: "RingBuffer",
                 output_dir: Optional[str] = None):
        self._buffer = ring_buffer
        self._dir = Path(output_dir or DEFAULT_RECORDING_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, RecordingSession] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────

    def start(
        self,
        monitors: Optional[list[int]] = None,
        max_seconds: int = 300,
        fmt: str = "gif",
        fps: float = 2.0,
    ) -> RecordingSession:
        """Start recording. Returns session immediately (non-blocking)."""
        fmt = fmt.lower()
        if fmt not in ("webm", "gif", "mp4"):
            fmt = "gif"
        max_seconds = min(max(5, max_seconds), MAX_RECORDING_SECONDS)
        fps = min(max(0.5, fps), 10.0)

        session_id = uuid.uuid4().hex[:12]
        ts = time.strftime("%Y%m%d_%H%M%S")
        mon_list = list(monitors) if monitors else []  # [] = capture all available

        # Determine which monitors to record (resolve at start time)
        available = self._available_monitor_ids()
        target_monitors = [m for m in mon_list if m in available] if mon_list else available
        if not target_monitors:
            target_monitors = available or [1]

        # Build output paths
        paths: dict[int, str] = {}
        for mid in target_monitors:
            fname = f"rec_{ts}_M{mid}_{session_id[:6]}.{fmt}"
            paths[mid] = str(self._dir / fname)

        session = RecordingSession(
            id=session_id,
            monitors=target_monitors,
            fmt=fmt,
            max_seconds=max_seconds,
            fps=fps,
            started_at=time.time(),
            output_paths=paths,
            active=True,
        )

        with self._lock:
            self._sessions[session_id] = session

        # One thread per monitor
        for mid in target_monitors:
            t = threading.Thread(
                target=self._record_monitor,
                args=(session_id, mid),
                daemon=True,
                name=f"rec-{session_id[:6]}-M{mid}",
            )
            self._threads[f"{session_id}:{mid}"] = t
            t.start()

        log.info("Recording started: %s monitors=%s fmt=%s max=%ds",
                 session_id, target_monitors, fmt, max_seconds)
        return session

    def stop(self, session_id: str) -> Optional[RecordingSession]:
        """Stop a recording session. Returns final session state."""
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            session.active = False
            session.stopped_at = time.time()

        log.info("Recording stopped: %s duration=%.1fs frames=%d",
                 session_id, session.duration_s(), session.frames_written)
        self._cleanup_old()
        return session

    def stop_all(self) -> list[RecordingSession]:
        """Stop all active recordings."""
        with self._lock:
            ids = [sid for sid, s in self._sessions.items() if s.active]
        return [s for sid in ids if (s := self.stop(sid)) is not None]

    def status(self) -> dict:
        with self._lock:
            active = [s.to_dict() for s in self._sessions.values() if s.active]
            recent = [s.to_dict() for s in self._sessions.values() if not s.active][-5:]
        return {
            "active": active,
            "recent": recent,
            "output_dir": str(self._dir),
            "enabled": True,
        }

    def get_session(self, session_id: str) -> Optional[RecordingSession]:
        return self._sessions.get(session_id)

    # ── Internal ───────────────────────────────────────────────────────────

    def _available_monitor_ids(self) -> list[int]:
        """Get monitor IDs that currently have frames in the buffer."""
        try:
            seen: set[int] = set()
            with self._buffer._lock:
                for slot in list(self._buffer._buffer):
                    mid = getattr(slot, "monitor_id", 0)
                    if mid and mid > 0:
                        seen.add(mid)
            return sorted(seen) or [1]
        except Exception:
            return [1]

    def _get_latest_frame_for_monitor(self, monitor_id: int):
        """Get the most recent frame slot for a monitor."""
        try:
            with self._buffer._lock:
                slots = list(self._buffer._buffer)
            for slot in reversed(slots):
                if getattr(slot, "monitor_id", 0) == monitor_id:
                    return slot
        except Exception:
            pass
        return None

    def _record_monitor(self, session_id: str, monitor_id: int) -> None:
        """Background thread: records one monitor."""
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return

        output_path = session.output_paths[monitor_id]
        fmt = session.fmt
        fps = session.fps
        interval = 1.0 / fps
        deadline = session.started_at + session.max_seconds

        frames_data: list[bytes] = []
        last_hash: Optional[str] = None

        try:
            while True:
                with self._lock:
                    s = self._sessions.get(session_id)
                if not s or not s.active or time.time() >= deadline:
                    break

                slot = self._get_latest_frame_for_monitor(monitor_id)
                if slot and getattr(slot, "phash", None) != last_hash:
                    last_hash = slot.phash
                    frames_data.append(slot.frame_bytes)
                    with self._lock:
                        if session_id in self._sessions:
                            self._sessions[session_id].frames_written += 1

                time.sleep(interval)

        except Exception as e:
            log.error("Recording thread error M%d: %s", monitor_id, e)
            with self._lock:
                if session_id in self._sessions:
                    self._sessions[session_id].error = str(e)

        # Encode collected frames to file
        if frames_data:
            try:
                if fmt == "gif":
                    self._write_gif(frames_data, output_path, fps)
                else:
                    self._write_video(frames_data, output_path, fps, fmt)
                log.info("Recording saved: %s (%d frames)", output_path, len(frames_data))
            except Exception as e:
                log.error("Recording encode error: %s", e)
                with self._lock:
                    if session_id in self._sessions:
                        self._sessions[session_id].error = f"encode: {e}"
        else:
            log.warning("Recording M%d: no frames captured", monitor_id)

    def _write_gif(self, frames: list[bytes], path: str, fps: float) -> None:
        """Write frames as animated GIF using Pillow."""
        from PIL import Image
        duration_ms = int(1000 / fps)
        images = []
        for fb in frames:
            try:
                img = Image.open(io.BytesIO(fb)).convert("RGB")
                # Resize to max 960px wide for reasonable GIF size
                if img.width > 960:
                    ratio = 960 / img.width
                    img = img.resize((960, int(img.height * ratio)), Image.LANCZOS)
                # Quantize to 128 colors for smaller GIF
                images.append(img.quantize(colors=128, method=Image.Quantize.FASTOCTREE))
            except Exception:
                continue

        if not images:
            return

        images[0].save(
            path, format="GIF", save_all=True,
            append_images=images[1:],
            duration=duration_ms, loop=0,
            optimize=True,
        )

    def _write_video(self, frames: list[bytes], path: str,
                     fps: float, fmt: str) -> None:
        """Write frames as WebM/MP4 using OpenCV."""
        try:
            import cv2
            import numpy as np
            from PIL import Image
        except ImportError:
            # Fallback to GIF if cv2 not available
            log.warning("opencv not available, falling back to GIF")
            gif_path = path.rsplit(".", 1)[0] + ".gif"
            self._write_gif(frames, gif_path, fps)
            return

        # Get frame dimensions from first frame
        first = Image.open(io.BytesIO(frames[0]))
        w, h = first.size

        fourcc = cv2.VideoWriter_fourcc(*("VP80" if fmt == "webm" else "mp4v"))
        writer = cv2.VideoWriter(path, fourcc, fps, (w, h))

        for fb in frames:
            try:
                img = Image.open(io.BytesIO(fb)).convert("RGB")
                if img.size != (w, h):
                    img = img.resize((w, h))
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                writer.write(frame)
            except Exception:
                continue

        writer.release()

    def _cleanup_old(self) -> None:
        """Delete oldest recordings if over MAX_RECORDINGS_KEPT."""
        try:
            files = sorted(
                self._dir.glob("rec_*"),
                key=lambda p: p.stat().st_mtime,
            )
            while len(files) > MAX_RECORDINGS_KEPT:
                files.pop(0).unlink(missing_ok=True)
        except Exception:
            pass
