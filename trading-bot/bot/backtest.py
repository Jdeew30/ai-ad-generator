"""Backtest d'une stratégie long-only sur des bougies historiques."""
from __future__ import annotations

from dataclasses import dataclass, field

from .data import Candle
from .risk import RiskConfig, exit_levels, position_size
from .strategy import MaCrossoverStrategy, Signal


@dataclass
class Trade:
    entry_time: int
    entry_price: float
    qty: float
    exit_time: int | None = None
    exit_price: float | None = None
    reason: str = ""
    pnl: float = 0.0


@dataclass
class BacktestResult:
    initial_capital: float
    final_equity: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    buy_and_hold_pct: float = 0.0

    @property
    def return_pct(self) -> float:
        return (self.final_equity / self.initial_capital - 1) * 100

    @property
    def win_rate(self) -> float:
        closed = [t for t in self.trades if t.exit_price is not None]
        return 100 * sum(t.pnl > 0 for t in closed) / len(closed) if closed else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        peak, worst = float("-inf"), 0.0
        for e in self.equity_curve:
            peak = max(peak, e)
            worst = max(worst, (peak - e) / peak if peak > 0 else 0)
        return worst * 100

    def summary(self) -> str:
        return "\n".join([
            f"Capital initial  : {self.initial_capital:,.2f}",
            f"Capital final    : {self.final_equity:,.2f}",
            f"Rendement        : {self.return_pct:+.2f} %",
            f"Buy & hold       : {self.buy_and_hold_pct:+.2f} %",
            f"Trades           : {len(self.trades)}",
            f"Taux de réussite : {self.win_rate:.1f} %",
            f"Drawdown max     : {self.max_drawdown_pct:.2f} %",
        ])


def run_backtest(
    candles: list[Candle],
    strategy: MaCrossoverStrategy | None = None,
    risk: RiskConfig | None = None,
    initial_capital: float = 10_000.0,
) -> BacktestResult:
    """
    Les signaux sont calculés à la clôture d'une bougie et exécutés à
    l'ouverture de la suivante (pas de biais d'anticipation). Stop-loss et
    take-profit sont vérifiés sur le plus bas / plus haut de chaque bougie ;
    si les deux sont touchés dans la même bougie, on suppose le pire (stop).
    """
    strategy = strategy or MaCrossoverStrategy()
    risk = risk or RiskConfig()
    signals = strategy.signals([c.close for c in candles])

    cash, trade = initial_capital, None
    stop = target = 0.0
    result = BacktestResult(initial_capital, initial_capital)

    def close(t: Trade, time: int, price: float, reason: str) -> float:
        t.exit_time, t.exit_price, t.reason = time, price, reason
        proceeds = t.qty * price * (1 - risk.fee_pct)
        t.pnl = proceeds - t.qty * t.entry_price * (1 + risk.fee_pct)
        return proceeds

    for i, c in enumerate(candles):
        prev_signal = signals[i - 1] if i > 0 else Signal.HOLD

        if trade is None and prev_signal is Signal.BUY:
            qty = position_size(cash, c.open, risk)
            if qty > 0:
                cash -= qty * c.open * (1 + risk.fee_pct)
                trade = Trade(c.time, c.open, qty)
                stop, target = exit_levels(c.open, risk)
                result.trades.append(trade)
        elif trade is not None and prev_signal is Signal.SELL:
            cash += close(trade, c.time, c.open, "signal")
            trade = None

        if trade is not None:
            if c.low <= stop:
                cash += close(trade, c.time, min(stop, c.open), "stop-loss")
                trade = None
            elif c.high >= target:
                cash += close(trade, c.time, max(target, c.open), "take-profit")
                trade = None

        result.equity_curve.append(cash + (trade.qty * c.close if trade else 0.0))

    if trade is not None:
        last = candles[-1]
        cash += close(trade, last.time, last.close, "fin du backtest")
        result.equity_curve[-1] = cash

    result.final_equity = cash
    if candles:
        result.buy_and_hold_pct = (candles[-1].close / candles[0].open - 1) * 100
    return result
