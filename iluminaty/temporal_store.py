"""
ILUMINATY - Temporal Visual Store (IPA v2.1)
=============================================
Stores compressed semantic transitions + frame references for recent context.

Profiles:
- core_ram: RAM only, no disk payloads
- vision_plus: RAM + optional encrypted rotating spool on disk
"""

from __future__ import annotations

import base64
import os
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .ring_buffer import FrameSlot

try:
    from cryptography.fernet import Fernet
    _HAS_FERNET = True
except Exception:
    _HAS_FERNET = False


@dataclass
class SemanticTransition:
    timestamp_ms: int
    tick_id: int
    kind: str
    summary: str
    confidence: float
    monitor: int
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class FrameReference:
    ref_id: str
    timestamp_ms: int
    tick_id: int
    monitor: int
    change_score: float
    boundary_reason: str
    mime_type: str
    width: int
    height: int
    source: str = "ram"
    spool_path: Optional[str] = None


class TemporalVisualStore:
    def __init__(
        self,
        horizon_seconds: int = 90,
        profile: str = "core_ram",
        disk_enabled: bool = False,
        disk_ttl_minutes: int = 30,
        sample_interval_ms: int = 1500,
        max_semantic_entries: int = 1200,
        max_frame_refs: int = 800,
    ):
        self.horizon_seconds = max(30, int(horizon_seconds))
        self.profile = "vision_plus" if profile == "vision_plus" else "core_ram"
        self.disk_enabled = bool(disk_enabled and self.profile == "vision_plus")
        self.disk_ttl_minutes = max(1, int(disk_ttl_minutes))
        self.sample_interval_ms = max(100, int(sample_interval_ms))

        self._semantic: deque[SemanticTransition] = deque(maxlen=max_semantic_entries)
        self._frame_refs: deque[FrameReference] = deque(maxlen=max_frame_refs)
        self._frame_payloads: dict[str, bytes] = {}
        self._frame_payload_meta: dict[str, tuple[str, int]] = {}  # ref_id -> (mime, ts_ms)

        self._lock = threading.Lock()
        self._last_sample_ms: dict[int, int] = {}
        self._warnings: list[str] = []

        self._fernet = None
        self._spool_dir: Optional[Path] = None
        if self.disk_enabled:
            self._init_disk_spool()

    def _init_disk_spool(self) -> None:
        root = Path(tempfile.gettempdir()) / "iluminaty_vision_plus_spool"
        root.mkdir(parents=True, exist_ok=True)
        self._spool_dir = root
        if _HAS_FERNET:
            key = os.environ.get("ILUMINATY_VISION_KEY")
            if not key:
                key = Fernet.generate_key().decode("ascii")
                os.environ["ILUMINATY_VISION_KEY"] = key
            self._fernet = Fernet(key.encode("ascii"))
        else:
            self._warnings.append("vision_plus_disk_disabled: cryptography.fernet not available")
            self.disk_enabled = False

    def _trim_locked(self) -> None:
        cutoff_ms = int(time.time() * 1000) - (self.horizon_seconds * 1000)
        while self._semantic and self._semantic[0].timestamp_ms < cutoff_ms:
            self._semantic.popleft()
        while self._frame_refs and self._frame_refs[0].timestamp_ms < cutoff_ms:
            old = self._frame_refs.popleft()
            self._frame_payloads.pop(old.ref_id, None)
            self._frame_payload_meta.pop(old.ref_id, None)
            if old.spool_path:
                try:
                    Path(old.spool_path).unlink(missing_ok=True)
                except Exception:
                    pass

        if self.disk_enabled and self._spool_dir:
            cutoff = time.time() - (self.disk_ttl_minutes * 60)
            try:
                for p in self._spool_dir.glob("*.bin"):
                    if p.stat().st_mtime < cutoff:
                        p.unlink(missing_ok=True)
            except Exception:
                pass

    def _should_sample(self, monitor: int, boundary_reason: str) -> bool:
        if boundary_reason:
            return True
        now_ms = int(time.time() * 1000)
        last = self._last_sample_ms.get(monitor, 0)
        if (now_ms - last) >= self.sample_interval_ms:
            self._last_sample_ms[monitor] = now_ms
            return True
        return False

    def add_frame_ref(
        self,
        slot: FrameSlot,
        *,
        tick_id: int,
        boundary_reason: str = "",
        force: bool = False,
    ) -> Optional[dict]:
        monitor = int(getattr(slot, "monitor_id", 0))
        if not force and not self._should_sample(monitor, boundary_reason):
            return None

        ts_ms = int(slot.timestamp * 1000)
        ref_id = f"fr_{ts_ms}_{uuid.uuid4().hex[:8]}"
        frame_ref = FrameReference(
            ref_id=ref_id,
            timestamp_ms=ts_ms,
            tick_id=int(tick_id),
            monitor=monitor,
            change_score=round(float(getattr(slot, "change_score", 0.0)), 4),
            boundary_reason=(boundary_reason or "sample"),
            mime_type=getattr(slot, "mime_type", "image/webp"),
            width=int(getattr(slot, "width", 0)),
            height=int(getattr(slot, "height", 0)),
            source="ram",
        )

        with self._lock:
            self._frame_refs.append(frame_ref)
            self._frame_payloads[ref_id] = slot.frame_bytes
            self._frame_payload_meta[ref_id] = (frame_ref.mime_type, frame_ref.timestamp_ms)
            if self.disk_enabled and self._fernet and self._spool_dir:
                try:
                    encrypted = self._fernet.encrypt(slot.frame_bytes)
                    spool_path = self._spool_dir / f"{ref_id}.bin"
                    spool_path.write_bytes(encrypted)
                    frame_ref.source = "disk+ram"
                    frame_ref.spool_path = str(spool_path)
                except Exception:
                    # Keep RAM path alive if disk spool fails.
                    pass
            self._trim_locked()
            return asdict(frame_ref)

    def add_semantic_transition(
        self,
        *,
        tick_id: int,
        kind: str,
        summary: str,
        confidence: float,
        monitor: int,
        evidence_refs: Optional[list[str]] = None,
    ) -> None:
        item = SemanticTransition(
            timestamp_ms=int(time.time() * 1000),
            tick_id=int(tick_id),
            kind=(kind or "event")[:48],
            summary=(summary or "")[:240],
            confidence=max(0.0, min(1.0, float(confidence))),
            monitor=int(monitor),
            evidence_refs=[str(x)[:160] for x in (evidence_refs or [])[:12]],
        )
        with self._lock:
            self._semantic.append(item)
            self._trim_locked()

    def get_trace(self, seconds: float = 90) -> list[dict]:
        cutoff_ms = int(time.time() * 1000) - int(max(1, seconds) * 1000)
        with self._lock:
            semantic = [asdict(s) for s in self._semantic if s.timestamp_ms >= cutoff_ms]
            frame_refs = [asdict(f) for f in self._frame_refs if f.timestamp_ms >= cutoff_ms]
        return {
            "semantic": semantic,
            "frame_refs": frame_refs,
        }

    def get_frame_bytes(self, ref_id: str) -> Optional[bytes]:
        with self._lock:
            payload = self._frame_payloads.get(ref_id)
            if payload is not None:
                return payload
            target = None
            for ref in reversed(self._frame_refs):
                if ref.ref_id == ref_id:
                    target = ref
                    break
        if not target or not target.spool_path or not self._fernet:
            return None
        try:
            encrypted = Path(target.spool_path).read_bytes()
            return self._fernet.decrypt(encrypted)
        except Exception:
            return None

    def get_frame_base64(self, ref_id: str) -> Optional[dict]:
        data = self.get_frame_bytes(ref_id)
        if data is None:
            return None
        with self._lock:
            mime = self._frame_payload_meta.get(ref_id, ("image/webp", 0))[0]
        return {
            "ref_id": ref_id,
            "mime_type": mime,
            "image_base64": base64.b64encode(data).decode("ascii"),
        }

    def query_frame_refs(
        self,
        *,
        at_ms: Optional[int] = None,
        window_seconds: Optional[float] = None,
        monitor_id: Optional[int] = None,
        limit: int = 10,
    ) -> list[dict]:
        limit = max(1, min(30, int(limit)))
        with self._lock:
            refs = list(self._frame_refs)

        if monitor_id is not None:
            refs = [r for r in refs if r.monitor == int(monitor_id)]

        if at_ms is not None:
            center = int(at_ms)
            refs.sort(key=lambda r: abs(r.timestamp_ms - center))
            selected = refs[:limit]
        else:
            secs = max(1.0, float(window_seconds or self.horizon_seconds))
            cutoff = int(time.time() * 1000) - int(secs * 1000)
            selected = [r for r in refs if r.timestamp_ms >= cutoff][-limit:]
        return [asdict(r) for r in selected]

    def stats(self) -> dict:
        with self._lock:
            return {
                "profile": self.profile,
                "disk_enabled": self.disk_enabled,
                "semantic_entries": len(self._semantic),
                "frame_refs": len(self._frame_refs),
                "payloads_in_ram": len(self._frame_payloads),
                "horizon_seconds": self.horizon_seconds,
                "warnings": list(self._warnings),
            }
