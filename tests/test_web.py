import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from polybot.config import Config, LeaderboardConfig, PaperConfig, RiskConfig, WebConfig, EngineConfig
from polybot.models import Side
from polybot.paper_broker import PaperBroker
from polybot.risk import RiskGuardedBroker
from polybot.tracker import EquityTracker
from polybot.web import DashboardServer, build_summary, load_journal_rows


def post_action(base, action, token):
    req = urllib.request.Request(
        f"{base}/api/action",
        data=json.dumps({"action": action}).encode(),
        headers={"Content-Type": "application/json", "X-Polybot-Token": token},
        method="POST",
    )
    return urllib.request.urlopen(req)


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


# --- control actions ---------------------------------------------------------


def test_do_action_halt_and_resume(stack):
    config, broker, tracker = stack
    server = DashboardServer(config, broker, tracker)
    kill_file = Path(config.risk.kill_switch_file)

    r1 = server.do_action("halt")
    assert r1["ok"] and kill_file.exists()
    assert build_summary(config, broker)["risk"]["kill_switch"] is True
    # a BUY is now blocked by the kill switch, a SELL still goes through
    assert not broker.place_order("t1", "c1", "Yes", Side.BUY, 0.5, 10).success

    r2 = server.do_action("resume")
    assert r2["ok"] and not kill_file.exists()
    assert broker.place_order("t2", "c2", "Yes", Side.BUY, 0.5, 10).success


def test_do_action_unknown(stack):
    config, broker, tracker = stack
    result = DashboardServer(config, broker, tracker).do_action("nope")
    assert not result["ok"] and "unknown" in result["error"]


def test_do_action_refresh_leaderboard_disabled(stack):
    config, broker, tracker = stack  # leaderboard disabled by default
    result = DashboardServer(config, broker, tracker).do_action("refresh_leaderboard")
    assert not result["ok"] and "disabled" in result["error"]


def test_do_action_halt_noop_when_risk_disabled(tmp_path):
    config = Config(
        mode="paper",
        paper=PaperConfig(state_file=str(tmp_path / "p.json"), slippage_bps=0.0),
        risk=RiskConfig(enabled=False, kill_switch_file=str(tmp_path / "HALT")),
        web=WebConfig(port=0),
    )
    broker = PaperBroker(config.paper)
    tracker = EquityTracker(str(tmp_path / "e.json"), interval_minutes=0)
    result = DashboardServer(config, broker, tracker).do_action("halt")
    assert not result["ok"]
    assert not Path(config.risk.kill_switch_file).exists()


def test_action_http_requires_token(stack):
    config, broker, tracker = stack
    server = DashboardServer(config, broker, tracker)
    server.start_background()
    base = f"http://127.0.0.1:{server.port}"
    try:
        # wrong token -> 403, kill switch not created
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            post_action(base, "halt", "wrong-token")
        assert excinfo.value.code == 403
        assert not Path(config.risk.kill_switch_file).exists()

        # correct token -> 200 and action performed
        resp = post_action(base, "halt", server.control_token)
        assert json.loads(resp.read())["ok"] is True
        assert Path(config.risk.kill_switch_file).exists()
    finally:
        server.stop()


def test_action_http_disabled_returns_403(tmp_path):
    config = Config(
        mode="paper",
        paper=PaperConfig(state_file=str(tmp_path / "p.json"), slippage_bps=0.0),
        risk=RiskConfig(kill_switch_file=str(tmp_path / "HALT"), state_file=str(tmp_path / "r.json")),
        web=WebConfig(port=0, controls_enabled=False),
    )
    broker = RiskGuardedBroker(PaperBroker(config.paper), config.risk)
    tracker = EquityTracker(str(tmp_path / "e.json"), interval_minutes=0)
    server = DashboardServer(config, broker, tracker)
    server.start_background()
    base = f"http://127.0.0.1:{server.port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            post_action(base, "halt", server.control_token)
        assert excinfo.value.code == 403
        # controls-disabled page hides the token from JS
        html = urllib.request.urlopen(f"{base}/").read().decode()
        assert "CONTROLS_ENABLED = false" in html
    finally:
        server.stop()


def test_token_embedded_in_page(stack):
    config, broker, tracker = stack
    server = DashboardServer(config, broker, tracker)
    server.start_background()
    base = f"http://127.0.0.1:{server.port}"
    try:
        html = urllib.request.urlopen(f"{base}/").read().decode()
        assert server.control_token in html
        assert "CONTROLS_ENABLED = true" in html
        assert "__CONTROL_TOKEN__" not in html  # placeholder was substituted
    finally:
        server.stop()
