"""Types partagés par tout le bot."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Candle:
    time: int  # horodatage d'ouverture, en millisecondes UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    stop: float
    target: float  # 0 = pas d'objectif de gain
    highest: float  # plus haut atteint depuis l'entrée (stop suiveur)
    opened_at: int
    entry_fee: float = 0.0


@dataclass(frozen=True)
class Trade:
    symbol: str
    qty: float
    entry_price: float
    exit_price: float
    opened_at: int
    closed_at: int
    pnl: float  # net de frais, en devise de cotation
    reason: str
    fees: float = 0.0

    @property
    def pnl_pct(self) -> float:
        cost = self.qty * self.entry_price
        return self.pnl / cost * 100 if cost else 0.0


@dataclass(frozen=True)
class Fill:
    side: str  # "buy" ou "sell"
    qty: float  # quantité réellement reçue (achat) ou vendue (vente)
    price: float
    fee: float  # en devise de cotation
