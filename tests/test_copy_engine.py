import time

import dataclasses

from polybot.broker import Broker
from polybot.config import Config, ConsensusConfig, EngineConfig, FilterCriteria, RiskConfig, SizingConfig
from polybot.copy_engine import CopyEngine
from polybot.data_client import DataApiError
from polybot.models import OrderResult, Position, Side, Trade, TraderStats


class FakeBroker(Broker):
    def __init__(self, cash: float = 1000.0):
        self.cash = cash
        self.orders = []

    def get_cash_balance(self) -> float:
        return self.cash

    def get_positions(self) -> dict:
        return {}

    def place_order(self, token_id, condition_id, outcome, side, price, size) -> OrderResult:
        self.orders.append((token_id, side, price, size))
        return OrderResult(True, token_id, side, price, size, order_id="fake-1")


class FakeDataClient:
    def __init__(self, stats_by_wallet, trades_by_wallet, positions_by_wallet=None):
        self.stats_by_wallet = stats_by_wallet
        self.trades_by_wallet = trades_by_wallet
        self.positions_by_wallet = positions_by_wallet or {}

    def get_trader_stats(self, wallet, trade_limit=500):
        stats = self.stats_by_wallet.get(wallet)
        if stats is None:
            raise DataApiError(f"no stats for {wallet}")
        return stats

    def get_trades(self, wallet, limit=50, after=None):
        trades = self.trades_by_wallet.get(wallet, [])
        if after is not None:
            trades = [t for t in trades if t.timestamp > after]
        return trades

    def get_positions_raw(self, wallet):
        return self.positions_by_wallet.get(wallet, [])


GOOD_STATS = TraderStats(
    address="0xgood",
    total_trades=50,
    wins=40,
    losses=10,
    total_volume_usd=10_000.0,
    open_positions=2,
    estimated_bankroll_usd=1000.0,
)

BAD_STATS = TraderStats(
    address="0xbad",
    total_trades=50,
    wins=5,
    losses=45,
    total_volume_usd=10_000.0,
    open_positions=2,
    estimated_bankroll_usd=1000.0,
)


def make_config(tmp_path, wallets, consensus=None, risk=None):
    cfg = Config(
        mode="paper",
        target_wallets=wallets,
        consensus=consensus or ConsensusConfig(),
        risk=risk or RiskConfig(max_price_drift_bps=0),
        filters=FilterCriteria(min_trades=10, min_win_rate=0.6, min_volume_usd=100.0, max_open_positions=10, min_avg_trade_usd=1.0),
        sizing=SizingConfig(copy_ratio=1.0, max_position_usd=1000.0, max_total_exposure_usd=1000.0, min_order_usd=1.0),
        engine=EngineConfig(
            poll_interval_seconds=1,
            trade_lookback_seconds=10_000_000,
            seen_trades_file=str(tmp_path / "seen.json"),
            log_file=str(tmp_path / "trades.log"),
        ),
    )
    return cfg


def make_trade(trade_id, trader, timestamp=None, side=Side.BUY):
    if timestamp is None:
        timestamp = int(time.time())
    return Trade(
        trade_id=trade_id,
        trader=trader,
        condition_id="cond1",
        token_id="tok1",
        outcome="Yes",
        side=side,
        price=0.5,
        size=100.0,
        timestamp=timestamp,
    )


def test_copies_trade_from_qualified_wallet(tmp_path):
    cfg = make_config(tmp_path, ["0xgood"])
    data_client = FakeDataClient(
        {"0xgood": GOOD_STATS},
        {"0xgood": [make_trade("t1", "0xgood")]},
    )
    broker = FakeBroker()
    engine = CopyEngine(cfg, broker, data_client=data_client)

    copied = engine.poll_once()

    assert copied == 1
    assert len(broker.orders) == 1


def test_does_not_copy_from_unqualified_wallet(tmp_path):
    cfg = make_config(tmp_path, ["0xbad"])
    data_client = FakeDataClient(
        {"0xbad": BAD_STATS},
        {"0xbad": [make_trade("t1", "0xbad")]},
    )
    broker = FakeBroker()
    engine = CopyEngine(cfg, broker, data_client=data_client)

    copied = engine.poll_once()

    assert copied == 0
    assert broker.orders == []


def test_does_not_copy_same_trade_twice(tmp_path):
    cfg = make_config(tmp_path, ["0xgood"])
    data_client = FakeDataClient(
        {"0xgood": GOOD_STATS},
        {"0xgood": [make_trade("t1", "0xgood")]},
    )
    broker = FakeBroker()
    engine = CopyEngine(cfg, broker, data_client=data_client)

    engine.poll_once()
    copied_second_pass = engine.poll_once()

    assert copied_second_pass == 0
    assert len(broker.orders) == 1


def test_seen_trades_persist_across_engine_instances(tmp_path):
    cfg = make_config(tmp_path, ["0xgood"])
    data_client = FakeDataClient(
        {"0xgood": GOOD_STATS},
        {"0xgood": [make_trade("t1", "0xgood")]},
    )
    broker = FakeBroker()
    CopyEngine(cfg, broker, data_client=data_client).poll_once()

    broker2 = FakeBroker()
    copied = CopyEngine(cfg, broker2, data_client=data_client).poll_once()

    assert copied == 0
    assert broker2.orders == []


def test_data_api_error_is_skipped_not_raised(tmp_path):
    cfg = make_config(tmp_path, ["0xmissing"])
    data_client = FakeDataClient({}, {})
    broker = FakeBroker()
    engine = CopyEngine(cfg, broker, data_client=data_client)

    copied = engine.poll_once()  # should not raise

    assert copied == 0


class FakeLeaderboardWatchlist:
    def __init__(self, wallets):
        self.wallets = wallets

    def get_wallets(self, force_refresh=False):
        return list(self.wallets)


def test_leaderboard_wallets_merge_into_candidates(tmp_path):
    # 0xgood comes only from the leaderboard, not the static watchlist
    cfg = make_config(tmp_path, [])
    data_client = FakeDataClient(
        {"0xgood": GOOD_STATS},
        {"0xgood": [make_trade("t1", "0xgood")]},
    )
    broker = FakeBroker()
    engine = CopyEngine(
        cfg, broker, data_client=data_client, leaderboard=FakeLeaderboardWatchlist(["0xgood"])
    )

    assert engine.poll_once() == 1
    assert len(broker.orders) == 1


def test_leaderboard_wallets_still_must_pass_filters(tmp_path):
    cfg = make_config(tmp_path, [])
    data_client = FakeDataClient(
        {"0xbad": BAD_STATS},
        {"0xbad": [make_trade("t1", "0xbad")]},
    )
    broker = FakeBroker()
    engine = CopyEngine(
        cfg, broker, data_client=data_client, leaderboard=FakeLeaderboardWatchlist(["0xbad"])
    )

    assert engine.poll_once() == 0
    assert broker.orders == []


def test_leaderboard_duplicates_of_static_watchlist_are_deduped(tmp_path):
    cfg = make_config(tmp_path, ["0xgood"])
    data_client = FakeDataClient(
        {"0xgood": GOOD_STATS},
        {"0xgood": [make_trade("t1", "0xgood")]},
    )
    broker = FakeBroker()
    engine = CopyEngine(
        cfg, broker, data_client=data_client, leaderboard=FakeLeaderboardWatchlist(["0xgood"])
    )

    assert engine._candidate_wallets() == ["0xgood"]
    assert engine.poll_once() == 1
    assert len(broker.orders) == 1  # copied once, not once per source


class FakeGamma:
    def __init__(self, price):
        self.price = price

    def get_token_price(self, condition_id, token_id):
        return self.price


def make_drift_engine(tmp_path, current_price, drift_bps=200):
    cfg = make_config(tmp_path, ["0xgood"], risk=RiskConfig(max_price_drift_bps=drift_bps))
    data_client = FakeDataClient(
        {"0xgood": GOOD_STATS},
        {"0xgood": [make_trade("t1", "0xgood")]},  # trade price is 0.5
    )
    broker = FakeBroker()
    engine = CopyEngine(cfg, broker, data_client=data_client, gamma_client=FakeGamma(current_price))
    return engine, broker


def test_drift_guard_blocks_chasing(tmp_path):
    # traded at 0.50, market now 0.60 -> way past the 2% ceiling of 0.51
    engine, broker = make_drift_engine(tmp_path, current_price=0.60)
    assert engine.poll_once() == 0
    assert broker.orders == []


def test_drift_guard_allows_within_ceiling(tmp_path):
    engine, broker = make_drift_engine(tmp_path, current_price=0.505)
    assert engine.poll_once() == 1
    assert len(broker.orders) == 1


def test_drift_guard_fails_open_when_price_unavailable(tmp_path):
    engine, broker = make_drift_engine(tmp_path, current_price=None)
    assert engine.poll_once() == 1


def test_drift_guard_never_blocks_sells(tmp_path):
    cfg = make_config(tmp_path, ["0xgood"], risk=RiskConfig(max_price_drift_bps=200))
    data_client = FakeDataClient(
        {"0xgood": GOOD_STATS},
        {"0xgood": [make_trade("t1", "0xgood", side=Side.SELL)]},
    )
    broker = FakeBroker()
    engine = CopyEngine(cfg, broker, data_client=data_client, gamma_client=FakeGamma(0.9))
    assert engine.poll_once() == 1


CONSENSUS = ConsensusConfig(enabled=True, min_agreement=0.6, min_traders=2)
ALLY_STATS = dataclasses.replace(GOOD_STATS, address="0xally")


def make_consensus_engine(tmp_path, ally_holdings, trade=None):
    """Two qualified wallets; 0xgood makes a trade, 0xally's holdings vary."""
    cfg = make_config(tmp_path, ["0xgood", "0xally"], consensus=CONSENSUS)
    data_client = FakeDataClient(
        {"0xgood": GOOD_STATS, "0xally": ALLY_STATS},
        {"0xgood": [trade or make_trade("t1", "0xgood")]},
        positions_by_wallet={
            "0xgood": [{"conditionId": "cond1", "asset": "tok1", "size": 100}],
            "0xally": ally_holdings,
        },
    )
    broker = FakeBroker()
    return CopyEngine(cfg, broker, data_client=data_client), broker


def test_consensus_blocks_buy_when_traders_disagree(tmp_path):
    # ally holds the opposite outcome -> 1/2 = 50% < 60%
    engine, broker = make_consensus_engine(
        tmp_path, [{"conditionId": "cond1", "asset": "tok_other", "size": 50}]
    )
    assert engine.poll_once() == 0
    assert broker.orders == []


def test_consensus_blocks_buy_when_too_few_traders_have_a_stake(tmp_path):
    # ally has no position in this market -> only 1 opinionated trader < min_traders
    engine, broker = make_consensus_engine(tmp_path, [])
    assert engine.poll_once() == 0
    assert broker.orders == []


def test_consensus_allows_buy_when_traders_agree(tmp_path):
    engine, broker = make_consensus_engine(
        tmp_path, [{"conditionId": "cond1", "asset": "tok1", "size": 50}]
    )
    assert engine.poll_once() == 1
    assert len(broker.orders) == 1


def test_consensus_never_blocks_sells(tmp_path):
    engine, broker = make_consensus_engine(
        tmp_path,
        [{"conditionId": "cond1", "asset": "tok_other", "size": 50}],  # would veto a BUY
        trade=make_trade("t1", "0xgood", side=Side.SELL),
    )
    assert engine.poll_once() == 1
    assert broker.orders[0][1] == Side.SELL


def test_consensus_disabled_ignores_holdings(tmp_path):
    cfg = make_config(tmp_path, ["0xgood", "0xally"])  # consensus disabled by default
    data_client = FakeDataClient(
        {"0xgood": GOOD_STATS, "0xally": ALLY_STATS},
        {"0xgood": [make_trade("t1", "0xgood")]},
        positions_by_wallet={
            "0xally": [{"conditionId": "cond1", "asset": "tok_other", "size": 50}],
        },
    )
    broker = FakeBroker()
    engine = CopyEngine(cfg, broker, data_client=data_client)
    assert engine.poll_once() == 1
