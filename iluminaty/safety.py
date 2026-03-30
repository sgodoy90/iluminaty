"""
ILUMINATY - Capa 7: Safety System
==================================
Kill switch global, whitelist de acciones, rate limiting adaptativo.

ESTA CAPA ES NO NEGOCIABLE. Sin ella, darle "manos" a la IA es peligroso.
Todo el sistema de acciones pasa por aqui antes de ejecutarse.
"""

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# ─── Whitelist default: acciones permitidas por defecto ───
DEFAULT_WHITELIST = {
    # Safe (siempre permitidas)
    "get_mouse_position", "screenshot_region", "get_elements",
    "find_element", "get_clipboard", "list_processes",
    "list_windows", "get_window_info", "read_file",
    "search_files", "git_status", "git_diff", "git_log",

    # Normal (permitidas en CONFIRM y AUTO)
    "click", "double_click", "right_click", "type_text", "hotkey",
    "move_mouse", "scroll", "drag_drop", "press_key", "release_key",
    "click_element", "type_in_field", "select_option",
    "set_clipboard", "focus_window", "minimize_window", "maximize_window",
    "navigate_url", "click_dom", "fill_form",
    "vscode_command", "terminal_exec", "write_file",
    "git_commit", "git_pull", "git_branch",

    # Destructive (siempre requieren confirmacion)
    "close_window", "kill_process", "delete_file",
    "git_push", "send_message",
}

# Acciones NUNCA permitidas (blacklist absoluta)
NEVER_ALLOW = {
    "format_disk", "rm_rf", "shutdown", "reboot",
    "drop_database", "delete_all", "sudo",
    "install_package", "uninstall_package",
    "modify_system_file", "change_password",
    "disable_firewall", "disable_antivirus",
}


@dataclass
class RateLimit:
    """Rate limit por categoria de accion."""
    max_per_minute: int
    max_per_hour: int
    _minute_log: deque = field(default_factory=lambda: deque(maxlen=200))
    _hour_log: deque = field(default_factory=lambda: deque(maxlen=5000))

    def check(self) -> bool:
        """Retorna True si puede ejecutar, False si excede limite."""
        now = time.time()

        # Limpiar entradas viejas
        while self._minute_log and now - self._minute_log[0] > 60:
            self._minute_log.popleft()
        while self._hour_log and now - self._hour_log[0] > 3600:
            self._hour_log.popleft()

        if len(self._minute_log) >= self.max_per_minute:
            return False
        if len(self._hour_log) >= self.max_per_hour:
            return False

        self._minute_log.append(now)
        self._hour_log.append(now)
        return True


class SafetySystem:
    """
    Sistema de seguridad central para todas las acciones del agente.

    Todo pasa por aqui:
    1. Kill switch check
    2. Blacklist check (NEVER_ALLOW)
    3. Whitelist check (solo ejecuta lo permitido)
    4. Rate limit check (por categoria)
    5. Si pasa todo, la accion se ejecuta
    """

    def __init__(self):
        self._killed = False
        self._kill_lock = threading.Lock()

        # Whitelist configurable
        self._whitelist: set[str] = set(DEFAULT_WHITELIST)

        # Rate limits por categoria
        self._rate_limits = {
            "safe": RateLimit(max_per_minute=60, max_per_hour=2000),
            "normal": RateLimit(max_per_minute=20, max_per_hour=500),
            "destructive": RateLimit(max_per_minute=3, max_per_hour=30),
            "system": RateLimit(max_per_minute=1, max_per_hour=5),
        }

        # Callback para kill switch
        self._kill_callbacks: list = []

        # Stats
        self._blocked_count = 0
        self._rate_limited_count = 0
        self._total_checks = 0

    # ─── Kill Switch ───

    def kill(self):
        """DETIENE toda actividad del agente inmediatamente."""
        with self._kill_lock:
            self._killed = True
        for callback in self._kill_callbacks:
            try:
                callback()
            except Exception:
                pass

    def resume(self):
        """Reactiva el agente despues de un kill."""
        with self._kill_lock:
            self._killed = False

    @property
    def is_killed(self) -> bool:
        with self._kill_lock:
            return self._killed

    def on_kill(self, callback):
        """Registra un callback que se ejecuta cuando se activa el kill switch."""
        self._kill_callbacks.append(callback)

    # ─── Whitelist Management ───

    def add_to_whitelist(self, action: str):
        """Agrega una accion a la whitelist."""
        if action not in NEVER_ALLOW:
            self._whitelist.add(action)

    def remove_from_whitelist(self, action: str):
        """Remueve una accion de la whitelist."""
        self._whitelist.discard(action)

    def set_whitelist(self, actions: set[str]):
        """Reemplaza la whitelist completa (filtra NEVER_ALLOW)."""
        self._whitelist = actions - NEVER_ALLOW

    def get_whitelist(self) -> list[str]:
        return sorted(self._whitelist)

    # ─── Main Check ───

    def check_action(self, action_name: str, category: str = "normal") -> dict:
        """
        Verifica si una accion puede ejecutarse.

        Returns:
            {
                "allowed": bool,
                "reason": str,    # "ok", "killed", "blacklisted", "not_whitelisted", "rate_limited"
            }
        """
        self._total_checks += 1

        # 1. Kill switch
        if self.is_killed:
            self._blocked_count += 1
            return {"allowed": False, "reason": "killed"}

        # 2. Blacklist absoluta
        if action_name in NEVER_ALLOW:
            self._blocked_count += 1
            return {"allowed": False, "reason": "blacklisted"}

        # 3. Whitelist
        if action_name not in self._whitelist:
            self._blocked_count += 1
            return {"allowed": False, "reason": "not_whitelisted"}

        # 4. Rate limit
        rate_limit = self._rate_limits.get(category)
        if rate_limit and not rate_limit.check():
            self._rate_limited_count += 1
            return {"allowed": False, "reason": "rate_limited"}

        # Todo OK
        return {"allowed": True, "reason": "ok"}

    # ─── Rate Limit Config ───

    def set_rate_limit(self, category: str, per_minute: int, per_hour: int):
        """Configura rate limits para una categoria."""
        self._rate_limits[category] = RateLimit(
            max_per_minute=per_minute,
            max_per_hour=per_hour,
        )

    def get_rate_limits(self) -> dict:
        return {
            cat: {"max_per_minute": rl.max_per_minute, "max_per_hour": rl.max_per_hour}
            for cat, rl in self._rate_limits.items()
        }

    @property
    def stats(self) -> dict:
        return {
            "killed": self.is_killed,
            "whitelist_size": len(self._whitelist),
            "blacklist_size": len(NEVER_ALLOW),
            "total_checks": self._total_checks,
            "blocked_count": self._blocked_count,
            "rate_limited_count": self._rate_limited_count,
            "rate_limits": self.get_rate_limits(),
        }
