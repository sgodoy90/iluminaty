"""Bollinger Band bounce strategy — mean reversion at band extremes."""
from __future__ import annotations

import math
from typing import Any

from ..models import Signal, Direction
from ..strategy_base import BaseStrategy, StrategyRegistry


def _sma(data: list[float], period: int) -> list[float]:
    if len(data) < period:
        return []
    return [sum(data[i:i + period]) / period for i in range(len(data) - period + 1)]


def _bollinger(closes: list[float], period: int = 20, num_std: float = 2.0):
    """Returns (upper, middle, lower) lists."""
    if len(closes) < period:
        return [], [], []
    middle = _sma(closes, period)
    upper, lower = [], []
    for i, m in enumerate(middle):
        window = closes[i:i + period]
        std = math.sqrt(sum((x - m) ** 2 for x in window) / period)
        upper.append(m + num_std * std)
        lower.append(m - num_std * std)
    return upper, middle, lower


@StrategyRegistry.register("bollinger_bounce")
class BollingerBounceStrategy(BaseStrategy):
    """Mean reversion: buy at lower band, sell at upper band."""

    name = "bollinger_bounce"
    timeframes = ["1h", "4h"]
    required_indicators = ["bollinger"]

    def __init__(self, period: int = 20, num_std: float = 2.0):
        self.period = period
        self.num_std = num_std

    def evaluate(
        self, market_data: dict[str, Any], visual_data: dict[str, Any] | None = None
    ) -> Signal:
        ohlcv = market_data.get("ohlcv", [])
        symbol = market_data.get("symbol", "")
        if len(ohlcv) < self.period + 2:
            return Signal(direction=Direction.HOLD, confidence=0.0,
                          source=self.name, reason="insufficient_data", symbol=symbol)

        closes = [c["close"] for c in ohlcv]
        upper, middle, lower = _bollinger(closes, self.period, self.num_std)
        if not upper:
            return Signal(direction=Direction.HOLD, confidence=0.0,
                          source=self.name, reason="no_bands", symbol=symbol)

        price = closes[-1]
        ub, mb, lb = upper[-1], middle[-1], lower[-1]
        band_width = ub - lb
        if band_width < 1e-9:
            return Signal(direction=Direction.HOLD, confidence=0.1,
                          source=self.name, reason="bands_flat", symbol=symbol)

        # Use visual Bollinger if available
        if visual_data and visual_data.get("bollinger"):
            vb = visual_data["bollinger"]
            if all(k in vb for k in ("upper", "middle", "lower")):
                ub, mb, lb = vb["upper"], vb["middle"], vb["lower"]
                band_width = ub - lb if ub != lb else 1

        # Position within bands: 0 = lower, 1 = upper
        band_position = (price - lb) / band_width if band_width else 0.5

        if band_position <= 0.05:
            # At or below lower band — buy signal
            confidence = min(0.6 + (0.05 - band_position) * 4, 0.85)
            return Signal(
                direction=Direction.LONG, confidence=round(confidence, 4),
                source=self.name,
                reason=f"lower_band_touch price={price:.2f} lb={lb:.2f}",
                symbol=symbol,
                metadata={"upper": ub, "middle": mb, "lower": lb, "band_pos": band_position},
            )
        elif band_position >= 0.95:
            # At or above upper band — sell signal
            confidence = min(0.6 + (band_position - 0.95) * 4, 0.85)
            return Signal(
                direction=Direction.SHORT, confidence=round(confidence, 4),
                source=self.name,
                reason=f"upper_band_touch price={price:.2f} ub={ub:.2f}",
                symbol=symbol,
                metadata={"upper": ub, "middle": mb, "lower": lb, "band_pos": band_position},
            )

        return Signal(
            direction=Direction.HOLD, confidence=0.2,
            source=self.name,
            reason=f"within_bands pos={band_position:.2f}",
            symbol=symbol,
            metadata={"upper": ub, "middle": mb, "lower": lb, "band_pos": band_position},
        )

    def backtest(self, historical_data: list[dict]) -> dict:
        closes = [c["close"] for c in historical_data]
        upper, middle, lower = _bollinger(closes, self.period, self.num_std)
        if not upper:
            return {"error": "insufficient_data", "trades": 0}

        trades: list[dict] = []
        position = None
        start = len(closes) - len(upper)

        for i in range(len(upper)):
            price = closes[start + i]
            bw = upper[i] - lower[i] if upper[i] != lower[i] else 1
            bp = (price - lower[i]) / bw

            if position is None:
                if bp <= 0.05:
                    position = {"side": "long", "entry": price}
                elif bp >= 0.95:
                    position = {"side": "short", "entry": price}
            else:
                close_it = False
                if position["side"] == "long" and bp >= 0.5:
                    close_it = True
                elif position["side"] == "short" and bp <= 0.5:
                    close_it = True
                if close_it:
                    pnl = (price - position["entry"]) if position["side"] == "long" \
                        else (position["entry"] - price)
                    trades.append({
                        "side": position["side"], "entry": position["entry"],
                        "exit": price, "pnl": round(pnl, 4),
                        "pnl_pct": round(pnl / position["entry"] * 100, 2),
                    })
                    position = None

        wins = [t for t in trades if t["pnl"] > 0]
        total_pnl = sum(t["pnl"] for t in trades)
        return {
            "strategy": self.name, "total_trades": len(trades),
            "wins": len(wins), "losses": len(trades) - len(wins),
            "win_rate": round(len(wins) / len(trades), 4) if trades else 0,
            "total_pnl": round(total_pnl, 4),
            "trades": trades[-10:],
        }
