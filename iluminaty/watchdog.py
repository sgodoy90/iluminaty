"""
ILUMINATY - E01: Proactive Watchdog
=====================================
La IA no espera a que le pregunten — vigila y alerta.

En vez de:  User: "que hay en mi pantalla?"  → AI: "Veo..."
Ahora:      AI: "Tu build fallo. Error en linea 42. Quieres que lo arregle?"

Triggers configurables:
  - ERROR/EXCEPTION en terminal
  - Build failed / tests failed
  - Crash dialogs / error popups
  - New notification
  - Security warnings
  - Custom regex patterns

El watchdog corre en background, analiza OCR text + visual changes,
y dispara webhooks/callbacks cuando detecta un patron.
"""

import re
import logging
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger(__name__)


@dataclass
class WatchdogAlert:
    """Una alerta generada por el watchdog."""
    id: str
    trigger_name: str
    severity: str         # "info", "warning", "error", "critical"
    message: str
    matched_text: str     # que texto disparo la alerta
    timestamp: float
    acknowledged: bool = False
    source: str = "ocr"   # "ocr", "title", "visual"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "trigger": self.trigger_name,
            "severity": self.severity,
            "message": self.message,
            "matched": self.matched_text[:100],
            "time": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
            "acknowledged": self.acknowledged,
            "source": self.source,
        }


@dataclass
class WatchdogTrigger:
    """Un trigger configurable del watchdog."""
    name: str
    pattern: str          # regex pattern
    severity: str = "warning"
    source: str = "ocr"   # "ocr", "title", "any"
    cooldown_seconds: float = 30.0  # no alertar mas de 1 vez cada N seg
    enabled: bool = True
    _last_fired: float = 0.0


# ─── Built-in triggers ───

DEFAULT_TRIGGERS = [
    WatchdogTrigger(
        name="error_in_terminal",
        pattern=r"(?i)(error|exception|traceback|fatal|panic|segfault)",
        severity="error",
        source="ocr",
        cooldown_seconds=15,
    ),
    WatchdogTrigger(
        name="build_failed",
        pattern=r"(?i)(build failed|compilation error|make.*error|cargo.*error|npm err)",
        severity="error",
        source="ocr",
        cooldown_seconds=30,
    ),
    WatchdogTrigger(
        name="test_failed",
        pattern=r"(?i)(test.*fail|tests? failed|\d+ failed|FAIL:)",
        severity="warning",
        source="ocr",
        cooldown_seconds=30,
    ),
    WatchdogTrigger(
        name="permission_denied",
        pattern=r"(?i)(permission denied|access denied|unauthorized|forbidden|401|403)",
        severity="warning",
        source="any",
        cooldown_seconds=60,
    ),
    WatchdogTrigger(
        name="disk_full",
        pattern=r"(?i)(disk full|no space left|storage.*full|out of memory|oom)",
        severity="critical",
        source="any",
        cooldown_seconds=120,
    ),
    WatchdogTrigger(
        name="connection_error",
        pattern=r"(?i)(connection refused|ECONNREFUSED|timeout|unreachable|dns.*fail)",
        severity="warning",
        source="ocr",
        cooldown_seconds=30,
    ),
    WatchdogTrigger(
        name="security_warning",
        pattern=r"(?i)(vulnerability|CVE-\d|security.*warning|cert.*expired|ssl.*error)",
        severity="critical",
        source="any",
        cooldown_seconds=120,
    ),
    WatchdogTrigger(
        name="git_conflict",
        pattern=r"(?i)(merge conflict|CONFLICT|<<<<<<|>>>>>>)",
        severity="warning",
        source="ocr",
        cooldown_seconds=60,
    ),
]


class Watchdog:
    """
    Motor de vigilancia proactiva.
    Analiza text/titulos en cada frame y dispara alertas.
    """

    def __init__(self, max_alerts: int = 100):
        self._triggers: list[WatchdogTrigger] = list(DEFAULT_TRIGGERS)
        self._alerts: deque[WatchdogAlert] = deque(maxlen=max_alerts)
        self._callbacks: list[Callable] = []
        self._alert_count: int = 0
        self._compiled: dict[str, re.Pattern] = {}
        self._lock = threading.Lock()  # BUG-013 fix: thread-safe mutations
        self._compile_patterns()

    def _compile_patterns(self):
        """Pre-compila regex patterns para velocidad."""
        for trigger in self._triggers:
            try:
                self._compiled[trigger.name] = re.compile(trigger.pattern)
            except re.error as e:
                logger.warning("Invalid watchdog regex '%s': %s", trigger.name, e)

    def add_trigger(self, trigger: WatchdogTrigger):
        """Agrega un trigger custom."""
        self._triggers.append(trigger)
        try:
            self._compiled[trigger.name] = re.compile(trigger.pattern)
        except re.error as e:
            logger.warning("Invalid custom watchdog regex '%s': %s", trigger.name, e)

    def remove_trigger(self, name: str):
        """Elimina un trigger por nombre."""
        self._triggers = [t for t in self._triggers if t.name != name]
        self._compiled.pop(name, None)

    def on_alert(self, callback: Callable):
        """Registra callback para cuando hay una alerta."""
        self._callbacks.append(callback)

    def scan(self, ocr_text: str = "", window_title: str = "") -> list[WatchdogAlert]:
        """
        Escanea texto OCR y/o titulo de ventana contra los triggers.
        Retorna lista de alertas nuevas (puede ser vacia).
        BUG-013 fix: thread-safe via lock.
        """
        now = time.time()
        new_alerts = []

        with self._lock:
            for trigger in self._triggers:
                if not trigger.enabled:
                    continue

                # Cooldown check
                if now - trigger._last_fired < trigger.cooldown_seconds:
                    continue

                pattern = self._compiled.get(trigger.name)
                if not pattern:
                    continue

                # Decide que texto escanear
                texts_to_scan = []
                if trigger.source in ("ocr", "any") and ocr_text:
                    texts_to_scan.append(("ocr", ocr_text))
                if trigger.source in ("title", "any") and window_title:
                    texts_to_scan.append(("title", window_title))

                for source, text in texts_to_scan:
                    match = pattern.search(text)
                    if match:
                        self._alert_count += 1
                        alert = WatchdogAlert(
                            id=f"alert-{self._alert_count}",
                            trigger_name=trigger.name,
                            severity=trigger.severity,
                            message=f"[{trigger.name}] detected: {match.group()[:60]}",
                            matched_text=match.group(),
                            timestamp=now,
                            source=source,
                        )
                        self._alerts.append(alert)
                        new_alerts.append(alert)
                        trigger._last_fired = now

                        # Fire callbacks
                        for cb in self._callbacks:
                            try:
                                cb(alert)
                            except Exception as e:
                                logger.debug("Watchdog callback failed: %s", e)

                        break  # one alert per trigger per scan

        return new_alerts

    def get_alerts(self, count: int = 20, unacknowledged_only: bool = False) -> list[dict]:
        """Retorna las ultimas N alertas."""
        alerts = list(self._alerts)
        if unacknowledged_only:
            alerts = [a for a in alerts if not a.acknowledged]
        return [a.to_dict() for a in alerts[-count:]]

    def acknowledge(self, alert_id: str) -> bool:
        """Marca una alerta como vista."""
        for alert in self._alerts:
            if alert.id == alert_id:
                alert.acknowledged = True
                return True
        return False

    def acknowledge_all(self):
        """Marca todas las alertas como vistas."""
        for alert in self._alerts:
            alert.acknowledged = True

    def get_triggers(self) -> list[dict]:
        """Lista todos los triggers configurados."""
        return [
            {
                "name": t.name,
                "pattern": t.pattern,
                "severity": t.severity,
                "source": t.source,
                "cooldown": t.cooldown_seconds,
                "enabled": t.enabled,
            }
            for t in self._triggers
        ]

    @property
    def stats(self) -> dict:
        unack = sum(1 for a in self._alerts if not a.acknowledged)
        return {
            "total_alerts": self._alert_count,
            "alerts_in_buffer": len(self._alerts),
            "unacknowledged": unack,
            "triggers_active": sum(1 for t in self._triggers if t.enabled),
            "triggers_total": len(self._triggers),
        }
