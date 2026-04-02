"""
ILUMINATY - Workers System v2
==============================
Lightweight orchestration layer on top of IPA:
- Monitor workers (per-screen semantic digest)
- Spatial worker (active monitor + monitor map)
- Fusion worker (global world snapshot)
- Intent worker (intent timeline)
- Action arbiter worker (single-writer execution lease)
- Verify worker (post-action verification timeline)
- Memory worker (worker-level event compression in RAM)
- Scheduler worker (multi-monitor attention budget + routing)

This module remains RAM-only and low-overhead.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass
class WorkerRuntime:
    name: str
    processed: int = 0
    errors: int = 0
    last_run_ms: int = 0
    avg_latency_ms: float = 0.0
    last_status: str = "idle"

    def note(self, *, latency_ms: float = 0.0, ok: bool = True) -> None:
        self.processed += 1
        self.last_run_ms = int(time.time() * 1000)
        if not ok:
            self.errors += 1
            self.last_status = "error"
        else:
            self.last_status = "ok"
        if self.processed <= 1:
            self.avg_latency_ms = float(latency_ms)
        else:
            self.avg_latency_ms = ((self.avg_latency_ms * (self.processed - 1)) + float(latency_ms)) / float(self.processed)

    def to_dict(self, now_ms: int) -> dict:
        return {
            "name": self.name,
            "processed": int(self.processed),
            "errors": int(self.errors),
            "last_status": self.last_status,
            "avg_latency_ms": round(float(self.avg_latency_ms), 3),
            "last_run_ms": int(self.last_run_ms),
            "staleness_ms": max(0, int(now_ms - int(self.last_run_ms or now_ms))),
        }


@dataclass
class MonitorDigest:
    monitor_id: int
    timestamp_ms: int
    tick_id: int
    scene_state: str
    scene_confidence: float
    change_score: float
    dominant_direction: str
    task_phase: str
    active_surface: str
    app_name: str
    window_title: str
    readiness: bool
    uncertainty: float
    attention_targets: list[str] = field(default_factory=list)
    visual_facts: list[dict] = field(default_factory=list)
    evidence_count: int = 0
    is_active: bool = False

    def to_dict(self, now_ms: int) -> dict:
        payload = asdict(self)
        payload["scene_confidence"] = round(float(self.scene_confidence), 3)
        payload["change_score"] = round(float(self.change_score), 4)
        payload["uncertainty"] = round(float(self.uncertainty), 3)
        payload["staleness_ms"] = max(0, int(now_ms - int(self.timestamp_ms)))
        payload["visual_facts_count"] = len(self.visual_facts)
        payload["attention_targets"] = list(self.attention_targets[:6])
        payload["visual_facts"] = list(self.visual_facts[:6])
        return payload


@dataclass
class WorkerSubgoal:
    subgoal_id: str
    monitor_id: int
    goal: str
    priority: float = 0.5
    risk: str = "normal"
    deadline_ms: Optional[int] = None
    created_ms: int = 0
    updated_ms: int = 0
    completed: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self, now_ms: int) -> dict:
        deadline = int(self.deadline_ms) if self.deadline_ms is not None else None
        overdue = bool(deadline is not None and now_ms > deadline and not self.completed)
        ttl = max(0, int(deadline - now_ms)) if deadline is not None else None
        return {
            "subgoal_id": self.subgoal_id,
            "monitor_id": int(self.monitor_id),
            "goal": str(self.goal),
            "priority": round(float(self.priority), 3),
            "risk": str(self.risk),
            "deadline_ms": deadline,
            "created_ms": int(self.created_ms),
            "updated_ms": int(self.updated_ms),
            "completed": bool(self.completed),
            "overdue": bool(overdue),
            "deadline_ttl_ms": ttl,
            "metadata": dict(self.metadata or {}),
        }


class WorkersSystem:
    """
    Worker orchestration without heavy threading.

    Designed for fast in-process updates from PerceptionEngine and action pipeline.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        intent_history: int = 120,
        verify_history: int = 160,
        monitor_history: int = 400,
        default_action_ttl_ms: int = 2500,
    ):
        self.enabled = bool(enabled)
        self.default_action_ttl_ms = max(250, int(default_action_ttl_ms))
        self._lock = threading.Lock()

        self._workers: dict[str, WorkerRuntime] = {
            "monitor": WorkerRuntime("monitor"),
            "spatial": WorkerRuntime("spatial"),
            "fusion": WorkerRuntime("fusion"),
            "intent": WorkerRuntime("intent"),
            "arbiter": WorkerRuntime("arbiter"),
            "verify": WorkerRuntime("verify"),
            "memory": WorkerRuntime("memory"),
            "scheduler": WorkerRuntime("scheduler"),
            "routing": WorkerRuntime("routing"),
        }

        self._active_monitor_id: int = 0
        self._monitor_digests: dict[int, MonitorDigest] = {}
        self._monitor_events: deque[dict] = deque(maxlen=max(40, int(monitor_history)))
        self._monitor_topology: list[int] = []
        self._spatial_snapshot: dict = {
            "active_monitor_id": 0,
            "monitor_ids": [],
            "monitor_count": 0,
        }
        self._fusion_world: dict = {}
        self._intent_timeline: deque[dict] = deque(maxlen=max(20, int(intent_history)))
        self._verify_timeline: deque[dict] = deque(maxlen=max(20, int(verify_history)))
        self._last_summary: str = "workers_bootstrap"

        self._arbiter_owner: Optional[str] = None
        self._arbiter_owner_since_ms: int = 0
        self._arbiter_ttl_ms: int = self.default_action_ttl_ms
        self._arbiter_denied: int = 0
        self._last_action_outcome: dict = {}
        self._subgoals: dict[str, WorkerSubgoal] = {}
        self._max_subgoals = 200  # cap: evict oldest completed/expired on overflow
        self._schedule_snapshot: dict = {
            "timestamp_ms": int(time.time() * 1000),
            "active_monitor_id": 0,
            "recommended_monitor_id": 0,
            "budgets": [],
            "reason": "bootstrap",
        }

    def _note_worker(self, name: str, *, latency_ms: float = 0.0, ok: bool = True) -> None:
        worker = self._workers.get(name)
        if worker:
            worker.note(latency_ms=latency_ms, ok=ok)

    def _expire_arbiter_if_needed_locked(self) -> None:
        if not self._arbiter_owner:
            return
        now_ms = int(time.time() * 1000)
        if now_ms > (self._arbiter_owner_since_ms + self._arbiter_ttl_ms):
            self._arbiter_owner = None
            self._arbiter_owner_since_ms = 0
            self._arbiter_ttl_ms = self.default_action_ttl_ms

    def _monitor_attention_score_locked(self, monitor_id: int, now_ms: int) -> float:
        digest = self._monitor_digests.get(int(monitor_id))
        if not digest:
            return 0.01
        staleness = max(0, int(now_ms - int(digest.timestamp_ms)))
        freshness = max(0.05, 1.0 - min(1.0, float(staleness) / 4000.0))
        score = (
            0.20
            + (0.55 if bool(digest.is_active) else 0.0)
            + (0.40 * float(max(0.0, min(1.0, digest.change_score))))
            + (0.25 * (1.0 - float(max(0.0, min(1.0, digest.uncertainty)))))
            + (0.15 if bool(digest.readiness) else 0.0)
        ) * freshness

        for subgoal in self._subgoals.values():
            if subgoal.completed or int(subgoal.monitor_id) != int(monitor_id):
                continue
            score += 0.75 * float(max(0.0, min(1.0, subgoal.priority)))
            if subgoal.deadline_ms is not None:
                ttl_ms = int(subgoal.deadline_ms) - now_ms
                if ttl_ms <= 0:
                    score += 0.35
                elif ttl_ms <= 15000:
                    score += 0.25
                elif ttl_ms <= 60000:
                    score += 0.12
            risk = str(subgoal.risk or "normal").strip().lower()
            if risk in {"high", "critical"}:
                score += 0.12
        return max(0.01, score)

    def _recompute_schedule_locked(self, reason: str = "update") -> None:
        now_ms = int(time.time() * 1000)
        monitor_ids = sorted(self._monitor_digests.keys())
        if not monitor_ids:
            self._schedule_snapshot = {
                "timestamp_ms": now_ms,
                "active_monitor_id": int(self._active_monitor_id or 0),
                "recommended_monitor_id": int(self._active_monitor_id or 0),
                "budgets": [],
                "reason": str(reason),
            }
            return

        raw_scores: dict[int, float] = {}
        for mid in monitor_ids:
            raw_scores[mid] = self._monitor_attention_score_locked(mid, now_ms)
        total = sum(raw_scores.values()) or 1.0
        budgets = []
        for mid in monitor_ids:
            share = float(raw_scores[mid] / total)
            budgets.append(
                {
                    "monitor_id": int(mid),
                    "score": round(float(raw_scores[mid]), 4),
                    "share": round(float(share), 4),
                }
            )
        budgets.sort(key=lambda item: item["share"], reverse=True)
        recommended = int(budgets[0]["monitor_id"]) if budgets else int(self._active_monitor_id or 0)
        self._schedule_snapshot = {
            "timestamp_ms": now_ms,
            "active_monitor_id": int(self._active_monitor_id or 0),
            "recommended_monitor_id": int(recommended),
            "budgets": budgets,
            "reason": str(reason),
        }

    def update_monitor_digest(
        self,
        *,
        monitor_id: int,
        tick_id: int,
        scene_state: str,
        scene_confidence: float,
        change_score: float,
        dominant_direction: str,
        window_info: Optional[dict],
        attention_targets: list[dict],
        world_snapshot: dict,
        visual_facts: Optional[list[dict]] = None,
        evidence_count: int = 0,
        is_active: bool = False,
    ) -> None:
        if not self.enabled:
            return
        started = time.perf_counter()
        now_ms = int(time.time() * 1000)
        info = window_info or {}
        app_name = str(info.get("name") or info.get("app_name") or "unknown")
        window_title = str(info.get("window_title") or info.get("title") or "unknown")
        attention = []
        for item in (attention_targets or [])[:6]:
            row = int(item.get("row", 0))
            col = int(item.get("col", 0))
            intensity = round(float(item.get("intensity", 0.0)), 3)
            attention.append(f"r{row}c{col}:{intensity}")

        digest = MonitorDigest(
            monitor_id=int(monitor_id),
            timestamp_ms=now_ms,
            tick_id=int(tick_id),
            scene_state=str(scene_state or "unknown"),
            scene_confidence=_clamp01(scene_confidence),
            change_score=max(0.0, float(change_score)),
            dominant_direction=str(dominant_direction or "none"),
            task_phase=str(world_snapshot.get("task_phase", "unknown")),
            active_surface=str(world_snapshot.get("active_surface", app_name)),
            app_name=app_name[:96],
            window_title=window_title[:180],
            readiness=bool(world_snapshot.get("readiness", False)),
            uncertainty=_clamp01(float(world_snapshot.get("uncertainty", 1.0))),
            attention_targets=attention,
            visual_facts=list((visual_facts or [])[:10]),
            evidence_count=max(0, int(evidence_count)),
            is_active=bool(is_active),
        )

        with self._lock:
            self._monitor_digests[int(monitor_id)] = digest
            if is_active:
                self._active_monitor_id = int(monitor_id)
            self._monitor_events.append(
                {
                    "timestamp_ms": now_ms,
                    "monitor_id": int(monitor_id),
                    "tick_id": int(tick_id),
                    "scene_state": str(scene_state or "unknown"),
                    "task_phase": str(world_snapshot.get("task_phase", "unknown")),
                    "readiness": bool(world_snapshot.get("readiness", False)),
                    "change_score": round(float(change_score), 4),
                }
            )
            self._last_summary = (
                f"mon:{int(monitor_id)} {scene_state} "
                f"phase={world_snapshot.get('task_phase', 'unknown')} "
                f"ready={bool(world_snapshot.get('readiness', False))}"
            )
            self._recompute_schedule_locked(reason="monitor_digest")
        self._note_worker("monitor", latency_ms=(time.perf_counter() - started) * 1000.0, ok=True)
        self._note_worker("memory", latency_ms=0.0, ok=True)
        self._note_worker("scheduler", latency_ms=0.0, ok=True)

    def update_spatial_state(
        self,
        *,
        active_monitor_id: int,
        monitor_ids: list[int],
    ) -> None:
        if not self.enabled:
            return
        started = time.perf_counter()
        with self._lock:
            self._active_monitor_id = int(active_monitor_id or self._active_monitor_id or 0)
            ordered = sorted({int(mid) for mid in (monitor_ids or []) if int(mid) > 0})
            self._monitor_topology = ordered
            self._spatial_snapshot = {
                "active_monitor_id": int(self._active_monitor_id),
                "monitor_ids": ordered,
                "monitor_count": len(ordered),
            }
            self._recompute_schedule_locked(reason="spatial_state")
        self._note_worker("spatial", latency_ms=(time.perf_counter() - started) * 1000.0, ok=True)
        self._note_worker("scheduler", latency_ms=0.0, ok=True)

    def update_fusion_world(self, world_snapshot: dict) -> None:
        if not self.enabled:
            return
        started = time.perf_counter()
        with self._lock:
            self._fusion_world = {
                "tick_id": int(world_snapshot.get("tick_id", 0) or 0),
                "timestamp_ms": int(world_snapshot.get("timestamp_ms", int(time.time() * 1000))),
                "task_phase": str(world_snapshot.get("task_phase", "unknown")),
                "active_surface": str(world_snapshot.get("active_surface", "unknown")),
                "readiness": bool(world_snapshot.get("readiness", False)),
                "uncertainty": round(float(world_snapshot.get("uncertainty", 1.0)), 3),
                "risk_mode": str(world_snapshot.get("risk_mode", "safe")),
                "domain_pack": str(world_snapshot.get("domain_pack", "general")),
                "attention_targets": list((world_snapshot.get("attention_targets") or [])[:6]),
            }
            self._recompute_schedule_locked(reason="fusion_world")
        self._note_worker("fusion", latency_ms=(time.perf_counter() - started) * 1000.0, ok=True)
        self._note_worker("scheduler", latency_ms=0.0, ok=True)

    def register_intent(self, intent: dict, *, source: str = "api") -> dict:
        started = time.perf_counter()
        now_ms = int(time.time() * 1000)
        intent_id = f"intent_{now_ms}_{uuid.uuid4().hex[:6]}"
        item = {
            "intent_id": intent_id,
            "timestamp_ms": now_ms,
            "source": str(source or "api")[:48],
            "action": str(intent.get("action", "unknown")),
            "category": str(intent.get("category", "normal")),
            "params": dict(intent.get("params") or {}),
        }
        with self._lock:
            self._intent_timeline.append(item)
        self._note_worker("intent", latency_ms=(time.perf_counter() - started) * 1000.0, ok=True)
        return item

    def claim_action(
        self,
        *,
        owner: str,
        ttl_ms: Optional[int] = None,
        force: bool = False,
    ) -> dict:
        started = time.perf_counter()
        now_ms = int(time.time() * 1000)
        ttl = self.default_action_ttl_ms if ttl_ms is None else max(250, int(ttl_ms))
        owner_norm = str(owner or "executor").strip() or "executor"
        with self._lock:
            self._expire_arbiter_if_needed_locked()
            granted = False
            held_by = self._arbiter_owner
            if force or (self._arbiter_owner is None) or (self._arbiter_owner == owner_norm):
                self._arbiter_owner = owner_norm
                self._arbiter_owner_since_ms = now_ms
                self._arbiter_ttl_ms = ttl
                granted = True
                held_by = owner_norm
            else:
                self._arbiter_denied += 1
            expires_ms = int(self._arbiter_owner_since_ms + self._arbiter_ttl_ms) if self._arbiter_owner else now_ms
        self._note_worker("arbiter", latency_ms=(time.perf_counter() - started) * 1000.0, ok=granted)
        return {
            "granted": bool(granted),
            "owner": owner_norm,
            "held_by": held_by,
            "expires_at_ms": int(expires_ms),
            "ttl_ms": int(ttl),
            "reason": "acquired" if granted else "arbiter_busy",
        }

    def release_action(
        self,
        *,
        owner: str,
        success: bool,
        message: str = "",
    ) -> dict:
        started = time.perf_counter()
        now_ms = int(time.time() * 1000)
        owner_norm = str(owner or "").strip()
        released = False
        with self._lock:
            self._expire_arbiter_if_needed_locked()
            if not self._arbiter_owner:
                released = True
            elif owner_norm and self._arbiter_owner == owner_norm:
                released = True
                self._arbiter_owner = None
                self._arbiter_owner_since_ms = 0
                self._arbiter_ttl_ms = self.default_action_ttl_ms
            self._last_action_outcome = {
                "timestamp_ms": now_ms,
                "owner": owner_norm or "unknown",
                "success": bool(success),
                "message": str(message or "")[:220],
            }
        self._note_worker("arbiter", latency_ms=(time.perf_counter() - started) * 1000.0, ok=released)
        return {
            "released": bool(released),
            "owner": owner_norm,
            "success": bool(success),
            "message": str(message or "")[:220],
        }

    def record_verification(
        self,
        *,
        intent_id: Optional[str],
        action: str,
        success: bool,
        reason: str,
        monitor_id: Optional[int] = None,
    ) -> None:
        started = time.perf_counter()
        now_ms = int(time.time() * 1000)
        event = {
            "timestamp_ms": now_ms,
            "intent_id": str(intent_id) if intent_id else None,
            "action": str(action or "unknown")[:80],
            "success": bool(success),
            "reason": str(reason or "")[:220],
            "monitor_id": int(monitor_id) if monitor_id is not None else None,
        }
        with self._lock:
            self._verify_timeline.append(event)
        self._note_worker("verify", latency_ms=(time.perf_counter() - started) * 1000.0, ok=True)
        self._note_worker("memory", latency_ms=0.0, ok=True)

    def set_subgoal(
        self,
        *,
        monitor_id: int,
        goal: str,
        priority: float = 0.5,
        risk: str = "normal",
        deadline_ms: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        started = time.perf_counter()
        now_ms = int(time.time() * 1000)
        subgoal_id = f"sg_{now_ms}_{uuid.uuid4().hex[:6]}"
        item = WorkerSubgoal(
            subgoal_id=subgoal_id,
            monitor_id=max(1, int(monitor_id)),
            goal=str(goal or "").strip()[:240] or "unspecified_goal",
            priority=max(0.0, min(1.0, float(priority))),
            risk=str(risk or "normal").strip().lower()[:24] or "normal",
            deadline_ms=int(deadline_ms) if deadline_ms is not None else None,
            created_ms=now_ms,
            updated_ms=now_ms,
            completed=False,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            # Evict oldest completed or expired subgoals if cap is reached
            if len(self._subgoals) >= self._max_subgoals:
                now_for_evict = int(time.time() * 1000)
                evict_candidates = sorted(
                    (sg for sg in self._subgoals.values()
                     if sg.completed or (sg.deadline_ms is not None and now_for_evict > sg.deadline_ms)),
                    key=lambda sg: sg.updated_ms,
                )
                for evict in evict_candidates[:max(1, len(evict_candidates) // 2)]:
                    self._subgoals.pop(evict.subgoal_id, None)
            self._subgoals[subgoal_id] = item
            self._recompute_schedule_locked(reason="set_subgoal")
        self._note_worker("scheduler", latency_ms=(time.perf_counter() - started) * 1000.0, ok=True)
        return item.to_dict(now_ms)

    def clear_subgoal(self, subgoal_id: str, *, completed: bool = True) -> dict:
        started = time.perf_counter()
        now_ms = int(time.time() * 1000)
        target = str(subgoal_id or "").strip()
        with self._lock:
            item = self._subgoals.get(target)
            if not item:
                result = {"ok": False, "reason": "subgoal_not_found", "subgoal_id": target}
            else:
                item.completed = bool(completed)
                item.updated_ms = now_ms
                result = {"ok": True, "subgoal": item.to_dict(now_ms)}
                if bool(completed):
                    self._subgoals.pop(target, None)
                self._recompute_schedule_locked(reason="clear_subgoal")
        self._note_worker("scheduler", latency_ms=(time.perf_counter() - started) * 1000.0, ok=result.get("ok", False))
        return result

    def list_subgoals(self, *, include_completed: bool = False) -> list[dict]:
        now_ms = int(time.time() * 1000)
        with self._lock:
            items = []
            for entry in self._subgoals.values():
                if entry.completed and not include_completed:
                    continue
                items.append(entry.to_dict(now_ms))
        items.sort(
            key=lambda row: (
                row.get("completed", False),
                -(row.get("priority", 0.0) or 0.0),
                row.get("deadline_ttl_ms") if row.get("deadline_ttl_ms") is not None else 10**12,
            )
        )
        return items

    def get_schedule(self) -> dict:
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._recompute_schedule_locked(reason="read")
            snapshot = dict(self._schedule_snapshot)
            snapshot["staleness_ms"] = max(0, now_ms - int(snapshot.get("timestamp_ms", now_ms)))
            snapshot["subgoals"] = [
                item.to_dict(now_ms)
                for item in self._subgoals.values()
                if not item.completed
            ]
            return snapshot

    def route_query(self, query: str, *, preferred_monitor_id: Optional[int] = None) -> dict:
        started = time.perf_counter()
        now_ms = int(time.time() * 1000)
        q = str(query or "").strip().lower()
        with self._lock:
            self._recompute_schedule_locked(reason="route_query")
            budgets = list(self._schedule_snapshot.get("budgets", []))
            best_monitor = int(self._schedule_snapshot.get("recommended_monitor_id", 0) or 0)
            best_score = 0.0
            if preferred_monitor_id is not None and int(preferred_monitor_id) > 0:
                best_monitor = int(preferred_monitor_id)
                best_score = 0.12

            for row in budgets:
                mid = int(row.get("monitor_id", 0) or 0)
                digest = self._monitor_digests.get(mid)
                if not digest:
                    continue
                score = float(row.get("share", 0.0))
                text_blob = f"{digest.app_name} {digest.window_title} {digest.active_surface}".lower()
                if q and q in text_blob:
                    score += 0.7
                elif q:
                    tokens = [tok for tok in q.replace("-", " ").split() if tok]
                    if tokens and all(tok in text_blob for tok in tokens):
                        score += 0.45
                    else:
                        score += 0.05 * sum(1 for tok in tokens if tok in text_blob)

                for subgoal in self._subgoals.values():
                    if subgoal.completed or int(subgoal.monitor_id) != mid:
                        continue
                    goal_text = str(subgoal.goal).lower()
                    if q and q in goal_text:
                        score += 0.6
                if score > best_score:
                    best_monitor = mid
                    best_score = score

            result = {
                "query": query,
                "monitor_id": int(best_monitor),
                "score": round(float(best_score), 4),
                "active_monitor_id": int(self._active_monitor_id or 0),
                "schedule_ts_ms": int(self._schedule_snapshot.get("timestamp_ms", now_ms)),
            }
        self._note_worker("routing", latency_ms=(time.perf_counter() - started) * 1000.0, ok=True)
        return result

    def get_monitor(self, monitor_id: int) -> Optional[dict]:
        now_ms = int(time.time() * 1000)
        with self._lock:
            digest = self._monitor_digests.get(int(monitor_id))
            if not digest:
                return None
            return digest.to_dict(now_ms)

    def list_monitors(self) -> list[dict]:
        now_ms = int(time.time() * 1000)
        with self._lock:
            items = [self._monitor_digests[mid].to_dict(now_ms) for mid in sorted(self._monitor_digests.keys())]
        return items

    def status(self) -> dict:
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._expire_arbiter_if_needed_locked()
            self._recompute_schedule_locked(reason="status")
            workers = {name: runtime.to_dict(now_ms) for name, runtime in self._workers.items()}
            monitors = [self._monitor_digests[mid].to_dict(now_ms) for mid in sorted(self._monitor_digests.keys())]
            owner = self._arbiter_owner
            expires_ms = int(self._arbiter_owner_since_ms + self._arbiter_ttl_ms) if owner else None
            remaining_ms = max(0, int((expires_ms or now_ms) - now_ms)) if owner else 0
            subgoals = [
                item.to_dict(now_ms)
                for item in self._subgoals.values()
                if not item.completed
            ]
            schedule = dict(self._schedule_snapshot)
            schedule["staleness_ms"] = max(0, int(now_ms - int(schedule.get("timestamp_ms", now_ms))))
            return {
                "enabled": bool(self.enabled),
                "timestamp_ms": now_ms,
                "summary": str(self._last_summary),
                "active_monitor_id": int(self._active_monitor_id or 0),
                "monitor_count": len(monitors),
                "monitors": monitors,
                "spatial": dict(self._spatial_snapshot),
                "fusion_world": dict(self._fusion_world),
                "workers": workers,
                "arbiter": {
                    "owner": owner,
                    "lease_expires_ms": expires_ms,
                    "lease_remaining_ms": int(remaining_ms),
                    "denied_count": int(self._arbiter_denied),
                    "last_action_outcome": dict(self._last_action_outcome),
                },
                "intent_recent": list(self._intent_timeline)[-20:],
                "verify_recent": list(self._verify_timeline)[-30:],
                "memory": {
                    "monitor_events": len(self._monitor_events),
                    "monitor_topology": list(self._monitor_topology),
                },
                "scheduler": schedule,
                "subgoals": subgoals,
            }
