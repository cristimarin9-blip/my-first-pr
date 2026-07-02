from __future__ import annotations

import logging

from polybot.broker import Broker
from polybot.config import ThresholdConfig
from polybot.gamma_client import GammaClient
from polybot.journal import TradeJournal
from polybot.models import Side
from polybot.state_store import load_json, save_json

log = logging.getLogger(__name__)


class ThresholdEngine:
    """Momentum strategy: buy an outcome when its probability reaches a trigger.

    Watches a configured list of markets. The moment either outcome (Yes OR
    No) reaches `trigger_probability`, it buys `order_usd` worth of that
    outcome -- once per outcome, tracked in a state file so a market is never
    re-entered after it fires. Positions opened here (and only those) are
    then managed with optional take-profit / stop-loss exits.

    Runs alongside the copy-trading engine against the same broker, so it
    works identically in paper and live mode.
    """

    def __init__(
        self,
        config: ThresholdConfig,
        broker: Broker,
        gamma_client: GammaClient | None = None,
        journal: TradeJournal | None = None,
    ):
        self.config = config
        self.broker = broker
        self.gamma = gamma_client or GammaClient()
        self.journal = journal
        # token_id -> {"condition_id", "outcome", "entry_price", "exited"}
        self._entered: dict[str, dict] = load_json(config.state_file, {})

    def _persist(self) -> None:
        save_json(self.config.state_file, self._entered)

    def poll_once(self) -> int:
        """One evaluation pass. Returns the number of orders placed."""
        actions = self._check_exits()
        actions += self._check_entries()
        self._persist()
        return actions

    def _check_entries(self) -> int:
        placed = 0
        held = self.broker.get_positions()
        for condition_id in self.config.markets:
            for token_id, outcome, price in self.gamma.get_market_tokens(condition_id):
                if token_id in self._entered:
                    continue  # already fired for this outcome (or exited it)
                if token_id in held:
                    continue  # copy-engine (or a previous run) already holds it
                if not (self.config.trigger_probability <= price <= self.config.max_entry_probability):
                    continue

                size = self.config.order_usd / price
                result = self.broker.place_order(
                    token_id=token_id,
                    condition_id=condition_id,
                    outcome=outcome,
                    side=Side.BUY,
                    price=price,
                    size=size,
                )
                if result.success:
                    self._entered[token_id] = {
                        "condition_id": condition_id,
                        "outcome": outcome,
                        "entry_price": result.price,
                        "exited": False,
                    }
                    placed += 1
                    log.info(
                        "THRESHOLD ENTRY %s '%s' at %.0f%% (%.4f shares, $%.2f)",
                        outcome, condition_id, price * 100, result.size, result.notional_usd,
                    )
                    if self.journal:
                        self.journal.record(
                            strategy="threshold",
                            source="threshold-entry",
                            market=condition_id,
                            condition_id=condition_id,
                            outcome=outcome,
                            result=result,
                        )
                else:
                    log.warning("threshold entry failed for %s: %s", token_id, result.error)
        return placed

    def _check_exits(self) -> int:
        placed = 0
        held = self.broker.get_positions()
        for token_id, info in self._entered.items():
            if info.get("exited"):
                continue
            pos = held.get(token_id)
            if pos is None or pos.size <= 0:
                info["exited"] = True  # resolved or closed elsewhere; stop tracking
                continue

            price = self.gamma.get_token_price(info["condition_id"], token_id)
            if price is None:
                continue

            reason = None
            if self.config.take_profit_probability > 0 and price >= self.config.take_profit_probability:
                reason = "take-profit"
            elif self.config.stop_loss_probability > 0 and price <= self.config.stop_loss_probability:
                reason = "stop-loss"
            if reason is None:
                continue

            result = self.broker.place_order(
                token_id=token_id,
                condition_id=info["condition_id"],
                outcome=info["outcome"],
                side=Side.SELL,
                price=price,
                size=pos.size,
            )
            if result.success:
                info["exited"] = True
                placed += 1
                log.info(
                    "THRESHOLD EXIT (%s) %s at %.0f%% (entry was %.0f%%)",
                    reason, info["outcome"], price * 100, info["entry_price"] * 100,
                )
                if self.journal:
                    self.journal.record(
                        strategy="threshold",
                        source=f"threshold-{reason}",
                        market=info["condition_id"],
                        condition_id=info["condition_id"],
                        outcome=info["outcome"],
                        result=result,
                    )
            else:
                log.warning("threshold %s exit failed for %s: %s", reason, token_id, result.error)
        return placed
