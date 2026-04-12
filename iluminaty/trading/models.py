"""Trading data models."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    CLOSE = "close"
    HOLD = "hold"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELED = "canceled"
    FAILED = "failed"


@dataclass
class Signal:
    direction: Direction
    confidence: float           # 0.0 - 1.0
    source: str                 # strategy name
    reason: str
    symbol: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "direction": self.direction.value,
            "confidence": round(self.confidence, 4),
            "source": self.source,
            "reason": self.reason,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
        }


@dataclass
class Order:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    exchange_id: str = ""       # exchange-assigned order ID
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    type: OrderType = OrderType.MARKET
    amount: float = 0.0
    price: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float | None = None
    filled_amount: float = 0.0
    fee: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "exchange_id": self.exchange_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "type": self.type.value,
            "amount": self.amount,
            "price": self.price,
            "status": self.status.value,
            "filled_price": self.filled_price,
            "filled_amount": self.filled_amount,
            "fee": self.fee,
            "created_at": self.created_at,
        }


@dataclass
class Position:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    entry_price: float = 0.0
    amount: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    unrealized_pnl: float = 0.0
    opened_at: float = field(default_factory=time.time)
    strategy: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side.value,
            "entry_price": self.entry_price,
            "amount": self.amount,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "unrealized_pnl": round(self.unrealized_pnl, 4),
            "opened_at": self.opened_at,
            "strategy": self.strategy,
        }


@dataclass
class TradeRecord:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    symbol: str = ""
    side: str = "buy"
    entry_price: float = 0.0
    exit_price: float = 0.0
    amount: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    fee_total: float = 0.0
    strategy: str = ""
    reason: str = ""
    opened_at: float = 0.0
    closed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "amount": self.amount,
            "pnl": round(self.pnl, 4),
            "pnl_pct": round(self.pnl_pct, 4),
            "fee_total": round(self.fee_total, 4),
            "strategy": self.strategy,
            "reason": self.reason,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
        }
