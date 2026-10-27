from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from ..models import Candidate
from .twitch import clip_slug

_YOUTUBE_ID = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})")
_TWITCH_LOGIN = re.compile(r"twitch\.tv/(?!videos/)([A-Za-z0-9_]{3,25})(?:/|$|\?)")


class QueueError(Exception):
    pass


def parse_timestamp(value: Any) -> float | None:
    """'1:23', '01:02:03', '83', 83 or '1h2m3s' -> seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}(\.\d+)?", text):
        seconds = 0.0
        for part in text.split(":"):
            seconds = seconds * 60 + float(part)
        return seconds
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", text)
    if match and any(match.groups()):
        hours, minutes, seconds = match.groups()
        return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)
    raise QueueError(f"horodatage illisible : {value!r} (formats acceptés : 83, 1:23, 1:02:03, 1h2m3s)")


def manual_candidate(url: str, start: Any = None, end: Any = None, title: str | None = None) -> Candidate:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise QueueError(f"URL invalide : {url!r}")
    begin, finish = parse_timestamp(start), parse_timestamp(end)
    if begin is not None and finish is not None and finish <= begin:
        raise QueueError(f"{url} : la fin ({end}) doit être après le début ({start})")
    slug = clip_slug(url) if "twitch.tv" in url else None
    video = _YOUTUBE_ID.search(url)
    if slug and begin is None and finish is None:
        key = f"twitch:{slug}"
    elif video and begin is not None and finish is not None:
        key = f"youtube:{video.group(1)}@{int(begin)}-{int(finish)}"
    else:
        digest = hashlib.sha1(f"{url}|{begin}|{finish}".encode()).hexdigest()[:16]
        key = f"manual:{digest}"
    login_match = _TWITCH_LOGIN.search(url) if "twitch.tv" in url and "clips.twitch.tv" not in url else None
    login = login_match.group(1).lower() if login_match else ""
    return Candidate(
        key=key,
        source="manual",
        url=url,
        title=(title or "").strip(),
        streamer=login,
        streamer_login=login,
        channel_url=f"https://www.twitch.tv/{login}" if login else "",
        duration=(finish - begin) if begin is not None and finish is not None else 0.0,
        start=begin,
        end=finish,
        manual=True,
    )


def load_queue(path: Path) -> list[Candidate]:
    """Reads queue.yaml: URLs added by hand, processed before anything discovered."""
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise QueueError(f"{path.name} : un objet avec une liste 'items' est attendu")
    items = data.get("items") or []
    if not isinstance(items, list):
        raise QueueError(f"{path.name} : 'items' doit être une liste")
    candidates = []
    for index, item in enumerate(items):
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict) or not item.get("url"):
            raise QueueError(f"{path.name} : l'élément {index + 1} n'a pas d'url")
        unknown = set(item) - {"url", "start", "end", "title"}
        if unknown:
            raise QueueError(f"{path.name} : clé(s) inconnue(s) dans l'élément {index + 1} : {', '.join(sorted(unknown))}")
        candidates.append(manual_candidate(item["url"], item.get("start"), item.get("end"), item.get("title")))
    return candidates
