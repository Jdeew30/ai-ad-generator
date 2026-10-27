from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import Secrets
from ..http import Http, HttpError
from ..media import MediaError, probe, run_ffmpeg
from ..models import Candidate, CompilationMeta, PublishResult, ShortMeta
from . import PublishError

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org"
MAX_UPLOAD = 49 * 1024 * 1024  # bots may send files up to 50 MB


def _links(results: list[PublishResult]) -> list[str]:
    return [f"{r.platform} : {r.url}" for r in results if r.ok and r.url]


class TelegramPublisher:
    """Sends each finished video to your phone with a ready-to-paste caption (handy for manual TikTok posts)."""

    name = "telegram"

    def __init__(self, secrets: Secrets, http: Http, *, api_url: str = API_URL) -> None:
        self.http = http
        self.chat_id = secrets.telegram_chat_id
        self.base = f"{api_url.rstrip('/')}/bot{secrets.telegram_bot_token}"

    def _send(self, method: str, data: dict[str, str], files: dict[str, tuple[str, bytes, str]] | None = None) -> dict:
        try:
            response = self.http.post(f"{self.base}/{method}", data={"chat_id": self.chat_id, **data}, files=files, timeout=300)
        except HttpError as exc:
            raise PublishError(f"Telegram {method} : {exc}") from exc
        body = response.json()
        if not body.get("ok"):
            raise PublishError(f"Telegram {method} : {body.get('description', 'erreur')}")
        return body.get("result") or {}

    def message(self, text: str) -> None:
        self._send("sendMessage", {"text": text[:4000], "disable_web_page_preview": "true"})

    def _fit(self, video: Path) -> Path:
        if video.stat().st_size <= MAX_UPLOAD:
            return video
        smaller = video.with_name(f"{video.stem}.telegram.mp4")
        duration = max(probe(video).duration, 1.0)
        kbps = int(MAX_UPLOAD * 8 * 0.9 / duration / 1000) - 128
        if kbps < 300:
            raise MediaError("vidéo trop longue pour Telegram")
        run_ffmpeg(
            ["-i", str(video), "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
             "-bufsize", f"{kbps * 2}k", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(smaller)]
        )  # fmt: skip
        return smaller

    def publish_short(self, video: Path, meta: ShortMeta, candidate: Candidate, published: list[PublishResult]) -> PublishResult:
        tiktok_text = f"{meta.title}\n\n{meta.hashtag_line()}\n🎥 {candidate.credit}"
        lines = [f"🎬 {meta.title}", "", "Texte à coller (TikTok) :", tiktok_text, ""]
        lines += _links(published) or ["(pas encore publié ailleurs)"]
        text = "\n".join(lines)
        try:
            to_send = self._fit(video)
        except MediaError as exc:
            self.message(f"{text}\n\n⚠️ Vidéo non envoyée : {exc}")
            return PublishResult(self.name, True, detail=f"texte seul ({exc})")
        try:
            info = probe(to_send)
            result = self._send(
                "sendVideo",
                {
                    "caption": text[:1024],
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
        return PublishResult(self.name, True, str(result.get("message_id", "")) or None)

    def publish_compilation(
        self, video: Path, meta: CompilationMeta, description: str, thumbnail: Path | None, published: list[PublishResult]
    ) -> PublishResult:
        text = "\n".join([f"🏆 {meta.title}", "", *(_links(published) or [f"Vidéo prête : {video.name} (artefact du workflow)"])])
        if thumbnail is not None and thumbnail.exists():
            result = self._send(
                "sendPhoto", {"caption": text[:1024]}, files={"photo": (thumbnail.name, thumbnail.read_bytes(), "image/jpeg")}
            )
        else:
            result = self._send("sendMessage", {"text": text[:4000]})
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
