from __future__ import annotations

import argparse
import logging
import sys
import time

from polybot.broker import Broker
from polybot.config import Config
from polybot.copy_engine import CopyEngine
from polybot.journal import TradeJournal
from polybot.logging_setup import setup_logging
from polybot.risk import RiskGuardedBroker
from polybot.threshold_engine import ThresholdEngine

log = logging.getLogger(__name__)


def build_broker(config: Config) -> Broker:
    if config.is_paper:
        from polybot.paper_broker import PaperBroker

        broker: Broker = PaperBroker(config.paper)
    else:
        from polybot.live_broker import LiveBroker

        broker = LiveBroker(config.live)

    if config.risk.enabled:
        broker = RiskGuardedBroker(broker, config.risk)
    return broker


def build_engines(config: Config, broker: Broker) -> list:
    """One engine per enabled strategy, all sharing the same broker and journal."""
    journal = TradeJournal(config.engine.journal_file)
    engines: list = [CopyEngine(config, broker, journal=journal)]
    if config.threshold.enabled:
        engines.append(ThresholdEngine(config.threshold, broker, journal=journal))
    return engines


def print_status(config: Config, broker: Broker) -> None:
    from polybot.gamma_client import GammaClient

    print(f"mode: {'PAPER' if config.is_paper else 'LIVE'}")
    print(f"cash: ${broker.get_cash_balance():.2f}")

    inner = broker.inner if isinstance(broker, RiskGuardedBroker) else broker
    realized = getattr(inner, "realized_pnl_usd", None)
    if realized is not None:
        print(f"realized pnl (all time): ${realized:.2f}")

    positions = broker.get_positions()
    print(f"open positions: {len(positions)} (cost basis ${broker.get_exposure_usd():.2f})")
    gamma = GammaClient(config.gamma_api_url)
    for pos in positions.values():
        line = (
            f"  {pos.outcome or pos.token_id}: {pos.size:.4f} shares "
            f"@ avg {pos.avg_price:.4f} (cost ${pos.cost_basis_usd:.2f})"
        )
        try:
            current = gamma.get_token_price(pos.condition_id, pos.token_id, retries=1)
        except Exception:
            current = None
        if current is not None:
            unrealized = (current - pos.avg_price) * pos.size
            line += f" | now {current:.4f}, unrealized ${unrealized:+.2f}"
        print(line)

    if isinstance(broker, RiskGuardedBroker):
        s = broker.today_summary()
        print(
            f"today ({s['date']}): {s['buys_today']}/{broker.config.max_buys_per_day} buys, "
            f"${s['buy_notional_today']:.2f}/${broker.config.max_buy_notional_per_day_usd:.2f} notional, "
            f"realized pnl ${s['realized_pnl_today']:+.2f}"
        )
        if s["halted"]:
            print("!! HALTED for the day (daily loss limit hit) -- SELLs only")
        if s["kill_switch"]:
            print(f"!! KILL SWITCH active ({broker.config.kill_switch_file} exists) -- SELLs only")


def run_loop(engines: list, interval_seconds: int, tracker=None, broker: Broker | None = None) -> None:
    while True:
        for engine in engines:
            try:
                n = engine.poll_once()
                if n:
                    log.info("%s: %d action(s) this pass", type(engine).__name__, n)
            except Exception:
                log.exception("unexpected error in %s, continuing", type(engine).__name__)
        if tracker is not None and broker is not None:
            tracker.maybe_snapshot(broker)
        time.sleep(interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Polymarket copy-trading bot")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--env", default=".env", help="path to .env")
    parser.add_argument(
        "--once", action="store_true", help="run a single poll pass and exit (useful for testing/cron)"
    )
    parser.add_argument(
        "--refresh-leaderboard",
        action="store_true",
        help="force-refresh the leaderboard watchlist, print the wallets, and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print portfolio (cash, positions, PnL) and today's risk counters, then exit",
    )
    args = parser.parse_args(argv)

    config = Config.load(args.config, args.env)
    setup_logging(config.engine.log_file)

    if args.refresh_leaderboard:
        from polybot.leaderboard import LeaderboardClient, LeaderboardWatchlist

        watchlist = LeaderboardWatchlist(config.leaderboard, LeaderboardClient(config.data_api_url))
        wallets = watchlist.get_wallets(force_refresh=True)
        for wallet in wallets:
            print(wallet)
        log.info(
            "%d wallet(s) cached to %s (they still must pass `filters` to be copied)",
            len(wallets), config.leaderboard.cache_file,
        )
        return 0 if wallets else 1

    if args.status:
        print_status(config, build_broker(config))
        return 0

    if not config.is_paper:
        log.warning(
            "*** LIVE MODE *** this will place real orders with real funds using the "
            "wallet behind POLYMARKET_PRIVATE_KEY. Ctrl-C within 5s to abort."
        )
        time.sleep(5)

    broker = build_broker(config)
    engines = build_engines(config, broker)
    log.info(
        "starting in %s mode with strategies: %s",
        "PAPER" if config.is_paper else "LIVE",
        ", ".join(type(e).__name__ for e in engines),
    )

    if args.once:
        total = 0
        for engine in engines:
            total += engine.poll_once()
        log.info("done: %d action(s)", total)
        return 0

    from polybot.tracker import EquityTracker

    tracker = EquityTracker(config.engine.equity_file, config.engine.equity_snapshot_minutes)
    tracker.maybe_snapshot(broker)  # seed the chart with a point at startup

    if config.web.enabled:
        from polybot.web import DashboardServer

        try:
            DashboardServer(config, broker, tracker).start_background()
        except OSError as exc:
            log.warning("dashboard disabled, could not bind %s:%d: %s", config.web.host, config.web.port, exc)

    run_loop(engines, config.engine.poll_interval_seconds, tracker, broker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
