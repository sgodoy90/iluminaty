"""Alert manager — price and signal alerts via watch_engine + exchange data."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("iluminaty.trading.alerts")


@dataclass
class Alert:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    type: str = "price"            # "price" | "signal" | "position"
    symbol: str = ""
    price: float | None = None
    direction: str = "above"       # "above" | "below"
    condition: str = ""            # custom condition string
    triggered: bool = False
    created_at: float = field(default_factory=time.time)
    triggered_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "symbol": self.symbol,
            "price": self.price,
            "direction": self.direction,
            "condition": self.condition,
            "triggered": self.triggered,
            "created_at": self.created_at,
            "triggered_at": self.triggered_at,
        }


class AlertManager:
    """Manages trading alerts using both exchange data and visual monitoring."""

    def __init__(self, watch_engine=None, exchange_client=None):
        self._watch_engine = watch_engine
        self._exchange = exchange_client
        self._alerts: dict[str, Alert] = {}
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[Alert], None]] = []
        self._monitor_thread: threading.Thread | None = None
        self._running = False

    def on_alert(self, callback: Callable[[Alert], None]):
        """Register a callback for when alerts trigger."""
        self._callbacks.append(callback)

    def set_price_alert(
        self, symbol: str, price: float, direction: str = "above"
    ) -> str:
        alert = Alert(type="price", symbol=symbol, price=price, direction=direction)
        with self._lock:
            self._alerts[alert.id] = alert
        log.info("Price alert set: %s %s %s @ %.2f", alert.id, symbol, direction, price)
        return alert.id

    def set_signal_alert(self, strategy: str, direction: str) -> str:
        alert = Alert(
            type="signal",
            condition=f"strategy={strategy},direction={direction}",
            direction=direction,
        )
        with self._lock:
            self._alerts[alert.id] = alert
        return alert.id

    def set_position_alert(self, position_id: str, condition: str) -> str:
        alert = Alert(
            type="position",
            condition=f"position={position_id},{condition}",
        )
        with self._lock:
            self._alerts[alert.id] = alert
        return alert.id

    def cancel_alert(self, alert_id: str) -> bool:
        with self._lock:
            return self._alerts.pop(alert_id, None) is not None

    def get_active_alerts(self) -> list[dict]:
        with self._lock:
            return [a.to_dict() for a in self._alerts.values() if not a.triggered]

    def get_all_alerts(self) -> list[dict]:
        with self._lock:
            return [a.to_dict() for a in self._alerts.values()]

    async def check_price_alerts(self, current_prices: dict[str, float]):
        """Check price alerts against current exchange prices."""
        with self._lock:
            alerts = [a for a in self._alerts.values()
                      if a.type == "price" and not a.triggered]

        for alert in alerts:
            price = current_prices.get(alert.symbol)
            if price is None:
                continue

            triggered = False
            if alert.direction == "above" and price >= (alert.price or 0):
                triggered = True
            elif alert.direction == "below" and price <= (alert.price or float("inf")):
                triggered = True

            if triggered:
                alert.triggered = True
                alert.triggered_at = time.time()
                log.info("ALERT TRIGGERED: %s %s %s @ %.2f (current: %.2f)",
                         alert.id, alert.symbol, alert.direction, alert.price, price)
                for cb in self._callbacks:
                    try:
                        cb(alert)
                    except Exception as e:
                        log.warning("Alert callback error: %s", e)

    def check_visual_alerts(self, monitor_id: int | None = None):
        """Use watch_engine for visual price monitoring on TradingView."""
        if not self._watch_engine:
            return

        with self._lock:
            price_alerts = [a for a in self._alerts.values()
                            if a.type == "price" and not a.triggered]

        for alert in price_alerts:
            if alert.price is None:
                continue
            condition = f"domain:price_{alert.direction}"
            try:
                result = self._watch_engine.wait(
                    condition="text_visible",
                    timeout=0.5,
                    text=str(int(alert.price)),
                    monitor_id=monitor_id,
                )
                if result.triggered:
                    alert.triggered = True
                    alert.triggered_at = time.time()
                    for cb in self._callbacks:
                        try:
                            cb(alert)
                        except Exception:
                            pass
            except Exception:
                pass

    def start_monitoring(self, interval: float = 5.0):
        """Start background alert monitoring thread."""
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True,
            name="trading-alerts",
        )
        self._monitor_thread.start()

    def stop_monitoring(self):
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
            self._monitor_thread = None

    def _monitor_loop(self, interval: float):
        while self._running:
            try:
                self.check_visual_alerts()
            except Exception as e:
                log.warning("Alert monitor error: %s", e)
            time.sleep(interval)
