from polybot.config import SizingConfig
from polybot.models import Side, Trade, TraderStats
from polybot.sizing import compute_copy_size

SIZING = SizingConfig(
    copy_ratio=0.5,
    max_position_usd=100.0,
    max_total_exposure_usd=500.0,
    min_order_usd=1.0,
)


def make_trade(**overrides) -> Trade:
    defaults = dict(
        trade_id="t1",
        trader="0xabc",
        condition_id="c1",
        token_id="tok1",
        outcome="Yes",
        side=Side.BUY,
        price=0.5,
        size=200.0,  # notional = 100
        timestamp=1_700_000_000,
    )
    defaults.update(overrides)
    return Trade(**defaults)


def make_stats(**overrides) -> TraderStats:
    defaults = dict(
        address="0xabc",
        total_trades=50,
        wins=30,
        losses=20,
        total_volume_usd=10_000.0,
        open_positions=3,
        estimated_bankroll_usd=1000.0,  # trade is 10% of their bankroll
    )
    defaults.update(overrides)
    return TraderStats(**defaults)


def test_proportional_sizing_scaled_by_copy_ratio():
    trade = make_trade()
    stats = make_stats()
    # trader committed 10% of their $1000 bankroll; we have $2000 bankroll,
    # copy_ratio=0.5 -> target = 2000 * 0.10 * 0.5 = $100 -> capped at max_position_usd=100
    size = compute_copy_size(trade, stats, our_bankroll_usd=2000.0, current_exposure_usd=0.0, sizing=SIZING)
    assert size == 100.0 / trade.price


def test_smaller_bankroll_scales_down():
    trade = make_trade()
    stats = make_stats()
    # our bankroll only $200 -> target = 200 * 0.10 * 0.5 = $10
    size = compute_copy_size(trade, stats, our_bankroll_usd=200.0, current_exposure_usd=0.0, sizing=SIZING)
    assert size == 10.0 / trade.price


def test_capped_by_max_position_usd():
    trade = make_trade(size=2000.0)  # notional = 1000, 100% of trader bankroll
    stats = make_stats()
    size = compute_copy_size(trade, stats, our_bankroll_usd=10_000.0, current_exposure_usd=0.0, sizing=SIZING)
    assert size * trade.price == SIZING.max_position_usd


def test_capped_by_remaining_exposure_room():
    trade = make_trade()
    stats = make_stats()
    size = compute_copy_size(
        trade, stats, our_bankroll_usd=2000.0, current_exposure_usd=480.0, sizing=SIZING
    )
    assert size * trade.price <= 20.0 + 1e-9


def test_zero_when_bankroll_is_zero():
    trade = make_trade()
    stats = make_stats()
    size = compute_copy_size(trade, stats, our_bankroll_usd=0.0, current_exposure_usd=0.0, sizing=SIZING)
    assert size == 0.0


def test_zero_when_below_min_order_usd():
    trade = make_trade()
    stats = make_stats(estimated_bankroll_usd=1_000_000.0)  # trade is a tiny fraction of bankroll
    size = compute_copy_size(trade, stats, our_bankroll_usd=2000.0, current_exposure_usd=0.0, sizing=SIZING)
    assert size == 0.0


def test_falls_back_to_trade_notional_when_no_bankroll_estimate():
    trade = make_trade()
    stats = make_stats(estimated_bankroll_usd=0.0)
    # falls back to trader_bankroll = trade.notional_usd -> fraction = 1.0
    size = compute_copy_size(trade, stats, our_bankroll_usd=200.0, current_exposure_usd=0.0, sizing=SIZING)
    assert size * trade.price == 100.0  # 200 * 1.0 * 0.5 = 100, under the $100 cap
