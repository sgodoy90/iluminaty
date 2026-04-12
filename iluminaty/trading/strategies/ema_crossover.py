"""EMA crossover strategy — fast EMA crosses slow EMA."""
from __future__ import annotations

from typing import Any

from ..models import Signal, Direction
from ..strategy_base import BaseStrategy, StrategyRegistry


def _ema(data: list[float], period: int) -> list[float]:
    """Compute Exponential Moving Average."""
    if len(data) < period:
        return []
    multiplier = 2.0 / (period + 1)
    ema_values = [sum(data[:period]) / period]
    for price in data[period:]:
        ema_values.append((price - ema_values[-1]) * multiplier + ema_values[-1])
    return ema_values


@StrategyRegistry.register("ema_crossover")
class EMACrossoverStrategy(BaseStrategy):
    """EMA 9/21 crossover with optional visual confirmation."""

    name = "ema_crossover"
    timeframes = ["1h", "4h"]
    required_indicators = ["ema_9", "ema_21"]

    def __init__(self, fast_period: int = 9, slow_period: int = 21):
        self.fast_period = fast_period
        self.slow_period = slow_period

    def evaluate(
        self, market_data: dict[str, Any], visual_data: dict[str, Any] | None = None
    ) -> Signal:
        ohlcv = market_data.get("ohlcv", [])
        if len(ohlcv) < self.slow_period + 2:
            return Signal(
                direction=Direction.HOLD,
                confidence=0.0,
                source=self.name,
                reason="insufficient_data",
                symbol=market_data.get("symbol", ""),
            )

        closes = [c["close"] for c in ohlcv]
        fast_ema = _ema(closes, self.fast_period)
        slow_ema = _ema(closes, self.slow_period)

        # Align arrays (slow EMA starts later)
        offset = self.slow_period - self.fast_period
        fast_aligned = fast_ema[offset:] if offset > 0 else fast_ema
        slow_aligned = slow_ema

        min_len = min(len(fast_aligned), len(slow_aligned))
        if min_len < 2:
            return Signal(
                direction=Direction.HOLD, confidence=0.0,
                source=self.name, reason="insufficient_ema_data",
                symbol=market_data.get("symbol", ""),
            )

        prev_fast = fast_aligned[-2]
        prev_slow = slow_aligned[-2]
        curr_fast = fast_aligned[-1]
        curr_slow = slow_aligned[-1]

        # Crossover detection
        bullish_cross = prev_fast <= prev_slow and curr_fast > curr_slow
        bearish_cross = prev_fast >= prev_slow and curr_fast < curr_slow

        if not bullish_cross and not bearish_cross:
            return Signal(
                direction=Direction.HOLD, confidence=0.3,
                source=self.name, reason="no_crossover",
                symbol=market_data.get("symbol", ""),
            )

        # Base confidence from EMA separation strength
        separation = abs(curr_fast - curr_slow) / curr_slow if curr_slow else 0
        confidence = min(0.5 + separation * 50, 0.85)

        # Visual confirmation boost
        if visual_data:
            visual_rsi = visual_data.get("rsi")
            if visual_rsi is not None:
                if bullish_cross and visual_rsi < 40:
                    confidence = min(confidence + 0.1, 0.95)
                elif bearish_cross and visual_rsi > 60:
                    confidence = min(confidence + 0.1, 0.95)

        direction = Direction.LONG if bullish_cross else Direction.SHORT
        reason = f"{'bullish' if bullish_cross else 'bearish'}_cross " \
                 f"fast={curr_fast:.2f} slow={curr_slow:.2f}"

        return Signal(
            direction=direction,
            confidence=round(confidence, 4),
            source=self.name,
            reason=reason,
            symbol=market_data.get("symbol", ""),
            metadata={"fast_ema": curr_fast, "slow_ema": curr_slow},
        )

    def backtest(self, historical_data: list[dict]) -> dict:
        if len(historical_data) < self.slow_period + 2:
            return {"error": "insufficient_data", "trades": 0}

        closes = [c["close"] for c in historical_data]
        fast_ema = _ema(closes, self.fast_period)
        slow_ema = _ema(closes, self.slow_period)

        offset = self.slow_period - self.fast_period
        fast_aligned = fast_ema[offset:]
        min_len = min(len(fast_aligned), len(slow_ema))
        start_idx = len(closes) - min_len

        trades: list[dict] = []
        position = None  # {"side": "long"|"short", "entry": price, "idx": i}

        for i in range(1, min_len):
            pf, ps = fast_aligned[i - 1], slow_ema[i - 1]
            cf, cs = fast_aligned[i], slow_ema[i]
            price = closes[start_idx + i]
            bullish = pf <= ps and cf > cs
            bearish = pf >= ps and cf < cs

            if position is None:
                if bullish:
                    position = {"side": "long", "entry": price, "idx": start_idx + i}
                elif bearish:
                    position = {"side": "short", "entry": price, "idx": start_idx + i}
            else:
                close_trade = False
                if position["side"] == "long" and bearish:
                    close_trade = True
                elif position["side"] == "short" and bullish:
                    close_trade = True

                if close_trade:
                    pnl = (price - position["entry"]) if position["side"] == "long" \
                        else (position["entry"] - price)
                    trades.append({
                        "side": position["side"],
                        "entry": position["entry"],
                        "exit": price,
                        "pnl": round(pnl, 4),
                        "pnl_pct": round(pnl / position["entry"] * 100, 2),
                    })
                    # Open reverse position
                    position = {
                        "side": "long" if bullish else "short",
                        "entry": price,
                        "idx": start_idx + i,
                    }

        wins = [t for t in trades if t["pnl"] > 0]
        total_pnl = sum(t["pnl"] for t in trades)
        return {
            "strategy": self.name,
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(trades) - len(wins),
            "win_rate": round(len(wins) / len(trades), 4) if trades else 0,
            "total_pnl": round(total_pnl, 4),
            "avg_pnl_pct": round(sum(t["pnl_pct"] for t in trades) / len(trades), 2) if trades else 0,
            "trades": trades[-10:],  # last 10 for preview
        }
