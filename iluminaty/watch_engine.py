"""
ILUMINATY - Watch Engine
=========================
Motor de espera activa para IPA. Permite a la IA delegar monitoreo
sin consumir tokens mientras espera.

La IA dice "avísame cuando X" y se desconecta.
IPA monitorea en background, notifica cuando ocurre el evento.
Cero tokens mientras espera.

Conditions soportadas:
  - page_loaded     : content_loaded gate event de IPA
  - motion_stopped  : motion_end gate event (pantalla quieta)
  - motion_started  : motion_start gate event (actividad detectada)
  - text_appeared   : texto específico aparece en OCR
  - text_disappeared: texto desaparece del OCR
  - window_opened   : ventana con título específico aparece
  - window_closed   : ventana desaparece
  - build_passed    : terminal muestra exit code 0 o "passed"
  - build_failed    : terminal muestra error / failed
  - element_visible : smart_locate encuentra el elemento
  - idle            : sin actividad por N segundos

Uso desde MCP:
    watch_and_notify(condition="page_loaded", timeout=30)
    monitor_until(condition="text_appeared", text="Upload complete", timeout=120)
"""
from __future__ import annotations

import re
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .ipa_bridge import IPABridge

log = logging.getLogger("iluminaty.watch_engine")


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class WatchResult:
    triggered: bool
    condition: str
    elapsed_s: float
    reason: str = ""          # human-readable description of what triggered
    evidence: str = ""        # OCR text or event description at trigger time
    monitor_id: int = 0
    timed_out: bool = False

    def to_dict(self) -> dict:
        return {
            "triggered":  self.triggered,
            "condition":  self.condition,
            "elapsed_s":  round(self.elapsed_s, 2),
            "reason":     self.reason,
            "evidence":   self.evidence,
            "monitor_id": self.monitor_id,
            "timed_out":  self.timed_out,
        }


# ── Watch Engine ──────────────────────────────────────────────────────────────

class WatchEngine:
    """Monitors screen state and notifies when a condition is met.

    Runs checks at ~2Hz (every 500ms) to avoid overwhelming the server.
    Uses IPA gate events + OCR for condition detection.
    """

    POLL_INTERVAL = 0.5  # seconds between checks

    def __init__(self, ipa_bridge: Optional["IPABridge"] = None,
                 ocr_fn: Optional[Callable] = None,
                 ui_tree_fn: Optional[Callable] = None,
                 windows_fn: Optional[Callable] = None):
        self._ipa        = ipa_bridge
        self._ocr_fn     = ocr_fn       # fn(monitor_id) -> str (current OCR text)
        self._ui_fn      = ui_tree_fn   # fn(query) -> bool (element found)
        self._windows_fn = windows_fn   # fn() -> list[dict] (visible windows)

    def wait(
        self,
        condition: str,
        timeout: float = 30.0,
        *,
        text: Optional[str] = None,          # for text_appeared / text_disappeared
        window_title: Optional[str] = None,  # for window_opened / window_closed
        element: Optional[str] = None,       # for element_visible
        idle_seconds: float = 3.0,           # for idle condition
        monitor_id: Optional[int] = None,
    ) -> WatchResult:
        """Block until condition is met or timeout expires.

        This runs synchronously — call from a thread or use the async wrapper.
        Returns WatchResult with triggered=True if condition met, False if timed out.
        """
        t_start = time.time()
        condition_norm = condition.strip().lower().replace("-", "_")

        log.info("watch(%s) started, timeout=%.0fs", condition_norm, timeout)

        # Snapshot IPA gate events already seen — only react to NEW ones
        # Use a mutable dict so _check can update state across iterations
        state = {
            "seen_gate_ts":    time.time(),
            "last_motion_type": "unknown",
            "last_ocr":        "",
        }

        while True:
            elapsed = time.time() - t_start

            if elapsed >= timeout:
                return WatchResult(
                    triggered=False, condition=condition_norm,
                    elapsed_s=elapsed, timed_out=True,
                    reason=f"Timed out after {timeout:.0f}s",
                )

            result = self._check(
                condition_norm, elapsed,
                text=text, window_title=window_title,
                element=element, idle_seconds=idle_seconds,
                monitor_id=monitor_id,
                seen_gate_ts=state["seen_gate_ts"],
                last_motion_type=state["last_motion_type"],
                last_ocr=state["last_ocr"],
                state=state,   # pass mutable state for updates
            )

            if result is not None:
                log.info("watch(%s) triggered in %.1fs: %s",
                         condition_norm, elapsed, result.reason)
                return result

            time.sleep(self.POLL_INTERVAL)

    def _check(
        self,
        condition: str,
        elapsed: float,
        *,
        text: Optional[str],
        window_title: Optional[str],
        element: Optional[str],
        idle_seconds: float,
        monitor_id: Optional[int],
        seen_gate_ts: float,
        last_motion_type: str,
        last_ocr: str,
        state: Optional[dict] = None,  # mutable state updated each iteration
    ) -> Optional[WatchResult]:
        """Single check iteration. Returns WatchResult if triggered, None to continue."""

        # ── IPA gate events (fastest path) ───────────────────────────────────
        new_events = []
        if self._ipa:
            events = self._ipa.recent_events(seconds=self.POLL_INTERVAL * 3)
            new_events = [e for e in events if e.timestamp > seen_gate_ts]
            # Update seen_gate_ts so next iteration only sees newer events
            if new_events and state is not None:
                state["seen_gate_ts"] = max(e.timestamp for e in new_events) + 0.001

            for evt in new_events:
                if condition == "page_loaded" and evt.event_type == "content_loaded":
                    return WatchResult(
                        triggered=True, condition=condition, elapsed_s=elapsed,
                        reason="Content loaded and stable",
                        evidence=evt.description, monitor_id=evt.monitor_id,
                    )
                if condition == "motion_stopped" and evt.event_type == "motion_end":
                    return WatchResult(
                        triggered=True, condition=condition, elapsed_s=elapsed,
                        reason=f"{evt.description}",
                        evidence=evt.motion_type, monitor_id=evt.monitor_id,
                    )
                if condition == "motion_started" and evt.event_type == "motion_start":
                    return WatchResult(
                        triggered=True, condition=condition, elapsed_s=elapsed,
                        reason=f"{evt.description}",
                        evidence=evt.motion_type, monitor_id=evt.monitor_id,
                    )

        # ── OCR-based conditions ──────────────────────────────────────────────
        current_ocr = ""
        if self._ocr_fn and condition in (
            "text_appeared", "text_disappeared", "build_passed", "build_failed", "idle"
        ):
            try:
                current_ocr = self._ocr_fn(monitor_id) or ""
                # Update last_ocr so text_disappeared works correctly next iteration
                if current_ocr and state is not None:
                    state["last_ocr"] = current_ocr
            except Exception:
                current_ocr = ""

        if condition == "text_appeared" and text:
            if text.lower() in current_ocr.lower():
                return WatchResult(
                    triggered=True, condition=condition, elapsed_s=elapsed,
                    reason=f"Text '{text}' found on screen",
                    evidence=current_ocr[:200],
                )

        if condition == "text_disappeared" and text:
            if text.lower() not in current_ocr.lower() and last_ocr:
                if text.lower() in last_ocr.lower():
                    return WatchResult(
                        triggered=True, condition=condition, elapsed_s=elapsed,
                        reason=f"Text '{text}' disappeared from screen",
                    )

        if condition == "build_passed":
            ocr_low = current_ocr.lower()
            indicators = ["passed", "success", "✓", "all tests", "build succeeded",
                          "exit code: 0", "0 errors", "finished successfully"]
            for ind in indicators:
                if ind in ocr_low:
                    return WatchResult(
                        triggered=True, condition=condition, elapsed_s=elapsed,
                        reason=f"Build success indicator: '{ind}'",
                        evidence=current_ocr[:300],
                    )

        if condition == "build_failed":
            ocr_low = current_ocr.lower()
            indicators = ["error", "failed", "failure", "exception", "traceback",
                          "exit code: 1", "build failed", "compilation failed"]
            for ind in indicators:
                if ind in ocr_low:
                    return WatchResult(
                        triggered=True, condition=condition, elapsed_s=elapsed,
                        reason=f"Build failure indicator: '{ind}'",
                        evidence=current_ocr[:300],
                    )

        if condition == "idle":
            if self._ipa:
                motion = self._ipa.motion_now(seconds=idle_seconds)
                if motion and motion.get("motion_type") in ("static", "idle"):
                    return WatchResult(
                        triggered=True, condition=condition, elapsed_s=elapsed,
                        reason=f"Screen idle for {idle_seconds:.0f}s",
                    )

        # ── Window-based conditions ───────────────────────────────────────────
        if condition in ("window_opened", "window_closed") and window_title:
            # Primary: poll list_windows directly — reliable, no IPA dependency
            # IPA gate events for window_focus are unreliable for newly spawned windows
            if self._windows_fn:
                try:
                    wins = self._windows_fn() or []
                    title_l = window_title.lower()
                    match = any(
                        title_l in str(w.get("title", "")).lower() or
                        title_l in str(w.get("app_name", "")).lower()
                        for w in wins
                    )
                    if condition == "window_opened" and match:
                        return WatchResult(
                            triggered=True, condition=condition, elapsed_s=elapsed,
                            reason=f"Window '{window_title}' is now open",
                            evidence=next(
                                (str(w.get("title", "")) for w in wins
                                 if title_l in str(w.get("title", "")).lower()),
                                window_title,
                            ),
                        )
                    if condition == "window_closed" and not match:
                        return WatchResult(
                            triggered=True, condition=condition, elapsed_s=elapsed,
                            reason=f"Window '{window_title}' is no longer open",
                        )
                except Exception:
                    pass

            # Fallback: IPA gate events (window_focus — fires on user focus, not open)
            if self._ipa:
                for evt in new_events:
                    if evt.event_type in ("window_focus", "window_opened") and \
                            window_title.lower() in evt.description.lower():
                        if condition == "window_opened":
                            return WatchResult(
                                triggered=True, condition=condition, elapsed_s=elapsed,
                                reason=f"Window '{window_title}' detected via IPA event",
                                evidence=evt.description,
                            )

        # ── Element visibility ────────────────────────────────────────────────
        if condition == "element_visible" and element and self._ui_fn:
            try:
                found = self._ui_fn(element)
                if found:
                    return WatchResult(
                        triggered=True, condition=condition, elapsed_s=elapsed,
                        reason=f"Element '{element}' found on screen",
                        evidence=element,
                    )
            except Exception:
                pass

        return None  # not triggered yet
