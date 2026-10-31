"""
Client Vinted en lecture seule, sans compte.

Fonctionnement (identique au site web) :
1. une visite de la page d'accueil fournit un cookie de session anonyme
   (`access_token_web`) et un identifiant anonyme (en-tête `X-Anon-Id`) ;
2. la recherche interroge `https://api.<domaine>/svc-catalogue/items`.

Vinted ne publie pas d'API officielle : si le site change, c'est ici qu'il faut
adapter (constantes ci-dessous). `python -m vintedbot doctor` aide au diagnostic.
"""
from __future__ import annotations

import gzip
import json
import logging
import random
import re
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit

from .models import Listing, condition_key

log = logging.getLogger("vintedbot.client")

SEARCH_PATH = "/svc-catalogue/items"
SESSION_COOKIE = "access_token_web"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
LOCALE_BY_TLD = {"fr": "fr-FR", "be": "fr-BE", "lu": "fr-LU", "es": "es-ES", "it": "it-IT", "de": "de-DE",
                 "at": "de-AT", "nl": "nl-NL", "pt": "pt-PT", "pl": "pl-PL", "co.uk": "en-GB", "ie": "en-IE",
                 "com": "en-US", "cz": "cs-CZ", "lt": "lt-LT", "se": "sv-SE", "fi": "fi-FI", "dk": "da-DK"}

# Paramètres d'URL du site (`catalog[]=...`) -> paramètres de l'API (`attribute_ids[catalog]=...`)
URL_ATTRIBUTES = {"catalog": "catalog", "catalog_ids": "catalog", "brand_ids": "brand", "size_ids": "size",
                  "status_ids": "status", "color_ids": "color", "material_ids": "material",
                  "video_game_platform_ids": "video_game_platform"}
URL_SCALARS = {"search_text", "price_from", "price_to", "currency"}


class VintedError(Exception):
    pass


class Blocked(VintedError):
    """Vinted refuse temporairement les requêtes (anti-robot ou trop de requêtes)."""


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    cookies: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class Transport(Protocol):
    def get(self, url: str, headers: dict[str, str]) -> Response: ...
    def reset(self) -> None: ...


class UrllibTransport:
    """HTTP avec la bibliothèque standard (cookies conservés, gzip décodé)."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.reset()

    def reset(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, url: str, headers: dict[str, str]) -> Response:
        request = urllib.request.Request(url, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as resp:
                status, raw_headers, body = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            status, raw_headers, body = e.code, e.headers, e.read()
        encoding = (raw_headers.get("Content-Encoding") or "").lower()
        if encoding == "gzip":
            body = gzip.decompress(body)
        elif encoding == "deflate":
            body = zlib.decompress(body)
        return Response(status, {k.lower(): v for k, v in raw_headers.items()}, body,
                        {c.name: c.value for c in self.jar})


def site_host(domain: str) -> str:
    host = domain.lower().strip().removeprefix("https://").removeprefix("http://").strip("/")
    if not re.fullmatch(r"(www\.)?vinted\.[a-z.]{2,6}", host):
        raise ValueError(f"Domaine Vinted invalide : « {domain} » (ex. www.vinted.fr)")
    return host if host.startswith("www.") else f"www.{host}"


def parse_search_url(url: str) -> tuple[str, dict[str, str]]:
    """
    Transforme une URL de recherche copiée depuis le site Vinted (avec ses filtres)
    en (domaine, paramètres d'API). Le tri est imposé par le bot (plus récentes d'abord).
    """
    parts = urlsplit(url.strip())
    host = site_host(parts.netloc)
    params: dict[str, str] = {}
    attributes: dict[str, list[str]] = {}
    for key, value in parse_qsl(parts.query):
        name = key.removesuffix("[]")
        if name in URL_SCALARS:
            params[name] = value
        elif name in URL_ATTRIBUTES:
            attributes.setdefault(URL_ATTRIBUTES[name], []).append(value)
        elif name.endswith("_ids"):
            attributes.setdefault(name[:-4], []).append(value)
    for attribute, values in attributes.items():
        params[f"attribute_ids[{attribute}]"] = ",".join(values)
    return host, params


def _amount(value: Any) -> float | None:
    try:
        if isinstance(value, dict):
            return float(value["amount"])
        if isinstance(value, (int, float, str)) and value != "":
            return float(value)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def parse_item(data: dict[str, Any], host: str) -> Listing | None:
    """Convertit une annonce de l'API ; None si elle est inexploitable (sans prix, masquée...)."""
    item_id, price = data.get("id"), _amount(data.get("price"))
    if not item_id or price is None or data.get("is_visible") is False:
        return None
    raw_price = data.get("price")
    currency = (raw_price.get("currency_code") if isinstance(raw_price, dict) else None) \
        or data.get("currency") or "EUR"
    photo = data.get("photo") or (data.get("photos") or [None])[0] or {}
    created = (photo.get("high_resolution") or {}).get("timestamp")
    user = data.get("user") or {}
    path = data.get("path") or f"/items/{item_id}"
    return Listing(
        id=int(item_id),
        title=str(data.get("title") or ""),
        price=price,
        currency=str(currency),
        total_price=_amount(data.get("total_item_price")),
        condition=condition_key(data.get("status"), data.get("status_id")),
        brand=str(data.get("brand_title") or ""),
        size=str(data.get("size_title") or ""),
        url=str(data.get("url") or f"https://{host}{path}"),
        photo_url=str(photo.get("url") or photo.get("full_size_url") or ""),
        seller=str(user.get("login") or ""),
        favourites=int(data.get("favourite_count") or 0),
        created_at=int(created) if created else None,
    )


class VintedClient:
    def __init__(self, domain: str = "www.vinted.fr", transport: Transport | None = None,
                 min_interval: float = 3.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.host = site_host(domain)
        self.api_host = "api." + self.host.removeprefix("www.")
        self.transport = transport or UrllibTransport()
        self.min_interval = min_interval
        self.clock, self.sleep = clock, sleep
        self.cookies: dict[str, str] = {}
        self.anon_id: str | None = None
        self.locale = LOCALE_BY_TLD.get(self.host.split("vinted.", 1)[1], "fr-FR")
        self._last_request = float("-inf")

    # ------------------------------------------------------------------ HTTP
    def _throttle(self) -> None:
        """Espace les requêtes (avec un peu d'aléa) pour rester discret et poli."""
        wait = self._last_request + self.min_interval * random.uniform(1.0, 1.3) - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._last_request = self.clock()

    def open_session(self) -> None:
        self.transport.reset()
        self._throttle()
        resp = self.transport.get(f"https://{self.host}/", {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": f"{self.locale},en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none",
        })
        if resp.status in (403, 429):
            raise Blocked(f"Vinted refuse l'accès à la page d'accueil (HTTP {resp.status})")
        if resp.status != 200:
            raise VintedError(f"Page d'accueil Vinted inaccessible (HTTP {resp.status})")
        self.cookies = resp.cookies
        self.anon_id = (resp.headers.get("x-anon-id") or "").strip() or None
        match = re.search(r'<html[^>]*\blang="([A-Za-z-]+)"', resp.text[:5000])
        if match:
            self.locale = match.group(1)
        if SESSION_COOKIE not in self.cookies:
            log.warning("Cookie %s absent : Vinted a peut-être changé son fonctionnement", SESSION_COOKIE)

    def _api_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": f"{self.locale},en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Origin": f"https://{self.host}", "Referer": f"https://{self.host}/",
            "Locale": self.locale, "Platform": "web", "X-Next-App": "marketplace-web",
            "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site",
        }
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if self.anon_id:
            headers["X-Anon-Id"] = self.anon_id
        return headers

    # -------------------------------------------------------------- recherche
    def search(self, params: dict[str, str], page: int = 1, per_page: int = 48) -> list[Listing]:
        """Annonces les plus récentes correspondant aux paramètres."""
        query = dict(params) | {"order": "newest_first", "page": str(page), "per_page": str(per_page)}
        url = f"https://{self.api_host}{SEARCH_PATH}?{urlencode(query)}"
        if not self.cookies:
            self.open_session()
        for attempt in range(2):
            self._throttle()
            resp = self.transport.get(url, self._api_headers())
            if resp.status == 401 and attempt == 0:
                log.info("Session Vinted expirée : renouvellement")
                self.open_session()
                continue
            if resp.status in (403, 429):
                raise Blocked(f"Vinted limite les requêtes (HTTP {resp.status})")
            if resp.status != 200:
                raise VintedError(f"Recherche Vinted en échec (HTTP {resp.status})")
            try:
                items = json.loads(resp.body)["items"]
            except (ValueError, KeyError, TypeError) as e:
                raise VintedError(f"Réponse Vinted inattendue : {e}") from None
            return [listing for item in items if (listing := parse_item(item, self.host))]
        raise VintedError("Session Vinted refusée après renouvellement (HTTP 401)")
