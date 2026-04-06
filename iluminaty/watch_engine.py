"""
ILUMINATY - Watch Engine v2
============================
Espera visual precisa subscripta al buffer de PerceptionEngine.

En lugar de pollear con time.sleep(0.5), usa threading.Event para
despertar INMEDIATAMENTE cuando PerceptionEngine detecta un cambio.

Latencia típica:
  - window_changed:  <50ms  (IPA gate 0 detecta en el próximo frame)
  - text_visible:    <200ms (OCR en frame siguiente al cambio)
  - screen_idle:     <100ms (IPA gate 1 detecta change_score=0)

Condiciones:
  - text_visible       : texto aparece en pantalla (OCR / IPA event)
  - text_hidden        : texto desaparece
  - window_changed     : ventana activa cambia
  - window_opened      : ventana con título específico aparece
  - window_closed      : ventana desaparece
  - screen_idle        : pantalla sin cambios por N segundos
  - motion             : cualquier movimiento detectado
  - page_loaded        : página/contenido cargó y está estable
  - build_passed       : terminal muestra éxito
  - build_failed       : terminal muestra error
  - element_visible    : elemento UI encontrado por nombre
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .perception import PerceptionEngine

log = logging.getLogger("iluminaty.watch_engine")


@dataclass
class WatchResult:
    triggered: bool
    condition: str
    elapsed_s: float
    reason: str = ""
    evidence: str = ""
    monitor_id: int = 0
    timed_out: bool = False

    def to_dict(self) -> dict:
        return {
            "triggered":  self.triggered,
            "condition":  self.condition,
            "elapsed_s":  round(self.elapsed_s, 2),
            "reason":     self.reason,
            "evidence":   self.evidence[:200] if self.evidence else "",
            "monitor_id": self.monitor_id,
            "timed_out":  self.timed_out,
        }


class WatchEngine:
    """Watches screen state using PerceptionEngine event push — no busy polling.

    When perception detects a change, it sets a threading.Event that wakes
    us up immediately. We then check whether the condition is met.
    Average latency = frame interval (333ms at 3fps) + check overhead (<5ms).
    """

    # Maximum time to wait between checks even if no new events arrive
    MAX_WAIT_INTERVAL = 0.5

    def __init__(
        self,
        perception: Optional["PerceptionEngine"] = None,
        ocr_fn: Optional[Callable] = None,       # fn(monitor_id) -> str
        ui_tree_fn: Optional[Callable] = None,   # fn(query) -> bool
        windows_fn: Optional[Callable] = None,   # fn() -> list[dict]
        # Legacy compat
        ipa_bridge=None,
    ):
        self._perception = perception
        self._ocr_fn     = ocr_fn
        self._ui_fn      = ui_tree_fn
        self._windows_fn = windows_fn

    def wait(
        self,
        condition: str,
        timeout: float = 30.0,
        *,
        text: Optional[str] = None,
        window_title: Optional[str] = None,
        element: Optional[str] = None,
        idle_seconds: float = 3.0,
        monitor_id: Optional[int] = None,
        value: Optional[str] = None,  # alias for text/window_title
    ) -> WatchResult:
        """Wait until condition is met or timeout.

        Uses perception event push for near-zero latency detection.
        Falls back to polling OCR/windows at MAX_WAIT_INTERVAL if no events.
        """
        t_start = time.monotonic()
        cond = condition.strip().lower().replace("-", "_")

        # Normalize aliases
        text = text or value
        window_title = window_title or (value if cond in ("window_opened", "window_closed", "window_title_contains", "url_contains") else None)

        log.info("watch(%s) started timeout=%.0fs text=%r window=%r",
                 cond, timeout, text, window_title)

        # Snapshot: only react to events AFTER this point
        since_ts = time.time()
        last_ocr = ""
        last_idle_motion_ts = time.time()

        while True:
            elapsed = time.monotonic() - t_start

            if elapsed >= timeout:
                return WatchResult(
                    triggered=False, condition=cond,
                    elapsed_s=elapsed, timed_out=True,
                    reason=f"Timed out after {timeout:.0f}s",
                )

            # ── Push-based: check new perception events ──────────────────────
            if self._perception is not None:
                result = self._check_perception_events(
                    cond, elapsed, since_ts,
                    text=text, window_title=window_title,
                    idle_seconds=idle_seconds, monitor_id=monitor_id,
                    last_idle_motion_ts=last_idle_motion_ts,
                )
                if result is not None:
                    return result

                # Update idle tracker: if motion detected, reset idle clock
                events = self._perception.get_events(last_seconds=self.MAX_WAIT_INTERVAL * 2)
                new = [e for e in events if e.timestamp > since_ts]
                if any(e.event_type in ("scene_change", "motion", "scrolling", "ui_activity",
                                        "video_detected", "text_appeared", "window_change")
                       for e in new):
                    last_idle_motion_ts = time.time()

            # ── Supplemental: OCR + window checks (slower path) ──────────────
            result = self._check_supplemental(
                cond, elapsed,
                text=text, window_title=window_title,
                element=element, idle_seconds=idle_seconds,
                monitor_id=monitor_id, last_ocr=last_ocr,
                last_idle_motion_ts=last_idle_motion_ts,
            )
            if isinstance(result, tuple):
                # check returned (WatchResult, new_last_ocr)
                wr, last_ocr = result
                if wr is not None:
                    return wr
            elif result is not None:
                return result

            # ── Wait for next event (push) or fallback timeout ────────────────
            if self._perception is not None:
                # Wait up to MAX_WAIT_INTERVAL — wakes immediately on new event
                self._perception.wait_for_event(timeout=self.MAX_WAIT_INTERVAL)
            else:
                time.sleep(self.MAX_WAIT_INTERVAL)

            since_ts = time.time() - self.MAX_WAIT_INTERVAL * 2  # recheck recent window

    def _check_perception_events(
        self,
        cond: str,
        elapsed: float,
        since_ts: float,
        *,
        text: Optional[str],
        window_title: Optional[str],
        idle_seconds: float,
        monitor_id: Optional[int],
        last_idle_motion_ts: float,
    ) -> Optional[WatchResult]:
        """Check perception event buffer for condition match."""
        events = self._perception.get_events(last_seconds=60, min_importance=0.0)
        new = [e for e in events if e.timestamp > since_ts]
        if not new:
            return None

        for evt in new:
            mon = getattr(evt, 'monitor', 0) or 0
            if monitor_id is not None and mon != 0 and mon != monitor_id:
                continue

            etype = evt.event_type
            desc  = evt.description or ""

            # window_changed / window_opened
            if cond in ("window_changed", "window_opened"):
                if etype in ("window_change", "window_opened", "title_change"):
                    if window_title is None or window_title.lower() in desc.lower():
                        return WatchResult(
                            triggered=True, condition=cond, elapsed_s=elapsed,
                            reason=desc, evidence=desc, monitor_id=mon,
                        )

            # window_closed — window that was visible is gone
            if cond == "window_closed" and window_title:
                # window_closed is better detected via windows_fn — skip here

                pass

            # page_loaded / content_ready
            if cond in ("page_loaded", "content_ready"):
                if etype in ("content_ready", "content_loaded", "page_navigation"):
                    return WatchResult(
                        triggered=True, condition=cond, elapsed_s=elapsed,
                        reason=desc, evidence=desc, monitor_id=mon,
                    )

            # motion
            if cond == "motion":
                if etype in ("scene_change", "ui_activity", "scrolling",
                             "motion_start", "video_detected"):
                    return WatchResult(
                        triggered=True, condition=cond, elapsed_s=elapsed,
                        reason=desc, evidence=desc, monitor_id=mon,
                    )

            # text_visible / text_appeared — via IPA text_appeared event
            if cond in ("text_visible", "text_appeared") and text:
                if etype == "text_appeared":
                    if text.lower() in desc.lower():
                        return WatchResult(
                            triggered=True, condition=cond, elapsed_s=elapsed,
                            reason=f"Text '{text}' detected",
                            evidence=desc, monitor_id=mon,
                        )

            # build_passed / build_failed via text events
            if cond == "build_passed" and etype == "text_appeared":
                indicators = ["passed", "success", "✓", "all tests",
                              "build succeeded", "0 errors", "finished successfully"]
                if any(ind in desc.lower() for ind in indicators):
                    return WatchResult(
                        triggered=True, condition=cond, elapsed_s=elapsed,
                        reason=f"Build success: {desc[:100]}",
                        evidence=desc, monitor_id=mon,
                    )

            if cond == "build_failed" and etype == "text_appeared":
                indicators = ["error", "failed", "failure", "exception",
                              "traceback", "build failed", "compilation failed"]
                if any(ind in desc.lower() for ind in indicators):
                    return WatchResult(
                        triggered=True, condition=cond, elapsed_s=elapsed,
                        reason=f"Build failure: {desc[:100]}",
                        evidence=desc, monitor_id=mon,
                    )

        # screen_idle — no motion events for N seconds
        if cond in ("screen_idle", "idle"):
            time_since_motion = time.time() - last_idle_motion_ts
            if time_since_motion >= idle_seconds:
                return WatchResult(
                    triggered=True, condition=cond, elapsed_s=elapsed,
                    reason=f"Screen idle for {time_since_motion:.1f}s",
                    monitor_id=monitor_id or 0,
                )

        return None

    def _check_supplemental(
        self,
        cond: str,
        elapsed: float,
        *,
        text: Optional[str],
        window_title: Optional[str],
        element: Optional[str],
        idle_seconds: float,
        monitor_id: Optional[int],
        last_ocr: str,
        last_idle_motion_ts: float,
    ):
        """OCR + window + element checks. Returns (WatchResult|None, new_ocr) or None."""

        new_ocr = last_ocr

        # OCR-based text detection (supplement to IPA events)
        if cond in ("text_visible", "text_appeared", "text_hidden", "text_disappeared",
                    "build_passed", "build_failed") and self._ocr_fn:
            try:
                current_ocr = self._ocr_fn(monitor_id) or ""
                new_ocr = current_ocr or last_ocr
            except Exception:
                current_ocr = ""

            if cond in ("text_visible", "text_appeared") and text:
                if text.lower() in current_ocr.lower():
                    return (WatchResult(
                        triggered=True, condition=cond, elapsed_s=elapsed,
                        reason=f"Text '{text}' visible on screen",
                        evidence=current_ocr[:200],
                    ), new_ocr)

            if cond in ("text_hidden", "text_disappeared") and text:
                if text.lower() not in current_ocr.lower() and last_ocr:
                    if text.lower() in last_ocr.lower():
                        return (WatchResult(
                            triggered=True, condition=cond, elapsed_s=elapsed,
                            reason=f"Text '{text}' disappeared",
                            evidence="",
                        ), new_ocr)

            if cond == "build_passed":
                ocr_low = current_ocr.lower()
                for ind in ["passed", "success", "✓", "all tests", "0 errors",
                            "finished successfully", "build succeeded"]:
                    if ind in ocr_low:
                        return (WatchResult(
                            triggered=True, condition=cond, elapsed_s=elapsed,
                            reason=f"Build indicator: '{ind}'",
                            evidence=current_ocr[:300],
                        ), new_ocr)

            if cond == "build_failed":
                ocr_low = current_ocr.lower()
                for ind in ["error", "failed", "failure", "exception",
                            "traceback", "exit code: 1", "build failed"]:
                    if ind in ocr_low:
                        return (WatchResult(
                            triggered=True, condition=cond, elapsed_s=elapsed,
                            reason=f"Failure indicator: '{ind}'",
                            evidence=current_ocr[:300],
                        ), new_ocr)

        # Window-based checks
        if cond in ("window_opened", "window_closed", "window_title_contains",
                    "url_contains") and window_title and self._windows_fn:
            try:
                wins = self._windows_fn() or []
                needle = window_title.lower()
                match = any(
                    needle in str(w.get("title", "")).lower() or
                    needle in str(w.get("app_name", "")).lower()
                    for w in wins
                )
                if cond in ("window_opened", "window_title_contains") and match:
                    matched = next(
                        (str(w.get("title", "")) for w in wins
                         if needle in str(w.get("title", "")).lower()),
                        window_title,
                    )
                    return (WatchResult(
                        triggered=True, condition=cond, elapsed_s=elapsed,
                        reason=f"Window '{window_title}' found",
                        evidence=matched,
                    ), new_ocr)
                if cond == "window_closed" and not match:
                    return (WatchResult(
                        triggered=True, condition=cond, elapsed_s=elapsed,
                        reason=f"Window '{window_title}' no longer visible",
                    ), new_ocr)
            except Exception:
                pass

        # Element visibility
        if cond == "element_visible" and element and self._ui_fn:
            try:
                found = self._ui_fn(element)
                if found:
                    return (WatchResult(
                        triggered=True, condition=cond, elapsed_s=elapsed,
                        reason=f"Element '{element}' found",
                        evidence=str(element),
                    ), new_ocr)
            except Exception:
                pass

        return (None, new_ocr)
