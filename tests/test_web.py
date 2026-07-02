import json
import urllib.request

import pytest

from polybot.config import Config, PaperConfig, RiskConfig, WebConfig, EngineConfig
from polybot.models import Side
from polybot.paper_broker import PaperBroker
from polybot.risk import RiskGuardedBroker
from polybot.tracker import EquityTracker
from polybot.web import DashboardServer, build_summary, load_journal_rows


@pytest.fixture
def stack(tmp_path):
    config = Config(
        mode="paper",
        paper=PaperConfig(starting_balance_usd=1000.0, state_file=str(tmp_path / "paper.json"), slippage_bps=0.0),
        risk=RiskConfig(
            kill_switch_file=str(tmp_path / "HALT"),
            state_file=str(tmp_path / "risk.json"),
            min_buy_price=0.03,
            max_buy_price=0.97,
        ),
        engine=EngineConfig(journal_file=str(tmp_path / "journal.csv")),
        web=WebConfig(enabled=True, host="127.0.0.1", port=0),  # ephemeral port
    )
    broker = RiskGuardedBroker(PaperBroker(config.paper), config.risk)
    tracker = EquityTracker(str(tmp_path / "equity.json"), interval_minutes=0)
    return config, broker, tracker


def test_build_summary(stack):
    config, broker, _ = stack
    broker.place_order("tok1", "cond1", "Yes", Side.BUY, 0.5, 100)
    summary = build_summary(config, broker)

    assert summary["mode"] == "paper"
    assert summary["cash"] == 950.0
    assert summary["exposure"] == 50.0
    assert summary["positions"][0]["outcome"] == "Yes"
    assert summary["risk"]["buys_today"] == 1
    assert not summary["risk"]["halted"]


def test_load_journal_rows_missing_file(tmp_path):
    assert load_journal_rows(str(tmp_path / "nope.csv")) == []


def test_dashboard_endpoints(stack):
    config, broker, tracker = stack
    broker.place_order("tok1", "cond1", "Yes", Side.BUY, 0.5, 100)
    tracker.maybe_snapshot(broker)

    server = DashboardServer(config, broker, tracker)
    server.start_background()
    base = f"http://127.0.0.1:{server.port}"
    try:
        html = urllib.request.urlopen(f"{base}/").read().decode()
        assert "viewport" in html  # mobile-ready
        assert "PnL" in html

        summary = json.loads(urllib.request.urlopen(f"{base}/api/summary").read())
        assert summary["cash"] == 950.0

        equity = json.loads(urllib.request.urlopen(f"{base}/api/equity").read())
        assert len(equity) == 1
        assert equity[0]["equity"] == 1000.0

        journal = json.loads(urllib.request.urlopen(f"{base}/api/journal").read())
        assert journal == []

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"{base}/nope")
        assert excinfo.value.code == 404
    finally:
        server.stop()
