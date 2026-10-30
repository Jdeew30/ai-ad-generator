from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    """ISO-8601 string, epoch seconds or datetime -> aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class Candidate:
    """A moment worth clipping: a Twitch clip, a YouTube segment or a manual URL."""

    key: str  # stable id used for de-duplication: "twitch:<slug>", "youtube:<id>@<s>-<e>", "manual:<hash>"
    source: str  # "twitch" | "youtube" | "manual"
    url: str  # page handed to yt-dlp
    title: str  # original title (clip title, video title)
    streamer: str  # display name, for credits
    streamer_login: str  # handle, for credits and per-streamer limits
    channel_url: str
    views: int = 0
    duration: float = 0.0  # length of the moment, in seconds
    created_at: datetime | None = None
    game: str | None = None
    clipper: str | None = None  # viewer who made the Twitch clip
    start: float | None = None  # section of `url` to cut, in seconds (None = whole media)
    end: float | None = None
    vod_id: str | None = None
    vod_offset: float | None = None
    thumbnail_url: str | None = None
    heat: float | None = None  # YouTube "most replayed" intensity, 0..1
    manual: bool = False  # queued by hand: skips popularity filters and AI rejection
    parts: list[str] = field(default_factory=list)  # clips of one moment put back together, in order (url is the first)
    score: float = 0.0

    @property
    def credit(self) -> str:
        """Short, human readable origin shown on the video."""
        if self.source == "twitch" or "twitch.tv" in self.channel_url:
            return f"twitch.tv/{self.streamer_login}"
        if self.channel_url:
            return self.channel_url.removeprefix("https://").removeprefix("www.")
        return self.streamer

    def vod_span(self) -> tuple[str, float, float] | None:
        """(vod id, start, end) inside the original broadcast, when known."""
        if self.key.startswith("youtube:") and self.start is not None and self.end is not None:
            video_id = self.key.split(":", 1)[1].split("@", 1)[0]
            return (f"yt:{video_id}", self.start, self.end)
        if self.vod_id and self.vod_offset is not None and self.duration:
            return (f"tw:{self.vod_id}", self.vod_offset, self.vod_offset + self.duration)
        return None

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["created_at"] = self.created_at.isoformat() if self.created_at else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candidate:
        names = {f.name for f in dataclasses.fields(cls)}
        values = {k: v for k, v in data.items() if k in names}
        values["created_at"] = parse_time(values.get("created_at"))
        return cls(**values)


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    words: list[Word] = field(default_factory=list)
    language: str | None = None

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words).strip()

    def timestamped(self, max_chars: int = 2500) -> str:
        """Readable transcript with a timestamp every sentence-ish chunk, for the AI review."""
        lines: list[str] = []
        current: list[str] = []
        start = None
        for word in self.words:
            if start is None:
                start = word.start
            current.append(word.text)
            if word.text.endswith((".", "!", "?", "…")) or len(current) >= 14:
                lines.append(f"[{start:5.1f}s] {' '.join(current)}")
                current, start = [], None
        if current and start is not None:
            lines.append(f"[{start:5.1f}s] {' '.join(current)}")
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[:max_chars] + " […]"

    def to_dict(self) -> dict[str, Any]:
        return {"language": self.language, "words": [dataclasses.asdict(w) for w in self.words]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        return cls(words=[Word(**w) for w in data.get("words", [])], language=data.get("language"))


@dataclass
class ShortMeta:
    """Everything needed to publish one short."""

    hook: str  # burned on the video, short and punchy
    title: str  # YouTube title
    description: str
    hashtags: list[str]
    score: float = 0.0
    trim_start: float | None = None  # relative to the downloaded media
    trim_end: float | None = None
    ai: bool = False  # written by Claude (True) or by the template (False)

    def tags(self) -> list[str]:
        return [h.lstrip("#") for h in self.hashtags if h.strip("# ")]

    def hashtag_line(self) -> str:
        return " ".join(f"#{tag}" for tag in self.tags())


@dataclass
class CompilationMeta:
    title: str
    description: str
    hashtags: list[str]
    thumbnail_text: str
    ai: bool = False


@dataclass
class PublishResult:
    platform: str
    ok: bool
    id: str | None = None
    url: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
