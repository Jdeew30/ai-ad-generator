from __future__ import annotations

import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import Secrets
from ..http import Http, HttpError
from ..media import MediaError, probe, run_ffmpeg
from ..models import Candidate, CompilationMeta, PublishResult, ShortMeta
from . import PublishError

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org"
MAX_UPLOAD = 49 * 1024 * 1024  # bots may send files up to 50 MB
LOCAL_MAX_UPLOAD = 1990 * 1024 * 1024  # through a local Bot API server: up to 2 GB
SEND_ATTEMPTS = 4  # a send refused for flood (429), a server error or the network is tried again
PAUSES = (3.0, 10.0, 30.0)
PARIS = ZoneInfo("Europe/Paris")


def _links(results: list[PublishResult]) -> list[str]:
    return [f"{r.platform} : {r.url}" for r in results if r.ok and r.url]


def run_url() -> str | None:
    """The page of the current GitHub Actions run, whose "Artifacts" section holds the finished videos."""
    server, repository, run = (os.environ.get(name) for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repository}/actions/runs/{run}" if server and repository and run else None


class TelegramPublisher:
    """Sends each finished video to your phone with a ready-to-paste caption (handy for manual TikTok posts)."""

    name = "telegram"

    def __init__(self, secrets: Secrets, http: Http, *, api_url: str | None = None) -> None:
        self.http = http
        self.chat_id = secrets.telegram_chat_id
        api_url = (api_url or API_URL).rstrip("/")
        self.base = f"{api_url}/bot{secrets.telegram_bot_token}"
        self.max_upload = MAX_UPLOAD if api_url == API_URL else LOCAL_MAX_UPLOAD

    def _send(self, method: str, data: dict[str, str], files: dict[str, tuple[str, bytes, str]] | None = None) -> dict:
        """One Bot API call. Flood control (429, « retry after N »), server errors and the network are waited out."""
        for attempt in range(SEND_ATTEMPTS):
            try:
                response = self.http.post(f"{self.base}/{method}", data={"chat_id": self.chat_id, **data}, files=files,
                                          timeout=300)
            except HttpError as exc:
                if attempt + 1 < SEND_ATTEMPTS and (exc.status is None or exc.status == 429 or exc.status >= 500):
                    pause = _retry_after(exc.body) if exc.status == 429 else PAUSES[min(attempt, len(PAUSES) - 1)]
                    log.warning("Telegram %s : %s — nouvel essai dans %.0f s", method, str(exc)[:200], pause)
                    self.http.sleep(pause)
                    continue
                raise PublishError(f"Telegram {method} : {exc}") from exc
            body = response.json()
            if not body.get("ok"):
                raise PublishError(f"Telegram {method} : {body.get('description', 'erreur')}")
            return body.get("result") or {}
        raise PublishError(f"Telegram {method} : échec")

    def message(self, text: str, *, html_text: bool = False) -> None:
        data = {"text": text[:4000], "disable_web_page_preview": "true"}
        if html_text:
            data["parse_mode"] = "HTML"
        self._send("sendMessage", data)

    def _fit(self, video: Path) -> Path:
        """The video, or a lighter copy under the upload limit (the bitrate is spread over its length)."""
        if video.stat().st_size <= self.max_upload:
            return video
        smaller = video.with_name(f"{video.stem}.telegram.mp4")
        duration = max(probe(video).duration, 1.0)
        kbps = int(self.max_upload * 8 * 0.9 / duration / 1000) - 128
        if kbps < 300:
            raise MediaError("vidéo trop longue pour Telegram")
        run_ffmpeg(
            ["-i", str(video), "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
             "-bufsize", f"{kbps * 2}k", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(smaller)]
        )  # fmt: skip
        return smaller

    def verify(self) -> str:
        self.message("✅ Clipper est bien connecté : les prochaines vidéos arriveront ici.")
        return "message de test envoyé"

    def publish_short(self, video: Path, meta: ShortMeta, candidate: Candidate, published: list[PublishResult]) -> PublishResult:
        """The video with its card (who, where, when, numbers, Claude's mark), then the text to paste on TikTok."""
        tiktok_text = f"{meta.title}\n\n{meta.hashtag_line()}\n🎥 {candidate.credit}"
        lines = card(meta, candidate)
        if _platform(candidate) == "YouTube":
            lines.append("⚠️ Extrait d'une vidéo YouTube : poste-le plutôt sur TikTok (sur YouTube, Content ID le repère presque toujours).")
        lines += _links(published) or ["(pas encore publié ailleurs)"]
        text = "\n".join(lines)
        paste = f"📋 Texte à coller (TikTok), touche-le pour le copier :\n<pre>{html.escape(tiktok_text, quote=False)}</pre>"
        try:
            to_send = self._fit(video)
        except MediaError as exc:
            self.message(f"{text}\n\n⚠️ Vidéo non envoyée : {exc}")
            self._paste(paste, tiktok_text)
            return PublishResult(self.name, True, detail=f"texte seul ({exc})")
        try:
            info = probe(to_send)
            result = self._send(
                "sendVideo",
                {
                    "caption": _fit_caption(text),
                    "supports_streaming": "true",
                    "width": str(info.width),
                    "height": str(info.height),
                    "duration": str(int(info.duration)),
                },
                files={"video": (f"{candidate.streamer_login or 'clip'}.mp4", to_send.read_bytes(), "video/mp4")},
            )
        finally:
            if to_send != video:  # the lighter copy only existed for Telegram
                to_send.unlink(missing_ok=True)
        if len(text) > 1024:
            self.message(text)
        self._paste(paste, tiktok_text)
        return PublishResult(self.name, True, str(result.get("message_id", "")) or None)

    def _paste(self, paste: str, plain: str) -> None:
        """The text to paste, in a block Telegram copies with a tap; as plain text if the formatting is refused."""
        try:
            self.message(paste, html_text=True)
        except PublishError as exc:
            log.warning("Texte à coller en bloc refusé (%s) : envoyé tel quel.", exc)
            self.message(f"📋 Texte à coller (TikTok) :\n{plain}")

    def publish_compilation(
        self, video: Path, meta: CompilationMeta, description: str, thumbnail: Path | None, published: list[PublishResult]
    ) -> PublishResult:
        links = _links(published)
        run = run_url()
        if links:
            where = links
        elif run:  # a best-of weighs hundreds of MB, far over the 50 MB a bot may send: the workflow sends its link next
            where = ["⬇️ Vidéo trop lourde pour Telegram : son lien de téléchargement arrive juste après."]
        else:
            where = [f"Vidéo prête : {video.name} (artefact du workflow)"]
        text = "\n".join([f"🏆 {meta.title}", "", *where])
        if thumbnail is not None and thumbnail.exists():
            result = self._send(
                "sendPhoto", {"caption": text[:1024]}, files={"photo": (thumbnail.name, thumbnail.read_bytes(), "image/jpeg")}
            )
        else:
            result = self._send("sendMessage", {"text": text[:4000]})
        if not links:  # to post it by hand: the texts, chapters and credits included
            self.message(f"À coller sur YouTube\n\nTitre :\n{meta.title}\n\nDescription :\n{description}")
        return PublishResult(self.name, True, str(result.get("message_id", "")) or None)

    def chats(self) -> list[tuple[str, str]]:
        """Chats that wrote to the bot recently (to find TELEGRAM_CHAT_ID)."""
        try:
            response = self.http.get(f"{self.base}/getUpdates")
        except HttpError as exc:
            raise PublishError(f"Telegram : {exc}") from exc
        found: dict[str, str] = {}
        for update in response.json().get("result", []):
            chat = (update.get("message") or update.get("channel_post") or {}).get("chat") or {}
            if "id" in chat:
                found[str(chat["id"])] = chat.get("title") or chat.get("username") or chat.get("first_name") or json.dumps(chat)
        return list(found.items())


FRESH_HOURS = 12  # a moment younger than this is said to be posted right away: the first accounts get the views


def card(meta: ShortMeta, candidate: Candidate, *, now: datetime | None = None) -> list[str]:
    """Who, where, when and how much: the facts of a short, then Claude's mark (an estimate, not a measure)."""
    platform = _platform(candidate)
    who = candidate.creator or candidate.streamer or candidate.streamer_login or "?"
    channel = candidate.channel_url.removeprefix("https://").removeprefix("www.") if candidate.channel_url else ""
    if candidate.creator and candidate.streamer and candidate.streamer != candidate.creator:  # one of its other channels
        channel = f"chaîne {candidate.streamer}" + (f" ({channel})" if channel else "")
    lines = [f"🎬 {meta.title}", f"👤 {who} · {platform}" + (f" · {channel}" if channel else "")]
    if candidate.title:
        lines.append(f"📝 « {candidate.title} »")
    when = [f"📅 {_date(candidate.created_at)}" if candidate.created_at else "", f"⏱ {_length(candidate)}"]
    if candidate.start is not None and candidate.end is not None and platform == "YouTube":
        when.append(f"passage {_clock(candidate.start)}–{_clock(candidate.end)}")
    lines.append(" · ".join(part for part in when if part))
    if candidate.created_at and not candidate.manual:
        age = ((now or datetime.now(timezone.utc)) - candidate.created_at).total_seconds() / 3600
        if 0 <= age < FRESH_HOURS:
            lines.append(f"⚡ Tout frais (il y a {max(1, round(age))} h) : à poster maintenant, avant les autres comptes")
    numbers = [f"👁 {_count(candidate.views)} vues" if candidate.views else "",
               f"👍 {_count(candidate.likes)}" if candidate.likes else "",
               f"💬 {_count(candidate.comments)}" if candidate.comments else ""]  # fmt: skip
    if candidate.pace:
        numbers.append(f"📈 {_count(int(candidate.pace))} vues/h")
    if any(numbers):
        lines.append(" · ".join(part for part in numbers if part))
    if meta.ai and meta.score:
        reason = meta.reason if len(meta.reason) <= 260 else meta.reason[:259].rstrip() + "…"
        lines.append(f"🔥 Score viral estimé : {meta.score:.0f}/100" + (f" — {reason}" if reason else ""))
    lines.append(f"🔗 {source_link(candidate)}")
    return lines


def source_link(candidate: Candidate) -> str:
    """The original: a YouTube video opens right at the passage."""
    match = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/)([\w-]{11})", candidate.url)
    if match and candidate.start is not None:
        return f"https://www.youtube.com/watch?v={match.group(1)}&t={int(candidate.start)}s"
    return candidate.url


def _platform(candidate: Candidate) -> str:
    if candidate.source == "youtube" or re.search(r"youtube\.com|youtu\.be", candidate.url):
        return "YouTube"
    return "Twitch" if candidate.source == "twitch" or "twitch.tv" in candidate.url else candidate.source.capitalize()


def _date(at: datetime) -> str:
    return at.astimezone(PARIS).strftime("%d/%m/%Y %H:%M")


def _length(candidate: Candidate) -> str:
    seconds = candidate.end - candidate.start if candidate.start is not None and candidate.end is not None else candidate.duration
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.1f} min".replace(".", ",")


def _clock(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def _count(number: int | None) -> str:
    return f"{number or 0:,}".replace(",", "\u202f")  # 1 875 147


def _retry_after(body: str) -> float:
    """Telegram's « retry after N » on a 429, 30 s at most to wait; 5 s when it says nothing."""
    try:
        seconds = float((json.loads(body).get("parameters") or {}).get("retry_after") or 5)
    except (ValueError, AttributeError):
        seconds = 5.0
    return min(max(seconds, 1.0), 30.0)


def _fit_caption(text: str) -> str:
    return text if len(text) <= 1024 else text[:1023] + "…"
