import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from polybot.config import Config, EngineConfig, LeaderboardConfig, PaperConfig, RiskConfig, WebConfig
from polybot.models import Side
from polybot.runtime import BotRuntime
from polybot.web import build_summary, load_journal_rows, tail_file, DashboardServer


def make_config(tmp_path, **overrides):
    cfg = Config(
        mode="paper",
        paper=PaperConfig(starting_balance_usd=1000.0, state_file=str(tmp_path / "paper.json"), slippage_bps=0.0),
        risk=RiskConfig(
            kill_switch_file=str(tmp_path / "HALT"),
            state_file=str(tmp_path / "risk.json"),
            min_buy_price=0.03,
            max_buy_price=0.97,
        ),
        engine=EngineConfig(
            journal_file=str(tmp_path / "journal.csv"),
            equity_file=str(tmp_path / "equity.json"),
            log_file=str(tmp_path / "trades.log"),
            seen_trades_file=str(tmp_path / "seen.json"),
        ),
        web=WebConfig(enabled=True, host="127.0.0.1", port=0),
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture
def runtime(tmp_path):
    cfg = make_config(tmp_path)
    return BotRuntime(cfg, config_path=str(tmp_path / "config.yaml"), env_path=str(tmp_path / ".env"))


def post_action(base, payload, token):
    req = urllib.request.Request(
        f"{base}/api/action",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Polybot-Token": token},
        method="POST",
    )
    return urllib.request.urlopen(req)


# --- helpers -----------------------------------------------------------------


def test_build_summary(runtime):
    runtime.broker.place_order("tok1", "cond1", "Yes", Side.BUY, 0.5, 100)
    summary = build_summary(runtime.config, runtime.broker)
    assert summary["mode"] == "paper"
    assert summary["cash"] == 950.0
    assert summary["exposure"] == 50.0
    assert summary["positions"][0]["outcome"] == "Yes"
    assert summary["risk"]["buys_today"] == 1


def test_load_journal_rows_missing_file(tmp_path):
    assert load_journal_rows(str(tmp_path / "nope.csv")) == []


def test_tail_file(tmp_path):
    p = tmp_path / "log.txt"
    p.write_text("\n".join(f"line{i}" for i in range(10)))
    assert tail_file(str(p), 3) == ["line7", "line8", "line9"]
    assert tail_file(str(tmp_path / "missing.txt")) == []


# --- HTTP endpoints ----------------------------------------------------------


def test_dashboard_endpoints(runtime):
    runtime.broker.place_order("tok1", "cond1", "Yes", Side.BUY, 0.5, 100)
    runtime.tracker.maybe_snapshot(runtime.broker)
    server = DashboardServer(runtime)
    server.start_background()
    base = f"http://127.0.0.1:{server.port}"
    try:
        html = urllib.request.urlopen(f"{base}/").read().decode()
        assert "viewport" in html and "PnL" in html and "Settings" in html

        summary = json.loads(urllib.request.urlopen(f"{base}/api/summary").read())
        assert summary["cash"] == 950.0
        assert summary["paused"] is False

        equity = json.loads(urllib.request.urlopen(f"{base}/api/equity").read())
        assert equity[0]["equity"] == 1000.0

        assert json.loads(urllib.request.urlopen(f"{base}/api/journal").read()) == []

        cfg = json.loads(urllib.request.urlopen(f"{base}/api/config").read())
        assert cfg["mode"] == "paper"
        assert "risk" in cfg["sections"]
        # secrets/paths never exposed in the editable config
        assert "state_file" not in cfg["sections"]["risk"]
        assert "kill_switch_file" not in cfg["sections"]["risk"]

        logs = json.loads(urllib.request.urlopen(f"{base}/api/logs").read())
        assert "lines" in logs

        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{base}/nope")
        assert exc.value.code == 404
    finally:
        server.stop()


def test_token_embedded_and_placeholder_substituted(runtime):
    server = DashboardServer(runtime)
    server.start_background()
    base = f"http://127.0.0.1:{server.port}"
    try:
        html = urllib.request.urlopen(f"{base}/").read().decode()
        assert server.control_token in html
        assert "CONTROLS_ENABLED = true" in html
        assert "__CONTROL_TOKEN__" not in html
    finally:
        server.stop()


def test_action_http_requires_token(runtime):
    server = DashboardServer(runtime)
    server.start_background()
    base = f"http://127.0.0.1:{server.port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            post_action(base, {"action": "halt"}, "wrong")
        assert exc.value.code == 403
        assert not Path(runtime.config.risk.kill_switch_file).exists()

        resp = post_action(base, {"action": "halt"}, server.control_token)
        assert json.loads(resp.read())["ok"] is True
        assert Path(runtime.config.risk.kill_switch_file).exists()
    finally:
        server.stop()


def test_action_http_disabled_returns_403(tmp_path):
    cfg = make_config(tmp_path)
    cfg.web = WebConfig(port=0, controls_enabled=False)
    rt = BotRuntime(cfg, config_path=str(tmp_path / "config.yaml"), env_path=str(tmp_path / ".env"))
    server = DashboardServer(rt)
    server.start_background()
    base = f"http://127.0.0.1:{server.port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            post_action(base, {"action": "halt"}, server.control_token)
        assert exc.value.code == 403
        html = urllib.request.urlopen(f"{base}/").read().decode()
        assert "CONTROLS_ENABLED = false" in html
    finally:
        server.stop()


def test_save_config_over_http(runtime):
    server = DashboardServer(runtime)
    server.start_background()
    base = f"http://127.0.0.1:{server.port}"
    try:
        payload = {
            "action": "save_config",
            "config": {
                "target_wallets": ["0xAAA"],
                "sections": {"risk": {"max_buys_per_day": 7}, "sizing": {"copy_ratio": 0.5}},
            },
        }
        resp = post_action(base, payload, server.control_token)
        assert json.loads(resp.read())["ok"] is True
        # applied live
        assert runtime.config.risk.max_buys_per_day == 7
        assert runtime.config.sizing.copy_ratio == 0.5
        assert runtime.config.target_wallets == ["0xaaa"]  # lowercased
        # persisted
        assert Path(runtime.config_path).exists()
    finally:
        server.stop()
