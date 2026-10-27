from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Secrets, TikTokPublishConfig
from ..http import Http, HttpError
from ..models import Candidate, PublishResult, ShortMeta
from . import PublishError

log = logging.getLogger(__name__)

API_URL = "https://open.tiktokapis.com/v2"
MIN_CHUNK = 5 * 1024 * 1024
MAX_SINGLE = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024
MAX_CAPTION = 2200

HINTS = {
    "unaudited_client_can_only_post_to_private_accounts": (
        "votre app TikTok n'est pas encore auditée : en mode « direct », TikTok n'accepte que les comptes privés. "
        "Passez publish.tiktok.mode à « inbox » (brouillon à valider dans l'app) ou demandez l'audit."
    ),
    "scope_not_authorized": "le jeton n'a pas le scope video.upload / video.publish : relancez `python -m clipper auth tiktok`.",
    "access_token_invalid": "jeton invalide : relancez `python -m clipper auth tiktok`.",
    "spam_risk_too_many_posts": "trop de publications aujourd'hui (limite anti-spam de TikTok).",
    "spam_risk_too_many_pending_share": "trop de brouillons en attente dans la boîte TikTok : validez-les ou supprimez-les.",
    "spam_risk_user_banned_from_posting": "ce compte n'a plus le droit de publier via l'API.",
    "reached_active_user_cap": "quota quotidien d'utilisateurs de l'app atteint.",
    "privacy_level_option_mismatch": "niveau de confidentialité refusé pour ce compte.",
}


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok's rules: one chunk up to 64 MB, else 10 MB
    chunks with the remainder folded into the last one."""
    if size <= 0:
        raise PublishError("fichier vidéo vide")
    if size <= MAX_SINGLE:
        return size, 1
    return CHUNK, size // CHUNK


def caption(meta: ShortMeta, candidate: Candidate) -> str:
    text = f"{meta.title}\n\n{meta.hashtag_line()}\n🎥 {candidate.credit}".strip()
    return text[:MAX_CAPTION]


class TikTokPublisher:
    """TikTok Content Posting API: inbox draft (default) or direct post."""

    name = "tiktok"

    def __init__(
        self,
        secrets: Secrets,
        settings: TikTokPublishConfig,
        http: Http,
        *,
        api_url: str = API_URL,
        on_new_refresh_token: Callable[[str], None] | None = None,
        poll_interval: float = 5.0,
        poll_timeout: float = 600.0,
    ) -> None:
        self.secrets = secrets
        self.settings = settings
        self.http = http
        self.api_url = api_url.rstrip("/")
        self.on_new_refresh_token = on_new_refresh_token
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._refresh_token = secrets.tiktok_refresh_token

    def _access_token(self) -> str:
        try:
            response = self.http.post(
                f"{self.api_url}/oauth/token/",
                data={
                    "client_key": self.secrets.tiktok_client_key,
                    "client_secret": self.secrets.tiktok_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                retry=True,
            )
        except HttpError as exc:
            raise PublishError(f"TikTok : jeton impossible à obtenir ({exc})") from exc
        data = response.json()
        if "access_token" not in data:
            raise PublishError(
                f"TikTok : {data.get('error', 'erreur')} — {data.get('error_description', '')}. "
                "Le refresh token a peut-être expiré (365 jours) : relancez `python -m clipper auth tiktok`."
            )
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self.on_new_refresh_token:
                self.on_new_refresh_token(new_refresh)
            else:
                log.warning("TikTok a fourni un nouveau refresh token : mettez à jour le secret TIKTOK_REFRESH_TOKEN.")
        return data["access_token"]

    def _call(self, token: str, path: str, payload: dict[str, Any], *, retry: bool = False) -> dict[str, Any]:
        try:
            response = self.http.post(
                f"{self.api_url}{path}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
                json=payload,
                retry=retry,
                ok=(200, 400, 401, 403, 429),
            )
        except HttpError as exc:
            raise PublishError(f"TikTok {path} : {exc}") from exc
        body = response.json()
        error = body.get("error") or {}
        code = error.get("code", "ok")
        if code != "ok":
            hint = HINTS.get(code, error.get("message") or "")
            raise PublishError(f"TikTok : {code} — {hint} (log_id {error.get('log_id', '?')})")
        return body.get("data") or {}

    def _upload(self, upload_url: str, video: Path, chunk_size: int, count: int) -> None:
        size = video.stat().st_size
        with video.open("rb") as handle:
            for index in range(count):
                start = index * chunk_size
                end = size - 1 if index == count - 1 else start + chunk_size - 1
                handle.seek(start)
                data = handle.read(end - start + 1)
                try:
                    self.http.put(
                        upload_url,
                        data=data,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(len(data)),
                            "Content-Range": f"bytes {start}-{end}/{size}",
                        },
                        ok=(200, 201, 206),
                        timeout=300,
                    )
                except HttpError as exc:
                    raise PublishError(f"TikTok : envoi du morceau {index + 1}/{count} impossible ({exc})") from exc

    def _wait(self, token: str, publish_id: str, done: set[str]) -> dict[str, Any]:
        deadline = time.monotonic() + self.poll_timeout
        while True:
            data = self._call(token, "/post/publish/status/fetch/", {"publish_id": publish_id}, retry=True)
            status = data.get("status")
            if status in done:
                return data
            if status == "FAILED":
                raise PublishError(f"TikTok a refusé la vidéo : {data.get('fail_reason') or 'raison inconnue'}")
            if time.monotonic() > deadline:
                raise PublishError(f"TikTok : toujours « {status} » après {self.poll_timeout:.0f}s (publish_id {publish_id})")
            self.http.sleep(self.poll_interval)

    def publish_short(self, video: Path, meta: ShortMeta, candidate: Candidate) -> PublishResult:
        token = self._access_token()
        size = video.stat().st_size
        chunk_size, count = chunk_plan(size)
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": count}
        if self.settings.mode == "inbox":
            data = self._call(token, "/post/publish/inbox/video/init/", {"source_info": source})
            done = {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"}
        else:
            creator = self._call(token, "/post/publish/creator_info/query/", {}, retry=True)
            options = creator.get("privacy_level_options") or []
            if self.settings.privacy not in options:
                raise PublishError(f"TikTok : confidentialité {self.settings.privacy} indisponible pour ce compte (options : {', '.join(options) or 'aucune'})")
            post_info = {
                "title": caption(meta, candidate),
                "privacy_level": self.settings.privacy,
                "disable_comment": not self.settings.allow_comments or bool(creator.get("comment_disabled")),
                "disable_duet": not self.settings.allow_duet or bool(creator.get("duet_disabled")),
                "disable_stitch": not self.settings.allow_stitch or bool(creator.get("stitch_disabled")),
                "video_cover_timestamp_ms": 1000,
                "brand_content_toggle": False,
                "brand_organic_toggle": False,
                "is_aigc": False,
            }
            data = self._call(token, "/post/publish/video/init/", {"post_info": post_info, "source_info": source})
            done = {"PUBLISH_COMPLETE"}
        publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
        if not publish_id or not upload_url:
            raise PublishError("TikTok n'a pas renvoyé d'URL d'envoi")
        self._upload(upload_url, video, chunk_size, count)
        status = self._wait(token, publish_id, done)
        if status.get("status") == "SEND_TO_USER_INBOX":
            detail = "brouillon envoyé dans votre boîte TikTok : ouvrez l'app pour publier (texte à coller dans Telegram / le résumé)"
        else:
            detail = f"publié ({self.settings.privacy})"
        post_ids = status.get("publicaly_available_post_id") or status.get("publicly_available_post_id") or []
        url = f"https://www.tiktok.com/video/{post_ids[0]}" if post_ids else None
        return PublishResult(self.name, True, publish_id, url, detail)
