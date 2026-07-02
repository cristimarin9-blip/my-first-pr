from __future__ import annotations

import argparse
import logging
import sys

from polybot.broker import Broker
from polybot.config import Config
from polybot.copy_engine import CopyEngine
from polybot.logging_setup import setup_logging

log = logging.getLogger(__name__)


def build_broker(config: Config) -> Broker:
    if config.is_paper:
        from polybot.paper_broker import PaperBroker

        return PaperBroker(config.paper)

    from polybot.live_broker import LiveBroker

    return LiveBroker(config.live)


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
        import time

        time.sleep(5)

    broker = build_broker(config)
    engine = CopyEngine(config, broker)

    if args.once:
        n = engine.poll_once()
        log.info("done: copied %d trade(s)", n)
        return 0

    engine.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
