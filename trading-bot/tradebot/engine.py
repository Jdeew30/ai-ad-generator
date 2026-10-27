"""
Moteur de trading en continu (paper ou live).

À chaque itération et pour chaque paire :
1. lit les bougies et le dernier prix ;
2. protège la position ouverte : stop-loss, stop suiveur, objectif ;
3. à chaque NOUVELLE bougie clôturée, applique le signal de la stratégie.

Un signal n'est appliqué qu'une fois par bougie : après un stop, le bot ne
rachète pas sur le même signal. Au tout premier démarrage, la bougie déjà
clôturée est seulement mémorisée, pour ne pas entrer sur un signal ancien.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from .config import Settings
from .data import DAY_MS, MarketData, timeframe_ms
from .exchange import Broker
from .indicators import atr
from .models import Position, Signal, Trade
from .notify import NullNotifier, Notifier
from .risk import check_exit, current_stop, plan_entry, rebase
from .store import Store
from .strategies import Strategy

log = logging.getLogger("tradebot.engine")

TELEGRAM_COMMANDS = {
    "/pause": "pause", "/reprendre": "resume", "/resume": "resume",
    "/fermer_tout": "close_all", "/closeall": "close_all",
    "/statut": "status", "/status": "status",
    "/aide": "help", "/help": "help", "/start": "help",
}

HELP = (
    "Commandes :\n"
    "/statut — capital et positions\n"
    "/pause — plus de nouvel achat (les stops restent actifs)\n"
    "/reprendre — reprendre les achats\n"
    "/fermer_tout — vendre toutes les positions et mettre en pause"
)

ERROR_NOTIFY_INTERVAL = 3600  # secondes entre deux alertes d'erreur pour une même paire
EQUITY_SNAPSHOT_MS = 5 * 60_000


class Engine:
    def __init__(self, settings: Settings, broker: Broker, data: MarketData, store: Store,
                 notifier: Notifier | None = None, strategy: Strategy | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.s = settings
        self.risk = settings.risk
        self.broker = broker
        self.data = data
        self.store = store
        self.notifier = notifier or NullNotifier()
        self.strategy = strategy or settings.build_strategy()
        self.clock = clock
        self.tf_ms = timeframe_ms(settings.timeframe)
        self.history = min(1000, max(300, self.strategy.warmup * 3))
        self.prices: dict[str, float] = {}
        self.stop_event = threading.Event()
        self._cash: float | None = None
        self._error_notified: dict[str, float] = {}

    # ------------------------------------------------------------------ boucle
    def run(self, max_ticks: int | None = None) -> None:
        intro = (f"🚀 {self.s.brand} démarré [{self.s.mode_label}] — {self.strategy.label} "
                 f"sur {', '.join(self.s.symbols)} en {self.s.timeframe}")
        self._event("info", intro)
        self.notifier.send(intro)
        ticks = 0
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as e:  # erreur réseau ou d'API : on réessaie au tour suivant
                self._report_error("général", e)
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            self.stop_event.wait(self.s.poll_seconds)
        self._event("info", "Arrêt du bot")
        self.notifier.send(f"⏹ {self.s.brand} arrêté. Les positions ouvertes ne sont plus surveillées.")

    def stop(self) -> None:
        self.stop_event.set()

    def tick(self) -> None:
        self._cash = None
        self._handle_commands()
        self._roll_day()
        for symbol in self.s.symbols:
            try:
                self._process(symbol)
            except Exception as e:  # une paire en erreur ne bloque pas les autres
                self._report_error(symbol, e)
        self._check_daily_loss()
        self._publish_status()

    # ------------------------------------------------------------- une paire
    def _process(self, symbol: str) -> None:
        candles = self.data.fetch_ohlcv(symbol, self.s.timeframe, limit=self.history)
        if not candles:
            return
        now = self.now_ms()
        price = candles[-1].close
        self.prices[symbol] = price
        closed = [c for c in candles if c.time + self.tf_ms <= now]

        pos = self.store.get_position(symbol)
        if pos:
            exit_ = check_exit(pos, self.risk, price, price, price)
            if exit_:
                self._close(pos, price, exit_[0])
                pos = None
            elif price > pos.highest:
                pos.highest = price
                self.store.save_position(pos)

        if len(closed) < self.strategy.warmup:
            return
        key = f"last_candle:{symbol}"
        seen = self.store.get(key)
        if seen == closed[-1].time:
            return
        self.store.set(key, closed[-1].time)
        if seen is None:
            return  # premier démarrage : on attend la prochaine bougie clôturée

        signal = self.strategy.signals(closed)[-1]
        if signal is Signal.BUY and pos is None:
            self._open(symbol, closed, price)
        elif signal is Signal.SELL and pos is not None:
            self._close(pos, price, "signal")

    def _open(self, symbol: str, closed: list, price: float) -> None:
        self._check_daily_loss()
        blocked = self._block_reason()
        if blocked:
            self._event("info", f"{symbol} : signal d'achat ignoré ({blocked})")
            return
        atr_value = atr(closed, self.risk.atr_period)[-1] if self.risk.stop_atr_mult else None
        plan = plan_entry(self.equity(), self.cash(), price, self.risk, atr_value)
        if plan is None:
            self._event("warning", f"{symbol} : signal d'achat ignoré (capital disponible insuffisant)")
            return
        problem = self.broker.check_order(symbol, plan.qty, price)
        if problem:
            self._event("warning", f"{symbol} : signal d'achat ignoré ({problem})")
            return

        fill = self.broker.buy(symbol, plan.qty, price)
        self._cash = None
        stop, target = rebase(plan, price, fill.price)
        self.store.save_position(Position(symbol, fill.qty, fill.price, stop, target, fill.price,
                                          self.now_ms(), fill.fee))
        msg = (f"🟢 ACHAT {symbol} [{self.s.mode_label}]\n{fill.qty:.6g} @ {fill.price:.6g} "
               f"= {fill.qty * fill.price:,.2f} {self.s.quote}\nStop {stop:.6g}"
               + (f" · Objectif {target:.6g}" if target else ""))
        self._event("trade", msg)
        self.notifier.send(msg)

    def _close(self, pos: Position, price: float, reason: str) -> None:
        available = self.broker.free_base(pos.symbol)
        if available < pos.qty * 0.5:
            self.store.delete_position(pos.symbol)
            self._alert("warning", f"⚠️ {pos.symbol} : position introuvable sur le compte "
                                   f"(vendue manuellement ?). Elle n'est plus suivie par le bot.")
            return
        fill = self.broker.sell(pos.symbol, pos.qty, price)
        self._cash = None
        share = fill.qty / pos.qty if pos.qty else 1.0
        cost = (pos.qty * pos.entry_price + pos.entry_fee) * share
        pnl = fill.qty * fill.price - fill.fee - cost
        self.store.add_trade(Trade(pos.symbol, fill.qty, pos.entry_price, fill.price, pos.opened_at,
                                   self.now_ms(), pnl, reason, pos.entry_fee * share + fill.fee))
        self.store.delete_position(pos.symbol)
        pct = pnl / cost * 100 if cost else 0.0
        msg = (f"{'✅' if pnl >= 0 else '🔻'} VENTE {pos.symbol} [{self.s.mode_label}] — {reason}\n"
               f"{fill.qty:.6g} @ {fill.price:.6g} · Résultat {pnl:+,.2f} {self.s.quote} ({pct:+.2f} %)")
        self._event("trade", msg)
        self.notifier.send(msg)

    # ------------------------------------------------------------ garde-fous
    def _block_reason(self) -> str | None:
        if self.store.get("paused", False):
            return "bot en pause"
        if self.store.get("halted_day") == self.today():
            return "perte journalière maximale atteinte"
        if len(self.store.positions()) >= self.risk.max_open_positions:
            return f"maximum de {self.risk.max_open_positions} positions ouvertes atteint"
        return None

    def _roll_day(self) -> None:
        today, previous = self.today(), self.store.get("day")
        if previous == today:
            return
        if previous:
            self._daily_summary(previous)
        self.store.set("day", today)
        self.store.set("day_start_equity", self.equity())

    def _check_daily_loss(self) -> None:
        limit = self.risk.max_daily_loss_pct
        if not limit or self.store.get("halted_day") == self.today():
            return
        start = self.store.get("day_start_equity")
        equity = self.equity()
        if start and equity <= start * (1 - limit):
            self.store.set("halted_day", self.today())
            self._alert("warning", f"🛑 Coupe-circuit : perte de {(1 - equity / start) * 100:.1f} % aujourd'hui. "
                                   "Plus aucun achat jusqu'à demain (UTC). Les stops restent actifs.")

    def _daily_summary(self, day: str) -> None:
        start = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        trades = [t for t in self.store.trades(since=start) if t.closed_at < start + DAY_MS]
        pnl = sum(t.pnl for t in trades)
        self.notifier.send(f"📊 Bilan du {day} [{self.s.mode_label}]\n{len(trades)} trade(s) clôturé(s), "
                           f"résultat {pnl:+,.2f} {self.s.quote}\nCapital : {self.equity():,.2f} {self.s.quote}")

    # -------------------------------------------------------------- commandes
    def _handle_commands(self) -> None:
        commands = self.store.pop_commands()
        commands += [TELEGRAM_COMMANDS.get(c, "help") for c in self.notifier.poll_commands()]
        for cmd in commands:
            if cmd == "pause":
                self.store.set("paused", True)
                self._alert("info", "⏸ Pause : plus aucun nouvel achat. Les positions ouvertes restent protégées.")
            elif cmd == "resume":
                self.store.set("paused", False)
                self._alert("info", "▶️ Reprise : le bot peut de nouveau acheter.")
            elif cmd == "close_all":
                self.close_all()
            elif cmd == "status":
                self.notifier.send(self.status_text())
            else:
                self.notifier.send(HELP)

    def close_all(self) -> None:
        self.store.set("paused", True)
        positions = self.store.positions()
        for pos in positions:
            try:
                price = self.prices.get(pos.symbol)
                if price is None:
                    price = self.data.fetch_ohlcv(pos.symbol, self.s.timeframe, limit=2)[-1].close
                self._close(pos, price, "fermeture manuelle")
            except Exception as e:
                self._report_error(pos.symbol, e)
        self._alert("warning", f"🧯 Fermeture de {len(positions)} position(s) demandée. Bot mis en pause.")

    # ---------------------------------------------------------------- état
    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def today(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%d")

    def cash(self) -> float:
        if self._cash is None:
            self._cash = self.broker.free_quote()
        return self._cash

    def equity(self) -> float:
        return self.cash() + sum(p.qty * self.prices.get(p.symbol, p.entry_price) for p in self.store.positions())

    def _publish_status(self) -> None:
        now, positions = self.now_ms(), []
        for p in self.store.positions():
            price = self.prices.get(p.symbol, p.entry_price)
            cost = p.qty * p.entry_price + p.entry_fee
            pnl = p.qty * price * (1 - self.risk.fee_pct) - cost
            positions.append({
                "symbol": p.symbol, "qty": p.qty, "entry_price": p.entry_price, "price": price,
                "value": p.qty * price, "pnl": pnl, "pnl_pct": pnl / cost * 100 if cost else 0.0,
                "stop": current_stop(p, self.risk)[0], "target": p.target, "opened_at": p.opened_at,
            })
        equity = self.equity()
        self.store.set("status", {
            "brand": self.s.brand, "mode": self.s.mode_label, "exchange": self.s.exchange,
            "strategy": str(self.strategy), "strategy_label": self.strategy.label,
            "timeframe": self.s.timeframe, "symbols": self.s.symbols, "quote": self.s.quote,
            "paused": bool(self.store.get("paused", False)),
            "halted": self.store.get("halted_day") == self.today(),
            "updated_at": now, "cash": self.cash(), "equity": equity,
            "day_start_equity": self.store.get("day_start_equity"), "positions": positions,
        })
        if now - self.store.get("last_equity_ts", 0) >= EQUITY_SNAPSHOT_MS:
            self.store.add_equity(now, equity)
            self.store.set("last_equity_ts", now)

    def status_text(self) -> str:
        status = self.store.get("status") or {}
        start = status.get("day_start_equity") or status.get("equity") or 0
        lines = [f"{self.s.brand} [{self.s.mode_label}]"
                 + (" — EN PAUSE" if status.get("paused") else "")
                 + (" — COUPE-CIRCUIT" if status.get("halted") else ""),
                 f"Capital : {status.get('equity', 0):,.2f} {self.s.quote} "
                 f"(jour : {status.get('equity', 0) - start:+,.2f})"]
        for p in status.get("positions", []):
            lines.append(f"• {p['symbol']} {p['pnl_pct']:+.2f} % (stop {p['stop']:.6g})")
        if not status.get("positions"):
            lines.append("Aucune position ouverte")
        return "\n".join(lines)

    # ---------------------------------------------------------------- journal
    def _event(self, level: str, message: str) -> None:
        getattr(log, "warning" if level in ("warning", "error") else "info")(message.replace("\n", " | "))
        self.store.log_event(level, message)

    def _alert(self, level: str, message: str) -> None:
        self._event(level, message)
        self.notifier.send(message)

    def _report_error(self, where: str, error: Exception) -> None:
        log.exception("Erreur (%s)", where)
        message = f"Erreur sur {where} : {type(error).__name__}: {error}"
        self.store.log_event("error", message)
        last = self._error_notified.get(where, 0.0)
        if self.clock() - last >= ERROR_NOTIFY_INTERVAL:
            self._error_notified[where] = self.clock()
            self.notifier.send(f"⚠️ {message}")
