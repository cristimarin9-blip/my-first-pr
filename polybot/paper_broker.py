from __future__ import annotations

import logging
import time

from polybot.broker import Broker
from polybot.config import PaperConfig
from polybot.models import OrderResult, Position, Side
from polybot.state_store import load_json, save_json

log = logging.getLogger(__name__)


class PaperBroker(Broker):
    """Simulated broker: virtual cash + positions persisted to a JSON file.

    Fills are simulated at the requested price plus a configurable slippage
    haircut, clamped to Polymarket's valid share-price range of (0, 1).
    No fee of any kind is applied -- paper mode is meant to mirror live mode
    exactly except for where the order actually gets sent.
    """

    def __init__(self, config: PaperConfig):
        self.config = config
        self.state_file = config.state_file
        state = load_json(self.state_file, None)
        if state is None:
            state = {
                "cash_usd": config.starting_balance_usd,
                "positions": {},
                "realized_pnl_usd": 0.0,
            }
        self.cash_usd: float = state["cash_usd"]
        self.realized_pnl_usd: float = state.get("realized_pnl_usd", 0.0)
        self._positions: dict[str, Position] = {
            token_id: Position(
                token_id=token_id,
                condition_id=p["condition_id"],
                outcome=p["outcome"],
                size=p["size"],
                avg_price=p["avg_price"],
            )
            for token_id, p in state["positions"].items()
        }

    def _persist(self) -> None:
        save_json(
            self.state_file,
            {
                "cash_usd": self.cash_usd,
                "realized_pnl_usd": self.realized_pnl_usd,
                "positions": {
                    tid: {
                        "condition_id": p.condition_id,
                        "outcome": p.outcome,
                        "size": p.size,
                        "avg_price": p.avg_price,
                    }
                    for tid, p in self._positions.items()
                    if p.size > 1e-9
                },
            },
        )

    def get_cash_balance(self) -> float:
        return self.cash_usd

    def get_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def _fill_price(self, price: float, side: Side) -> float:
        slip = self.config.slippage_bps / 10_000.0
        filled = price * (1 + slip) if side == Side.BUY else price * (1 - slip)
        return min(max(filled, 0.0001), 0.9999)

    def place_order(
        self,
        token_id: str,
        condition_id: str,
        outcome: str,
        side: Side,
        price: float,
        size: float,
    ) -> OrderResult:
        if size <= 0:
            return OrderResult(False, token_id, side, price, size, error="size must be positive")

        fill_price = self._fill_price(price, side)
        notional = fill_price * size

        if side == Side.BUY:
            if notional > self.cash_usd:
                return OrderResult(
                    False, token_id, side, fill_price, size, error="insufficient paper cash balance"
                )
            pos = self._positions.get(
                token_id, Position(token_id=token_id, condition_id=condition_id, outcome=outcome)
            )
            new_size = pos.size + size
            pos.avg_price = (pos.size * pos.avg_price + notional) / new_size
            pos.size = new_size
            self._positions[token_id] = pos
            self.cash_usd -= notional
        else:  # SELL
            pos = self._positions.get(token_id)
            held = pos.size if pos else 0.0
            if size > held + 1e-9:
                return OrderResult(
                    False, token_id, side, fill_price, size, error="cannot sell more than held position"
                )
            realized = (fill_price - pos.avg_price) * size
            self.realized_pnl_usd += realized
            pos.size -= size
            self.cash_usd += notional
            if pos.size <= 1e-9:
                self._positions.pop(token_id, None)

        self._persist()
        log.info(
            "PAPER FILL %s %s size=%.4f price=%.4f notional=$%.2f cash=$%.2f",
            side.value, token_id, size, fill_price, notional, self.cash_usd,
        )
        return OrderResult(True, token_id, side, fill_price, size, order_id=f"paper-{int(time.time()*1000)}")
