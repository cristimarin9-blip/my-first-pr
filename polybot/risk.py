from __future__ import annotations

import logging
import time
from pathlib import Path

from polybot.broker import Broker
from polybot.config import RiskConfig
from polybot.models import OrderResult, Position, Side
from polybot.state_store import load_json, save_json

log = logging.getLogger(__name__)


class RiskGuardedBroker(Broker):
    """Wraps any Broker and enforces circuit breakers on every order.

    BUYs (new exposure) can be rejected; SELLs (de-risking) always pass
    through, including while halted -- a safety system that refuses to let
    you exit would itself be a risk.

    Keeps its own small ledger of average entry prices for the orders it
    forwards, so it can compute *realized* PnL per day identically for paper
    and live brokers, and trip the daily-loss halt on either.
    """

    def __init__(self, inner: Broker, config: RiskConfig):
        self.inner = inner
        self.config = config
        state = load_json(config.state_file, None) or {}
        self._date: str = state.get("date", self._today())
        self._buys_today: int = state.get("buys_today", 0)
        self._buy_notional_today: float = state.get("buy_notional_today", 0.0)
        self._realized_pnl_today: float = state.get("realized_pnl_today", 0.0)
        self._halted: bool = state.get("halted", False)
        # token_id -> {"size": float, "avg_price": float}
        self._ledger: dict[str, dict] = state.get("ledger", {})
        self._rollover_if_new_day()

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def _rollover_if_new_day(self) -> None:
        today = self._today()
        if today != self._date:
            log.info(
                "risk: new day %s (yesterday: %d buys, $%.2f notional, $%.2f realized pnl%s)",
                today, self._buys_today, self._buy_notional_today, self._realized_pnl_today,
                ", HALTED" if self._halted else "",
            )
            self._date = today
            self._buys_today = 0
            self._buy_notional_today = 0.0
            self._realized_pnl_today = 0.0
            self._halted = False
            self._persist()

    def _persist(self) -> None:
        save_json(
            self.config.state_file,
            {
                "date": self._date,
                "buys_today": self._buys_today,
                "buy_notional_today": self._buy_notional_today,
                "realized_pnl_today": self._realized_pnl_today,
                "halted": self._halted,
                "ledger": self._ledger,
            },
        )

    # --- Broker interface ----------------------------------------------------

    def get_cash_balance(self) -> float:
        return self.inner.get_cash_balance()

    def get_positions(self) -> dict[str, Position]:
        return self.inner.get_positions()

    def place_order(
        self,
        token_id: str,
        condition_id: str,
        outcome: str,
        side: Side,
        price: float,
        size: float,
    ) -> OrderResult:
        self._rollover_if_new_day()

        if side == Side.BUY:
            reason = self._buy_rejection_reason(condition_id, price, price * size)
            if reason:
                log.warning("risk: BUY rejected (%s) token=%s $%.2f", reason, token_id, price * size)
                return OrderResult(False, token_id, side, price, size, error=f"risk: {reason}")

        result = self.inner.place_order(token_id, condition_id, outcome, side, price, size)
        if result.success:
            self._record_fill(token_id, side, result.price, result.size)
        return result

    # --- checks ---------------------------------------------------------------

    def _buy_rejection_reason(self, condition_id: str, price: float, notional: float) -> str | None:
        if Path(self.config.kill_switch_file).exists():
            return f"kill switch active ({self.config.kill_switch_file} exists)"
        if self._halted:
            return f"halted for the day (realized pnl ${self._realized_pnl_today:.2f})"
        if not (self.config.min_buy_price <= price <= self.config.max_buy_price):
            return (
                f"price {price:.4f} outside allowed band "
                f"[{self.config.min_buy_price}, {self.config.max_buy_price}]"
            )
        if self._buys_today + 1 > self.config.max_buys_per_day:
            return f"max_buys_per_day ({self.config.max_buys_per_day}) reached"
        if self._buy_notional_today + notional > self.config.max_buy_notional_per_day_usd:
            return (
                f"max_buy_notional_per_day_usd exceeded "
                f"(${self._buy_notional_today:.2f} + ${notional:.2f} > "
                f"${self.config.max_buy_notional_per_day_usd:.2f})"
            )
        market_exposure = sum(
            p.cost_basis_usd
            for p in self.inner.get_positions().values()
            if p.condition_id == condition_id
        )
        if market_exposure + notional > self.config.max_market_exposure_usd:
            return (
                f"max_market_exposure_usd exceeded for market {condition_id} "
                f"(${market_exposure:.2f} + ${notional:.2f} > "
                f"${self.config.max_market_exposure_usd:.2f})"
            )
        return None

    # --- accounting -------------------------------------------------------------

    def _record_fill(self, token_id: str, side: Side, price: float, size: float) -> None:
        entry = self._ledger.get(token_id, {"size": 0.0, "avg_price": 0.0})
        if side == Side.BUY:
            self._buys_today += 1
            self._buy_notional_today += price * size
            new_size = entry["size"] + size
            entry["avg_price"] = (entry["size"] * entry["avg_price"] + price * size) / new_size
            entry["size"] = new_size
            self._ledger[token_id] = entry
        else:
            realized = (price - entry["avg_price"]) * min(size, entry["size"])
            self._realized_pnl_today += realized
            entry["size"] = max(entry["size"] - size, 0.0)
            if entry["size"] <= 1e-9:
                self._ledger.pop(token_id, None)
            else:
                self._ledger[token_id] = entry
            if (
                self.config.daily_loss_limit_usd > 0
                and self._realized_pnl_today <= -self.config.daily_loss_limit_usd
                and not self._halted
            ):
                self._halted = True
                log.error(
                    "risk: DAILY LOSS LIMIT HIT (realized $%.2f <= -$%.2f) -- "
                    "no more BUYs today, SELLs still allowed",
                    self._realized_pnl_today, self.config.daily_loss_limit_usd,
                )
        self._persist()

    # --- introspection (used by --status) -----------------------------------------

    def today_summary(self) -> dict:
        self._rollover_if_new_day()
        return {
            "date": self._date,
            "buys_today": self._buys_today,
            "buy_notional_today": self._buy_notional_today,
            "realized_pnl_today": self._realized_pnl_today,
            "halted": self._halted,
            "kill_switch": Path(self.config.kill_switch_file).exists(),
        }
