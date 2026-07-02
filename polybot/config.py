from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is an optional convenience
    load_dotenv = None


@dataclass
class PaperConfig:
    starting_balance_usd: float = 1000.0
    state_file: str = "data/paper_state.json"
    slippage_bps: float = 25.0


@dataclass
class LiveConfig:
    clob_host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    signature_type: int = 0
    slippage_bps: float = 50.0
    # py-clob-client has no reliable "get my USDC balance" call across all
    # wallet types. If the on-chain balance query fails/is unavailable, we
    # fall back to this manually-configured figure so live sizing still has
    # a bankroll to work from. Set it to roughly what you've funded your
    # Polymarket wallet with.
    assumed_bankroll_usd: float = 0.0
    private_key: str | None = None
    funder_address: str | None = None


@dataclass
class FilterCriteria:
    min_trades: int = 30
    min_win_rate: float = 0.55
    min_volume_usd: float = 5000.0
    max_open_positions: int = 40
    min_avg_trade_usd: float = 10.0


@dataclass
class ConsensusConfig:
    # When enabled, a BUY is only copied if at least `min_agreement` of the
    # qualified traders with a stake in that market hold the same outcome.
    # SELLs (exits) are never blocked by consensus.
    enabled: bool = False
    min_agreement: float = 0.6
    # Minimum number of qualified traders with a position in the market
    # (including the one whose trade triggered the check) before consensus
    # is meaningful. Below this, the trade is skipped, so a single trader
    # can never count as "100% agreement" with themselves.
    min_traders: int = 2


@dataclass
class ThresholdConfig:
    # Standalone strategy (runs alongside copy-trading): watch a list of
    # markets and automatically buy an outcome (Yes OR No) the moment its
    # probability reaches `trigger_probability`.
    enabled: bool = False
    markets: list[str] = field(default_factory=list)  # Gamma condition IDs to watch
    trigger_probability: float = 0.90
    # Don't chase an outcome that already blew past the trigger -- above this
    # there's almost no payoff left relative to the risk.
    max_entry_probability: float = 0.98
    order_usd: float = 10.0
    # Exit rules for positions THIS strategy opened (0 disables either rule).
    take_profit_probability: float = 0.99
    stop_loss_probability: float = 0.50
    state_file: str = "data/threshold_state.json"

    def __post_init__(self) -> None:
        if not 0.5 <= self.trigger_probability < 1.0:
            raise ValueError("threshold.trigger_probability must be in [0.5, 1.0)")
        if self.max_entry_probability < self.trigger_probability:
            raise ValueError("threshold.max_entry_probability must be >= trigger_probability")
        if self.stop_loss_probability >= self.trigger_probability and self.stop_loss_probability > 0:
            raise ValueError("threshold.stop_loss_probability must be below trigger_probability")


@dataclass
class SizingConfig:
    copy_ratio: float = 0.25
    max_position_usd: float = 100.0
    max_total_exposure_usd: float = 500.0
    min_order_usd: float = 1.0


@dataclass
class EngineConfig:
    poll_interval_seconds: int = 30
    trade_lookback_seconds: int = 3600
    seen_trades_file: str = "data/seen_trades.json"
    log_file: str = "data/trades.log"


@dataclass
class Config:
    mode: str = "paper"
    target_wallets: list[str] = field(default_factory=list)
    watchlist_file: str | None = None
    filters: FilterCriteria = field(default_factory=FilterCriteria)
    consensus: ConsensusConfig = field(default_factory=ConsensusConfig)
    threshold: ThresholdConfig = field(default_factory=ThresholdConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    data_api_url: str = "https://data-api.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"

    @property
    def is_paper(self) -> bool:
        return self.mode.lower() != "live"

    @classmethod
    def load(cls, path: str | Path = "config.yaml", env_path: str | Path = ".env") -> "Config":
        if load_dotenv is not None and Path(env_path).exists():
            load_dotenv(env_path)

        raw: dict[str, Any] = {}
        config_path = Path(path)
        if config_path.exists():
            with config_path.open() as f:
                raw = yaml.safe_load(f) or {}

        cfg = cls(
            mode=os.environ.get("POLYBOT_MODE", raw.get("mode", "paper")),
            target_wallets=[w.lower() for w in raw.get("target_wallets", [])],
            watchlist_file=raw.get("watchlist_file"),
            filters=FilterCriteria(**raw.get("filters", {})),
            consensus=ConsensusConfig(**raw.get("consensus", {})),
            threshold=ThresholdConfig(**raw.get("threshold", {})),
            sizing=SizingConfig(**raw.get("sizing", {})),
            engine=EngineConfig(**raw.get("engine", {})),
            paper=PaperConfig(**raw.get("paper", {})),
            live=LiveConfig(**raw.get("live", {})),
            data_api_url=raw.get("data_api_url", "https://data-api.polymarket.com"),
            gamma_api_url=raw.get("gamma_api_url", "https://gamma-api.polymarket.com"),
        )

        cfg.live.private_key = os.environ.get("POLYMARKET_PRIVATE_KEY") or None
        cfg.live.funder_address = os.environ.get("POLYMARKET_FUNDER_ADDRESS") or None

        if not cfg.is_paper and not cfg.live.private_key:
            raise ValueError(
                "POLYBOT_MODE=live requires POLYMARKET_PRIVATE_KEY to be set "
                "(see .env.example). Refusing to start in live mode without it."
            )

        return cfg

    def load_watchlist(self) -> list[str]:
        wallets = list(self.target_wallets)
        if self.watchlist_file and Path(self.watchlist_file).exists():
            import json

            with open(self.watchlist_file) as f:
                extra = json.load(f)
            wallets.extend(w.lower() for w in extra)
        # de-duplicate while preserving order
        seen: set[str] = set()
        deduped = []
        for w in wallets:
            if w not in seen:
                seen.add(w)
                deduped.append(w)
        return deduped
