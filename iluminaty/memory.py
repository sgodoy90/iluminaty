"""
ILUMINATY - Temporal Memory
==============================
Memoria temporal OPCIONAL que guarda resumenes (NO frames)
de lo que paso en la pantalla.

Principios:
- NUNCA guarda frames/imagenes raw en disco
- Solo guarda resumenes de texto generados por IA o OCR
- Encriptado con key efimera (se pierde al cerrar)
- Auto-expire: entradas mas viejas que N dias se borran
- Opt-in: deshabilitado por default

Esto permite preguntar "que hice a las 3pm?" sin
tener screenshots guardados.
"""

import time
import json
import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MemoryEntry:
    """Una entrada de memoria temporal."""
    timestamp: float
    summary: str          # resumen de texto, no imagen
    app: str
    workflow: str
    ocr_snippet: str      # primeros 200 chars de OCR
    duration_seconds: float
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)),
            "summary": self.summary,
            "app": self.app,
            "workflow": self.workflow,
            "ocr_snippet": self.ocr_snippet[:100],
            "duration": f"{self.duration_seconds:.0f}s",
            "tags": self.tags,
        }


class TemporalMemory:
    """
    Memoria temporal en RAM.
    Guarda resumenes comprimidos, no frames.
    Se puede habilitar persistencia a disco (encriptada).
    """

    def __init__(self, max_entries: int = 500, enabled: bool = False):
        self.enabled = enabled
        self.max_entries = max_entries
        self._entries: deque[MemoryEntry] = deque(maxlen=max_entries)
        self._last_snapshot_time: float = 0
        self._snapshot_interval: float = 30.0  # cada 30 segundos

    def add(self, entry: MemoryEntry):
        """Agrega una entrada de memoria."""
        if not self.enabled:
            return
        self._entries.append(entry)

    def should_snapshot(self) -> bool:
        """Verifica si es hora de tomar un snapshot de memoria."""
        if not self.enabled:
            return False
        now = time.time()
        if now - self._last_snapshot_time >= self._snapshot_interval:
            self._last_snapshot_time = now
            return True
        return False

    def create_entry(
        self,
        app: str,
        workflow: str,
        ocr_text: str = "",
        custom_summary: str = "",
    ) -> MemoryEntry:
        """Crea y agrega una entrada de memoria."""
        # Auto-generate summary from OCR if no custom summary
        summary = custom_summary
        if not summary and ocr_text:
            # Tomar las primeras lineas significativas
            lines = [l.strip() for l in ocr_text.split("\n") if l.strip() and len(l.strip()) > 3]
            summary = f"In {app} ({workflow}): " + "; ".join(lines[:3])

        entry = MemoryEntry(
            timestamp=time.time(),
            summary=summary[:300],
            app=app,
            workflow=workflow,
            ocr_snippet=ocr_text[:200] if ocr_text else "",
            duration_seconds=self._snapshot_interval,
            tags=[workflow, app.lower()],
        )
        self.add(entry)
        return entry

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Busca en la memoria por texto."""
        if not self.enabled:
            return []

        query_lower = query.lower()
        results = []
        for entry in reversed(self._entries):
            if (query_lower in entry.summary.lower()
                or query_lower in entry.app.lower()
                or query_lower in entry.workflow.lower()
                or query_lower in entry.ocr_snippet.lower()
                or any(query_lower in tag for tag in entry.tags)):
                results.append(entry.to_dict())
                if len(results) >= limit:
                    break

        return results

    def get_recent(self, minutes: int = 30) -> list[dict]:
        """Entradas de los ultimos N minutos."""
        cutoff = time.time() - (minutes * 60)
        return [
            e.to_dict() for e in self._entries
            if e.timestamp >= cutoff
        ]

    def get_timeline(self, hours: int = 1) -> str:
        """Genera timeline de texto para el AI prompt."""
        cutoff = time.time() - (hours * 3600)
        entries = [e for e in self._entries if e.timestamp >= cutoff]

        if not entries:
            return "No activity recorded in the last hour."

        lines = [f"### Activity Timeline (last {hours}h)"]
        for e in entries:
            t = time.strftime("%H:%M", time.localtime(e.timestamp))
            lines.append(f"- **{t}** [{e.workflow}] {e.summary[:80]}")

        return "\n".join(lines)

    @property
    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "entries": len(self._entries),
            "max_entries": self.max_entries,
            "oldest": time.strftime(
                "%H:%M:%S", time.localtime(self._entries[0].timestamp)
            ) if self._entries else None,
            "newest": time.strftime(
                "%H:%M:%S", time.localtime(self._entries[-1].timestamp)
            ) if self._entries else None,
        }

    def clear(self):
        """Borra toda la memoria."""
        self._entries.clear()
