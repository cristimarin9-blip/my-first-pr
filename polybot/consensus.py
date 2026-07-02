from __future__ import annotations

from dataclasses import dataclass

from polybot.config import ConsensusConfig
from polybot.models import Trade

# A trader's holdings: list of (condition_id, token_id) pairs they hold with size > 0.
Holdings = list[tuple[str, str]]


@dataclass(frozen=True)
class ConsensusResult:
    agree: int         # qualified traders on the same outcome as the trade
    opinionated: int   # qualified traders with any position in the market

    @property
    def fraction(self) -> float:
        if self.opinionated == 0:
            return 0.0
        return self.agree / self.opinionated

    def passes(self, config: ConsensusConfig) -> bool:
        return self.opinionated >= config.min_traders and self.fraction >= config.min_agreement


def evaluate_consensus(trade: Trade, holdings_by_trader: dict[str, Holdings]) -> ConsensusResult:
    """Measure how many qualified traders share the outcome `trade` is buying.

    "Opinionated" means holding any outcome token in the trade's market;
    "agree" means holding the exact token the trade bought. The source trader
    always counts as agreeing -- their trade IS their stance, even if the
    positions API hasn't reflected it yet.
    """
    agree: set[str] = set()
    opinionated: set[str] = set()

    for trader, holdings in holdings_by_trader.items():
        for condition_id, token_id in holdings:
            if condition_id == trade.condition_id:
                opinionated.add(trader)
                if token_id == trade.token_id:
                    agree.add(trader)

    opinionated.add(trade.trader)
    agree.add(trade.trader)

    return ConsensusResult(agree=len(agree), opinionated=len(opinionated))
