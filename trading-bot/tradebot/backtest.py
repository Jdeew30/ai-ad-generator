"""
Backtest : rejoue une stratégie sur l'historique avec les mêmes règles de
risque que le trading réel.

Hypothèses (prudentes) :
- un signal calculé à la clôture d'une bougie est exécuté à l'ouverture de la
  suivante : aucune donnée future n'est utilisée ;
- glissement de prix (slippage) et frais appliqués à chaque ordre ;
- stops et objectifs vérifiés sur le plus bas / plus haut de chaque bougie, en
  supposant le pire si les deux sont touchés dans la même bougie.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .data import DAY_MS, periods_per_year
from .indicators import atr
from .models import Candle, Position, Signal, Trade
from .risk import RiskConfig, check_exit, plan_entry
from .stats import Stats, compute_stats
from .strategies import Strategy


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    strategy: str
    initial_capital: float
    final_equity: float
    trades: list[Trade]
    times: list[int]
    equity: list[float]
    benchmark: list[float]  # capital en buy & hold, pour comparaison
    stats: Stats


def run_backtest(
    candles: Sequence[Candle],
    strategy: Strategy,
    risk: RiskConfig | None = None,
    initial_capital: float = 10_000.0,
    timeframe: str = "1h",
    symbol: str = "",
    slippage_pct: float = 0.0005,
) -> BacktestResult:
    risk = risk or RiskConfig()
    signals = strategy.signals(candles)
    atr_values = atr(candles, risk.atr_period) if risk.stop_atr_mult else [None] * len(candles)

    cash = initial_capital
    pos: Position | None = None
    trades: list[Trade] = []
    equity: list[float] = []
    exposure = 0
    day, day_start, halted = None, initial_capital, False

    def close(p: Position, time: int, price: float, reason: str) -> float:
        fill = price * (1 - slippage_pct)
        proceeds = p.qty * fill
        fee = proceeds * risk.fee_pct
        pnl = proceeds - fee - (p.qty * p.entry_price + p.entry_fee)
        trades.append(Trade(symbol, p.qty, p.entry_price, fill, p.opened_at, time, pnl, reason, p.entry_fee + fee))
        return proceeds - fee

    for i, c in enumerate(candles):
        if c.time // DAY_MS != day:
            day, halted = c.time // DAY_MS, False
            day_start = equity[-1] if equity else initial_capital

        prev = signals[i - 1] if i else Signal.HOLD
        if pos is None and prev is Signal.BUY and not halted:
            price = c.open * (1 + slippage_pct)
            plan = plan_entry(cash, cash, price, risk, atr_values[i - 1])
            if plan:
                fee = plan.qty * price * risk.fee_pct
                cash -= plan.qty * price + fee
                pos = Position(symbol, plan.qty, price, plan.stop, plan.target, price, c.time, fee)
        elif pos is not None and prev is Signal.SELL:
            cash += close(pos, c.time, c.open, "signal")
            pos = None

        if pos is not None:
            exposure += 1
            exit_ = check_exit(pos, risk, c.open, c.low, c.high)
            if exit_:
                cash += close(pos, c.time, exit_[1], exit_[0])
                pos = None
            else:
                pos.highest = max(pos.highest, c.high)

        value = cash + (pos.qty * c.close if pos else 0.0)
        equity.append(value)
        if risk.max_daily_loss_pct and value <= day_start * (1 - risk.max_daily_loss_pct):
            halted = True  # coupe-circuit : plus d'achat jusqu'au lendemain

    if pos is not None:
        cash += close(pos, candles[-1].time, candles[-1].close, "fin du backtest")
        equity[-1] = cash

    benchmark = [initial_capital * c.close / candles[0].open for c in candles] if candles else []
    buy_hold = (benchmark[-1] / initial_capital - 1) * 100 if benchmark else 0.0
    stats = compute_stats(initial_capital, equity, trades, periods_per_year(timeframe), buy_hold,
                          100 * exposure / len(candles) if candles else 0.0)
    return BacktestResult(symbol, timeframe, str(strategy), initial_capital, equity[-1] if equity else cash,
                          trades, [c.time for c in candles], equity, benchmark, stats)
