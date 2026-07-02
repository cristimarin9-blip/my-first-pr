from polybot.config import ConsensusConfig
from polybot.consensus import evaluate_consensus
from polybot.models import Side, Trade

CONFIG = ConsensusConfig(enabled=True, min_agreement=0.6, min_traders=2)


def make_trade(trader="0xsource", token_id="tok_yes", condition_id="cond1", side=Side.BUY):
    return Trade(
        trade_id="t1",
        trader=trader,
        condition_id=condition_id,
        token_id=token_id,
        outcome="Yes",
        side=side,
        price=0.5,
        size=100.0,
        timestamp=1_700_000_000,
    )


def test_source_trader_always_counts_as_agreeing():
    result = evaluate_consensus(make_trade(), {"0xsource": []})
    assert result.agree == 1
    assert result.opinionated == 1


def test_lone_trader_fails_min_traders():
    # 1/1 = 100% agreement, but only one opinionated trader -> below min_traders
    result = evaluate_consensus(make_trade(), {"0xsource": []})
    assert not result.passes(CONFIG)


def test_unanimous_agreement_passes():
    holdings = {
        "0xsource": [("cond1", "tok_yes")],
        "0xother": [("cond1", "tok_yes")],
    }
    result = evaluate_consensus(make_trade(), holdings)
    assert result.agree == 2
    assert result.opinionated == 2
    assert result.passes(CONFIG)


def test_split_opinion_fails_threshold():
    # 1 of 2 opinionated traders agrees -> 50% < 60%
    holdings = {
        "0xsource": [("cond1", "tok_yes")],
        "0xother": [("cond1", "tok_no")],
    }
    result = evaluate_consensus(make_trade(), holdings)
    assert result.agree == 1
    assert result.opinionated == 2
    assert not result.passes(CONFIG)


def test_two_of_three_passes_sixty_percent():
    holdings = {
        "0xsource": [("cond1", "tok_yes")],
        "0xa": [("cond1", "tok_yes")],
        "0xb": [("cond1", "tok_no")],
    }
    result = evaluate_consensus(make_trade(), holdings)
    assert result.agree == 2
    assert result.opinionated == 3
    assert result.passes(CONFIG)


def test_positions_in_other_markets_are_ignored():
    holdings = {
        "0xsource": [],
        "0xother": [("cond_other", "tok_yes")],
    }
    result = evaluate_consensus(make_trade(), holdings)
    assert result.opinionated == 1  # only the source trader
    assert not result.passes(CONFIG)


def test_min_agreement_of_one_requires_unanimity():
    strict = ConsensusConfig(enabled=True, min_agreement=1.0, min_traders=2)
    unanimous = {
        "0xsource": [("cond1", "tok_yes")],
        "0xother": [("cond1", "tok_yes")],
    }
    split = {
        "0xsource": [("cond1", "tok_yes")],
        "0xother": [("cond1", "tok_no")],
    }
    assert evaluate_consensus(make_trade(), unanimous).passes(strict)
    assert not evaluate_consensus(make_trade(), split).passes(strict)
