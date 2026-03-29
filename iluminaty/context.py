"""
ILUMINATY - Context Engine
=============================
No solo VER la pantalla — ENTENDER que esta haciendo el usuario.

Niveles de contexto:
1. App tracking: que app esta activa y por cuanto tiempo
2. Workflow detection: "coding", "browsing", "meeting", "designing", etc.
3. Activity timeline: resumen comprimido de la ultima hora
4. Focus patterns: cuanto tiempo en cada app, cambios frecuentes = distraccion

El contexto se inyecta en el AI prompt para que la IA
no solo vea un frame suelto sino que entienda la historia.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# ─── Workflow Patterns ───

WORKFLOW_PATTERNS = {
    "coding": {
        "apps": ["code", "visual studio", "vim", "neovim", "intellij", "pycharm",
                 "webstorm", "sublime", "atom", "cursor", "zed", "terminal",
                 "cmd.exe", "powershell", "warp", "iterm", "hyper", "alacritty",
                 "windsurf", "claude"],
        "titles": [".py", ".js", ".ts", ".rs", ".go", ".java", ".cpp",
                   ".html", ".css", ".json", ".md", "github.com",
                   "localhost", "127.0.0.1", "stackoverflow"],
    },
    "browsing": {
        "apps": ["chrome", "firefox", "safari", "brave", "edge", "opera", "arc"],
        "titles": ["google", "youtube", "reddit", "twitter", "facebook",
                   "instagram", "tiktok", "news", "blog", "article"],
    },
    "meeting": {
        "apps": ["zoom", "teams", "meet", "slack", "discord", "webex", "skype"],
        "titles": ["meeting", "call", "huddle", "standup", "sync"],
    },
    "designing": {
        "apps": ["figma", "sketch", "photoshop", "illustrator", "canva",
                 "gimp", "inkscape", "affinity", "xd"],
        "titles": ["design", "mockup", "wireframe", "prototype", "layout"],
    },
    "writing": {
        "apps": ["word", "docs", "notion", "obsidian", "bear", "ulysses",
                 "typora", "overleaf", "latex"],
        "titles": ["document", "draft", "essay", "report", "notes"],
    },
    "email": {
        "apps": ["outlook", "thunderbird", "mail", "spark"],
        "titles": ["gmail", "outlook", "mail", "inbox", "compose"],
    },
    "media": {
        "apps": ["spotify", "vlc", "mpv", "quicktime", "netflix"],
        "titles": ["youtube.com/watch", "netflix", "spotify", "music", "video",
                   "twitch"],
    },
    "finance": {
        "apps": ["excel", "sheets", "quickbooks", "mint"],
        "titles": ["bank", "trading", "binance", "coinbase", "portfolio",
                   "invoice", "spreadsheet", "budget"],
    },
    "gaming": {
        "apps": ["steam", "epic", "battle.net", "riot"],
        "titles": ["game", "play", "steam"],
    },
}


@dataclass
class AppSession:
    """Sesion de uso de una app."""
    app_name: str
    window_title: str
    start_time: float
    end_time: float
    duration_seconds: float = 0.0

    def __post_init__(self):
        self.duration_seconds = self.end_time - self.start_time


@dataclass
class WorkflowState:
    """Estado actual del workflow del usuario."""
    current_workflow: str           # "coding", "browsing", etc.
    confidence: float               # 0.0 - 1.0
    current_app: str
    current_title: str
    time_in_workflow: float         # segundos en este workflow
    time_in_app: float              # segundos en esta app
    switch_count_last_5min: int     # cambios de app en 5 min
    is_focused: bool                # pocas apps = focused, muchas = disperso
    context_summary: str            # resumen para el AI prompt


class ContextEngine:
    """
    Motor de contexto que trackea que hace el usuario
    y genera un resumen inteligente para la IA.
    """

    def __init__(self, max_history: int = 500):
        self._history: list[dict] = []  # [{timestamp, app, title, workflow}]
        self.max_history = max_history
        self._current_app: str = ""
        self._current_title: str = ""
        self._current_workflow: str = "unknown"
        self._workflow_start: float = time.time()
        self._app_start: float = time.time()
        self._app_times: defaultdict[str, float] = defaultdict(float)
        self._workflow_times: defaultdict[str, float] = defaultdict(float)

    def _detect_workflow(self, app_name: str, window_title: str) -> tuple[str, float]:
        """
        Detecta el workflow basado en app y titulo de ventana.
        Returns (workflow_name, confidence).
        """
        app_lower = app_name.lower()
        title_lower = window_title.lower()
        scores: dict[str, float] = defaultdict(float)

        for workflow, patterns in WORKFLOW_PATTERNS.items():
            # Check app name
            for app_pattern in patterns["apps"]:
                if app_pattern in app_lower:
                    scores[workflow] += 0.6
                    break

            # Check window title
            for title_pattern in patterns["titles"]:
                if title_pattern in title_lower:
                    scores[workflow] += 0.4
                    break

        if not scores:
            return "unknown", 0.0

        best = max(scores, key=scores.get)
        return best, min(scores[best], 1.0)

    def update(self, app_name: str, window_title: str):
        """
        Actualiza el contexto con la app/ventana actual.
        Llamar esto en cada frame o cada pocos segundos.
        """
        now = time.time()

        # Detectar workflow
        workflow, confidence = self._detect_workflow(app_name, window_title)

        # Si cambio de app, registrar
        if app_name != self._current_app or window_title != self._current_title:
            # Acumular tiempo en app anterior
            if self._current_app:
                elapsed = now - self._app_start
                self._app_times[self._current_app] += elapsed

            self._current_app = app_name
            self._current_title = window_title
            self._app_start = now

        # Si cambio de workflow, registrar
        if workflow != self._current_workflow:
            if self._current_workflow:
                elapsed = now - self._workflow_start
                self._workflow_times[self._current_workflow] += elapsed

            self._current_workflow = workflow
            self._workflow_start = now

        # Agregar al historial
        self._history.append({
            "timestamp": now,
            "app": app_name,
            "title": window_title[:100],
            "workflow": workflow,
        })

        # Trim history
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history:]

    def get_state(self) -> WorkflowState:
        """Retorna el estado actual del workflow."""
        now = time.time()

        # Contar switches en los ultimos 5 minutos
        cutoff = now - 300
        recent = [h for h in self._history if h["timestamp"] >= cutoff]
        switches = 0
        prev_app = None
        for h in recent:
            if h["app"] != prev_app and prev_app is not None:
                switches += 1
            prev_app = h["app"]

        # Determinar si esta focused
        is_focused = switches < 10  # menos de 10 cambios en 5 min = focused

        # Tiempo actual
        time_in_workflow = now - self._workflow_start
        time_in_app = now - self._app_start

        # Generar summary
        summary = self._generate_summary(now)

        return WorkflowState(
            current_workflow=self._current_workflow,
            confidence=self._detect_workflow(self._current_app, self._current_title)[1],
            current_app=self._current_app,
            current_title=self._current_title,
            time_in_workflow=round(time_in_workflow, 1),
            time_in_app=round(time_in_app, 1),
            switch_count_last_5min=switches,
            is_focused=is_focused,
            context_summary=summary,
        )

    def _generate_summary(self, now: float) -> str:
        """Genera un resumen de contexto para el AI prompt."""
        state_parts = []

        # Workflow actual
        time_in = now - self._workflow_start
        if time_in < 60:
            time_str = f"{int(time_in)}s"
        elif time_in < 3600:
            time_str = f"{int(time_in/60)}m"
        else:
            time_str = f"{int(time_in/3600)}h {int((time_in%3600)/60)}m"

        state_parts.append(
            f"User is **{self._current_workflow}** "
            f"in {self._current_app} for {time_str}"
        )

        # Apps recientes (ultimos 5 min)
        cutoff = now - 300
        recent = [h for h in self._history if h["timestamp"] >= cutoff]
        if recent:
            apps_used = list(dict.fromkeys(h["app"] for h in recent))  # unique, ordered
            if len(apps_used) > 1:
                state_parts.append(f"Recent apps: {', '.join(apps_used[:5])}")

        # Focus level
        switches = 0
        prev = None
        for h in recent:
            if h["app"] != prev and prev is not None:
                switches += 1
            prev = h["app"]

        if switches > 15:
            state_parts.append("Focus: LOW (frequent app switching)")
        elif switches > 5:
            state_parts.append("Focus: MEDIUM")
        else:
            state_parts.append("Focus: HIGH (deep work)")

        # Top workflows hoy
        if self._workflow_times:
            top = sorted(self._workflow_times.items(), key=lambda x: x[1], reverse=True)[:3]
            breakdown = ", ".join(f"{w}: {int(t/60)}m" for w, t in top if t > 60)
            if breakdown:
                state_parts.append(f"Session breakdown: {breakdown}")

        return ".".join(state_parts)

    def get_timeline(self, minutes: int = 30) -> list[dict]:
        """Retorna timeline de actividad de los ultimos N minutos."""
        cutoff = time.time() - (minutes * 60)
        return [h for h in self._history if h["timestamp"] >= cutoff]

    def get_app_stats(self) -> dict:
        """Stats de tiempo por app."""
        now = time.time()
        # Add current session
        times = dict(self._app_times)
        if self._current_app:
            times[self._current_app] = times.get(self._current_app, 0) + (now - self._app_start)

        # Sort by time
        sorted_apps = sorted(times.items(), key=lambda x: x[1], reverse=True)
        return {
            "apps": [
                {"name": app, "seconds": round(t, 1), "minutes": round(t/60, 1)}
                for app, t in sorted_apps[:15]
            ],
            "total_apps": len(times),
        }

    def get_workflow_stats(self) -> dict:
        """Stats de tiempo por workflow."""
        now = time.time()
        times = dict(self._workflow_times)
        if self._current_workflow:
            times[self._current_workflow] = times.get(self._current_workflow, 0) + (now - self._workflow_start)

        sorted_wf = sorted(times.items(), key=lambda x: x[1], reverse=True)
        return {
            "workflows": [
                {"name": wf, "seconds": round(t, 1), "minutes": round(t/60, 1)}
                for wf, t in sorted_wf
            ],
        }

    def to_ai_context(self) -> str:
        """
        Genera bloque de contexto para inyectar en el AI prompt.
        Esto es lo que diferencia a ILUMINATY de un simple screenshot tool.
        """
        state = self.get_state()
        return f"""### User Context
{state.context_summary}
**Current window**: {state.current_title[:80]}
**App switches (5min)**: {state.switch_count_last_5min}"""

    def reset(self):
        """Reset completo."""
        self._history.clear()
        self._app_times.clear()
        self._workflow_times.clear()
        self._current_app = ""
        self._current_title = ""
        self._current_workflow = "unknown"
        self._workflow_start = time.time()
        self._app_start = time.time()
