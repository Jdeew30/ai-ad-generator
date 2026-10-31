"""Outils communs aux tests : annonces au format de l'API Vinted, faux réseau, faux client."""
from __future__ import annotations

import json
import logging

from vintedbot.client import Response, parse_item
from vintedbot.config import Settings, Watch
from vintedbot.models import Listing

logging.disable(logging.CRITICAL)

NOW = 1_800_000_000.0


def api_item(item_id: int, title: str, price: float, status: str = "Très bon état", *,
             legacy_price: bool = False, age_minutes: float = 5, total: float | None = None,
             brand: str = "", visible: bool = True) -> dict:
    """Annonce telle que renvoyée par /svc-catalogue/items (format actuel ou ancien format de prix)."""
    price_field = f"{price:.2f}" if legacy_price else {"amount": f"{price:.2f}", "currency_code": "EUR"}
    total_value = total if total is not None else round(price * 1.05 + 0.7, 2)
    return {
        "id": item_id, "title": title, "price": price_field,
        "total_item_price": f"{total_value:.2f}" if legacy_price else {"amount": f"{total_value:.2f}",
                                                                         "currency_code": "EUR"},
        "service_fee": {"amount": f"{total_value - price:.2f}", "currency_code": "EUR"},
        "currency": "EUR" if legacy_price else None,
        "status": status, "brand_title": brand, "size_title": "", "is_visible": visible,
        "url": f"https://www.vinted.fr/items/{item_id}-annonce", "path": f"/items/{item_id}-annonce",
        "favourite_count": 2,
        "photo": {"url": f"https://images.vinted.net/{item_id}.jpg",
                  "high_resolution": {"id": "x", "timestamp": int(NOW - age_minutes * 60)}},
        "user": {"id": 9, "login": "vendeur"},
    }


def listing(item_id: int, title: str, price: float, **kwargs) -> Listing:
    return parse_item(api_item(item_id, title, price, **kwargs), "www.vinted.fr")


def search_response(items: list[dict]) -> Response:
    return Response(200, {"content-type": "application/json"}, json.dumps({"items": items}).encode())


class FakeTransport:
    """Simule vinted.fr : page d'accueil (cookies + X-Anon-Id) puis réponses de recherche programmées."""

    def __init__(self, search_responses: list[Response] | None = None, home_status: int = 200) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.search_responses = list(search_responses or [])
        self.home_status = home_status
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1

    def get(self, url: str, headers: dict[str, str]) -> Response:
        self.requests.append((url, headers))
        if "/svc-catalogue/" not in url:
            return Response(self.home_status, {"x-anon-id": f"anon-{self.resets}"},
                            b'<!DOCTYPE html><html lang="fr-FR"><head></head></html>',
                            {"access_token_web": f"token{self.resets}", "anon_id": "a"})
        return self.search_responses.pop(0) if self.search_responses else search_response([])


class FakeClient:
    """Remplace VintedClient : renvoie des pages d'annonces programmées."""

    def __init__(self, pages: list[list[Listing]] | None = None) -> None:
        self.pages = list(pages or [])
        self.calls: list[tuple[dict, int, int]] = []
        self.error: Exception | None = None

    def search(self, params: dict, page: int = 1, per_page: int = 48) -> list[Listing]:
        self.calls.append((params, page, per_page))
        if self.error:
            raise self.error
        return self.pages.pop(0) if self.pages else []


def make_watch(**kwargs) -> Watch:
    defaults = {"name": "PS5", "query": "ps5", "preset": "console", "min_price": 100}
    watch = Watch(**(defaults | kwargs))
    watch.prepare("www.vinted.fr")
    return watch


def make_settings(*watches: Watch, **kwargs) -> Settings:
    s = Settings(watches=list(watches), min_samples=10, **kwargs)
    return s


class FakeNotifier:
    def __init__(self) -> None:
        self.deals: list[tuple] = []
        self.texts: list[str] = []

    def deal(self, ev, reason) -> None:
        self.deals.append((ev, reason))

    def text(self, message: str) -> None:
        self.texts.append(message)
