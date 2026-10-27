from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import requests

from ..config import Secrets, YouTubePublishConfig
from ..http import Http, HttpError
from ..models import Candidate, CompilationMeta, PublishResult, ShortMeta
from . import PublishError

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
CHUNK = 8 * 1024 * 1024  # multiple of 256 KiB, as the resumable protocol requires
MAX_RESUMES = 5


def _safe(text: str, limit: int) -> str:
    """YouTube rejects '<' and '>' in titles and descriptions."""
    text = text.replace("<", "‹").replace(">", "›").strip()
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[: limit - 3].decode("utf-8", "ignore").rstrip() + "…"


def _tags(tags: list[str]) -> list[str]:
    kept: list[str] = []
    total = 0
    for tag in tags:
        tag = re.sub(r"[<>,\"]", "", tag).strip()
        cost = len(tag) + (2 if " " in tag else 0) + 1
        if tag and total + cost <= 480:
            kept.append(tag)
            total += cost
    return kept


def short_description(meta: ShortMeta, candidate: Candidate) -> str:
    credit = f"🎥 Moment original : {candidate.streamer} — {candidate.channel_url or candidate.credit}"
    if candidate.clipper:
        credit += f" (clip de {candidate.clipper})"
    lines = [meta.description, "", credit]
    if candidate.url and candidate.url != candidate.channel_url:
        lines.append(f"🔗 {candidate.url}")
    lines += ["", f"{meta.hashtag_line()} #shorts".strip()]
    return "\n".join(lines)


class YouTubePublisher:
    """YouTube Data API v3: resumable upload (8 MiB chunks, resumes after network errors)."""

    name = "youtube"

    def __init__(
        self,
        secrets: Secrets,
        settings: YouTubePublishConfig,
        http: Http,
        *,
        language: str = "fr",
        token_url: str = TOKEN_URL,
        upload_url: str = UPLOAD_URL,
        thumbnail_url: str = THUMBNAIL_URL,
    ) -> None:
        self.secrets = secrets
        self.settings = settings
        self.http = http
        self.language = language
        self.token_url = token_url
        self.upload_url = upload_url
        self.thumbnail_url = thumbnail_url

    def _access_token(self) -> str:
        try:
            response = self.http.post(
                self.token_url,
                data={
                    "client_id": self.secrets.youtube_client_id,
                    "client_secret": self.secrets.youtube_client_secret,
                    "refresh_token": self.secrets.youtube_refresh_token,
                    "grant_type": "refresh_token",
                },
                retry=True,
            )
        except HttpError as exc:
            if exc.status in {400, 401} and "invalid_grant" in exc.body:
                raise PublishError(
                    "YOUTUBE_REFRESH_TOKEN expiré ou révoqué. Si l'écran de consentement OAuth est en mode « Test », "
                    "Google l'invalide au bout de 7 jours : passez-le « En production » puis relancez "
                    "`python -m clipper auth youtube`."
                ) from exc
            raise PublishError(f"Jeton YouTube impossible à obtenir : {exc}") from exc
        return response.json()["access_token"]

    def _error(self, exc: HttpError) -> PublishError:
        reason = ""
        match = re.search(r'"reason"\s*:\s*"([^"]+)"', exc.body)
        if match:
            reason = match.group(1)
        hints = {
            "quotaExceeded": "quota quotidien de l'API YouTube épuisé (réinitialisé à minuit, heure du Pacifique)",
            "uploadLimitExceeded": "limite d'envois de la chaîne atteinte pour aujourd'hui",
            "youtubeSignupRequired": "le compte Google n'a pas encore de chaîne YouTube",
            "forbidden": "accès refusé par YouTube",
        }
        return PublishError(f"YouTube : {hints.get(reason, reason or 'erreur')} ({exc})")

    def _upload(self, video: Path, body: dict[str, Any], *, notify: bool) -> dict[str, Any]:
        token = self._access_token()
        size = video.stat().st_size
        try:
            init = self.http.post(
                self.upload_url,
                params={"uploadType": "resumable", "part": "snippet,status", "notifySubscribers": str(notify).lower()},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(size),
                },
                json=body,
                retry=True,  # opening a session does not create a video
            )
        except HttpError as exc:
            raise self._error(exc) from exc
        session = init.headers.get("Location")
        if not session:
            raise PublishError("YouTube n'a pas renvoyé d'URL d'envoi")

        offset, resumes = 0, 0
        with video.open("rb") as handle:
            while True:
                handle.seek(offset)
                chunk = handle.read(CHUNK)
                end = offset + len(chunk) - 1
                try:
                    response = self.http.session.put(
                        session,
                        data=chunk,
                        headers={"Authorization": f"Bearer {token}", "Content-Range": f"bytes {offset}-{end}/{size}"},
                        timeout=300,
                    )
                except (requests.ConnectionError, requests.Timeout) as exc:
                    response, error = None, str(exc)
                else:
                    error = f"HTTP {response.status_code}"
                if response is not None and response.status_code in {200, 201}:
                    return response.json()
                if response is not None and response.status_code == 308:
                    offset = _next_offset(response)
                    continue
                if response is not None and response.status_code < 500 and response.status_code not in {408, 429}:
                    raise self._error(HttpError(f"envoi refusé : {response.text[:300]}", response.status_code, response.text))
                resumes += 1
                if resumes > MAX_RESUMES:
                    raise PublishError(f"YouTube : envoi interrompu ({error})")
                log.warning("YouTube : envoi interrompu (%s), reprise…", error)
                self.http.sleep(min(2**resumes, 30))
                offset, finished = self._resume_offset(session, token, size)
                if finished is not None:
                    return finished

    def _resume_offset(self, session: str, token: str, size: int) -> tuple[int, dict[str, Any] | None]:
        """Asks how much the session holds; it may even be complete if only the response was lost."""
        try:
            response = self.http.session.put(
                session, headers={"Authorization": f"Bearer {token}", "Content-Range": f"bytes */{size}"}, timeout=60
            )
        except (requests.ConnectionError, requests.Timeout):
            return 0, None
        if response.status_code in {200, 201}:
            return size, response.json()
        return (_next_offset(response) if response.status_code == 308 else 0), None

    def _body(self, title: str, description: str, tags: list[str]) -> dict[str, Any]:
        return {
            "snippet": {
                "title": _safe(title, 100),
                "description": _safe(description, 4900),
                "tags": _tags(tags),
                "categoryId": self.settings.category_id,
                "defaultLanguage": self.language,
                "defaultAudioLanguage": self.language,
            },
            "status": {
                "privacyStatus": self.settings.privacy,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": False,
                "embeddable": True,
            },
        }

    def publish_short(self, video: Path, meta: ShortMeta, candidate: Candidate) -> PublishResult:
        body = self._body(meta.title, short_description(meta, candidate), meta.tags())
        result = self._upload(video, body, notify=self.settings.notify_subscribers)
        video_id = result["id"]
        return PublishResult(self.name, True, video_id, f"https://www.youtube.com/shorts/{video_id}", self._status(result))

    def publish_compilation(self, video: Path, meta: CompilationMeta, description: str, thumbnail: Path | None) -> PublishResult:
        body = self._body(meta.title, description, [h.lstrip("#") for h in meta.hashtags])
        result = self._upload(video, body, notify=self.settings.notify_subscribers)
        video_id = result["id"]
        detail = self._status(result)
        if thumbnail is not None and thumbnail.exists():
            try:
                self.http.post(
                    self.thumbnail_url,
                    params={"videoId": video_id, "uploadType": "media"},
                    headers={"Authorization": f"Bearer {self._access_token()}", "Content-Type": "image/jpeg"},
                    data=thumbnail.read_bytes(),
                    retry=True,
                )
            except HttpError as exc:
                note = "miniature refusée (la chaîne doit être validée par téléphone pour les miniatures personnalisées)"
                log.warning("YouTube : %s : %s", note, exc)
                detail = f"{detail}; {note}" if detail else note
        return PublishResult(self.name, True, video_id, f"https://www.youtube.com/watch?v={video_id}", detail)

    def _status(self, result: dict[str, Any]) -> str | None:
        privacy = (result.get("status") or {}).get("privacyStatus")
        if self.settings.privacy == "public" and privacy and privacy != "public":
            return f"visibilité : {privacy} (un projet Google non audité ne peut publier qu'en privé)"
        return None


def _next_offset(response: requests.Response) -> int:
    """308 Resume Incomplete: 'Range: bytes=0-N' means N+1 bytes are stored."""
    match = re.match(r"bytes=0-(\d+)", response.headers.get("Range", ""))
    return int(match.group(1)) + 1 if match else 0
