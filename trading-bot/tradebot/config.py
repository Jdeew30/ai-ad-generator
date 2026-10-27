"""
Configuration : fichier `config.toml` pour les réglages, `.env` pour les secrets.

La lecture est stricte : une clé inconnue (faute de frappe) est une erreur,
pour éviter qu'un garde-fou mal orthographié soit silencieusement ignoré.
"""
from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .data import timeframe_ms
from .exchange import split_symbol
from .risk import RiskConfig
from .strategies import Strategy, build_strategy


class ConfigError(ValueError):
    pass


@dataclass
class Settings:
    brand: str = "TradeBot"
    data_dir: Path = Path("data")
    exchange: str = "binance"
    mode: str = "paper"  # paper | live
    sandbox: bool = True
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    timeframe: str = "1h"
    poll_seconds: int = 60
    paper_capital: float = 10_000.0
    slippage_pct: float = 0.0005
    strategy: str = "ema_rsi"
    strategy_params: dict[str, Any] = field(default_factory=dict)
    risk: RiskConfig = field(default_factory=RiskConfig)
    telegram_enabled: bool = False
    telegram_token: str = ""
    telegram_chat_id: str = ""
    dashboard_enabled: bool = True
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080
    dashboard_password: str = ""
    api_key: str = ""
    api_secret: str = ""
    api_password: str = ""

    @property
    def quote(self) -> str:
        return split_symbol(self.symbols[0])[1]

    @property
    def mode_label(self) -> str:
        if self.mode == "paper":
            return "PAPER"
        return "SANDBOX" if self.sandbox else "LIVE"

    @property
    def db_path(self) -> Path:
        name = "paper" if self.mode == "paper" else f"{self.mode_label.lower()}-{self.exchange}"
        return self.data_dir / f"{name}.sqlite"

    def build_strategy(self) -> Strategy:
        return build_strategy(self.strategy, self.strategy_params)

    def validate(self, for_trading: bool = True) -> None:
        """`for_trading=False` (backtest) : n'exige pas les secrets du mode live ni de Telegram."""
        if self.mode not in ("paper", "live"):
            raise ConfigError("exchange.mode doit valoir « paper » ou « live »")
        if not self.symbols:
            raise ConfigError("trading.symbols : indiquez au moins une paire")
        try:
            quotes = {split_symbol(s)[1] for s in self.symbols}
            timeframe_ms(self.timeframe)
            self.build_strategy()
        except ValueError as e:
            raise ConfigError(str(e)) from None
        if len(quotes) > 1:
            raise ConfigError(f"Toutes les paires doivent avoir la même devise de cotation (trouvé : {', '.join(quotes)})")
        if len(set(self.symbols)) != len(self.symbols):
            raise ConfigError("trading.symbols contient des doublons")
        if self.poll_seconds < 5:
            raise ConfigError("trading.poll_seconds doit être >= 5")
        if self.paper_capital <= 0 or not 0 <= self.slippage_pct < 0.05:
            raise ConfigError("trading.paper_capital doit être > 0 et slippage_pct entre 0 et 0.05")
        if not 0 < int(self.dashboard_port) < 65536:
            raise ConfigError("dashboard.port invalide")
        if not for_trading:
            return
        if self.telegram_enabled and not (self.telegram_token and self.telegram_chat_id):
            raise ConfigError("Telegram activé : définissez TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans .env")
        if self.mode == "live" and not (self.api_key and self.api_secret):
            raise ConfigError("Mode live : définissez EXCHANGE_API_KEY et EXCHANGE_API_SECRET dans .env")


_SECTIONS: dict[str, dict[str, str]] = {
    # section TOML -> {clé TOML : attribut de Settings}
    "general": {"brand": "brand", "data_dir": "data_dir"},
    "exchange": {"name": "exchange", "mode": "mode", "sandbox": "sandbox"},
    "trading": {"symbols": "symbols", "timeframe": "timeframe", "poll_seconds": "poll_seconds",
                "paper_capital": "paper_capital", "slippage_pct": "slippage_pct"},
    "telegram": {"enabled": "telegram_enabled"},
    "dashboard": {"enabled": "dashboard_enabled", "host": "dashboard_host", "port": "dashboard_port"},
}

_ENV = {
    "EXCHANGE_API_KEY": "api_key",
    "EXCHANGE_API_SECRET": "api_secret",
    "EXCHANGE_API_PASSWORD": "api_password",
    "TELEGRAM_BOT_TOKEN": "telegram_token",
    "TELEGRAM_CHAT_ID": "telegram_chat_id",
    "DASHBOARD_PASSWORD": "dashboard_password",
    "DASHBOARD_HOST": "dashboard_host",
}


def load_settings(path: str | Path | None = "config.toml", env: Mapping[str, str] | None = None,
                  validate: bool = True, for_trading: bool = True) -> Settings:
    env = os.environ if env is None else env
    settings = Settings()
    if path is not None and Path(path).exists():
        try:
            raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path} : syntaxe TOML invalide ({e})") from None
        _apply(settings, raw)
    for var, attr in _ENV.items():
        if env.get(var):
            setattr(settings, attr, env[var])
    settings.data_dir = Path(settings.data_dir)
    if validate:
        settings.validate(for_trading)
    return settings


def _apply(settings: Settings, raw: dict[str, Any]) -> None:
    unknown = set(raw) - set(_SECTIONS) - {"strategy", "risk"}
    if unknown:
        raise ConfigError(f"Section(s) inconnue(s) : {', '.join(sorted(unknown))}")
    for section, mapping in _SECTIONS.items():
        values = raw.get(section, {})
        bad = set(values) - set(mapping)
        if bad:
            raise ConfigError(f"[{section}] clé(s) inconnue(s) : {', '.join(sorted(bad))}")
        for key, value in values.items():
            setattr(settings, mapping[key], value)

    strategy = dict(raw.get("strategy", {}))
    bad = set(strategy) - {"name", "params"}
    if bad:
        raise ConfigError(f"[strategy] clé(s) inconnue(s) : {', '.join(sorted(bad))}")
    settings.strategy = strategy.get("name", settings.strategy)
    settings.strategy_params = dict(strategy.get("params", {}))

    try:
        settings.risk = RiskConfig.from_dict(raw.get("risk", {}))
    except (TypeError, ValueError) as e:
        raise ConfigError(str(e)) from None


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
