"""Trading configuration — all secrets from env vars, never logged."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class TradingConfig:
    exchange_id: str = "binance"
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    default_symbol: str = "BTC/USDT"
    market_type: str = "spot"           # "spot" | "future"
    # Risk
    max_position_pct: float = 0.02      # 2% of balance per trade
    max_drawdown_pct: float = 0.05      # 5% max drawdown halt
    stop_loss_pct: float = 0.015        # 1.5% default stop
    take_profit_pct: float = 0.03       # 3% default take-profit
    max_concurrent_positions: int = 3
    daily_loss_limit_pct: float = 0.03  # 3% daily loss halt
    # Strategies
    strategies: list[str] = field(default_factory=lambda: ["ema_crossover"])
    # Visual
    visual_enabled: bool = True
    # Engine
    poll_interval_s: float = 5.0
    # State
    db_path: str | None = None          # None = in-memory SQLite

    @classmethod
    def from_env(cls) -> TradingConfig:
        """Load config from TRADING_* environment variables."""
        return cls(
            exchange_id=os.environ.get("TRADING_EXCHANGE", "binance"),
            api_key=os.environ.get("TRADING_API_KEY", ""),
            api_secret=os.environ.get("TRADING_API_SECRET", ""),
            testnet=os.environ.get("TRADING_TESTNET", "1") == "1",
            default_symbol=os.environ.get("TRADING_SYMBOL", "BTC/USDT"),
            market_type=os.environ.get("TRADING_MARKET", "spot"),
            max_position_pct=float(os.environ.get("TRADING_MAX_POSITION_PCT", "0.02")),
            max_drawdown_pct=float(os.environ.get("TRADING_MAX_DRAWDOWN_PCT", "0.05")),
            stop_loss_pct=float(os.environ.get("TRADING_STOP_LOSS_PCT", "0.015")),
            take_profit_pct=float(os.environ.get("TRADING_TAKE_PROFIT_PCT", "0.03")),
            max_concurrent_positions=int(os.environ.get("TRADING_MAX_POSITIONS", "3")),
            daily_loss_limit_pct=float(os.environ.get("TRADING_DAILY_LOSS_PCT", "0.03")),
            strategies=[s.strip() for s in os.environ.get("TRADING_STRATEGIES", "ema_crossover").split(",")],
            visual_enabled=os.environ.get("TRADING_VISUAL", "1") == "1",
            poll_interval_s=float(os.environ.get("TRADING_POLL_INTERVAL", "5.0")),
            db_path=os.environ.get("TRADING_DB_PATH"),
        )

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def safe_repr(self) -> dict:
        """Return config dict with secrets masked."""
        return {
            "exchange_id": self.exchange_id,
            "testnet": self.testnet,
            "default_symbol": self.default_symbol,
            "market_type": self.market_type,
            "api_key_set": bool(self.api_key),
            "api_secret_set": bool(self.api_secret),
            "max_position_pct": self.max_position_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "max_concurrent_positions": self.max_concurrent_positions,
            "strategies": self.strategies,
            "visual_enabled": self.visual_enabled,
            "poll_interval_s": self.poll_interval_s,
        }
