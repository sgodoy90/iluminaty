"""RSI divergence strategy — overbought/oversold with divergence detection."""
from __future__ import annotations

from typing import Any

from ..models import Signal, Direction
from ..strategy_base import BaseStrategy, StrategyRegistry


def _rsi(closes: list[float], period: int = 14) -> list[float]:
    """Compute RSI values."""
    if len(closes) < period + 1:
        return []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else 100
        rsi_values.append(100 - (100 / (1 + rs)))
    return rsi_values


@StrategyRegistry.register("rsi_divergence")
class RSIDivergenceStrategy(BaseStrategy):
    """RSI overbought/oversold with price-RSI divergence detection."""

    name = "rsi_divergence"
    timeframes = ["1h", "4h"]
    required_indicators = ["rsi"]

    def __init__(
        self,
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        lookback: int = 5,
    ):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.lookback = lookback

    def evaluate(
        self, market_data: dict[str, Any], visual_data: dict[str, Any] | None = None
    ) -> Signal:
        ohlcv = market_data.get("ohlcv", [])
        symbol = market_data.get("symbol", "")
        if len(ohlcv) < self.period + self.lookback + 2:
            return Signal(direction=Direction.HOLD, confidence=0.0,
                          source=self.name, reason="insufficient_data", symbol=symbol)

        closes = [c["close"] for c in ohlcv]
        rsi_values = _rsi(closes, self.period)
        if len(rsi_values) < self.lookback + 1:
            return Signal(direction=Direction.HOLD, confidence=0.0,
                          source=self.name, reason="insufficient_rsi", symbol=symbol)

        current_rsi = rsi_values[-1]
        recent_rsi = rsi_values[-self.lookback - 1:-1]
        recent_closes = closes[-self.lookback - 1:-1]
        current_close = closes[-1]

        # Use visual RSI if available (higher accuracy than calculated)
        if visual_data and visual_data.get("rsi") is not None:
            current_rsi = visual_data["rsi"]

        # Bullish divergence: price making lower lows but RSI making higher lows
        min_price_idx = recent_closes.index(min(recent_closes))
        min_rsi_idx = recent_rsi.index(min(recent_rsi))
        bullish_div = (
            current_close < recent_closes[min_price_idx]
            and current_rsi > recent_rsi[min_rsi_idx]
            and current_rsi < self.oversold + 10
        )

        # Bearish divergence: price making higher highs but RSI making lower highs
        max_price_idx = recent_closes.index(max(recent_closes))
        max_rsi_idx = recent_rsi.index(max(recent_rsi))
        bearish_div = (
            current_close > recent_closes[max_price_idx]
            and current_rsi < recent_rsi[max_rsi_idx]
            and current_rsi > self.overbought - 10
        )

        # Simple overbought/oversold
        oversold_signal = current_rsi < self.oversold
        overbought_signal = current_rsi > self.overbought

        if bullish_div:
            return Signal(
                direction=Direction.LONG, confidence=0.80,
                source=self.name, reason=f"bullish_divergence rsi={current_rsi:.1f}",
                symbol=symbol, metadata={"rsi": current_rsi},
            )
        elif bearish_div:
            return Signal(
                direction=Direction.SHORT, confidence=0.80,
                source=self.name, reason=f"bearish_divergence rsi={current_rsi:.1f}",
                symbol=symbol, metadata={"rsi": current_rsi},
            )
        elif oversold_signal:
            confidence = 0.5 + (self.oversold - current_rsi) / 100
            return Signal(
                direction=Direction.LONG, confidence=min(confidence, 0.70),
                source=self.name, reason=f"oversold rsi={current_rsi:.1f}",
                symbol=symbol, metadata={"rsi": current_rsi},
            )
        elif overbought_signal:
            confidence = 0.5 + (current_rsi - self.overbought) / 100
            return Signal(
                direction=Direction.SHORT, confidence=min(confidence, 0.70),
                source=self.name, reason=f"overbought rsi={current_rsi:.1f}",
                symbol=symbol, metadata={"rsi": current_rsi},
            )

        return Signal(
            direction=Direction.HOLD, confidence=0.3,
            source=self.name, reason=f"neutral rsi={current_rsi:.1f}",
            symbol=symbol, metadata={"rsi": current_rsi},
        )

    def backtest(self, historical_data: list[dict]) -> dict:
        closes = [c["close"] for c in historical_data]
        rsi_values = _rsi(closes, self.period)
        if len(rsi_values) < 2:
            return {"error": "insufficient_data", "trades": 0}

        trades: list[dict] = []
        position = None
        start = len(closes) - len(rsi_values)

        for i, rsi_val in enumerate(rsi_values):
            price = closes[start + i]
            if position is None:
                if rsi_val < self.oversold:
                    position = {"side": "long", "entry": price}
                elif rsi_val > self.overbought:
                    position = {"side": "short", "entry": price}
            else:
                close_it = False
                if position["side"] == "long" and rsi_val > self.overbought:
                    close_it = True
                elif position["side"] == "short" and rsi_val < self.oversold:
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
