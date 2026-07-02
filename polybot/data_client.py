from __future__ import annotations

import logging
import time
from typing import Any

import requests

from polybot.models import Side, Trade, TraderStats

log = logging.getLogger(__name__)


class DataApiError(RuntimeError):
    pass


class DataApiClient:
    """Thin wrapper around Polymarket's public data-api.

    This is an unofficial, undocumented read-only API also used by the
    Polymarket website itself to show user activity/positions. Endpoints and
    field names may change without notice -- this client fails loudly
    (`DataApiError`) rather than silently returning wrong numbers so the
    copy-engine can skip a wallet instead of trading on bad data.
    """

    def __init__(self, base_url: str = "https://data-api.polymarket.com", timeout: float = 10.0):
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
                log.warning("data-api request failed (%s/%s) %s: %s", attempt + 1, retries, url, exc)
                time.sleep(min(2**attempt, 8))
        raise DataApiError(f"GET {url} failed after {retries} attempts: {last_exc}")

    def get_trades(self, user: str, limit: int = 100, after: int | None = None) -> list[Trade]:
        """Recent fills for `user`, oldest first."""
        params: dict[str, Any] = {"user": user, "limit": limit, "takerOnly": "false"}
        raw = self._get("/trades", params)
        if not isinstance(raw, list):
            raise DataApiError(f"unexpected /trades response shape for {user}: {type(raw)}")

        trades = []
        for item in raw:
            try:
                ts = int(item.get("timestamp", 0))
                if after is not None and ts <= after:
                    continue
                trades.append(
                    Trade(
                        trade_id=str(item.get("transactionHash", "")) + ":" + str(item.get("asset", "")) + ":" + str(ts),
                        trader=str(item.get("proxyWallet") or item.get("maker") or user).lower(),
                        condition_id=str(item.get("conditionId", "")),
                        token_id=str(item.get("asset", "")),
                        outcome=str(item.get("outcome", "")),
                        side=Side.BUY if str(item.get("side", "BUY")).upper() == "BUY" else Side.SELL,
                        price=float(item.get("price", 0.0)),
                        size=float(item.get("size", 0.0)),
                        timestamp=ts,
                        title=str(item.get("title", "")),
                    )
                )
            except (TypeError, ValueError) as exc:
                log.warning("skipping malformed trade entry for %s: %s (%s)", user, item, exc)
        trades.sort(key=lambda t: t.timestamp)
        return trades

    def get_positions_raw(self, user: str) -> list[dict[str, Any]]:
        raw = self._get("/positions", {"user": user, "limit": 500})
        if not isinstance(raw, list):
            raise DataApiError(f"unexpected /positions response shape for {user}: {type(raw)}")
        return raw

    def get_trader_stats(self, user: str, trade_limit: int = 500) -> TraderStats:
        """Best-effort trader scorecard built from public trade + position history.

        win/loss is derived from `realizedPnl` on positions the API reports as
        already having realized PnL (i.e. at least partially closed or
        resolved); it is a heuristic, not an authoritative accounting ledger.
        """
        trades = self.get_trades(user, limit=trade_limit)
        positions = self.get_positions_raw(user)

        total_volume_usd = sum(t.notional_usd for t in trades)
        open_positions = 0
        open_exposure_usd = 0.0
        wins = 0
        losses = 0

        for pos in positions:
            size = float(pos.get("size", 0.0) or 0.0)
            realized_pnl = float(pos.get("realizedPnl", 0.0) or 0.0)
            cur_price = float(pos.get("curPrice", 0.0) or 0.0)

            if size > 0:
                open_positions += 1
                open_exposure_usd += size * cur_price
            if realized_pnl > 0:
                wins += 1
            elif realized_pnl < 0:
                losses += 1

        estimated_bankroll_usd = open_exposure_usd

        return TraderStats(
            address=user.lower(),
            total_trades=len(trades),
            wins=wins,
            losses=losses,
            total_volume_usd=total_volume_usd,
            open_positions=open_positions,
            estimated_bankroll_usd=estimated_bankroll_usd,
        )
