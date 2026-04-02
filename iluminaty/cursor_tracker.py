"""
ILUMINATY - Cursor Tracker (Phase A)
====================================
Low-cost RAM-only cursor proprioception.
Tracks pointer position continuously so action layer has fresh coordinates.
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class CursorTracker:
    """Continuously samples cursor position via ActionBridge.get_mouse_position()."""

    def __init__(
        self,
        *,
        actions=None,
        poll_ms: int = 20,
        max_history: int = 128,
    ):
        self._actions = actions
        self._poll_ms = max(5, int(poll_ms))
        self._max_history = max(16, int(max_history))
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last = {
            "x": 0,
            "y": 0,
            "timestamp_ms": 0,
            "moved": False,
        }
        self._history: list[dict] = []
        self._samples = 0
        self._errors = 0
        self._last_error = ""

    def set_actions(self, actions) -> None:
        self._actions = actions

    def _read_cursor(self) -> dict:
        if self._actions and hasattr(self._actions, "get_mouse_position"):
            pos = self._actions.get_mouse_position() or {}
            return {
                "x": int(pos.get("x", 0)),
                "y": int(pos.get("y", 0)),
            }
        return {"x": 0, "y": 0}

    def sample_once(self) -> dict:
        now_ms = int(time.time() * 1000)
        try:
            pos = self._read_cursor()
            with self._lock:
                prev_x = int(self._last.get("x", 0))
                prev_y = int(self._last.get("y", 0))
                moved = (int(pos["x"]) != prev_x) or (int(pos["y"]) != prev_y)
                self._last = {
                    "x": int(pos["x"]),
                    "y": int(pos["y"]),
                    "timestamp_ms": now_ms,
                    "moved": bool(moved),
                }
                self._history.append(dict(self._last))
                if len(self._history) > self._max_history:
                    self._history = self._history[-self._max_history :]
                self._samples += 1
                return dict(self._last)
        except Exception as e:
            with self._lock:
                self._errors += 1
                self._last_error = str(e)
                return dict(self._last)

    def _loop(self) -> None:
        while self._running:
            self.sample_once()
            time.sleep(self._poll_ms / 1000.0)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="cursor-tracker")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.5)
            self._thread = None

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._last)

    def recent(self, n: int = 16) -> list[dict]:
        n = max(1, int(n))
        with self._lock:
            return list(self._history[-n:])

    def status(self) -> dict:
        now_ms = int(time.time() * 1000)
        with self._lock:
            last = dict(self._last)
            return {
                "running": bool(self._running),
                "poll_ms": int(self._poll_ms),
                "samples": int(self._samples),
                "errors": int(self._errors),
                "last_error": str(self._last_error),
                "cursor": last,
                "staleness_ms": max(0, int(now_ms - int(last.get("timestamp_ms", 0) or 0))),
            }
