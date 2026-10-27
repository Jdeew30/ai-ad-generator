"""Exécution des ordres : portefeuille simulé ou exchange réel via ccxt."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from .models import Fill


def split_symbol(symbol: str) -> tuple[str, str]:
    base, _, quote = symbol.partition("/")
    if not base or not quote:
        raise ValueError(f"Paire invalide « {symbol} » (format attendu : BTC/USDT)")
    return base, quote


class Broker(Protocol):
    def free_quote(self) -> float: ...
    def free_base(self, symbol: str) -> float: ...
    def check_order(self, symbol: str, qty: float, price: float) -> str | None:
        """Raison du refus si l'ordre est sous les minimums de l'exchange, sinon None."""
    def buy(self, symbol: str, qty: float, price: float) -> Fill: ...
    def sell(self, symbol: str, qty: float, price: float) -> Fill: ...


class PaperBroker:
    """
    Portefeuille fictif (aucun argent réel). Le solde est transmis à `on_change`
    après chaque ordre pour être sauvegardé et survivre à un redémarrage.
    """

    def __init__(self, capital: float, fee_pct: float = 0.001, slippage_pct: float = 0.0005,
                 wallet: dict | None = None, on_change: Callable[[dict], None] | None = None,
                 min_order_value: float = 10.0) -> None:
        self.wallet = wallet or {"quote": capital, "base": {}}
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.min_order_value = min_order_value
        self.on_change = on_change

    def free_quote(self) -> float:
        return self.wallet["quote"]

    def free_base(self, symbol: str) -> float:
        return self.wallet["base"].get(symbol, 0.0)

    def check_order(self, symbol: str, qty: float, price: float) -> str | None:
        if qty * price < self.min_order_value:
            return f"montant {qty * price:.2f} inférieur au minimum de {self.min_order_value:.2f}"
        return None

    def buy(self, symbol: str, qty: float, price: float) -> Fill:
        fill_price = price * (1 + self.slippage_pct)
        cost = qty * fill_price
        fee = cost * self.fee_pct
        if cost + fee > self.wallet["quote"] + 1e-9:
            raise ValueError("Solde insuffisant")
        self.wallet["quote"] -= cost + fee
        self.wallet["base"][symbol] = self.free_base(symbol) + qty
        self._changed()
        return Fill("buy", qty, fill_price, fee)

    def sell(self, symbol: str, qty: float, price: float) -> Fill:
        qty = min(qty, self.free_base(symbol))
        fill_price = price * (1 - self.slippage_pct)
        proceeds = qty * fill_price
        fee = proceeds * self.fee_pct
        self.wallet["quote"] += proceeds - fee
        remaining = self.free_base(symbol) - qty
        if remaining > 1e-12:
            self.wallet["base"][symbol] = remaining
        else:
            self.wallet["base"].pop(symbol, None)
        self._changed()
        return Fill("sell", qty, fill_price, fee)

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(self.wallet)


def build_ccxt(exchange_id: str, api_key: str = "", secret: str = "", password: str = "",
               sandbox: bool = True) -> Any:
    try:
        import ccxt  # dépendance optionnelle : uniquement pour le mode live
    except ImportError:
        raise SystemExit("Ce mode nécessite ccxt : pip install -r requirements.txt") from None
    cls = getattr(ccxt, exchange_id, None)
    if cls is None:
        raise SystemExit(f"Exchange « {exchange_id} » non supporté par ccxt")
    options: dict[str, Any] = {"enableRateLimit": True}
    if api_key:
        options |= {"apiKey": api_key, "secret": secret}
    if password:
        options["password"] = password
    exchange = cls(options)
    if sandbox:
        exchange.set_sandbox_mode(True)
    exchange.load_markets()
    return exchange


class CcxtBroker:
    """Ordres au marché sur un exchange réel (ou son testnet)."""

    def __init__(self, exchange: Any, quote: str) -> None:
        self.exchange = exchange
        self.quote = quote

    def _free(self, currency: str) -> float:
        return float(self.exchange.fetch_balance()["free"].get(currency, 0.0) or 0.0)

    def free_quote(self) -> float:
        return self._free(self.quote)

    def free_base(self, symbol: str) -> float:
        return self._free(split_symbol(symbol)[0])

    def check_order(self, symbol: str, qty: float, price: float) -> str | None:
        limits = self.exchange.market(symbol).get("limits", {})
        min_amount = (limits.get("amount") or {}).get("min") or 0
        min_cost = (limits.get("cost") or {}).get("min") or 0
        try:
            amount = float(self.exchange.amount_to_precision(symbol, qty))
        except Exception as e:  # ccxt lève une erreur si la quantité arrondie est trop faible
            return f"quantité {qty:.8f} refusée par l'exchange ({e})"
        if amount < min_amount:
            return f"quantité {amount} inférieure au minimum {min_amount}"
        if amount * price < min_cost:
            return f"montant {amount * price:.2f} inférieur au minimum {min_cost}"
        return None

    def buy(self, symbol: str, qty: float, price: float) -> Fill:
        amount = float(self.exchange.amount_to_precision(symbol, qty))
        order = self._settled(self.exchange.create_market_buy_order(symbol, amount), symbol)
        return self._fill(order, symbol, "buy", price)

    def sell(self, symbol: str, qty: float, price: float) -> Fill:
        # Ne jamais vendre plus que ce qui est réellement disponible (frais prélevés en crypto, etc.)
        amount = float(self.exchange.amount_to_precision(symbol, min(qty, self.free_base(symbol))))
        order = self._settled(self.exchange.create_market_sell_order(symbol, amount), symbol)
        return self._fill(order, symbol, "sell", price)

    def _settled(self, order: dict, symbol: str) -> dict:
        if order.get("filled") is None or order.get("average") is None:
            try:
                return self.exchange.fetch_order(order["id"], symbol)
            except Exception:
                return order
        return order

    def _fill(self, order: dict, symbol: str, side: str, price_hint: float) -> Fill:
        base, quote = split_symbol(symbol)
        filled = float(order.get("filled") or order.get("amount") or 0.0)
        avg = float(order.get("average") or order.get("price") or price_hint)
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        base_fee = quote_fee = 0.0
        for fee in fees:
            cost = float(fee.get("cost") or 0.0)
            if fee.get("currency") == base:
                base_fee += cost
            elif fee.get("currency") == quote:
                quote_fee += cost
            # frais payés dans un autre jeton (ex. BNB) : n'affectent pas les soldes suivis
        qty = filled - base_fee if side == "buy" else filled
        return Fill(side, qty, avg, quote_fee + base_fee * avg)
