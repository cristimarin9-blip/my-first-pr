from __future__ import annotations

import logging
import time
from typing import Any

import requests

from polybot.config import LeaderboardConfig
from polybot.state_store import load_json, save_json

log = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "OVERALL", "POLITICS", "SPORTS", "ESPORTS", "CRYPTO", "CULTURE",
    "MENTIONS", "WEATHER", "ECONOMICS", "TECH", "FINANCE",
}
VALID_TIME_PERIODS = {"DAY", "WEEK", "MONTH", "ALL"}
VALID_ORDER_BY = {"PNL", "VOL"}


class LeaderboardError(RuntimeError):
    pass


class LeaderboardClient:
    """Fetches Polymarket's trader leaderboard (data-api /v1/leaderboard)."""

    def __init__(self, base_url: str = "https://data-api.polymarket.com", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _get(self, path: str, params: dict[str, Any], retries: int = 3) -> Any:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                log.warning("leaderboard request failed (%s/%s) %s: %s", attempt + 1, retries, url, exc)
                time.sleep(min(2**attempt, 8))
        raise LeaderboardError(f"GET {url} failed after {retries} attempts: {last_exc}")

    def get_top_wallets(
        self,
        category: str = "OVERALL",
        time_period: str = "MONTH",
        order_by: str = "PNL",
        limit: int = 25,
    ) -> list[str]:
        """Wallet addresses of the top traders, best rank first."""
        category = category.upper()
        time_period = time_period.upper()
        order_by = order_by.upper()
        if category not in VALID_CATEGORIES:
            raise ValueError(f"invalid leaderboard category {category!r}, expected one of {sorted(VALID_CATEGORIES)}")
        if time_period not in VALID_TIME_PERIODS:
            raise ValueError(f"invalid leaderboard time_period {time_period!r}, expected one of {sorted(VALID_TIME_PERIODS)}")
        if order_by not in VALID_ORDER_BY:
            raise ValueError(f"invalid leaderboard order_by {order_by!r}, expected one of {sorted(VALID_ORDER_BY)}")
        limit = max(1, min(limit, 50))  # API accepts 1-50

        raw = self._get(
            "/v1/leaderboard",
            {"category": category, "timePeriod": time_period, "orderBy": order_by, "limit": limit},
        )
        if isinstance(raw, dict):  # tolerate a wrapped response shape
            raw = raw.get("leaderboard") or raw.get("data") or []
        if not isinstance(raw, list):
            raise LeaderboardError(f"unexpected leaderboard response shape: {type(raw)}")

        wallets = []
        for entry in raw:
            wallet = str(entry.get("proxyWallet", "") or "").lower()
            if wallet.startswith("0x"):
                wallets.append(wallet)
            else:
                log.warning("skipping leaderboard entry without proxyWallet: %s", entry)
        return wallets


class LeaderboardWatchlist:
    """Cached wallet list auto-populated from the leaderboard.

    Refetches at most every `refresh_hours`; on fetch failure it falls back
    to the last cached list so a flaky API never empties the watchlist
    mid-run. The cache file survives restarts.
    """

    def __init__(self, config: LeaderboardConfig, client: LeaderboardClient | None = None):
        self.config = config
        self.client = client or LeaderboardClient()

    def get_wallets(self, force_refresh: bool = False) -> list[str]:
        cache = load_json(self.config.cache_file, None)
        now = time.time()
        max_age = self.config.refresh_hours * 3600

        if not force_refresh and cache and now - cache.get("fetched_at", 0) < max_age:
            return list(cache.get("wallets", []))

        try:
            wallets = self.client.get_top_wallets(
                category=self.config.category,
                time_period=self.config.time_period,
                order_by=self.config.order_by,
                limit=self.config.top_n,
            )
        except LeaderboardError as exc:
            stale = list(cache.get("wallets", [])) if cache else []
            log.warning(
                "leaderboard refresh failed, using %d cached wallet(s): %s", len(stale), exc
            )
            return stale

        save_json(self.config.cache_file, {"fetched_at": now, "wallets": wallets})
        log.info(
            "leaderboard refreshed: %d wallet(s) (%s / %s / %s)",
            len(wallets), self.config.category, self.config.time_period, self.config.order_by,
        )
        return wallets
