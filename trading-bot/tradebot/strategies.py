"""
Stratégies de trading (spot, achat uniquement).

Une stratégie transforme des bougies en signaux : BUY = ouvrir une position si
on n'en a pas, SELL = la fermer si on en a une. Les stops et objectifs de gain
sont gérés à part, par `risk.py`, et s'appliquent à toutes les stratégies.

Pour ajouter une stratégie : une dataclass héritant de `Strategy`, décorée par
`@register`, avec `warmup` et `signals`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any, ClassVar

from .indicators import bollinger, ema, highest, lowest, macd, rsi
from .models import Candle, Signal

STRATEGIES: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    STRATEGIES[cls.name] = cls
    return cls


class Strategy(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    # Valeurs testées par la commande `optimize`.
    search_space: ClassVar[dict[str, list[Any]]] = {}

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for f in fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            if isinstance(value, (int, float)) and value < 0:
                raise ValueError(f"{self.name}.{f.name} ne peut pas être négatif")

    @property
    @abstractmethod
    def warmup(self) -> int:
        """Nombre de bougies nécessaires avant le premier signal possible."""

    @abstractmethod
    def signals(self, candles: Sequence[Candle]) -> list[Signal]:
        """Un signal par bougie, calculé uniquement avec les données passées."""

    def params(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    def __str__(self) -> str:
        return f"{self.name}(" + ", ".join(f"{k}={v}" for k, v in self.params().items()) + ")"


def _trend_ok(closes: Sequence[float], trend: list[float | None], i: int) -> bool:
    return trend[i] is not None and closes[i] > trend[i]  # type: ignore[operator]


@register
@dataclass
class EmaRsi(Strategy):
    name: ClassVar[str] = "ema_rsi"
    label: ClassVar[str] = "Tendance — croisement EMA + RSI"
    description: ClassVar[str] = (
        "Achète quand l'EMA rapide croise au-dessus de l'EMA lente (début de tendance) "
        "si le RSI n'est pas en surachat ; vend au croisement inverse ou en surachat extrême."
    )
    search_space: ClassVar[dict[str, list[Any]]] = {"fast": [8, 12, 20], "slow": [26, 50, 100]}

    fast: int = 12
    slow: int = 26
    rsi_period: int = 14
    rsi_max_entry: float = 70.0
    rsi_exit: float = 80.0

    def validate(self) -> None:
        super().validate()
        if not 0 < self.fast < self.slow:
            raise ValueError("ema_rsi : il faut 0 < fast < slow")

    @property
    def warmup(self) -> int:
        return max(self.slow, self.rsi_period + 1) + 1

    def signals(self, candles: Sequence[Candle]) -> list[Signal]:
        closes = [c.close for c in candles]
        f, s, r = ema(closes, self.fast), ema(closes, self.slow), rsi(closes, self.rsi_period)
        out = [Signal.HOLD] * len(closes)
        for i in range(1, len(closes)):
            if None in (f[i], s[i], f[i - 1], s[i - 1], r[i]):
                continue
            if f[i - 1] <= s[i - 1] and f[i] > s[i] and r[i] < self.rsi_max_entry:
                out[i] = Signal.BUY
            elif (f[i - 1] >= s[i - 1] and f[i] < s[i]) or r[i] > self.rsi_exit:
                out[i] = Signal.SELL
        return out


@register
@dataclass
class BollingerRsi(Strategy):
    name: ClassVar[str] = "bollinger_rsi"
    label: ClassVar[str] = "Retour à la moyenne — Bollinger + RSI"
    description: ClassVar[str] = (
        "Achète les excès de baisse (clôture sous la bande basse et RSI bas), uniquement "
        "au-dessus de la moyenne de tendance si trend_ema > 0 ; revend au retour sur la moyenne."
    )
    search_space: ClassVar[dict[str, list[Any]]] = {
        "period": [20, 30], "mult": [2.0, 2.5], "rsi_entry": [25, 30, 35], "trend_ema": [0, 200],
    }

    period: int = 20
    mult: float = 2.0
    rsi_period: int = 14
    rsi_entry: float = 30.0
    trend_ema: int = 200  # 0 = pas de filtre de tendance

    def validate(self) -> None:
        super().validate()
        if self.period < 2 or self.mult <= 0:
            raise ValueError("bollinger_rsi : period >= 2 et mult > 0")

    @property
    def warmup(self) -> int:
        return max(self.period, self.rsi_period + 1, self.trend_ema) + 1

    def signals(self, candles: Sequence[Candle]) -> list[Signal]:
        closes = [c.close for c in candles]
        mid, _, lower = bollinger(closes, self.period, self.mult)
        r = rsi(closes, self.rsi_period)
        trend = ema(closes, self.trend_ema) if self.trend_ema else None
        out = [Signal.HOLD] * len(closes)
        for i, close in enumerate(closes):
            if mid[i] is None or r[i] is None:
                continue
            if close < lower[i] and r[i] < self.rsi_entry and (trend is None or _trend_ok(closes, trend, i)):
                out[i] = Signal.BUY
            elif close >= mid[i]:
                out[i] = Signal.SELL
        return out


@register
@dataclass
class DonchianBreakout(Strategy):
    name: ClassVar[str] = "donchian_breakout"
    label: ClassVar[str] = "Cassure — canal de Donchian (Turtle)"
    description: ClassVar[str] = (
        "Achète quand le prix casse le plus haut des N dernières bougies ; revend quand il "
        "casse le plus bas des M dernières. Suit les grosses tendances, beaucoup de petits échecs."
    )
    search_space: ClassVar[dict[str, list[Any]]] = {
        "entry_period": [20, 40, 55], "exit_period": [10, 20], "trend_ema": [0, 200],
    }

    entry_period: int = 20
    exit_period: int = 10
    trend_ema: int = 0

    def validate(self) -> None:
        super().validate()
        if self.entry_period < 2 or self.exit_period < 2:
            raise ValueError("donchian_breakout : périodes >= 2")

    @property
    def warmup(self) -> int:
        return max(self.entry_period, self.exit_period, self.trend_ema) + 1

    def signals(self, candles: Sequence[Candle]) -> list[Signal]:
        closes = [c.close for c in candles]
        hi = highest([c.high for c in candles], self.entry_period)
        lo = lowest([c.low for c in candles], self.exit_period)
        trend = ema(closes, self.trend_ema) if self.trend_ema else None
        out = [Signal.HOLD] * len(closes)
        for i in range(1, len(closes)):
            # canal des bougies précédentes : la bougie courante doit le casser
            if hi[i - 1] is not None and closes[i] > hi[i - 1] and (trend is None or _trend_ok(closes, trend, i)):
                out[i] = Signal.BUY
            elif lo[i - 1] is not None and closes[i] < lo[i - 1]:
                out[i] = Signal.SELL
        return out


@register
@dataclass
class MacdTrend(Strategy):
    name: ClassVar[str] = "macd_trend"
    label: ClassVar[str] = "Momentum — MACD filtré par la tendance"
    description: ClassVar[str] = (
        "Achète quand la ligne MACD croise au-dessus de sa ligne de signal, si le prix est "
        "au-dessus de l'EMA de tendance ; revend au croisement inverse."
    )
    search_space: ClassVar[dict[str, list[Any]]] = {
        "fast": [8, 12], "slow": [21, 26], "trend_ema": [0, 100, 200],
    }

    fast: int = 12
    slow: int = 26
    signal: int = 9
    trend_ema: int = 200

    def validate(self) -> None:
        super().validate()
        if not 0 < self.fast < self.slow or self.signal < 1:
            raise ValueError("macd_trend : il faut 0 < fast < slow et signal >= 1")

    @property
    def warmup(self) -> int:
        return max(self.slow + self.signal, self.trend_ema) + 1

    def signals(self, candles: Sequence[Candle]) -> list[Signal]:
        closes = [c.close for c in candles]
        line, sig = macd(closes, self.fast, self.slow, self.signal)
        trend = ema(closes, self.trend_ema) if self.trend_ema else None
        out = [Signal.HOLD] * len(closes)
        for i in range(1, len(closes)):
            if None in (line[i], sig[i], line[i - 1], sig[i - 1]):
                continue
            if line[i - 1] <= sig[i - 1] and line[i] > sig[i] and (trend is None or _trend_ok(closes, trend, i)):
                out[i] = Signal.BUY
            elif line[i - 1] >= sig[i - 1] and line[i] < sig[i]:
                out[i] = Signal.SELL
        return out


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    cls = STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"Stratégie inconnue « {name} ». Disponibles : {', '.join(STRATEGIES)}")
    params = dict(params or {})
    allowed = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    unknown = set(params) - allowed
    if unknown:
        raise ValueError(f"Paramètre(s) inconnu(s) pour {name} : {', '.join(sorted(unknown))}. "
                         f"Paramètres possibles : {', '.join(sorted(allowed))}")
    return cls(**params)
