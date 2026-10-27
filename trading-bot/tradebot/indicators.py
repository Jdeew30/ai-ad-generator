"""
Indicateurs techniques en Python pur.

Chaque fonction renvoie une liste de même longueur que l'entrée, avec None
tant qu'il n'y a pas assez d'historique. La valeur à l'indice i n'utilise que
les données jusqu'à i inclus (aucune donnée future).
"""
from __future__ import annotations

from collections.abc import Sequence

from .models import Candle

Series = list[float | None]


def sma(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Sequence[float | None], period: int) -> Series:
    """EMA amorcée par la SMA des `period` premières valeurs ; ignore les None de tête."""
    out: Series = [None] * len(values)
    start = next((i for i, v in enumerate(values) if v is not None), len(values))
    if len(values) - start < period:
        return out
    k = 2 / (period + 1)
    seed_end = start + period
    prev = sum(values[start:seed_end]) / period  # type: ignore[arg-type]
    out[seed_end - 1] = prev
    for i in range(seed_end, len(values)):
        prev = values[i] * k + prev * (1 - k)  # type: ignore[operator]
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> Series:
    """RSI de Wilder (0-100)."""
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0)
        losses += max(-change, 0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def stdev(values: Sequence[float], period: int) -> Series:
    """Écart-type glissant (population)."""
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        out[i] = (sum((x - mean) ** 2 for x in window) / period) ** 0.5
    return out


def bollinger(values: Sequence[float], period: int = 20, mult: float = 2.0) -> tuple[Series, Series, Series]:
    """(milieu, bande haute, bande basse)."""
    mid, sd = sma(values, period), stdev(values, period)
    upper: Series = [m + mult * s if m is not None and s is not None else None for m, s in zip(mid, sd)]
    lower: Series = [m - mult * s if m is not None and s is not None else None for m, s in zip(mid, sd)]
    return mid, upper, lower


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series]:
    """(ligne MACD, ligne de signal)."""
    f, s = ema(values, fast), ema(values, slow)
    line: Series = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    return line, ema(line, signal)


def highest(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = max(values[i - period + 1 : i + 1])
    return out


def lowest(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = min(values[i - period + 1 : i + 1])
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> Series:
    """Average True Range de Wilder : mesure de la volatilité, en prix."""
    out: Series = [None] * len(candles)
    if len(candles) < period:
        return out
    tr = [candles[0].high - candles[0].low]
    for prev, c in zip(candles, candles[1:]):
        tr.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
    value = sum(tr[:period]) / period
    out[period - 1] = value
    for i in range(period, len(candles)):
        value = (value * (period - 1) + tr[i]) / period
        out[i] = value
    return out
