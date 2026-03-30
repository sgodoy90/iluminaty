"""
ILUMINATY - Capa 6: Action Cascade Resolver
=============================================
Resuelve una accion usando cascada inteligente:

1. API Directa (VS Code command, etc) ........ <10ms
2. Keyboard (hotkey, type) ................... ~50ms
3. UI Tree (buscar elemento, click) .......... ~100ms
4. Vision/OCR (screenshot + OCR + click) ..... ~500ms

Siempre intenta el metodo mas rapido primero.
Si falla, cae al siguiente. Si todos fallan, reporta error.
"""

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class ResolutionResult:
    """Resultado de resolver una accion."""
    action: str
    method_used: str  # "api", "keyboard", "ui_tree", "vision"
    success: bool
    message: str
    attempts: list[dict]  # [{method, success, error, duration_ms}]
    total_ms: float

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "method_used": self.method_used,
            "success": self.success,
            "message": self.message,
            "attempts": self.attempts,
            "total_ms": round(self.total_ms, 1),
        }


class ActionResolver:
    """
    Resuelve acciones usando cascada de metodos.
    Conecta todas las capas: API > Keyboard > UI Tree > Vision.
    """

    def __init__(self):
        # Capas inyectadas externamente
        self._actions = None      # ActionBridge (Capa 1)
        self._ui_tree = None      # UITree (Capa 2)
        self._vscode = None       # VSCodeBridge (Capa 3)
        self._browser = None      # BrowserBridge (Capa 4)
        self._filesystem = None   # FileSystemSandbox (Capa 5)

        # Mapeo de acciones a estrategias de cascada
        self._strategies: dict[str, list] = {}
        self._register_defaults()

    def set_layers(self, actions=None, ui_tree=None, vscode=None,
                   browser=None, filesystem=None):
        """Conecta las capas inferiores."""
        if actions:
            self._actions = actions
        if ui_tree:
            self._ui_tree = ui_tree
        if vscode:
            self._vscode = vscode
        if browser:
            self._browser = browser
        if filesystem:
            self._filesystem = filesystem

    def _register_defaults(self):
        """Registra estrategias de cascada default."""
        # save_file: VS Code API > Ctrl+S > buscar boton Save > screenshot+OCR
        self._strategies["save_file"] = [
            ("api", self._save_via_vscode),
            ("keyboard", self._save_via_keyboard),
            ("ui_tree", self._save_via_ui_tree),
        ]
        self._strategies["open_file"] = [
            ("api", self._open_via_vscode),
            ("keyboard", self._open_via_keyboard),
        ]
        self._strategies["copy"] = [
            ("keyboard", self._copy_via_keyboard),
        ]
        self._strategies["paste"] = [
            ("keyboard", self._paste_via_keyboard),
        ]
        self._strategies["undo"] = [
            ("keyboard", self._undo_via_keyboard),
        ]
        self._strategies["find"] = [
            ("keyboard", self._find_via_keyboard),
        ]
        self._strategies["close_tab"] = [
            ("keyboard", self._close_tab_via_keyboard),
        ]
        self._strategies["new_tab"] = [
            ("api", self._new_tab_via_browser),
            ("keyboard", self._new_tab_via_keyboard),
        ]
        self._strategies["click_element"] = [
            ("ui_tree", self._click_via_ui_tree),
            ("vision", self._click_via_coordinates),
        ]

    def resolve(self, action: str, params: Optional[dict] = None) -> ResolutionResult:
        """
        Resuelve una accion usando cascada.
        Intenta cada metodo en orden hasta que uno funcione.
        """
        params = params or {}
        start = time.time()
        attempts = []

        strategies = self._strategies.get(action, [])
        if not strategies:
            # Accion directa sin cascada
            return self._resolve_direct(action, params, start)

        for method_name, method_fn in strategies:
            attempt_start = time.time()
            try:
                result = method_fn(params)
                elapsed = (time.time() - attempt_start) * 1000
                if result.get("success"):
                    attempts.append({
                        "method": method_name, "success": True,
                        "duration_ms": round(elapsed, 1),
                    })
                    total = (time.time() - start) * 1000
                    return ResolutionResult(
                        action=action, method_used=method_name,
                        success=True, message=result.get("message", "OK"),
                        attempts=attempts, total_ms=total,
                    )
                else:
                    attempts.append({
                        "method": method_name, "success": False,
                        "error": result.get("error", "Failed"),
                        "duration_ms": round(elapsed, 1),
                    })
            except Exception as e:
                elapsed = (time.time() - attempt_start) * 1000
                attempts.append({
                    "method": method_name, "success": False,
                    "error": str(e), "duration_ms": round(elapsed, 1),
                })

        total = (time.time() - start) * 1000
        return ResolutionResult(
            action=action, method_used="none",
            success=False, message=f"All {len(attempts)} methods failed",
            attempts=attempts, total_ms=total,
        )

    def _resolve_direct(self, action: str, params: dict, start: float) -> ResolutionResult:
        """Resuelve acciones que van directo a una capa sin cascada."""
        attempts = []
        # Intentar mapeo directo
        direct_map = {
            "click": lambda p: self._actions.click(p["x"], p["y"], p.get("button", "left")) if self._actions else None,
            "type_text": lambda p: self._actions.type_text(p["text"], p.get("interval", 0.02)) if self._actions else None,
            "hotkey": lambda p: self._actions.hotkey(*p["keys"]) if self._actions else None,
            "scroll": lambda p: self._actions.scroll(p["amount"], p.get("x"), p.get("y")) if self._actions else None,
            "navigate": lambda p: self._browser.navigate(p["url"]) if self._browser else None,
            "read_file": lambda p: self._filesystem.read_file(p["path"]) if self._filesystem else None,
            "write_file": lambda p: self._filesystem.write_file(p["path"], p["content"]) if self._filesystem else None,
        }

        fn = direct_map.get(action)
        if fn:
            try:
                result = fn(params)
                if result is None:
                    result = {"success": False, "error": "Layer not available"}
                elapsed = (time.time() - start) * 1000
                success = result.success if hasattr(result, 'success') else result.get("success", False)
                msg = result.message if hasattr(result, 'message') else result.get("message", str(result))
                return ResolutionResult(
                    action=action, method_used="direct",
                    success=success, message=msg,
                    attempts=[{"method": "direct", "success": success, "duration_ms": round(elapsed, 1)}],
                    total_ms=elapsed,
                )
            except Exception as e:
                elapsed = (time.time() - start) * 1000
                return ResolutionResult(
                    action=action, method_used="direct",
                    success=False, message=str(e),
                    attempts=[{"method": "direct", "success": False, "error": str(e)}],
                    total_ms=elapsed,
                )

        total = (time.time() - start) * 1000
        return ResolutionResult(
            action=action, method_used="none",
            success=False, message=f"Unknown action: {action}",
            attempts=[], total_ms=total,
        )

    # ─── Strategy Implementations ───

    def _save_via_vscode(self, params: dict) -> dict:
        if not self._vscode or not self._vscode.available:
            return {"success": False, "error": "VS Code not available"}
        return self._vscode.execute_command("workbench.action.files.save")

    def _save_via_keyboard(self, params: dict) -> dict:
        if not self._actions or not self._actions.available:
            return {"success": False, "error": "Actions not available"}
        result = self._actions.hotkey("ctrl", "s")
        return {"success": result.success, "message": result.message}

    def _save_via_ui_tree(self, params: dict) -> dict:
        if not self._ui_tree or not self._ui_tree.available:
            return {"success": False, "error": "UI Tree not available"}
        if not self._actions:
            return {"success": False, "error": "Actions not available"}
        element = self._ui_tree.find_element(name="Save", role="button")
        if not element:
            return {"success": False, "error": "Save button not found"}
        cx = element["x"] + element["width"] // 2
        cy = element["y"] + element["height"] // 2
        result = self._actions.click(cx, cy)
        return {"success": result.success, "message": result.message}

    def _open_via_vscode(self, params: dict) -> dict:
        if not self._vscode or not self._vscode.available:
            return {"success": False, "error": "VS Code not available"}
        path = params.get("path", "")
        if path:
            return self._vscode.open_file(path)
        return self._vscode.execute_command("workbench.action.files.openFile")

    def _open_via_keyboard(self, params: dict) -> dict:
        if not self._actions:
            return {"success": False, "error": "Actions not available"}
        result = self._actions.hotkey("ctrl", "o")
        return {"success": result.success}

    def _copy_via_keyboard(self, params: dict) -> dict:
        if not self._actions:
            return {"success": False}
        r = self._actions.hotkey("ctrl", "c")
        return {"success": r.success}

    def _paste_via_keyboard(self, params: dict) -> dict:
        if not self._actions:
            return {"success": False}
        r = self._actions.hotkey("ctrl", "v")
        return {"success": r.success}

    def _undo_via_keyboard(self, params: dict) -> dict:
        if not self._actions:
            return {"success": False}
        r = self._actions.hotkey("ctrl", "z")
        return {"success": r.success}

    def _find_via_keyboard(self, params: dict) -> dict:
        if not self._actions:
            return {"success": False}
        r = self._actions.hotkey("ctrl", "f")
        return {"success": r.success}

    def _close_tab_via_keyboard(self, params: dict) -> dict:
        if not self._actions:
            return {"success": False}
        r = self._actions.hotkey("ctrl", "w")
        return {"success": r.success}

    def _new_tab_via_browser(self, params: dict) -> dict:
        if not self._browser or not self._browser.available:
            return {"success": False, "error": "Browser not available"}
        return self._browser.new_tab(params.get("url", "about:blank"))

    def _new_tab_via_keyboard(self, params: dict) -> dict:
        if not self._actions:
            return {"success": False}
        r = self._actions.hotkey("ctrl", "t")
        return {"success": r.success}

    def _click_via_ui_tree(self, params: dict) -> dict:
        if not self._ui_tree or not self._actions:
            return {"success": False, "error": "UI Tree or Actions not available"}
        name = params.get("name", "")
        role = params.get("role")
        element = self._ui_tree.find_element(name=name, role=role)
        if not element:
            return {"success": False, "error": f"Element '{name}' not found"}
        cx = element["x"] + element["width"] // 2
        cy = element["y"] + element["height"] // 2
        r = self._actions.click(cx, cy)
        return {"success": r.success, "message": f"Clicked '{name}' at ({cx},{cy})"}

    def _click_via_coordinates(self, params: dict) -> dict:
        if not self._actions:
            return {"success": False}
        x, y = params.get("x", 0), params.get("y", 0)
        r = self._actions.click(x, y)
        return {"success": r.success}

    def register_strategy(self, action: str, strategies: list[tuple]):
        """Registra una estrategia personalizada de cascada."""
        self._strategies[action] = strategies

    @property
    def stats(self) -> dict:
        return {
            "registered_actions": len(self._strategies),
            "actions": list(self._strategies.keys()),
            "layers": {
                "actions": self._actions is not None,
                "ui_tree": self._ui_tree is not None,
                "vscode": self._vscode is not None,
                "browser": self._browser is not None,
                "filesystem": self._filesystem is not None,
            },
        }
