"""Données de marché : bougies OHLCV depuis un CSV, Binance (public) ou générées."""
from __future__ import annotations

import csv
import json
import math
import random
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Candle:
    time: int  # horodatage d'ouverture, en millisecondes
    open: float
    high: float
    low: float
    close: float
    volume: float


def load_csv(path: str | Path) -> list[Candle]:
    """CSV avec les colonnes time,open,high,low,close,volume."""
    with open(path, newline="") as f:
        return [
            Candle(int(float(r["time"])), float(r["open"]), float(r["high"]),
                   float(r["low"]), float(r["close"]), float(r["volume"]))
            for r in csv.DictReader(f)
        ]


def save_csv(candles: list[Candle], path: str | Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.time, c.open, c.high, c.low, c.close, c.volume])


def fetch_binance(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 1000) -> list[Candle]:
    """Bougies depuis l'API publique de Binance (aucune clé nécessaire)."""
    query = urllib.parse.urlencode({"symbol": symbol.replace("/", ""), "interval": interval, "limit": limit})
    with urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?{query}", timeout=15) as resp:
        rows = json.load(resp)
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def synthetic(n: int = 2000, start: float = 100.0, seed: int = 42) -> list[Candle]:
    """Marché fictif (marche aléatoire avec tendances) pour tester sans réseau."""
    rng = random.Random(seed)
    price, out = start, []
    for i in range(n):
        drift = 0.0006 * math.sin(i / 150)
        o = price
        c = max(0.01, o * (1 + drift + rng.gauss(0, 0.01)))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.003)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.003)))
        out.append(Candle(i * 3_600_000, o, h, l, c, rng.uniform(10, 100)))
        price = c
    return out
