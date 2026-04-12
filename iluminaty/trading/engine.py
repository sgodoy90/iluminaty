"""Trading engine — central orchestrator for the hybrid trading bot."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from .alerts import AlertManager
from .config import TradingConfig
from .exchange import ExchangeClient
from .models import Direction, Order, OrderSide, OrderType, Position, Signal
from .risk import RiskManager
from .signals import SignalAggregator
from .state import StateManager
from .strategy_base import BaseStrategy, StrategyRegistry
from .visual_reader import VisualReader

log = logging.getLogger("iluminaty.trading.engine")


class TradingEngine:
    """Hybrid trading engine: API execution + visual analysis."""

    def __init__(self, config: TradingConfig | None = None, server_state=None):
        self.config = config or TradingConfig.from_env()
        self._server_state = server_state

        # Components
        self.exchange = ExchangeClient(self.config)
        self.state = StateManager(self.config.db_path)
        self.risk = RiskManager(self.config, self.state)
        self.aggregator = SignalAggregator(min_confidence=0.6)
        self.visual = VisualReader(server_state)
        self.alerts = AlertManager(
            watch_engine=server_state.watch_engine if server_state else None,
            exchange_client=self.exchange,
        )

        # Active strategies
        self._strategies: list[BaseStrategy] = []
        self._running = False
        self._loop_thread: threading.Thread | None = None
        self._cycle_count = 0
        self._last_cycle: float = 0
        self._last_signal: Signal | None = None
        self._errors: list[str] = []

    def _load_strategies(self):
        """Load strategies from config."""
        # Ensure strategies are registered by importing the package
        import iluminaty.trading.strategies  # noqa: F401

        self._strategies = []
        for name in self.config.strategies:
            try:
                strategy = StrategyRegistry.create(name)
                self._strategies.append(strategy)
                log.info("Loaded strategy: %s", name)
            except KeyError as e:
                log.warning("Strategy not found: %s (%s)", name, e)
                self._errors.append(f"strategy_not_found: {name}")

    async def start(self) -> dict:
        """Start the trading engine."""
        if self._running:
            return {"status": "already_running"}

        if not self.config.is_configured():
            return {"status": "error", "reason": "API keys not configured. Set TRADING_API_KEY and TRADING_API_SECRET env vars."}

        self._load_strategies()
        if not self._strategies:
            return {"status": "error", "reason": "No valid strategies loaded"}

        # Test exchange connection
        try:
            ticker = await self.exchange.get_ticker(self.config.default_symbol)
            log.info("Exchange connected. %s price: %s", self.config.default_symbol, ticker.get("last"))
        except Exception as e:
            return {"status": "error", "reason": f"Exchange connection failed: {e}"}

        self._running = True
        self._errors = []
        self.alerts.start_monitoring(interval=self.config.poll_interval_s)

        # Start the main loop in a background thread
        self._loop_thread = threading.Thread(
            target=self._run_loop, daemon=True, name="trading-engine"
        )
        self._loop_thread.start()

        return {
            "status": "started",
            "symbol": self.config.default_symbol,
            "strategies": [s.name for s in self._strategies],
            "testnet": self.config.testnet,
            "price": ticker.get("last"),
        }

    async def stop(self) -> dict:
        """Stop the trading engine gracefully."""
        if not self._running:
            return {"status": "already_stopped"}

        self._running = False
        self.alerts.stop_monitoring()
        if self._loop_thread:
            self._loop_thread.join(timeout=10)
            self._loop_thread = None

        self.exchange.close()
        return {
            "status": "stopped",
            "total_cycles": self._cycle_count,
            "positions_open": len(self.state.get_open_positions()),
        }

    def _run_loop(self):
        """Main trading loop (runs in background thread)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while self._running:
                try:
                    result = loop.run_until_complete(self.run_cycle())
                    if result.get("error"):
                        self._errors.append(result["error"])
                        if len(self._errors) > 10:
                            self._errors = self._errors[-10:]
                except Exception as e:
                    log.error("Cycle error: %s", e, exc_info=True)
                    self._errors.append(str(e))
                time.sleep(self.config.poll_interval_s)
        finally:
            loop.close()

    async def run_cycle(self) -> dict:
        """Single evaluation cycle: fetch → analyze → decide → execute."""
        self._cycle_count += 1
        self._last_cycle = time.time()
        symbol = self.config.default_symbol

        # 1. Fetch market data from exchange
        try:
            ticker = await self.exchange.get_ticker(symbol)
            ohlcv = await self.exchange.get_ohlcv(symbol, "1h", 100)
        except Exception as e:
            return {"error": f"data_fetch_failed: {e}"}

        market_data = {
            "symbol": symbol,
            "ticker": ticker,
            "ohlcv": ohlcv,
            "current_price": ticker.get("last", 0),
        }

        # 2. Read visual indicators from TradingView (if enabled)
        visual_data = None
        if self.config.visual_enabled:
            try:
                visual_data = self.visual.read_all_indicators()
                if visual_data:
                    log.debug("Visual data: %s", visual_data)
            except Exception as e:
                log.debug("Visual read skipped: %s", e)

        # 3. Check price alerts
        current_prices = {symbol: ticker.get("last", 0)}
        await self.alerts.check_price_alerts(current_prices)

        # 4. Evaluate all strategies
        signals: list[Signal] = []
        for strategy in self._strategies:
            try:
                signal = strategy.evaluate(market_data, visual_data)
                signal.symbol = symbol
                signals.append(signal)
                self.state.log_signal(signal)
            except Exception as e:
                log.warning("Strategy %s error: %s", strategy.name, e)

        # 5. Aggregate signals
        best_signal = self.aggregator.aggregate(signals)
        self._last_signal = best_signal

        if not best_signal or best_signal.direction == Direction.HOLD:
            return {
                "cycle": self._cycle_count,
                "price": ticker.get("last"),
                "signal": "hold",
                "strategies_evaluated": len(signals),
            }

        # 6. Check if we should close existing positions
        if best_signal.direction == Direction.CLOSE:
            return await self._close_positions(best_signal, ticker.get("last", 0))

        # 7. Check risk before opening
        can_open, reason = self.risk.can_open_position()
        if not can_open:
            log.info("Risk gate blocked: %s", reason)
            return {
                "cycle": self._cycle_count,
                "price": ticker.get("last"),
                "signal": best_signal.to_dict(),
                "action": "blocked",
                "reason": reason,
            }

        # 8. Check for conflicting positions
        positions = self.state.get_open_positions()
        entry_price = ticker.get("last", 0)
        side_needed = OrderSide.BUY if best_signal.direction == Direction.LONG else OrderSide.SELL

        # Close opposite positions first
        for pos in positions:
            if pos.symbol == symbol and pos.side != side_needed:
                await self._close_single_position(pos, entry_price, "signal_reversal")

        # Don't double up on same-direction positions
        same_side = [p for p in positions if p.symbol == symbol and p.side == side_needed]
        if same_side:
            return {
                "cycle": self._cycle_count,
                "price": entry_price,
                "signal": best_signal.to_dict(),
                "action": "skip",
                "reason": "already_positioned",
            }

        # 9. Execute trade
        return await self._execute_trade(best_signal, market_data)

    async def _execute_trade(self, signal: Signal, market_data: dict) -> dict:
        """Execute a trade based on the signal."""
        symbol = signal.symbol or self.config.default_symbol
        entry_price = market_data.get("current_price", 0)
        if not entry_price:
            return {"error": "no_price"}

        side = "buy" if signal.direction == Direction.LONG else "sell"
        stop_loss = self.risk.calculate_stop_loss(entry_price, side)
        take_profit = self.risk.calculate_take_profit(entry_price, side)

        # Get balance for position sizing
        try:
            balance = await self.exchange.get_balance()
            free_usd = balance.get("free_usd", 0)
        except Exception as e:
            return {"error": f"balance_fetch_failed: {e}"}

        amount = self.risk.calculate_position_size(free_usd, entry_price, stop_loss)
        if amount <= 0:
            return {"error": "position_size_zero", "balance": free_usd}

        # Place order
        try:
            order_result = await self.exchange.place_order(
                symbol=symbol,
                side=side,
                order_type="market",
                amount=amount,
            )
        except Exception as e:
            return {"error": f"order_failed: {e}"}

        filled_price = order_result.get("price") or entry_price
        filled_amount = order_result.get("filled") or amount

        # Record position
        position = Position(
            symbol=symbol,
            side=OrderSide(side),
            entry_price=filled_price,
            amount=filled_amount,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=signal.source,
        )
        self.state.record_position(position)

        # Visual verification (non-blocking)
        if self.config.visual_enabled:
            verified = self.visual.verify_order_on_screen(
                text_hint="filled", timeout=3.0
            )
            if verified:
                log.info("Order visually confirmed on screen")

        log.info(
            "TRADE EXECUTED: %s %s %.6f %s @ %.2f (SL: %.2f, TP: %.2f) [%s]",
            side, symbol, filled_amount, signal.direction.value,
            filled_price, stop_loss, take_profit, signal.source,
        )

        return {
            "cycle": self._cycle_count,
            "action": "trade_executed",
            "order": order_result,
            "position": position.to_dict(),
            "signal": signal.to_dict(),
        }

    async def _close_positions(self, signal: Signal, current_price: float) -> dict:
        """Close all positions for the signal's symbol."""
        positions = self.state.get_open_positions()
        symbol = signal.symbol or self.config.default_symbol
        closed = []
        for pos in positions:
            if pos.symbol == symbol:
                trade = await self._close_single_position(pos, current_price, signal.reason)
                if trade:
                    closed.append(trade.to_dict())
        return {
            "cycle": self._cycle_count,
            "action": "positions_closed",
            "closed": closed,
        }

    async def _close_single_position(self, pos: Position, exit_price: float, reason: str):
        """Close a single position."""
        close_side = "sell" if pos.side == OrderSide.BUY else "buy"
        try:
            await self.exchange.place_order(
                symbol=pos.symbol, side=close_side,
                order_type="market", amount=pos.amount,
            )
        except Exception as e:
            log.error("Failed to close position %s: %s", pos.id, e)
            return None

        trade = self.state.close_position(pos.id, exit_price, reason)
        if trade:
            log.info("Position closed: %s P&L=%.4f (%.2f%%)", pos.id, trade.pnl, trade.pnl_pct * 100)
        return trade

    async def execute_signal(self, signal: Signal) -> dict:
        """Manually execute a specific signal (for MCP tool use)."""
        if not self._running and not self.config.is_configured():
            return {"error": "engine_not_configured"}

        self.exchange._init_exchange()
        ticker = await self.exchange.get_ticker(signal.symbol or self.config.default_symbol)
        market_data = {
            "symbol": signal.symbol or self.config.default_symbol,
            "current_price": ticker.get("last", 0),
            "ticker": ticker,
        }
        return await self._execute_trade(signal, market_data)

    def get_status(self) -> dict:
        """Get current engine status."""
        positions = self.state.get_open_positions()
        stats = self.state.get_stats()
        return {
            "running": self._running,
            "config": self.config.safe_repr(),
            "cycle_count": self._cycle_count,
            "last_cycle": self._last_cycle,
            "last_signal": self._last_signal.to_dict() if self._last_signal else None,
            "strategies": [s.name for s in self._strategies],
            "available_strategies": StrategyRegistry.list_all(),
            "positions": [p.to_dict() for p in positions],
            "stats": stats,
            "alerts": self.alerts.get_active_alerts(),
            "errors": self._errors[-5:],
        }

    def is_running(self) -> bool:
        return self._running
