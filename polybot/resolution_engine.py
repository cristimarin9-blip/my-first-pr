from __future__ import annotations

import logging
import time

from polybot.broker import Broker
from polybot.config import ResolutionConfig
from polybot.gamma_client import GammaApiError, GammaClient
from polybot.journal import TradeJournal
from polybot.models import Side
from polybot.state_store import load_json, save_json

log = logging.getLogger(__name__)


class ResolutionEngine:
    """Buys near-certain outcomes in markets that are about to resolve.

    Each poll scans every active market closing within `within_hours` and, for
    any outcome priced in [min_probability, max_probability], buys `order_usd`
    of it -- once per outcome (tracked in a state file so a market never
    re-fires). The thesis: an outcome trading above ~90% with only hours left
    on the clock almost always resolves in its favour, paying out at 1.0.

    Runs against the same broker as the other strategies, so it works
    identically in paper and live mode, and every buy still passes through the
    risk circuit breakers.
    """

    def __init__(
        self,
        config: ResolutionConfig,
        broker: Broker,
        gamma_client: GammaClient | None = None,
        journal: TradeJournal | None = None,
    ):
        self.config = config
        self.broker = broker
        self.gamma = gamma_client or GammaClient()
        self.journal = journal
        self._bought: set[str] = set(load_json(config.state_file, []))

    def _persist(self) -> None:
        save_json(self.config.state_file, list(self._bought)[-5000:])

    def poll_once(self) -> int:
        try:
            markets = self.gamma.get_markets_closing_within(
                self.config.within_hours,
                limit=self.config.scan_limit,
                min_liquidity_usd=self.config.min_liquidity_usd,
            )
        except GammaApiError as exc:
            log.warning("resolution scan failed, skipping this pass: %s", exc)
            return 0

        now = time.time()
        window = self.config.within_hours * 3600
        held = self.broker.get_positions()
        placed = 0

        for market in markets:
            seconds_left = market["end_ts"] - now
            if seconds_left <= 0 or seconds_left > window:
                continue
            for token_id, outcome, price in market["tokens"]:
                if token_id in self._bought or token_id in held:
                    continue
                if not (self.config.min_probability <= price <= self.config.max_probability):
                    continue

                size = self.config.order_usd / price
                result = self.broker.place_order(
                    token_id=token_id,
                    condition_id=market["condition_id"],
                    outcome=outcome,
                    side=Side.BUY,
                    price=price,
                    size=size,
                )
                if result.success:
                    self._bought.add(token_id)
                    placed += 1
                    log.info(
                        "RESOLUTION BUY %s '%s' at %.0f%% closing in %dm (%.2f shares, $%.2f)",
                        outcome, market["question"] or market["condition_id"],
                        price * 100, seconds_left / 60, result.size, result.notional_usd,
                    )
                    if self.journal:
                        self.journal.record(
                            strategy="resolution",
                            source="resolution",
                            market=market["question"],
                            condition_id=market["condition_id"],
                            outcome=outcome,
                            result=result,
                        )
                else:
                    log.warning("resolution buy failed for %s: %s", token_id, result.error)

        self._persist()
        return placed
