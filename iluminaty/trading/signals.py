"""Signal aggregator — fuses signals from multiple strategies."""
from __future__ import annotations

from .models import Signal, Direction


class SignalAggregator:
    """Combine signals from multiple strategies into one decision."""

    def __init__(self, min_confidence: float = 0.6, min_agreement: int = 1):
        self.min_confidence = min_confidence
        self.min_agreement = min_agreement

    def aggregate(self, signals: list[Signal]) -> Signal | None:
        """Pick the best actionable signal from multiple strategies.

        Returns None if no signal meets the threshold.
        Rules:
        1. Filter out HOLD signals
        2. Group by direction
        3. Pick direction with most agreement (and highest avg confidence)
        4. Return strongest signal if it meets min_confidence
        """
        actionable = [s for s in signals if s.direction not in (Direction.HOLD,)]
        if not actionable:
            return None

        # Group by direction
        groups: dict[Direction, list[Signal]] = {}
        for s in actionable:
            groups.setdefault(s.direction, []).append(s)

        # Find best group: most signals, then highest avg confidence
        best_dir = max(
            groups.keys(),
            key=lambda d: (len(groups[d]), sum(s.confidence for s in groups[d]) / len(groups[d])),
        )
        best_group = groups[best_dir]

        if len(best_group) < self.min_agreement:
            return None

        # Return highest confidence signal from the best group
        best_signal = max(best_group, key=lambda s: s.confidence)
        if best_signal.confidence < self.min_confidence:
            return None

        return best_signal
