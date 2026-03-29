"""
ILUMINATY - E05: Multi-Modal Fusion
======================================
Fusiona vision + audio + contexto en UNA percepcion unificada.

En vez de datos separados:
  - Imagen: screenshot
  - Audio: transcripcion
  - Contexto: workflow

Ahora TODO junto:
  "El usuario esta en una reunion de Zoom discutiendo el presupuesto Q3.
   La hoja de calculo en pantalla muestra ingresos de $2.3M.
   Sarah acaba de preguntar sobre el timeline de contratacion.
   El usuario parece estar compartiendo pantalla."

Este es el formato DEFINITIVO que la IA recibe.
"""

import time
import base64
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UnifiedPerception:
    """
    Percepcion unificada: todo lo que la IA necesita saber,
    fusionado en un solo paquete coherente.
    """
    # Visual
    frame_base64: str
    frame_width: int
    frame_height: int
    frame_format: str

    # Text (OCR)
    screen_text: str
    text_block_count: int

    # Audio
    is_speaking: bool
    audio_level: float
    transcript: str

    # Context
    active_app: str
    window_title: str
    workflow: str
    workflow_confidence: float
    focus_level: str
    time_in_workflow: float

    # Spatial
    layout_zones: list
    screen_description: str

    # Watchdog
    active_alerts: list

    # Profile
    user_context: str

    # Meta
    timestamp: float
    monitor_count: int

    def to_ai_prompt(self) -> str:
        """
        El prompt DEFINITIVO que la IA recibe.
        Fusiona todo en un solo mensaje coherente en ingles.
        """
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))

        sections = []

        # Header
        sections.append(f"## Live Screen Perception - {ts}")

        # Context bar
        sections.append(
            f"**User is {self.workflow}** in {self.active_app} | "
            f"Focus: {self.focus_level} | "
            f"{'Speaking' if self.is_speaking else 'Silent'} | "
            f"{self.monitor_count} monitor(s)"
        )

        # Active alerts (PRIORITY)
        if self.active_alerts:
            sections.append("\n### ALERTS (action may be needed)")
            for alert in self.active_alerts[:5]:
                sev = alert.get("severity", "info").upper()
                sections.append(f"- **[{sev}]** {alert.get('message', '')}")

        # Window
        sections.append(f"\n**Window**: {self.window_title[:80]}")

        # Screen text (OCR)
        if self.screen_text:
            text_preview = self.screen_text[:1500]
            sections.append(f"\n### Visible Text ({self.text_block_count} blocks)\n```\n{text_preview}\n```")

        # Audio transcript
        if self.transcript:
            sections.append(f"\n### Recent Speech\n> {self.transcript[:500]}")

        # Layout
        if self.layout_zones:
            sections.append("\n### Screen Layout")
            for zone in self.layout_zones[:6]:
                sections.append(f"- {zone.get('name', '?')}: {zone.get('content', '?')} ({zone.get('coverage', '?')})")

        # User profile context
        if self.user_context:
            sections.append(f"\n{self.user_context}")

        # Instructions
        sections.append(
            "\n### How to Help"
            "\nAn image of the current screen is attached. "
            "You have full visual, audio, and contextual awareness. "
            "If there are ALERTS, address them first. "
            "Otherwise, observe and assist based on what the user is doing."
        )

        return "\n".join(sections)

    def to_dict(self, include_image: bool = False) -> dict:
        result = {
            "timestamp": self.timestamp,
            "workflow": self.workflow,
            "focus": self.focus_level,
            "app": self.active_app,
            "window": self.window_title[:100],
            "is_speaking": self.is_speaking,
            "audio_level": self.audio_level,
            "transcript": self.transcript[:200] if self.transcript else "",
            "ocr_blocks": self.text_block_count,
            "alerts": self.active_alerts,
            "layout_zones": self.layout_zones,
            "monitor_count": self.monitor_count,
            "ai_prompt": self.to_ai_prompt(),
        }
        if include_image:
            result["image_base64"] = self.frame_base64
            result["image_format"] = self.frame_format
        return result


class PerceptionFusion:
    """
    Motor de fusion multi-modal.
    Combina datos de todos los modulos de ILUMINATY
    en una UnifiedPerception coherente.
    """

    def fuse(
        self,
        # Visual
        frame_bytes: bytes = b"",
        frame_width: int = 0,
        frame_height: int = 0,
        frame_format: str = "image/webp",
        # OCR
        ocr_text: str = "",
        ocr_blocks: list = None,
        # Audio
        is_speaking: bool = False,
        audio_level: float = 0.0,
        transcript: str = "",
        # Context
        active_app: str = "",
        window_title: str = "",
        workflow: str = "unknown",
        workflow_confidence: float = 0.0,
        focus_level: str = "unknown",
        time_in_workflow: float = 0.0,
        # Spatial
        layout_zones: list = None,
        # Watchdog
        active_alerts: list = None,
        # Profile
        user_context: str = "",
        # Meta
        monitor_count: int = 1,
    ) -> UnifiedPerception:
        """Fusiona todas las fuentes en una percepcion unificada."""

        frame_b64 = base64.b64encode(frame_bytes).decode("ascii") if frame_bytes else ""

        # Generar descripcion del screen
        screen_desc_parts = [f"User is in {active_app}"]
        if workflow != "unknown":
            screen_desc_parts.append(f"({workflow})")
        if is_speaking:
            screen_desc_parts.append("and speaking")

        return UnifiedPerception(
            frame_base64=frame_b64,
            frame_width=frame_width,
            frame_height=frame_height,
            frame_format=frame_format,
            screen_text=ocr_text,
            text_block_count=len(ocr_blocks) if ocr_blocks else 0,
            is_speaking=is_speaking,
            audio_level=audio_level,
            transcript=transcript,
            active_app=active_app,
            window_title=window_title,
            workflow=workflow,
            workflow_confidence=workflow_confidence,
            focus_level=focus_level,
            time_in_workflow=time_in_workflow,
            layout_zones=layout_zones or [],
            screen_description=" ".join(screen_desc_parts),
            active_alerts=active_alerts or [],
            user_context=user_context,
            timestamp=time.time(),
            monitor_count=monitor_count,
        )
