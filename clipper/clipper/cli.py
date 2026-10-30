from __future__ import annotations

import argparse
import importlib
import logging
import os
import re
import secrets as stdlib_secrets
import subprocess
import sys
from pathlib import Path

from .config import ASSETS_DIR, PROJECT_DIR, ConfigError, Paths, Secrets, load_config, load_dotenv
from .publishers import PublishError

log = logging.getLogger("clipper")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("urllib3", "httpx", "httpx2", "anthropic", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _summary(markdown: str) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    print("\n" + markdown)


def _clipper(args: argparse.Namespace):
    from .pipeline import Clipper

    config = load_config(Path(args.config) if args.config else None)
    return Clipper(config, Secrets.from_env(), Paths.resolve())


def cmd_run(args: argparse.Namespace) -> int:
    queue = None if args.no_queue else PROJECT_DIR / "queue.yaml"
    focus = [target for target in re.split(r"[,\s]+", args.focus or "") if target] or None
    report = _clipper(args).run_shorts(
        dry_run=args.dry_run, count=args.count, url=args.url, start=args.start, end=args.end, queue=queue, focus=focus
    )
    _summary(report.markdown())
    return 1 if report.errors else 0


def cmd_compile(args: argparse.Namespace) -> int:
    report = _clipper(args).run_compilation(dry_run=args.dry_run, count=args.count)
    _summary(report.markdown())
    return 1 if report.errors else 0


def cmd_discover(args: argparse.Namespace) -> int:
    from datetime import timedelta

    from .selection import rank
    from .sources import discover
    from .sources.manual import QueueError
    from .sources.twitch import SourceError

    clipper = _clipper(args)
    config = clipper.config
    if args.compilation:
        window = timedelta(days=config.compilation.window_days)
    else:
        window = timedelta(hours=config.twitch.window_hours)
    try:
        found = discover(config, clipper.secrets, clipper.http, clipper.ytdlp, now=clipper.now, window=window,
                         queue=None if args.compilation else PROJECT_DIR / "queue.yaml")
    except (SourceError, QueueError) as exc:
        log.error("Découverte impossible : %s", exc)
        return 1
    ranked = rank(found, clipper.state, config, now=clipper.now, compilation=args.compilation)
    print(f"\n{'score':>6}  {'vues':>8}  {'durée':>5}  créateur — titre")
    for candidate in ranked[: args.limit]:
        print(f"{candidate.score:6.2f}  {candidate.views:8d}  {candidate.duration:5.0f}  {candidate.streamer} — {candidate.title}  ({candidate.url})")
    if not ranked:
        print("(aucun candidat)")
    return 0


def cmd_clips(args: argparse.Namespace) -> int:
    from .http import Http
    from .sources.twitch import SourceError, TwitchApi, clip_slug, clips_around

    secrets = Secrets.from_env()
    if not secrets.twitch_api:
        log.error("Il faut les clés API Twitch (TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET).")
        return 2
    slug = clip_slug(args.clip) or args.clip.strip()
    api = TwitchApi(secrets.twitch_client_id or "", secrets.twitch_client_secret or "", Http())
    try:
        clip, found = clips_around(api, slug, args.minutes)
    except SourceError as exc:
        log.error("%s", exc)
        return 1
    lines = [
        f"## Clips de {clip.get('broadcaster_name') or '?'} à {args.minutes:g} min de « {clip.get('title') or slug} »",
        "",
        "Pour un short plus long, relance le mode `url` avec le lien voulu, et `start` / `end` pour le garder en entier.",
        "",
        "| créé (UTC) | durée | vues | replay | titre | lien |",
        "|---|---|---|---|---|---|",
    ]
    for other in found:
        offset = other.get("vod_offset")
        replay = f"{other['video_id']} à {offset} s" if other.get("video_id") and offset is not None else "—"
        mark = " ← ce clip" if other.get("id") == clip["id"] else ""
        title = (other.get("title") or "").replace("|", "/")
        lines.append(f"| {(other.get('created_at') or '')[:19].replace('T', ' ')} | {float(other.get('duration') or 0):.0f} s "
                     f"| {other.get('view_count', 0)} | {replay} | {title}{mark} | {other.get('url', '')} |")
    _summary("\n".join(lines))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .check import markdown, run_checks
    from .http import Http

    config = load_config(Path(args.config) if args.config else None)
    checks = run_checks(config, Secrets.from_env(), Http())
    _summary(markdown(checks))
    return 1 if any(check.status == "error" for check in checks) else 0


def cmd_auth(args: argparse.Namespace) -> int:
    from .auth import AuthError, code_from_redirect, tiktok_authorize_url, tiktok_tokens, youtube_refresh_token
    from .http import Http

    http = Http()
    found = Secrets.from_env()
    try:
        if args.service == "youtube":
            client_id = args.client_id or found.youtube_client_id or input("YOUTUBE_CLIENT_ID : ").strip()
            client_secret = args.client_secret or found.youtube_client_secret or input("YOUTUBE_CLIENT_SECRET : ").strip()
            tokens = youtube_refresh_token(client_id, client_secret, http, open_browser=not args.no_browser)
            print("\nAjoutez ce secret (GitHub > Settings > Secrets and variables > Actions) :\n")
            print(f"YOUTUBE_REFRESH_TOKEN={tokens['refresh_token']}\n")
        elif args.service == "tiktok":
            client_key = args.client_id or found.tiktok_client_key or input("TIKTOK_CLIENT_KEY : ").strip()
            client_secret = args.client_secret or found.tiktok_client_secret or input("TIKTOK_CLIENT_SECRET : ").strip()
            redirect = args.redirect_uri or input("Redirect URI déclarée dans l'app TikTok : ").strip()
            mode = load_config(Path(args.config) if args.config else None).publish.tiktok.mode
            state = stdlib_secrets.token_urlsafe(12)
            print("\nOuvrez cette adresse avec le compte TikTok de la chaîne et acceptez :\n")
            print(tiktok_authorize_url(client_key, redirect, state, mode=mode) + "\n")
            pasted = input("Collez ici l'adresse complète de la page sur laquelle vous arrivez : ")
            tokens = tiktok_tokens(client_key, client_secret, code_from_redirect(pasted, state), redirect, http)
            print(f"\nScopes accordés : {tokens.get('scope')}")
            print("Ajoutez ce secret :\n")
            print(f"TIKTOK_REFRESH_TOKEN={tokens['refresh_token']}\n")
        else:
            from .publishers.telegram import TelegramPublisher

            token = args.client_id or found.telegram_bot_token or input("TELEGRAM_BOT_TOKEN : ").strip()
            chats = TelegramPublisher(Secrets(telegram_bot_token=token), http).chats()
            if not chats:
                print("Aucun message reçu : envoyez « /start » à votre bot dans Telegram, puis relancez cette commande.")
                return 1
            for chat_id, name in chats:
                print(f"TELEGRAM_CHAT_ID={chat_id}   ({name})")
    except AuthError as exc:
        print(f"Échec : {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    problems = 0

    def check(ok: bool, label: str, fix: str = "") -> None:
        nonlocal problems
        print(f"{'✅' if ok else '❌'} {label}{'' if ok or not fix else f' — {fix}'}")
        problems += 0 if ok else 1

    try:
        config = load_config(Path(args.config) if args.config else None)
        check(True, f"configuration valide ({len(config.twitch.streamers)} streamers suivis)")
    except ConfigError as exc:
        check(False, "configuration", str(exc))
        config = None
    for module, package in (("yt_dlp", "yt-dlp"), ("faster_whisper", "faster-whisper"), ("cv2", "opencv-python-headless"),
                            ("anthropic", "anthropic"), ("nacl", "PyNaCl"), ("PIL", "pillow")):
        try:
            importlib.import_module(module)
            check(True, f"module {package}")
        except ImportError:
            check(False, f"module {package}", "pip install -r requirements.txt")
    try:
        from .media import MediaError, ffmpeg_bin

        filters = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
        check(" subtitles " in filters, "ffmpeg avec libass (sous-titres)", "installez un ffmpeg complet (apt install ffmpeg)")
    except (OSError, subprocess.TimeoutExpired, MediaError) as exc:
        check(False, "ffmpeg", str(exc))
    check((ASSETS_DIR / "fonts" / "Anton-Regular.ttf").exists(), "police Anton")
    check((ASSETS_DIR / "models" / "face_detection_yunet_2023mar.onnx").exists(), "modèle de détection de visage")
    found = Secrets.from_env()
    print("\nIdentifiants (facultatifs, chacun active une fonction) :")
    for label, ok, effect in (
        ("Twitch API", found.twitch_api, "découverte des lives + infos de VOD (sinon : liste via yt-dlp)"),
        ("Claude (ANTHROPIC_API_KEY)", bool(found.anthropic_api_key), "tri et textes par IA (sinon : gabarit)"),
        ("YouTube", found.youtube, "publication automatique YouTube"),
        ("TikTok", found.tiktok, "envoi TikTok"),
        ("Telegram", found.telegram, "vidéos envoyées sur votre téléphone"),
    ):
        print(f"{'✅' if ok else '➖'} {label} : {effect}")
    if config is not None:
        try:
            from .state import State

            state = State.load(Paths.resolve().state)
            print(f"\nÉtat : {len(state.posted)} clip(s) publié(s), {len(state.rejected)} écarté(s).")
        except (ValueError, OSError) as exc:
            check(False, "fichier d'état", str(exc))
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m clipper", description="Chaîne de clips automatisée (Twitch/YouTube -> Shorts/TikTok).")
    parser.add_argument("--config", help="chemin de config.yaml (défaut : celui du dossier clipper)")
    parser.add_argument("-v", "--verbose", action="store_true", help="journal détaillé")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="trouve, monte et publie les meilleurs clips (shorts)")
    run.add_argument("--dry-run", action="store_true", help="monte les vidéos sans rien publier ni mémoriser")
    run.add_argument("--count", type=int, help="nombre de shorts (défaut : shorts.per_run)")
    run.add_argument("--url", help="traite seulement cette URL (clip Twitch, vidéo YouTube, VOD…)")
    run.add_argument("--start", help="début de l'extrait pour --url (ex. 1:23)")
    run.add_argument("--end", help="fin de l'extrait pour --url (ex. 2:05)")
    run.add_argument("--no-queue", action="store_true", help="ignore queue.yaml")
    run.add_argument("--focus", help="seulement ces chaînes ou vidéos, séparées par des virgules "
                                     "(chaîne Twitch, @chaîne YouTube ou lien de vidéo YouTube)")
    run.set_defaults(func=cmd_run)

    render = commands.add_parser("render", help="monte une URL en short sans publier (= run --url … --dry-run)")
    render.add_argument("url")
    render.add_argument("--start")
    render.add_argument("--end")
    render.set_defaults(func=cmd_run, dry_run=True, count=None, no_queue=True)

    compile_ = commands.add_parser("compile", help="monte et publie le best-of de la semaine")
    compile_.add_argument("--dry-run", action="store_true")
    compile_.add_argument("--count", type=int, help="nombre de clips (défaut : compilation.clips)")
    compile_.set_defaults(func=cmd_compile)

    discover = commands.add_parser("discover", help="liste les candidats classés, sans rien télécharger")
    discover.add_argument("--compilation", action="store_true", help="fenêtre de la compilation (7 jours)")
    discover.add_argument("--limit", type=int, default=30)
    discover.set_defaults(func=cmd_discover)

    clips = commands.add_parser("clips", help="liste les autres clips d'un même moment (pour un short plus long)")
    clips.add_argument("clip", help="lien ou identifiant d'un clip Twitch")
    clips.add_argument("--minutes", type=float, default=10.0, help="écart maximal avec ce clip (défaut : 10 min)")
    clips.set_defaults(func=cmd_clips)

    auth = commands.add_parser("auth", help="obtient les jetons à enregistrer en secrets (à lancer sur votre ordinateur)")
    auth.add_argument("service", choices=["youtube", "tiktok", "telegram"])
    auth.add_argument("--client-id", help="client id (YouTube), client key (TikTok) ou jeton du bot (Telegram)")
    auth.add_argument("--client-secret")
    auth.add_argument("--redirect-uri", help="TikTok : l'URI de redirection déclarée dans l'app")
    auth.add_argument("--no-browser", action="store_true")
    auth.set_defaults(func=cmd_auth)

    doctor = commands.add_parser("doctor", help="vérifie l'installation et les identifiants présents")
    doctor.set_defaults(func=cmd_doctor)

    check = commands.add_parser("check", help="essaie chaque identifiant (message de test Telegram), sans rien publier")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROJECT_DIR / ".env")
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except ConfigError as exc:
        log.error("Configuration : %s", exc)
        return 2
    except PublishError as exc:
        log.error("Publication : %s", exc)
        return 2
    except KeyboardInterrupt:
        return 130
