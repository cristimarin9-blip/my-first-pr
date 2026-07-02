from __future__ import annotations

from abc import ABC, abstractmethod

from polybot.models import OrderResult, Position, Side


class Broker(ABC):
    """Common interface implemented by both PaperBroker and LiveBroker.

    The copy-engine only ever talks to this interface, so switching between
    paper and live trading is purely a config change (`mode: paper|live`).
    """

    @abstractmethod
    def get_cash_balance(self) -> float:
        ...

    @abstractmethod
    def get_positions(self) -> dict[str, Position]:
        """Open positions keyed by token_id."""

    def get_exposure_usd(self) -> float:
        return sum(p.cost_basis_usd for p in self.get_positions().values())

    @abstractmethod
    def place_order(
        self,
        token_id: str,
        condition_id: str,
        outcome: str,
        side: Side,
        price: float,
        size: float,
    ) -> OrderResult:
        ...
