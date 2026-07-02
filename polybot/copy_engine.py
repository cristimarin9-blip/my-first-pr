from __future__ import annotations

import logging
import time

from polybot.broker import Broker
from polybot.config import Config
from polybot.data_client import DataApiClient, DataApiError
from polybot.models import Trade, TraderStats
from polybot.sizing import compute_copy_size
from polybot.state_store import load_json, save_json
from polybot.trader_filter import passes_filters

log = logging.getLogger(__name__)


class CopyEngine:
    """Polls a set of candidate wallets, filters them, and mirrors their new trades."""

    def __init__(
        self,
        config: Config,
        broker: Broker,
        data_client: DataApiClient | None = None,
    ):
        self.config = config
        self.broker = broker
        self.data_client = data_client or DataApiClient(config.data_api_url)
        self._seen_trade_ids: set[str] = set(
            load_json(config.engine.seen_trades_file, [])
        )
        self._startup_cutoff = int(time.time()) - config.engine.trade_lookback_seconds

    def _persist_seen(self) -> None:
        # Cap growth: keep the most recent 5000 ids, order isn't meaningful.
        trimmed = list(self._seen_trade_ids)[-5000:]
        save_json(self.config.engine.seen_trades_file, trimmed)

    def poll_once(self) -> int:
        """Run one evaluation pass over all candidate wallets. Returns trades copied."""
        wallets = self.config.load_watchlist()
        copied = 0
        for wallet in wallets:
            try:
                stats = self.data_client.get_trader_stats(wallet)
            except DataApiError as exc:
                log.warning("skipping %s: could not fetch trader stats (%s)", wallet, exc)
                continue

            if not passes_filters(stats, self.config.filters):
                log.debug("wallet %s does not pass filters, skipping", wallet)
                continue

            try:
                trades = self.data_client.get_trades(wallet, limit=50, after=self._startup_cutoff)
            except DataApiError as exc:
                log.warning("skipping %s: could not fetch trades (%s)", wallet, exc)
                continue

            for trade in trades:
                if trade.trade_id in self._seen_trade_ids:
                    continue
                self._seen_trade_ids.add(trade.trade_id)
                if self._copy_trade(trade, stats):
                    copied += 1

        self._persist_seen()
        return copied

    def _copy_trade(self, trade: Trade, stats: TraderStats) -> bool:
        our_bankroll = self.broker.get_cash_balance()
        exposure = self.broker.get_exposure_usd()
        size = compute_copy_size(trade, stats, our_bankroll, exposure, self.config.sizing)

        if size <= 0:
            log.debug("computed copy size <= 0 for trade %s, skipping", trade.trade_id)
            return False

        result = self.broker.place_order(
            token_id=trade.token_id,
            condition_id=trade.condition_id,
            outcome=trade.outcome,
            side=trade.side,
            price=trade.price,
            size=size,
        )
        if result.success:
            log.info(
                "copied %s %s from %s: %.4f shares @ %.4f ($%.2f)",
                trade.side.value, trade.title or trade.token_id, trade.trader,
                result.size, result.price, result.notional_usd,
            )
        else:
            log.warning("failed to copy trade %s from %s: %s", trade.trade_id, trade.trader, result.error)
        return result.success

    def run_forever(self) -> None:
        log.info(
            "copy-engine starting in %s mode, polling every %ss",
            "PAPER" if self.config.is_paper else "LIVE",
            self.config.engine.poll_interval_seconds,
        )
        while True:
            try:
                n = self.poll_once()
                if n:
                    log.info("copied %d new trade(s) this pass", n)
            except Exception:
                log.exception("unexpected error during poll, continuing")
            time.sleep(self.config.engine.poll_interval_seconds)
