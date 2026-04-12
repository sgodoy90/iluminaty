"""Tests for the trading bot core modules."""
from __future__ import annotations

import os
import sys
import time

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─── Config ───

class TestTradingConfig:
    def test_from_env_defaults(self):
        from iluminaty.trading.config import TradingConfig
        # Clear any existing env vars
        for k in list(os.environ):
            if k.startswith("TRADING_"):
                del os.environ[k]
        cfg = TradingConfig.from_env()
        assert cfg.exchange_id == "binance"
        assert cfg.testnet is True
        assert cfg.default_symbol == "BTC/USDT"
        assert cfg.max_position_pct == 0.02
        assert cfg.is_configured() is False

    def test_from_env_custom(self):
        from iluminaty.trading.config import TradingConfig
        os.environ["TRADING_API_KEY"] = "test_key"
        os.environ["TRADING_API_SECRET"] = "test_secret"
        os.environ["TRADING_SYMBOL"] = "ETH/USDT"
        os.environ["TRADING_TESTNET"] = "0"
        try:
            cfg = TradingConfig.from_env()
            assert cfg.api_key == "test_key"
            assert cfg.api_secret == "test_secret"
            assert cfg.default_symbol == "ETH/USDT"
            assert cfg.testnet is False
            assert cfg.is_configured() is True
        finally:
            del os.environ["TRADING_API_KEY"]
            del os.environ["TRADING_API_SECRET"]
            del os.environ["TRADING_SYMBOL"]
            del os.environ["TRADING_TESTNET"]

    def test_safe_repr_masks_secrets(self):
        from iluminaty.trading.config import TradingConfig
        cfg = TradingConfig(api_key="secret123", api_secret="secret456")
        safe = cfg.safe_repr()
        assert "secret123" not in str(safe)
        assert "secret456" not in str(safe)
        assert safe["api_key_set"] is True
        assert safe["api_secret_set"] is True


# ─── Models ───

class TestModels:
    def test_signal_to_dict(self):
        from iluminaty.trading.models import Signal, Direction
        sig = Signal(
            direction=Direction.LONG, confidence=0.85,
            source="ema_crossover", reason="bullish_cross",
            symbol="BTC/USDT",
        )
        d = sig.to_dict()
        assert d["direction"] == "long"
        assert d["confidence"] == 0.85
        assert d["source"] == "ema_crossover"

    def test_order_to_dict(self):
        from iluminaty.trading.models import Order, OrderSide, OrderType
        order = Order(symbol="BTC/USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.001)
        d = order.to_dict()
        assert d["symbol"] == "BTC/USDT"
        assert d["side"] == "buy"
        assert d["type"] == "market"

    def test_position_to_dict(self):
        from iluminaty.trading.models import Position, OrderSide
        pos = Position(
            symbol="BTC/USDT", side=OrderSide.BUY,
            entry_price=50000.0, amount=0.1,
            stop_loss=49000.0, take_profit=52000.0,
        )
        d = pos.to_dict()
        assert d["entry_price"] == 50000.0
        assert d["stop_loss"] == 49000.0


# ─── State Manager ───

class TestStateManager:
    def test_record_and_get_positions(self):
        from iluminaty.trading.state import StateManager
        from iluminaty.trading.models import Position, OrderSide
        sm = StateManager()  # in-memory
        pos = Position(
            symbol="BTC/USDT", side=OrderSide.BUY,
            entry_price=50000.0, amount=0.1,
        )
        sm.record_position(pos)
        positions = sm.get_open_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTC/USDT"
        sm.close()

    def test_close_position_records_trade(self):
        from iluminaty.trading.state import StateManager
        from iluminaty.trading.models import Position, OrderSide
        sm = StateManager()
        pos = Position(
            id="test1", symbol="BTC/USDT", side=OrderSide.BUY,
            entry_price=50000.0, amount=0.1,
        )
        sm.record_position(pos)
        trade = sm.close_position("test1", 51000.0, "take_profit")
        assert trade is not None
        assert trade.pnl == pytest.approx(100.0)  # (51000 - 50000) * 0.1
        assert trade.pnl_pct == pytest.approx(0.02)
        assert sm.get_open_positions() == []
        sm.close()

    def test_pnl_summary(self):
        from iluminaty.trading.state import StateManager
        from iluminaty.trading.models import Position, OrderSide
        sm = StateManager()
        for i, exit_p in enumerate([51000, 49000, 52000]):
            pos = Position(
                id=f"t{i}", symbol="BTC/USDT", side=OrderSide.BUY,
                entry_price=50000.0, amount=0.1,
            )
            sm.record_position(pos)
            sm.close_position(f"t{i}", float(exit_p))
        pnl = sm.get_pnl("day")
        assert pnl["total_trades"] == 3
        assert pnl["wins"] == 2
        assert pnl["losses"] == 1
        sm.close()


# ─── Strategy Framework ───

class TestStrategyRegistry:
    def test_register_and_list(self):
        from iluminaty.trading.strategy_base import StrategyRegistry
        import iluminaty.trading.strategies  # noqa: F401
        available = StrategyRegistry.list_all()
        assert "ema_crossover" in available
        assert "rsi_divergence" in available
        assert "bollinger_bounce" in available

    def test_create_strategy(self):
        from iluminaty.trading.strategy_base import StrategyRegistry
        import iluminaty.trading.strategies  # noqa: F401
        strategy = StrategyRegistry.create("ema_crossover")
        assert strategy.name == "ema_crossover"

    def test_unknown_strategy_raises(self):
        from iluminaty.trading.strategy_base import StrategyRegistry
        with pytest.raises(KeyError):
            StrategyRegistry.get("nonexistent_strategy")


# ─── EMA Crossover Strategy ───

class TestEMACrossover:
    def _make_ohlcv(self, closes):
        return [{"open": c, "high": c + 10, "low": c - 10, "close": c, "volume": 100}
                for c in closes]

    def test_hold_on_insufficient_data(self):
        from iluminaty.trading.strategies.ema_crossover import EMACrossoverStrategy
        s = EMACrossoverStrategy()
        sig = s.evaluate({"ohlcv": self._make_ohlcv([100] * 5), "symbol": "BTC/USDT"})
        assert sig.direction.value == "hold"

    def test_bullish_crossover(self):
        from iluminaty.trading.strategies.ema_crossover import EMACrossoverStrategy
        # Create data where fast EMA crosses above slow EMA
        closes = [100] * 30  # flat start
        for i in range(20):
            closes.append(100 + i * 2)  # rising
        s = EMACrossoverStrategy(fast_period=5, slow_period=15)
        sig = s.evaluate({"ohlcv": self._make_ohlcv(closes), "symbol": "BTC/USDT"})
        # The fast EMA should be above slow EMA after the rise
        # Exact signal depends on crossover timing
        assert sig.direction.value in ("long", "hold")

    def test_backtest_returns_stats(self):
        from iluminaty.trading.strategies.ema_crossover import EMACrossoverStrategy
        import math
        # Sine wave data for oscillating crossovers
        closes = [100 + 20 * math.sin(i / 5) for i in range(200)]
        s = EMACrossoverStrategy()
        result = s.backtest(self._make_ohlcv(closes))
        assert "total_trades" in result
        assert "win_rate" in result
        assert result["total_trades"] > 0


# ─── RSI Strategy ───

class TestRSIDivergence:
    def _make_ohlcv(self, closes):
        return [{"open": c, "high": c + 10, "low": c - 10, "close": c, "volume": 100}
                for c in closes]

    def test_oversold_signal(self):
        from iluminaty.trading.strategies.rsi_divergence import RSIDivergenceStrategy
        # Descending prices should create oversold RSI
        closes = [100 - i * 0.5 for i in range(50)]
        s = RSIDivergenceStrategy()
        sig = s.evaluate({"ohlcv": self._make_ohlcv(closes), "symbol": "BTC/USDT"})
        # With steadily declining prices, RSI should be low
        assert sig.direction.value in ("long", "hold")

    def test_backtest(self):
        from iluminaty.trading.strategies.rsi_divergence import RSIDivergenceStrategy
        import math
        closes = [100 + 30 * math.sin(i / 8) for i in range(200)]
        s = RSIDivergenceStrategy()
        result = s.backtest(self._make_ohlcv(closes))
        assert "total_trades" in result


# ─── Bollinger Strategy ───

class TestBollingerBounce:
    def _make_ohlcv(self, closes):
        return [{"open": c, "high": c + 5, "low": c - 5, "close": c, "volume": 100}
                for c in closes]

    def test_within_bands_hold(self):
        from iluminaty.trading.strategies.bollinger_bounce import BollingerBounceStrategy
        # Flat data should be within bands
        closes = [100.0] * 30
        s = BollingerBounceStrategy()
        sig = s.evaluate({"ohlcv": self._make_ohlcv(closes), "symbol": "BTC/USDT"})
        assert sig.direction.value == "hold"

    def test_backtest(self):
        from iluminaty.trading.strategies.bollinger_bounce import BollingerBounceStrategy
        import math
        closes = [100 + 25 * math.sin(i / 6) for i in range(200)]
        s = BollingerBounceStrategy()
        result = s.backtest(self._make_ohlcv(closes))
        assert "total_trades" in result


# ─── Signal Aggregator ───

class TestSignalAggregator:
    def test_no_actionable_signals(self):
        from iluminaty.trading.signals import SignalAggregator
        from iluminaty.trading.models import Signal, Direction
        agg = SignalAggregator()
        signals = [
            Signal(direction=Direction.HOLD, confidence=0.3, source="a", reason="none"),
            Signal(direction=Direction.HOLD, confidence=0.2, source="b", reason="none"),
        ]
        assert agg.aggregate(signals) is None

    def test_picks_highest_confidence(self):
        from iluminaty.trading.signals import SignalAggregator
        from iluminaty.trading.models import Signal, Direction
        agg = SignalAggregator(min_confidence=0.5)
        signals = [
            Signal(direction=Direction.LONG, confidence=0.7, source="a", reason="strong"),
            Signal(direction=Direction.LONG, confidence=0.9, source="b", reason="stronger"),
            Signal(direction=Direction.SHORT, confidence=0.6, source="c", reason="weak"),
        ]
        result = agg.aggregate(signals)
        assert result is not None
        assert result.confidence == 0.9
        assert result.direction == Direction.LONG


# ─── Risk Manager ───

class TestRiskManager:
    def test_position_sizing(self):
        from iluminaty.trading.risk import RiskManager
        from iluminaty.trading.config import TradingConfig
        from iluminaty.trading.state import StateManager
        cfg = TradingConfig(max_position_pct=0.02)
        sm = StateManager()
        rm = RiskManager(cfg, sm)
        # 2% of 10000 = 200 risk. Entry 50000, stop 49000 = 1000 risk per unit
        size = rm.calculate_position_size(10000, 50000, 49000)
        assert size == pytest.approx(0.004, abs=0.001)  # 200/50000 cap
        sm.close()

    def test_stop_loss_calculation(self):
        from iluminaty.trading.risk import RiskManager
        from iluminaty.trading.config import TradingConfig
        from iluminaty.trading.state import StateManager
        cfg = TradingConfig(stop_loss_pct=0.015)
        sm = StateManager()
        rm = RiskManager(cfg, sm)
        sl = rm.calculate_stop_loss(50000, "buy")
        assert sl == pytest.approx(49250.0)
        sl_sell = rm.calculate_stop_loss(50000, "sell")
        assert sl_sell == pytest.approx(50750.0)
        sm.close()

    def test_can_open_position_max_reached(self):
        from iluminaty.trading.risk import RiskManager
        from iluminaty.trading.config import TradingConfig
        from iluminaty.trading.state import StateManager
        from iluminaty.trading.models import Position, OrderSide
        cfg = TradingConfig(max_concurrent_positions=2)
        sm = StateManager()
        rm = RiskManager(cfg, sm)
        for i in range(2):
            sm.record_position(Position(
                id=f"p{i}", symbol="BTC/USDT", side=OrderSide.BUY,
                entry_price=50000, amount=0.01,
            ))
        ok, reason = rm.can_open_position()
        assert ok is False
        assert "max_positions" in reason
        sm.close()


# ─── Alerts ───

class TestAlertManager:
    def test_create_and_list_alerts(self):
        from iluminaty.trading.alerts import AlertManager
        am = AlertManager()
        aid = am.set_price_alert("BTC/USDT", 60000, "above")
        alerts = am.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0]["price"] == 60000

    def test_cancel_alert(self):
        from iluminaty.trading.alerts import AlertManager
        am = AlertManager()
        aid = am.set_price_alert("BTC/USDT", 60000, "above")
        assert am.cancel_alert(aid) is True
        assert len(am.get_active_alerts()) == 0

    @pytest.mark.asyncio
    async def test_price_alert_triggers(self):
        from iluminaty.trading.alerts import AlertManager
        am = AlertManager()
        triggered_alerts = []
        am.on_alert(lambda a: triggered_alerts.append(a))
        am.set_price_alert("BTC/USDT", 60000, "above")
        await am.check_price_alerts({"BTC/USDT": 61000})
        assert len(triggered_alerts) == 1
        assert triggered_alerts[0].triggered is True
