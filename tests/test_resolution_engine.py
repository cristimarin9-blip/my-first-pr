import time

import pytest

from polybot.config import PaperConfig, ResolutionConfig
from polybot.gamma_client import GammaClient, _iso, _parse_iso_ts
from polybot.paper_broker import PaperBroker
from polybot.resolution_engine import ResolutionEngine


class FakeGamma:
    def __init__(self, markets):
        self.markets = markets

    def get_markets_closing_within(self, hours, limit=200, min_liquidity_usd=0.0):
        # Mimic the real client: only return markets inside the window.
        now = time.time()
        return [m for m in self.markets if 0 < m["end_ts"] - now <= hours * 3600]


def market(cond, minutes_left, tokens, question="Q?"):
    return {
        "condition_id": cond,
        "question": question,
        "end_ts": time.time() + minutes_left * 60,
        "tokens": tokens,
    }


def make_engine(tmp_path, markets, **cfg):
    config = ResolutionConfig(
        enabled=True, within_hours=3.0, min_probability=0.90, max_probability=0.97,
        order_usd=10.0, state_file=str(tmp_path / "res.json"), **cfg,
    )
    broker = PaperBroker(
        PaperConfig(starting_balance_usd=1000.0, state_file=str(tmp_path / "paper.json"), slippage_bps=0.0)
    )
    return ResolutionEngine(config, broker, gamma_client=FakeGamma(markets)), broker


def test_buys_near_certain_outcome_closing_soon(tmp_path):
    engine, broker = make_engine(
        tmp_path, [market("c1", 90, [("tok_yes", "Yes", 0.93), ("tok_no", "No", 0.07)])]
    )
    assert engine.poll_once() == 1
    assert broker.get_positions()["tok_yes"].size == pytest.approx(10.0 / 0.93)


def test_ignores_market_closing_too_far_out(tmp_path):
    # 5 hours away, outside the 3h window
    engine, broker = make_engine(
        tmp_path, [market("c1", 300, [("tok_yes", "Yes", 0.95), ("tok_no", "No", 0.05)])]
    )
    assert engine.poll_once() == 0
    assert broker.get_positions() == {}


def test_ignores_outcome_below_threshold(tmp_path):
    engine, broker = make_engine(
        tmp_path, [market("c1", 60, [("tok_yes", "Yes", 0.80), ("tok_no", "No", 0.20)])]
    )
    assert engine.poll_once() == 0


def test_ignores_outcome_above_max(tmp_path):
    engine, broker = make_engine(
        tmp_path, [market("c1", 60, [("tok_yes", "Yes", 0.995), ("tok_no", "No", 0.005)])]
    )
    assert engine.poll_once() == 0


def test_buys_whichever_side_is_over_threshold(tmp_path):
    engine, broker = make_engine(
        tmp_path, [market("c1", 60, [("tok_yes", "Yes", 0.04), ("tok_no", "No", 0.96)])]
    )
    assert engine.poll_once() == 1
    assert "tok_no" in broker.get_positions()


def test_does_not_rebuy_same_outcome(tmp_path):
    engine, broker = make_engine(
        tmp_path, [market("c1", 60, [("tok_yes", "Yes", 0.93), ("tok_no", "No", 0.07)])]
    )
    engine.poll_once()
    assert engine.poll_once() == 0
    assert len(broker.get_positions()) == 1


def test_state_persists_across_instances(tmp_path):
    markets = [market("c1", 60, [("tok_yes", "Yes", 0.93), ("tok_no", "No", 0.07)])]
    engine, broker = make_engine(tmp_path, markets)
    engine.poll_once()

    engine2 = ResolutionEngine(engine.config, broker, gamma_client=FakeGamma(markets))
    assert engine2.poll_once() == 0


def test_skips_expired_market(tmp_path):
    engine, broker = make_engine(
        tmp_path, [market("c1", -10, [("tok_yes", "Yes", 0.95), ("tok_no", "No", 0.05)])]
    )
    assert engine.poll_once() == 0


def test_config_validation():
    with pytest.raises(ValueError):
        ResolutionConfig(within_hours=0)
    with pytest.raises(ValueError):
        ResolutionConfig(min_probability=0.3)
    with pytest.raises(ValueError):
        ResolutionConfig(min_probability=0.9, max_probability=0.8)


def test_iso_roundtrip():
    ts = 1_800_000_000
    assert _parse_iso_ts(_iso(ts)) == ts
    assert _parse_iso_ts("2026-07-02T20:00:00Z") is not None
    assert _parse_iso_ts(None) is None
    assert _parse_iso_ts("garbage") is None


class _StubbedGamma(GammaClient):
    def __init__(self, response):
        super().__init__()
        self.response = response
        self.last_params = None

    def _get(self, path, params, retries=3):
        self.last_params = params
        return self.response


def test_gamma_parses_closing_markets():
    now = time.time()
    resp = [
        {
            "conditionId": "0xcond",
            "question": "Will it rain?",
            "endDate": _iso(now + 3600),
            "clobTokenIds": '["tokA", "tokB"]',
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.92", "0.08"]',
        },
        {"conditionId": "0xbad", "endDate": None},  # skipped: no end date
    ]
    client = _StubbedGamma(resp)
    markets = client.get_markets_closing_within(3.0, min_liquidity_usd=100.0)
    assert len(markets) == 1
    m = markets[0]
    assert m["condition_id"] == "0xcond"
    assert m["tokens"][0] == ("tokA", "Yes", 0.92)
    assert client.last_params["liquidity_num_min"] == 100.0
    assert client.last_params["closed"] == "false"
