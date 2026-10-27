"""`python -m clipper check`: tries every configured credential without publishing anything."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import Config, Secrets
from .github_secrets import API_URL as GITHUB_API_URL
from .github_secrets import secret_saver
from .http import Http
from .publishers.telegram import TelegramPublisher
from .publishers.tiktok import TikTokPublisher
from .publishers.youtube import YouTubePublisher
from .sources.twitch import TwitchApi

ICONS = {"ok": "✅", "error": "❌", "absent": "➖"}


@dataclass
class Check:
    name: str
    status: str  # "ok" | "error" | "absent"
    detail: str


def run_checks(
    config: Config,
    secrets: Secrets,
    http: Http,
    *,
    twitch: TwitchApi | None = None,
    claude: Any = None,
    youtube: YouTubePublisher | None = None,
    tiktok: TikTokPublisher | None = None,
    telegram: TelegramPublisher | None = None,
    github_api_url: str = GITHUB_API_URL,
) -> list[Check]:
    checks: list[Check] = []

    def attempt(name: str, configured: bool, probe: Callable[[], str], missing: str) -> None:
        if not configured:
            checks.append(Check(name, "absent", missing))
            return
        try:
            checks.append(Check(name, "ok", probe()))
        except Exception as exc:  # a diagnostic: every failure is reported, none stops the others
            checks.append(Check(name, "error", str(exc) or type(exc).__name__))

    def check_twitch() -> str:
        api = twitch or TwitchApi(secrets.twitch_client_id or "", secrets.twitch_client_secret or "", http)
        logins = config.twitch.streamers or ["twitch"]
        found = api.users(logins)
        missing = sorted(set(logins) - set(found))
        detail = f"API OK, {len(found)} chaîne(s) sur {len(logins)} trouvée(s)"
        return detail + (f" ; introuvables : {', '.join(missing)}" if missing else "")

    def check_claude() -> str:
        client = claude
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=secrets.anthropic_api_key, max_retries=1)
        model = client.models.retrieve(config.ai.model)
        return f"clé valide, modèle {model.id} disponible"

    def check_youtube() -> str:
        publisher = youtube or YouTubePublisher(secrets, config.publish.youtube, http, language=config.channel.language)
        return publisher.verify()

    def check_tiktok() -> str:
        publisher = tiktok or TikTokPublisher(secrets, config.publish.tiktok, http)
        if publisher.on_new_refresh_token is None:
            publisher.on_new_refresh_token = secret_saver(http, secrets.gh_secrets_token, secrets.github_repository, "TIKTOK_REFRESH_TOKEN")
        return publisher.verify()

    def check_telegram() -> str:
        return (telegram or TelegramPublisher(secrets, http)).verify()

    def check_github() -> str:
        if not secrets.github_repository:
            return "jeton présent (vérifiable seulement dans GitHub Actions)"
        http.get(
            f"{github_api_url.rstrip('/')}/repos/{secrets.github_repository}/actions/secrets/public-key",
            headers={"Authorization": f"Bearer {secrets.gh_secrets_token}", "Accept": "application/vnd.github+json"},
        )
        return "peut mettre à jour le secret TIKTOK_REFRESH_TOKEN"

    attempt("Twitch (API officielle)", secrets.twitch_api, check_twitch,
            "pas de clés : les clips sont lus via yt-dlp (ça marche, sans la découverte des lives)")
    attempt("Claude", bool(secrets.anthropic_api_key), check_claude, "pas de clé : tri par popularité, textes par gabarit")
    attempt("YouTube", secrets.youtube, check_youtube, "pas de jetons : pas de publication YouTube")
    attempt("TikTok", secrets.tiktok, check_tiktok, "pas de jetons : pas d'envoi TikTok")
    attempt("Telegram", secrets.telegram, check_telegram, "pas de bot : pas de vidéo sur ton téléphone")
    attempt("GitHub (GH_SECRETS_TOKEN)", bool(secrets.gh_secrets_token), check_github,
            "facultatif : sert à garder le jeton TikTok à jour tout seul")
    return checks


def markdown(checks: list[Check]) -> str:
    lines = ["## Clipper — vérification des branchements", "", "| | Service | Résultat |", "| --- | --- | --- |"]
    for check in checks:
        detail = check.detail.replace("|", "/").replace("\n", " ")
        lines.append(f"| {ICONS[check.status]} | {check.name} | {detail} |")
    return "\n".join(lines)
