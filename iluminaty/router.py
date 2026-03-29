"""
ILUMINATY - F16: AI Model Router
===================================
Auto-selecciona el modelo mas barato que pueda responder la pregunta.
Ahorra 60-80% en costos de API.

Logica:
  "Que color es el boton?"     → Solo OCR text (gratis)
  "Que dice el error?"         → OCR text (gratis)
  "Que bug visual ves?"        → Vision API ($$$)
  "Resume los ultimos 5 min"   → Context engine (gratis)
  "Vigila errores"             → Watchdog (gratis)
  "Que estoy haciendo?"        → Context state (gratis)

El router analiza el prompt y decide:
  1. Puede responderse sin IA? (OCR, context, watchdog) → FREE
  2. Necesita vision? → Envia frame al modelo mas barato
  3. Necesita razonamiento complejo? → Modelo premium
"""

import re
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class RouteDecision:
    """Decision del router."""
    route: str            # "local", "vision_cheap", "vision_premium"
    reason: str
    estimated_cost: float  # USD estimado
    model_suggestion: str
    needs_image: bool
    needs_audio: bool


# Patrones para detectar que tipo de pregunta es
LOCAL_PATTERNS = [
    (r"(?i)(what|which|que).*(app|window|program|aplicacion)", "context"),
    (r"(?i)(what|que).*(doing|haciendo|workflow)", "context"),
    (r"(?i)(read|lee|text|texto|dice|says?|error|warning)", "ocr"),
    (r"(?i)(what|que).*(time|hora|fecha|date)", "context"),
    (r"(?i)(monitor|screen|pantalla).*(info|status)", "system"),
    (r"(?i)(watch|vigila|alert|alerta|detect)", "watchdog"),
    (r"(?i)(history|historial|timeline|last \d+ min)", "memory"),
    (r"(?i)(focus|enfoque|distract|concentr)", "context"),
    (r"(?i)(how long|cuanto tiempo|duration)", "context"),
    (r"(?i)(summary|resumen|summarize|resume)", "context"),
]

VISION_PATTERNS = [
    (r"(?i)(see|look|mira|ve|show|muestra|visual|color|layout|design|UI|button|icon)", "vision"),
    (r"(?i)(bug|glitch|broken|roto|misalign|desfas)", "vision"),
    (r"(?i)(screenshot|screen|pantalla|image|imagen)", "vision"),
    (r"(?i)(compare|compara|difference|diferencia|change|cambio)", "vision"),
    (r"(?i)(where|donde|position|posicion|location)", "vision_spatial"),
    (r"(?i)(click|press|tap|drag|hover)", "vision_action"),
]

AUDIO_PATTERNS = [
    (r"(?i)(said|dijo|hear|escuch|speak|habla|voice|voz|call|llama)", "audio"),
    (r"(?i)(meeting|reunion|conversation|conversacion)", "audio"),
    (r"(?i)(transcri|transcript)", "audio"),
]

# Costo estimado por modelo (USD per call)
MODEL_COSTS = {
    "local": 0.0,
    "gpt-4o-mini": 0.002,
    "gemini-flash": 0.001,
    "gpt-4o": 0.01,
    "claude-sonnet": 0.008,
    "gemini-pro": 0.005,
    "claude-opus": 0.03,
    "gpt-4-turbo": 0.02,
}


class AIRouter:
    """
    Router inteligente que decide como responder cada pregunta
    al menor costo posible.
    """

    def __init__(self):
        self._total_cost: float = 0.0
        self._total_routed: int = 0
        self._saved_by_local: int = 0
        self._route_history: list[dict] = []

    def route(self, prompt: str) -> RouteDecision:
        """
        Analiza el prompt y decide la ruta optima.
        """
        self._total_routed += 1
        prompt_lower = prompt.lower()

        # Check if can be answered locally (FREE)
        for pattern, source in LOCAL_PATTERNS:
            if re.search(pattern, prompt):
                self._saved_by_local += 1
                decision = RouteDecision(
                    route="local",
                    reason=f"Can be answered from {source} (no AI needed)",
                    estimated_cost=0.0,
                    model_suggestion="local",
                    needs_image=False,
                    needs_audio=False,
                )
                self._log(prompt, decision)
                return decision

        # Check if needs audio
        needs_audio = any(re.search(p, prompt) for p, _ in AUDIO_PATTERNS)

        # Check if needs vision
        needs_vision = any(re.search(p, prompt) for p, _ in VISION_PATTERNS)

        if not needs_vision and not needs_audio:
            # Default: try cheap vision model (covers most cases)
            decision = RouteDecision(
                route="vision_cheap",
                reason="General question, using cheapest vision model",
                estimated_cost=MODEL_COSTS["gemini-flash"],
                model_suggestion="gemini-flash",
                needs_image=True,
                needs_audio=needs_audio,
            )
        elif needs_vision and len(prompt) > 100:
            # Complex visual question → premium model
            decision = RouteDecision(
                route="vision_premium",
                reason="Complex visual analysis needed",
                estimated_cost=MODEL_COSTS["gpt-4o"],
                model_suggestion="gpt-4o",
                needs_image=True,
                needs_audio=needs_audio,
            )
        elif needs_audio and not needs_vision:
            # Audio only → cheap model + transcript
            decision = RouteDecision(
                route="vision_cheap",
                reason="Audio question, sending transcript to cheap model",
                estimated_cost=MODEL_COSTS["gpt-4o-mini"],
                model_suggestion="gpt-4o-mini",
                needs_image=False,
                needs_audio=True,
            )
        else:
            # Vision needed
            decision = RouteDecision(
                route="vision_cheap",
                reason="Visual question, using efficient vision model",
                estimated_cost=MODEL_COSTS["gemini-flash"],
                model_suggestion="gemini-flash",
                needs_image=True,
                needs_audio=needs_audio,
            )

        self._total_cost += decision.estimated_cost
        self._log(prompt, decision)
        return decision

    def _log(self, prompt: str, decision: RouteDecision):
        self._route_history.append({
            "time": time.strftime("%H:%M:%S"),
            "prompt": prompt[:50],
            "route": decision.route,
            "model": decision.model_suggestion,
            "cost": decision.estimated_cost,
        })
        if len(self._route_history) > 100:
            self._route_history = self._route_history[-100:]

    @property
    def stats(self) -> dict:
        return {
            "total_routed": self._total_routed,
            "saved_by_local": self._saved_by_local,
            "savings_pct": round(
                (self._saved_by_local / max(self._total_routed, 1)) * 100, 1
            ),
            "total_estimated_cost": round(self._total_cost, 4),
            "avg_cost_per_query": round(
                self._total_cost / max(self._total_routed - self._saved_by_local, 1), 4
            ),
        }

    def get_history(self, count: int = 20) -> list[dict]:
        return self._route_history[-count:]
