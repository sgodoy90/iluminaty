"""Visual reader — extract trading indicators from TradingView via iluminaty OCR."""
from __future__ import annotations

import logging
import re
import time
from typing import Any

log = logging.getLogger("iluminaty.trading.visual")

# Regex patterns for extracting indicator values from OCR text
_PRICE_RE = re.compile(r"[\d,]+\.?\d*")
_RSI_RE = re.compile(r"RSI[:\s]*(\d+\.?\d*)", re.IGNORECASE)
_MACD_RE = re.compile(r"MACD[:\s]*([-\d.]+)", re.IGNORECASE)
_MACD_SIGNAL_RE = re.compile(r"Signal[:\s]*([-\d.]+)", re.IGNORECASE)
_MACD_HIST_RE = re.compile(r"Hist(?:ogram)?[:\s]*([-\d.]+)", re.IGNORECASE)
_BB_UPPER_RE = re.compile(r"(?:Upper|UB)[:\s]*([\d,]+\.?\d*)", re.IGNORECASE)
_BB_MIDDLE_RE = re.compile(r"(?:Middle|MB|Basis)[:\s]*([\d,]+\.?\d*)", re.IGNORECASE)
_BB_LOWER_RE = re.compile(r"(?:Lower|LB)[:\s]*([\d,]+\.?\d*)", re.IGNORECASE)
_VOLUME_RE = re.compile(r"Vol(?:ume)?[:\s]*([\d.]+[KMB]?)", re.IGNORECASE)
_EMA_RE = re.compile(r"EMA\s*\(?(\d+)\)?[:\s]*([\d,]+\.?\d*)", re.IGNORECASE)


def _parse_number(text: str) -> float | None:
    """Parse a number string, handling commas."""
    try:
        return float(text.replace(",", ""))
    except (ValueError, TypeError):
        return None


class VisualReader:
    """Read TradingView indicators via iluminaty's internal perception."""

    def __init__(self, server_state=None):
        self._state = server_state
        self._last_ocr: str = ""
        self._last_read: float = 0
        self._cache_ttl: float = 1.0  # seconds

    def _get_ocr_text(self, monitor_id: int | None = None) -> str:
        """Get OCR text from iluminaty's perception engine."""
        now = time.time()
        if now - self._last_read < self._cache_ttl and self._last_ocr:
            return self._last_ocr

        if self._state is None:
            return ""

        # Try perception engine first (has cached OCR)
        if self._state.perception:
            try:
                ctx = self._state.perception.get_context(monitor_id=monitor_id)
                if ctx and hasattr(ctx, "ocr_text") and ctx.ocr_text:
                    self._last_ocr = ctx.ocr_text
                    self._last_read = now
                    return self._last_ocr
            except Exception:
                pass

        # Fallback: try fast OCR directly
        try:
            from iluminaty.fast_ocr import get_fast_ocr
            ocr = get_fast_ocr()
            if ocr and self._state.buffer:
                frame = self._state.buffer.latest(monitor_id=monitor_id)
                if frame and hasattr(frame, "jpeg"):
                    result = ocr.read_bytes(frame.jpeg)
                    if result:
                        self._last_ocr = " ".join(
                            r.get("text", "") for r in result if r.get("text")
                        )
                        self._last_read = now
                        return self._last_ocr
        except Exception:
            pass

        return self._last_ocr

    def read_current_price(self, monitor_id: int | None = None) -> float | None:
        """Read the current price from TradingView screen."""
        text = self._get_ocr_text(monitor_id)
        if not text:
            return None

        # Look for price-like patterns (large numbers with decimals)
        numbers = _PRICE_RE.findall(text)
        candidates = []
        for n in numbers:
            val = _parse_number(n)
            if val and val > 1.0:  # Filter tiny numbers
                candidates.append(val)

        if not candidates:
            return None

        # The largest number is likely the price (for crypto)
        return max(candidates)

    def read_indicator(
        self, name: str, monitor_id: int | None = None
    ) -> dict[str, Any] | None:
        """Read a specific indicator value from OCR text."""
        text = self._get_ocr_text(monitor_id)
        if not text:
            return None

        name_lower = name.lower()

        if name_lower == "rsi":
            m = _RSI_RE.search(text)
            if m:
                return {"rsi": float(m.group(1))}

        elif name_lower == "macd":
            result = {}
            m = _MACD_RE.search(text)
            if m:
                result["macd"] = float(m.group(1))
            m = _MACD_SIGNAL_RE.search(text)
            if m:
                result["signal"] = float(m.group(1))
            m = _MACD_HIST_RE.search(text)
            if m:
                result["histogram"] = float(m.group(1))
            return result or None

        elif name_lower == "bollinger":
            result = {}
            m = _BB_UPPER_RE.search(text)
            if m:
                result["upper"] = _parse_number(m.group(1))
            m = _BB_MIDDLE_RE.search(text)
            if m:
                result["middle"] = _parse_number(m.group(1))
            m = _BB_LOWER_RE.search(text)
            if m:
                result["lower"] = _parse_number(m.group(1))
            return result or None

        elif name_lower.startswith("ema"):
            results = {}
            for m in _EMA_RE.finditer(text):
                period = int(m.group(1))
                value = _parse_number(m.group(2))
                if value:
                    results[f"ema_{period}"] = value
            return results or None

        elif name_lower == "volume":
            m = _VOLUME_RE.search(text)
            if m:
                raw = m.group(1)
                multiplier = 1
                if raw.endswith("K"):
                    multiplier = 1000
                    raw = raw[:-1]
                elif raw.endswith("M"):
                    multiplier = 1_000_000
                    raw = raw[:-1]
                elif raw.endswith("B"):
                    multiplier = 1_000_000_000
                    raw = raw[:-1]
                val = _parse_number(raw)
                if val:
                    return {"volume": val * multiplier}

        return None

    def read_all_indicators(
        self, monitor_id: int | None = None
    ) -> dict[str, Any]:
        """Read all detectable indicators from TradingView."""
        result: dict[str, Any] = {}

        price = self.read_current_price(monitor_id)
        if price:
            result["price"] = price

        for ind_name in ("rsi", "macd", "bollinger", "ema", "volume"):
            data = self.read_indicator(ind_name, monitor_id)
            if data:
                result.update(data)

        return result

    def detect_chart_pattern(
        self, monitor_id: int | None = None
    ) -> list[str]:
        """Detect simple patterns from OCR text (alert-based)."""
        text = self._get_ocr_text(monitor_id).lower()
        patterns = []

        pattern_keywords = {
            "double_top": ["double top"],
            "double_bottom": ["double bottom"],
            "head_shoulders": ["head and shoulders", "h&s"],
            "triangle": ["triangle", "ascending triangle", "descending triangle"],
            "wedge": ["wedge", "rising wedge", "falling wedge"],
            "flag": ["bull flag", "bear flag"],
            "breakout": ["breakout", "break out"],
            "breakdown": ["breakdown", "break down"],
        }

        for pattern_name, keywords in pattern_keywords.items():
            if any(kw in text for kw in keywords):
                patterns.append(pattern_name)

        return patterns

    def verify_order_on_screen(
        self, text_hint: str = "filled", timeout: float = 10.0, monitor_id: int | None = None
    ) -> bool:
        """Verify an order was filled by watching for text on screen."""
        if not self._state or not self._state.watch_engine:
            return False

        try:
            result = self._state.watch_engine.wait(
                condition="text_visible",
                timeout=timeout,
                text=text_hint,
                monitor_id=monitor_id,
            )
            return result.triggered
        except Exception as e:
            log.warning("Order verification failed: %s", e)
            return False
