from __future__ import annotations

import logging

from polybot.broker import Broker
from polybot.config import LiveConfig
from polybot.models import OrderResult, Position, Side

log = logging.getLogger(__name__)


class LiveBroker(Broker):
    """Places real orders on Polymarket's CLOB via the official py-clob-client SDK.

    IMPORTANT: this executes real trades with real funds on Polygon mainnet.
    We never set `fee_rate_bps` on outgoing orders (it defaults to 0), and no
    percentage is skimmed off copied trade sizes anywhere in this codebase --
    that is the whole point of not using the original fee-charging template.
    Polymarket itself may still apply its own protocol-level maker/taker
    rules independent of this bot; that is outside our control.
    """

    def __init__(self, config: LiveConfig):
        from py_clob_client.client import ClobClient

        if not config.private_key:
            raise ValueError("live trading requires POLYMARKET_PRIVATE_KEY to be set")

        self.config = config
        self.client = ClobClient(
            host=config.clob_host,
            key=config.private_key,
            chain_id=config.chain_id,
            signature_type=config.signature_type,
            funder=config.funder_address or None,
        )
        self.client.set_api_creds(self.client.create_or_derive_api_creds())

    def get_cash_balance(self) -> float:
        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

            resp = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            balance = float(resp.get("balance", 0)) / 1_000_000  # USDC uses 6 decimals
            if balance > 0:
                return balance
        except Exception as exc:  # pragma: no cover - depends on live account/network state
            log.warning("could not fetch live USDC balance: %s", exc)

        if self.config.assumed_bankroll_usd > 0:
            log.info(
                "using configured live.assumed_bankroll_usd=%.2f (on-chain balance query unavailable)",
                self.config.assumed_bankroll_usd,
            )
            return self.config.assumed_bankroll_usd

        log.warning(
            "no live cash balance available and live.assumed_bankroll_usd is 0; "
            "sizing will treat bankroll as empty and skip all trades"
        )
        return 0.0

    def get_positions(self) -> dict[str, Position]:
        # py-clob-client does not expose a portfolio/positions endpoint.
        # The copy-engine gets live position/exposure bookkeeping from
        # Polymarket's data-api (see data_client.py) instead of from here.
        return {}

    def place_order(
        self,
        token_id: str,
        condition_id: str,
        outcome: str,
        side: Side,
        price: float,
        size: float,
    ) -> OrderResult:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        if size <= 0:
            return OrderResult(False, token_id, side, price, size, error="size must be positive")

        slip = self.config.slippage_bps / 10_000.0
        limit_price = price * (1 + slip) if side == Side.BUY else price * (1 - slip)
        limit_price = round(min(max(limit_price, 0.0001), 0.9999), 4)

        order_args = OrderArgs(
            token_id=token_id,
            price=limit_price,
            size=size,
            side=BUY if side == Side.BUY else SELL,
        )
        try:
            signed_order = self.client.create_order(order_args)
            # FAK (fill-and-kill / IOC): take whatever liquidity is available
            # right now at our limit price, cancel the rest. We deliberately
            # avoid resting GTC orders since copy-trading is about mirroring
            # what already happened, not waiting around for a better price.
            resp = self.client.post_order(signed_order, OrderType.FAK)
        except Exception as exc:
            log.exception("live order failed for token_id=%s", token_id)
            return OrderResult(False, token_id, side, limit_price, size, error=str(exc))

        success = bool(resp.get("success", True)) if isinstance(resp, dict) else True
        order_id = resp.get("orderID") if isinstance(resp, dict) else None
        error = None if success else str(resp)
        log.info("LIVE ORDER %s %s size=%.4f limit_price=%.4f -> %s", side.value, token_id, size, limit_price, resp)
        return OrderResult(success, token_id, side, limit_price, size, order_id=order_id, error=error)
