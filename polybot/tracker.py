from __future__ import annotations

import logging
import time

from polybot.broker import Broker
from polybot.state_store import load_json, save_json

log = logging.getLogger(__name__)

MAX_POINTS = 50_000  # ~6 months at 5-minute snapshots


class EquityTracker:
    """Records periodic snapshots of the portfolio for the PnL chart.

    Each point: {"ts", "cash", "exposure", "equity", "realized_pnl"} where
    equity = cash + cost basis of open positions. Snapshots are throttled to
    one per `interval_minutes` so the file grows slowly; the series is capped
    at MAX_POINTS (oldest dropped).
    """

    def __init__(self, path: str, interval_minutes: float = 5.0):
        self.path = path
        self.interval_seconds = interval_minutes * 60
        self._points: list[dict] = load_json(path, [])
        self._last_ts: float = self._points[-1]["ts"] if self._points else 0.0

    def maybe_snapshot(self, broker: Broker) -> bool:
        """Record a point if the interval has elapsed. Returns True if recorded."""
        now = time.time()
        if now - self._last_ts < self.interval_seconds:
            return False
        try:
            cash = broker.get_cash_balance()
            exposure = broker.get_exposure_usd()
        except Exception as exc:
            log.warning("equity snapshot skipped, broker unavailable: %s", exc)
            return False

        inner = getattr(broker, "inner", broker)
        realized = getattr(inner, "realized_pnl_usd", 0.0)

        self._points.append(
            {
                "ts": int(now),
                "cash": round(cash, 2),
                "exposure": round(exposure, 2),
                "equity": round(cash + exposure, 2),
                "realized_pnl": round(realized, 2),
            }
        )
        if len(self._points) > MAX_POINTS:
            self._points = self._points[-MAX_POINTS:]
        self._last_ts = now
        save_json(self.path, self._points)
        return True

    def get_points(self) -> list[dict]:
        return list(self._points)
