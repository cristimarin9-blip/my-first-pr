from __future__ import annotations

import csv
import logging
import time
from pathlib import Path

from polybot.models import OrderResult

log = logging.getLogger(__name__)

FIELDS = [
    "timestamp",
    "date_utc",
    "strategy",
    "source",
    "market",
    "condition_id",
    "token_id",
    "outcome",
    "side",
    "price",
    "size",
    "notional_usd",
    "order_id",
]


class TradeJournal:
    """Append-only CSV log of every executed trade, for later analysis.

    One row per successful fill, tagged with the strategy that produced it
    and (for copy-trading) the wallet it was copied from. Load it into a
    spreadsheet or pandas to compute your own win rate, per-trader PnL
    attribution, etc.
    """

    def __init__(self, path: str):
        self.path = Path(path)

    def record(
        self,
        strategy: str,
        source: str,
        market: str,
        condition_id: str,
        outcome: str,
        result: OrderResult,
    ) -> None:
        if not result.success:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not self.path.exists()
            now = time.time()
            with self.path.open("a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                if is_new:
                    writer.writeheader()
                writer.writerow(
                    {
                        "timestamp": int(now),
                        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                        "strategy": strategy,
                        "source": source,
                        "market": market,
                        "condition_id": condition_id,
                        "token_id": result.token_id,
                        "outcome": outcome,
                        "side": result.side.value,
                        "price": f"{result.price:.4f}",
                        "size": f"{result.size:.4f}",
                        "notional_usd": f"{result.notional_usd:.2f}",
                        "order_id": result.order_id or "",
                    }
                )
        except OSError as exc:
            # Journaling must never take down the trading loop.
            log.warning("could not write trade journal entry: %s", exc)
