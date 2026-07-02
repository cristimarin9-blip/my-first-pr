from __future__ import annotations

import argparse
import logging
import sys
import time

from polybot.broker import Broker
from polybot.config import Config
from polybot.copy_engine import CopyEngine
from polybot.logging_setup import setup_logging
from polybot.threshold_engine import ThresholdEngine

log = logging.getLogger(__name__)


def build_broker(config: Config) -> Broker:
    if config.is_paper:
        from polybot.paper_broker import PaperBroker

        return PaperBroker(config.paper)

    from polybot.live_broker import LiveBroker

    return LiveBroker(config.live)


def build_engines(config: Config, broker: Broker) -> list:
    """One engine per enabled strategy, all sharing the same broker."""
    engines: list = [CopyEngine(config, broker)]
    if config.threshold.enabled:
        engines.append(ThresholdEngine(config.threshold, broker))
    return engines


def run_loop(engines: list, interval_seconds: int) -> None:
    while True:
        for engine in engines:
            try:
                n = engine.poll_once()
                if n:
                    log.info("%s: %d action(s) this pass", type(engine).__name__, n)
            except Exception:
                log.exception("unexpected error in %s, continuing", type(engine).__name__)
        time.sleep(interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Polymarket copy-trading bot")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--env", default=".env", help="path to .env")
    parser.add_argument(
        "--once", action="store_true", help="run a single poll pass and exit (useful for testing/cron)"
    )
    args = parser.parse_args(argv)

    config = Config.load(args.config, args.env)
    setup_logging(config.engine.log_file)

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

    run_loop(engines, config.engine.poll_interval_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
