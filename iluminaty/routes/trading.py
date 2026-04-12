"""Route module — trading bot endpoints."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Query, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import iluminaty.server as _srv

router = APIRouter()
log = logging.getLogger("iluminaty.routes.trading")


def _auth(k):
    return _srv._check_auth(k)


def _get_engine():
    """Get or lazily create the trading engine."""
    if not hasattr(_srv._state, "trading_engine") or _srv._state.trading_engine is None:
        from iluminaty.trading.config import TradingConfig
        from iluminaty.trading.engine import TradingEngine
        config = TradingConfig.from_env()
        _srv._state.trading_engine = TradingEngine(config=config, server_state=_srv._state)
    return _srv._state.trading_engine


# ─── Status ───

@router.get("/trading/status")
async def trading_status(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    engine = _get_engine()
    return engine.get_status()


# ─── Start / Stop ───

class _StartBody(BaseModel):
    strategies: Optional[list[str]] = None
    symbol: Optional[str] = None

@router.post("/trading/start")
async def trading_start(
    body: _StartBody = _StartBody(),
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    engine = _get_engine()
    if body.strategies:
        engine.config.strategies = body.strategies
    if body.symbol:
        engine.config.default_symbol = body.symbol
    result = await engine.start()
    return result


@router.post("/trading/stop")
async def trading_stop(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    engine = _get_engine()
    return await engine.stop()


# ─── Balance & Positions ───

@router.get("/trading/balance")
async def trading_balance(
    currency: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    engine = _get_engine()
    try:
        return await engine.exchange.get_balance(currency)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/trading/positions")
async def trading_positions(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    engine = _get_engine()
    positions = engine.state.get_open_positions()
    return {"positions": [p.to_dict() for p in positions]}


@router.get("/trading/history")
async def trading_history(
    limit: int = Query(50, ge=1, le=500),
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    engine = _get_engine()
    trades = engine.state.get_trade_history(limit)
    return {"trades": [t.to_dict() for t in trades]}


# ─── Orders ───

class _OrderBody(BaseModel):
    symbol: Optional[str] = None
    side: str = "buy"
    type: str = "market"
    amount: float = 0.0
    price: Optional[float] = None

@router.post("/trading/order")
async def trading_order(
    body: _OrderBody,
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    engine = _get_engine()
    symbol = body.symbol or engine.config.default_symbol
    try:
        result = await engine.exchange.place_order(
            symbol=symbol, side=body.side, order_type=body.type,
            amount=body.amount, price=body.price,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


class _CancelBody(BaseModel):
    order_id: str
    symbol: Optional[str] = None

@router.post("/trading/order/cancel")
async def trading_cancel(
    body: _CancelBody,
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    engine = _get_engine()
    symbol = body.symbol or engine.config.default_symbol
    try:
        return await engine.exchange.cancel_order(body.order_id, symbol)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ─── Visual Indicators ───

@router.get("/trading/indicators")
async def trading_indicators(
    monitor_id: Optional[int] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    engine = _get_engine()
    data = engine.visual.read_all_indicators(monitor_id)
    return {"indicators": data, "source": "tradingview_ocr"}


# ─── Evaluate (single cycle) ───

@router.post("/trading/evaluate")
async def trading_evaluate(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    engine = _get_engine()
    result = await engine.run_cycle()
    return result


# ─── Strategies ───

@router.get("/trading/strategies")
async def trading_strategies(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    from iluminaty.trading.strategy_base import StrategyRegistry
    import iluminaty.trading.strategies  # noqa: F401 — trigger registration
    return {
        "available": StrategyRegistry.list_all(),
        "active": _get_engine().config.strategies,
    }


# ─── Alerts ───

class _AlertBody(BaseModel):
    type: str = "price"
    symbol: Optional[str] = None
    price: Optional[float] = None
    direction: str = "above"

@router.post("/trading/alert")
async def trading_alert_create(
    body: _AlertBody,
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    engine = _get_engine()
    symbol = body.symbol or engine.config.default_symbol
    alert_id = engine.alerts.set_price_alert(symbol, body.price or 0, body.direction)
    return {"alert_id": alert_id, "status": "created"}


@router.get("/trading/alerts")
async def trading_alerts_list(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    engine = _get_engine()
    return {"alerts": engine.alerts.get_all_alerts()}


@router.delete("/trading/alert/{alert_id}")
async def trading_alert_delete(
    alert_id: str,
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    engine = _get_engine()
    removed = engine.alerts.cancel_alert(alert_id)
    return {"removed": removed}


# ─── P&L / Stats ───

@router.get("/trading/pnl")
async def trading_pnl(
    period: str = Query("day"),
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    engine = _get_engine()
    return engine.state.get_pnl(period)


@router.get("/trading/stats")
async def trading_stats(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    engine = _get_engine()
    return engine.state.get_stats()


# ─── Backtest ───

class _BacktestBody(BaseModel):
    strategy: str = "ema_crossover"
    symbol: Optional[str] = None
    timeframe: str = "1h"
    limit: int = 500

@router.post("/trading/backtest")
async def trading_backtest(
    body: _BacktestBody,
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    engine = _get_engine()
    symbol = body.symbol or engine.config.default_symbol

    # Fetch historical data
    try:
        ohlcv = await engine.exchange.get_ohlcv(symbol, body.timeframe, body.limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch data: {e}")

    # Run backtest
    from iluminaty.trading.strategy_base import StrategyRegistry
    import iluminaty.trading.strategies  # noqa: F401
    try:
        strategy = StrategyRegistry.create(body.strategy)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {body.strategy}")

    result = strategy.backtest(ohlcv)
    result["symbol"] = symbol
    result["timeframe"] = body.timeframe
    result["candles"] = len(ohlcv)
    return result
