import pytest

from polybot.config import PaperConfig, ThresholdConfig
from polybot.paper_broker import PaperBroker
from polybot.threshold_engine import ThresholdEngine


class FakeGammaClient:
    """Prices are mutable so tests can move the market between polls."""

    def __init__(self, tokens_by_market):
        # condition_id -> list of [token_id, outcome, price]
        self.tokens_by_market = tokens_by_market

    def get_market_tokens(self, condition_id):
        return [tuple(t) for t in self.tokens_by_market.get(condition_id, [])]

    def get_token_price(self, condition_id, token_id):
        for tid, _, price in self.tokens_by_market.get(condition_id, []):
            if tid == token_id:
                return price
        return None

    def set_price(self, condition_id, token_id, price):
        for t in self.tokens_by_market[condition_id]:
            if t[0] == token_id:
                t[2] = price


def make_engine(tmp_path, tokens_by_market, **config_overrides):
    settings = dict(
        enabled=True,
        markets=list(tokens_by_market),
        trigger_probability=0.90,
        max_entry_probability=0.98,
        order_usd=10.0,
        take_profit_probability=0.99,
        stop_loss_probability=0.50,
        state_file=str(tmp_path / "threshold_state.json"),
    )
    settings.update(config_overrides)
    config = ThresholdConfig(**settings)
    broker = PaperBroker(
        PaperConfig(starting_balance_usd=1000.0, state_file=str(tmp_path / "paper.json"), slippage_bps=0.0)
    )
    gamma = FakeGammaClient(tokens_by_market)
    return ThresholdEngine(config, broker, gamma_client=gamma), broker, gamma


def test_no_entry_below_trigger(tmp_path):
    engine, broker, _ = make_engine(
        tmp_path, {"cond1": [["tok_yes", "Yes", 0.80], ["tok_no", "No", 0.20]]}
    )
    assert engine.poll_once() == 0
    assert broker.get_positions() == {}


def test_enters_yes_when_it_reaches_trigger(tmp_path):
    engine, broker, _ = make_engine(
        tmp_path, {"cond1": [["tok_yes", "Yes", 0.91], ["tok_no", "No", 0.09]]}
    )
    assert engine.poll_once() == 1
    pos = broker.get_positions()["tok_yes"]
    assert pos.size == pytest.approx(10.0 / 0.91)


def test_enters_no_side_too(tmp_path):
    engine, broker, _ = make_engine(
        tmp_path, {"cond1": [["tok_yes", "Yes", 0.08], ["tok_no", "No", 0.92]]}
    )
    assert engine.poll_once() == 1
    assert "tok_no" in broker.get_positions()


def test_skips_entry_above_max_probability(tmp_path):
    engine, broker, _ = make_engine(
        tmp_path, {"cond1": [["tok_yes", "Yes", 0.99], ["tok_no", "No", 0.01]]}
    )
    assert engine.poll_once() == 0
    assert broker.get_positions() == {}


def test_does_not_enter_same_outcome_twice(tmp_path):
    engine, broker, _ = make_engine(
        tmp_path, {"cond1": [["tok_yes", "Yes", 0.91], ["tok_no", "No", 0.09]]}
    )
    engine.poll_once()
    assert engine.poll_once() == 0  # still above trigger, but already entered
    assert broker.get_positions()["tok_yes"].size == pytest.approx(10.0 / 0.91)


def test_take_profit_exit(tmp_path):
    engine, broker, gamma = make_engine(
        tmp_path, {"cond1": [["tok_yes", "Yes", 0.91], ["tok_no", "No", 0.09]]}
    )
    engine.poll_once()
    gamma.set_price("cond1", "tok_yes", 0.995)
    assert engine.poll_once() == 1
    assert "tok_yes" not in broker.get_positions()
    assert broker.realized_pnl_usd > 0


def test_stop_loss_exit(tmp_path):
    engine, broker, gamma = make_engine(
        tmp_path, {"cond1": [["tok_yes", "Yes", 0.91], ["tok_no", "No", 0.09]]}
    )
    engine.poll_once()
    gamma.set_price("cond1", "tok_yes", 0.45)
    assert engine.poll_once() == 1
    assert "tok_yes" not in broker.get_positions()
    assert broker.realized_pnl_usd < 0


def test_no_reentry_after_exit(tmp_path):
    engine, broker, gamma = make_engine(
        tmp_path, {"cond1": [["tok_yes", "Yes", 0.91], ["tok_no", "No", 0.09]]}
    )
    engine.poll_once()
    gamma.set_price("cond1", "tok_yes", 0.45)  # stop-loss fires
    engine.poll_once()
    gamma.set_price("cond1", "tok_yes", 0.92)  # climbs back above trigger
    assert engine.poll_once() == 0
    assert "tok_yes" not in broker.get_positions()


def test_disabled_exit_rules_hold_position(tmp_path):
    engine, broker, gamma = make_engine(
        tmp_path,
        {"cond1": [["tok_yes", "Yes", 0.91], ["tok_no", "No", 0.09]]},
        take_profit_probability=0.0,
        stop_loss_probability=0.0,
    )
    engine.poll_once()
    gamma.set_price("cond1", "tok_yes", 0.995)
    assert engine.poll_once() == 0
    assert "tok_yes" in broker.get_positions()


def test_state_persists_across_instances(tmp_path):
    tokens = {"cond1": [["tok_yes", "Yes", 0.91], ["tok_no", "No", 0.09]]}
    engine, broker, _ = make_engine(tmp_path, tokens)
    engine.poll_once()

    engine2 = ThresholdEngine(engine.config, broker, gamma_client=FakeGammaClient(tokens))
    assert engine2.poll_once() == 0  # remembers the entry, no double-buy


def test_config_rejects_bad_trigger():
    with pytest.raises(ValueError):
        ThresholdConfig(trigger_probability=0.3)
    with pytest.raises(ValueError):
        ThresholdConfig(trigger_probability=0.9, max_entry_probability=0.8)
    with pytest.raises(ValueError):
        ThresholdConfig(trigger_probability=0.9, stop_loss_probability=0.95)
