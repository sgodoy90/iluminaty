"""
ILUMINATY - Hybrid Grounding Engine (IPA v2.2)
==============================================
Resolves actionable UI targets from multi-source evidence:
- UI Tree (accessibility)
- OCR blocks (screen text regions)
- Visual/attention hints (lightweight)

Designed to be additive and low-cost. It does not replace existing
action/perception flows; it augments them with target confidence.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional


def _norm_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _token_set(value: str) -> set[str]:
    return {tok for tok in _norm_text(value).split() if len(tok) >= 2}


def _overlap_score(query: str, text: str) -> float:
    q = _token_set(query)
    t = _token_set(text)
    if not q:
        return 0.0
    if not t:
        return 0.0
    inter = len(q.intersection(t))
    return max(0.0, min(1.0, inter / max(1, len(q))))


def _bbox_center(bbox: dict) -> tuple[int, int]:
    x = int(bbox.get("x", 0))
    y = int(bbox.get("y", 0))
    w = max(1, int(bbox.get("w", 1)))
    h = max(1, int(bbox.get("h", 1)))
    return (x + (w // 2), y + (h // 2))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass
class GroundingCandidate:
    source: str
    name: str
    role: str
    bbox: dict
    center_xy: tuple[int, int]
    confidence: float
    tick_id: int
    monitor_id: int
    staleness_ms: int
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 3)
        return data


@dataclass
class GroundingResult:
    success: bool
    blocked: bool
    reason: str
    target: Optional[GroundingCandidate]
    candidates: list[GroundingCandidate]
    world_ref: dict
    confidence_threshold: float
    max_staleness_ms: int
    context_check: dict

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "blocked": self.blocked,
            "reason": self.reason,
            "target": self.target.to_dict() if self.target else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "world_ref": self.world_ref,
            "confidence_threshold": round(float(self.confidence_threshold), 3),
            "max_staleness_ms": int(self.max_staleness_ms),
            "context_check": dict(self.context_check),
        }


class GroundingEngine:
    """
    Hybrid grounding for low-cost actionable target selection.
    """

    def __init__(self):
        self._ui_tree = None
        self._vision = None
        self._perception = None
        self._buffer = None
        self._lock = threading.Lock()
        self._last_good: dict[str, tuple[GroundingCandidate, int]] = {}
        self._stats = {
            "resolves": 0,
            "successes": 0,
            "blocked": 0,
            "avg_latency_ms": 0.0,
            "source_hits": {"ui_tree": 0, "ocr": 0, "visual": 0, "memory": 0},
            "last_reason": "init",
        }

    def set_layers(self, *, ui_tree=None, vision=None, perception=None, buffer=None) -> None:
        self._ui_tree = ui_tree
        self._vision = vision
        self._perception = perception
        self._buffer = buffer

    def _confidence_threshold(self, mode: str, category: str) -> float:
        mode_norm = (mode or "SAFE").strip().upper()
        cat = (category or "normal").strip().lower()
        if mode_norm == "RAW":
            return 0.0
        if mode_norm == "HYBRID":
            if cat == "destructive":
                return 0.72
            return 0.55
        return 0.72

    def _max_staleness_for_mode(self, mode: str) -> int:
        mode_norm = (mode or "SAFE").strip().upper()
        if mode_norm == "RAW":
            return 120000
        return 1200

    def _world_ref(self) -> dict:
        if not self._perception:
            return {
                "tick_id": None,
                "task_phase": "unknown",
                "active_surface": "unknown",
                "staleness_ms": 0,
            }
        try:
            world = self._perception.get_world_state()
            return {
                "tick_id": world.get("tick_id"),
                "task_phase": world.get("task_phase", "unknown"),
                "active_surface": world.get("active_surface", "unknown"),
                "staleness_ms": int(world.get("staleness_ms", 0)),
            }
        except Exception:
            return {
                "tick_id": None,
                "task_phase": "unknown",
                "active_surface": "unknown",
                "staleness_ms": 0,
            }

    def _context_check(self, context_tick_id: Optional[int], max_staleness_ms: int) -> dict:
        if not self._perception:
            return {"allowed": True, "reason": "perception_unavailable", "latest_tick_id": None, "staleness_ms": 0}
        try:
            if hasattr(self._perception, "check_context_freshness"):
                return self._perception.check_context_freshness(context_tick_id, max_staleness_ms)
        except Exception:
            pass  # noqa: suppressed Exception
        world = self._world_ref()
        staleness = int(world.get("staleness_ms", 0))
        if staleness > int(max_staleness_ms):
            return {
                "allowed": False,
                "reason": "context_stale",
                "latest_tick_id": world.get("tick_id"),
                "staleness_ms": staleness,
            }
        return {
            "allowed": True,
            "reason": "fresh",
            "latest_tick_id": world.get("tick_id"),
            "staleness_ms": staleness,
        }

    def _collect_ui_candidates(
        self,
        query: str,
        role: Optional[str],
        monitor_id: Optional[int],
        tick_id: int,
        staleness_ms: int,
    ) -> list[GroundingCandidate]:
        out: list[GroundingCandidate] = []
        if not self._ui_tree or not getattr(self._ui_tree, "available", False):
            return out
        default_monitor = 0
        slot = self._latest_slot(monitor_id)
        if slot is not None:
            try:
                default_monitor = int(getattr(slot, "monitor_id", 0) or 0)
            except Exception:
                default_monitor = 0
        try:
            hits = self._ui_tree.find_all(name=query, role=role) or []
        except Exception:
            hits = []
        for hit in hits[:16]:
            x = int(hit.get("x", 0))
            y = int(hit.get("y", 0))
            w = max(1, int(hit.get("width", 1)))
            h = max(1, int(hit.get("height", 1)))
            name = str(hit.get("name", query or "target")).strip() or query
            hit_role = str(hit.get("role", role or "unknown")).strip() or "unknown"
            overlap = _overlap_score(query, name)
            conf = 0.72 + (0.28 * overlap)
            out.append(
                GroundingCandidate(
                    source="ui_tree",
                    name=name[:160],
                    role=hit_role[:48],
                    bbox={"x": x, "y": y, "w": w, "h": h},
                    center_xy=(x + (w // 2), y + (h // 2)),
                    confidence=round(_clamp01(conf), 3),
                    tick_id=tick_id,
                    monitor_id=int(monitor_id if monitor_id is not None else hit.get("monitor", default_monitor) or default_monitor),
                    staleness_ms=staleness_ms,
                    evidence_refs=[f"ui:{name[:48]}"],
                )
            )
        return out

    def _latest_slot(self, monitor_id: Optional[int]):
        if not self._buffer:
            return None
        try:
            if monitor_id is not None and hasattr(self._buffer, "get_latest_for_monitor"):
                slot = self._buffer.get_latest_for_monitor(int(monitor_id))
                if slot is not None:
                    return slot
            return self._buffer.get_latest()
        except Exception:
            return None

    def _collect_ocr_candidates(
        self,
        query: str,
        role: Optional[str],
        monitor_id: Optional[int],
        tick_id: int,
        staleness_ms: int,
    ) -> list[GroundingCandidate]:
        out: list[GroundingCandidate] = []
        if not self._vision or not getattr(self._vision, "ocr", None):
            return out
        slot = self._latest_slot(monitor_id)
        if slot is None:
            return out
        try:
            ocr = self._vision.ocr.extract_text(slot.frame_bytes, frame_hash=getattr(slot, "phash", None))
        except Exception:
            return out
        blocks = ocr.get("blocks", []) or []
        for block in blocks[:50]:
            text = str(block.get("text", "")).strip()
            if not text:
                continue
            overlap = _overlap_score(query, text)
            if overlap <= 0.0:
                continue
            x = int(block.get("x", 0))
            y = int(block.get("y", 0))
            w = max(1, int(block.get("w", 1)))
            h = max(1, int(block.get("h", 1)))
            block_conf = _clamp01(float(block.get("confidence", 0.0)) / 100.0)
            conf = (0.45 * overlap) + (0.45 * block_conf) + 0.1
            out.append(
                GroundingCandidate(
                    source="ocr",
                    name=text[:160],
                    role=(role or "text")[:48],
                    bbox={"x": x, "y": y, "w": w, "h": h},
                    center_xy=(x + (w // 2), y + (h // 2)),
                    confidence=round(_clamp01(conf), 3),
                    tick_id=tick_id,
                    monitor_id=int(monitor_id if monitor_id is not None else getattr(slot, "monitor_id", 0) or 0),
                    staleness_ms=staleness_ms,
                    evidence_refs=[f"ocr:{text[:48]}"],
                )
            )
        return out

    def _collect_visual_candidates(
        self,
        query: str,
        role: Optional[str],
        monitor_id: Optional[int],
        tick_id: int,
        staleness_ms: int,
    ) -> list[GroundingCandidate]:
        out: list[GroundingCandidate] = []
        if not self._perception:
            return out
        slot = self._latest_slot(monitor_id)
        frame_w = int(getattr(slot, "width", 1920) or 1920)
        frame_h = int(getattr(slot, "height", 1080) or 1080)
        try:
            world = self._perception.get_world_state()
        except Exception:
            world = {}
        visual_facts = world.get("visual_facts", []) or []
        top_visual = None
        best_overlap = 0.0
        for fact in visual_facts[:12]:
            txt = str(fact.get("text", ""))
            overlap = _overlap_score(query, txt)
            if overlap > best_overlap:
                best_overlap = overlap
                top_visual = fact

        attn = (world.get("attention_targets") or [])
        first_attn = str(attn[0]) if attn else "middle-center:0.4"
        pos_part, _, inten_part = first_attn.partition(":")
        intensity = _clamp01(float(inten_part or 0.4))
        pos = pos_part.strip().lower()
        vx = frame_w // 2
        vy = frame_h // 2
        if "left" in pos:
            vx = frame_w // 4
        elif "right" in pos:
            vx = (frame_w * 3) // 4
        if "top" in pos:
            vy = frame_h // 4
        elif "bottom" in pos or "bot" in pos:
            vy = (frame_h * 3) // 4
        vw = max(40, frame_w // 5)
        vh = max(40, frame_h // 6)
        bbox = {"x": max(0, vx - (vw // 2)), "y": max(0, vy - (vh // 2)), "w": vw, "h": vh}

        conf = 0.20 + (0.45 * intensity) + (0.35 * best_overlap)
        if top_visual is not None:
            conf += 0.05
        out.append(
            GroundingCandidate(
                source="visual",
                name=str((top_visual or {}).get("text", query or "visual_target"))[:160],
                role=(role or "region")[:48],
                bbox=bbox,
                center_xy=_bbox_center(bbox),
                confidence=round(_clamp01(conf), 3),
                tick_id=tick_id,
                monitor_id=int(monitor_id or getattr(slot, "monitor_id", 0) or 0),
                staleness_ms=staleness_ms,
                evidence_refs=[str((top_visual or {}).get("evidence_ref", "attn:hot"))[:160]],
            )
        )
        return out

    def _fuse(self, candidates: list[GroundingCandidate]) -> list[GroundingCandidate]:
        if not candidates:
            return []
        clusters: list[list[GroundingCandidate]] = []
        for cand in candidates:
            placed = False
            for cluster in clusters:
                ref = cluster[0]
                if ref.monitor_id != cand.monitor_id:
                    continue
                dx = ref.center_xy[0] - cand.center_xy[0]
                dy = ref.center_xy[1] - cand.center_xy[1]
                if math.sqrt((dx * dx) + (dy * dy)) <= 90:
                    cluster.append(cand)
                    placed = True
                    break
            if not placed:
                clusters.append([cand])

        fused: list[GroundingCandidate] = []
        for cluster in clusters:
            by_source = {"ui_tree": 0.0, "ocr": 0.0, "visual": 0.0}
            evidence: list[str] = []
            for cand in cluster:
                if cand.source in by_source:
                    by_source[cand.source] = max(by_source[cand.source], cand.confidence)
                evidence.extend(cand.evidence_refs[:2])

            raw = (0.50 * by_source["ui_tree"]) + (0.30 * by_source["ocr"]) + (0.20 * by_source["visual"])
            source_count = len([1 for val in by_source.values() if val > 0.0])
            if source_count >= 2:
                raw += 0.05

            best = max(cluster, key=lambda c: c.confidence)
            staleness_penalty = 0.25 * min(2500, max(0, int(best.staleness_ms))) / 2500.0
            conf = _clamp01(raw - staleness_penalty)

            fused.append(
                GroundingCandidate(
                    source="hybrid",
                    name=best.name[:160],
                    role=best.role[:48],
                    bbox=dict(best.bbox),
                    center_xy=tuple(best.center_xy),
                    confidence=round(conf, 3),
                    tick_id=best.tick_id,
                    monitor_id=best.monitor_id,
                    staleness_ms=best.staleness_ms,
                    evidence_refs=list(dict.fromkeys(evidence))[:8],
                )
            )
        fused.sort(key=lambda c: c.confidence, reverse=True)
        return fused

    def _remember_key(self, query: str, role: Optional[str], monitor_id: Optional[int]) -> str:
        return f"{_norm_text(query)}|{_norm_text(role or '')}|{int(monitor_id or 0)}"

    def _get_last_good(self, key: str, tick_id: int, staleness_ms: int) -> Optional[GroundingCandidate]:
        with self._lock:
            entry = self._last_good.get(key)
        if entry is None:
            return None
        prev, saved_at_ms = entry
        age_ms = max(0, int((time.time() * 1000) - int(saved_at_ms)))
        decay = min(0.25, age_ms / 20000.0)
        conf = _clamp01(float(prev.confidence) - decay)
        return GroundingCandidate(
            source="memory",
            name=prev.name,
            role=prev.role,
            bbox=dict(prev.bbox),
            center_xy=tuple(prev.center_xy),
            confidence=round(conf, 3),
            tick_id=tick_id,
            monitor_id=prev.monitor_id,
            staleness_ms=staleness_ms,
            evidence_refs=list(prev.evidence_refs)[:6],
        )

    def _save_last_good(self, key: str, target: GroundingCandidate) -> None:
        with self._lock:
            self._last_good[key] = (target, int(time.time() * 1000))

    def resolve(
        self,
        *,
        query: str,
        role: Optional[str] = None,
        monitor_id: Optional[int] = None,
        mode: str = "SAFE",
        category: str = "normal",
        context_tick_id: Optional[int] = None,
        max_staleness_ms: Optional[int] = None,
        top_k: int = 5,
    ) -> dict:
        started = time.time()
        query = (query or "").strip()
        if not query:
            return GroundingResult(
                success=False,
                blocked=True,
                reason="query_required",
                target=None,
                candidates=[],
                world_ref=self._world_ref(),
                confidence_threshold=self._confidence_threshold(mode, category),
                max_staleness_ms=self._max_staleness_for_mode(mode),
                context_check={"allowed": False, "reason": "query_required"},
            ).to_dict()

        effective_staleness = int(
            max_staleness_ms if max_staleness_ms is not None else self._max_staleness_for_mode(mode)
        )
        world_ref = self._world_ref()
        tick_id = int(world_ref.get("tick_id") or 0)
        staleness_ms = int(world_ref.get("staleness_ms", 0))
        context_check = self._context_check(context_tick_id, effective_staleness)

        candidates: list[GroundingCandidate] = []
        if context_check.get("allowed", True):
            candidates.extend(self._collect_ui_candidates(query, role, monitor_id, tick_id, staleness_ms))
            candidates.extend(self._collect_ocr_candidates(query, role, monitor_id, tick_id, staleness_ms))
            candidates.extend(self._collect_visual_candidates(query, role, monitor_id, tick_id, staleness_ms))

        fused = self._fuse(candidates)
        key = self._remember_key(query, role, monitor_id)
        if not fused:
            memory = self._get_last_good(key, tick_id=tick_id, staleness_ms=staleness_ms)
            if memory is not None:
                fused = [memory]

        fused = fused[: max(1, int(top_k))]
        target = fused[0] if fused else None
        threshold = self._confidence_threshold(mode, category)

        blocked = False
        reason = "ok"
        if not context_check.get("allowed", True):
            blocked = True
            reason = str(context_check.get("reason", "context_blocked"))
        elif target is None:
            blocked = True
            reason = "grounding_not_found"
        elif (mode or "SAFE").upper() != "RAW":
            if target.staleness_ms > effective_staleness:
                blocked = True
                reason = "grounding_stale"
            elif target.confidence < threshold:
                blocked = True
                reason = "low_grounding_confidence"

        success = (not blocked) and (target is not None)
        if success and target is not None:
            self._save_last_good(key, target)

        elapsed_ms = (time.time() - started) * 1000.0
        with self._lock:
            self._stats["resolves"] += 1
            if success:
                self._stats["successes"] += 1
            if blocked:
                self._stats["blocked"] += 1
            prev_avg = float(self._stats["avg_latency_ms"])
            n = max(1, int(self._stats["resolves"]))
            self._stats["avg_latency_ms"] = ((prev_avg * (n - 1)) + elapsed_ms) / n
            self._stats["last_reason"] = reason
            for src in ("ui_tree", "ocr", "visual", "memory"):
                if any(c.source == src for c in candidates):
                    self._stats["source_hits"][src] += 1
                if src == "memory" and any(c.source == "memory" for c in fused):
                    self._stats["source_hits"]["memory"] += 1

        result = GroundingResult(
            success=success,
            blocked=blocked,
            reason=reason,
            target=target,
            candidates=fused,
            world_ref=world_ref,
            confidence_threshold=threshold,
            max_staleness_ms=effective_staleness,
            context_check=context_check,
        )
        return result.to_dict()

    def status(self) -> dict:
        with self._lock:
            resolves = max(1, int(self._stats["resolves"]))
            return {
                "enabled": True,
                "profile": "balanced",
                "mode": "hybrid_ui_text",
                "stats": {
                    **self._stats,
                    "avg_latency_ms": round(float(self._stats["avg_latency_ms"]), 3),
                    "success_rate_pct": round((float(self._stats["successes"]) / resolves) * 100.0, 3),
                    "blocked_rate_pct": round((float(self._stats["blocked"]) / resolves) * 100.0, 3),
                },
            }
