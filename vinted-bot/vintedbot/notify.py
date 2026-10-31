"""Notifications : console, Telegram (photo + bouton) et Discord (webhook)."""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

from .analysis import Evaluation
from .models import condition_label

log = logging.getLogger("vintedbot.notify")

COLORS = {"🔥": 0xF85149, "✅": 0x3FB950, "⚠️": 0xD29922, "👀": 0x58A6FF}


def eur(value: float) -> str:
    """1234.5 -> « 1 234,50 € »."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " €"


def age_text(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    if minutes < 1:
        return "à l'instant"
    if minutes < 60:
        return f"il y a {minutes:.0f} min"
    if minutes < 48 * 60:
        return f"il y a {minutes / 60:.0f} h"
    return f"il y a {minutes / 1440:.0f} jours"


def deal_lines(ev: Evaluation, reason: str) -> list[str]:
    """Message d'alerte, une information par ligne."""
    item = ev.listing
    lines = [f"{ev.label} · {ev.watch} · {ev.score}/100", item.title]
    price = f"💶 {eur(item.price)}"
    if ev.previous_price:
        price += f" (au lieu de {eur(ev.previous_price)} : baisse de prix)"
    details = [condition_label(item.condition)] + [x for x in (item.brand, item.size) if x]
    lines.append(f"{price} · {' · '.join(details)}")
    if ev.reference:
        lines.append(f"📊 Valeur ≈ {eur(ev.reference)} ({ev.reference_source}) → {-ev.discount * 100:+.0f} %")
        lines.append(f"💰 Marge estimée ≈ {ev.profit:+.0f} € (revente ≈ {eur(ev.resale)}, "
                     f"coût total ≈ {eur(ev.cost)} avec protection et port)")
    else:
        lines.append(f"📊 Marché : {ev.reference_source} · coût total ≈ {eur(ev.cost)}")
    meta = [x for x in (f"⏱ {age_text(ev.age_minutes)}" if ev.age_minutes is not None else None,
                        f"❤️ {item.favourites} favori(s)" if item.favourites else None,
                        f"👤 {item.seller}" if item.seller else None) if x]
    if meta:
        lines.append(" · ".join(meta))
    lines.extend(f"⚠️ {flag}" for flag in ev.flags)
    lines.append(f"🎯 {reason}")
    return lines


class Notifier(Protocol):
    def deal(self, ev: Evaluation, reason: str) -> None: ...
    def text(self, message: str) -> None: ...


class ConsoleNotifier:
    def __init__(self, printer: Callable[[str], None] = print) -> None:
        self.print = printer

    def deal(self, ev: Evaluation, reason: str) -> None:
        self.print("\n".join([*deal_lines(ev, reason), f"🔗 {ev.listing.url}", ""]))

    def text(self, message: str) -> None:
        self.print(message)


class TelegramNotifier:
    API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, chat_id: str, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.token, self.chat_id, self._open = token, str(chat_id), opener

    def _call(self, method: str, params: dict[str, Any]) -> None:
        data = urllib.parse.urlencode(params).encode()
        with self._open(self.API.format(token=self.token, method=method), data=data, timeout=20) as resp:
            resp.read()

    def deal(self, ev: Evaluation, reason: str) -> None:
        text = "\n".join(deal_lines(ev, reason))
        button = json.dumps({"inline_keyboard": [[{"text": "Voir l'annonce sur Vinted", "url": ev.listing.url}]]})
        try:
            if ev.listing.photo_url:
                try:
                    self._call("sendPhoto", {"chat_id": self.chat_id, "photo": ev.listing.photo_url,
                                             "caption": text[:1020], "reply_markup": button})
                    return
                except Exception as e:  # photo refusée par Telegram : on envoie le texte seul
                    log.info("Envoi de la photo impossible (%s), envoi du texte", e)
            self._call("sendMessage", {"chat_id": self.chat_id, "text": text[:4000], "reply_markup": button,
                                       "disable_web_page_preview": "true"})
        except Exception as e:
            log.warning("Échec d'envoi Telegram : %s", e)

    def text(self, message: str) -> None:
        try:
            self._call("sendMessage", {"chat_id": self.chat_id, "text": message[:4000],
                                       "disable_web_page_preview": "true"})
        except Exception as e:
            log.warning("Échec d'envoi Telegram : %s", e)


class DiscordNotifier:
    def __init__(self, webhook: str, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.webhook, self._open = webhook, opener

    def _post(self, payload: dict[str, Any]) -> None:
        request = urllib.request.Request(
            self.webhook, data=json.dumps(payload).encode(), method="POST",
            # Discord refuse l'agent par défaut de Python
            headers={"Content-Type": "application/json", "User-Agent": "VintedBot (alertes, 1.0)"})
        try:
            with self._open(request, timeout=20) as resp:
                resp.read()
        except Exception as e:
            log.warning("Échec d'envoi Discord : %s", e)

    def deal(self, ev: Evaluation, reason: str) -> None:
        lines = deal_lines(ev, reason)
        embed: dict[str, Any] = {
            "title": f"{lines[0]}"[:256], "url": ev.listing.url,
            "description": "\n".join(lines[1:])[:4000],
            "color": next((c for k, c in COLORS.items() if ev.label.startswith(k)), 0x58A6FF),
        }
        if ev.listing.photo_url:
            embed["image"] = {"url": ev.listing.photo_url}
        self._post({"username": "VintedBot", "embeds": [embed]})

    def text(self, message: str) -> None:
        self._post({"username": "VintedBot", "content": message[:2000]})
