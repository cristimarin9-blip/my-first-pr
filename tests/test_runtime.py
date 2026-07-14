from pathlib import Path

import pytest

from polybot.config import Config, EngineConfig, LeaderboardConfig, PaperConfig, RiskConfig, ThresholdConfig, WebConfig
from polybot.models import Side
from polybot.runtime import BotRuntime


def make_runtime(tmp_path, **cfg_overrides):
    cfg = Config(
        mode="paper",
        paper=PaperConfig(starting_balance_usd=1000.0, state_file=str(tmp_path / "paper.json"), slippage_bps=0.0),
        risk=RiskConfig(kill_switch_file=str(tmp_path / "HALT"), state_file=str(tmp_path / "risk.json")),
        engine=EngineConfig(
            journal_file=str(tmp_path / "j.csv"),
            equity_file=str(tmp_path / "e.json"),
            seen_trades_file=str(tmp_path / "seen.json"),
            log_file=str(tmp_path / "log.txt"),
        ),
        web=WebConfig(port=0),
    )
    for k, v in cfg_overrides.items():
        setattr(cfg, k, v)
    return BotRuntime(cfg, config_path=str(tmp_path / "config.yaml"), env_path=str(tmp_path / ".env"))


def test_resolution_is_the_default_engine(tmp_path):
    rt = make_runtime(tmp_path)
    names = [type(e).__name__ for e in rt.engines]
    # resolution runs by default (and first), copy also on by default
    assert names[0] == "ResolutionEngine"
    assert "CopyEngine" in names


def test_copy_can_be_disabled(tmp_path):
    rt = make_runtime(tmp_path, copy_enabled=False)
    names = [type(e).__name__ for e in rt.engines]
    assert "CopyEngine" not in names
    assert names == ["ResolutionEngine"]


def test_resolution_can_be_disabled(tmp_path):
    from polybot.config import ResolutionConfig

    rt = make_runtime(tmp_path, resolution=ResolutionConfig(enabled=False))
    names = [type(e).__name__ for e in rt.engines]
    assert "ResolutionEngine" not in names
    assert names == ["CopyEngine"]


def test_threshold_engine_added_when_enabled(tmp_path):
    rt = make_runtime(tmp_path, threshold=ThresholdConfig(enabled=True, markets=["0xabc"]))
    assert "ThresholdEngine" in [type(e).__name__ for e in rt.engines]


def test_pause_blocks_polling(tmp_path):
    rt = make_runtime(tmp_path)
    rt.dispatch("pause")
    assert rt.is_paused()
    # poll_once_all returns 0 and does not touch engines while paused
    assert rt.poll_once_all() == 0
    rt.dispatch("unpause")
    assert not rt.is_paused()


def test_halt_and_resume_kill_switch(tmp_path):
    rt = make_runtime(tmp_path)
    assert rt.dispatch("halt")["ok"]
    assert Path(rt.config.risk.kill_switch_file).exists()
    # kill switch blocks a BUY through the runtime's broker
    assert not rt.broker.place_order("t", "c", "Yes", Side.BUY, 0.5, 10).success
    assert rt.dispatch("resume")["ok"]
    assert not Path(rt.config.risk.kill_switch_file).exists()


def test_reset_paper_restores_balance(tmp_path):
    rt = make_runtime(tmp_path)
    rt.broker.place_order("t", "c", "Yes", Side.BUY, 0.5, 100)
    assert rt.broker.get_cash_balance() == 950.0
    assert rt.dispatch("reset_paper")["ok"]
    assert rt.broker.get_cash_balance() == 1000.0
    assert rt.broker.get_positions() == {}


def test_save_config_applies_and_persists(tmp_path):
    rt = make_runtime(tmp_path)
    rt.save_config({
        "target_wallets": ["0xABC", "0xdef"],
        "sections": {
            "risk": {"max_buys_per_day": 3, "min_buy_price": 0.9},
            "leaderboard": {"enabled": True},
        },
    })
    assert rt.config.risk.max_buys_per_day == 3
    assert rt.config.risk.min_buy_price == 0.9
    assert rt.config.leaderboard.enabled is True
    assert rt.config.target_wallets == ["0xabc", "0xdef"]

    # persisted to disk and reloadable
    assert Path(rt.config_path).exists()
    reloaded = Config.load(rt.config_path, rt.env_path)
    assert reloaded.risk.max_buys_per_day == 3


def test_save_config_never_writes_hidden_fields(tmp_path):
    rt = make_runtime(tmp_path)
    original_state_file = rt.config.risk.state_file
    # attempt to smuggle a hidden field in -- must be ignored
    rt.save_config({"sections": {"risk": {"state_file": "/evil/path", "max_buys_per_day": 9}}})
    assert rt.config.risk.state_file == original_state_file
    assert rt.config.risk.max_buys_per_day == 9


def test_save_config_invalid_value_rolls_back(tmp_path):
    rt = make_runtime(tmp_path)
    before = rt.config.threshold.trigger_probability
    # trigger_probability must be in [0.5, 1.0) -- 0.1 fails ThresholdConfig validation
    with pytest.raises(ValueError):
        rt.save_config({"sections": {"threshold": {"trigger_probability": 0.1}}})
    # config unchanged and no broken config.yaml committed
    assert rt.config.threshold.trigger_probability == before
    if Path(rt.config_path).exists():
        Config.load(rt.config_path, rt.env_path)  # whatever is on disk still loads


def test_editable_config_excludes_paths_and_secrets(tmp_path):
    rt = make_runtime(tmp_path)
    cfg = rt.editable_config()
    assert cfg["mode"] == "paper"
    assert "state_file" not in cfg["sections"]["risk"]
    assert "kill_switch_file" not in cfg["sections"]["risk"]
    assert "journal_file" not in cfg["sections"]["engine"]
    # a real tunable is present
    assert "max_buys_per_day" in cfg["sections"]["risk"]


def test_unknown_action(tmp_path):
    rt = make_runtime(tmp_path)
    result = rt.dispatch("frobnicate")
    assert not result["ok"] and "unknown" in result["error"]
