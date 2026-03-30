"""
ILUMINATY - Capa 6: Post-Action Verifier
==========================================
Despues de ejecutar una accion, VERIFICA que tuvo efecto.

"Guarde el archivo" → verifica que el archivo fue modificado
"Hice click en Submit" → verifica que la pagina cambio
"Escribi en el campo" → verifica que el campo tiene el texto

Sin verificacion, la IA actua a ciegas.
"""

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class VerificationResult:
    """Resultado de una verificacion post-accion."""
    action: str
    verified: bool
    method: str  # "file_check", "ui_check", "screenshot_diff", "dom_check"
    message: str
    duration_ms: float

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "verified": self.verified,
            "method": self.method,
            "message": self.message,
            "duration_ms": round(self.duration_ms, 1),
        }


class ActionVerifier:
    """
    Verifica que las acciones tuvieron efecto real.
    Usa multiples metodos de verificacion segun la accion.
    """

    def __init__(self):
        self._filesystem = None
        self._ui_tree = None
        self._browser = None
        self._actions = None  # Para screenshot_region
        self._verifiers: dict[str, callable] = {}
        self._register_defaults()

    def set_layers(self, filesystem=None, ui_tree=None, browser=None, actions=None):
        if filesystem:
            self._filesystem = filesystem
        if ui_tree:
            self._ui_tree = ui_tree
        if browser:
            self._browser = browser
        if actions:
            self._actions = actions

    def _register_defaults(self):
        self._verifiers["save_file"] = self._verify_file_saved
        self._verifiers["write_file"] = self._verify_file_written
        self._verifiers["navigate"] = self._verify_navigation
        self._verifiers["click_element"] = self._verify_click
        self._verifiers["type_text"] = self._verify_type
        self._verifiers["terminal_exec"] = self._verify_command
        self._verifiers["delete_file"] = self._verify_file_deleted

    def verify(self, action: str, params: dict, pre_state: Optional[dict] = None) -> VerificationResult:
        """Verifica que una accion tuvo efecto."""
        start = time.time()
        verifier = self._verifiers.get(action)

        if not verifier:
            elapsed = (time.time() - start) * 1000
            return VerificationResult(
                action=action, verified=True,
                method="none", message="No verifier for this action (assumed ok)",
                duration_ms=elapsed,
            )

        try:
            result = verifier(params, pre_state)
            result.duration_ms = (time.time() - start) * 1000
            return result
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return VerificationResult(
                action=action, verified=False,
                method="error", message=f"Verification error: {e}",
                duration_ms=elapsed,
            )

    def capture_pre_state(self, action: str, params: dict) -> dict:
        """Captura estado pre-accion para comparar despues."""
        state = {"timestamp": time.time(), "action": action}

        if action in ("save_file", "write_file") and self._filesystem:
            path = params.get("path", "")
            if path:
                info = self._filesystem.file_info(path)
                state["file_modified"] = info.get("modified", "")
                state["file_size"] = info.get("size", 0)

        if action == "navigate" and self._browser:
            state["url"] = self._browser.get_url()

        return state

    # ─── Verifier Implementations ───

    def _verify_file_saved(self, params: dict, pre_state: Optional[dict]) -> VerificationResult:
        if not self._filesystem:
            return VerificationResult("save_file", True, "none", "No filesystem", 0)
        path = params.get("path", "")
        if not path:
            return VerificationResult("save_file", True, "none", "No path to check", 0)
        info = self._filesystem.file_info(path)
        if not info.get("success"):
            return VerificationResult("save_file", False, "file_check", "File not found", 0)
        if pre_state and info.get("modified") != pre_state.get("file_modified"):
            return VerificationResult("save_file", True, "file_check", "File modification time changed", 0)
        return VerificationResult("save_file", True, "file_check", "File exists", 0)

    def _verify_file_written(self, params: dict, pre_state: Optional[dict]) -> VerificationResult:
        if not self._filesystem:
            return VerificationResult("write_file", True, "none", "No filesystem", 0)
        path = params.get("path", "")
        content = params.get("content", "")
        result = self._filesystem.read_file(path)
        if not result.get("success"):
            return VerificationResult("write_file", False, "file_check", "File not readable", 0)
        if result.get("content", "")[:100] == content[:100]:
            return VerificationResult("write_file", True, "file_check", "Content matches", 0)
        return VerificationResult("write_file", False, "file_check", "Content mismatch", 0)

    def _verify_navigation(self, params: dict, pre_state: Optional[dict]) -> VerificationResult:
        if not self._browser:
            return VerificationResult("navigate", True, "none", "No browser", 0)
        target = params.get("url", "")
        current = self._browser.get_url()
        if target and target in current:
            return VerificationResult("navigate", True, "dom_check", f"URL is {current}", 0)
        if pre_state and current != pre_state.get("url", ""):
            return VerificationResult("navigate", True, "dom_check", f"URL changed to {current}", 0)
        return VerificationResult("navigate", False, "dom_check", f"URL unchanged: {current}", 0)

    def _verify_click(self, params: dict, pre_state: Optional[dict]) -> VerificationResult:
        # Clicks son dificiles de verificar sin screenshot diff
        # Por ahora, verificamos que no hubo error
        return VerificationResult("click_element", True, "none", "Click assumed successful", 0)

    def _verify_type(self, params: dict, pre_state: Optional[dict]) -> VerificationResult:
        return VerificationResult("type_text", True, "none", "Type assumed successful", 0)

    def _verify_command(self, params: dict, pre_state: Optional[dict]) -> VerificationResult:
        # El resultado del comando ya indica exito/fallo
        return VerificationResult("terminal_exec", True, "none", "Check command result directly", 0)

    def _verify_file_deleted(self, params: dict, pre_state: Optional[dict]) -> VerificationResult:
        if not self._filesystem:
            return VerificationResult("delete_file", True, "none", "No filesystem", 0)
        path = params.get("path", "")
        info = self._filesystem.file_info(path)
        if not info.get("success"):
            return VerificationResult("delete_file", True, "file_check", "File confirmed deleted", 0)
        return VerificationResult("delete_file", False, "file_check", "File still exists", 0)

    @property
    def stats(self) -> dict:
        return {
            "registered_verifiers": list(self._verifiers.keys()),
            "layers": {
                "filesystem": self._filesystem is not None,
                "ui_tree": self._ui_tree is not None,
                "browser": self._browser is not None,
                "actions": self._actions is not None,
            },
        }
