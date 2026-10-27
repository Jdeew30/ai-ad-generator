"""Gestion du risque : taille de position, stop-loss, take-profit."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.01   # 1 % du capital risqué par trade
    stop_loss_pct: float = 0.02    # stop à -2 %
    take_profit_pct: float = 0.04  # objectif à +4 %
    max_position_pct: float = 0.5  # jamais plus de 50 % du capital dans une position
    fee_pct: float = 0.001         # frais de 0,1 % par ordre

    def __post_init__(self) -> None:
        for name in ("risk_per_trade", "stop_loss_pct", "take_profit_pct", "max_position_pct"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} doit être compris entre 0 et 1")
        if not 0 <= self.fee_pct < 1:
            raise ValueError("fee_pct doit être compris entre 0 et 1")


def position_size(equity: float, price: float, cfg: RiskConfig) -> float:
    """
    Quantité à acheter pour que toucher le stop-loss ne coûte que
    `risk_per_trade` du capital, plafonnée à `max_position_pct` du capital.
    """
    if equity <= 0 or price <= 0:
        return 0.0
    by_risk = equity * cfg.risk_per_trade / cfg.stop_loss_pct
    notional = min(by_risk, equity * cfg.max_position_pct)
    return notional / (price * (1 + cfg.fee_pct))


def exit_levels(entry: float, cfg: RiskConfig) -> tuple[float, float]:
    """(stop_loss, take_profit) pour un prix d'entrée donné."""
    return entry * (1 - cfg.stop_loss_pct), entry * (1 + cfg.take_profit_pct)
