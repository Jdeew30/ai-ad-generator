"""YouTube watch: every channel's RSS feed at each run, a database of creators and of their channels, and a
pre-score of each new video to decide which ones are worth looking into.

RSS feeds (youtube.com/feeds/videos.xml?channel_id=…) are public, cost no API quota and need no cookies: they give
the 15 latest uploads of a channel with their views and likes. The few calls that need yt-dlp (the channel's id
once, then the details and moments of the best videos only) stay rare.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import statistics
import tempfile
import unicodedata
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..http import Http, HttpError
from ..models import parse_time

log = logging.getLogger(__name__)

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
       "media": "http://search.yahoo.com/mrss/"}  # fmt: skip
MAX_ERRORS = 3  # a channel that cannot be read so many runs in a row is set aside (« introuvable »)
SNAPSHOTS = 6  # view counts kept per video, to measure how fast it grows
# Words of titles that often go with a moment worth a short (a reaction, a fight, a first, a fail…).
_HOOKS = re.compile(
    r"r[ée]agi|r[ée]action|clash|embrouille|d[ée]bat|dispute|cash|g[ée]nant|malaise|choqu|incroyable|fou\b|folie|"
    r"jamais|premi[eè]re fois|pire|meilleur|record|d[ée]fi|challenge|\b\d+ ?h\b|piege|pi[eè]ge|prank|larme|pleur|"
    r"[ée]motion|craqu|rage|catastrophe|vrai|v[ée]rit[ée]|secret|scandale|interdit|survi|perdu|gagn|battle|vs\b",
    re.IGNORECASE,
)


@dataclass
class FeedVideo:
    """An upload as a channel's RSS feed lists it."""

    id: str
    channel_id: str
    title: str
    published: datetime | None
    views: int | None
    likes: int | None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


def parse_feed(text: str) -> list[FeedVideo]:
    """The uploads of a YouTube RSS feed, newest first."""
    root = ElementTree.fromstring(text)
    videos: list[FeedVideo] = []
    for entry in root.findall("atom:entry", _NS):
        video_id = entry.findtext("yt:videoId", default="", namespaces=_NS)
        if not video_id:
            continue
        group = entry.find("media:group", _NS)
        community = group.find("media:community", _NS) if group is not None else None
        stats = community.find("media:statistics", _NS) if community is not None else None
        rating = community.find("media:starRating", _NS) if community is not None else None
        videos.append(FeedVideo(
            id=video_id,
            channel_id=entry.findtext("yt:channelId", default="", namespaces=_NS),
            title=(entry.findtext("atom:title", default="", namespaces=_NS) or "").strip(),
            published=parse_time(entry.findtext("atom:published", default=None, namespaces=_NS)),
            views=_int(stats.get("views")) if stats is not None else None,
            likes=_int(rating.get("count")) if rating is not None else None,
        ))  # fmt: skip
    return videos


def read_feed(http: Http, channel_id: str) -> list[FeedVideo]:
    response = http.get(FEED.format(channel_id), timeout=20)
    try:
        return parse_feed(response.text)
    except ElementTree.ParseError as exc:
        raise HttpError(f"flux RSS illisible ({exc})") from exc


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def plain(text: str) -> str:
    """'Maxime Biaggi Gaming' -> 'maximebiaggigaming'."""
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


# --- creators -----------------------------------------------------------------------------------------------------


class Creators:
    """The creators watched and their channels, kept next to the state (creators.json on the state branch).

    A creator has one main channel or more, and secondary channels. A secondary channel is only watched once it is
    verified: listed by the owner, or titled « <name> <suffix> » with enough subscribers *and* its description naming
    the creator's main channel. Each channel keeps its YouTube id, address, title, subscribers, categories, when it
    was last read and whether it is active.
    """

    VERSION = 1

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        data = data or {}
        self.creators: dict[str, dict[str, Any]] = data.get("creators", {})
        self.lookups: dict[str, str] = data.get("lookups", {})  # Twitch login -> when its YouTube channel was looked for

    @classmethod
    def load(cls, path: Path) -> Creators:
        if not path.exists():
            return cls(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != cls.VERSION:
            raise ValueError(f"{path} : version inconnue ({data.get('version')!r})")
        return cls(path, data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".creators-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": self.VERSION, "creators": self.creators, "lookups": self.lookups}, handle,
                          ensure_ascii=False, indent=1, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # The channels of every creator, flattened: (creator key, channel key, channel).
    def channels(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [(key, ref, channel) for key, creator in self.creators.items()
                for ref, channel in creator.get("channels", {}).items()]  # fmt: skip

    def add_creator(self, ref: str, *, source: str, verified: str, now: datetime, name: str | None = None,
                    categories: list[str] | None = None) -> str:
        """The creator whose main channel is `ref` (« @Squeezie »), added if new; its key."""
        key = key_of(ref)
        creator = self.creators.setdefault(key, {
            "name": name or ref.strip().rstrip("/").rsplit("/", 1)[-1].lstrip("@"), "categories": list(categories or []),
            "status": "active", "source": source, "added": now.isoformat(), "channels": {},
        })  # fmt: skip
        creator["channels"].setdefault(key, _channel(ref, role="main", verified=verified, now=now))
        return key

    def add_secondary(self, creator_key: str, ref: str, *, verified: str, now: datetime) -> bool:
        """A secondary channel of a creator, once verified; False when it is already known (as any creator's)."""
        if any(key_of(ref) == other or key_of(ref) == key_of(channel.get("ref", "")) for _, other, channel in self.channels()):
            return False
        self.creators[creator_key]["channels"][key_of(ref)] = _channel(ref, role="secondary", verified=verified, now=now)
        return True

    def find_channel(self, channel_id: str) -> tuple[str, dict[str, Any]] | None:
        for key, _ref, channel in self.channels():
            if channel.get("id") == channel_id:
                return key, channel
        return None

    def sync(self, refs: list[str], now: datetime) -> None:
        """The owner's list: each @handle is a creator's main channel; one taken off the list stops being watched."""
        listed = {key_of(ref) for ref in refs}
        for ref in refs:
            key = key_of(ref)
            if key not in self.creators:
                self.add_creator(ref, source="config", verified="liste du propriétaire", now=now)
            creator = self.creators[key]
            if creator.get("source") == "config" and creator.get("status") == "removed":
                creator["status"] = "active"
        for key, creator in self.creators.items():
            if creator.get("source") == "config" and key not in listed:
                creator["status"] = "removed"

    def watched(self) -> list[tuple[str, dict[str, Any]]]:
        """(creator key, channel) of every channel to read: active creators, active and verified channels."""
        return [(key, channel) for key, _ref, channel in self.channels()
                if self.creators[key].get("status") == "active" and channel.get("status") == "active"
                and channel.get("verified")]  # fmt: skip

    def summary(self) -> dict[str, int]:
        watched = self.watched()
        return {
            "creators": sum(1 for creator in self.creators.values() if creator.get("status") == "active"),
            "channels": len(watched),
            "with_id": sum(1 for _, channel in watched if channel.get("id")),
            "secondary": sum(1 for _, channel in watched if channel.get("role") == "secondary"),
            "set_aside": sum(1 for _, _ref, channel in self.channels() if channel.get("status") != "active"),
        }


def key_of(ref: str) -> str:
    """'@Squeezie' or 'https://www.youtube.com/@Squeezie' -> 'squeezie': how a channel or a creator is filed."""
    return ref.strip().rstrip("/").rsplit("/", 1)[-1].lstrip("@").lower()


def _channel(ref: str, *, role: str, verified: str, now: datetime) -> dict[str, Any]:
    return {"ref": ref, "id": None, "title": None, "url": None, "role": role, "verified": verified, "status": "active",
            "followers": None, "categories": {}, "added": now.isoformat(), "checked": None, "errors": 0,
            "last_upload": None}  # fmt: skip


def record_details(channel: dict[str, Any], info: dict[str, Any], now: datetime) -> None:
    """What yt-dlp says of a channel (its page or one of its videos): id, title, address, subscribers."""
    channel["id"] = info.get("channel_id") or channel.get("id")
    channel["title"] = info.get("channel") or info.get("uploader") or channel.get("title")
    handle = info.get("uploader_id") or ""
    channel["url"] = info.get("channel_url") or (f"https://www.youtube.com/{handle}" if handle.startswith("@") else channel.get("url"))
    if info.get("channel_follower_count") is not None:
        channel["followers"] = info["channel_follower_count"]
    channel["checked"] = now.isoformat()
    channel["errors"] = 0


def failed(channel: dict[str, Any], why: str) -> None:
    channel["errors"] = int(channel.get("errors") or 0) + 1
    channel["error"] = why[:200]
    if channel["errors"] >= MAX_ERRORS:
        channel["status"] = "introuvable"
        log.warning("Chaîne YouTube %s mise de côté après %d échecs : %s", channel.get("ref"), channel["errors"], why[:200])


def mentions(description: str, main: dict[str, Any]) -> bool:
    """Whether a channel's description points at a main channel: its @handle, its id or its address."""
    text = description.lower()
    handle = (main.get("ref") or "").lower()
    marks = [handle if handle.startswith("@") else f"@{handle}", (main.get("id") or "").lower(),
             (main.get("url") or "").lower().removeprefix("https://").removeprefix("www.")]  # fmt: skip
    return any(len(mark) > 2 and re.search(re.escape(mark) + r"(?![\w.-])", text) for mark in marks)


# --- videos -------------------------------------------------------------------------------------------------------


def remember(history: dict[str, dict[str, Any]], video: FeedVideo, creator: str, now: datetime) -> dict[str, Any]:
    """The video in the history of videos seen, with today's view count added to its last ones."""
    entry = history.setdefault(video.id, {"channel": video.channel_id, "creator": creator, "title": video.title,
                                          "published": video.published.isoformat() if video.published else None,
                                          "seen": now.isoformat(), "views": [], "examined": 0})  # fmt: skip
    entry["title"] = video.title or entry.get("title")
    if video.likes is not None:
        entry["likes"] = video.likes
    if video.views is not None:
        entry["views"] = (entry.get("views") or [])[-(SNAPSHOTS - 1):] + [[now.isoformat(), video.views]]
    return entry


def velocity(entry: dict[str, Any], now: datetime) -> float | None:
    """Views per hour: between the last two counts when they are hours apart, else since the upload."""
    views = entry.get("views") or []
    if len(views) >= 2:
        (t0, v0), (t1, v1) = views[-2], views[-1]
        hours = ((parse_time(t1) or now) - (parse_time(t0) or now)).total_seconds() / 3600
        if hours >= 1.0 and v1 >= v0:
            return (v1 - v0) / hours
    published = parse_time(entry.get("published"))
    if not views or published is None:
        return None
    hours = max((now - published).total_seconds() / 3600, 1.0)
    return views[-1][1] / hours


def pre_score(entry: dict[str, Any], channel_pace: float | None, now: datetime) -> float:
    """How promising a new video looks, 0-100, before anything is downloaded. An estimate, not a measure.

    How fast it gathers views against the channel's usual pace (and in absolute), how much it is liked, how fresh it
    is and whether its title promises a moment (a reaction, a clash, a first, a fail…).
    """
    pace = velocity(entry, now) or 0.0
    relative = 0.5 if not channel_pace else (max(-2.0, min(3.0, math.log2((pace + 1) / (channel_pace + 1)))) + 2) / 5
    absolute = min(1.0, math.log10(pace + 1) / 5)  # 100 000 views an hour = 1
    views = (entry.get("views") or [[None, 0]])[-1][1] or 0
    liked = min(1.0, (entry.get("likes") or 0) / views / 0.08) if views else 0.0
    published = parse_time(entry.get("published"))
    fresh = math.exp(-max((now - published).total_seconds() / 3600, 0.0) / 72) if published else 0.3
    title = entry.get("title") or ""
    hooks = min(1.0, len(_HOOKS.findall(title)) / 2 + (0.25 if re.search(r"[!?]", title) else 0.0)
                + (0.25 if sum(c.isupper() for c in title) > len(title) * 0.4 > 4 else 0.0))  # fmt: skip
    return round(100 * (0.35 * relative + 0.25 * absolute + 0.15 * liked + 0.15 * fresh + 0.10 * hooks), 1)


def channel_paces(history: dict[str, dict[str, Any]], now: datetime) -> dict[str, float]:
    """The usual pace (median views per hour) of each channel's recent videos, from the history."""
    paces: dict[str, list[float]] = {}
    for entry in history.values():
        pace = velocity(entry, now)
        if pace is not None:
            paces.setdefault(entry.get("channel") or "", []).append(pace)
    return {channel: statistics.median(values) for channel, values in paces.items() if len(values) >= 3}


def due(entry: dict[str, Any], now: datetime, *, max_age: timedelta) -> bool:
    """Whether a video seen in a feed should be looked into this run.

    Fresh enough, not looked into yet, or once more a day later when it gave nothing the first time (its « most
    replayed » curve and its comments come with the views).
    """
    published = parse_time(entry.get("published"))
    if published is None or now - published > max_age:
        return False
    if not entry.get("examined"):
        return True
    last = parse_time(entry.get("examined_at"))
    return entry.get("examined", 0) < 2 and entry.get("outcome") == "none" and (last is None or now - last >= timedelta(hours=20))


def prune(history: dict[str, dict[str, Any]], now: datetime, *, max_age: timedelta) -> dict[str, dict[str, Any]]:
    """Videos older than the catch-up window (and two days more) leave the history."""
    keep: dict[str, dict[str, Any]] = {}
    for video_id, entry in history.items():
        published = parse_time(entry.get("published")) or parse_time(entry.get("seen"))
        if published is None or now - published <= max_age + timedelta(days=2):
            keep[video_id] = entry
    return keep
