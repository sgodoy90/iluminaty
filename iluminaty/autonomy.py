"""
ILUMINATY - Capa 7: Autonomy Levels
=====================================
3 niveles de autonomia para controlar cuanto poder tiene la IA.

SUGGEST: La IA solo sugiere acciones. No toca nada.
CONFIRM: La IA planifica y pide permiso antes de actuar.
AUTO:    La IA actua sola. Solo pide permiso para destructivas.

El usuario elige. Cada app puede tener su propio nivel.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AutonomyLevel(Enum):
    SUGGEST = "suggest"    # Solo sugiere, no actua
    CONFIRM = "confirm"    # Pide permiso para cada accion
    AUTO = "auto"          # Actua solo, confirma destructivas


class ActionCategory(Enum):
    SAFE = "safe"                # move_mouse, scroll, read, get_position
    NORMAL = "normal"            # click, type, hotkey, navigate
    DESTRUCTIVE = "destructive"  # delete_file, kill_process, send_email, format
    SYSTEM = "system"            # shutdown, reboot, install, uninstall


# Que categorias se ejecutan automaticamente en cada nivel
AUTO_EXECUTE = {
    AutonomyLevel.SUGGEST: set(),  # Nada
    AutonomyLevel.CONFIRM: set(),  # Nada (todo requiere confirm)
    AutonomyLevel.AUTO: {ActionCategory.SAFE, ActionCategory.NORMAL},  # Safe + Normal auto
}

# Acciones que SIEMPRE requieren confirmacion, sin importar el nivel
ALWAYS_CONFIRM = {
    "delete_file", "kill_process", "send_email", "send_message",
    "format_disk", "shutdown", "reboot", "uninstall",
    "git_push", "git_force_push", "drop_database",
    "overwrite_file", "rm_rf", "close_all_windows",
}


@dataclass
class PendingAction:
    """Una accion esperando aprobacion del usuario."""
    action_id: str
    action_name: str
    params: dict
    category: ActionCategory
    description: str
    created_at: float = 0.0
    expires_at: float = 0.0  # Timeout de 60s por defecto
    approved: Optional[bool] = None
    resolved_at: float = 0.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.expires_at:
            self.expires_at = self.created_at + 60.0  # 60s timeout

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at and self.approved is None

    @property
    def status(self) -> str:
        if self.approved is True:
            return "approved"
        if self.approved is False:
            return "rejected"
        if self.expired:
            return "expired"
        return "pending"

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "action_name": self.action_name,
            "params": self.params,
            "category": self.category.value,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


@dataclass
class AppOverride:
    """Override de autonomia para una app especifica."""
    app_name: str
    level: AutonomyLevel
    allowed_actions: Optional[set[str]] = None  # None = todas permitidas
    blocked_actions: Optional[set[str]] = None   # None = ninguna bloqueada


class AutonomyManager:
    """
    Gestiona los niveles de autonomia del agente.

    Decide si una accion se ejecuta automaticamente,
    requiere confirmacion, o solo se sugiere.
    """

    def __init__(self, default_level: AutonomyLevel = AutonomyLevel.SUGGEST):
        self.default_level = default_level
        self._app_overrides: dict[str, AppOverride] = {}
        self._pending: dict[str, PendingAction] = {}
        self._history: list[dict] = []
        self._action_counter: int = 0

    def set_level(self, level: AutonomyLevel):
        """Cambia el nivel de autonomia global."""
        self.default_level = level

    def set_app_override(self, app_name: str, level: AutonomyLevel,
                         allowed: Optional[set[str]] = None,
                         blocked: Optional[set[str]] = None):
        """Override de nivel para una app especifica."""
        self._app_overrides[app_name.lower()] = AppOverride(
            app_name=app_name.lower(),
            level=level,
            allowed_actions=allowed,
            blocked_actions=blocked,
        )

    def remove_app_override(self, app_name: str):
        self._app_overrides.pop(app_name.lower(), None)

    def get_level(self, app_name: Optional[str] = None) -> AutonomyLevel:
        """Nivel efectivo para un contexto dado."""
        if app_name:
            override = self._app_overrides.get(app_name.lower())
            if override:
                return override.level
        return self.default_level

    def should_execute(self, action_name: str, category: ActionCategory,
                       app_name: Optional[str] = None) -> str:
        """
        Decide que hacer con una accion.

        Returns:
            "execute"  - Ejecutar automaticamente
            "confirm"  - Requiere confirmacion del usuario
            "suggest"  - Solo sugerir, no ejecutar
            "blocked"  - Accion bloqueada por whitelist/blacklist
        """
        level = self.get_level(app_name)

        # Check app-level blocks
        if app_name:
            override = self._app_overrides.get(app_name.lower())
            if override:
                if override.blocked_actions and action_name in override.blocked_actions:
                    return "blocked"
                if override.allowed_actions and action_name not in override.allowed_actions:
                    return "blocked"

        # ALWAYS_CONFIRM acciones siempre piden permiso
        if action_name in ALWAYS_CONFIRM:
            if level == AutonomyLevel.SUGGEST:
                return "suggest"
            return "confirm"

        # Nivel SUGGEST: nunca ejecuta
        if level == AutonomyLevel.SUGGEST:
            return "suggest"

        # Nivel CONFIRM: todo requiere confirmacion
        if level == AutonomyLevel.CONFIRM:
            return "confirm"

        # Nivel AUTO: safe + normal auto, destructive/system confirm
        if level == AutonomyLevel.AUTO:
            if category in AUTO_EXECUTE[AutonomyLevel.AUTO]:
                return "execute"
            return "confirm"

        return "suggest"

    def request_confirmation(self, action_name: str, params: dict,
                             category: ActionCategory,
                             description: str) -> PendingAction:
        """Crea una accion pendiente esperando aprobacion."""
        self._action_counter += 1
        action_id = f"action_{self._action_counter}_{int(time.time())}"

        pending = PendingAction(
            action_id=action_id,
            action_name=action_name,
            params=params,
            category=category,
            description=description,
        )
        self._pending[action_id] = pending
        return pending

    def approve(self, action_id: str) -> bool:
        """Aprueba una accion pendiente."""
        pending = self._pending.get(action_id)
        if not pending or pending.expired:
            return False
        pending.approved = True
        pending.resolved_at = time.time()
        self._history.append({"action_id": action_id, "decision": "approved", "time": time.time()})
        return True

    def reject(self, action_id: str) -> bool:
        """Rechaza una accion pendiente."""
        pending = self._pending.get(action_id)
        if not pending or pending.expired:
            return False
        pending.approved = False
        pending.resolved_at = time.time()
        self._history.append({"action_id": action_id, "decision": "rejected", "time": time.time()})
        return True

    def get_pending(self) -> list[dict]:
        """Lista acciones pendientes de aprobacion."""
        self._cleanup_expired()
        return [p.to_dict() for p in self._pending.values() if p.approved is None and not p.expired]

    def _cleanup_expired(self):
        """Limpia acciones expiradas."""
        expired = [k for k, v in self._pending.items() if v.expired]
        for k in expired:
            self._history.append({"action_id": k, "decision": "expired", "time": time.time()})
            del self._pending[k]

    @property
    def stats(self) -> dict:
        self._cleanup_expired()
        return {
            "default_level": self.default_level.value,
            "app_overrides": {k: v.level.value for k, v in self._app_overrides.items()},
            "pending_count": len([p for p in self._pending.values() if p.approved is None]),
            "total_decisions": len(self._history),
        }
