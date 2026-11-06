from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import Candidate, PublishResult, ShortMeta, parse_time, utcnow

MAX_DOWNLOAD_FAILURES = 3
SAME_MOMENT_SECONDS = 90  # two clips of one streamer created this close are the same moment


def spans_overlap(a: tuple[str, float, float] | None, b: tuple[str, float, float] | None) -> bool:
    """True when two (vod, start, end) spans cover mostly the same part of one broadcast."""
    if a is None or b is None or a[0] != b[0]:
        return False
    overlap = min(a[2], b[2]) - max(a[1], b[1])
    if overlap <= 0:
        return False
    shortest = max(min(a[2] - a[1], b[2] - b[1]), 1e-6)
    return overlap / shortest >= 0.3


def same_moment(
    a: tuple[str, tuple[str, float, float] | None, datetime | None],
    b: tuple[str, tuple[str, float, float] | None, datetime | None],
) -> bool:
    """Compares (login, vod span, created_at) moments.

    The VOD position is authoritative when both clips have one in the same VOD. Without it
    (yt-dlp listing, deleted VOD), clips of one streamer created within 90 s of each other are
    assumed to show the same moment: viewers clip a funny moment right after it happens.
    """
    login_a, span_a, created_a = a
    login_b, span_b, created_b = b
    if span_a is not None and span_b is not None and span_a[0] == span_b[0]:
        return spans_overlap(span_a, span_b)
    if login_a.lower() != login_b.lower() or created_a is None or created_b is None:
        return False
    return abs((created_a - created_b).total_seconds()) <= SAME_MOMENT_SECONDS


def moment_of(candidate: Candidate) -> tuple[str, tuple[str, float, float] | None, datetime | None]:
    # YouTube segments carry their position; their upload date says nothing about the moment.
    created = None if candidate.key.startswith("youtube:") else candidate.created_at
    return (candidate.streamer_login, candidate.vod_span(), created)


class State:
    """What the bot already did, persisted as one JSON file (a git branch in GitHub Actions)."""

    VERSION = 1

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        data = data or {}
        self.posted: dict[str, dict[str, Any]] = data.get("posted", {})
        self.rejected: dict[str, dict[str, Any]] = data.get("rejected", {})
        self.failures: dict[str, dict[str, Any]] = data.get("failures", {})
        self.reviews: dict[str, dict[str, Any]] = data.get("reviews", {})
        self.compiled: dict[str, str] = data.get("compiled", {})
        self.compilations: list[dict[str, Any]] = data.get("compilations", [])
        self.streamers_seen: dict[str, str] = data.get("streamers_seen", {})  # login -> last seen live or in the top
        self.youtube_cursor: int = int(data.get("youtube_cursor", 0))  # next YouTube channel to look at
        self.youtube_related: dict[str, dict[str, Any]] = data.get("youtube_related", {})  # YouTuber -> other channels
        self.youtube_videos: dict[str, dict[str, Any]] = data.get("youtube_videos", {})  # video id -> seen in a feed
        self.runs: list[dict[str, Any]] = data.get("runs", [])  # counts of each run, for the dashboard

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != cls.VERSION:
            raise ValueError(f"{path} : version d'état inconnue ({data.get('version')!r})")
        return cls(path, data)

    def save(self) -> None:
        self.prune()
        payload = {
            "version": self.VERSION,
            "posted": self.posted,
            "rejected": self.rejected,
            "failures": self.failures,
            "reviews": self.reviews,
            "compiled": self.compiled,
            "compilations": self.compilations[-60:],
            "streamers_seen": self.streamers_seen,
            "youtube_cursor": self.youtube_cursor,
            "youtube_related": self.youtube_related,
            "youtube_videos": self.youtube_videos,
            "runs": self.runs[-400:],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def prune(self, now: datetime | None = None) -> None:
        now = now or utcnow()

        def keep(entry: dict[str, Any] | str, days: int) -> bool:
            at = parse_time(entry if isinstance(entry, str) else entry.get("at"))
            return at is None or now - at <= timedelta(days=days)

        self.posted = {k: v for k, v in self.posted.items() if keep(v, 120)}
        self.rejected = {k: v for k, v in self.rejected.items() if keep(v, 30)}
        self.failures = {k: v for k, v in self.failures.items() if keep(v, 7)}
        self.reviews = {k: v for k, v in self.reviews.items() if keep(v, 3)}
        self.compiled = {k: v for k, v in self.compiled.items() if keep(v, 60)}
        self.streamers_seen = {k: v for k, v in self.streamers_seen.items() if keep(v, 14)}
        self.youtube_related = {k: v for k, v in self.youtube_related.items() if keep(v, 30)}
        self.youtube_videos = {k: v for k, v in self.youtube_videos.items() if keep(v.get("published") or v.get("seen") or "", 30)}
        self.runs = [run for run in self.runs if keep(run, 45)]

    # --- queries ---------------------------------------------------------------------------

    def seen(self, key: str) -> bool:
        failures = self.failures.get(key, {}).get("count", 0)
        return key in self.posted or key in self.rejected or failures >= MAX_DOWNLOAD_FAILURES

    def overlaps_posted(self, candidate: Candidate) -> bool:
        moment = moment_of(candidate)
        for entry in self.posted.values():
            span = entry.get("span")
            other = (
                entry.get("streamer_login", ""),
                (span[0], float(span[1]), float(span[2])) if span else None,
                parse_time(entry.get("created_at")),
            )
            if same_moment(moment, other):
                return True
        return False

    def posted_count(self, login: str, since: datetime) -> int:
        login = login.lower()
        return sum(
            1
            for entry in self.posted.values()
            if entry.get("streamer_login", "").lower() == login
            and (parse_time(entry.get("at")) or since) >= since
        )

    def long_count(self, since: datetime, min_length: float) -> int:
        """Shorts sent since `since` that last `min_length` seconds or more."""
        return sum(
            1
            for entry in self.posted.values()
            if (entry.get("length") or 0) >= min_length and (parse_time(entry.get("at")) or since) >= since
        )

    def recent_posts(self, since: datetime) -> list[dict[str, Any]]:
        """Shorts posted since `since`, most recent first."""
        entries = [entry for entry in self.posted.values() if (parse_time(entry.get("at")) or since) >= since]
        return sorted(entries, key=lambda entry: parse_time(entry.get("at")) or since, reverse=True)

    def cached_review(self, key: str) -> dict[str, Any] | None:
        return self.reviews.get(key)

    # --- updates ---------------------------------------------------------------------------

    def record_post(self, candidate: Candidate, meta: ShortMeta, results: list[PublishResult], *, length: float | None = None) -> None:
        span = candidate.vod_span()
        self.posted[candidate.key] = {
            "at": utcnow().isoformat(),
            "streamer_login": candidate.streamer_login,
            "streamer": candidate.streamer,
            "source": candidate.source,
            "url": candidate.url,
            "title": meta.title,
            "score": meta.score or None,  # Claude's mark of the passage (0-100), for the dashboard
            "views": candidate.views,
            "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
            "span": list(span) if span else None,
            "length": round(length, 1) if length else None,  # seconds of the short sent
            "results": {r.platform: r.to_dict() for r in results},
        }
        self.failures.pop(candidate.key, None)
        self.reviews.pop(candidate.key, None)

    def record_rejection(self, candidate: Candidate, reason: str) -> None:
        self.rejected[candidate.key] = {"at": utcnow().isoformat(), "reason": reason[:300]}
        self.reviews.pop(candidate.key, None)

    def record_failure(self, candidate: Candidate, error: str, *, final: bool = False) -> None:
        """One more failure of the clip; it is left aside after MAX_DOWNLOAD_FAILURES, or at once when `final`."""
        entry = self.failures.setdefault(candidate.key, {"count": 0})
        count = MAX_DOWNLOAD_FAILURES if final else entry["count"] + 1
        entry.update(count=count, at=utcnow().isoformat(), error=error[:300])

    def store_review(self, key: str, review: dict[str, Any]) -> None:
        self.reviews[key] = {**review, "at": utcnow().isoformat()}

    def record_compilation(self, keys: list[str], title: str, results: list[PublishResult]) -> None:
        now = utcnow().isoformat()
        for key in keys:
            self.compiled[key] = now
        self.compilations.append(
            {"at": now, "title": title, "keys": keys, "results": {r.platform: r.to_dict() for r in results}}
        )
