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
import hashlib
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True)
class FrameSlot:
    """Un frame en el ring buffer. Vive solo en RAM."""
    timestamp: float
    frame_bytes: bytes  # comprimido (JPEG/WebP/PNG)
    phash: str          # hash perceptual para change detection
    width: int
    height: int
    mime_type: str = "image/jpeg"
    region: Optional[str] = None
    change_score: float = 0.0


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
        self._last_hash: Optional[str] = None
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
        """Hash rápido para change detection. MD5 es suficiente aquí — no es crypto."""
        return hashlib.md5(frame_bytes).hexdigest()

    def _compute_change_score(self, new_hash: str) -> float:
        """0.0 si idéntico al frame anterior, 1.0 si cambió."""
        if self._last_hash is None:
            return 1.0
        return 0.0 if new_hash == self._last_hash else 1.0

    def push(
        self,
        frame_bytes: bytes,
        width: int,
        height: int,
        region: Optional[str] = None,
        mime_type: str = "image/jpeg",
        skip_if_unchanged: bool = True,
    ) -> bool:
        """
        Mete un frame al buffer. Retorna True si se guardó, False si se descartó.
        
        Con skip_if_unchanged=True, frames idénticos al anterior se descartan.
        Esto es el corazón del ahorro de tokens y RAM.
        """
        self._frame_count += 1
        phash = self._compute_hash(frame_bytes)
        change_score = self._compute_change_score(phash)
        
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
        )
        
        with self._lock:
            self._buffer.append(slot)  # deque con maxlen auto-evicta el más viejo
            self._last_hash = phash
        
        return True

    def get_latest(self) -> Optional[FrameSlot]:
        """El frame más reciente."""
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def get_latest_n(self, n: int = 5) -> list[FrameSlot]:
        """Los últimos N frames (para dar contexto temporal a la IA)."""
        with self._lock:
            items = list(self._buffer)
        return items[-n:]

    def get_since(self, seconds_ago: float) -> list[FrameSlot]:
        """Frames desde hace N segundos. Para 'qué acaba de pasar'."""
        cutoff = time.time() - seconds_ago
        with self._lock:
            return [s for s in self._buffer if s.timestamp >= cutoff]

    def get_all(self) -> list[FrameSlot]:
        """Todo el buffer actual. Para streaming completo a la IA."""
        with self._lock:
            return list(self._buffer)

    def clear(self):
        """Limpia todo. Útil para un 'reset' manual."""
        with self._lock:
            self._buffer.clear()
            self._last_hash = None

    def flush(self):
        """Alias de clear — destruye toda la evidencia visual."""
        self.clear()
