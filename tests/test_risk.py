import pytest

from polybot.config import PaperConfig, RiskConfig
from polybot.models import Side
from polybot.paper_broker import PaperBroker
from polybot.risk import RiskGuardedBroker


def make_broker(tmp_path, **risk_overrides):
    settings = dict(
        enabled=True,
        min_buy_price=0.03,
        max_buy_price=0.97,
        max_market_exposure_usd=100.0,
        max_buys_per_day=3,
        max_buy_notional_per_day_usd=120.0,
        daily_loss_limit_usd=20.0,
        kill_switch_file=str(tmp_path / "HALT"),
        state_file=str(tmp_path / "risk_state.json"),
    )
    settings.update(risk_overrides)
    inner = PaperBroker(
        PaperConfig(starting_balance_usd=1000.0, state_file=str(tmp_path / "paper.json"), slippage_bps=0.0)
    )
    return RiskGuardedBroker(inner, RiskConfig(**settings))


def buy(broker, token="tok1", cond="cond1", price=0.5, size=100):
    return broker.place_order(token, cond, "Yes", Side.BUY, price, size)


def sell(broker, token="tok1", cond="cond1", price=0.5, size=100):
    return broker.place_order(token, cond, "Yes", Side.SELL, price, size)


def test_normal_buy_passes_through(tmp_path):
    broker = make_broker(tmp_path)
    result = buy(broker, price=0.5, size=100)
    assert result.success
    assert broker.get_positions()["tok1"].size == 100


def test_price_band_rejects_longshots_and_near_certainties(tmp_path):
    broker = make_broker(tmp_path)
    low = buy(broker, price=0.01, size=100)
    high = buy(broker, price=0.99, size=10)
    assert not low.success and "band" in low.error
    assert not high.success and "band" in high.error
    assert broker.get_positions() == {}


def test_market_exposure_cap(tmp_path):
    broker = make_broker(tmp_path, max_buy_notional_per_day_usd=10_000.0)
    assert buy(broker, token="tok1", cond="cond1", price=0.5, size=150).success  # $75 in cond1
    second = buy(broker, token="tok2", cond="cond1", price=0.5, size=100)  # +$50 > $100 cap
    assert not second.success and "max_market_exposure_usd" in second.error
    # a different market is unaffected
    assert buy(broker, token="tok3", cond="cond2", price=0.5, size=80).success


def test_max_buys_per_day(tmp_path):
    broker = make_broker(tmp_path, max_buy_notional_per_day_usd=10_000.0, max_market_exposure_usd=10_000.0)
    for i in range(3):
        assert buy(broker, token=f"tok{i}", cond=f"cond{i}", price=0.5, size=10).success
    fourth = buy(broker, token="tok9", cond="cond9", price=0.5, size=10)
    assert not fourth.success and "max_buys_per_day" in fourth.error


def test_max_buy_notional_per_day(tmp_path):
    broker = make_broker(tmp_path, max_market_exposure_usd=10_000.0)
    assert buy(broker, token="tok1", cond="cond1", price=0.5, size=200).success  # $100
    second = buy(broker, token="tok2", cond="cond2", price=0.5, size=60)  # +$30 > $120 cap
    assert not second.success and "max_buy_notional_per_day_usd" in second.error


def test_daily_loss_limit_halts_buys_but_not_sells(tmp_path):
    broker = make_broker(tmp_path)
    buy(broker, token="tok1", cond="cond1", price=0.5, size=100)  # $50 in
    # sell at a big loss: realized = (0.2 - 0.5) * 100 = -$30 <= -$20 limit
    assert sell(broker, token="tok1", cond="cond1", price=0.2, size=100).success

    blocked = buy(broker, token="tok2", cond="cond2", price=0.5, size=10)
    assert not blocked.success and "halted" in blocked.error

    # exits still work while halted
    buy_state = broker.today_summary()
    assert buy_state["halted"]


def test_kill_switch_blocks_buys_allows_sells(tmp_path):
    broker = make_broker(tmp_path)
    assert buy(broker, price=0.5, size=100).success

    (tmp_path / "HALT").touch()
    blocked = buy(broker, token="tok2", cond="cond2", price=0.5, size=10)
    assert not blocked.success and "kill switch" in blocked.error
    assert sell(broker, price=0.6, size=50).success

    (tmp_path / "HALT").unlink()
    assert buy(broker, token="tok2", cond="cond2", price=0.5, size=10).success


def test_counters_reset_on_new_day(tmp_path):
    broker = make_broker(tmp_path)
    buy(broker, price=0.5, size=100)
    assert broker.today_summary()["buys_today"] == 1

    broker._date = "2000-01-01"  # simulate a stale state from yesterday
    summary = broker.today_summary()
    assert summary["buys_today"] == 0
    assert not summary["halted"]


def test_risk_state_persists_across_instances(tmp_path):
    broker = make_broker(tmp_path)
    buy(broker, price=0.5, size=100)

    broker2 = make_broker(tmp_path)
    assert broker2.today_summary()["buys_today"] == 1
    assert broker2._ledger["tok1"]["size"] == pytest.approx(100)


def test_failed_inner_order_does_not_count(tmp_path):
    broker = make_broker(tmp_path, max_buy_notional_per_day_usd=10_000.0, max_market_exposure_usd=10_000.0)
    # paper broker rejects: not enough cash
    result = buy(broker, price=0.5, size=1_000_000)
    assert not result.success
    assert broker.today_summary()["buys_today"] == 0
