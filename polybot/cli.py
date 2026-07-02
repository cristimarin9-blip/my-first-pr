from __future__ import annotations

import argparse
import logging
import sys
import time

from polybot.broker import Broker
from polybot.config import Config
from polybot.logging_setup import setup_logging
from polybot.risk import RiskGuardedBroker
from polybot.runtime import BotRuntime, build_broker

log = logging.getLogger(__name__)


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

    runtime = BotRuntime(config, args.config, args.env)

    if args.once:
        total = runtime.poll_once_all()
        log.info("done: %d action(s)", total)
        return 0

    if config.web.enabled:
        from polybot.web import DashboardServer

        try:
            DashboardServer(runtime).start_background()
        except OSError as exc:
            log.warning("dashboard disabled, could not bind %s:%d: %s", config.web.host, config.web.port, exc)

    runtime.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
