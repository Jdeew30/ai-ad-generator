"""
Surveillance en continu des recherches.

Premier passage d'une recherche (« démarrage ») : le bot lit plusieurs pages pour
apprendre le prix du marché, sans alerter pour chaque annonce ancienne ; il envoie
seulement un résumé des meilleures offres déjà en ligne.
Ensuite, à chaque passage : nouvelles annonces et baisses de prix uniquement.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .analysis import (Evaluation, MarketStats, comparable_reason, decide, eligible_reason, evaluate,
                       market_stats, rank)
from .client import Blocked, VintedClient, VintedError
from .config import Settings, Watch
from .models import Listing
from .notify import Notifier, eur
from .store import Store

log = logging.getLogger("vintedbot.monitor")

PRICE_DROP = 0.97  # une baisse d'au moins 3 % fait réévaluer l'annonce
MAX_BACKOFF = 1800


@dataclass
class CheckResult:
    watch: str
    seen: int
    fresh: int
    stats: MarketStats | None
    bootstrap: bool
    deals: list[tuple[Evaluation, str]] = field(default_factory=list)  # triées par score


class Monitor:
    def __init__(self, settings: Settings, client: VintedClient, store: Store, notifiers: Sequence[Notifier],
                 clock: Callable[[], float] = time.time) -> None:
        self.s = settings
        self.client = client
        self.store = store
        self.notifiers = list(notifiers)
        self.clock = clock
        self.stop_event = threading.Event()
        self.backoff = 0

    # ---------------------------------------------------------------- envoi
    def _text(self, message: str) -> None:
        for n in self.notifiers:
            n.text(message)

    def _deal(self, ev: Evaluation, reason: str) -> None:
        for n in self.notifiers:
            n.deal(ev, reason)

    # ------------------------------------------------------------ une recherche
    def fetch(self, watch: Watch, pages: int) -> list[Listing]:
        listings: dict[int, Listing] = {}
        for page in range(1, pages + 1):
            batch = self.client.search(watch.api_params, page=page, per_page=self.s.per_page)
            for listing in batch:
                listings.setdefault(listing.id, listing)
            if len(batch) < self.s.per_page:
                break
        return list(listings.values())

    def check(self, watch: Watch, notify: bool = True) -> CheckResult:
        bootstrap = not self.store.get(f"bootstrapped:{watch.name}")
        listings = self.fetch(watch, self.s.bootstrap_pages if bootstrap else 1)
        now = self.clock()

        known = self.store.known_prices(watch.name, [x.id for x in listings])
        fresh: list[tuple[Listing, float | None]] = []
        for listing in listings:
            old = known.get(listing.id)
            self.store.record(watch.name, listing, comparable_reason(listing, watch) is None, now)
            if old is None:
                fresh.append((listing, None))
            elif listing.price < old * PRICE_DROP:
                fresh.append((listing, old))

        stats = market_stats(self.store.market_prices(watch.name, now - self.s.market_window_days * 86400))
        deals = []
        for listing, old in fresh:
            if eligible_reason(listing, watch):
                continue
            ev = evaluate(listing, watch, stats, self.s.costs, self.s.min_samples, now, old)
            reason = decide(ev, watch, self.s.notify_suspicious)
            if reason and not self.store.was_notified(watch.name, listing.id):
                deals.append((ev, reason))
        deals.sort(key=lambda d: rank(d[0]), reverse=True)
        result = CheckResult(watch.name, len(listings), len(fresh), stats, bootstrap, deals)

        if bootstrap:
            self.store.set(f"bootstrapped:{watch.name}", True)
            if notify:
                self._bootstrap_report(watch, result)
        elif notify:
            for ev, reason in deals[: self.s.max_alerts_per_check]:
                self._deal(ev, reason)
                self.store.mark_notified(watch.name, ev.listing.id)
        return result

    def _bootstrap_report(self, watch: Watch, result: CheckResult) -> None:
        stats = result.stats
        if watch.market_price:
            market = f"valeur fixée à {eur(watch.market_price)}"
        elif stats and stats.count >= self.s.min_samples:
            market = (f"prix médian {eur(stats.median)} (la moitié des annonces entre {eur(stats.p25)} "
                      f"et {eur(stats.p75)}, {stats.count} annonces)")
        else:
            market = (f"apprentissage en cours : {stats.count if stats else 0}/{self.s.min_samples} annonces "
                      "comparables. Les alertes automatiques démarreront ensuite"
                      + (" (l'alerte au prix cible est déjà active)." if watch.target_price else "."))
        self._text(f"🔎 « {watch.name} » : {result.seen} annonces analysées. Marché : {market}")
        if self.s.initial_digest and result.deals:
            self._text(f"📋 Meilleures offres déjà en ligne pour « {watch.name} » :")
            for ev, reason in result.deals[:5]:
                self._deal(ev, reason)
                self.store.mark_notified(watch.name, ev.listing.id)

    # ----------------------------------------------------------------- boucle
    def run(self, max_cycles: int | None = None) -> None:
        watches = [w for w in self.s.watches if w.enabled]
        if not watches:
            raise VintedError("Aucune recherche active dans config.toml")
        self._text(f"🟢 VintedBot démarré : {len(watches)} recherche(s) surveillée(s) — "
                   + ", ".join(w.name for w in watches))
        due = {w.name: 0.0 for w in watches}
        cycles = 0
        while not self.stop_event.is_set():
            for watch in watches:
                if self.stop_event.is_set() or due[watch.name] > self.clock():
                    continue
                if not self._safe_check(watch, due):
                    break
            self.store.prune(self.clock() - 2 * self.s.market_window_days * 86400)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            # attente par tranches d'une seconde : Ctrl+C reste immédiat, y compris sous Windows
            deadline = time.monotonic() + max(1.0, min(due.values()) - self.clock())
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                self.stop_event.wait(min(1.0, deadline - time.monotonic()))
        self._text("🔴 VintedBot arrêté.")

    def _safe_check(self, watch: Watch, due: dict[str, float]) -> bool:
        """Vérifie une recherche ; False si Vinted bloque (tout est alors mis en pause)."""
        interval = self.s.interval(watch) * random.uniform(0.85, 1.15)
        try:
            result = self.check(watch)
        except Blocked as e:
            self.backoff = min(MAX_BACKOFF, max(60, self.backoff * 2))
            log.warning("%s — pause de %d s", e, self.backoff)
            if self.backoff == 60:
                self._text(f"⏸ Vinted limite temporairement l'accès ({e}). Le bot ralentit et réessaie "
                           "automatiquement. Si ça dure, augmentez poll_seconds.")
            for name in due:
                due[name] = self.clock() + self.backoff
            return False
        except (VintedError, OSError) as e:
            log.warning("[%s] erreur : %s (nouvel essai au prochain passage)", watch.name, e)
            due[watch.name] = self.clock() + interval
            return True
        if self.backoff:
            self._text("▶️ Accès à Vinted rétabli, surveillance reprise.")
            self.backoff = 0
        log.info("[%s] %d annonces, %d nouvelles, %d alerte(s)", watch.name, result.seen, result.fresh,
                 min(len(result.deals), self.s.max_alerts_per_check) if not result.bootstrap else 0)
        due[watch.name] = self.clock() + interval
        return True

    def stop(self) -> None:
        self.stop_event.set()
