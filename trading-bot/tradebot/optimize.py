"""
Optimisation des paramètres avec validation hors échantillon.

Les paramètres sont choisis sur la première partie de l'historique
(« entraînement ») puis vérifiés sur la suite, jamais vue pendant le choix
(« test »). Si les résultats de test sont nettement moins bons, la stratégie
est sur-ajustée au passé : ne pas l'utiliser telle quelle.
"""
from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .backtest import run_backtest
from .models import Candle
from .risk import RiskConfig
from .stats import Stats
from .strategies import STRATEGIES, build_strategy

METRICS: dict[str, Callable[[Stats], float]] = {
    "sharpe": lambda s: s.sharpe,
    "return": lambda s: s.total_return_pct,
    "calmar": lambda s: s.calmar,
}


@dataclass(frozen=True)
class OptimizationRow:
    params: dict[str, Any]
    score: float
    train: Stats
    test: Stats | None


def optimize(
    candles: Sequence[Candle],
    strategy_name: str,
    risk: RiskConfig | None = None,
    timeframe: str = "1h",
    metric: str = "sharpe",
    train_ratio: float = 0.7,
    top: int = 5,
    min_trades: int = 5,
    max_combos: int = 500,
    base_params: dict[str, Any] | None = None,
) -> list[OptimizationRow]:
    if metric not in METRICS:
        raise ValueError(f"Critère inconnu « {metric} » ({', '.join(METRICS)})")
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Stratégie inconnue « {strategy_name} »")
    if not 0.3 <= train_ratio <= 0.9:
        raise ValueError("train_ratio doit être compris entre 0.3 et 0.9")

    space = STRATEGIES[strategy_name].search_space
    split = int(len(candles) * train_ratio)
    train, test = candles[:split], candles[split:]

    scored: list[tuple[float, dict[str, Any], Stats]] = []
    for values in itertools.islice(itertools.product(*space.values()), max_combos):
        params = dict(base_params or {}) | dict(zip(space, values))
        try:
            strategy = build_strategy(strategy_name, params)
        except ValueError:
            continue  # combinaison invalide (ex. fast >= slow)
        if strategy.warmup * 2 > len(train):
            continue
        stats = run_backtest(train, strategy, risk, timeframe=timeframe).stats
        score = METRICS[metric](stats) if stats.trades >= min_trades else float("-inf")
        scored.append((score, params, stats))

    scored.sort(key=lambda row: row[0], reverse=True)
    rows = []
    for score, params, train_stats in scored[:top]:
        strategy = build_strategy(strategy_name, params)
        test_stats = (run_backtest(test, strategy, risk, timeframe=timeframe).stats
                      if len(test) > strategy.warmup * 2 else None)
        rows.append(OptimizationRow(params, score, train_stats, test_stats))
    return rows
