"""
Configuration : `config.toml` (recherches, coûts, notifications) + `.env` (secrets).

Lecture stricte : une clé inconnue est une erreur, pour qu'une faute de frappe
ne désactive pas un filtre sans que vous le sachiez.
"""
from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .client import parse_search_url, site_host
from .models import CONDITIONS
from .presets import PRESETS, Preset


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Costs:
    shipping: float = 4.5          # port payé à l'achat (remplacé par celui du profil ou de la recherche)
    buyer_fee_fixed: float = 0.70  # protection acheteur Vinted : partie fixe...
    buyer_fee_pct: float = 0.05    # ...et pourcentage du prix (utilisés si Vinted ne donne pas le total)
    resale_haircut: float = 0.10   # revente réaliste = valeur du marché - 10 % (annonces ≠ ventes conclues)
    resale_costs: float = 0.0      # vos frais de revente par article (emballage, commission...)


@dataclass
class Watch:
    """Une recherche surveillée."""

    name: str
    query: str = ""
    url: str = ""
    preset: str = "general"
    include: list[str] = field(default_factory=list)     # mots obligatoires (tous)
    exclude: list[str] = field(default_factory=list)     # mots interdits (un seul suffit)
    red_flags: list[str] = field(default_factory=list)   # mots signalés en plus de ceux du profil
    conditions: list[str] = field(default_factory=list)  # états acceptés (vide = tous)
    min_price: float | None = None
    max_price: float | None = None
    market_price: float | None = None   # valeur de marché fixée à la main (sinon apprise)
    target_price: float | None = None   # alerte dès que le prix est inférieur ou égal
    min_discount: float = 0.25          # au moins -25 % sous le marché...
    min_profit: float = 15.0            # ...et au moins 15 € de marge estimée
    suspicious_discount: float = 0.65   # au-delà de -65 % : « trop beau pour être vrai »
    allow_defective: bool = False       # alerter aussi sur les articles HS (pour les réparateurs)
    shipping: float | None = None
    poll_seconds: int | None = None
    enabled: bool = True
    host: str = ""
    api_params: dict[str, str] = field(default_factory=dict)

    @property
    def profile(self) -> Preset:
        return PRESETS[self.preset]

    def shipping_cost(self, costs: Costs) -> float:
        for value in (self.shipping, self.profile.shipping, costs.shipping):
            if value is not None:
                return value
        return 0.0

    def prepare(self, default_host: str) -> None:
        """Construit les paramètres d'API et vérifie la cohérence de la recherche."""
        if self.preset not in PRESETS:
            raise ConfigError(f"[{self.name}] profil « {self.preset} » inconnu ({', '.join(PRESETS)})")
        bad = set(self.conditions) - set(CONDITIONS)
        if bad:
            raise ConfigError(f"[{self.name}] état(s) inconnu(s) : {', '.join(bad)} ({', '.join(CONDITIONS)})")
        if self.url:
            try:
                self.host, params = parse_search_url(self.url)
            except ValueError as e:
                raise ConfigError(f"[{self.name}] {e}") from None
        else:
            self.host, params = default_host, {}
        if self.query:
            params["search_text"] = self.query
        # Le prix maximum n'est PAS envoyé à Vinted : le bot doit voir toutes les annonces
        # pour estimer correctement le prix du marché. Il est appliqué seulement aux alertes.
        url_max, url_min = params.pop("price_to", None), params.pop("price_from", None)
        if self.max_price is None and url_max:
            self.max_price = float(url_max)
        if self.min_price is None and url_min:
            self.min_price = float(url_min)
        if self.min_price:
            params["price_from"] = f"{self.min_price:g}"
        if not params.get("search_text") and not any(k.startswith("attribute_ids") for k in params):
            raise ConfigError(f"[{self.name}] indiquez `query` ou une `url` de recherche Vinted")
        for name in ("min_discount", "suspicious_discount"):
            if not 0 <= getattr(self, name) < 1:
                raise ConfigError(f"[{self.name}] {name} doit être compris entre 0 et 1 (ex. 0.25 = -25 %)")
        if self.min_price and self.max_price and self.min_price > self.max_price:
            raise ConfigError(f"[{self.name}] min_price est supérieur à max_price")
        self.api_params = params


@dataclass
class Settings:
    domain: str = "www.vinted.fr"
    poll_seconds: int = 90
    per_page: int = 48
    bootstrap_pages: int = 3
    min_interval: float = 3.0
    data_dir: Path = Path("data")
    min_samples: int = 15
    market_window_days: int = 30
    notify_suspicious: bool = True
    initial_digest: bool = True
    max_alerts_per_check: int = 8
    costs: Costs = field(default_factory=Costs)
    telegram_enabled: bool = False
    telegram_token: str = ""
    telegram_chat_id: str = ""
    discord_enabled: bool = False
    discord_webhook: str = ""
    watches: list[Watch] = field(default_factory=list)

    def interval(self, watch: Watch) -> int:
        return watch.poll_seconds or self.poll_seconds

    def validate(self, for_notifications: bool = True) -> None:
        try:
            self.domain = site_host(self.domain)
        except ValueError as e:
            raise ConfigError(str(e)) from None
        if self.poll_seconds < 30 or self.min_interval < 1:
            raise ConfigError("poll_seconds doit être >= 30 et min_interval >= 1 (restez discret)")
        if not 1 <= self.per_page <= 96 or not 1 <= self.bootstrap_pages <= 10:
            raise ConfigError("per_page doit être entre 1 et 96, bootstrap_pages entre 1 et 10")
        names = [w.name for w in self.watches]
        if len(set(names)) != len(names):
            raise ConfigError("Deux recherches portent le même nom")
        for watch in self.watches:
            watch.prepare(self.domain)
            if watch.poll_seconds is not None and watch.poll_seconds < 30:
                raise ConfigError(f"[{watch.name}] poll_seconds doit être >= 30")
        if for_notifications:
            if self.telegram_enabled and not (self.telegram_token and self.telegram_chat_id):
                raise ConfigError("Telegram activé : définissez TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans .env")
            if self.discord_enabled and not self.discord_webhook.startswith("https://"):
                raise ConfigError("Discord activé : définissez DISCORD_WEBHOOK_URL dans .env")


_GENERAL = {f.name for f in fields(Settings)} - {"costs", "telegram_enabled", "telegram_token", "telegram_chat_id",
                                                  "discord_enabled", "discord_webhook", "watches"}
_WATCH = {f.name for f in fields(Watch)} - {"host", "api_params"}
_ENV = {"TELEGRAM_BOT_TOKEN": "telegram_token", "TELEGRAM_CHAT_ID": "telegram_chat_id",
        "DISCORD_WEBHOOK_URL": "discord_webhook"}


def _check_keys(section: str, values: dict[str, Any], allowed: set[str]) -> None:
    bad = set(values) - allowed
    if bad:
        raise ConfigError(f"[{section}] clé(s) inconnue(s) : {', '.join(sorted(bad))}. "
                          f"Clés possibles : {', '.join(sorted(allowed))}")


def load_settings(path: str | Path | None = "config.toml", env: Mapping[str, str] | None = None,
                  validate: bool = True, for_notifications: bool = True) -> Settings:
    env = os.environ if env is None else env
    s = Settings()
    if path is not None and Path(path).exists():
        try:
            raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path} : syntaxe TOML invalide ({e})") from None
        _check_keys("racine", raw, {"general", "costs", "telegram", "discord", "watch"})
        general = raw.get("general", {})
        _check_keys("general", general, _GENERAL)
        for key, value in general.items():
            setattr(s, key, value)
        costs = raw.get("costs", {})
        _check_keys("costs", costs, {f.name for f in fields(Costs)})
        s.costs = Costs(**costs)
        for section in ("telegram", "discord"):
            values = raw.get(section, {})
            _check_keys(section, values, {"enabled"})
            setattr(s, f"{section}_enabled", bool(values.get("enabled", False)))
        for i, w in enumerate(raw.get("watch", []), 1):
            if "name" not in w:
                raise ConfigError(f"[[watch]] n°{i} : `name` est obligatoire")
            _check_keys(f"watch « {w['name']} »", w, _WATCH)
            s.watches.append(Watch(**w))
    for var, attr in _ENV.items():
        if env.get(var):
            setattr(s, attr, env[var])
    s.data_dir = Path(s.data_dir)
    if validate:
        s.validate(for_notifications)
    return s


def load_dotenv(path: str | Path = ".env") -> None:
    """Charge un fichier .env sans écraser les variables déjà définies."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)
