"""
ILUMINATY - Capa 6: Intent Classifier
=======================================
Traduce lenguaje natural a acciones concretas.

"Guarda el archivo" → {"action": "save_file", "params": {}}
"Abre Chrome en google.com" → {"action": "navigate", "params": {"url": "https://google.com"}}
"Escribe hola mundo" → {"action": "type_text", "params": {"text": "hola mundo"}}

Usa pattern matching + keyword extraction (no requiere LLM).
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Intent:
    """Una intencion clasificada."""
    action: str
    params: dict
    confidence: float  # 0.0 - 1.0
    raw_input: str
    category: str  # "safe", "normal", "destructive"

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "params": self.params,
            "confidence": round(self.confidence, 2),
            "raw_input": self.raw_input,
            "category": self.category,
        }


# Pattern definitions: (regex, action, category, param_extractor)
PATTERNS = [
    # ─── Save/Open ───
    (r"(?:guarda|save|salva)(?:\s+(?:el|the)\s+)?(?:archivo|file)?",
     "save_file", "normal", lambda m: {}),
    (r"(?:abre|open|abrir)\s+(?:el\s+)?(?:archivo|file)\s+(.+)",
     "open_file", "safe", lambda m: {"path": m.group(1).strip()}),

    # ─── Type ───
    (r"(?:escribe|type|escribir|teclea)\s+['\"]?(.+?)['\"]?\s*$",
     "type_text", "normal", lambda m: {"text": m.group(1)}),

    # ─── Click ───
    (r"(?:click|clic|haz click|presiona)\s+(?:en\s+)?(?:el\s+)?(?:boton\s+)?['\"]?(.+?)['\"]?\s*$",
     "click_element", "normal", lambda m: {"name": m.group(1)}),
    (r"(?:doble click|double click)\s+(?:en\s+)?(.+)",
     "double_click_element", "normal", lambda m: {"name": m.group(1)}),

    # ─── Navigation ───
    (r"(?:navega|navigate|ve|go)\s+(?:a|to)\s+(.+)",
     "navigate", "normal", lambda m: {"url": _normalize_url(m.group(1).strip())}),
    (r"(?:abre|open)\s+(?:chrome|browser|navegador)(?:\s+(?:en|in)\s+(.+))?",
     "open_browser", "normal", lambda m: {"url": _normalize_url(m.group(1).strip()) if m.group(1) else ""}),

    # ─── Copy/Paste/Undo ───
    (r"(?:copia|copy)", "copy", "safe", lambda m: {}),
    (r"(?:pega|paste)", "paste", "normal", lambda m: {}),
    (r"(?:deshacer|undo)", "undo", "normal", lambda m: {}),
    (r"(?:rehacer|redo)", "redo", "normal", lambda m: {}),
    (r"(?:corta|cut)", "cut", "normal", lambda m: {}),

    # ─── Scroll ───
    (r"(?:scroll|desplaza)\s+(?:hacia\s+)?(?:arriba|up)(?:\s+(\d+))?",
     "scroll", "safe", lambda m: {"amount": int(m.group(1) or 3)}),
    (r"(?:scroll|desplaza)\s+(?:hacia\s+)?(?:abajo|down)(?:\s+(\d+))?",
     "scroll", "safe", lambda m: {"amount": -int(m.group(1) or 3)}),

    # ─── Find/Search ───
    (r"(?:busca|find|search|encuentra)\s+['\"]?(.+?)['\"]?\s*$",
     "find", "safe", lambda m: {"text": m.group(1)}),

    # ─── Tab Management ───
    (r"(?:nueva|new)\s+(?:pestaña|tab|pestana)",
     "new_tab", "normal", lambda m: {}),
    (r"(?:cierra|close)\s+(?:la\s+)?(?:pestaña|tab|pestana)",
     "close_tab", "normal", lambda m: {}),

    # ─── Terminal ───
    (r"(?:ejecuta|run|corre)\s+(.+)",
     "terminal_exec", "normal", lambda m: {"command": m.group(1).strip()}),

    # ─── Window Management ───
    (r"(?:minimiza|minimize)\s+(?:la\s+)?(?:ventana|window)?",
     "minimize_window", "normal", lambda m: {}),
    (r"(?:maximiza|maximize)\s+(?:la\s+)?(?:ventana|window)?",
     "maximize_window", "normal", lambda m: {}),
    (r"(?:cierra|close)\s+(?:la\s+)?(?:ventana|window)\s*(?:de\s+)?(.+)?",
     "close_window", "destructive", lambda m: {"title": m.group(1) or ""}),

    # ─── Process ───
    (r"(?:abre|open|lanza|launch)\s+(.+)",
     "launch_app", "normal", lambda m: {"app": m.group(1).strip()}),
    (r"(?:cierra|close|mata|kill)\s+(?:el\s+)?(?:proceso|process)\s+(.+)",
     "kill_process", "destructive", lambda m: {"name": m.group(1).strip()}),

    # ─── Git ───
    (r"(?:git\s+)?(?:commitea|commit)\s+['\"]?(.+?)['\"]?\s*$",
     "git_commit", "normal", lambda m: {"message": m.group(1)}),
    (r"(?:git\s+)?(?:push|sube|pushea)",
     "git_push", "destructive", lambda m: {}),
    (r"(?:git\s+)?(?:pull|jala|baja)",
     "git_pull", "normal", lambda m: {}),
    (r"(?:git\s+)?status",
     "git_status", "safe", lambda m: {}),

    # ─── File Operations ───
    (r"(?:lee|read|leer)\s+(?:el\s+)?(?:archivo|file)\s+(.+)",
     "read_file", "safe", lambda m: {"path": m.group(1).strip()}),
    (r"(?:borra|delete|elimina)\s+(?:el\s+)?(?:archivo|file)\s+(.+)",
     "delete_file", "destructive", lambda m: {"path": m.group(1).strip()}),

    # ─── Screenshot ───
    (r"(?:screenshot|captura|pantallazo)",
     "screenshot", "safe", lambda m: {}),

    # ─── Hotkey ───
    (r"(?:presiona|press)\s+(ctrl|alt|shift|cmd|win)[\+\s]+([\w]+)",
     "hotkey", "normal", lambda m: {"keys": [m.group(1).lower(), m.group(2).lower()]}),
]


def _normalize_url(url: str) -> str:
    """Agrega https:// si no tiene protocolo."""
    url = url.strip().strip("'\"")
    if url and not url.startswith(("http://", "https://", "about:", "file:")):
        url = "https://" + url
    return url


class IntentClassifier:
    """
    Clasifica lenguaje natural en acciones ejecutables.
    No requiere LLM — usa pattern matching con regex.
    """

    def __init__(self):
        self._patterns = list(PATTERNS)
        self._aliases: dict[str, str] = {}

    def classify(self, text: str) -> Optional[Intent]:
        """Clasifica un texto en una intencion."""
        text_clean = text.strip().lower()

        for pattern, action, category, extractor in self._patterns:
            match = re.search(pattern, text_clean, re.IGNORECASE)
            if match:
                try:
                    params = extractor(match)
                except Exception:
                    params = {}

                # Calcular confianza basada en cuanto del input matchea
                match_len = match.end() - match.start()
                confidence = min(match_len / max(len(text_clean), 1), 1.0)

                # Check alias
                action = self._aliases.get(action, action)

                return Intent(
                    action=action,
                    params=params,
                    confidence=confidence,
                    raw_input=text,
                    category=category,
                )

        return None

    def classify_or_default(self, text: str) -> Intent:
        """Clasifica, o retorna un intent con action='unknown'."""
        result = self.classify(text)
        if result:
            return result
        return Intent(
            action="unknown",
            params={"raw": text},
            confidence=0.0,
            raw_input=text,
            category="safe",
        )

    def add_alias(self, from_action: str, to_action: str):
        """Agrega un alias de accion."""
        self._aliases[from_action] = to_action

    def add_pattern(self, pattern: str, action: str, category: str = "normal"):
        """Agrega un patron personalizado."""
        self._patterns.append((pattern, action, category, lambda m: {}))

    @property
    def stats(self) -> dict:
        return {
            "pattern_count": len(self._patterns),
            "alias_count": len(self._aliases),
            "supported_actions": sorted(set(a for _, a, _, _ in self._patterns)),
        }
