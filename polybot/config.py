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
class LeaderboardConfig:
    # Auto-populate the copy-trading watchlist from Polymarket's trader
    # leaderboard. Scraped wallets are merged with target_wallets and
    # watchlist_file and still have to pass `filters` before being copied.
    enabled: bool = False
    category: str = "OVERALL"    # OVERALL, POLITICS, SPORTS, ESPORTS, CRYPTO, CULTURE, MENTIONS, WEATHER, ECONOMICS, TECH, FINANCE
    time_period: str = "MONTH"   # DAY, WEEK, MONTH, ALL
    order_by: str = "PNL"        # PNL or VOL
    top_n: int = 25              # API maximum is 50
    refresh_hours: float = 24.0
    cache_file: str = "data/leaderboard_watchlist.json"


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
class RiskConfig:
    # Circuit breakers enforced on every order, regardless of which strategy
    # produced it. BUYs (new exposure) get blocked; SELLs (de-risking) are
    # always allowed through.
    enabled: bool = True
    # Entry price band: don't buy longshots or near-certainties.
    min_buy_price: float = 0.03
    max_buy_price: float = 0.97
    # Concentration cap: total cost basis in any single market.
    max_market_exposure_usd: float = 150.0
    # Daily rate/spend limits (reset at local midnight).
    max_buys_per_day: int = 50
    max_buy_notional_per_day_usd: float = 250.0
    # Halt new BUYs for the rest of the day once today's REALIZED loss
    # reaches this amount. 0 disables.
    daily_loss_limit_usd: float = 100.0
    # Copy-engine guard: skip copying a BUY if the market already moved more
    # than this many basis points above the source trader's fill price
    # (avoids chasing a move that already happened). 0 disables.
    max_price_drift_bps: float = 0.0
    # Touch this file (e.g. `touch data/HALT`) to instantly block all new
    # BUYs without stopping the process; exits keep working. Delete to resume.
    kill_switch_file: str = "data/HALT"
    state_file: str = "data/risk_state.json"


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
    journal_file: str = "data/trade_journal.csv"
    equity_file: str = "data/equity_history.json"
    equity_snapshot_minutes: float = 5.0


@dataclass
class WebConfig:
    # Built-in dashboard (PnL chart, positions, risk counters), served while
    # the bot runs. Mobile-friendly -- open it from any browser, PC or phone.
    enabled: bool = True
    # 127.0.0.1 = this machine only. Set to 0.0.0.0 to reach it from other
    # devices on your network (e.g. your phone) -- only expose it on networks
    # you trust (see `controls_enabled` below).
    host: str = "127.0.0.1"
    port: int = 8080
    # Allow action buttons in the dashboard (Halt / Resume / Refresh
    # leaderboard). Actions are CSRF-protected by a per-run token embedded in
    # the page -- a malicious website can't read it, so it can't trigger
    # actions. But anyone who can *load* the dashboard can act, so if you set
    # host: 0.0.0.0 on an untrusted network, set this to false (or keep the
    # dashboard behind an SSH tunnel / VPN).
    controls_enabled: bool = True


@dataclass
class Config:
    mode: str = "paper"
    target_wallets: list[str] = field(default_factory=list)
    watchlist_file: str | None = None
    filters: FilterCriteria = field(default_factory=FilterCriteria)
    consensus: ConsensusConfig = field(default_factory=ConsensusConfig)
    leaderboard: LeaderboardConfig = field(default_factory=LeaderboardConfig)
    threshold: ThresholdConfig = field(default_factory=ThresholdConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    web: WebConfig = field(default_factory=WebConfig)
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
            leaderboard=LeaderboardConfig(**raw.get("leaderboard", {})),
            threshold=ThresholdConfig(**raw.get("threshold", {})),
            risk=RiskConfig(**raw.get("risk", {})),
            sizing=SizingConfig(**raw.get("sizing", {})),
            engine=EngineConfig(**raw.get("engine", {})),
            web=WebConfig(**raw.get("web", {})),
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
