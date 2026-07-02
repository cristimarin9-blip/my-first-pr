import pytest

from polybot.config import Config


def test_defaults_to_paper_mode_with_no_files(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYBOT_MODE", raising=False)
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    cfg = Config.load(tmp_path / "missing_config.yaml", tmp_path / "missing.env")
    assert cfg.mode == "paper"
    assert cfg.is_paper


def test_live_mode_requires_private_key(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mode: live\n")
    with pytest.raises(ValueError):
        Config.load(config_path, tmp_path / "missing.env")


def test_live_mode_with_private_key_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xdeadbeef")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mode: live\n")
    cfg = Config.load(config_path, tmp_path / "missing.env")
    assert not cfg.is_paper
    assert cfg.live.private_key == "0xdeadbeef"


def test_watchlist_merges_target_wallets_and_file(tmp_path):
    watchlist_path = tmp_path / "watchlist.json"
    watchlist_path.write_text('["0xAAA", "0xbbb"]')
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"target_wallets: ['0xCCC']\nwatchlist_file: {watchlist_path}\n"
    )
    cfg = Config.load(config_path, tmp_path / "missing.env")
    assert cfg.load_watchlist() == ["0xccc", "0xaaa", "0xbbb"]


def test_no_fee_field_exists_anywhere_in_config(tmp_path):
    cfg = Config.load(tmp_path / "missing_config.yaml", tmp_path / "missing.env")
    for section in (cfg.sizing, cfg.paper, cfg.live, cfg.engine, cfg.filters):
        for field_name in vars(section):
            assert "fee" not in field_name.lower()
