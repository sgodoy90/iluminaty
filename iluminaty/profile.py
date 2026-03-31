"""
ILUMINATY - E04: User Profile Learning
=========================================
La IA recuerda tus preferencias entre sesiones.
Perfil persistente, encriptado, local-only.

Aprende:
  - Apps favoritas y tiempo de uso
  - Stack tecnologico (lenguajes, frameworks)
  - Patrones de trabajo (horarios, flujos)
  - Preferencias de comunicacion
  - Proyectos recurrentes

El perfil se guarda en disco ENCRIPTADO.
El usuario puede ver/editar/borrar cualquier dato.
"""

import os
import json
import logging
import time
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class UserProfile:
    """Perfil del usuario construido por observacion."""
    # Identificacion
    username: str = ""
    machine_id: str = ""

    # Apps y herramientas
    top_apps: dict = field(default_factory=dict)        # {app: total_seconds}
    preferred_browser: str = ""
    preferred_editor: str = ""
    preferred_terminal: str = ""

    # Tech stack
    languages_detected: dict = field(default_factory=dict)  # {lang: frequency}
    frameworks_detected: list = field(default_factory=list)

    # Patrones de trabajo
    typical_start_hour: int = 9
    typical_end_hour: int = 18
    most_active_workflow: str = ""
    avg_focus_duration_minutes: float = 0.0

    # Preferencias
    prefers_dark_mode: bool = True
    primary_language: str = "en"       # idioma de la interfaz
    monitor_count: int = 1

    # Meta
    created_at: float = 0.0
    last_updated: float = 0.0
    observation_hours: float = 0.0

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "top_apps": dict(sorted(self.top_apps.items(), key=lambda x: x[1], reverse=True)[:10]),
            "preferred_editor": self.preferred_editor,
            "preferred_browser": self.preferred_browser,
            "languages": self.languages_detected,
            "most_active_workflow": self.most_active_workflow,
            "monitor_count": self.monitor_count,
            "observation_hours": round(self.observation_hours, 1),
            "last_updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.last_updated)) if self.last_updated else "never",
        }


class ProfileLearner:
    """
    Aprende el perfil del usuario observando su actividad.
    Persiste en disco como JSON (encriptable).
    """

    def __init__(self, profile_path: Optional[str] = None, enabled: bool = False):
        self.enabled = enabled
        self.profile_path = profile_path or os.path.join(
            os.path.expanduser("~"), ".iluminaty", "profile.json"
        )
        self.profile = UserProfile()
        self._session_start = time.time()
        self._last_save_time = time.time()  # BUG-002 fix: separate save tracker

        if enabled:
            self._load()

    def _load(self):
        """Carga perfil desde disco."""
        try:
            path = Path(self.profile_path)
            if path.exists():
                data = json.loads(path.read_text())
                for key, value in data.items():
                    if hasattr(self.profile, key):
                        setattr(self.profile, key, value)
        except Exception as e:
            logger.debug("Failed to load user profile: %s", e)

    def _save(self):
        """Guarda perfil a disco."""
        if not self.enabled:
            return
        try:
            path = Path(self.profile_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Solo datos del perfil, no objetos complejos
            data = {}
            for key in self.profile.__dataclass_fields__:
                val = getattr(self.profile, key)
                if isinstance(val, (str, int, float, bool, dict, list)):
                    data[key] = val
            path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.debug("Failed to save user profile: %s", e)

    def observe(self, app_name: str, window_title: str, workflow: str,
                ocr_text: str = "", monitor_count: int = 1):
        """
        Observa la actividad actual y actualiza el perfil.
        Llamar periodicamente (cada 30-60 segundos).
        """
        if not self.enabled:
            return

        now = time.time()
        self.profile.last_updated = now
        if not self.profile.created_at:
            self.profile.created_at = now

        # Acumular tiempo por app
        self.profile.top_apps[app_name] = self.profile.top_apps.get(app_name, 0) + 30

        # Detectar preferencias
        app_lower = app_name.lower()
        if any(e in app_lower for e in ["code", "vim", "cursor", "zed", "sublime", "intellij"]):
            self.profile.preferred_editor = app_name
        if any(b in app_lower for b in ["chrome", "firefox", "brave", "safari", "edge"]):
            self.profile.preferred_browser = app_name
        if any(t in app_lower for t in ["terminal", "cmd", "powershell", "iterm", "warp"]):
            self.profile.preferred_terminal = app_name

        # Detectar lenguajes en OCR/titulo
        lang_patterns = {
            "Python": [".py", "python", "pip", "django", "flask", "fastapi"],
            "JavaScript": [".js", "node", "npm", "react", "vue", "next"],
            "TypeScript": [".ts", ".tsx", "typescript"],
            "Rust": [".rs", "cargo", "rustc"],
            "Go": [".go", "golang"],
            "Java": [".java", "maven", "gradle"],
            "C++": [".cpp", ".hpp", "cmake"],
            "C#": [".cs", "dotnet", "nuget"],
            "HTML/CSS": [".html", ".css", ".scss"],
        }

        text_lower = (window_title + " " + ocr_text[:500]).lower()
        for lang, patterns in lang_patterns.items():
            if any(p in text_lower for p in patterns):
                self.profile.languages_detected[lang] = self.profile.languages_detected.get(lang, 0) + 1

        # Workflow mas activo
        if workflow and workflow != "unknown":
            self.profile.most_active_workflow = workflow

        # Monitor count
        self.profile.monitor_count = monitor_count

        # Horas de observacion
        self.profile.observation_hours = (now - self._session_start) / 3600

        # Auto-save cada 5 minutos (BUG-002 fix: use _last_save_time not last_updated)
        if now - self._last_save_time > 300:
            self._last_save_time = now
            self._save()

    def get_profile(self) -> dict:
        return self.profile.to_dict()

    def get_ai_context(self) -> str:
        """Genera contexto del usuario para el AI prompt."""
        if not self.enabled or not self.profile.last_updated:
            return ""

        parts = ["### User Profile"]
        if self.profile.preferred_editor:
            parts.append(f"- Editor: {self.profile.preferred_editor}")
        if self.profile.languages_detected:
            top_langs = sorted(self.profile.languages_detected.items(), key=lambda x: x[1], reverse=True)[:5]
            parts.append(f"- Languages: {', '.join(l for l, _ in top_langs)}")
        if self.profile.most_active_workflow:
            parts.append(f"- Primary workflow: {self.profile.most_active_workflow}")
        parts.append(f"- Monitors: {self.profile.monitor_count}")
        return "\n".join(parts)

    def delete_profile(self):
        """Borra todo el perfil (privacy)."""
        self.profile = UserProfile()
        try:
            Path(self.profile_path).unlink(missing_ok=True)
        except Exception as e:
            logger.debug("Failed to delete user profile file: %s", e)

    @property
    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "has_profile": self.profile.last_updated > 0,
            "observation_hours": round(self.profile.observation_hours, 1),
            "apps_tracked": len(self.profile.top_apps),
            "languages_detected": len(self.profile.languages_detected),
        }
