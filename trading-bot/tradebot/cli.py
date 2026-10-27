"""Interface en ligne de commande : python -m tradebot <commande>."""
from __future__ import annotations

import argparse
import json
import logging
import secrets
import shutil
import signal
import sys
from dataclasses import fields
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import __version__
from .backtest import run_backtest
from .config import ConfigError, Settings, load_dotenv, load_settings
from .data import BinancePublic, CcxtData, MarketData, fetch_history, load_csv, save_csv, synthetic
from .exchange import CcxtBroker, PaperBroker, build_ccxt
from .models import Candle
from .notify import NullNotifier, TelegramNotifier
from .optimize import METRICS, optimize
from .report import fmt_time, render_report
from .stats import Stats
from .store import Store
from .strategies import STRATEGIES

PROJECT_DIR = Path(__file__).resolve().parent.parent


def parse_param(text: str) -> tuple[str, Any]:
    key, sep, raw = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"« {text} » : format attendu nom=valeur")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return key.strip(), value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tradebot", description="Bot de trading crypto auto-hébergé")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    base = argparse.ArgumentParser(add_help=False)
    base.add_argument("--config", default="config.toml", help="fichier de configuration (défaut : config.toml)")
    base.add_argument("--env", default=".env", help="fichier de secrets (défaut : .env)")

    market = argparse.ArgumentParser(add_help=False, parents=[base])
    market.add_argument("--symbol", help="paire, ex. BTC/USDT (défaut : celles de la config)")
    market.add_argument("--timeframe", help="unité de temps, ex. 15m, 1h, 4h, 1d")
    market.add_argument("--strategy", choices=sorted(STRATEGIES), help="stratégie à utiliser")
    market.add_argument("--param", action="append", type=parse_param, default=[], metavar="NOM=VALEUR",
                        help="paramètre de stratégie (répétable), ex. --param fast=10")
    market.add_argument("--days", type=float, default=365, help="historique en jours (défaut : 365)")
    market.add_argument("--source", choices=["exchange", "csv", "synthetic"], default="exchange",
                        help="données : exchange (défaut), csv, ou synthetic (fictives, hors ligne)")
    market.add_argument("--csv", help="fichier CSV (avec --source csv)")
    market.add_argument("--capital", type=float, default=10_000.0)

    sub.add_parser("init", help="crée config.toml et .env à partir des modèles")
    sub.add_parser("strategies", help="liste les stratégies et leurs paramètres")

    bt = sub.add_parser("backtest", parents=[market], help="teste la stratégie sur l'historique")
    bt.add_argument("--report", help="écrit un rapport HTML (ex. rapport.html)")
    bt.add_argument("--trades", action="store_true", help="affiche le détail des trades")

    opt = sub.add_parser("optimize", parents=[market], help="cherche les meilleurs paramètres (avec test hors échantillon)")
    opt.add_argument("--metric", choices=sorted(METRICS), default="sharpe")
    opt.add_argument("--top", type=int, default=5)
    opt.add_argument("--train-ratio", type=float, default=0.7)

    dl = sub.add_parser("download", parents=[market], help="télécharge l'historique en CSV")
    dl.add_argument("--out", required=True, help="fichier CSV de sortie")

    sub.add_parser("run", parents=[base], help="lance le bot (paper ou live selon la config)")
    sub.add_parser("reset-paper", parents=[base], help="remet à zéro le portefeuille simulé")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    handlers = {
        "init": cmd_init, "strategies": cmd_strategies, "backtest": cmd_backtest,
        "optimize": cmd_optimize, "download": cmd_download, "run": cmd_run, "reset-paper": cmd_reset_paper,
    }
    try:
        return handlers[args.command](args)
    except ConfigError as e:
        print(f"Erreur de configuration : {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


# ---------------------------------------------------------------- utilitaires
def load(args: argparse.Namespace, for_trading: bool = False) -> Settings:
    load_dotenv(args.env)
    path = args.config if Path(args.config).exists() else None
    if path is None and args.command == "run":
        raise ConfigError(f"{args.config} introuvable. Lancez d'abord : python -m tradebot init")
    settings = load_settings(path, validate=False)
    if getattr(args, "timeframe", None):
        settings.timeframe = args.timeframe
    if getattr(args, "strategy", None) and args.strategy != settings.strategy:
        settings.strategy, settings.strategy_params = args.strategy, {}
    if getattr(args, "param", None):
        settings.strategy_params = settings.strategy_params | dict(args.param)
    settings.validate(for_trading=for_trading)
    return settings


def market_data(settings: Settings) -> MarketData:
    """Données publiques (sans clé) : ccxt si installé, sinon l'API publique de Binance."""
    try:
        import ccxt  # noqa: F401
    except ImportError:
        if settings.exchange != "binance":
            raise ConfigError(f"Pour l'exchange « {settings.exchange} », installez ccxt : "
                              "pip install -r requirements.txt") from None
        return BinancePublic()
    return CcxtData(build_ccxt(settings.exchange, sandbox=False))


def get_candles(args: argparse.Namespace, settings: Settings, symbol: str) -> list[Candle]:
    if args.source == "synthetic":
        return synthetic(timeframe=settings.timeframe)
    if args.source == "csv":
        if not args.csv:
            raise ConfigError("--csv est requis avec --source csv")
        return load_csv(args.csv)
    print(f"Téléchargement de {args.days:g} jours de {symbol} en {settings.timeframe}…", file=sys.stderr)
    return fetch_history(market_data(settings), symbol, settings.timeframe, args.days)


def symbols_for(args: argparse.Namespace, settings: Settings) -> list[str]:
    if args.source != "exchange":
        return [args.symbol or ("SYNTHÉTIQUE" if args.source == "synthetic" else Path(args.csv or "csv").stem)]
    return [args.symbol] if args.symbol else settings.symbols


# ------------------------------------------------------------------ commandes
def cmd_init(args: argparse.Namespace) -> int:
    for template, target in (("config.example.toml", "config.toml"), (".env.example", ".env")):
        if Path(target).exists():
            print(f"{target} existe déjà : inchangé")
        else:
            shutil.copyfile(PROJECT_DIR / template, target)
            print(f"{target} créé — à personnaliser")
    return 0


def cmd_strategies(args: argparse.Namespace) -> int:
    for name, cls in STRATEGIES.items():
        defaults = ", ".join(f"{f.name}={f.default}" for f in fields(cls))  # type: ignore[arg-type]
        print(f"{name}\n  {cls.label}\n  {cls.description}\n  Paramètres : {defaults}\n")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    settings = load(args)
    strategy = settings.build_strategy()
    for symbol in symbols_for(args, settings):
        candles = get_candles(args, settings, symbol)
        if len(candles) <= strategy.warmup:
            print(f"{symbol} : pas assez de données ({len(candles)} bougies)", file=sys.stderr)
            continue
        result = run_backtest(candles, strategy, settings.risk, args.capital, settings.timeframe,
                              symbol, settings.slippage_pct)
        print(f"\n=== {symbol} · {strategy} · {settings.timeframe} · "
              f"{fmt_time(candles[0].time)} → {fmt_time(candles[-1].time)} ({len(candles)} bougies) ===")
        print(f"Capital : {result.initial_capital:,.2f} → {result.final_equity:,.2f}")
        print(result.stats.summary())
        if args.trades:
            for t in result.trades:
                print(f"  {fmt_time(t.opened_at)} → {fmt_time(t.closed_at)}  {t.entry_price:>12.6g} → "
                      f"{t.exit_price:<12.6g} {t.pnl:+10.2f} ({t.pnl_pct:+.2f} %)  {t.reason}")
        if args.report:
            path = Path(args.report)
            if len(symbols_for(args, settings)) > 1:
                path = path.with_name(f"{path.stem}-{symbol.replace('/', '-')}{path.suffix}")
            path.write_text(render_report(result, settings.brand), encoding="utf-8")
            print(f"Rapport : {path}")
    print("\nRappel : un backtest favorable ne garantit aucun gain futur.")
    return 0


def _row(stats: Stats | None) -> str:
    if stats is None:
        return "     (données insuffisantes)"
    return (f"{stats.total_return_pct:+8.2f} % {stats.sharpe:6.2f} {-stats.max_drawdown_pct:7.2f} % "
            f"{stats.trades:6d}")


_COLUMNS = f"{'rendement':>10} {'sharpe':>6} {'drawdown':>9} {'trades':>6}"


def cmd_optimize(args: argparse.Namespace) -> int:
    settings = load(args)
    for symbol in symbols_for(args, settings):
        candles = get_candles(args, settings, symbol)
        rows = optimize(candles, settings.strategy, settings.risk, settings.timeframe, args.metric,
                        args.train_ratio, args.top, base_params=settings.strategy_params)
        print(f"\n=== Optimisation {settings.strategy} sur {symbol} ({len(candles)} bougies, critère : {args.metric}) ===")
        if not rows:
            print("Aucune combinaison testable (historique trop court ?)")
            continue
        print(f"{'':<45}│ {'ENTRAÎNEMENT':^34} │ {'TEST (période jamais vue)':^34}")
        print(f"{'paramètres':<45}│ {_COLUMNS} │ {_COLUMNS}")
        for row in rows:
            params = ", ".join(f"{k}={v}" for k, v in row.params.items()
                               if k in STRATEGIES[settings.strategy].search_space)
            print(f"{params:<45}│ {_row(row.train)} │ {_row(row.test)}")
        best = rows[0]
        if best.test and best.test.total_return_pct <= 0:
            print("⚠️  La meilleure combinaison perd de l'argent sur la période de test : ne l'utilisez pas.")
        elif best.test and best.train.total_return_pct > 0 and best.test.total_return_pct < best.train.total_return_pct / 2:
            print("⚠️  Résultats de test nettement inférieurs à l'entraînement : sur-ajustement probable.")
        print("Pour utiliser une combinaison, reportez-la dans [strategy.params] de config.toml.")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    settings = load(args)
    symbol = args.symbol or settings.symbols[0]
    candles = fetch_history(market_data(settings), symbol, settings.timeframe, args.days)
    save_csv(candles, args.out)
    print(f"{len(candles)} bougies enregistrées dans {args.out}")
    return 0


def cmd_reset_paper(args: argparse.Namespace) -> int:
    settings = load(args)
    path = settings.data_dir / "paper.sqlite"
    if input(f"Effacer le portefeuille simulé et son historique ({path}) ? Tapez OUI : ").strip() != "OUI":
        print("Annulé.")
        return 1
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    print("Portefeuille simulé remis à zéro.")
    return 0


def _confirm_real_money(settings: Settings) -> None:
    import os
    warning = (f"ATTENTION : mode LIVE sur {settings.exchange}, de l'argent RÉEL va être engagé "
               f"sur {', '.join(settings.symbols)}.")
    if os.environ.get("CONFIRM_REAL_MONEY") == "OUI":
        logging.getLogger("tradebot").warning(warning)
        return
    if not sys.stdin.isatty():
        raise ConfigError(f"{warning} Pour confirmer sans terminal (Docker), définissez CONFIRM_REAL_MONEY=OUI.")
    if input(f"{warning}\nTapez OUI pour continuer : ").strip() != "OUI":
        raise ConfigError("Démarrage annulé.")


def cmd_run(args: argparse.Namespace) -> int:
    from .dashboard import Dashboard
    from .engine import Engine

    settings = load(args, for_trading=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    file_log = RotatingFileHandler(settings.data_dir / "tradebot.log", maxBytes=5_000_000, backupCount=3,
                                   encoding="utf-8")
    file_log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s : %(message)s"))
    logging.getLogger().addHandler(file_log)
    log = logging.getLogger("tradebot")

    store = Store(settings.db_path)
    if settings.mode == "paper":
        data = market_data(settings)
        broker: Any = PaperBroker(settings.paper_capital, settings.risk.fee_pct, settings.slippage_pct,
                                  wallet=store.get("paper_wallet"), on_change=lambda w: store.set("paper_wallet", w))
    else:
        exchange = build_ccxt(settings.exchange, settings.api_key, settings.api_secret, settings.api_password,
                              sandbox=settings.sandbox)
        missing = [s for s in settings.symbols if s not in exchange.markets]
        if missing:
            raise ConfigError(f"Paire(s) inconnue(s) sur {settings.exchange} : {', '.join(missing)}")
        if not settings.sandbox:
            _confirm_real_money(settings)
        data, broker = CcxtData(exchange), CcxtBroker(exchange, settings.quote)

    notifier: Any = (TelegramNotifier(settings.telegram_token, settings.telegram_chat_id)
                     if settings.telegram_enabled else NullNotifier())
    engine = Engine(settings, broker, data, store, notifier)

    dashboard = None
    if settings.dashboard_enabled:
        password = settings.dashboard_password
        if not password:
            password = secrets.token_urlsafe(12)
            log.warning("DASHBOARD_PASSWORD non défini : mot de passe temporaire « %s » "
                        "(définissez-le dans .env)", password)
        dashboard = Dashboard(store, password, settings.dashboard_host, settings.dashboard_port,
                              settings.brand).start()
        log.info("Tableau de bord : %s (identifiant libre, mot de passe ci-dessus ou dans .env)", dashboard.url)

    def shutdown(signum: int, frame: Any) -> None:
        log.info("Arrêt demandé…")
        engine.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        engine.run()
    finally:
        if dashboard:
            dashboard.stop()
        store.close()
    return 0
