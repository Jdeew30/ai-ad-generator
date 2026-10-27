"""Statistiques de performance, pour le backtest comme pour le trading réel."""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from .models import Trade


@dataclass(frozen=True)
class Stats:
    total_return_pct: float
    buy_hold_pct: float
    max_drawdown_pct: float
    sharpe: float
    profit_factor: float | None  # None = aucune perte
    win_rate: float
    trades: int
    avg_trade_pct: float
    best_trade_pct: float
    worst_trade_pct: float
    exposure_pct: float  # % du temps passé en position

    @property
    def calmar(self) -> float:
        return self.total_return_pct / self.max_drawdown_pct if self.max_drawdown_pct else 0.0

    def as_dict(self) -> dict:
        return asdict(self) | {"calmar": self.calmar}

    def summary(self) -> str:
        pf = "∞" if self.profit_factor is None else f"{self.profit_factor:.2f}"
        return "\n".join([
            f"Rendement         : {self.total_return_pct:+.2f} %",
            f"Buy & hold        : {self.buy_hold_pct:+.2f} %",
            f"Drawdown max      : {self.max_drawdown_pct:.2f} %",
            f"Ratio de Sharpe   : {self.sharpe:.2f}",
            f"Profit factor     : {pf}",
            f"Trades            : {self.trades}",
            f"Taux de réussite  : {self.win_rate:.1f} %",
            f"Trade moyen       : {self.avg_trade_pct:+.2f} %",
            f"Meilleur / pire   : {self.best_trade_pct:+.2f} % / {self.worst_trade_pct:+.2f} %",
            f"Temps en position : {self.exposure_pct:.1f} %",
        ])


def max_drawdown_pct(equity: Sequence[float]) -> float:
    peak, worst = 0.0, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            worst = max(worst, (peak - e) / peak)
    return worst * 100


def sharpe_ratio(equity: Sequence[float], periods_per_year: float) -> float:
    returns = [b / a - 1 for a, b in zip(equity, equity[1:]) if a > 0]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return mean / math.sqrt(var) * math.sqrt(periods_per_year) if var > 0 else 0.0


def compute_stats(initial: float, equity: Sequence[float], trades: Sequence[Trade],
                  periods_per_year: float, buy_hold_pct: float = 0.0, exposure_pct: float = 0.0) -> Stats:
    final = equity[-1] if equity else initial
    gains = sum(t.pnl for t in trades if t.pnl > 0)
    losses = -sum(t.pnl for t in trades if t.pnl < 0)
    pcts = [t.pnl_pct for t in trades]
    return Stats(
        total_return_pct=(final / initial - 1) * 100 if initial else 0.0,
        buy_hold_pct=buy_hold_pct,
        max_drawdown_pct=max_drawdown_pct(equity),
        sharpe=sharpe_ratio(equity, periods_per_year),
        profit_factor=gains / losses if losses else (None if gains else 0.0),
        win_rate=100 * sum(t.pnl > 0 for t in trades) / len(trades) if trades else 0.0,
        trades=len(trades),
        avg_trade_pct=sum(pcts) / len(pcts) if pcts else 0.0,
        best_trade_pct=max(pcts, default=0.0),
        worst_trade_pct=min(pcts, default=0.0),
        exposure_pct=exposure_pct,
    )
