# Polymarket Copy-Trading Bot

A copy-trading bot for [Polymarket](https://polymarket.com) that watches wallets you
choose, filters them by performance criteria (win rate, trade count, volume,
open-position count, ...), and mirrors their new trades into your own
portfolio -- either as a **paper (simulated) trade** or a **real order** on
Polymarket's order book.

**Paper trading is the default.** Nothing touches real funds until you
explicitly set `mode: live` in your config *and* provide a private key.

This project is inspired by the class of "copy-trading" bots (e.g. the
`polygun`-style templates) that skim a percentage (often ~1%) off every
copied trade as a service fee. **This bot does not do that.** There is no fee
configuration anywhere in the codebase -- position sizing only ever accounts
for your own risk limits (`sizing.*` in the config), never a cut for anyone
else. See [No fees, anywhere](#no-fees-anywhere) below.

## Features

- **Paper trading by default** -- a simulated wallet (starting balance,
  slippage model, position/PnL tracking) persisted to a local JSON file, so
  you can validate a strategy before risking real money.
- **Live trading** -- switch to `mode: live` to place real limit/IOC orders
  on Polymarket's CLOB via the official [`py-clob-client`](https://pypi.org/project/py-clob-client/) SDK.
- **Configurable trader filters** -- only copy wallets that meet your bar for:
  - minimum number of trades
  - minimum win rate
  - minimum lifetime traded volume
  - maximum number of concurrently open positions
  - minimum average trade size (filters out dust/spam wallets)
- **Optional consensus gate** -- only copy a BUY when at least X% of your
  qualified top traders with a stake in that market are holding the same
  outcome (`consensus.*` in the config). Exits are never blocked.
- **Proportional position sizing** -- mirrors the *fraction of bankroll* a
  trader committed to a trade (not the raw dollar amount), scaled by your own
  `copy_ratio`, and capped by your own per-trade and total-exposure limits.
- **Same code path for paper and live** -- the copy-engine only talks to a
  `Broker` interface, so switching modes is a one-line config change.

## How it works

```
watchlist (explicit wallets + optional JSON file)
        |
        v
DataApiClient  --------->  trader stats (win rate, volume, open positions, ...)
        |                          |
        v                          v
  passes_filters()?  ------ no --> skip wallet
        | yes
        v
  new trades since last poll
        |
        v
  compute_copy_size()  (proportional sizing, capped by your risk limits)
        |
        v
  Broker.place_order()  --->  PaperBroker (simulated fill) or LiveBroker (real order)
```

## Project layout

```
polybot/
  config.py          # config schema + loader (config.yaml + .env)
  models.py           # Trade, Position, TraderStats, OrderResult, ...
  data_client.py      # wraps Polymarket's public data-api (trades, positions, trader stats)
  gamma_client.py     # wraps Polymarket's public Gamma API (market metadata/prices)
  trader_filter.py    # win-rate / volume / position-count filtering
  consensus.py        # optional "X% of top traders agree" gate for BUYs
  sizing.py           # proportional position sizing + risk caps
  broker.py           # Broker interface shared by paper and live
  paper_broker.py      # simulated broker, no real funds, no fees
  live_broker.py       # real orders via py-clob-client, no fees
  copy_engine.py       # polling loop that ties it all together
  cli.py / main.py     # entrypoint
tests/                # unit tests (filters, sizing, paper broker, engine, config)
config.example.yaml   # copy to config.yaml
.env.example          # copy to .env (secrets only, never commit)
watchlist.example.json
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
cp .env.example .env
```

`config.yaml` and `.env` are git-ignored -- put your personal settings and
secrets there, not in the example files.

### Choosing who to copy

Add wallet addresses either directly under `target_wallets` in `config.yaml`,
or in a JSON array file referenced by `watchlist_file` (see
`watchlist.example.json`). Every candidate wallet still has to pass the
`filters` section before it's actually copied -- the watchlist is just the
pool of *candidates*, not a guarantee they'll be traded. There is no public,
documented "top traders" API to pull this list automatically, so populate the
watchlist yourself from Polymarket's leaderboard/activity feed or from wallets
you already follow.

### Paper trading (default)

```bash
python main.py --once     # one evaluation pass, useful for testing/cron
python main.py             # runs forever, polling every `engine.poll_interval_seconds`
```

Your simulated portfolio (cash balance, open positions, realized PnL) is
stored in `data/paper_state.json` (path configurable). Delete that file to
reset the paper account back to `paper.starting_balance_usd`.

### Live trading

1. Set `mode: live` in `config.yaml`.
2. Put your wallet's private key in `.env` as `POLYMARKET_PRIVATE_KEY`
   (and `POLYMARKET_FUNDER_ADDRESS` if you trade through a Polymarket
   email/Magic or browser-proxy wallet rather than a plain EOA -- see
   `live.signature_type` in `config.example.yaml`).
3. Set `live.assumed_bankroll_usd` to roughly what you've funded your wallet
   with (used as a sizing fallback if the on-chain balance query fails).
4. `python main.py`

Live orders are IOC ("fill-and-kill") limit orders priced at the source
trade's price plus `live.slippage_bps` of buffer, sized by the same
`sizing.*` rules as paper mode. **Start with small `sizing.max_position_usd`
and `sizing.max_total_exposure_usd` values and watch it for a while before
trusting it with meaningful size.**

### Consensus gate (optional)

Set `consensus.enabled: true` to require agreement among your qualified
traders before a BUY is copied:

```yaml
consensus:
  enabled: true
  min_agreement: 0.6   # >=60% of opinionated qualified traders must hold the same outcome
  min_traders: 2       # at least 2 qualified traders must have a stake in the market
```

"Opinionated" means a qualified trader currently holds *any* outcome token in
the trade's market; "agreement" means holding the *same* outcome the trade
bought. The trader whose trade triggered the check always counts as agreeing
(their trade is their stance), which is why `min_traders: 2` is the sensible
floor -- it means at least one *other* qualified trader must have skin in that
market before consensus can pass. SELLs are never blocked: when the trader
you copy exits, the bot mirrors the exit regardless of what everyone else
holds, since refusing to exit only adds risk.

## No fees, anywhere

Every order this bot places -- in either mode -- uses exactly the size
computed by `sizing.compute_copy_size`. Nothing in this codebase deducts a
percentage, adds a `fee_rate_bps`, or routes any part of a trade elsewhere.
`tests/test_paper_broker.py::test_no_fee_is_ever_charged` and
`tests/test_config.py::test_no_fee_field_exists_anywhere_in_config` exist
specifically to catch a regression here. (Polymarket's own protocol-level
maker/taker mechanics, if any apply to your order, are outside this bot's
control.)

## Configuration reference

See `config.example.yaml` for the full, commented list of options:
`filters.*` (who qualifies to be copied), `sizing.*` (how much to copy and
your risk caps), `paper.*` (simulated broker settings), `live.*` (real
broker settings), and `engine.*` (poll interval, state file locations).

## Testing

```bash
pip install -r requirements.txt
pytest
```

Tests cover the filter logic, position sizing math, the paper broker's
accounting (including the no-fee guarantee above), and the copy-engine's
polling/dedup behavior, all without hitting the network. The live broker
(`py-clob-client` integration) is not covered by automated tests since it
requires a funded wallet and places real orders -- test it yourself against a
small amount before relying on it.

## Important disclaimers

- **This is not financial advice, and it is not audited or battle-tested
  against real funds.** Read the code before trusting it with money.
- Past performance of a copied trader (win rate, volume, etc.) does not
  guarantee future results.
- Polymarket's data-api and Gamma API used here (`data_client.py`,
  `gamma_client.py`) are public but **undocumented and unofficial**; their
  response shapes may change without notice. `LiveBroker` uses the official
  `py-clob-client` SDK for order placement/signing.
- Trading on Polymarket may be restricted or illegal in your jurisdiction.
  You are responsible for complying with local laws and Polymarket's own
  terms of service.
- Never commit your `.env` file or private key.
