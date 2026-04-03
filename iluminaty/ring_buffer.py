"""
ILUMINATY - Ring Buffer Visual en RAM
=====================================
Buffer circular que vive SOLO en memoria.
Nada toca disco. Cuando el proceso muere, todo desaparece.

Arquitectura:
- collections.deque con maxlen fijo = auto-eviction
- Cada slot: { timestamp, frame_bytes, hash, metadata }
- Frame nuevo entra por la derecha, el más viejo sale por la izquierda
- Sin locks pesados: un threading.Lock simple basta para el MVP
"""

import time
import logging
import hashlib
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Soft dependency: cv2 for histogram-based change scoring
try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False


@dataclass(slots=True)
class FrameSlot:
    """Un frame en el ring buffer. Vive solo en RAM."""
    timestamp: float
    frame_bytes: bytes  # comprimido (JPEG/WebP/PNG)
    phash: str          # content hash for cache/dedup (MD5)
    width: int
    height: int
    mime_type: str = "image/jpeg"
    region: Optional[str] = None
    change_score: float = 0.0
    monitor_id: int = 0  # IPA: which monitor this frame came from (0=all)


class RingBuffer:
    """
    Buffer circular en RAM pura. Cero disco.
    
    Uso de memoria estimado:
    - 1 fps, JPEG 720p (~40-60KB), 30 slots = ~1.5MB
    - 2 fps, JPEG 1080p (~80-120KB), 60 slots = ~6MB
    - 5 fps, JPEG 1080p (~80-120KB), 150 slots = ~15MB
    
    Incluso el caso extremo es trivial para RAM moderna.
    """

    def __init__(self, max_seconds: int = 30, target_fps: float = 1.0):
        self.max_slots = int(max_seconds * target_fps)
        self.target_fps = target_fps
        self._buffer: deque[FrameSlot] = deque(maxlen=self.max_slots)
        self._lock = threading.Lock()
        # IPA v2: per-monitor hash/thumb state (keyed by monitor_id)
        self._last_hash: dict[int, str] = {}
        self._last_thumb: dict[int, Optional[np.ndarray]] = {}
        self._frame_count: int = 0
        self._dropped_count: int = 0  # frames que no cambiaron
        
    @property
    def size(self) -> int:
        return len(self._buffer)
    
    @property
    def memory_bytes(self) -> int:
        """Uso actual de RAM del buffer."""
        with self._lock:
            return sum(len(slot.frame_bytes) for slot in self._buffer)
    
    @property
    def memory_mb(self) -> float:
        return self.memory_bytes / (1024 * 1024)
    
    @property
    def stats(self) -> dict:
        return {
            "slots_used": self.size,
            "slots_max": self.max_slots,
            "memory_mb": round(self.memory_mb, 2),
            "total_frames_captured": self._frame_count,
            "frames_dropped_no_change": self._dropped_count,
            "efficiency_pct": round(
                (self._dropped_count / max(self._frame_count, 1)) * 100, 1
            ),
            "target_fps": self.target_fps,
            "buffer_seconds": self.max_slots / max(self.target_fps, 0.1),
        }

    def _compute_hash(self, frame_bytes: bytes) -> str:
        """Fast content hash for change detection.
        Samples head + tail instead of hashing all bytes.
        For an 80KB WebP: 4KB sample = ~5x faster than full-frame MD5
        while still catching all real changes (header + entropy tail differ on any change).
        """
        sample = frame_bytes[:2048] + frame_bytes[-2048:] if len(frame_bytes) > 4096 else frame_bytes
        return hashlib.md5(sample).hexdigest()

    def _decode_thumbnail(self, frame_bytes: bytes) -> Optional[np.ndarray]:
        """Decode frame to tiny grayscale thumbnail (128x72) for histogram comparison.
        IPA Gate 1: ~0.2ms — negligible cost for continuous change scoring."""
        if not _HAS_CV2:
            return None
        try:
            arr = np.frombuffer(frame_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
            if img is None:
                return None
            return cv2.resize(img, (128, 72), interpolation=cv2.INTER_AREA)
        except Exception:
            return None

    def _compute_change_score(self, new_hash: str, frame_bytes: bytes, monitor_id: int = 0) -> float:
        """IPA continuous change score: 0.0 (identical) to 1.0 (completely different).

        Two-tier scoring per monitor (v2: each monitor compared against its own previous frame):
        1. MD5 fast path: if hash matches → 0.0 (no decode needed, <0.01ms)
        2. Histogram path: decode to 128x72 thumbnail, compare 64-bin histograms
           using cv2.compareHist(CORREL). Returns continuous 0.0-1.0. (~0.3ms)

        Falls back to binary (0.0/1.0) if cv2 is not available.
        """
        prev_hash = self._last_hash.get(monitor_id)
        prev_thumb = self._last_thumb.get(monitor_id)

        if prev_hash is None:
            # First frame for this monitor — cache thumbnail and return 1.0
            self._last_thumb[monitor_id] = self._decode_thumbnail(frame_bytes)
            return 1.0

        # Fast path: identical bytes = 0.0
        if new_hash == prev_hash:
            return 0.0

        # Histogram comparison for continuous scoring
        new_thumb = self._decode_thumbnail(frame_bytes)
        if new_thumb is not None and prev_thumb is not None:
            try:
                hist_new = cv2.calcHist([new_thumb], [0], None, [64], [0, 256])
                hist_prev = cv2.calcHist([prev_thumb], [0], None, [64], [0, 256])
                cv2.normalize(hist_new, hist_new)
                cv2.normalize(hist_prev, hist_prev)
                correlation = cv2.compareHist(hist_new, hist_prev, cv2.HISTCMP_CORREL)
                self._last_thumb[monitor_id] = new_thumb
                return round(max(1.0 - max(correlation, 0.0), 0.0), 4)
            except Exception as e:
                logger.debug("Histogram change score fallback on monitor %s: %s", monitor_id, e)

        # Fallback: binary scoring (cv2 not available or decode failed)
        if new_thumb is not None:
            self._last_thumb[monitor_id] = new_thumb
        return 1.0

    def push(
        self,
        frame_bytes: bytes,
        width: int,
        height: int,
        region: Optional[str] = None,
        mime_type: str = "image/jpeg",
        skip_if_unchanged: bool = True,
        monitor_id: int = 0,
    ) -> bool:
        """
        Mete un frame al buffer. Retorna True si se guardó, False si se descartó.

        Con skip_if_unchanged=True, frames idénticos al anterior se descartan.
        IPA: change_score is now continuous 0.0-1.0 via histogram comparison.
        """
        self._frame_count += 1
        phash = self._compute_hash(frame_bytes)
        change_score = self._compute_change_score(phash, frame_bytes, monitor_id)

        if skip_if_unchanged and change_score == 0.0:
            self._dropped_count += 1
            return False

        slot = FrameSlot(
            timestamp=time.time(),
            frame_bytes=frame_bytes,
            phash=phash,
            width=width,
            height=height,
            mime_type=mime_type,
            region=region,
            change_score=change_score,
            monitor_id=monitor_id,
        )

        with self._lock:
            self._buffer.append(slot)
            self._last_hash[monitor_id] = phash

        return True

    def get_latest(self) -> Optional[FrameSlot]:
        """El frame más reciente."""
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def get_latest_n(self, n: int = 5) -> list[FrameSlot]:
        """Los últimos N frames (para dar contexto temporal a la IA)."""
        with self._lock:
            buf_len = len(self._buffer)
            if n >= buf_len:
                return list(self._buffer)
            return [self._buffer[i] for i in range(buf_len - n, buf_len)]

    def get_since(self, seconds_ago: float) -> list[FrameSlot]:
        """Frames desde hace N segundos. Para 'qué acaba de pasar'."""
        cutoff = time.time() - seconds_ago
        with self._lock:
            return [s for s in self._buffer if s.timestamp >= cutoff]

    def get_all(self) -> list[FrameSlot]:
        """Todo el buffer actual. Para streaming completo a la IA."""
        with self._lock:
            return list(self._buffer)

    def get_latest_for_monitor(self, monitor_id: int) -> Optional[FrameSlot]:
        """Most recent frame from a specific monitor."""
        with self._lock:
            for slot in reversed(self._buffer):
                if slot.monitor_id == monitor_id:
                    return slot
        return None

    def get_latest_per_monitor(self) -> dict[int, FrameSlot]:
        """Latest frame from each monitor. Returns {monitor_id: FrameSlot}."""
        result: dict[int, FrameSlot] = {}
        with self._lock:
            for slot in reversed(self._buffer):
                mid = slot.monitor_id
                if mid not in result:
                    result[mid] = slot
        return result

    def clear(self):
        """Limpia todo. Útil para un 'reset' manual."""
        with self._lock:
            self._buffer.clear()
            self._last_hash.clear()
            self._last_thumb.clear()

    def flush(self):
        """Alias de clear — destruye toda la evidencia visual."""
        self.clear()
