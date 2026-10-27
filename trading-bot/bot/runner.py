"""Boucle de trading en continu (paper ou live)."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .broker import Broker
from .data import Candle
from .risk import RiskConfig, exit_levels, position_size
from .strategy import MaCrossoverStrategy, Signal

log = logging.getLogger("bot")


@dataclass
class Position:
    qty: float
    entry: float
    stop: float
    target: float


class TradingBot:
    """
    À chaque itération : récupère les bougies, ignore la bougie en cours,
    vérifie stop-loss / take-profit au dernier prix puis applique le signal.
    La position ouverte est sauvegardée dans `state_file` pour survivre à un
    redémarrage.
    """

    def __init__(
        self,
        broker: Broker,
        fetch_candles: Callable[[], list[Candle]],
        strategy: MaCrossoverStrategy | None = None,
        risk: RiskConfig | None = None,
        state_file: str | Path | None = None,
    ) -> None:
        self.broker = broker
        self.fetch_candles = fetch_candles
        self.strategy = strategy or MaCrossoverStrategy()
        self.risk = risk or RiskConfig()
        self.state_file = Path(state_file) if state_file else None
        self.position: Position | None = self._load_state()

    def step(self) -> str:
        """Une itération. Renvoie l'action effectuée (pour les logs et les tests)."""
        candles = self.fetch_candles()
        closed = candles[:-1]  # la dernière bougie n'est pas encore clôturée
        if len(closed) < self.strategy.warmup:
            return "attente: pas assez de données"
        price = candles[-1].close

        if self.position:
            if price <= self.position.stop:
                return self._sell(price, "stop-loss")
            if price >= self.position.target:
                return self._sell(price, "take-profit")

        signal = self.strategy.latest_signal([c.close for c in closed])
        if signal is Signal.BUY and not self.position:
            return self._buy(price)
        if signal is Signal.SELL and self.position:
            return self._sell(price, "signal")
        return f"rien à faire (signal={signal.value}, prix={price:.4f})"

    def run(self, poll_seconds: float, max_steps: int | None = None) -> None:
        steps = 0
        while max_steps is None or steps < max_steps:
            try:
                log.info(self.step())
            except Exception:  # le bot ne doit pas mourir sur une erreur réseau ponctuelle
                log.exception("Erreur pendant l'itération")
            steps += 1
            if max_steps is None or steps < max_steps:
                time.sleep(poll_seconds)

    def _buy(self, price: float) -> str:
        qty = position_size(self.broker.quote_balance(), price, self.risk)
        if qty <= 0:
            return "achat ignoré: solde insuffisant"
        fill = self.broker.market_buy(qty, price)
        stop, target = exit_levels(fill.price, self.risk)
        self.position = Position(fill.qty, fill.price, stop, target)
        self._save_state()
        return f"ACHAT {fill.qty:.6f} @ {fill.price:.4f} (stop {stop:.4f}, objectif {target:.4f})"

    def _sell(self, price: float, reason: str) -> str:
        assert self.position
        fill = self.broker.market_sell(self.position.qty, price)
        pnl = (fill.price - self.position.entry) * fill.qty - fill.fee
        self.position = None
        self._save_state()
        return f"VENTE {fill.qty:.6f} @ {fill.price:.4f} [{reason}] PnL≈{pnl:+.2f}"

    def _load_state(self) -> Position | None:
        if self.state_file and self.state_file.exists():
            data = json.loads(self.state_file.read_text())
            return Position(**data["position"]) if data.get("position") else None
        return None

    def _save_state(self) -> None:
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"position": asdict(self.position) if self.position else None}
            self.state_file.write_text(json.dumps(payload, indent=2))
