"""Risk manager — position sizing, stop-loss, drawdown enforcement."""
from __future__ import annotations

import time

from .config import TradingConfig
from .state import StateManager


class RiskManager:
    """Enforces hard risk limits. Cannot be overridden by strategies."""

    def __init__(self, config: TradingConfig, state: StateManager):
        self._config = config
        self._state = state

    def can_open_position(self) -> tuple[bool, str]:
        """Check if opening a new position is allowed."""
        positions = self._state.get_open_positions()

        # Max concurrent positions
        if len(positions) >= self._config.max_concurrent_positions:
            return False, f"max_positions_reached ({len(positions)}/{self._config.max_concurrent_positions})"

        # Max drawdown check
        ok, drawdown = self.check_drawdown()
        if not ok:
            return False, f"max_drawdown_breached ({drawdown:.2%} >= {self._config.max_drawdown_pct:.2%})"

        # Daily loss limit
        pnl_today = self._state.get_pnl("day")
        if pnl_today["total_pnl"] < 0:
            # Approximate daily loss as % (we'd need starting balance for exact)
            if pnl_today["losses"] >= 5 and pnl_today["win_rate"] < 0.3:
                return False, "daily_loss_pattern (5+ losses, <30% win rate today)"

        return True, "ok"

    def check_drawdown(self) -> tuple[bool, float]:
        """Check if current drawdown is within limits.

        Returns (is_ok, current_drawdown_pct).
        """
        pnl = self._state.get_pnl("all")
        # Simple peak-to-trough using trade history
        trades = self._state.get_trade_history(limit=500)
        if not trades:
            return True, 0.0

        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for t in reversed(trades):  # oldest first
            cumulative += t.pnl
            peak = max(peak, cumulative)
            drawdown = (peak - cumulative) / peak if peak > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)

        return max_drawdown < self._config.max_drawdown_pct, max_drawdown

    def calculate_position_size(
        self, balance: float, entry_price: float, stop_price: float
    ) -> float:
        """Calculate position size based on risk per trade.

        Uses fixed-fractional method: risk = max_position_pct * balance.
        """
        risk_amount = balance * self._config.max_position_pct
        risk_per_unit = abs(entry_price - stop_price)
        if risk_per_unit <= 0:
            return 0.0
        size = risk_amount / risk_per_unit
        # Cap at risk amount / entry_price as max
        max_size = risk_amount / entry_price if entry_price > 0 else 0
        return min(size, max_size)

    def calculate_stop_loss(self, entry_price: float, side: str) -> float:
        """Calculate default stop-loss price."""
        if side == "buy":
            return round(entry_price * (1 - self._config.stop_loss_pct), 8)
        return round(entry_price * (1 + self._config.stop_loss_pct), 8)

    def calculate_take_profit(self, entry_price: float, side: str) -> float:
        """Calculate default take-profit price."""
        if side == "buy":
            return round(entry_price * (1 + self._config.take_profit_pct), 8)
        return round(entry_price * (1 - self._config.take_profit_pct), 8)

    def max_loss_per_trade(self, balance: float) -> float:
        return balance * self._config.max_position_pct
