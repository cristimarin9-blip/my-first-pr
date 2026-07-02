from __future__ import annotations

from polybot.config import FilterCriteria
from polybot.models import TraderStats


def passes_filters(stats: TraderStats, criteria: FilterCriteria) -> bool:
    """Decide whether a candidate trader qualifies to be copied."""
    if stats.total_trades < criteria.min_trades:
        return False
    if stats.win_rate < criteria.min_win_rate:
        return False
    if stats.total_volume_usd < criteria.min_volume_usd:
        return False
    if stats.open_positions > criteria.max_open_positions:
        return False
    if stats.avg_trade_usd < criteria.min_avg_trade_usd:
        return False
    return True


def rank_traders(stats_by_wallet: dict[str, TraderStats], criteria: FilterCriteria) -> list[TraderStats]:
    """Qualified traders, best win_rate first (ties broken by volume)."""
    qualified = [s for s in stats_by_wallet.values() if passes_filters(s, criteria)]
    qualified.sort(key=lambda s: (s.win_rate, s.total_volume_usd), reverse=True)
    return qualified
