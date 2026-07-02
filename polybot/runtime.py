from __future__ import annotations

import dataclasses
import logging
import os
import tempfile
import threading
from pathlib import Path

import yaml

from polybot.broker import Broker
from polybot.config import Config
from polybot.copy_engine import CopyEngine
from polybot.envfile import set_env_var
from polybot.journal import TradeJournal
from polybot.risk import RiskGuardedBroker
from polybot.threshold_engine import ThresholdEngine
from polybot.tracker import EquityTracker

log = logging.getLogger(__name__)

# Config sections the dashboard Settings tab may edit.
EDITABLE_SECTIONS = ["leaderboard", "filters", "consensus", "sizing", "risk", "threshold", "engine"]
# Field-name fragments that must never be shown or accepted in the web form
# (file paths, network binding, secrets).
_HIDDEN_FIELD_MARKERS = ("_file", "host", "port", "url", "private_key", "funder", "token")


def _is_hidden(field_name: str) -> bool:
    return any(marker in field_name for marker in _HIDDEN_FIELD_MARKERS)


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


class BotRuntime:
    """Owns the live config, broker, and engines, and mediates all control.

    The trading loop and the web dashboard share one BotRuntime. Every mutation
    (pause, reload, save settings, reset) goes through here under a lock, so the
    whole bot can be operated from the dashboard without touching the terminal.
    """

    def __init__(self, config: Config, config_path: str = "config.yaml", env_path: str = ".env"):
        self.config = config
        self.config_path = str(config_path)
        self.env_path = str(env_path)
        self._lock = threading.RLock()
        self._paused = False
        self._wake = threading.Event()
        self._build()

    def _build(self) -> None:
        """(Re)build broker, engines, journal, and tracker from self.config."""
        self.broker = build_broker(self.config)
        self.journal = TradeJournal(self.config.engine.journal_file)
        engines: list = [CopyEngine(self.config, self.broker, journal=self.journal)]
        if self.config.threshold.enabled:
            engines.append(ThresholdEngine(self.config.threshold, self.broker, journal=self.journal))
        self.engines = engines
        self.tracker = EquityTracker(
            self.config.engine.equity_file, self.config.engine.equity_snapshot_minutes
        )

    # --- trading loop --------------------------------------------------------

    def poll_once_all(self) -> int:
        with self._lock:
            engines = list(self.engines)
            broker = self.broker
            tracker = self.tracker
            paused = self._paused
        total = 0
        if not paused:
            for engine in engines:
                try:
                    n = engine.poll_once()
                    if n:
                        log.info("%s: %d action(s) this pass", type(engine).__name__, n)
                    total += n
                except Exception:
                    log.exception("unexpected error in %s, continuing", type(engine).__name__)
        try:
            tracker.maybe_snapshot(broker)
        except Exception:
            log.exception("equity snapshot failed")
        return total

    def run_forever(self) -> None:
        with self._lock:
            mode = "PAPER" if self.config.is_paper else "LIVE"
            names = ", ".join(type(e).__name__ for e in self.engines)
        log.info("runtime starting in %s mode with strategies: %s", mode, names)
        while True:
            self.poll_once_all()
            interval = max(int(self.config.engine.poll_interval_seconds), 1)
            # Sleep, but wake early on poll_now()/resume().
            self._wake.wait(timeout=interval)
            self._wake.clear()

    # --- introspection -------------------------------------------------------

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def editable_config(self) -> dict:
        """Config subset for the Settings form (paths/secrets stripped out)."""
        with self._lock:
            full = dataclasses.asdict(self.config)
            sections = {}
            for name in EDITABLE_SECTIONS:
                section = full.get(name, {})
                sections[name] = {k: v for k, v in section.items() if not _is_hidden(k)}
            return {
                "mode": self.config.mode,
                "target_wallets": list(self.config.target_wallets),
                "sections": sections,
            }

    # --- actions -------------------------------------------------------------

    def dispatch(self, action: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        try:
            if action == "halt":
                return self._set_kill_switch(True)
            if action == "resume":
                return self._set_kill_switch(False)
            if action == "pause":
                with self._lock:
                    self._paused = True
                return {"ok": True, "message": "Polling paused -- the bot will place no new trades until resumed."}
            if action == "unpause":
                with self._lock:
                    self._paused = False
                self._wake.set()
                return {"ok": True, "message": "Polling resumed."}
            if action == "poll_now":
                self._wake.set()
                return {"ok": True, "message": "Polling now…"}
            if action == "reset_paper":
                return self._reset_paper()
            if action == "reload":
                self.reload()
                return {"ok": True, "message": "Config reloaded from disk."}
            if action == "save_config":
                self.save_config(payload.get("config") or {})
                return {"ok": True, "message": "Settings saved and applied."}
            if action == "refresh_leaderboard":
                return self._refresh_leaderboard()
            return {"ok": False, "error": f"unknown action: {action!r}"}
        except Exception as exc:
            log.exception("action %s failed", action)
            return {"ok": False, "error": str(exc)}

    def _set_kill_switch(self, on: bool) -> dict:
        if not self.config.risk.enabled:
            return {"ok": False, "error": "risk guard is disabled, so the kill switch has no effect"}
        kill_file = Path(self.config.risk.kill_switch_file)
        if on:
            kill_file.parent.mkdir(parents=True, exist_ok=True)
            kill_file.touch()
            log.warning("HALT engaged via dashboard kill switch (%s)", kill_file)
            return {"ok": True, "message": "Halted -- new buys blocked. Exits still allowed."}
        try:
            kill_file.unlink()
        except FileNotFoundError:
            pass
        log.info("kill switch cleared via dashboard, trading resumed")
        return {"ok": True, "message": "Resumed -- new buys allowed again."}

    def _reset_paper(self) -> dict:
        with self._lock:
            if not self.config.is_paper:
                return {"ok": False, "error": "reset is only allowed in paper mode"}
            Path(self.config.paper.state_file).unlink(missing_ok=True)
            self._build()
        log.warning("paper portfolio reset via dashboard")
        return {"ok": True, "message": "Paper portfolio reset to the starting balance."}

    def _refresh_leaderboard(self) -> dict:
        if not self.config.leaderboard.enabled:
            return {"ok": False, "error": "leaderboard is disabled in settings"}
        from polybot.leaderboard import LeaderboardClient, LeaderboardWatchlist

        watchlist = LeaderboardWatchlist(
            self.config.leaderboard, LeaderboardClient(self.config.data_api_url)
        )
        wallets = watchlist.get_wallets(force_refresh=True)
        return {"ok": True, "message": f"Leaderboard refreshed: {len(wallets)} wallet(s) cached."}

    def reload(self) -> None:
        with self._lock:
            self.config = Config.load(self.config_path, self.env_path)
            self._build()

    def save_config(self, form: dict) -> None:
        """Merge form values into config.yaml, validate, then apply live."""
        with self._lock:
            base = dataclasses.asdict(self.config)

            if "target_wallets" in form:
                base["target_wallets"] = [
                    str(w).strip().lower() for w in form["target_wallets"] if str(w).strip()
                ]
            for name, fields in (form.get("sections") or {}).items():
                if name in base and isinstance(base[name], dict) and isinstance(fields, dict):
                    for key, value in fields.items():
                        if not _is_hidden(key) and key in base[name]:
                            base[name][key] = value

            # Secrets are never written to config.yaml.
            base.get("live", {}).pop("private_key", None)
            base.get("live", {}).pop("funder_address", None)

            mode = str(form.get("mode", self.config.mode)).lower()
            base["mode"] = mode

            new_key = str(form.get("live_private_key") or "").strip()

            # Validate against the *intended* env (mode has env precedence, and
            # a live switch needs the key visible) before we persist anything.
            prev_mode = os.environ.get("POLYBOT_MODE")
            prev_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
            try:
                os.environ["POLYBOT_MODE"] = mode
                if new_key:
                    os.environ["POLYMARKET_PRIVATE_KEY"] = new_key
                self._validate(base)
            except Exception:
                _restore_env("POLYBOT_MODE", prev_mode)
                if new_key:
                    _restore_env("POLYMARKET_PRIVATE_KEY", prev_key)
                raise

            # Validation passed -- persist and apply.
            if new_key:
                set_env_var(self.env_path, "POLYMARKET_PRIVATE_KEY", new_key)
            set_env_var(self.env_path, "POLYBOT_MODE", mode)
            with open(self.config_path, "w") as f:
                yaml.safe_dump(base, f, sort_keys=False)
            self.config = Config.load(self.config_path, self.env_path)
            self._build()

    def _validate(self, base: dict) -> None:
        """Round-trip `base` through Config.load in a temp file to catch errors."""
        directory = Path(self.config_path).parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(directory), suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as f:
                yaml.safe_dump(base, f, sort_keys=False)
            Config.load(tmp, self.env_path)  # raises ValueError/TypeError on bad values
        finally:
            os.unlink(tmp)


def _restore_env(key: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value
