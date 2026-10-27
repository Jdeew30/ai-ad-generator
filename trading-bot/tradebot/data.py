"""Données de marché : API publique Binance, ccxt, CSV ou marché fictif."""
from __future__ import annotations

import csv
import json
import math
import random
import time
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from .models import Candle

_UNITS_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
DAY_MS = _UNITS_MS["d"]


def timeframe_ms(timeframe: str) -> int:
    """'15m' -> 900000. Accepte m, h, d, w."""
    try:
        n, unit = int(timeframe[:-1]), timeframe[-1]
        if n <= 0 or unit not in _UNITS_MS:
            raise ValueError
    except (ValueError, IndexError):
        raise ValueError(f"Unité de temps invalide « {timeframe} » (ex. 1m, 15m, 1h, 4h, 1d)") from None
    return n * _UNITS_MS[unit]


def periods_per_year(timeframe: str) -> float:
    return 365 * DAY_MS / timeframe_ms(timeframe)  # la crypto cote 24 h/24, 7 j/7


class MarketData(Protocol):
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500,
                    since: int | None = None) -> list[Candle]: ...


class BinancePublic:
    """Bougies Binance sans clé API ni dépendance."""

    BASE_URL = "https://api.binance.com/api/v3/klines"

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500,
                    since: int | None = None) -> list[Candle]:
        query: dict[str, Any] = {"symbol": symbol.replace("/", ""), "interval": timeframe, "limit": min(limit, 1000)}
        if since is not None:
            query["startTime"] = since
        with urllib.request.urlopen(f"{self.BASE_URL}?{urllib.parse.urlencode(query)}", timeout=20) as resp:
            rows = json.load(resp)
        return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


class CcxtData:
    """Bougies de n'importe quel exchange supporté par ccxt."""

    def __init__(self, exchange: Any) -> None:
        self.exchange = exchange

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500,
                    since: int | None = None) -> list[Candle]:
        rows = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        return [Candle(int(r[0]), *(float(x) for x in r[1:6])) for r in rows]


def fetch_history(source: MarketData, symbol: str, timeframe: str, days: float,
                  now_ms: int | None = None) -> list[Candle]:
    """Historique de `days` jours, par pages, sans la bougie en cours de formation."""
    step = timeframe_ms(timeframe)
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    since = now - int(days * DAY_MS)
    out: list[Candle] = []
    while since < now:
        batch = source.fetch_ohlcv(symbol, timeframe, limit=1000, since=since)
        fresh = [c for c in batch if (not out or c.time > out[-1].time) and c.time + step <= now]
        out.extend(fresh)
        if not batch or batch[-1].time + step <= since:
            break
        since = batch[-1].time + step
        if not fresh:
            break
    return out


def load_csv(path: str | Path) -> list[Candle]:
    """CSV avec les colonnes time,open,high,low,close,volume (time en ms)."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = {"time", "open", "high", "low", "close", "volume"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} : colonnes manquantes {', '.join(sorted(missing))}")
        candles = [Candle(int(float(r["time"])), float(r["open"]), float(r["high"]),
                          float(r["low"]), float(r["close"]), float(r["volume"])) for r in reader]
    return sorted(candles, key=lambda c: c.time)


def save_csv(candles: Sequence[Candle], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.time, c.open, c.high, c.low, c.close, c.volume])


def synthetic(n: int = 3000, start: float = 100.0, seed: int = 42, timeframe: str = "1h") -> list[Candle]:
    """Marché fictif (phases de hausse et de baisse + bruit) pour tester sans réseau."""
    rng = random.Random(seed)
    step = timeframe_ms(timeframe)
    price, out = start, []
    for i in range(n):
        drift = 0.0008 * math.sin(i / 120)
        o = price
        c = max(0.01, o * (1 + drift + rng.gauss(0, 0.008)))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.003)))
        low = min(o, c) * (1 - abs(rng.gauss(0, 0.003)))
        out.append(Candle(1_700_000_000_000 + i * step, o, h, low, c, rng.uniform(10, 100)))
        price = c
    return out
