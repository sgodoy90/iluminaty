"""
ILUMINATY - Capa 1: Action Bridge
===================================
La IA puede VER y ahora tambien ACTUAR.
Traduce intenciones de la IA a acciones reales en el OS.

"Click en el boton Save" → coordenadas → click real
"Escribe 'hello world'" → keyboard injection
"Drag and drop" → arrastrar elementos
"Doble click en icono" → abrir archivos

Usa pyautogui para cross-platform (Win/Mac/Linux).
Integra con SafetySystem (Capa 7) para rate limiting y permisos.
"""

import time
import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Resultado de una accion ejecutada."""
    action: str
    success: bool
    message: str
    timestamp: float = 0.0
    duration_ms: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "success": self.success,
            "message": self.message,
            "duration_ms": round(self.duration_ms, 1),
            "time": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
        }


class ActionBridge:
    """
    Puente entre la IA y el OS.
    Ejecuta acciones fisicas (mouse, keyboard, drag) en la maquina.

    v1.0: 15 acciones (click, double_click, right_click, type_text, hotkey,
    move_mouse, scroll, drag_drop, press_key, release_key, screenshot_region,
    get_mouse_position, click_element, type_in_field, select_option)

    Integra con:
    - SafetySystem (Capa 7) para permisos y rate limiting
    - UI Tree (Capa 2, cuando disponible) para click_element/type_in_field
    """

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self._pyautogui = None
        self._action_log: deque[ActionResult] = deque(maxlen=500)
        self._ui_tree = None  # Set externally when ui_tree.py is available
        self._held_keys: set[str] = set()

        if enabled:
            self._try_import()

    def _try_import(self):
        try:
            import pyautogui
            pyautogui.FAILSAFE = True   # mover mouse a esquina = abort
            pyautogui.PAUSE = 0.15      # pausa entre acciones (reducido de 0.3 para mas fluidez)
            self._pyautogui = pyautogui
        except ImportError:
            self._pyautogui = None

    def set_ui_tree(self, ui_tree):
        """Hook para conectar UI Tree (Capa 2) cuando este disponible."""
        self._ui_tree = ui_tree

    @property
    def available(self) -> bool:
        return self._pyautogui is not None and self.enabled

    def enable(self):
        """Habilita el action bridge."""
        self.enabled = True
        if not self._pyautogui:
            self._try_import()

    def disable(self):
        """Deshabilita el action bridge y libera teclas held."""
        self.enabled = False
        self._release_all_keys()

    def _log(self, result: ActionResult):
        self._action_log.append(result)

    def _exec(self, action_name: str, fn, *args, **kwargs) -> ActionResult:
        """Wrapper comun: valida disponibilidad, mide tiempo, loguea."""
        if not self.available:
            return ActionResult(action_name, False, "Action bridge not available")
        start = time.time()
        try:
            msg = fn(*args, **kwargs)
            elapsed = (time.time() - start) * 1000
            result = ActionResult(action_name, True, msg, duration_ms=elapsed)
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            result = ActionResult(action_name, False, f"{action_name} failed: {e}", duration_ms=elapsed)
        self._log(result)
        return result

    # ─── Mouse Actions ───

    def click(self, x: int, y: int, button: str = "left") -> ActionResult:
        """Click en coordenadas de pantalla."""
        def do():
            self._pyautogui.click(x, y, button=button)
            return f"Clicked at ({x},{y}) {button}"
        return self._exec("click", do)

    def double_click(self, x: int, y: int, button: str = "left") -> ActionResult:
        """Doble click en coordenadas."""
        def do():
            self._pyautogui.doubleClick(x, y, button=button)
            return f"Double-clicked at ({x},{y}) {button}"
        return self._exec("double_click", do)

    def right_click(self, x: int, y: int) -> ActionResult:
        """Click derecho (context menu)."""
        def do():
            self._pyautogui.rightClick(x, y)
            return f"Right-clicked at ({x},{y})"
        return self._exec("right_click", do)

    def move_mouse(self, x: int, y: int, duration: float = 0.0) -> ActionResult:
        """Mueve el mouse. duration=0 es instantaneo."""
        def do():
            self._pyautogui.moveTo(x, y, duration=duration)
            return f"Moved to ({x},{y})"
        return self._exec("move_mouse", do)

    def drag_drop(self, start_x: int, start_y: int, end_x: int, end_y: int,
                  duration: float = 0.5, button: str = "left") -> ActionResult:
        """Drag and drop de un punto a otro."""
        def do():
            self._pyautogui.moveTo(start_x, start_y)
            self._pyautogui.mouseDown(button=button)
            self._pyautogui.moveTo(end_x, end_y, duration=duration)
            self._pyautogui.mouseUp(button=button)
            return f"Dragged ({start_x},{start_y}) → ({end_x},{end_y})"
        return self._exec("drag_drop", do)

    def scroll(self, amount: int, x: Optional[int] = None, y: Optional[int] = None) -> ActionResult:
        """Scroll. Positive = up, negative = down."""
        def do():
            self._pyautogui.scroll(amount, x=x, y=y)
            direction = "up" if amount > 0 else "down"
            return f"Scrolled {direction} {abs(amount)}"
        return self._exec("scroll", do)

    # ─── Keyboard Actions ───

    def type_text(self, text: str, interval: float = 0.02) -> ActionResult:
        """Escribe texto via keyboard.
        Supports full Unicode via clipboard paste fallback:
        - ASCII-only text → pyautogui.write() (respects per-char interval)
        - Text with non-ASCII chars → copy to clipboard + Ctrl+V (instant, lossless)
        """
        def do():
            is_ascii = all(ord(c) < 128 for c in text)
            if is_ascii:
                self._pyautogui.write(text, interval=interval)
                return f"Typed {len(text)} chars (keyboard)"
            # Unicode fallback: clipboard paste
            try:
                import pyperclip
                pyperclip.copy(text)
                self._pyautogui.hotkey("ctrl", "v")
                return f"Typed {len(text)} chars (clipboard paste — unicode)"
            except ImportError:
                pass  # noqa: suppressed ImportError
            # Last resort: type char by char with pyautogui (drops non-ASCII silently)
            self._pyautogui.write(text, interval=interval)
            return f"Typed {len(text)} chars (best-effort — install pyperclip for full unicode)"
        return self._exec("type_text", do)

    def hotkey(self, *keys) -> ActionResult:
        """Ejecuta un atajo de teclado (Ctrl+S, Alt+Tab, etc)."""
        def do():
            self._pyautogui.hotkey(*keys)
            return f"Pressed {'+'.join(keys)}"
        return self._exec("hotkey", do)

    def press_key(self, key: str) -> ActionResult:
        """Presiona y suelta una tecla individual (enter, tab, escape, etc)."""
        def do():
            self._pyautogui.press(key)
            return f"Pressed {key}"
        return self._exec("press_key", do)

    def hold_key(self, key: str) -> ActionResult:
        """Mantiene una tecla presionada (para combinaciones manuales)."""
        def do():
            self._pyautogui.keyDown(key)
            self._held_keys.add(key)
            return f"Holding {key}"
        return self._exec("hold_key", do)

    def release_key(self, key: str) -> ActionResult:
        """Suelta una tecla que estaba held."""
        def do():
            self._pyautogui.keyUp(key)
            self._held_keys.discard(key)
            return f"Released {key}"
        return self._exec("release_key", do)

    def _release_all_keys(self):
        """Suelta todas las teclas held (safety cleanup)."""
        if self._pyautogui:
            for key in list(self._held_keys):
                try:
                    self._pyautogui.keyUp(key)
                except Exception as e:
                    logger.debug("Failed to release held key '%s': %s", key, e)
            self._held_keys.clear()

    # ─── UI Tree Integration (Capa 2 hooks) ───

    def click_element(self, name: str, role: Optional[str] = None) -> ActionResult:
        """Click en un elemento UI por nombre/rol (requiere UI Tree)."""
        if not self._ui_tree:
            return ActionResult("click_element", False,
                                "UI Tree not available — use click(x, y) with coordinates")
        def do():
            element = self._ui_tree.find_element(name=name, role=role)
            if not element:
                raise ValueError(f"Element not found: {name}")
            cx = element["x"] + element["width"] // 2
            cy = element["y"] + element["height"] // 2
            self._pyautogui.click(cx, cy)
            return f"Clicked element '{name}' at ({cx},{cy})"
        return self._exec("click_element", do)

    def type_in_field(self, field_name: str, text: str, clear_first: bool = True) -> ActionResult:
        """Escribe en un campo UI por nombre (requiere UI Tree)."""
        if not self._ui_tree:
            return ActionResult("type_in_field", False,
                                "UI Tree not available — use click(x,y) then type_text()")
        def do():
            element = self._ui_tree.find_element(name=field_name, role="textfield")
            if not element:
                raise ValueError(f"Field not found: {field_name}")
            cx = element["x"] + element["width"] // 2
            cy = element["y"] + element["height"] // 2
            self._pyautogui.click(cx, cy)
            if clear_first:
                self._pyautogui.hotkey("ctrl", "a")
                time.sleep(0.05)
            self._pyautogui.write(text, interval=0.02)
            return f"Typed '{text[:30]}...' in field '{field_name}'"
        return self._exec("type_in_field", do)

    def select_option(self, element_name: str, option: str) -> ActionResult:
        """Selecciona una opcion en un dropdown/combobox (requiere UI Tree)."""
        if not self._ui_tree:
            return ActionResult("select_option", False,
                                "UI Tree not available")
        def do():
            element = self._ui_tree.find_element(name=element_name, role="combobox")
            if not element:
                raise ValueError(f"Dropdown not found: {element_name}")
            cx = element["x"] + element["width"] // 2
            cy = element["y"] + element["height"] // 2
            self._pyautogui.click(cx, cy)
            time.sleep(0.2)
            # Type option name to filter, then Enter
            self._pyautogui.write(option, interval=0.03)
            time.sleep(0.1)
            self._pyautogui.press("enter")
            return f"Selected '{option}' in '{element_name}'"
        return self._exec("select_option", do)

    # ─── Utility ───

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
        items = list(self._action_log)[-count:]
        return [a.to_dict() for a in items]

    @property
    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "total_actions": len(self._action_log),
            "held_keys": list(self._held_keys),
            "ui_tree_connected": self._ui_tree is not None,
            "actions_available": [
                "click", "double_click", "right_click", "move_mouse",
                "drag_drop", "scroll", "type_text", "hotkey", "press_key",
                "hold_key", "release_key", "click_element", "type_in_field",
                "select_option", "screenshot_region", "get_mouse_position",
            ],
        }
