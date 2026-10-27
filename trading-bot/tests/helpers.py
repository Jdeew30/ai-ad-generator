"""Outils communs aux tests."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import ClassVar

from tradebot.config import Settings
from tradebot.data import DAY_MS
from tradebot.engine import Engine
from tradebot.exchange import PaperBroker
from tradebot.models import Candle, Signal
from tradebot.risk import RiskConfig
from tradebot.store import Store
from tradebot.strategies import Strategy

logging.disable(logging.CRITICAL)  # les tests vérifient le journal en base, pas la console

H = 3_600_000
T0 =1_700_000_000_000 - 1_700_000_000_000 % DAY_MS  # minuit UTC


def candles_from(closes: Sequence[float], spread: float = 0.001) -> list[Candle]:
    return [Candle(T0 + i * H, c, c * (1 + spread), c * (1 - spread), c, 1.0) for i, c in enumerate(closes)]


@dataclass
class Scripted(Strategy):
    """Stratégie de test : le volume de la bougie code le signal (1 = achat, 2 = vente)."""

    name: ClassVar[str] = "scripted"
    label: ClassVar[str] = "Scripted"
    description: ClassVar[str] = ""

    @property
    def warmup(self) -> int:
        return 1

    def signals(self, candles: Sequence[Candle]) -> list[Signal]:
        return [{1: Signal.BUY, 2: Signal.SELL}.get(int(c.volume), Signal.HOLD) for c in candles]


class FakeData:
    def __init__(self, symbols: Sequence[str]) -> None:
        self.series: dict[str, list[Candle]] = {s: [] for s in symbols}
        self.fail: set[str] = set()

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500, since: int | None = None) -> list[Candle]:
        if symbol in self.fail:
            raise ConnectionError("réseau indisponible")
        return self.series[symbol][-limit:]


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.commands: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)

    def poll_commands(self) -> list[str]:
        commands, self.commands = self.commands, []
        return commands


class Harness:
    """Moteur en mode paper sur des bougies contrôlées, avec une horloge simulée."""

    def __init__(self, symbols: Sequence[str] = ("BTC/USDT",), risk: RiskConfig | None = None,
                 capital: float = 10_000.0, store: Store | None = None) -> None:
        self.symbols = list(symbols)
        self.settings = Settings(symbols=self.symbols, risk=risk or RiskConfig(fee_pct=0), slippage_pct=0)
        self.store = store or Store()
        self.broker = PaperBroker(capital, self.settings.risk.fee_pct, 0, wallet=self.store.get("paper_wallet"),
                                  on_change=lambda w: self.store.set("paper_wallet", w))
        self.data = FakeData(self.symbols)
        self.notifier = FakeNotifier()
        self.now = T0
        self.engine = Engine(self.settings, self.broker, self.data, self.store, self.notifier, Scripted(),
                             clock=lambda: self.now / 1000)

    def step(self, signals: dict[str, int] | None = None, price: float | dict[str, float] = 100.0) -> None:
        """Clôture la bougie en cours (avec son signal), en ouvre une nouvelle au prix donné, puis un tour."""
        signals = signals or {}
        for s in self.symbols:
            series = self.data.series[s]
            if series:
                series[-1] = replace(series[-1], volume=signals.get(s, 0))
            p = price[s] if isinstance(price, dict) else price
            series.append(Candle(T0 + len(series) * H, p, p, p, p, 0))
        self.now = T0 + (len(self.data.series[self.symbols[0]]) - 1) * H + H // 2
        self.engine.tick()

    def set_price(self, price: float, symbol: str | None = None) -> None:
        """Change le prix de la bougie en cours puis fait un tour (même bougie)."""
        s = symbol or self.symbols[0]
        self.data.series[s][-1] = replace(self.data.series[s][-1], close=price, high=price, low=price)
        self.engine.tick()

    def warm(self) -> None:
        self.step()
        self.step()  # premier signal vu : mémorisé sans agir
