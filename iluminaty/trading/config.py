"""Trading configuration — all secrets from env vars, never logged."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field


def _load_dotenv_once() -> None:
    """Load .env from the project root into os.environ if TRADING_API_KEY is not set."""
    if os.environ.get("TRADING_API_KEY"):
        return  # already set — nothing to do
    # Walk up from this file to find .env
    here = pathlib.Path(__file__).resolve()
    for parent in [here.parent.parent.parent, here.parent.parent, here.parent]:
        env_file = parent / ".env"
        if env_file.exists():
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
            break


def _auto_dotenv_enabled() -> bool:
    """
    Auto-load .env is opt-in to keep from_env deterministic in test/prod
    environments where secrets should only come from explicit TRADING_* vars.
    """
    raw = str(os.environ.get("TRADING_AUTO_DOTENV", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


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
    tv_monitor_id: int | None = None   # monitor where TradingView is open
    visual_weight: float = 0.7         # visual strategy weight vs API (0-1)
    require_chart: bool = False        # if True, skip cycle when TV not visible
    # Engine
    poll_interval_s: float = 5.0
    # State
    db_path: str | None = None          # None = in-memory SQLite

    @classmethod
    def from_env(cls) -> TradingConfig:
        """Load config from TRADING_* environment variables."""
        if _auto_dotenv_enabled():
            _load_dotenv_once()
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
            tv_monitor_id=int(os.environ["TRADING_TV_MONITOR"]) if os.environ.get("TRADING_TV_MONITOR") else None,
            visual_weight=float(os.environ.get("TRADING_VISUAL_WEIGHT", "0.7")),
            require_chart=os.environ.get("TRADING_REQUIRE_CHART", "0") == "1",
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
            "tv_monitor_id": self.tv_monitor_id,
            "visual_weight": self.visual_weight,
            "require_chart": self.require_chart,
            "poll_interval_s": self.poll_interval_s,
        }
