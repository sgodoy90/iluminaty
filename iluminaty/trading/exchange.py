"""Exchange client — ccxt wrapper for Binance (spot + futures)."""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .config import TradingConfig

log = logging.getLogger("iluminaty.trading.exchange")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ccxt")


class ExchangeClient:
    """Thin async wrapper around ccxt.binance."""

    def __init__(self, config: TradingConfig):
        self._config = config
        self._exchange = None

    def _init_exchange(self):
        if self._exchange is not None:
            return
        try:
            import ccxt
        except ImportError:
            raise RuntimeError(
                "ccxt is required for trading. Install with: "
                "pip install 'iluminaty[trading]'"
            )
        opts: dict[str, Any] = {
            "apiKey": self._config.api_key,
            "secret": self._config.api_secret,
            "enableRateLimit": True,
        }
        if self._config.market_type == "future":
            opts["options"] = {"defaultType": "future"}

        exchange_cls = getattr(ccxt, self._config.exchange_id, None)
        if exchange_cls is None:
            raise ValueError(f"Unknown exchange: {self._config.exchange_id}")

        self._exchange = exchange_cls(opts)
        if self._config.testnet:
            self._exchange.set_sandbox_mode(True)
            log.info("Exchange connected in TESTNET mode")
        else:
            log.info("Exchange connected in LIVE mode")

    def _run_sync(self, fn, *args, **kwargs):
        """Run a sync ccxt call in the thread pool."""
        self._init_exchange()
        return fn(*args, **kwargs)

    async def _run(self, method_name: str, *args, **kwargs) -> Any:
        self._init_exchange()
        method = getattr(self._exchange, method_name)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor,
            lambda: method(*args, **kwargs),
        )

    async def get_balance(self, currency: str | None = None) -> dict:
        balance = await self._run("fetch_balance")
        if currency:
            asset = balance.get(currency, {})
            return {
                "currency": currency,
                "free": asset.get("free", 0),
                "used": asset.get("used", 0),
                "total": asset.get("total", 0),
            }
        return {
            "total_usd": balance.get("total", {}).get("USDT", 0),
            "free_usd": balance.get("free", {}).get("USDT", 0),
            "assets": {
                k: v for k, v in balance.get("total", {}).items()
                if isinstance(v, (int, float)) and v > 0
            },
        }

    async def get_ticker(self, symbol: str) -> dict:
        t = await self._run("fetch_ticker", symbol)
        return {
            "symbol": symbol,
            "last": t.get("last"),
            "bid": t.get("bid"),
            "ask": t.get("ask"),
            "high": t.get("high"),
            "low": t.get("low"),
            "volume": t.get("baseVolume"),
            "change_pct": t.get("percentage"),
            "timestamp": t.get("timestamp"),
        }

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[dict]:
        raw = await self._run("fetch_ohlcv", symbol, timeframe, None, limit)
        return [
            {
                "timestamp": c[0],
                "open": c[1],
                "high": c[2],
                "low": c[3],
                "close": c[4],
                "volume": c[5],
            }
            for c in raw
        ]

    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        ob = await self._run("fetch_order_book", symbol, limit)
        return {
            "bids": ob.get("bids", [])[:limit],
            "asks": ob.get("asks", [])[:limit],
            "timestamp": ob.get("timestamp"),
        }

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str = "market",
        amount: float = 0.0,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict:
        log.info("Placing %s %s %s %.6f @ %s", order_type, side, symbol, amount, price or "market")
        result = await self._run(
            "create_order", symbol, order_type, side, amount, price, params or {}
        )
        return {
            "id": result.get("id"),
            "symbol": result.get("symbol"),
            "side": result.get("side"),
            "type": result.get("type"),
            "amount": result.get("amount"),
            "price": result.get("price"),
            "status": result.get("status"),
            "filled": result.get("filled"),
            "cost": result.get("cost"),
            "fee": result.get("fee"),
            "timestamp": result.get("timestamp"),
        }

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        log.info("Canceling order %s on %s", order_id, symbol)
        result = await self._run("cancel_order", order_id, symbol)
        return {
            "id": result.get("id"),
            "status": result.get("status"),
        }

    async def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        orders = await self._run("fetch_open_orders", symbol)
        return [
            {
                "id": o.get("id"),
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "type": o.get("type"),
                "amount": o.get("amount"),
                "price": o.get("price"),
                "status": o.get("status"),
                "timestamp": o.get("timestamp"),
            }
            for o in orders
        ]

    async def get_positions(self) -> list[dict]:
        """Fetch futures positions (empty list for spot)."""
        if self._config.market_type != "future":
            return []
        positions = await self._run("fetch_positions")
        return [
            {
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "contracts": p.get("contracts"),
                "entryPrice": p.get("entryPrice"),
                "unrealizedPnl": p.get("unrealizedPnl"),
                "leverage": p.get("leverage"),
            }
            for p in positions
            if p.get("contracts", 0) != 0
        ]

    def close(self):
        if self._exchange:
            try:
                self._exchange.close()
            except Exception:
                pass
            self._exchange = None
