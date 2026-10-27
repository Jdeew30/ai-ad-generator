from __future__ import annotations

import logging
from typing import Protocol

from ..config import Config, Secrets
from ..http import Http

log = logging.getLogger(__name__)


class PublishError(Exception):
    pass


class Publisher(Protocol):
    name: str


def _enabled(setting: bool | str, configured: bool, label: str, missing: str) -> bool:
    if setting is False:
        return False
    if not configured:
        if setting is True:
            raise PublishError(f"{label} est activé (enabled: true) mais {missing} manquent.")
        log.info("%s : non configuré (%s absents), publication ignorée.", label, missing)
        return False
    return True


def build_publishers(config: Config, secrets: Secrets, http: Http) -> dict[str, object]:
    """Publishers whose credentials are present ("auto") or explicitly required (true)."""
    from .telegram import TelegramPublisher
    from .tiktok import TikTokPublisher
    from .youtube import YouTubePublisher

    publishers: dict[str, object] = {}
    settings = config.publish
    if _enabled(settings.youtube.enabled, secrets.youtube, "YouTube", "YOUTUBE_CLIENT_ID / _SECRET / _REFRESH_TOKEN"):
        publishers["youtube"] = YouTubePublisher(secrets, settings.youtube, http, language=config.channel.language)
    if _enabled(settings.tiktok.enabled, secrets.tiktok, "TikTok", "TIKTOK_CLIENT_KEY / _SECRET / _REFRESH_TOKEN"):
        publishers["tiktok"] = TikTokPublisher(secrets, settings.tiktok, http)
    if _enabled(settings.telegram.enabled, secrets.telegram, "Telegram", "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"):
        publishers["telegram"] = TelegramPublisher(secrets, http)
    return publishers
