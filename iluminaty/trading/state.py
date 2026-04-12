"""State manager — SQLite-backed position and trade tracking."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Optional

from .models import Position, TradeRecord, OrderSide


class StateManager:
    """Thread-safe SQLite state for positions, trades, and P&L."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or ":memory:"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    amount REAL NOT NULL,
                    stop_loss REAL,
                    take_profit REAL,
                    unrealized_pnl REAL DEFAULT 0,
                    opened_at REAL NOT NULL,
                    strategy TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    amount REAL NOT NULL,
                    pnl REAL NOT NULL,
                    pnl_pct REAL NOT NULL,
                    fee_total REAL DEFAULT 0,
                    strategy TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    opened_at REAL NOT NULL,
                    closed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    direction TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source TEXT NOT NULL,
                    reason TEXT DEFAULT '',
                    symbol TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}'
                );
            """)

    def record_position(self, pos: Position) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO positions "
                "(id, symbol, side, entry_price, amount, stop_loss, take_profit, "
                "unrealized_pnl, opened_at, strategy) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pos.id, pos.symbol, pos.side.value, pos.entry_price,
                 pos.amount, pos.stop_loss, pos.take_profit,
                 pos.unrealized_pnl, pos.opened_at, pos.strategy),
            )
            self._conn.commit()

    def update_position(self, pos: Position) -> None:
        self.record_position(pos)

    def remove_position(self, position_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))
            self._conn.commit()

    def get_open_positions(self) -> list[Position]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM positions").fetchall()
        return [
            Position(
                id=r["id"], symbol=r["symbol"],
                side=OrderSide(r["side"]),
                entry_price=r["entry_price"], amount=r["amount"],
                stop_loss=r["stop_loss"], take_profit=r["take_profit"],
                unrealized_pnl=r["unrealized_pnl"],
                opened_at=r["opened_at"], strategy=r["strategy"],
            )
            for r in rows
        ]

    def record_trade(self, trade: TradeRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO trades "
                "(id, symbol, side, entry_price, exit_price, amount, pnl, pnl_pct, "
                "fee_total, strategy, reason, opened_at, closed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trade.id, trade.symbol, trade.side, trade.entry_price,
                 trade.exit_price, trade.amount, trade.pnl, trade.pnl_pct,
                 trade.fee_total, trade.strategy, trade.reason,
                 trade.opened_at, trade.closed_at),
            )
            self._conn.commit()

    def close_position(self, position_id: str, exit_price: float, reason: str = "signal") -> Optional[TradeRecord]:
        positions = self.get_open_positions()
        pos = next((p for p in positions if p.id == position_id), None)
        if not pos:
            return None

        if pos.side == OrderSide.BUY:
            pnl = (exit_price - pos.entry_price) * pos.amount
        else:
            pnl = (pos.entry_price - exit_price) * pos.amount
        pnl_pct = pnl / (pos.entry_price * pos.amount) if pos.entry_price * pos.amount else 0

        trade = TradeRecord(
            symbol=pos.symbol, side=pos.side.value,
            entry_price=pos.entry_price, exit_price=exit_price,
            amount=pos.amount, pnl=pnl, pnl_pct=pnl_pct,
            strategy=pos.strategy, reason=reason,
            opened_at=pos.opened_at,
        )
        self.record_trade(trade)
        self.remove_position(position_id)
        return trade

    def get_trade_history(self, limit: int = 100) -> list[TradeRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trades ORDER BY closed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            TradeRecord(
                id=r["id"], symbol=r["symbol"], side=r["side"],
                entry_price=r["entry_price"], exit_price=r["exit_price"],
                amount=r["amount"], pnl=r["pnl"], pnl_pct=r["pnl_pct"],
                fee_total=r["fee_total"], strategy=r["strategy"],
                reason=r["reason"], opened_at=r["opened_at"],
                closed_at=r["closed_at"],
            )
            for r in rows
        ]

    def get_pnl(self, period: str = "day") -> dict:
        now = time.time()
        if period == "day":
            since = now - 86400
        elif period == "week":
            since = now - 86400 * 7
        elif period == "month":
            since = now - 86400 * 30
        else:
            since = 0

        with self._lock:
            rows = self._conn.execute(
                "SELECT pnl, pnl_pct FROM trades WHERE closed_at >= ?", (since,)
            ).fetchall()

        total_pnl = sum(r["pnl"] for r in rows)
        wins = [r for r in rows if r["pnl"] > 0]
        losses = [r for r in rows if r["pnl"] < 0]

        return {
            "period": period,
            "total_trades": len(rows),
            "total_pnl": round(total_pnl, 4),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(rows), 4) if rows else 0,
            "avg_win": round(sum(r["pnl"] for r in wins) / len(wins), 4) if wins else 0,
            "avg_loss": round(sum(r["pnl"] for r in losses) / len(losses), 4) if losses else 0,
        }

    def get_stats(self) -> dict:
        pnl_day = self.get_pnl("day")
        pnl_all = self.get_pnl("all")
        positions = self.get_open_positions()
        return {
            "open_positions": len(positions),
            "today": pnl_day,
            "all_time": pnl_all,
        }

    def log_signal(self, signal) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO signals_log "
                "(timestamp, direction, confidence, source, reason, symbol, metadata) "
                "VALUES (?,?,?,?,?,?,?)",
                (signal.timestamp, signal.direction.value, signal.confidence,
                 signal.source, signal.reason, signal.symbol,
                 json.dumps(signal.metadata)),
            )
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
