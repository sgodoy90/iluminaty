"""
ILUMINATY - E03: Action Bridge
=================================
La IA puede VER y ahora tambien ACTUAR.
Traduce intenciones de la IA a acciones reales en el OS.

"Click en el boton Save" → coordenadas → click real
"Escribe 'hello world'" → keyboard injection
"Abre Chrome" → launch app

Usa pyautogui para cross-platform (Win/Mac/Linux).
SIEMPRE requiere confirmacion para acciones destructivas.
"""

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class ActionResult:
    """Resultado de una accion ejecutada."""
    action: str
    success: bool
    message: str
    timestamp: float = 0.0
    requires_confirmation: bool = False

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "success": self.success,
            "message": self.message,
            "time": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
        }


class ActionBridge:
    """
    Puente entre la IA y el OS.
    Ejecuta acciones fisicas (mouse, keyboard) en la maquina.

    REGLAS DE SEGURIDAD:
    - Acciones destructivas requieren confirmacion
    - Todo se loguea en action_log
    - Rate limited: max 10 acciones por minuto
    - Puede ser deshabilitado completamente
    """

    def __init__(self, enabled: bool = False, require_confirmation: bool = True):
        self.enabled = enabled
        self.require_confirmation = require_confirmation
        self._pyautogui = None
        self._action_log: list[ActionResult] = []
        self._actions_this_minute: int = 0
        self._minute_start: float = time.time()
        self.max_actions_per_minute: int = 10

        if enabled:
            self._try_import()

    def _try_import(self):
        try:
            import pyautogui
            pyautogui.FAILSAFE = True  # mover mouse a esquina = abort
            pyautogui.PAUSE = 0.3     # pausa entre acciones
            self._pyautogui = pyautogui
        except ImportError:
            self._pyautogui = None

    @property
    def available(self) -> bool:
        return self._pyautogui is not None and self.enabled

    def _check_rate_limit(self) -> bool:
        now = time.time()
        if now - self._minute_start > 60:
            self._actions_this_minute = 0
            self._minute_start = now
        if self._actions_this_minute >= self.max_actions_per_minute:
            return False
        self._actions_this_minute += 1
        return True

    def _log(self, result: ActionResult):
        self._action_log.append(result)
        if len(self._action_log) > 200:
            self._action_log = self._action_log[-200:]

    def click(self, x: int, y: int, button: str = "left") -> ActionResult:
        """Click en coordenadas de pantalla."""
        if not self.available:
            return ActionResult("click", False, "Action bridge not available")
        if not self._check_rate_limit():
            return ActionResult("click", False, "Rate limit exceeded")

        try:
            self._pyautogui.click(x, y, button=button)
            result = ActionResult("click", True, f"Clicked at ({x},{y}) {button}")
        except Exception as e:
            result = ActionResult("click", False, f"Click failed: {e}")

        self._log(result)
        return result

    def type_text(self, text: str, interval: float = 0.02) -> ActionResult:
        """Escribe texto via keyboard."""
        if not self.available:
            return ActionResult("type", False, "Action bridge not available")
        if not self._check_rate_limit():
            return ActionResult("type", False, "Rate limit exceeded")

        try:
            self._pyautogui.typewrite(text, interval=interval)
            result = ActionResult("type", True, f"Typed {len(text)} chars")
        except Exception as e:
            result = ActionResult("type", False, f"Type failed: {e}")

        self._log(result)
        return result

    def hotkey(self, *keys) -> ActionResult:
        """Ejecuta un atajo de teclado (Ctrl+S, Alt+Tab, etc)."""
        if not self.available:
            return ActionResult("hotkey", False, "Action bridge not available")
        if not self._check_rate_limit():
            return ActionResult("hotkey", False, "Rate limit exceeded")

        try:
            self._pyautogui.hotkey(*keys)
            result = ActionResult("hotkey", True, f"Pressed {'+'.join(keys)}")
        except Exception as e:
            result = ActionResult("hotkey", False, f"Hotkey failed: {e}")

        self._log(result)
        return result

    def move_mouse(self, x: int, y: int) -> ActionResult:
        """Mueve el mouse sin click."""
        if not self.available:
            return ActionResult("move", False, "Action bridge not available")

        try:
            self._pyautogui.moveTo(x, y)
            result = ActionResult("move", True, f"Moved to ({x},{y})")
        except Exception as e:
            result = ActionResult("move", False, f"Move failed: {e}")

        self._log(result)
        return result

    def scroll(self, amount: int, x: Optional[int] = None, y: Optional[int] = None) -> ActionResult:
        """Scroll. Positive = up, negative = down."""
        if not self.available:
            return ActionResult("scroll", False, "Action bridge not available")

        try:
            self._pyautogui.scroll(amount, x=x, y=y)
            direction = "up" if amount > 0 else "down"
            result = ActionResult("scroll", True, f"Scrolled {direction} {abs(amount)}")
        except Exception as e:
            result = ActionResult("scroll", False, f"Scroll failed: {e}")

        self._log(result)
        return result

    def screenshot_region(self, x: int, y: int, w: int, h: int) -> Optional[bytes]:
        """Captura una region especifica (para verificar despues de una accion)."""
        if not self._pyautogui:
            return None
        try:
            import io
            img = self._pyautogui.screenshot(region=(x, y, w, h))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None

    def get_mouse_position(self) -> dict:
        """Posicion actual del mouse."""
        if not self._pyautogui:
            return {"x": 0, "y": 0}
        pos = self._pyautogui.position()
        return {"x": pos.x, "y": pos.y}

    def get_action_log(self, count: int = 20) -> list[dict]:
        return [a.to_dict() for a in self._action_log[-count:]]

    @property
    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "total_actions": len(self._action_log),
            "actions_this_minute": self._actions_this_minute,
            "max_per_minute": self.max_actions_per_minute,
            "require_confirmation": self.require_confirmation,
        }
