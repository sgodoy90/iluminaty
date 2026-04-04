"""IPA v3 — Data types for Compressed Visual Stream."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class Region:
    """A spatial region in the 14x14 patch grid."""
    x1: int
    y1: int
    x2: int
    y2: int
    label: str = ""
    confidence: float = 0.0

    @property
    def area(self) -> int:
        return (self.x2 - self.x1) * (self.y2 - self.y1)

    def __repr__(self) -> str:
        return f"Region({self.label} [{self.x1},{self.y1}]-[{self.x2},{self.y2}] conf={self.confidence:.2f})"


@dataclass
class KeyMoment:
    """A timestamped scene transition in the timeline."""
    timestamp: float
    description: str
    scene_state: str = ""
    window_name: str = ""
    confidence: float = 0.0


@dataclass
class PatchFrame:
    """One frame in the visual stream — either I-frame (keyframe) or P-frame (delta)."""
    timestamp: float
    frame_type: str                     # "I" or "P"
    patch_grid: bytes                   # Compressed patches (TurboQuant 3-bit)
    change_mask: bytes                  # 25 bytes = 196-bit bitmap (which patches changed)
    motion_vectors: bytes               # Per-changed-patch direction+magnitude
    cls_embedding: bytes                # Compressed CLS for search (~292 bytes)
    n_changed: int = 0                  # Count of changed patches (0 for I-frames = all 196)
    metadata: dict = field(default_factory=dict)  # {monitor_id, scene_hint, window_name}

    @property
    def size_bytes(self) -> int:
        return len(self.patch_grid) + len(self.change_mask) + len(self.motion_vectors) + len(self.cls_embedding)

    @property
    def monitor_id(self) -> int:
        return self.metadata.get("monitor_id", 1)

    @property
    def window_name(self) -> str:
        return self.metadata.get("window_name", "")

    @property
    def scene_hint(self) -> str:
        return self.metadata.get("scene_hint", "")


@dataclass
class MotionField:
    """Motion information derived from patch deltas."""
    motion_type: str = "static"         # scroll_down, scroll_up, cursor, video, typing, static
    active_region: Optional[tuple] = None  # (x1, y1, x2, y2) in patch grid coords
    speed: float = 0.0                  # 0.0 - 1.0 normalized
    direction: tuple = (0.0, 0.0)       # (dx, dy) dominant direction
    n_active_patches: int = 0           # patches with motion
    detail: str = ""                    # human-readable motion description

    def to_dict(self) -> dict:
        return {
            "motion_type": self.motion_type,
            "active_region": self.active_region,
            "speed": round(self.speed, 3),
            "direction": (round(self.direction[0], 3), round(self.direction[1], 3)),
            "n_active_patches": self.n_active_patches,
            "detail": self.detail,
        }


@dataclass
class VisualContext:
    """The rich visual perception output — what the AI 'sees'."""
    spatial_map: list = field(default_factory=list)    # list[Region]
    motion: MotionField = field(default_factory=MotionField)
    changes: list = field(default_factory=list)        # list[str] descriptions
    timeline: list = field(default_factory=list)       # list[KeyMoment]
    scene_state: str = "unknown"
    confidence: float = 0.0
    token_estimate: int = 0
    timestamp: float = field(default_factory=time.time)
    buffer_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "spatial_map": [
                {"label": r.label, "region": [r.x1, r.y1, r.x2, r.y2], "confidence": round(r.confidence, 2)}
                for r in self.spatial_map
            ],
            "motion": self.motion.to_dict(),
            "changes": self.changes,
            "timeline": [
                {"t": round(km.timestamp, 2), "desc": km.description, "scene": km.scene_state}
                for km in self.timeline
            ],
            "scene_state": self.scene_state,
            "confidence": round(self.confidence, 3),
            "token_estimate": self.token_estimate,
            "buffer_seconds": round(self.buffer_seconds, 1),
        }

    def to_text(self) -> str:
        """Render as compact text for LLM consumption (~300-500 tokens)."""
        parts = []
        parts.append(f"[Scene: {self.scene_state} (conf={self.confidence:.2f})]")

        if self.spatial_map:
            regions = ", ".join(f"{r.label} [{r.x1},{r.y1}]-[{r.x2},{r.y2}]" for r in self.spatial_map[:6])
            parts.append(f"[Layout: {regions}]")

        m = self.motion
        if m.motion_type != "static":
            parts.append(f"[Motion: {m.motion_type} speed={m.speed:.2f} at {m.active_region or 'full'}]")
            if m.detail:
                parts.append(f"  {m.detail}")

        if self.changes:
            parts.append(f"[Changes: {'; '.join(self.changes[:5])}]")

        if self.timeline:
            tl = " → ".join(f"{km.description}" for km in self.timeline[-5:])
            parts.append(f"[Timeline: {tl}]")

        return "\n".join(parts)


@dataclass
class SearchResult:
    """Result from semantic visual search."""
    similarity: float
    timestamp: float
    frame_type: str
    window_name: str
    scene_hint: str
    monitor_id: int = 1
