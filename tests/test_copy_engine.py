import time

from polybot.broker import Broker
from polybot.config import Config, EngineConfig, FilterCriteria, SizingConfig
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
    def __init__(self, stats_by_wallet, trades_by_wallet):
        self.stats_by_wallet = stats_by_wallet
        self.trades_by_wallet = trades_by_wallet

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


def make_config(tmp_path, wallets):
    cfg = Config(
        mode="paper",
        target_wallets=wallets,
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


def make_trade(trade_id, trader, timestamp=None):
    if timestamp is None:
        timestamp = int(time.time())
    return Trade(
        trade_id=trade_id,
        trader=trader,
        condition_id="cond1",
        token_id="tok1",
        outcome="Yes",
        side=Side.BUY,
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
