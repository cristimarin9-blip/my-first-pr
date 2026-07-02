from __future__ import annotations

import logging
import time

from polybot.broker import Broker
from polybot.config import Config
from polybot.consensus import Holdings, evaluate_consensus
from polybot.data_client import DataApiClient, DataApiError
from polybot.gamma_client import GammaClient
from polybot.journal import TradeJournal
from polybot.leaderboard import LeaderboardClient, LeaderboardWatchlist
from polybot.models import Side, Trade, TraderStats
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
        leaderboard: LeaderboardWatchlist | None = None,
        gamma_client: GammaClient | None = None,
        journal: TradeJournal | None = None,
    ):
        self.config = config
        self.broker = broker
        self.data_client = data_client or DataApiClient(config.data_api_url)
        self.journal = journal
        self._gamma = gamma_client
        if self._gamma is None and config.risk.max_price_drift_bps > 0:
            self._gamma = GammaClient(config.gamma_api_url)
        if leaderboard is not None:
            self.leaderboard = leaderboard
        elif config.leaderboard.enabled:
            self.leaderboard = LeaderboardWatchlist(
                config.leaderboard, LeaderboardClient(config.data_api_url)
            )
        else:
            self.leaderboard = None
        self._seen_trade_ids: set[str] = set(
            load_json(config.engine.seen_trades_file, [])
        )
        self._startup_cutoff = int(time.time()) - config.engine.trade_lookback_seconds

    def _persist_seen(self) -> None:
        # Cap growth: keep the most recent 5000 ids, order isn't meaningful.
        trimmed = list(self._seen_trade_ids)[-5000:]
        save_json(self.config.engine.seen_trades_file, trimmed)

    def _candidate_wallets(self) -> list[str]:
        """Static watchlist merged with leaderboard wallets (if enabled), deduped."""
        wallets = self.config.load_watchlist()
        if self.leaderboard is not None:
            known = set(wallets)
            for wallet in self.leaderboard.get_wallets():
                if wallet not in known:
                    known.add(wallet)
                    wallets.append(wallet)
        return wallets

    def poll_once(self) -> int:
        """Run one evaluation pass over all candidate wallets. Returns trades copied."""
        wallets = self._candidate_wallets()

        qualified: dict[str, TraderStats] = {}
        for wallet in wallets:
            try:
                stats = self.data_client.get_trader_stats(wallet)
            except DataApiError as exc:
                log.warning("skipping %s: could not fetch trader stats (%s)", wallet, exc)
                continue
            if passes_filters(stats, self.config.filters):
                qualified[wallet] = stats
            else:
                log.debug("wallet %s does not pass filters, skipping", wallet)

        holdings_by_trader: dict[str, Holdings] | None = None
        if self.config.consensus.enabled:
            holdings_by_trader = self._fetch_holdings(qualified)

        copied = 0
        for wallet, stats in qualified.items():
            try:
                trades = self.data_client.get_trades(wallet, limit=50, after=self._startup_cutoff)
            except DataApiError as exc:
                log.warning("skipping %s: could not fetch trades (%s)", wallet, exc)
                continue

            for trade in trades:
                if trade.trade_id in self._seen_trade_ids:
                    continue
                self._seen_trade_ids.add(trade.trade_id)
                if not self._passes_consensus(trade, holdings_by_trader):
                    continue
                if self._copy_trade(trade, stats):
                    copied += 1

        self._persist_seen()
        return copied

    def _fetch_holdings(self, qualified: dict[str, TraderStats]) -> dict[str, Holdings]:
        """Open (condition_id, token_id) holdings for every qualified trader."""
        holdings_by_trader: dict[str, Holdings] = {}
        for wallet in qualified:
            try:
                raw = self.data_client.get_positions_raw(wallet)
            except DataApiError as exc:
                log.warning("consensus: could not fetch positions for %s, treating as no holdings (%s)", wallet, exc)
                raw = []
            holdings: Holdings = []
            for pos in raw:
                try:
                    size = float(pos.get("size", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                if size > 0:
                    holdings.append((str(pos.get("conditionId", "")), str(pos.get("asset", ""))))
            holdings_by_trader[wallet] = holdings
        return holdings_by_trader

    def _passes_consensus(self, trade: Trade, holdings_by_trader: dict[str, Holdings] | None) -> bool:
        if holdings_by_trader is None:
            return True
        # Never block an exit: if qualified traders are getting out, staying
        # in because "not enough of them agree on selling" just adds risk.
        if trade.side == Side.SELL:
            return True

        result = evaluate_consensus(trade, holdings_by_trader)
        if result.passes(self.config.consensus):
            return True
        log.info(
            "consensus veto: %s on %s has %d/%d qualified traders (%.0f%%), need >=%.0f%% of >=%d",
            trade.side.value, trade.title or trade.token_id,
            result.agree, result.opinionated, result.fraction * 100,
            self.config.consensus.min_agreement * 100, self.config.consensus.min_traders,
        )
        return False

    def _passes_drift_guard(self, trade: Trade) -> bool:
        """Skip BUYs the market has already run away from.

        Copying only makes sense at (roughly) the price the trader got. If
        the market has since moved more than `risk.max_price_drift_bps` above
        their fill, we'd be chasing -- worse entry, worse odds. Fails open
        when the current price can't be fetched. Never blocks SELLs.
        """
        max_drift = self.config.risk.max_price_drift_bps
        if max_drift <= 0 or trade.side != Side.BUY or self._gamma is None:
            return True
        current = self._gamma.get_token_price(trade.condition_id, trade.token_id)
        if current is None:
            return True
        ceiling = trade.price * (1 + max_drift / 10_000.0)
        if current > ceiling:
            log.info(
                "drift veto: %s traded at %.4f but market is now %.4f (> %.4f ceiling)",
                trade.title or trade.token_id, trade.price, current, ceiling,
            )
            return False
        return True

    def _copy_trade(self, trade: Trade, stats: TraderStats) -> bool:
        if not self._passes_drift_guard(trade):
            return False
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
            if self.journal:
                self.journal.record(
                    strategy="copy",
                    source=trade.trader,
                    market=trade.title,
                    condition_id=trade.condition_id,
                    outcome=trade.outcome,
                    result=result,
                )
        else:
            log.warning("failed to copy trade %s from %s: %s", trade.trade_id, trade.trader, result.error)
        return result.success

