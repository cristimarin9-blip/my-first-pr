from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)


def _parse_iso_ts(value: str | None) -> float | None:
    """Parse a Gamma ISO-8601 timestamp (e.g. '2026-07-02T20:00:00Z') to unix seconds."""
    if not value:
        return None
    try:
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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

    def get_markets_closing_within(
        self, hours: float, limit: int = 200, min_liquidity_usd: float = 0.0
    ) -> list[dict[str, Any]]:
        """Active markets whose end date falls within the next `hours`.

        Returns dicts: {condition_id, question, end_ts, tokens}, where tokens is
        a list of (token_id, outcome_name, price) with price the outcome's
        current probability in [0, 1]. Sorted soonest-closing first.
        """
        now = time.time()
        params: dict[str, Any] = {
            "closed": "false",
            "active": "true",
            "archived": "false",
            "end_date_min": _iso(now),
            "end_date_max": _iso(now + hours * 3600),
            "limit": limit,
            "order": "endDate",
            "ascending": "true",
        }
        if min_liquidity_usd > 0:
            params["liquidity_num_min"] = min_liquidity_usd

        raw = self._get("/markets", params)
        if not isinstance(raw, list):
            raise GammaApiError(f"unexpected /markets response shape: {type(raw)}")

        markets = []
        for m in raw:
            end_ts = _parse_iso_ts(m.get("endDate"))
            if end_ts is None:
                continue
            try:
                token_ids = json.loads(m.get("clobTokenIds", "[]"))
                outcomes = json.loads(m.get("outcomes", "[]"))
                prices = json.loads(m.get("outcomePrices", "[]"))
            except (TypeError, ValueError):
                continue
            tokens = []
            for tid, outcome, price in zip(token_ids, outcomes, prices):
                try:
                    tokens.append((str(tid), str(outcome), float(price)))
                except (TypeError, ValueError):
                    continue
            if not tokens:
                continue
            markets.append(
                {
                    "condition_id": str(m.get("conditionId", "")),
                    "question": str(m.get("question", "")),
                    "end_ts": end_ts,
                    "tokens": tokens,
                }
            )
        markets.sort(key=lambda x: x["end_ts"])
        return markets

    def get_market_by_condition_id(self, condition_id: str, retries: int = 3) -> dict[str, Any] | None:
        raw = self._get("/markets", {"condition_ids": condition_id}, retries=retries)
        if not isinstance(raw, list) or not raw:
            return None
        return raw[0]

    def get_market_tokens(self, condition_id: str) -> list[tuple[str, str, float]]:
        """All outcome tokens for a market as (token_id, outcome_name, price) tuples.

        Price is the outcome's current probability in [0, 1]. Returns [] if the
        market can't be fetched or its fields don't parse.
        """
        market = self.get_market_by_condition_id(condition_id)
        if not market:
            return []
        try:
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            outcomes = json.loads(market.get("outcomes", "[]"))
            prices = json.loads(market.get("outcomePrices", "[]"))
        except (TypeError, ValueError):
            return []
        tokens = []
        for tid, outcome, price in zip(token_ids, outcomes, prices):
            try:
                tokens.append((str(tid), str(outcome), float(price)))
            except (TypeError, ValueError):
                continue
        return tokens

    def get_token_price(self, condition_id: str, token_id: str, retries: int = 3) -> float | None:
        """Best-effort last/mid price for a specific outcome token, in [0, 1]."""
        market = self.get_market_by_condition_id(condition_id, retries=retries)
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
