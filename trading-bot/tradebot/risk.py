"""
Gestion du risque, commune au backtest et au trading réel.

Le principe : on décide d'abord où se trouve le stop, puis on calcule la
quantité pour que toucher ce stop ne coûte que `risk_per_trade` du capital.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from .models import Position


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.01      # part du capital perdue si le stop est touché
    stop_loss_pct: float = 0.03       # stop à -3 % sous le prix d'entrée...
    stop_atr_mult: float = 0.0        # ...ou, si > 0, à N × ATR sous l'entrée (s'adapte à la volatilité)
    atr_period: int = 14
    take_profit_rr: float = 2.0       # objectif = N × la distance du stop (0 = pas d'objectif)
    trailing_stop_pct: float = 0.0    # stop suiveur à -X % du plus haut (0 = désactivé)
    max_position_pct: float = 0.25    # jamais plus de 25 % du capital dans une position
    max_open_positions: int = 3
    max_daily_loss_pct: float = 0.05  # plus aucun achat ce jour-là après -5 % (0 = désactivé)
    fee_pct: float = 0.001            # frais par ordre (0,1 %)

    def __post_init__(self) -> None:
        def check(name: str, low: float, high: float, low_inclusive: bool = False) -> None:
            v = getattr(self, name)
            ok = (low <= v if low_inclusive else low < v) and v <= high
            if not ok:
                raise ValueError(f"risk.{name} = {v} hors limites")

        check("risk_per_trade", 0, 0.2)
        check("stop_loss_pct", 0, 0.5)
        check("stop_atr_mult", 0, 20, low_inclusive=True)
        check("take_profit_rr", 0, 50, low_inclusive=True)
        check("trailing_stop_pct", 0, 0.5, low_inclusive=True)
        check("max_position_pct", 0, 1)
        check("max_daily_loss_pct", 0, 1, low_inclusive=True)
        check("fee_pct", 0, 0.05, low_inclusive=True)
        if self.atr_period < 1 or self.max_open_positions < 1:
            raise ValueError("risk.atr_period et risk.max_open_positions doivent être >= 1")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskConfig:
        allowed = {f.name for f in fields(cls)}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Paramètre(s) de risque inconnu(s) : {', '.join(sorted(unknown))}")
        return cls(**data)


@dataclass(frozen=True)
class EntryPlan:
    qty: float
    stop: float
    target: float


def stop_price(entry: float, cfg: RiskConfig, atr_value: float | None = None) -> float:
    if cfg.stop_atr_mult and atr_value:
        stop = entry - cfg.stop_atr_mult * atr_value
        if 0 < stop < entry:
            return stop
    return entry * (1 - cfg.stop_loss_pct)


def position_size(equity: float, entry: float, stop: float, cfg: RiskConfig) -> float:
    """Quantité telle qu'une sortie au stop (frais compris) coûte `risk_per_trade` du capital."""
    if equity <= 0 or entry <= 0 or not 0 < stop < entry:
        return 0.0
    loss_per_unit = (entry - stop) + (entry + stop) * cfg.fee_pct
    qty = equity * cfg.risk_per_trade / loss_per_unit
    cap = equity * cfg.max_position_pct / (entry * (1 + cfg.fee_pct))
    return min(qty, cap)


def plan_entry(equity: float, free_cash: float, price: float, cfg: RiskConfig,
               atr_value: float | None = None) -> EntryPlan | None:
    stop = stop_price(price, cfg, atr_value)
    target = price + (price - stop) * cfg.take_profit_rr if cfg.take_profit_rr else 0.0
    qty = min(position_size(equity, price, stop, cfg), free_cash / (price * (1 + cfg.fee_pct)))
    return EntryPlan(qty, stop, target) if qty > 0 else None


def rebase(plan: EntryPlan, planned_price: float, fill_price: float) -> tuple[float, float]:
    """Décale stop et objectif si l'ordre a été exécuté à un autre prix que prévu."""
    shift = fill_price - planned_price
    return plan.stop + shift, plan.target + shift if plan.target else 0.0


def current_stop(pos: Position, cfg: RiskConfig) -> tuple[float, str]:
    if cfg.trailing_stop_pct:
        trailing = pos.highest * (1 - cfg.trailing_stop_pct)
        if trailing > pos.stop:
            return trailing, "stop suiveur"
    return pos.stop, "stop-loss"


def check_exit(pos: Position, cfg: RiskConfig, open_: float, low: float, high: float) -> tuple[str, float] | None:
    """
    (raison, prix de sortie) si un stop ou l'objectif est touché.
    Si les deux le sont dans la même bougie, on suppose le pire (le stop).
    En cas de gap, l'exécution se fait au prix d'ouverture.
    """
    stop, reason = current_stop(pos, cfg)
    if low <= stop:
        return reason, min(stop, open_)
    if pos.target and high >= pos.target:
        return "take-profit", max(pos.target, open_)
    return None
