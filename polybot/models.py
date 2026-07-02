from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Trade:
    """A single fill made by some trader on Polymarket."""

    trade_id: str
    trader: str
    condition_id: str
    token_id: str
    outcome: str
    side: Side
    price: float
    size: float
    timestamp: int  # unix seconds
    title: str = ""

    @property
    def notional_usd(self) -> float:
        return self.price * self.size


@dataclass
class ClosedPosition:
    """A position a trader fully exited or that resolved, used to score wins/losses."""

    condition_id: str
    token_id: str
    realized_pnl: float
    cost_basis_usd: float


@dataclass
class TraderStats:
    address: str
    total_trades: int
    wins: int
    losses: int
    total_volume_usd: float
    open_positions: int
    estimated_bankroll_usd: float

    @property
    def win_rate(self) -> float:
        decided = self.wins + self.losses
        if decided == 0:
            return 0.0
        return self.wins / decided

    @property
    def avg_trade_usd(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_volume_usd / self.total_trades


@dataclass
class Position:
    """Our own (paper or live) position in a single outcome token."""

    token_id: str
    condition_id: str
    outcome: str
    size: float = 0.0
    avg_price: float = 0.0

    @property
    def cost_basis_usd(self) -> float:
        return self.size * self.avg_price


@dataclass
class OrderResult:
    success: bool
    token_id: str
    side: Side
    price: float
    size: float
    order_id: str | None = None
    error: str | None = None

    @property
    def notional_usd(self) -> float:
        return self.price * self.size
