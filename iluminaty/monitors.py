"""
ILUMINATY - Multi-Monitor Intelligence
=========================================
Captura inteligente de multiples monitores.

Features:
- Per-monitor capture con FPS/quality independiente
- Focus-follows-activity: mas FPS en monitor activo
- Monitor-specific blur profiles
- Cross-monitor context tracking
"""

from typing import Optional
from dataclasses import dataclass

import mss


@dataclass
class MonitorInfo:
    """Info de un monitor."""
    id: int
    left: int
    top: int
    width: int
    height: int
    is_primary: bool
    is_active: bool = False     # tiene la ventana activa?
    fps_multiplier: float = 1.0  # 1.0 = normal, 2.0 = doble FPS


class MonitorManager:
    """
    Gestiona multiples monitores.
    El monitor con la ventana activa recibe mas FPS.
    Los monitores inactivos bajan FPS para ahorrar CPU.
    """

    def __init__(self, active_multiplier: float = 2.0, inactive_multiplier: float = 0.5):
        self.active_multiplier = active_multiplier
        self.inactive_multiplier = inactive_multiplier
        self._monitors: list[MonitorInfo] = []
        self._active_monitor_id: int = 1

    def refresh(self) -> list[MonitorInfo]:
        """Detecta monitores disponibles."""
        with mss.mss() as sct:
            self._monitors = []
            for i, m in enumerate(sct.monitors):
                if i == 0:
                    continue  # skip "all monitors combined"
                self._monitors.append(MonitorInfo(
                    id=i,
                    left=m["left"],
                    top=m["top"],
                    width=m["width"],
                    height=m["height"],
                    is_primary=(i == 1),
                    is_active=(i == self._active_monitor_id),
                    fps_multiplier=self.active_multiplier if i == self._active_monitor_id else self.inactive_multiplier,
                ))
        return self._monitors

    def set_active(self, monitor_id: int):
        """Marca un monitor como activo (basado en ventana activa)."""
        self._active_monitor_id = monitor_id
        for m in self._monitors:
            m.is_active = (m.id == monitor_id)
            m.fps_multiplier = self.active_multiplier if m.is_active else self.inactive_multiplier

    def detect_active_from_window(self, window_bounds: dict) -> int:
        """Detecta en que monitor esta la ventana activa."""
        if not window_bounds or not self._monitors:
            return 1

        wx = window_bounds.get("left", 0) + window_bounds.get("width", 0) // 2
        wy = window_bounds.get("top", 0) + window_bounds.get("height", 0) // 2

        for m in self._monitors:
            if (m.left <= wx < m.left + m.width and
                m.top <= wy < m.top + m.height):
                self.set_active(m.id)
                return m.id

        return self._active_monitor_id

    def get_monitor(self, monitor_id: int) -> Optional[MonitorInfo]:
        """Obtiene info de un monitor especifico."""
        for m in self._monitors:
            if m.id == monitor_id:
                return m
        return None

    def get_active_monitor(self) -> Optional[MonitorInfo]:
        """Returns the currently active monitor (where the active window is)."""
        for m in self._monitors:
            if m.is_active:
                return m
        # Fallback: return primary monitor
        for m in self._monitors:
            if m.is_primary:
                return m
        return self._monitors[0] if self._monitors else None

    @property
    def monitors(self) -> list[MonitorInfo]:
        if not self._monitors:
            self.refresh()
        return self._monitors

    @property
    def count(self) -> int:
        return len(self.monitors)

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "active": self._active_monitor_id,
            "monitors": [
                {
                    "id": m.id,
                    "resolution": f"{m.width}x{m.height}",
                    "position": f"({m.left},{m.top})",
                    "primary": m.is_primary,
                    "active": m.is_active,
                    "fps_multiplier": m.fps_multiplier,
                }
                for m in self.monitors
            ],
        }
