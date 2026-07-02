from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)


class GammaApiError(RuntimeError):
    pass


class GammaClient:
    """Wrapper around Polymarket's public Gamma markets API (market metadata & prices)."""

    def __init__(self, base_url: str = "https://gamma-api.polymarket.com", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _get(self, path: str, params: dict[str, Any] | None = None, retries: int = 3) -> Any:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                log.warning("gamma-api request failed (%s/%s) %s: %s", attempt + 1, retries, url, exc)
                time.sleep(min(2**attempt, 8))
        raise GammaApiError(f"GET {url} failed after {retries} attempts: {last_exc}")

    def get_market_by_condition_id(self, condition_id: str) -> dict[str, Any] | None:
        raw = self._get("/markets", {"condition_ids": condition_id})
        if not isinstance(raw, list) or not raw:
            return None
        return raw[0]

    def get_token_price(self, condition_id: str, token_id: str) -> float | None:
        """Best-effort last/mid price for a specific outcome token, in [0, 1]."""
        market = self.get_market_by_condition_id(condition_id)
        if not market:
            return None
        try:
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            prices = json.loads(market.get("outcomePrices", "[]"))
        except (TypeError, ValueError):
            return None
        for tid, price in zip(token_ids, prices):
            if str(tid) == str(token_id):
                try:
                    return float(price)
                except (TypeError, ValueError):
                    return None
        return None
