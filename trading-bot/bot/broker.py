"""Exécution des ordres : simulation (paper) ou réelle via ccxt."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Fill:
    side: str  # "buy" ou "sell"
    qty: float
    price: float
    fee: float


class Broker(Protocol):
    def quote_balance(self) -> float: ...
    def market_buy(self, qty: float, price_hint: float) -> Fill: ...
    def market_sell(self, qty: float, price_hint: float) -> Fill: ...


class PaperBroker:
    """Portefeuille fictif : aucun argent réel n'est engagé."""

    def __init__(self, cash: float = 10_000.0, fee_pct: float = 0.001) -> None:
        self.cash = cash
        self.base = 0.0
        self.fee_pct = fee_pct

    def quote_balance(self) -> float:
        return self.cash

    def market_buy(self, qty: float, price_hint: float) -> Fill:
        cost = qty * price_hint
        fee = cost * self.fee_pct
        if cost + fee > self.cash + 1e-9:
            raise ValueError("Solde insuffisant")
        self.cash -= cost + fee
        self.base += qty
        return Fill("buy", qty, price_hint, fee)

    def market_sell(self, qty: float, price_hint: float) -> Fill:
        qty = min(qty, self.base)
        proceeds = qty * price_hint
        fee = proceeds * self.fee_pct
        self.cash += proceeds - fee
        self.base -= qty
        return Fill("sell", qty, price_hint, fee)


class CcxtBroker:
    """
    Ordres réels sur un exchange supporté par ccxt (Binance, Kraken, ...).
    Les clés sont lues dans TRADING_API_KEY / TRADING_API_SECRET.
    """

    def __init__(self, exchange_id: str, symbol: str, sandbox: bool = True) -> None:
        try:
            import ccxt  # import local : dépendance optionnelle
        except ImportError as e:
            raise SystemExit("Le mode live nécessite ccxt : pip install -r requirements.txt") from e
        key, secret = os.environ.get("TRADING_API_KEY"), os.environ.get("TRADING_API_SECRET")
        if not key or not secret:
            raise SystemExit("Définissez TRADING_API_KEY et TRADING_API_SECRET (voir .env.example)")
        self.exchange = getattr(ccxt, exchange_id)({"apiKey": key, "secret": secret, "enableRateLimit": True})
        if sandbox:
            self.exchange.set_sandbox_mode(True)
        self.symbol = symbol
        self.quote = symbol.split("/")[1]

    def quote_balance(self) -> float:
        return float(self.exchange.fetch_balance()["free"].get(self.quote, 0.0))

    def fetch_ohlcv(self, timeframe: str, limit: int) -> list[list[float]]:
        return self.exchange.fetch_ohlcv(self.symbol, timeframe, limit=limit)

    def _fill(self, order: dict, side: str, price_hint: float) -> Fill:
        fee = (order.get("fee") or {}).get("cost") or 0.0
        return Fill(side, float(order.get("filled") or order["amount"]),
                    float(order.get("average") or price_hint), float(fee))

    def market_buy(self, qty: float, price_hint: float) -> Fill:
        qty = float(self.exchange.amount_to_precision(self.symbol, qty))
        return self._fill(self.exchange.create_market_buy_order(self.symbol, qty), "buy", price_hint)

    def market_sell(self, qty: float, price_hint: float) -> Fill:
        qty = float(self.exchange.amount_to_precision(self.symbol, qty))
        return self._fill(self.exchange.create_market_sell_order(self.symbol, qty), "sell", price_hint)
