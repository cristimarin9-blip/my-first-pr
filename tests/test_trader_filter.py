from polybot.config import FilterCriteria
from polybot.models import TraderStats
from polybot.trader_filter import passes_filters, rank_traders

CRITERIA = FilterCriteria(
    min_trades=10,
    min_win_rate=0.6,
    min_volume_usd=1000.0,
    max_open_positions=5,
    min_avg_trade_usd=10.0,
)


def make_stats(**overrides) -> TraderStats:
    defaults = dict(
        address="0xabc",
        total_trades=20,
        wins=15,
        losses=5,
        total_volume_usd=2000.0,
        open_positions=2,
        estimated_bankroll_usd=500.0,
    )
    defaults.update(overrides)
    return TraderStats(**defaults)


def test_qualified_trader_passes():
    assert passes_filters(make_stats(), CRITERIA)


def test_fails_min_trades():
    assert not passes_filters(make_stats(total_trades=5), CRITERIA)


def test_fails_win_rate():
    assert not passes_filters(make_stats(wins=5, losses=15), CRITERIA)


def test_fails_volume():
    assert not passes_filters(make_stats(total_volume_usd=100.0), CRITERIA)


def test_fails_too_many_open_positions():
    assert not passes_filters(make_stats(open_positions=50), CRITERIA)


def test_fails_dust_trader():
    # 20 trades but only $100 total volume -> $5 avg trade, below min_avg_trade_usd
    assert not passes_filters(make_stats(total_volume_usd=100.0, total_trades=20), CRITERIA)


def test_win_rate_with_no_decided_trades_is_zero():
    stats = make_stats(wins=0, losses=0)
    assert stats.win_rate == 0.0
    assert not passes_filters(stats, CRITERIA)


def test_rank_traders_orders_by_win_rate_then_volume():
    a = make_stats(address="0xa", wins=18, losses=2, total_volume_usd=5000.0)  # 0.9 win rate
    b = make_stats(address="0xb", wins=15, losses=5, total_volume_usd=9000.0)  # 0.75 win rate
    c = make_stats(address="0xc", wins=5, losses=15)  # fails filter
    ranked = rank_traders({"a": a, "b": b, "c": c}, CRITERIA)
    assert [s.address for s in ranked] == ["0xa", "0xb"]
