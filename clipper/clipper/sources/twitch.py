from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Config, Secrets
from ..http import Http, HttpError
from ..models import Candidate, parse_time
from ..selection import excluded_title
from ..ytdlp import DownloadError, YtDlp

log = logging.getLogger(__name__)

HELIX_URL = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

_SLUG = re.compile(r"(?:clips\.twitch\.tv/(?:embed\?(?:.*&)?clip=)?|/clip/)([A-Za-z0-9_-]+)")


class SourceError(Exception):
    """A whole source is unreachable (network, credentials): the run reports it as a failure."""


def clip_slug(url: str) -> str | None:
    match = _SLUG.search(url)
    return match.group(1) if match else None


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


class TwitchApi:
    """Official Helix API with an app access token (client credentials)."""

    def __init__(self, client_id: str, client_secret: str, http: Http, *, base_url: str = HELIX_URL, token_url: str = TOKEN_URL) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.token_url = token_url
        self._token: str | None = None

    def _access_token(self, refresh: bool = False) -> str:
        if self._token and not refresh:
            return self._token
        try:
            response = self.http.post(
                self.token_url,
                data={"client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "client_credentials"},
                retry=True,
            )
        except HttpError as exc:
            if exc.status in {400, 401, 403}:
                raise HttpError("Twitch a refusé TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET : vérifiez les secrets.", exc.status) from exc
            raise
        self._token = response.json()["access_token"]
        return self._token

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            headers = {"Client-Id": self.client_id, "Authorization": f"Bearer {self._access_token(refresh=attempt > 0)}"}
            try:
                return self.http.get(f"{self.base_url}/{path}", params=params, headers=headers).json()
            except HttpError as exc:
                if exc.status == 401 and attempt == 0:
                    continue  # app tokens expire after ~60 days: fetch a new one once
                raise
        raise AssertionError("unreachable")

    def users(self, logins: list[str]) -> dict[str, dict[str, Any]]:
        users: dict[str, dict[str, Any]] = {}
        for batch in _chunks(sorted(set(logins)), 100):
            for user in self.get("users", {"login": batch}).get("data", []):
                users[user["login"].lower()] = user
        return users

    def users_by_id(self, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        users: dict[str, dict[str, Any]] = {}
        for batch in _chunks(sorted({i for i in ids if i}), 100):
            for user in self.get("users", {"id": batch}).get("data", []):
                users[user["id"]] = user
        return users

    def clips(self, broadcaster_id: str, started_at: datetime, ended_at: datetime, first: int) -> list[dict[str, Any]]:
        params = {
            "broadcaster_id": broadcaster_id,
            "started_at": _rfc3339(started_at),
            "ended_at": _rfc3339(ended_at),
            "first": max(1, min(first, 100)),
        }
        return self.get("clips", params).get("data", [])

    def clip(self, slug: str) -> dict[str, Any] | None:
        data = self.get("clips", {"id": slug}).get("data", [])
        return data[0] if data else None

    def game_clips(self, game_id: str, started_at: datetime, ended_at: datetime, first: int) -> list[dict[str, Any]]:
        """The most viewed clips of a category, every channel and language mixed."""
        params = {"game_id": game_id, "started_at": _rfc3339(started_at), "ended_at": _rfc3339(ended_at), "first": max(1, min(first, 100))}
        return self.get("clips", params).get("data", [])

    def top_games(self, first: int) -> list[dict[str, Any]]:
        """The categories watched the most right now."""
        return self.get("games/top", {"first": max(1, min(first, 100))}).get("data", [])

    def streams(self, language: str, first: int) -> list[dict[str, Any]]:
        params = {"language": language, "type": "live", "first": max(1, min(first, 100))}
        return self.get("streams", params).get("data", [])

    def videos(self, ids: Iterable[str]) -> dict[str, str]:
        """Titles of the lives (VODs) the clips were cut from, by video id."""
        titles: dict[str, str] = {}
        for batch in _chunks(sorted({i for i in ids if i}), 100):
            try:
                data = self.get("videos", {"id": batch}).get("data", [])
            except HttpError as exc:
                if exc.status == 404:  # none of these replays exists any more
                    continue
                raise
            titles.update((video["id"], video.get("title") or "") for video in data)
        return titles

    def games(self, ids: Iterable[str]) -> dict[str, str]:
        names: dict[str, str] = {}
        for batch in _chunks(sorted({i for i in ids if i}), 100):
            for game in self.get("games", {"id": batch}).get("data", []):
                names[game["id"]] = game["name"]
        return names


def _display(config: Config, login: str, fallback: str) -> str:
    return config.override_for(login).name or fallback


def candidate_from_helix(clip: dict[str, Any], login: str, games: dict[str, str], config: Config) -> Candidate:
    offset = clip.get("vod_offset")
    return Candidate(
        key=f"twitch:{clip['id']}",
        source="twitch",
        url=clip.get("url") or f"https://clips.twitch.tv/{clip['id']}",
        title=(clip.get("title") or "").strip(),
        streamer=_display(config, login, clip.get("broadcaster_name") or login),
        streamer_login=login,
        channel_url=f"https://www.twitch.tv/{login}",
        views=int(clip.get("view_count") or 0),
        duration=float(clip.get("duration") or 0.0),
        created_at=parse_time(clip.get("created_at")),
        game=games.get(clip.get("game_id") or ""),
        clipper=clip.get("creator_name") or None,
        vod_id=clip.get("video_id") or None,
        vod_offset=float(offset) if offset is not None else None,
        thumbnail_url=clip.get("thumbnail_url") or None,
    )


def candidate_from_listing(entry: dict[str, Any], login: str, config: Config) -> Candidate | None:
    url = entry.get("url") or ""
    slug = clip_slug(url)
    if not slug:
        return None
    return Candidate(
        key=f"twitch:{slug}",
        source="twitch",
        url=url,
        title=(entry.get("title") or "").strip(),
        streamer=_display(config, login, login),
        streamer_login=login,
        channel_url=f"https://www.twitch.tv/{login}",
        views=int(entry.get("view_count") or 0),
        duration=float(entry.get("duration") or 0.0),
        created_at=parse_time(entry.get("timestamp")),
        thumbnail_url=entry.get("thumbnail") or None,
    )


def discover_twitch(
    config: Config,
    secrets: Secrets,
    http: Http,
    ytdlp: YtDlp,
    *,
    now: datetime,
    window: timedelta,
    per_streamer: int | None = None,
    api: TwitchApi | None = None,
    memory: dict[str, str] | None = None,
) -> list[Candidate]:
    """Clips of the followed channels and, with the API keys, of the rest of French Twitch.

    `memory` (login -> last time seen) keeps the channels found live or in a category's top clips, so that
    their clips are still looked at in the next runs, when they are offline.
    """
    if not config.twitch.enabled:
        return []
    per_streamer = per_streamer or config.twitch.clips_per_streamer
    logins = [login for login in config.twitch.streamers if not config.is_blocked(login)]
    if api is None and secrets.twitch_api:
        api = TwitchApi(secrets.twitch_client_id or "", secrets.twitch_client_secret or "", http)
    if api is not None:
        return _discover_helix(config, api, logins, now=now, window=window, per_streamer=per_streamer, memory=memory)
    log.info("Clés API Twitch absentes : liste des clips via yt-dlp (pas de découverte des lives).")
    return _discover_listing(config, ytdlp, logins, now=now, window=window, per_streamer=per_streamer)


def _sample(logins: list[str], count: int = 12) -> str:
    return ", ".join(logins[:count]) + ("…" if len(logins) > count else "")


def _discover_helix(
    config: Config,
    api: TwitchApi,
    logins: list[str],
    *,
    now: datetime,
    window: timedelta,
    per_streamer: int,
    memory: dict[str, str] | None = None,
) -> list[Candidate]:
    settings = config.twitch
    seen_at = now.isoformat()
    if settings.discover_live:
        try:
            streams = api.streams(settings.discover_language, settings.discover_limit)
        except HttpError as exc:
            log.warning("Découverte des lives impossible : %s", exc)
            streams = []
        live = [s["user_login"].lower() for s in streams if not config.is_blocked(s["user_login"].lower())]
        if memory is not None:
            memory.update({login: seen_at for login in live})
        added = [login for login in live if login not in logins]
        if added:
            log.info("Lives %s ajoutés : %d chaînes (%s)", settings.discover_language, len(added), _sample(added))
        logins = logins + added
    if memory and settings.remember_live > 0:
        recent = sorted(memory, key=lambda login: memory[login], reverse=True)
        remembered = [login for login in recent if login not in logins and not config.is_blocked(login)]
        remembered = remembered[: settings.remember_live]
        if remembered:
            log.info("Chaînes vues ces 14 derniers jours : %d de plus (%s)", len(remembered), _sample(remembered))
        logins = logins + remembered
    if not logins:
        return []
    try:
        users = api.users(logins)
    except HttpError as exc:
        raise SourceError(f"API Twitch injoignable : {exc}") from exc
    missing = sorted(set(logins) - set(users))
    if missing:
        log.warning("Chaînes Twitch introuvables (login à corriger dans config.yaml) : %s", ", ".join(missing))
    clips: list[tuple[dict[str, Any], str]] = []
    failures = 0
    for login, user in users.items():
        try:
            found = api.clips(user["id"], now - window, now, per_streamer)
        except HttpError as exc:
            log.warning("Clips de %s indisponibles : %s", login, exc)
            failures += 1
            continue
        clips.extend((clip, login) for clip in found)
    if users and failures == len(users):
        raise SourceError("aucun clip Twitch récupérable (API en panne ?)")
    if settings.discover_categories > 0:
        known = {clip["id"] for clip, _ in clips}
        extra = _category_clips(config, api, now=now, window=window, known=known)
        if memory is not None:
            memory.update({login: seen_at for _, login in extra})
        clips.extend(extra)
    if settings.skip_live_words:
        clips = _skip_lives(config, api, clips)
    try:
        games = api.games(clip.get("game_id") or "" for clip, _ in clips)
    except HttpError as exc:
        log.warning("Noms des jeux indisponibles : %s", exc)
        games = {}
    return [candidate_from_helix(clip, login, games, config) for clip, login in clips]


def clips_around(
    api: TwitchApi, slug: str, minutes: float, channel: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A clip and every clip made within `minutes` of it, oldest first: on its own channel, or on `channel`.

    Viewers often clip the same moment several times, some longer or starting earlier: when the live's replay is
    gone, they are the only way to show more of it. Another streamer who was there gives another point of view.
    """
    clip = api.clip(slug)
    if clip is None:
        raise SourceError(f"clip Twitch introuvable : {slug}")
    created = parse_time(clip.get("created_at"))
    if created is None:
        raise SourceError(f"date du clip {slug} inconnue")
    broadcaster = clip["broadcaster_id"]
    if channel:
        login = channel.strip().lstrip("@").lower()
        user = api.users([login]).get(login)
        if user is None:
            raise SourceError(f"chaîne Twitch introuvable : {channel}")
        broadcaster = user["id"]
    margin = timedelta(minutes=minutes)
    found = api.clips(broadcaster, created - margin, created + margin, 100)
    if broadcaster == clip["broadcaster_id"] and not any(other.get("id") == clip["id"] for other in found):
        found.append(clip)
    return clip, sorted(found, key=lambda other: other.get("created_at") or "")


def _skip_lives(config: Config, api: TwitchApi, clips: list[tuple[dict[str, Any], str]]) -> list[tuple[dict[str, Any], str]]:
    """Drops, before any download, the clips cut from a live titled after a co-stream or a watch party ("Worlds"...).

    Their picture is mostly the official broadcast, which the streamer does not own: Claude rejects them anyway.
    """
    try:
        titles = api.videos(clip.get("video_id") or "" for clip, _ in clips)
    except HttpError as exc:
        log.warning("Titres des lives indisponibles : %s", exc)
        return clips
    words = config.twitch.skip_live_words
    kept = [(clip, login) for clip, login in clips if not excluded_title(titles.get(clip.get("video_id") or "", ""), words)]
    if len(kept) < len(clips):
        log.info("Clips de co-stream ou de watch party écartés (titre du live) : %d", len(clips) - len(kept))
    return kept


def _category_clips(
    config: Config, api: TwitchApi, *, now: datetime, window: timedelta, known: set[str]
) -> list[tuple[dict[str, Any], str]]:
    """The week's most viewed French clips in the categories watched the most, whoever the channel."""
    language = config.twitch.discover_language
    try:
        categories = api.top_games(config.twitch.discover_categories)
    except HttpError as exc:
        log.warning("Catégories populaires indisponibles : %s", exc)
        return []
    found: list[dict[str, Any]] = []
    for category in categories:
        try:
            clips = api.game_clips(category["id"], now - window, now, 100)
        except HttpError as exc:
            log.warning("Clips de la catégorie %s indisponibles : %s", category.get("name"), exc)
            continue
        found += [clip for clip in clips if clip.get("language") == language and clip["id"] not in known]
        known.update(clip["id"] for clip in clips)
    if not found:
        return []
    try:
        users = api.users_by_id(clip.get("broadcaster_id") or "" for clip in found)
    except HttpError as exc:
        log.warning("Chaînes des clips populaires introuvables : %s", exc)
        return []
    result = []
    for clip in found:
        user = users.get(clip.get("broadcaster_id") or "")
        if user and not config.is_blocked(user["login"].lower()):
            result.append((clip, user["login"].lower()))
    channels = sorted({login for _, login in result})
    log.info("Catégories populaires : %d clips %s de la semaine, %d chaînes (%s)", len(result), language, len(channels), _sample(channels))
    return result


def _discover_listing(
    config: Config, ytdlp: YtDlp, logins: list[str], *, now: datetime, window: timedelta, per_streamer: int
) -> list[Candidate]:
    hours = window.total_seconds() / 3600
    period = "24hr" if hours <= 24 else "7d" if hours <= 24 * 7 else "30d"
    candidates: list[Candidate] = []
    errors: list[str] = []
    for login in logins:
        try:
            entries = ytdlp.list_flat(f"https://www.twitch.tv/{login}/clips?filter=clips&range={period}", per_streamer)
        except DownloadError as exc:
            log.warning("Clips de %s indisponibles : %s", login, exc)
            errors.append(str(exc))
            continue
        for entry in entries:
            candidate = candidate_from_listing(entry, login, config)
            if candidate is None:
                continue
            if candidate.created_at and candidate.created_at < now - window:
                continue
            candidates.append(candidate)
    if logins and len(errors) == len(logins):
        raise SourceError(f"Twitch injoignable pour toutes les chaînes (dernière erreur : {errors[-1]})")
    return candidates
