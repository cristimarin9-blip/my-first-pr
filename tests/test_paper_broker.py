import pytest

from polybot.config import PaperConfig
from polybot.models import Side
from polybot.paper_broker import PaperBroker


@pytest.fixture
def broker(tmp_path):
    cfg = PaperConfig(
        starting_balance_usd=1000.0,
        state_file=str(tmp_path / "paper_state.json"),
        slippage_bps=0.0,
    )
    return PaperBroker(cfg)


def test_starting_balance(broker):
    assert broker.get_cash_balance() == 1000.0
    assert broker.get_positions() == {}


def test_buy_reduces_cash_and_opens_position(broker):
    result = broker.place_order("tok1", "cond1", "Yes", Side.BUY, price=0.5, size=100)
    assert result.success
    assert broker.get_cash_balance() == 950.0
    positions = broker.get_positions()
    assert positions["tok1"].size == 100
    assert positions["tok1"].avg_price == 0.5


def test_buy_averages_price_across_fills(broker):
    broker.place_order("tok1", "cond1", "Yes", Side.BUY, price=0.5, size=100)
    broker.place_order("tok1", "cond1", "Yes", Side.BUY, price=0.7, size=100)
    pos = broker.get_positions()["tok1"]
    assert pos.size == 200
    assert pos.avg_price == pytest.approx(0.6)


def test_sell_realizes_pnl_and_closes_position(broker):
    broker.place_order("tok1", "cond1", "Yes", Side.BUY, price=0.5, size=100)
    result = broker.place_order("tok1", "cond1", "Yes", Side.SELL, price=0.8, size=100)
    assert result.success
    assert broker.realized_pnl_usd == pytest.approx(30.0)
    assert "tok1" not in broker.get_positions()
    # started with 1000, spent 50 on buy, received 80 on sell
    assert broker.get_cash_balance() == pytest.approx(1030.0)


def test_cannot_sell_more_than_held(broker):
    broker.place_order("tok1", "cond1", "Yes", Side.BUY, price=0.5, size=100)
    result = broker.place_order("tok1", "cond1", "Yes", Side.SELL, price=0.8, size=200)
    assert not result.success
    assert "cannot sell" in result.error


def test_insufficient_cash_rejected(broker):
    result = broker.place_order("tok1", "cond1", "Yes", Side.BUY, price=0.5, size=10_000)
    assert not result.success
    assert "insufficient" in result.error


def test_no_fee_is_ever_charged(broker):
    # buying and immediately selling at the same price should be a perfect
    # wash with zero slippage configured -- no hidden fee deduction.
    broker.place_order("tok1", "cond1", "Yes", Side.BUY, price=0.5, size=100)
    broker.place_order("tok1", "cond1", "Yes", Side.SELL, price=0.5, size=100)
    assert broker.get_cash_balance() == 1000.0
    assert broker.realized_pnl_usd == 0.0


def test_state_persists_across_instances(tmp_path):
    cfg = PaperConfig(starting_balance_usd=1000.0, state_file=str(tmp_path / "state.json"), slippage_bps=0.0)
    b1 = PaperBroker(cfg)
    b1.place_order("tok1", "cond1", "Yes", Side.BUY, price=0.5, size=100)

    b2 = PaperBroker(cfg)
    assert b2.get_cash_balance() == 950.0
    assert b2.get_positions()["tok1"].size == 100


def test_slippage_applied_to_fill_price(tmp_path):
    cfg = PaperConfig(starting_balance_usd=1000.0, state_file=str(tmp_path / "s.json"), slippage_bps=100)
    b = PaperBroker(cfg)
    result = b.place_order("tok1", "cond1", "Yes", Side.BUY, price=0.5, size=100)
    assert result.price == pytest.approx(0.505)
