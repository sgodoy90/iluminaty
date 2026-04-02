"""
ILUMINATY - Action Completion Watchers (Phase A)
=================================================
Uses change_score + frame cadence to detect probable action completion.
"""

from __future__ import annotations

import time
from typing import Optional


class ActionCompletionWatcher:
    def __init__(self, *, buffer=None):
        self._buffer = buffer
        self._stats = {
            "calls": 0,
            "completed": 0,
            "timeouts": 0,
            "last_reason": "init",
            "avg_wait_ms": 0.0,
        }

    def set_buffer(self, buffer) -> None:
        self._buffer = buffer

    def _latest(self, monitor_id: Optional[int] = None):
        if not self._buffer:
            return None
        try:
            if monitor_id is not None and hasattr(self._buffer, "get_latest_for_monitor"):
                slot = self._buffer.get_latest_for_monitor(int(monitor_id))
                if slot is not None:
                    return slot
            if hasattr(self._buffer, "get_latest"):
                return self._buffer.get_latest()
        except Exception:
            return None
        return None

    def wait_for_settle(
        self,
        *,
        monitor_id: Optional[int] = None,
        since_timestamp: float = 0.0,
        timeout_ms: int = 1400,
        settle_ms: int = 220,
        poll_ms: int = 35,
        change_threshold: float = 0.03,
        idle_grace_ms: int = 250,
    ) -> dict:
        """
        Wait until we observe changes and then stabilization.
        Returns quickly when no change is observed (idle grace) to avoid latency spikes.
        """
        timeout_ms = max(100, int(timeout_ms))
        settle_ms = max(40, int(settle_ms))
        poll_ms = max(10, int(poll_ms))
        idle_grace_ms = max(80, int(idle_grace_ms))
        change_threshold = max(0.0, float(change_threshold))

        started = time.time()
        deadline = started + (timeout_ms / 1000.0)
        last_seen_ts = float(since_timestamp or 0.0)
        saw_change = False
        stable_since = None
        first_change_ms = 0

        while time.time() < deadline:
            now = time.time()
            now_ms = int(now * 1000)
            slot = self._latest(monitor_id=monitor_id)
            if slot is not None:
                slot_ts = float(getattr(slot, "timestamp", 0.0) or 0.0)
                slot_change = float(getattr(slot, "change_score", 0.0) or 0.0)
                if slot_ts > last_seen_ts:
                    last_seen_ts = slot_ts
                    if slot_change >= change_threshold:
                        saw_change = True
                        first_change_ms = first_change_ms or now_ms
                        stable_since = None
                    else:
                        if stable_since is None:
                            stable_since = now_ms
                        if saw_change and (now_ms - stable_since) >= settle_ms:
                            return self._finish(
                                started,
                                completed=True,
                                reason="settled_after_change",
                                monitor_id=monitor_id,
                                saw_change=saw_change,
                                first_change_ms=first_change_ms,
                            )
                else:
                    if saw_change and stable_since is not None and (now_ms - stable_since) >= settle_ms:
                        return self._finish(
                            started,
                            completed=True,
                            reason="settled_after_change",
                            monitor_id=monitor_id,
                            saw_change=saw_change,
                            first_change_ms=first_change_ms,
                        )
            if (not saw_change) and (int((now - started) * 1000) >= idle_grace_ms):
                return self._finish(
                    started,
                    completed=True,
                    reason="no_change_observed",
                    monitor_id=monitor_id,
                    saw_change=False,
                    first_change_ms=0,
                )
            time.sleep(poll_ms / 1000.0)

        return self._finish(
            started,
            completed=False,
            reason="timeout",
            monitor_id=monitor_id,
            saw_change=saw_change,
            first_change_ms=first_change_ms,
        )

    def _finish(
        self,
        started: float,
        *,
        completed: bool,
        reason: str,
        monitor_id: Optional[int],
        saw_change: bool,
        first_change_ms: int,
    ) -> dict:
        waited_ms = int((time.time() - started) * 1000)
        self._stats["calls"] = int(self._stats.get("calls", 0)) + 1
        if completed:
            self._stats["completed"] = int(self._stats.get("completed", 0)) + 1
        else:
            self._stats["timeouts"] = int(self._stats.get("timeouts", 0)) + 1
        self._stats["last_reason"] = str(reason)
        calls = max(1, int(self._stats["calls"]))
        avg_prev = float(self._stats.get("avg_wait_ms", 0.0))
        self._stats["avg_wait_ms"] = ((avg_prev * (calls - 1)) + float(waited_ms)) / float(calls)
        return {
            "completed": bool(completed),
            "reason": str(reason),
            "monitor_id": int(monitor_id) if monitor_id is not None else None,
            "wait_ms": int(waited_ms),
            "saw_change": bool(saw_change),
            "first_change_ms": int(first_change_ms),
        }

    def stats(self) -> dict:
        return {
            "calls": int(self._stats.get("calls", 0)),
            "completed": int(self._stats.get("completed", 0)),
            "timeouts": int(self._stats.get("timeouts", 0)),
            "last_reason": str(self._stats.get("last_reason", "unknown")),
            "avg_wait_ms": round(float(self._stats.get("avg_wait_ms", 0.0)), 2),
        }
