"""Stratégie : croisement de moyennes mobiles filtré par le RSI."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .indicators import ema, rsi


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class MaCrossoverStrategy:
    """
    Achat quand l'EMA rapide croise au-dessus de l'EMA lente et que le RSI
    n'est pas en surachat. Vente quand l'EMA rapide repasse en dessous,
    ou quand le RSI signale un surachat extrême.
    """

    fast: int = 12
    slow: int = 26
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_exit: float = 80.0

    def __post_init__(self) -> None:
        if not 0 < self.fast < self.slow:
            raise ValueError("Il faut 0 < fast < slow")

    @property
    def warmup(self) -> int:
        """Nombre de bougies nécessaires avant le premier signal."""
        return max(self.slow, self.rsi_period + 1) + 1

    def signals(self, closes: list[float]) -> list[Signal]:
        """Un signal par bougie, calculé uniquement avec les données passées."""
        fast, slow, r = ema(closes, self.fast), ema(closes, self.slow), rsi(closes, self.rsi_period)
        out = [Signal.HOLD] * len(closes)
        for i in range(1, len(closes)):
            if None in (fast[i], slow[i], fast[i - 1], slow[i - 1], r[i]):
                continue
            crossed_up = fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]
            crossed_down = fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]
            if crossed_up and r[i] < self.rsi_overbought:
                out[i] = Signal.BUY
            elif crossed_down or r[i] > self.rsi_exit:
                out[i] = Signal.SELL
        return out

    def latest_signal(self, closes: list[float]) -> Signal:
        return self.signals(closes)[-1] if closes else Signal.HOLD
