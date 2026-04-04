"""
ILUMINATY - Visual Memory
==========================
Persistencia de contexto visual entre sesiones de IA.

El problema: cuando una IA se reconecta, empieza completamente ciega.
No sabe en qué estaba trabajando el usuario, qué ventanas había,
ni qué pasó en la sesión anterior.

La solución: al finalizar cada sesión, guardar el estado visual compacto
en disco. Al iniciar la siguiente sesión, restaurarlo automáticamente.

Qué se guarda (JSON comprimido, ~10-50KB por sesión):
  - Spatial context del momento del cierre
  - Últimas N ventanas activas con timestamps
  - Gate events significativos de los últimos 30 min
  - WorldState del IPA al cierre
  - OCR text del frame más reciente por monitor

Qué NO se guarda:
  - Imágenes (ring buffer vive solo en RAM)
  - Datos personales más allá del contexto de trabajo
  - Historial completo de acciones

Uso:
    memory = VisualMemory()
    memory.save(state_dict)              # al cerrar servidor
    context = memory.load()             # al abrir nueva sesión
    context.to_session_prompt()         # texto para inyectar en system prompt
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("iluminaty.visual_memory")

# Default storage location
DEFAULT_MEMORY_DIR = Path.home() / ".iluminaty" / "memory"
MAX_SESSIONS = 10     # keep last 10 sessions
MAX_EVENTS   = 20     # gate events to persist


@dataclass
class SessionMemory:
    """Snapshot of visual context at session end."""
    saved_at:       float = field(default_factory=time.time)
    session_id:     str   = ""
    duration_s:     float = 0.0

    # Spatial context
    monitor_count:  int   = 0
    active_monitor: int   = 0
    monitors:       list  = field(default_factory=list)    # [{id, zone, width, height}]
    active_window:  dict  = field(default_factory=dict)    # {title, app, monitor_id}
    window_history: list  = field(default_factory=list)    # last N window titles

    # IPA state
    scene_state:    str   = "unknown"
    task_phase:     str   = "unknown"
    workflow:       str   = "unknown"
    gate_events:    list  = field(default_factory=list)    # last N significant events

    # OCR text per monitor (for context, not raw pixels)
    ocr_by_monitor: dict  = field(default_factory=dict)    # {monitor_id: text[:500]}

    # Domain
    domain_pack:    str   = "general"

    def to_dict(self) -> dict:
        return {
            "saved_at":       self.saved_at,
            "session_id":     self.session_id,
            "duration_s":     round(self.duration_s, 1),
            "monitor_count":  self.monitor_count,
            "active_monitor": self.active_monitor,
            "monitors":       self.monitors,
            "active_window":  self.active_window,
            "window_history": self.window_history,
            "scene_state":    self.scene_state,
            "task_phase":     self.task_phase,
            "workflow":       self.workflow,
            "gate_events":    self.gate_events,
            "ocr_by_monitor": self.ocr_by_monitor,
            "domain_pack":    self.domain_pack,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionMemory":
        m = cls()
        for k, v in d.items():
            if hasattr(m, k):
                setattr(m, k, v)
        return m

    def to_session_prompt(self) -> str:
        """Generate a compact system prompt injection for session start.

        ~200-400 tokens. Tells the AI what the user was doing last time
        without sending any images.
        """
        import datetime
        saved = datetime.datetime.fromtimestamp(self.saved_at)
        age_h = (time.time() - self.saved_at) / 3600

        if age_h > 48:
            age_str = f"{int(age_h / 24)} days ago"
        elif age_h > 1:
            age_str = f"{int(age_h)}h ago"
        else:
            age_str = f"{int(age_h * 60)}min ago"

        lines = [
            f"# Previous Session Context (saved {age_str})",
            f"Duration: {self.duration_s/60:.0f} minutes",
            "",
        ]

        # Active work
        if self.active_window:
            title = self.active_window.get("title", "unknown")[:80]
            app   = self.active_window.get("app", "")
            mon   = self.active_window.get("monitor_id", "?")
            lines.append(f"Last active: {app or title} on Monitor {mon}")

        if self.workflow and self.workflow != "unknown":
            lines.append(f"Workflow: {self.workflow} | Phase: {self.task_phase}")

        if self.domain_pack and self.domain_pack != "general":
            lines.append(f"Domain: {self.domain_pack}")

        # Monitor layout
        if self.monitors:
            lines.append("")
            lines.append("Monitor layout:")
            for m in self.monitors:
                active_mark = " ← was active" if m.get("id") == self.active_monitor else ""
                lines.append(f"  M{m.get('id')} [{m.get('zone','?')}] {m.get('width')}x{m.get('height')}{active_mark}")

        # Recent windows
        if self.window_history:
            recent = list(dict.fromkeys(self.window_history))[:5]
            lines.append("")
            lines.append(f"Recent windows: {' | '.join(recent)}")

        # OCR context
        for mid, text in self.ocr_by_monitor.items():
            if text and len(text.strip()) > 10:
                preview = text.strip()[:200].replace("\n", " ")
                lines.append(f"M{mid} content: {preview}")

        # Significant events
        if self.gate_events:
            lines.append("")
            lines.append("Last events before session end:")
            for evt in self.gate_events[-5:]:
                lines.append(f"  • {evt.get('description', '')} ({evt.get('event_type', '')})")

        lines.append("")
        lines.append("Note: This is the visual context from the previous session.")
        lines.append("Call get_spatial_context for current state.")

        return "\n".join(lines)

    def age_hours(self) -> float:
        return (time.time() - self.saved_at) / 3600

    def is_fresh(self, max_hours: float = 24.0) -> bool:
        return self.age_hours() < max_hours


class VisualMemory:
    """Manages persistence of visual session context.

    Saves on server shutdown, loads on startup.
    Storage: ~/.iluminaty/memory/ (gzipped JSON, ~10-50KB per session)
    """

    def __init__(self, memory_dir: Optional[Path] = None):
        self._dir = Path(memory_dir or DEFAULT_MEMORY_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._session_start = time.time()
        self._session_id = f"session_{int(self._session_start)}"
        self._current: Optional[SessionMemory] = None

    # ── Save ──────────────────────────────────────────────────────────────────

    def save(self, state: dict) -> bool:
        """Save current session state to disk.

        state should contain keys from _state: perception, monitor_mgr,
        ipa_bridge, context — all optional, handled gracefully.
        """
        try:
            mem = SessionMemory(
                saved_at=time.time(),
                session_id=self._session_id,
                duration_s=time.time() - self._session_start,
            )

            # Spatial context
            spatial = state.get("spatial", {})
            if spatial:
                mem.monitor_count  = spatial.get("monitor_count", 0)
                mem.active_monitor = spatial.get("active_monitor_id", 0)
                mem.monitors       = [
                    {
                        "id":     m.get("id"),
                        "zone":   m.get("zone", "?"),
                        "width":  m.get("width"),
                        "height": m.get("height"),
                    }
                    for m in spatial.get("monitors", [])
                ]
                aw = spatial.get("active_window", {})
                if aw:
                    mem.active_window = {
                        "title":      aw.get("title", "")[:80],
                        "app":        aw.get("app_name", ""),
                        "monitor_id": aw.get("monitor_id", 0),
                    }

            # Context
            ctx = state.get("context", {})
            if ctx:
                mem.workflow   = ctx.get("workflow", "unknown")
                mem.task_phase = ctx.get("task_phase", "unknown")

            # IPA gate events
            ipa_events = state.get("ipa_events", [])
            mem.gate_events = [
                {
                    "event_type":  e.get("event_type", ""),
                    "description": e.get("description", ""),
                    "timestamp":   e.get("timestamp", 0),
                }
                for e in ipa_events[-MAX_EVENTS:]
            ]

            # OCR by monitor
            mem.ocr_by_monitor = {
                str(mid): text[:500]
                for mid, text in state.get("ocr_by_monitor", {}).items()
                if text and text.strip()
            }

            # Window history
            mem.window_history = state.get("window_history", [])[-20:]

            # Domain
            mem.domain_pack = state.get("domain_pack", "general")

            # Scene state
            mem.scene_state = state.get("scene_state", "unknown")

            # Write to disk
            path = self._dir / f"{self._session_id}.json.gz"
            data = json.dumps(mem.to_dict(), ensure_ascii=False).encode()
            with gzip.open(path, "wb") as f:
                f.write(data)

            log.info("Visual memory saved: %s (%.1f KB)", path.name, len(data)/1024)
            self._current = mem

            # Trim old sessions
            self._trim_old_sessions()
            return True

        except Exception as e:
            log.warning("Failed to save visual memory: %s", e)
            return False

    # ── Load ──────────────────────────────────────────────────────────────────

    def load(self, max_age_hours: float = 48.0) -> Optional[SessionMemory]:
        """Load the most recent session from disk."""
        try:
            sessions = sorted(
                self._dir.glob("session_*.json.gz"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for path in sessions:
                try:
                    with gzip.open(path, "rb") as f:
                        data = json.loads(f.read().decode())
                    mem = SessionMemory.from_dict(data)
                    if mem.is_fresh(max_age_hours):
                        log.info("Visual memory loaded: %s (age=%.1fh)",
                                 path.name, mem.age_hours())
                        return mem
                except Exception:
                    continue
        except Exception as e:
            log.debug("Failed to load visual memory: %s", e)
        return None

    def load_all(self, limit: int = 5) -> list[SessionMemory]:
        """Load last N sessions for multi-session context."""
        results = []
        try:
            sessions = sorted(
                self._dir.glob("session_*.json.gz"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:limit]
            for path in sessions:
                try:
                    with gzip.open(path, "rb") as f:
                        data = json.loads(f.read().decode())
                    results.append(SessionMemory.from_dict(data))
                except Exception:
                    continue
        except Exception as e:
            log.debug("load_all failed: %s", e)
        return results

    def has_memory(self, max_age_hours: float = 48.0) -> bool:
        mem = self.load(max_age_hours)
        return mem is not None

    def clear(self) -> None:
        """Clear all saved sessions."""
        for path in self._dir.glob("session_*.json.gz"):
            try:
                path.unlink()
            except Exception:
                pass

    def _trim_old_sessions(self) -> None:
        sessions = sorted(
            self._dir.glob("session_*.json.gz"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in sessions[MAX_SESSIONS:]:
            try:
                old.unlink()
            except Exception:
                pass

    def stats(self) -> dict:
        sessions = list(self._dir.glob("session_*.json.gz"))
        total_kb = sum(p.stat().st_size for p in sessions) / 1024
        return {
            "memory_dir":    str(self._dir),
            "sessions_saved": len(sessions),
            "total_kb":      round(total_kb, 1),
            "session_id":    self._session_id,
        }
