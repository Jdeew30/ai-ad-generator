from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Landscape first: Twitch also serves an auto-cropped portrait rendition we would crop again.
FORMAT = (
    "bv*[height<=1080][format_id!*=portrait]+ba/"
    "b[height<=1080][format_id!*=portrait]/"
    "bv*+ba/b"
)


class DownloadError(Exception):
    def __init__(self, message: str, permanent: bool = False, blocked: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.blocked = blocked  # YouTube's anti-bot check: every other YouTube request of the run fails too


_PERMANENT = re.compile(
    r"no longer available|not found|does not exist|private video|been removed|video unavailable|HTTP Error 404",
    re.IGNORECASE,
)
_BOT_CHECK = re.compile(r"confirm you.re not a bot", re.IGNORECASE)


class _Logger:
    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)


class YtDlp:
    """Thin wrapper around the yt-dlp Python API (listing, metadata, section downloads)."""

    def __init__(self, cookies_file: Path | None = None) -> None:
        self.cookies_file = cookies_file
        self.blocked = False  # YouTube asked to sign in to prove this server is not a bot

    def _options(self, **extra: Any) -> dict[str, Any]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _Logger(),
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            # YouTube needs a JavaScript runtime: deno when installed, else the Node.js already on GitHub runners.
            "js_runtimes": {"deno": {}, "node": {}},
        }
        if self.cookies_file:
            options["cookiefile"] = str(self.cookies_file)
            # Signed in, yt-dlp gives up on a channel page it could not load instead of reading the channel through
            # the API (« Playlists that require authentication… »): the robot only reads public videos.
            options["extractor_args"] = {"youtubetab": {"skip": ["authcheck"]}}
        options.update(extra)
        return options

    def _run(self, url: str, options: dict[str, Any], download: bool) -> dict[str, Any]:
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=download)
                return ydl.sanitize_info(info) if info else {}
        except yt_dlp.utils.DownloadError as exc:
            message = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).removeprefix("ERROR: ")
            message = re.sub(r";?\s*please report this issue on.*", "", message, flags=re.S | re.I).strip()
            blocked = bool(_BOT_CHECK.search(message))
            self.blocked = self.blocked or blocked
            raise DownloadError(message, permanent=bool(_PERMANENT.search(message)), blocked=blocked) from exc

    def list_flat(self, url: str, limit: int) -> list[dict[str, Any]]:
        info = self._run(url, self._options(extract_flat="in_playlist", playlistend=limit, lazy_playlist=True), False)
        return [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)][:limit]

    def info(self, url: str) -> dict[str, Any]:
        return self._run(url, self._options(), False)

    def download(
        self,
        url: str,
        dest_dir: Path,
        name: str,
        *,
        start: float | None = None,
        end: float | None = None,
        exact: bool = True,
    ) -> tuple[Path, dict[str, Any]]:
        """Downloads `url`, or only its section from `start` to `end` (seconds).

        `exact` re-encodes the section so that it starts on the very frame asked for, which takes minutes on a
        long 1080p60 section. Without it the section is copied as is and starts on the keyframe just before
        `start` (up to a couple of seconds earlier on Twitch).
        """
        from yt_dlp.utils import download_range_func

        dest_dir.mkdir(parents=True, exist_ok=True)
        extra: dict[str, Any] = {
            "format": FORMAT,
            "merge_output_format": "mp4",
            "outtmpl": str(dest_dir / f"{name}.%(ext)s"),
            "overwrites": True,
            "noplaylist": True,
        }
        if start is not None or end is not None:
            extra["download_ranges"] = download_range_func(None, [(start or 0.0, end if end is not None else float("inf"))])
            extra["force_keyframes_at_cuts"] = exact
        info = self._run(url, self._options(**extra), True)
        paths = [Path(d["filepath"]) for d in info.get("requested_downloads") or [] if d.get("filepath")]
        path = next((p for p in paths if p.exists()), None)
        if path is None:
            matches = sorted(dest_dir.glob(f"{name}.*"))
            path = next((p for p in matches if p.suffix in {".mp4", ".mkv", ".webm", ".mov"}), None)
        if path is None:
            raise DownloadError(f"yt-dlp n'a produit aucun fichier pour {url}")
        return path, info
