"""
ILUMINATY - Capa 6: Error Recovery
====================================
Cuando una accion falla, intenta recuperarse automaticamente.

Estrategias:
1. Retry: reintentar la misma accion
2. Alternative: intentar metodo alternativo (cascada)
3. Rollback: deshacer cambios parciales
4. Escalate: reportar al usuario

"Click fallo" → retry 1x → UI Tree fallback → reportar
"Write fallo" → check permisos → retry → reportar
"""

import time
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class RecoveryStrategy(str, Enum):
    RETRY = "retry"
    ALTERNATIVE = "alternative"
    ROLLBACK = "rollback"
    ESCALATE = "escalate"


@dataclass
class RecoveryAttempt:
    """Un intento de recovery."""
    strategy: RecoveryStrategy
    action: str
    success: bool
    message: str
    duration_ms: float

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy.value,
            "action": self.action,
            "success": self.success,
            "message": self.message,
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass
class RecoveryResult:
    """Resultado completo de un proceso de recovery."""
    original_action: str
    original_error: str
    recovered: bool
    attempts: list[RecoveryAttempt]
    final_message: str

    def to_dict(self) -> dict:
        return {
            "original_action": self.original_action,
            "original_error": self.original_error,
            "recovered": self.recovered,
            "attempts": [a.to_dict() for a in self.attempts],
            "final_message": self.final_message,
        }


class ErrorRecovery:
    """
    Sistema de recuperacion automatica ante errores.
    Cada tipo de error tiene una cadena de recovery strategies.
    """

    def __init__(self, max_retries: int = 2):
        self._max_retries = max_retries
        self._resolver = None  # ActionResolver (Capa 6)
        self._recovery_chains: dict[str, list[RecoveryStrategy]] = {}
        self._stats_recovered = 0
        self._stats_escalated = 0
        self._register_defaults()

    def set_resolver(self, resolver):
        """Conecta el ActionResolver para retries y alternatives."""
        self._resolver = resolver

    def _register_defaults(self):
        """Cadenas de recovery por defecto."""
        # Acciones de mouse: retry → alternative → escalate
        for action in ["click", "double_click", "click_element", "drag_drop"]:
            self._recovery_chains[action] = [
                RecoveryStrategy.RETRY,
                RecoveryStrategy.ALTERNATIVE,
                RecoveryStrategy.ESCALATE,
            ]

        # Acciones de teclado: retry → escalate
        for action in ["type_text", "hotkey", "press_key"]:
            self._recovery_chains[action] = [
                RecoveryStrategy.RETRY,
                RecoveryStrategy.ESCALATE,
            ]

        # File operations: retry → escalate
        for action in ["write_file", "read_file", "delete_file"]:
            self._recovery_chains[action] = [
                RecoveryStrategy.RETRY,
                RecoveryStrategy.ESCALATE,
            ]

        # Navigation: retry → alternative → escalate
        self._recovery_chains["navigate"] = [
            RecoveryStrategy.RETRY,
            RecoveryStrategy.ALTERNATIVE,
            RecoveryStrategy.ESCALATE,
        ]

        # Save: retry → alternative → escalate
        self._recovery_chains["save_file"] = [
            RecoveryStrategy.RETRY,
            RecoveryStrategy.ALTERNATIVE,
            RecoveryStrategy.ESCALATE,
        ]

        # Git: retry → escalate (no alternative for destructive)
        for action in ["git_commit", "git_push", "git_pull"]:
            self._recovery_chains[action] = [
                RecoveryStrategy.RETRY,
                RecoveryStrategy.ESCALATE,
            ]

    def recover(self, action: str, params: dict, error: str) -> RecoveryResult:
        """Intenta recuperarse de un error en una accion."""
        chain = self._recovery_chains.get(action, [RecoveryStrategy.ESCALATE])
        attempts = []

        for strategy in chain:
            start = time.time()

            if strategy == RecoveryStrategy.RETRY:
                attempt = self._do_retry(action, params)
            elif strategy == RecoveryStrategy.ALTERNATIVE:
                attempt = self._do_alternative(action, params)
            elif strategy == RecoveryStrategy.ROLLBACK:
                attempt = self._do_rollback(action, params)
            elif strategy == RecoveryStrategy.ESCALATE:
                attempt = RecoveryAttempt(
                    strategy=RecoveryStrategy.ESCALATE,
                    action=action, success=False,
                    message=f"Escalating to user: {error}",
                    duration_ms=0,
                )

            attempt.duration_ms = (time.time() - start) * 1000
            attempts.append(attempt)

            if attempt.success:
                self._stats_recovered += 1
                return RecoveryResult(
                    original_action=action,
                    original_error=error,
                    recovered=True,
                    attempts=attempts,
                    final_message=f"Recovered via {strategy.value}: {attempt.message}",
                )

        self._stats_escalated += 1
        return RecoveryResult(
            original_action=action,
            original_error=error,
            recovered=False,
            attempts=attempts,
            final_message=f"All recovery strategies failed for '{action}': {error}",
        )

    def _do_retry(self, action: str, params: dict) -> RecoveryAttempt:
        """Reintenta la misma accion via resolver."""
        if not self._resolver:
            return RecoveryAttempt(
                RecoveryStrategy.RETRY, action, False,
                "No resolver available for retry", 0,
            )
        for i in range(self._max_retries):
            time.sleep(0.2 * (i + 1))  # Backoff
            result = self._resolver.resolve(action, params)
            if result.success:
                return RecoveryAttempt(
                    RecoveryStrategy.RETRY, action, True,
                    f"Retry #{i + 1} succeeded", 0,
                )
        return RecoveryAttempt(
            RecoveryStrategy.RETRY, action, False,
            f"All {self._max_retries} retries failed", 0,
        )

    def _do_alternative(self, action: str, params: dict) -> RecoveryAttempt:
        """Intenta un metodo alternativo via resolver cascade."""
        if not self._resolver:
            return RecoveryAttempt(
                RecoveryStrategy.ALTERNATIVE, action, False,
                "No resolver available", 0,
            )
        # El resolver ya maneja cascada internamente
        result = self._resolver.resolve(action, params)
        if result.success:
            return RecoveryAttempt(
                RecoveryStrategy.ALTERNATIVE, action, True,
                f"Alternative method worked: {result.method_used}", 0,
            )
        return RecoveryAttempt(
            RecoveryStrategy.ALTERNATIVE, action, False,
            "No alternative methods succeeded", 0,
        )

    def _do_rollback(self, action: str, params: dict) -> RecoveryAttempt:
        """Intenta deshacer cambios parciales."""
        # Rollback basico: Ctrl+Z para acciones de edicion
        if self._resolver and action in ("write_file", "type_text", "fill_form"):
            result = self._resolver.resolve("undo", {})
            if result.success:
                return RecoveryAttempt(
                    RecoveryStrategy.ROLLBACK, action, True,
                    "Rolled back via undo", 0,
                )
        return RecoveryAttempt(
            RecoveryStrategy.ROLLBACK, action, False,
            "Rollback not available for this action", 0,
        )

    @property
    def stats(self) -> dict:
        return {
            "max_retries": self._max_retries,
            "recovered_count": self._stats_recovered,
            "escalated_count": self._stats_escalated,
            "recovery_rate": round(
                self._stats_recovered / max(self._stats_recovered + self._stats_escalated, 1) * 100, 1),
            "registered_chains": len(self._recovery_chains),
        }
