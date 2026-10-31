"""Interface en ligne de commande : python -m vintedbot <commande>."""
from __future__ import annotations

import argparse
import logging
import shutil
import signal
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import __version__
from .analysis import Evaluation, comparable_reason, decide, eligible_reason, evaluate, market_stats, rank
from .client import Blocked, VintedClient, VintedError
from .config import ConfigError, Settings, Watch, load_dotenv, load_settings
from .models import CONDITIONS, Listing, condition_label
from .monitor import Monitor
from .notify import ConsoleNotifier, DiscordNotifier, Notifier, TelegramNotifier, eur
from .presets import PRESETS
from .store import Store

PROJECT_DIR = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vintedbot", description="Alertes de bonnes affaires Vinted pour l'achat-revente")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    base = argparse.ArgumentParser(add_help=False)
    base.add_argument("--config", default="config.toml", help="fichier de configuration (défaut : config.toml)")
    base.add_argument("--env", default=".env", help="fichier de secrets (défaut : .env)")

    sub.add_parser("init", help="crée config.toml et .env à partir des modèles")
    sub.add_parser("presets", help="liste les profils d'expert (console, sneakers...)")
    sub.add_parser("doctor", parents=[base], help="vérifie la connexion à Vinted")

    an = sub.add_parser("analyze", parents=[base], help="étude de marché d'un produit")
    an.add_argument("target", help="mots-clés (ex. \"ps5 slim\") ou URL de recherche Vinted")
    an.add_argument("--preset", default="general", choices=sorted(PRESETS))
    an.add_argument("--include", action="append", default=[], help="mot obligatoire (répétable)")
    an.add_argument("--exclude", action="append", default=[], help="mot interdit (répétable)")
    an.add_argument("--min-price", type=float, help="écarte les annonces moins chères (accessoires)")
    an.add_argument("--min-profit", type=float, default=20.0, help="marge visée en euros (défaut : 20)")
    an.add_argument("--shipping", type=float, help="port à l'achat (défaut : celui du profil)")
    an.add_argument("--pages", type=int, default=3, help="pages lues (48 annonces par page, défaut : 3)")

    ck = sub.add_parser("check", parents=[base], help="passage unique : marché et meilleures offres actuelles")
    ck.add_argument("--watch", help="nom d'une recherche (défaut : toutes)")

    sub.add_parser("run", parents=[base], help="lance la surveillance en continu")
    sub.add_parser("test-notify", parents=[base], help="envoie une alerte de démonstration")
    sub.add_parser("stats", parents=[base], help="ce que le bot a appris pour chaque recherche")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    handlers = {"init": cmd_init, "presets": cmd_presets, "doctor": cmd_doctor, "analyze": cmd_analyze,
                "check": cmd_check, "run": cmd_run, "test-notify": cmd_test_notify, "stats": cmd_stats}
    try:
        return handlers[args.command](args)
    except ConfigError as e:
        print(f"Erreur de configuration : {e}", file=sys.stderr)
        return 2
    except Blocked as e:
        print(f"Vinted bloque temporairement l'accès : {e}. Réessayez plus tard.", file=sys.stderr)
        return 3
    except VintedError as e:
        print(f"Erreur Vinted : {e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


# ------------------------------------------------------------------ outils
def load(args: argparse.Namespace, for_notifications: bool = False) -> Settings:
    load_dotenv(args.env)
    path = args.config if Path(args.config).exists() else None
    if path is None and args.command in ("run", "check", "stats", "test-notify"):
        raise ConfigError(f"{args.config} introuvable. Lancez d'abord : python -m vintedbot init")
    return load_settings(path, for_notifications=for_notifications)


def make_client(settings: Settings, domain: str | None = None) -> VintedClient:
    return VintedClient(domain or settings.domain, min_interval=settings.min_interval)


def make_notifiers(settings: Settings) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if settings.telegram_enabled:
        notifiers.append(TelegramNotifier(settings.telegram_token, settings.telegram_chat_id))
    if settings.discord_enabled:
        notifiers.append(DiscordNotifier(settings.discord_webhook))
    return notifiers


# ---------------------------------------------------------------- commandes
def cmd_init(args: argparse.Namespace) -> int:
    for template, target in (("config.example.toml", "config.toml"), (".env.example", ".env")):
        if Path(target).exists():
            print(f"{target} existe déjà : inchangé")
        else:
            shutil.copyfile(PROJECT_DIR / template, target)
            print(f"{target} créé — à personnaliser")
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    for name, preset in PRESETS.items():
        port = f" · port estimé {eur(preset.shipping)}" if preset.shipping else ""
        print(f"{name:<12} {preset.label}{port}\n             {preset.description}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load(args)
    client = make_client(settings)
    print(f"1. Page d'accueil https://{client.host}/ …")
    client.open_session()
    print(f"   ✅ cookies reçus : {', '.join(sorted(client.cookies)) or 'aucun'}")
    print(f"   {'✅' if 'access_token_web' in client.cookies else '⚠️ '} cookie de session anonyme "
          f"{'présent' if 'access_token_web' in client.cookies else 'ABSENT'}")
    print(f"   {'✅' if client.anon_id else '⚠️ '} identifiant anonyme {'présent' if client.anon_id else 'absent'}"
          f" · langue {client.locale}")
    print(f"2. Recherche de test sur https://{client.api_host} …")
    items = client.search({"search_text": "nintendo switch"}, per_page=5)
    print(f"   ✅ {len(items)} annonce(s) reçue(s)")
    for item in items[:3]:
        print(f"   • {item.title[:50]!r} — {eur(item.price)} — {condition_label(item.condition)} — {item.url}")
    if not items:
        print("   ⚠️ aucune annonce : le format de Vinted a peut-être changé")
        return 1
    print("Tout fonctionne. Lancez « python -m vintedbot check » pour tester vos recherches.")
    return 0


def _max_buy_price(resale: float, profit: float, shipping: float, settings: Settings) -> float:
    c = settings.costs
    return (resale - profit - c.resale_costs - c.buyer_fee_fixed - shipping) / (1 + c.buyer_fee_pct)


def cmd_analyze(args: argparse.Namespace) -> int:
    settings = load(args)
    is_url = args.target.startswith("http")
    watch = Watch(name="analyse", query="" if is_url else args.target, url=args.target if is_url else "",
                  preset=args.preset, include=args.include, exclude=args.exclude, min_price=args.min_price,
                  shipping=args.shipping, min_profit=args.min_profit, min_discount=0.15)
    watch.prepare(settings.domain)
    client = make_client(settings, watch.host)
    listings: dict[int, Listing] = {}
    for page in range(1, max(1, args.pages) + 1):
        batch = client.search(watch.api_params, page=page, per_page=settings.per_page)
        for item in batch:
            listings.setdefault(item.id, item)
        if len(batch) < settings.per_page:
            break
    comparable = [x for x in listings.values() if comparable_reason(x, watch) is None]
    print(f"\nAnalyse « {args.target} » sur {watch.host} : {len(listings)} annonces lues, "
          f"{len(comparable)} comparables ({len(listings) - len(comparable)} écartées : accessoires, mots exclus…)")
    stats = market_stats(x.price for x in comparable)
    if not stats or stats.count < 5:
        print("Pas assez d'annonces comparables pour estimer un prix. Élargissez la recherche.")
        return 1

    print(f"\nPrix demandés sur le marché : médiane {eur(stats.median)} · "
          f"25 % des annonces sous {eur(stats.p25)} · 75 % sous {eur(stats.p75)}")
    by_condition: dict[str, list[float]] = defaultdict(list)
    for x in comparable:
        by_condition[x.condition].append(x.price)
    parts = [f"{condition_label(k)} {eur(market_stats(v).median)} ({len(v)})"
             for k in [*CONDITIONS, "inconnu"] if len(v := by_condition.get(k, [])) >= 3]
    if parts:
        print("Par état : " + " · ".join(parts))
    shipping = watch.shipping_cost(settings.costs)
    resale = stats.median * (1 - settings.costs.resale_haircut)
    max_buy = _max_buy_price(resale, args.min_profit, shipping, settings)
    print(f"Revente réaliste (médiane -{settings.costs.resale_haircut * 100:.0f} %) : ≈ {eur(resale)}")
    print(f"➡️  Pour gagner au moins {args.min_profit:g} € (protection acheteur + {eur(shipping)} de port), "
          f"achetez à {eur(max(0.0, max_buy))} maximum (état « très bon »).")

    now = time.time()
    evaluations = [evaluate(x, watch, stats, settings.costs, 5, now) for x in comparable
                   if eligible_reason(x, watch) is None]
    good = sorted((e for e in evaluations if decide(e, watch)), key=rank, reverse=True)
    print(f"\nMeilleures offres en ligne ({len(good)} à -15 % ou plus avec au moins {args.min_profit:g} € de marge) :")
    for ev in good[:10]:
        flags = " ⚠️" if ev.flags else ""
        print(f"  {ev.label} {ev.score:>3}/100 · {eur(ev.listing.price):>10} · {-ev.discount * 100:+.0f} % vs valeur · "
              f"marge ≈ {ev.profit:+.0f} €{flags}\n      {ev.listing.title[:70]} — {ev.listing.url}")
    if not good:
        print("  Aucune pour le moment.")
    print("\nPour surveiller ce produit, ajoutez dans config.toml :\n"
          f"[[watch]]\nname = \"{args.target if not is_url else 'Ma recherche'}\"\n"
          + (f"url = \"{args.target}\"\n" if is_url else f"query = \"{args.target}\"\n")
          + f"preset = \"{args.preset}\"\n"
          + (f"min_price = {args.min_price:g}\n" if args.min_price else "")
          + f"max_price = {max(1.0, max_buy):.0f}\nmin_profit = {args.min_profit:g}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    settings = load(args)
    watches = [w for w in settings.watches if w.enabled and (not args.watch or w.name == args.watch)]
    if not watches:
        raise ConfigError(f"Aucune recherche « {args.watch} »" if args.watch else "Aucune recherche active")
    clients: dict[str, VintedClient] = {}
    for watch in watches:
        client = clients.setdefault(watch.host, make_client(settings, watch.host))
        # base temporaire : simulation complète d'un premier passage, rien n'est enregistré
        Monitor(settings, client, Store(), [ConsoleNotifier()]).check(watch)
        print()
    return 0


def cmd_test_notify(args: argparse.Namespace) -> int:
    settings = load(args, for_notifications=True)
    notifiers = make_notifiers(settings) or [ConsoleNotifier()]
    watch = Watch(name="Démo PS5", query="ps5", preset="console")
    watch.prepare(settings.domain)
    listing = Listing(1, "Console PS5 Slim + 2 manettes (annonce de démonstration)", 249.0, total_price=262.15,
                      condition="tres_bon", brand="Sony", url=f"https://{settings.domain}/catalog?search_text=ps5",
                      seller="demo", favourites=3, created_at=int(time.time()) - 120)
    ev = evaluate(listing, watch, None, settings.costs, 15, time.time())
    ev = Evaluation(**{**ev.__dict__, "reference": 380.0, "reference_source": "démonstration", "discount": 0.34,
                       "resale": 342.0, "profit": 342.0 - ev.cost, "score": 78})
    for n in notifiers:
        n.deal(ev, "alerte de démonstration")
    print(f"Alerte de démonstration envoyée via : {', '.join(type(n).__name__ for n in notifiers)}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    settings = load(args)
    store = Store(settings.data_dir / "vintedbot.sqlite")
    since = time.time() - settings.market_window_days * 86400
    for watch in settings.watches:
        seen, alerts = store.counts(watch.name)
        stats = market_stats(store.market_prices(watch.name, since))
        market = (f"médiane {eur(stats.median)} [{eur(stats.p25)} – {eur(stats.p75)}] sur {stats.count} annonces"
                  if stats else "aucune donnée")
        status = "" if watch.enabled else " (désactivée)"
        print(f"{watch.name}{status} : {seen} annonces vues, {alerts} alerte(s) · marché : {market}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = load(args, for_notifications=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("vintedbot")
    notifiers = make_notifiers(settings)
    if not notifiers:
        log.warning("Ni Telegram ni Discord activé : les alertes s'afficheront seulement ici")
        notifiers = [ConsoleNotifier()]
    hosts = {w.host for w in settings.watches}
    if len(hosts) > 1:
        raise ConfigError("Toutes les recherches doivent viser le même site Vinted (ex. uniquement vinted.fr)")
    client = make_client(settings, next(iter(hosts), settings.domain))
    store = Store(settings.data_dir / "vintedbot.sqlite")
    monitor = Monitor(settings, client, store, notifiers)

    def shutdown(signum: int, frame: Any) -> None:
        log.info("Arrêt demandé…")
        monitor.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        monitor.run()
    finally:
        store.close()
    return 0
