"""
ILUMINATY - IPA v2 Semantic World State
=======================================
RAM-only semantic state for "eyes + hands" control loops.
No screenshots are persisted. Only compact semantic traces.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _zone_label(row: int, col: int) -> str:
    try:
        row_i = int(row)
        col_i = int(col)
    except Exception:
        row_i = 0
        col_i = 0
    vertical = "top" if row_i < 2 else "bottom" if row_i >= 4 else "middle"
    horizontal = "left" if col_i < 3 else "right" if col_i >= 5 else "center"
    return f"{vertical}-{horizontal}"


@dataclass
class WorldSnapshot:
    timestamp_ms: int
    task_phase: str
    active_surface: str
    entities: list[str] = field(default_factory=list)
    affordances: list[str] = field(default_factory=list)
    attention_targets: list[str] = field(default_factory=list)
    uncertainty: float = 1.0
    readiness: bool = False
    readiness_reasons: list[str] = field(default_factory=list)
    risk_mode: str = "safe"


@dataclass
class WorldTraceEntry:
    timestamp_ms: int
    summary: str
    boundary_reason: str
    task_phase: str
    active_surface: str
    readiness: bool
    uncertainty: float


class WorldStateEngine:
    """
    Maintains semantic world snapshots + compressed episodic trace in RAM.
    """

    def __init__(self, horizon_seconds: int = 90, max_trace_entries: int = 600):
        self._horizon_seconds = horizon_seconds
        self._trace: deque[WorldTraceEntry] = deque(maxlen=max_trace_entries)
        self._lock = threading.Lock()
        self._risk_mode = "safe"
        now_ms = int(time.time() * 1000)
        self._current = WorldSnapshot(
            timestamp_ms=now_ms,
            task_phase="unknown",
            active_surface="unknown",
            uncertainty=1.0,
            readiness=False,
            readiness_reasons=["insufficient_context"],
            risk_mode=self._risk_mode,
        )
        self._last_signature: tuple[str, str, bool, str] = (
            self._current.task_phase,
            self._current.active_surface,
            self._current.readiness,
            "",
        )

    @property
    def horizon_seconds(self) -> int:
        return self._horizon_seconds

    def set_risk_mode(self, mode: str) -> None:
        mode_norm = (mode or "safe").strip().lower()
        if mode_norm not in {"safe", "raw", "hybrid"}:
            mode_norm = "safe"
        with self._lock:
            self._risk_mode = mode_norm
            self._current.risk_mode = mode_norm

    def _task_phase_from_scene(self, scene_state: str, dominant_direction: str) -> str:
        s = (scene_state or "").lower()
        if s in {"loading", "transition"}:
            return "loading"
        if s == "typing":
            return "editing"
        if s == "scrolling":
            return "navigation"
        if s == "interaction":
            return "interaction"
        if s == "video":
            return "consuming"
        if s == "idle":
            return "idle"
        if dominant_direction in {"up", "down", "left", "right"}:
            return "navigation"
        return "unknown"

    def _infer_affordances(
        self,
        app_name: str,
        workflow: str,
        task_phase: str,
    ) -> list[str]:
        app = (app_name or "").lower()
        wf = (workflow or "").lower()

        affordances: list[str] = ["click", "type_text", "hotkey", "scroll"]
        if any(token in app for token in ("chrome", "edge", "firefox", "browser", "brave")):
            affordances.extend(["browser_navigate", "find_ui_element"])
        if any(token in app for token in ("code", "cursor", "vscode", "intellij", "pycharm", "terminal")):
            affordances.extend(["run_command", "read_file", "write_file"])
        if wf in {"coding", "finance", "browsing"}:
            affordances.append("do_action")
        if task_phase == "loading":
            affordances.append("wait")

        # Keep order, remove duplicates
        seen = set()
        unique = []
        for item in affordances:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique[:12]

    def _build_entities(
        self,
        app_name: str,
        workflow: str,
        monitor_id: int,
        recent_events: list[dict],
    ) -> list[str]:
        entities: list[str] = []
        if app_name:
            entities.append(f"app:{app_name}")
        if workflow and workflow != "unknown":
            entities.append(f"workflow:{workflow}")
        entities.append(f"monitor:{monitor_id}")
        for event in recent_events[-5:]:
            event_type = event.get("type", "")
            if event_type:
                entities.append(f"event:{event_type}")
        # Keep order / dedup
        dedup = []
        seen = set()
        for item in entities:
            if item not in seen:
                seen.add(item)
                dedup.append(item)
        return dedup[:16]

    def update(
        self,
        *,
        scene_state: str,
        scene_confidence: float,
        window_title: str,
        app_name: str,
        workflow: str,
        monitor_id: int,
        attention_hot_zones: list[dict],
        recent_events: list[dict],
        dominant_direction: str = "none",
    ) -> dict:
        now_ms = int(time.time() * 1000)
        task_phase = self._task_phase_from_scene(scene_state, dominant_direction)
        app = app_name or "unknown"
        title = (window_title or "").strip()
        active_surface = f"{app} :: {title[:120]}" if title else app

        attention_targets = [
            f"{_zone_label(z.get('row', 0), z.get('col', 0))}:{z.get('intensity', 0):.2f}"
            for z in attention_hot_zones[:3]
        ]

        uncertainty = 1.0 - _clamp01(scene_confidence)
        if task_phase == "loading":
            uncertainty += 0.2
        if not recent_events:
            uncertainty += 0.1
        uncertainty = _clamp01(uncertainty)

        readiness_reasons: list[str] = []
        if task_phase == "loading":
            readiness_reasons.append("scene_not_stable")
        if uncertainty > 0.55:
            readiness_reasons.append("high_uncertainty")
        if app == "unknown":
            readiness_reasons.append("unknown_surface")

        readiness = len(readiness_reasons) == 0
        if readiness:
            readiness_reasons = ["ready_for_action"]

        snapshot = WorldSnapshot(
            timestamp_ms=now_ms,
            task_phase=task_phase,
            active_surface=active_surface,
            entities=self._build_entities(app, workflow, monitor_id, recent_events),
            affordances=self._infer_affordances(app, workflow, task_phase),
            attention_targets=attention_targets,
            uncertainty=round(uncertainty, 3),
            readiness=readiness,
            readiness_reasons=readiness_reasons,
            risk_mode=self._risk_mode,
        )

        with self._lock:
            self._current = snapshot
            self._append_trace_if_boundary(snapshot, recent_events)
            self._trim_trace_locked()
            return asdict(self._current)

    def _append_trace_if_boundary(self, snapshot: WorldSnapshot, recent_events: list[dict]) -> None:
        top_event = recent_events[-1].get("type", "") if recent_events else ""
        signature = (
            snapshot.task_phase,
            snapshot.active_surface,
            snapshot.readiness,
            top_event,
        )
        if signature == self._last_signature and self._trace:
            return
        self._last_signature = signature

        summary = (
            f"{snapshot.task_phase} | {snapshot.active_surface} | "
            f"{'ready' if snapshot.readiness else 'not-ready'} | u={snapshot.uncertainty:.2f}"
        )
        reason = "state_transition" if self._trace else "bootstrap"
        self._trace.append(
            WorldTraceEntry(
                timestamp_ms=snapshot.timestamp_ms,
                summary=summary,
                boundary_reason=reason,
                task_phase=snapshot.task_phase,
                active_surface=snapshot.active_surface,
                readiness=snapshot.readiness,
                uncertainty=snapshot.uncertainty,
            )
        )

    def _trim_trace_locked(self) -> None:
        cutoff_ms = int(time.time() * 1000) - (self._horizon_seconds * 1000)
        while self._trace and self._trace[0].timestamp_ms < cutoff_ms:
            self._trace.popleft()

    def note_action(self, action: str, success: bool, message: str = "") -> None:
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._trace.append(
                WorldTraceEntry(
                    timestamp_ms=now_ms,
                    summary=f"action:{action} -> {'success' if success else 'failed'}",
                    boundary_reason="action_feedback",
                    task_phase=self._current.task_phase,
                    active_surface=self._current.active_surface,
                    readiness=self._current.readiness,
                    uncertainty=self._current.uncertainty,
                )
            )
            self._trim_trace_locked()
            if message:
                self._trace.append(
                    WorldTraceEntry(
                        timestamp_ms=now_ms,
                        summary=message[:180],
                        boundary_reason="action_message",
                        task_phase=self._current.task_phase,
                        active_surface=self._current.active_surface,
                        readiness=self._current.readiness,
                        uncertainty=self._current.uncertainty,
                    )
                )
                self._trim_trace_locked()

    def get_world(self) -> dict:
        with self._lock:
            return asdict(self._current)

    def get_trace(self, seconds: float = 90) -> list[dict]:
        cutoff_ms = int(time.time() * 1000) - int(max(seconds, 1) * 1000)
        with self._lock:
            return [asdict(entry) for entry in self._trace if entry.timestamp_ms >= cutoff_ms]

    def get_readiness(self) -> dict:
        with self._lock:
            return {
                "timestamp_ms": self._current.timestamp_ms,
                "readiness": self._current.readiness,
                "uncertainty": self._current.uncertainty,
                "reasons": list(self._current.readiness_reasons),
                "task_phase": self._current.task_phase,
                "active_surface": self._current.active_surface,
                "risk_mode": self._current.risk_mode,
            }
