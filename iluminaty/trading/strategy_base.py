"""Strategy framework — pluggable strategies with decorator registration."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import Signal


class BaseStrategy(ABC):
    """Abstract base for all trading strategies."""

    name: str = "unnamed"
    timeframes: list[str] = ["1h"]
    required_indicators: list[str] = []
    min_confidence: float = 0.6

    @abstractmethod
    def evaluate(
        self, market_data: dict[str, Any], visual_data: dict[str, Any] | None = None
    ) -> Signal:
        """Evaluate market + visual data and return a trading signal.

        Args:
            market_data: Keys: "ticker", "ohlcv", "orderbook" (from exchange API).
            visual_data: Keys: "price", "rsi", "macd", "bollinger", "patterns"
                         (from TradingView OCR). None if visual disabled.
        """
        ...

    @abstractmethod
    def backtest(self, historical_data: list[dict]) -> dict:
        """Run backtest on historical OHLCV data. Returns stats dict."""
        ...


class StrategyRegistry:
    """Decorator-based strategy registration."""

    _strategies: dict[str, type[BaseStrategy]] = {}

    @classmethod
    def register(cls, name: str):
        """Decorator: @StrategyRegistry.register("ema_crossover")"""
        def decorator(strategy_cls: type[BaseStrategy]):
            strategy_cls.name = name
            cls._strategies[name] = strategy_cls
            return strategy_cls
        return decorator

    @classmethod
    def get(cls, name: str) -> type[BaseStrategy]:
        if name not in cls._strategies:
            raise KeyError(
                f"Strategy '{name}' not registered. "
                f"Available: {list(cls._strategies.keys())}"
            )
        return cls._strategies[name]

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseStrategy:
        return cls.get(name)(**kwargs)

    @classmethod
    def list_all(cls) -> list[str]:
        return list(cls._strategies.keys())
