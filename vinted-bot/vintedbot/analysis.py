"""
Analyse d'une annonce : vaut-elle le coup pour la revente ?

1. Filtres : mots interdits ou obligatoires, prix, état.
2. Prix du marché : fixé à la main, ou appris (médiane des annonces comparables
   vues sur les 30 derniers jours, valeurs aberrantes écartées).
3. Ajustement selon l'état (neuf avec étiquette > très bon > bon > satisfaisant).
4. Coût réel d'achat : prix + protection acheteur + port.
5. Revente réaliste : valeur de marché moins une décote (un prix affiché n'est
   pas un prix de vente), moins vos frais.
6. Score sur 100 : décote, rentabilité, fraîcheur, état, signaux d'alerte.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .config import Costs, Watch
from .models import Listing, condition_factor, condition_label, normalize
from .presets import COUNTERFEIT

PEPITE, BONNE_AFFAIRE = 75, 55


def find(text: str, phrases: Iterable[str]) -> list[str]:
    """Expressions présentes dans `text` (déjà normalisé), en mots entiers."""
    found = []
    for phrase in phrases:
        p = normalize(phrase)
        if p and re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", text):
            found.append(phrase)
    return found


# ------------------------------------------------------------------ marché
@dataclass(frozen=True)
class MarketStats:
    count: int
    median: float
    p25: float
    p75: float


def quantile(values: Sequence[float], q: float) -> float:
    """Quantile par interpolation linéaire sur une liste triée."""
    pos = (len(values) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def market_stats(prices: Iterable[float]) -> MarketStats | None:
    values = sorted(p for p in prices if p > 0)
    if not values:
        return None
    if len(values) >= 8:  # on écarte les valeurs aberrantes (accessoires, lots, prix fantaisistes)
        q1, q3 = quantile(values, 0.25), quantile(values, 0.75)
        iqr = q3 - q1
        values = [v for v in values if q1 - 1.5 * iqr <= v <= q3 + 1.5 * iqr] or values
    return MarketStats(len(values), quantile(values, 0.5), quantile(values, 0.25), quantile(values, 0.75))


# ----------------------------------------------------------------- filtres
def _filter(listing: Listing, watch: Watch, for_market: bool) -> str | None:
    if watch.min_price is not None and listing.price < watch.min_price:
        return f"prix sous {watch.min_price:g}"
    missing = [w for w in watch.include if not find(listing.text, [w])]
    if missing:
        return f"mot(s) obligatoire(s) absent(s) : {', '.join(missing)}"
    banned = find(listing.text, [*watch.exclude, *watch.profile.exclude])
    if banned:
        return f"mot exclu : {banned[0]}"
    broken = find(listing.text, watch.profile.defective)
    if broken and (for_market or not watch.allow_defective):
        return f"article déclaré défectueux (« {broken[0]} »)"
    return None


def comparable_reason(listing: Listing, watch: Watch) -> str | None:
    """Pourquoi l'annonce n'est pas comparable (elle ne compte pas dans le prix du marché)."""
    return _filter(listing, watch, for_market=True)


def eligible_reason(listing: Listing, watch: Watch) -> str | None:
    """Pourquoi l'annonce ne peut pas donner lieu à une alerte (None = éligible)."""
    reason = _filter(listing, watch, for_market=False)
    if reason:
        return reason
    if watch.max_price is not None and listing.price > watch.max_price:
        return f"prix au-dessus de {watch.max_price:g}"
    if watch.conditions and listing.condition not in watch.conditions:
        return f"état non souhaité ({condition_label(listing.condition)})"
    return None


# --------------------------------------------------------------- évaluation
@dataclass(frozen=True)
class Evaluation:
    listing: Listing
    watch: str
    reference: float | None   # valeur estimée de l'article dans son état
    reference_source: str
    discount: float | None    # 0.30 = 30 % sous la valeur
    resale: float | None
    cost: float               # coût total d'achat (protection + port compris)
    profit: float | None
    roi: float | None
    score: int
    flags: tuple[str, ...]
    suspicious: bool
    age_minutes: float | None
    previous_price: float | None = None  # si l'alerte vient d'une baisse de prix

    @property
    def label(self) -> str:
        if self.suspicious:
            return "⚠️ À VÉRIFIER"
        if self.score >= PEPITE:
            return "🔥 PÉPITE"
        if self.score >= BONNE_AFFAIRE:
            return "✅ BONNE AFFAIRE"
        return "👀 À SURVEILLER"


def buy_cost(listing: Listing, costs: Costs, shipping: float) -> float:
    """Prix + protection acheteur + port."""
    if listing.total_price is not None and listing.total_price >= listing.price:
        with_fee = listing.total_price
    else:
        with_fee = listing.price * (1 + costs.buyer_fee_pct) + costs.buyer_fee_fixed
    return with_fee + shipping


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def evaluate(listing: Listing, watch: Watch, stats: MarketStats | None, costs: Costs, min_samples: int,
             now: float, previous_price: float | None = None) -> Evaluation:
    flags: list[str] = []
    if watch.market_price:
        base, source = watch.market_price, "valeur fixée"
    elif stats and stats.count >= min_samples:
        base, source = stats.median, f"médiane de {stats.count} annonces"
    else:
        base, source = None, (f"apprentissage ({stats.count if stats else 0}/{min_samples} annonces)")

    reference = base * condition_factor(listing.condition) if base else None
    cost = buy_cost(listing, costs, watch.shipping_cost(costs))
    discount = resale = profit = roi = None
    if reference:
        discount = 1 - listing.price / reference
        resale = reference * (1 - costs.resale_haircut)
        profit = resale - cost - costs.resale_costs
        roi = profit / cost if cost else None

    counterfeit = find(listing.text, COUNTERFEIT)
    red = [w for w in find(listing.text, [*watch.profile.red_flags, *watch.red_flags]) if w not in counterfeit]
    if counterfeit:
        flags.append(f"contrefaçon possible (« {counterfeit[0]} »)")
    if red:
        flags.append("à vérifier : " + ", ".join(f"« {w} »" for w in red[:4]))
    if listing.condition == "satisfaisant":
        flags.append("état satisfaisant : défauts probables")
    broken = find(listing.text, watch.profile.defective)
    if broken:
        flags.append(f"article déclaré défectueux (« {broken[0]} ») : la marge suppose une réparation réussie")
    too_good = discount is not None and discount >= watch.suspicious_discount
    if too_good:
        flags.append(f"prix anormalement bas ({discount * 100:.0f} % sous le marché) : arnaque, article incomplet "
                     "ou défectueux ? Payez uniquement via Vinted et demandez des photos ou une vidéo.")
    suspicious = bool(counterfeit) or bool(broken) or too_good

    age = (now - listing.created_at) / 60 if listing.created_at and listing.created_at <= now else None
    score = 0.0
    if discount is not None:
        score += 60 * _clamp(discount / 0.5)
    if roi is not None:
        score += 25 * _clamp(roi / 0.6)
    if age is not None:
        score += 10 if age <= 15 else 5 if age <= 120 else 0
    if listing.condition in ("neuf", "neuf_etiquette"):
        score += 5
    score -= 15 * len(red) + (30 if suspicious else 0)
    return Evaluation(listing, watch.name, reference, source, discount, resale, cost, profit, roi,
                      round(max(0.0, min(100.0, score))), tuple(flags), suspicious, age, previous_price)


def rank(ev: Evaluation) -> tuple[bool, int]:
    """Clé de tri (ordre décroissant) : les vraies affaires d'abord, les offres suspectes ensuite."""
    return (not ev.suspicious, ev.score)


def decide(ev: Evaluation, watch: Watch, notify_suspicious: bool = True) -> str | None:
    """Raison de l'alerte, ou None s'il ne faut pas alerter."""
    if ev.suspicious and not notify_suspicious:
        return None
    if watch.target_price is not None and ev.listing.price <= watch.target_price:
        return f"prix ≤ votre prix cible de {watch.target_price:g} €"
    if ev.discount is None or ev.profit is None:
        return None
    if ev.discount >= watch.min_discount and ev.profit >= watch.min_profit:
        return f"{ev.discount * 100:.0f} % sous le marché et ≈ {ev.profit:.0f} € de marge"
    return None
