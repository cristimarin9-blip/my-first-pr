from __future__ import annotations

from polybot.config import SizingConfig
from polybot.models import Trade, TraderStats


def compute_copy_size(
    trade: Trade,
    trader_stats: TraderStats,
    our_bankroll_usd: float,
    current_exposure_usd: float,
    sizing: SizingConfig,
) -> float:
    """Return the number of shares WE should trade to mirror `trade`.

    We mirror the *fraction of bankroll* the trader committed, not the raw
    dollar amount, then scale it by `sizing.copy_ratio` and clamp to our own
    risk limits. Returns 0.0 if the trade should be skipped entirely.

    No fee is added or deducted anywhere in this calculation -- unlike the
    template bot this project replaces, we do not skim a percentage off
    copied trades.
    """
    if our_bankroll_usd <= 0 or trade.price <= 0:
        return 0.0

    trader_bankroll = trader_stats.estimated_bankroll_usd
    if trader_bankroll <= 0:
        # No usable bankroll estimate (e.g. brand new wallet) -- fall back to
        # treating the trade itself as the trader's whole visible bankroll,
        # which is the most conservative assumption (smallest copy size).
        trader_bankroll = trade.notional_usd

    trader_position_fraction = trade.notional_usd / trader_bankroll
    target_usd = our_bankroll_usd * trader_position_fraction * sizing.copy_ratio

    target_usd = min(target_usd, sizing.max_position_usd)

    room_left_usd = max(sizing.max_total_exposure_usd - current_exposure_usd, 0.0)
    target_usd = min(target_usd, room_left_usd)

    if target_usd < sizing.min_order_usd:
        return 0.0

    return target_usd / trade.price
