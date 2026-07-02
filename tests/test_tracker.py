from polybot.config import PaperConfig
from polybot.models import Side
from polybot.paper_broker import PaperBroker
from polybot.state_store import load_json
from polybot.tracker import EquityTracker


def make_broker(tmp_path):
    return PaperBroker(
        PaperConfig(starting_balance_usd=1000.0, state_file=str(tmp_path / "paper.json"), slippage_bps=0.0)
    )


def test_snapshot_records_equity(tmp_path):
    broker = make_broker(tmp_path)
    broker.place_order("tok1", "cond1", "Yes", Side.BUY, 0.5, 100)  # $50 exposure
    tracker = EquityTracker(str(tmp_path / "equity.json"), interval_minutes=5)

    assert tracker.maybe_snapshot(broker)
    points = tracker.get_points()
    assert len(points) == 1
    assert points[0]["cash"] == 950.0
    assert points[0]["exposure"] == 50.0
    assert points[0]["equity"] == 1000.0


def test_snapshots_are_throttled(tmp_path):
    broker = make_broker(tmp_path)
    tracker = EquityTracker(str(tmp_path / "equity.json"), interval_minutes=5)

    assert tracker.maybe_snapshot(broker)
    assert not tracker.maybe_snapshot(broker)  # within the interval
    assert len(tracker.get_points()) == 1


def test_zero_interval_always_snapshots(tmp_path):
    broker = make_broker(tmp_path)
    tracker = EquityTracker(str(tmp_path / "equity.json"), interval_minutes=0)

    assert tracker.maybe_snapshot(broker)
    assert tracker.maybe_snapshot(broker)
    assert len(tracker.get_points()) == 2


def test_history_persists_across_instances(tmp_path):
    broker = make_broker(tmp_path)
    path = str(tmp_path / "equity.json")
    EquityTracker(path, interval_minutes=0).maybe_snapshot(broker)

    tracker2 = EquityTracker(path, interval_minutes=0)
    assert len(tracker2.get_points()) == 1
    assert load_json(path, [])[0]["equity"] == 1000.0
