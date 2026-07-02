import pytest

from polybot.config import LeaderboardConfig
from polybot.leaderboard import LeaderboardClient, LeaderboardError, LeaderboardWatchlist
from polybot.state_store import load_json, save_json


class FakeLeaderboardClient:
    def __init__(self, wallets=None, fail=False):
        self.wallets = wallets or []
        self.fail = fail
        self.calls = 0

    def get_top_wallets(self, category, time_period, order_by, limit):
        self.calls += 1
        if self.fail:
            raise LeaderboardError("simulated API failure")
        return list(self.wallets[:limit])


def make_config(tmp_path, **overrides):
    settings = dict(
        enabled=True,
        top_n=25,
        refresh_hours=24.0,
        cache_file=str(tmp_path / "leaderboard.json"),
    )
    settings.update(overrides)
    return LeaderboardConfig(**settings)


# --- client parsing / validation --------------------------------------------


class StubbedClient(LeaderboardClient):
    """LeaderboardClient with the HTTP layer replaced by a canned response."""

    def __init__(self, response):
        super().__init__()
        self.response = response
        self.last_params = None

    def _get(self, path, params, retries=3):
        self.last_params = params
        return self.response


def test_client_extracts_and_lowercases_wallets():
    client = StubbedClient(
        [
            {"rank": 1, "proxyWallet": "0xAbC1", "pnl": 1000},
            {"rank": 2, "proxyWallet": "0xDeF2", "pnl": 500},
            {"rank": 3, "userName": "no-wallet-entry"},
        ]
    )
    assert client.get_top_wallets() == ["0xabc1", "0xdef2"]


def test_client_tolerates_wrapped_response():
    client = StubbedClient({"leaderboard": [{"proxyWallet": "0xAAA"}]})
    assert client.get_top_wallets() == ["0xaaa"]


def test_client_clamps_limit_and_uppercases_params():
    client = StubbedClient([])
    client.get_top_wallets(category="sports", time_period="week", order_by="vol", limit=999)
    assert client.last_params == {
        "category": "SPORTS",
        "timePeriod": "WEEK",
        "orderBy": "VOL",
        "limit": 50,
    }


def test_client_rejects_invalid_params():
    client = StubbedClient([])
    with pytest.raises(ValueError):
        client.get_top_wallets(category="NOT_A_CATEGORY")
    with pytest.raises(ValueError):
        client.get_top_wallets(time_period="FORTNIGHT")
    with pytest.raises(ValueError):
        client.get_top_wallets(order_by="LUCK")


# --- cached watchlist --------------------------------------------------------


def test_fetches_and_caches(tmp_path):
    config = make_config(tmp_path)
    client = FakeLeaderboardClient(wallets=["0xaaa", "0xbbb"])
    watchlist = LeaderboardWatchlist(config, client)

    assert watchlist.get_wallets() == ["0xaaa", "0xbbb"]
    assert client.calls == 1
    cache = load_json(config.cache_file, None)
    assert cache["wallets"] == ["0xaaa", "0xbbb"]


def test_fresh_cache_skips_fetch(tmp_path):
    config = make_config(tmp_path)
    client = FakeLeaderboardClient(wallets=["0xaaa"])
    watchlist = LeaderboardWatchlist(config, client)

    watchlist.get_wallets()
    watchlist.get_wallets()
    assert client.calls == 1


def test_stale_cache_triggers_refetch(tmp_path):
    config = make_config(tmp_path)
    save_json(config.cache_file, {"fetched_at": 0, "wallets": ["0xold"]})
    client = FakeLeaderboardClient(wallets=["0xnew"])
    watchlist = LeaderboardWatchlist(config, client)

    assert watchlist.get_wallets() == ["0xnew"]
    assert client.calls == 1


def test_force_refresh_ignores_fresh_cache(tmp_path):
    config = make_config(tmp_path)
    client = FakeLeaderboardClient(wallets=["0xaaa"])
    watchlist = LeaderboardWatchlist(config, client)

    watchlist.get_wallets()
    watchlist.get_wallets(force_refresh=True)
    assert client.calls == 2


def test_fetch_failure_falls_back_to_stale_cache(tmp_path):
    config = make_config(tmp_path)
    save_json(config.cache_file, {"fetched_at": 0, "wallets": ["0xstale"]})
    client = FakeLeaderboardClient(fail=True)
    watchlist = LeaderboardWatchlist(config, client)

    assert watchlist.get_wallets() == ["0xstale"]


def test_fetch_failure_with_no_cache_returns_empty(tmp_path):
    config = make_config(tmp_path)
    client = FakeLeaderboardClient(fail=True)
    watchlist = LeaderboardWatchlist(config, client)

    assert watchlist.get_wallets() == []
