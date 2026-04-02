"""
ILUMINATY - OS Surface Signals
==============================
Pragmatic OS-first helpers:
- Notification feed (watchdog + audio interrupts)
- Dialog detection from current visual context
- System tray state probe (Windows)
"""

from __future__ import annotations

import ctypes
import logging
import re
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)


_DIALOG_KEYWORDS = (
    "dialog",
    "mensaje",
    "message",
    "warning",
    "error",
    "confirm",
    "confirmation",
    "are you sure",
    "alert",
    "failed",
    "retry",
)

_AFFORDANCE_PATTERNS = (
    r"\bok\b",
    r"\baccept\b",
    r"\bconfirm\b",
    r"\bcontinue\b",
    r"\byes\b",
    r"\ballow\b",
    r"\bsave\b",
    r"\bretry\b",
    r"\bcancel\b",
    r"\bno\b",
    r"\bdeny\b",
    r"\bdiscard\b",
    r"\bclose\b",
)


def _extract_affordances(text: str) -> list[str]:
    lower = str(text or "").lower()
    found = []
    for pat in _AFFORDANCE_PATTERNS:
        m = re.search(pat, lower)
        if not m:
            continue
        label = m.group(0).strip().lower()
        if label and label not in found:
            found.append(label)
    return found[:12]


class OSSurfaceSignals:
    """Collects OS-facing signals without heavyweight platform dependencies."""

    def __init__(self, *, watchdog=None, audio_interrupt=None):
        self._watchdog = watchdog
        self._audio_interrupt = audio_interrupt

    def set_layers(self, *, watchdog=None, audio_interrupt=None) -> None:
        if watchdog is not None:
            self._watchdog = watchdog
        if audio_interrupt is not None:
            self._audio_interrupt = audio_interrupt

    def notifications(self, limit: int = 20) -> dict:
        items = []
        now = time.time()

        if self._watchdog:
            try:
                alerts = self._watchdog.get_alerts(count=max(1, int(limit)), unacknowledged_only=False) or []
            except Exception:
                alerts = []
            for alert in alerts:
                ts = float(alert.get("timestamp", now))
                items.append(
                    {
                        "source": "watchdog",
                        "timestamp": ts,
                        "timestamp_ms": int(ts * 1000),
                        "severity": str(alert.get("severity", "warning")),
                        "message": str(alert.get("message", ""))[:220],
                        "trigger": str(alert.get("trigger", ""))[:80],
                    }
                )

        if self._audio_interrupt:
            try:
                events = self._audio_interrupt.recent_events(limit=max(1, int(limit))) or []
            except Exception:
                events = []
            for evt in events:
                ts = float(evt.get("timestamp", now))
                items.append(
                    {
                        "source": "audio_interrupt",
                        "timestamp": ts,
                        "timestamp_ms": int(ts * 1000),
                        "severity": "warning",
                        "message": str(evt.get("text", evt.get("kind", "audio_interrupt")))[:220],
                        "kind": str(evt.get("kind", "unknown")),
                    }
                )

        items.sort(key=lambda x: float(x.get("timestamp", 0.0)), reverse=True)
        items = items[: max(1, int(limit))]
        for item in items:
            item.pop("timestamp", None)
        return {
            "count": len(items),
            "items": items,
            "sources": sorted({str(i.get("source")) for i in items}),
        }

    def tray_state(self) -> dict:
        if sys.platform != "win32":
            return {
                "supported": False,
                "platform": sys.platform,
                "detected": False,
                "windows": [],
            }

        tray_windows = []
        try:
            user32 = ctypes.windll.user32

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            for cls_name in ("Shell_TrayWnd", "NotifyIconOverflowWindow"):
                hwnd = user32.FindWindowW(cls_name, None)
                if not hwnd:
                    continue
                rect = RECT()
                ok = user32.GetWindowRect(hwnd, ctypes.byref(rect))
                if not ok:
                    continue
                tray_windows.append(
                    {
                        "class_name": cls_name,
                        "handle": int(hwnd),
                        "x": int(rect.left),
                        "y": int(rect.top),
                        "width": int(rect.right - rect.left),
                        "height": int(rect.bottom - rect.top),
                    }
                )
        except Exception as e:
            logger.debug("Tray probe failed: %s", e)

        return {
            "supported": True,
            "platform": "win32",
            "detected": bool(tray_windows),
            "windows": tray_windows,
        }

    def detect_dialog(self, *, slot, vision=None, active_title: str = "") -> dict:
        """
        Detect whether the current frame/title likely represents a blocking dialog.
        Returns lightweight semantic clues and affordances.
        """
        title_norm = str(active_title or "").strip().lower()
        title_hit = any(k in title_norm for k in _DIALOG_KEYWORDS)

        ocr_text = ""
        ocr_blocks = []
        if slot is not None and vision is not None:
            try:
                result = vision.ocr.extract_text(slot.frame_bytes, frame_hash=getattr(slot, "phash", None))
                ocr_text = str(result.get("text", ""))
                ocr_blocks = result.get("blocks", []) or []
            except Exception as e:
                logger.debug("Dialog OCR detect failed: %s", e)

        text_norm = ocr_text.lower()
        text_hits = [k for k in _DIALOG_KEYWORDS if k in text_norm]
        affordances = _extract_affordances(ocr_text)

        detected = bool(title_hit or text_hits or affordances)
        confidence = 0.0
        if detected:
            confidence = 0.5
            if title_hit:
                confidence += 0.2
            if text_hits:
                confidence += 0.2
            if affordances:
                confidence += 0.1
        confidence = max(0.0, min(1.0, confidence))

        return {
            "detected": detected,
            "confidence": round(confidence, 2),
            "active_title": str(active_title or "")[:180],
            "title_hit": bool(title_hit),
            "keyword_hits": text_hits[:8],
            "affordances": affordances,
            "ocr_preview": str(ocr_text or "")[:500],
            "ocr_block_count": len(ocr_blocks),
        }

