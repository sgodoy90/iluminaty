"""
ILUMINATY - IPA v2.1 Semantic World State
==========================================
RAM-only semantic state for "eyes + hands" control loops.
No screenshots are persisted here. Only compact semantic traces.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional

from .domain_packs import DomainPackRegistry


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
    tick_id: int
    task_phase: str
    active_surface: str
    entities: list[str] = field(default_factory=list)
    affordances: list[str] = field(default_factory=list)
    attention_targets: list[str] = field(default_factory=list)
    uncertainty: float = 1.0
    readiness: bool = False
    readiness_reasons: list[str] = field(default_factory=list)
    risk_mode: str = "safe"
    visual_facts: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    staleness_ms: int = 0
    domain_pack: str = "general"
    domain_confidence: float = 0.0
    domain_source: str = "builtin"
    domain_signals: list[str] = field(default_factory=list)
    domain_policy: dict = field(default_factory=dict)
    domain_context: dict = field(default_factory=dict)


@dataclass
class WorldTraceEntry:
    timestamp_ms: int
    tick_id: int
    summary: str
    boundary_reason: str
    task_phase: str
    active_surface: str
    readiness: bool
    uncertainty: float
    evidence_refs: list[str] = field(default_factory=list)
    frame_refs: list[dict] = field(default_factory=list)


class WorldStateEngine:
    """
    Maintains semantic world snapshots + compressed episodic trace in RAM.
    """

    def __init__(
        self,
        horizon_seconds: int = 90,
        max_trace_entries: int = 600,
        domain_registry: Optional[DomainPackRegistry] = None,
    ):
        self._horizon_seconds = horizon_seconds
        self._trace: deque[WorldTraceEntry] = deque(maxlen=max_trace_entries)
        self._lock = threading.Lock()
        self._risk_mode = "safe"
        self._tick_id = 0
        self._domain_registry = domain_registry or DomainPackRegistry.from_environment()
        self._domain_override: Optional[str] = None
        now_ms = int(time.time() * 1000)
        self._current = WorldSnapshot(
            timestamp_ms=now_ms,
            tick_id=self._tick_id,
            task_phase="unknown",
            active_surface="unknown",
            uncertainty=1.0,
            readiness=False,
            readiness_reasons=["insufficient_context"],
            risk_mode=self._risk_mode,
            visual_facts=[],
            evidence=[],
            staleness_ms=0,
            domain_pack="general",
            domain_confidence=0.0,
            domain_source="builtin",
            domain_signals=[],
            domain_policy={"max_staleness_ms": {"safe": 1500, "hybrid": 1200, "raw": 4000}},
            domain_context={"fallback": True},
        )
        self._last_signature: tuple[str, str, bool, str, str] = (
            self._current.task_phase,
            self._current.active_surface,
            self._current.readiness,
            "",
            self._current.domain_pack,
        )

    @property
    def horizon_seconds(self) -> int:
        return self._horizon_seconds

    @property
    def tick_id(self) -> int:
        with self._lock:
            return self._tick_id

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
        return unique[:16]

    def _merge_affordances(self, base: list[str], extra: list[str]) -> list[str]:
        merged: list[str] = []
        seen = set()
        for item in list(base) + list(extra):
            token = str(item).strip()
            if not token or token in seen:
                continue
            seen.add(token)
            merged.append(token)
        return merged[:20]

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
        for event in recent_events[-8:]:
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
        return dedup[:24]

    def _normalize_visual_fact(self, fact: dict) -> dict:
        return {
            "kind": str(fact.get("kind", "observation"))[:80],
            "text": str(fact.get("text", ""))[:240],
            "confidence": round(float(_clamp01(float(fact.get("confidence", 0.0)))), 3),
            "monitor": int(fact.get("monitor", 0)),
            "timestamp_ms": int(fact.get("timestamp_ms", int(time.time() * 1000))),
            "source": str(fact.get("source", "unknown"))[:60],
            "evidence_ref": str(fact.get("evidence_ref", ""))[:160],
        }

    def _normalize_evidence(self, ev: dict) -> dict:
        return {
            "id": str(ev.get("id", ""))[:160],
            "type": str(ev.get("type", "event"))[:60],
            "summary": str(ev.get("summary", ""))[:240],
            "confidence": round(float(_clamp01(float(ev.get("confidence", 0.0)))), 3),
            "timestamp_ms": int(ev.get("timestamp_ms", int(time.time() * 1000))),
            "monitor": int(ev.get("monitor", 0)),
        }

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
        visual_facts: Optional[list[dict]] = None,
        evidence: Optional[list[dict]] = None,
        frame_refs: Optional[list[dict]] = None,
    ) -> dict:
        now_ms = int(time.time() * 1000)
        task_phase = self._task_phase_from_scene(scene_state, dominant_direction)
        import os as _os
        # Sanitize app_name — strip full exe paths to basename
        raw_app = (app_name or "").strip()
        if _os.sep in raw_app or "/" in raw_app:
            raw_app = _os.path.splitext(_os.path.basename(raw_app))[0]
        title = (window_title or "").strip()
        # Fallback: extract app name from window title when app_name is missing
        # e.g. "sgodoy90/iluminaty - Brave" -> "Brave"
        # e.g. "C:\Windows\system32\cmd.exe" -> "cmd"
        if not raw_app or raw_app.lower() in ("unknown", ""):
            if title:
                # Try last segment after " - "
                if " - " in title:
                    raw_app = title.split(" - ")[-1].strip()[:30]
                else:
                    raw_app = title[:30]
        app = raw_app or "unknown"
        active_surface = f"{app} :: {title[:80]}" if title and title.lower() != app.lower() else app

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

        clean_facts = [self._normalize_visual_fact(f) for f in (visual_facts or [])][:12]
        clean_evidence = [self._normalize_evidence(e) for e in (evidence or [])][:20]
        entities = self._build_entities(app, workflow, monitor_id, recent_events)
        domain = self._domain_registry.resolve(
            app_name=app,
            workflow=workflow,
            window_title=title,
            task_phase=task_phase,
            entities=entities,
            recent_events=recent_events,
            visual_facts=clean_facts,
            override=self._domain_override,
        )

        for hint in domain.attention_hints[:3]:
            marker = f"hint:{hint}"
            if marker not in attention_targets:
                attention_targets.append(marker)
        attention_targets = attention_targets[:6]

        readiness_reasons: list[str] = []
        if task_phase == "loading":
            readiness_reasons.append("scene_not_stable")
        if uncertainty > 0.55:
            readiness_reasons.append("high_uncertainty")
        if app == "unknown":
            readiness_reasons.append("unknown_surface")
        if domain.uncertainty_ceiling is not None and uncertainty > float(domain.uncertainty_ceiling):
            readiness_reasons.append("domain_uncertainty_guard")

        ready = len(readiness_reasons) == 0
        if ready:
            readiness_reasons = ["ready_for_action"]
        affordances = self._merge_affordances(
            self._infer_affordances(app, workflow, task_phase),
            domain.affordances,
        )

        with self._lock:
            self._tick_id += 1
            snapshot = WorldSnapshot(
                timestamp_ms=now_ms,
                tick_id=self._tick_id,
                task_phase=task_phase,
                active_surface=active_surface,
                entities=entities,
                affordances=affordances,
                attention_targets=attention_targets,
                uncertainty=round(uncertainty, 3),
                readiness=ready,
                readiness_reasons=readiness_reasons,
                risk_mode=self._risk_mode,
                visual_facts=clean_facts,
                evidence=clean_evidence,
                staleness_ms=0,
                **domain.to_world_fields(),
            )

            self._current = snapshot
            self._append_trace_if_boundary(snapshot, recent_events, frame_refs or [])
            self._trim_trace_locked()
            return self._serialize_current_locked()

    def _serialize_current_locked(self) -> dict:
        data = asdict(self._current)
        data["staleness_ms"] = max(0, int(time.time() * 1000) - self._current.timestamp_ms)
        return data

    def _append_trace_if_boundary(
        self,
        snapshot: WorldSnapshot,
        recent_events: list[dict],
        frame_refs: list[dict],
    ) -> None:
        top_event = recent_events[-1].get("type", "") if recent_events else ""
        signature = (
            snapshot.task_phase,
            snapshot.active_surface,
            snapshot.readiness,
            top_event,
            snapshot.domain_pack,
        )
        if signature == self._last_signature and self._trace:
            return
        self._last_signature = signature

        summary = (
            f"{snapshot.task_phase} | {snapshot.domain_pack} | {snapshot.active_surface} | "
            f"{'ready' if snapshot.readiness else 'not-ready'} | u={snapshot.uncertainty:.2f}"
        )
        reason = "state_transition" if self._trace else "bootstrap"
        refs = [f.get("ref_id", "") for f in frame_refs if f.get("ref_id")]
        self._trace.append(
            WorldTraceEntry(
                timestamp_ms=snapshot.timestamp_ms,
                tick_id=snapshot.tick_id,
                summary=summary,
                boundary_reason=reason,
                task_phase=snapshot.task_phase,
                active_surface=snapshot.active_surface,
                readiness=snapshot.readiness,
                uncertainty=snapshot.uncertainty,
                evidence_refs=refs[:10],
                frame_refs=frame_refs[:6],
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
                    tick_id=self._tick_id,
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
                        tick_id=self._tick_id,
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
            return self._serialize_current_locked()

    def get_trace(self, seconds: float = 90) -> list[dict]:
        cutoff_ms = int(time.time() * 1000) - int(max(seconds, 1) * 1000)
        with self._lock:
            return [asdict(entry) for entry in self._trace if entry.timestamp_ms >= cutoff_ms]

    def get_readiness(self) -> dict:
        with self._lock:
            return {
                "timestamp_ms": self._current.timestamp_ms,
                "tick_id": self._current.tick_id,
                "readiness": self._current.readiness,
                "uncertainty": self._current.uncertainty,
                "reasons": list(self._current.readiness_reasons),
                "task_phase": self._current.task_phase,
                "active_surface": self._current.active_surface,
                "risk_mode": self._current.risk_mode,
                "staleness_ms": max(0, int(time.time() * 1000) - self._current.timestamp_ms),
                "domain_pack": self._current.domain_pack,
                "domain_confidence": self._current.domain_confidence,
                "domain_policy": dict(self._current.domain_policy),
            }

    def list_domain_packs(self) -> dict:
        with self._lock:
            active = {
                "domain_pack": self._current.domain_pack,
                "domain_confidence": self._current.domain_confidence,
                "domain_source": self._current.domain_source,
            }
            override = self._domain_override
        return {
            "packs": self._domain_registry.list_packs(),
            "active": active,
            "override": override,
        }

    def reload_domain_packs(self) -> dict:
        result = self._domain_registry.reload_custom_packs()
        with self._lock:
            active = {
                "domain_pack": self._current.domain_pack,
                "domain_confidence": self._current.domain_confidence,
                "domain_source": self._current.domain_source,
            }
            override = self._domain_override
        return {
            **result,
            "active": active,
            "override": override,
        }

    def set_domain_override(self, name: Optional[str]) -> dict:
        candidate = (name or "").strip().lower()
        if candidate in {"", "auto", "none"}:
            with self._lock:
                self._domain_override = None
            return {"ok": True, "override": None, "reason": "auto"}
        if not self._domain_registry.has_pack(candidate):
            with self._lock:
                current_override = self._domain_override
            return {"ok": False, "override": current_override, "reason": "unknown_domain_pack"}
        with self._lock:
            self._domain_override = candidate
        return {"ok": True, "override": candidate, "reason": "forced"}

    def check_context_freshness(
        self,
        context_tick_id: Optional[int],
        max_staleness_ms: int,
    ) -> dict:
        now_ms = int(time.time() * 1000)
        with self._lock:
            latest_tick = self._current.tick_id
            staleness = max(0, now_ms - self._current.timestamp_ms)
            if staleness > max(0, int(max_staleness_ms)):
                return {
                    "allowed": False,
                    "reason": "context_stale",
                    "latest_tick_id": latest_tick,
                    "staleness_ms": staleness,
                }
            if context_tick_id is not None and int(context_tick_id) != latest_tick:
                return {
                    "allowed": False,
                    "reason": "context_tick_mismatch",
                    "latest_tick_id": latest_tick,
                    "staleness_ms": staleness,
                }
            return {
                "allowed": True,
                "reason": "fresh",
                "latest_tick_id": latest_tick,
                "staleness_ms": staleness,
            }
