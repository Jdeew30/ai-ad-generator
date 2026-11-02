from __future__ import annotations

import dataclasses
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Config, ConfigError, Secrets
from ..http import Http
from ..models import Candidate
from ..ytdlp import YtDlp
from .manual import load_queue
from .twitch import discover_twitch
from .youtube import discover_youtube

log = logging.getLogger(__name__)

_YOUTUBE_VIDEO = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|youtu\.be/)[A-Za-z0-9_-]{11}")
_TWITCH_CHANNEL = re.compile(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{3,25})/?$|^([A-Za-z0-9_]{3,25})$")
FOCUS_CLIPS_PER_STREAMER = 50
FOCUS_SEGMENTS_PER_VIDEO = 6


def focus_config(config: Config, targets: list[str]) -> Config:
    """A run on these sources only: Twitch channels (login or link), YouTube channels (@handle or link) or videos.

    Nothing else is looked at (no lives, categories or turns), more clips are taken from each channel, a long video
    gives more of its most replayed moments, and the per-streamer daily cap does not apply: they were asked for.
    """
    streamers: list[str] = []
    channels: list[str] = []
    videos: list[str] = []
    for target in (t.strip() for t in targets):
        if not target:
            continue
        twitch = _TWITCH_CHANNEL.match(target)
        if _YOUTUBE_VIDEO.search(target):
            videos.append(target)
        elif target.startswith("@") or "youtube.com/" in target:
            channels.append(target)
        elif twitch:
            streamers.append((twitch.group(1) or twitch.group(2)).lower())
        else:
            raise ConfigError(f"cible inconnue : {target!r} (chaîne Twitch, @chaîne YouTube ou lien de vidéo YouTube)")
    return dataclasses.replace(
        config,
        twitch=dataclasses.replace(
            config.twitch, enabled=bool(streamers), streamers=streamers, discover_live=False, remember_live=0,
            discover_categories=0, clips_per_streamer=max(config.twitch.clips_per_streamer, FOCUS_CLIPS_PER_STREAMER),
        ),
        youtube=dataclasses.replace(
            config.youtube, enabled=bool(channels or videos), channels=channels, videos=videos, channels_per_run=0,
            related_channels=False, segments_per_video=max(config.youtube.segments_per_video, FOCUS_SEGMENTS_PER_VIDEO),
        ),
        shorts=dataclasses.replace(config.shorts, max_per_streamer_per_day=1_000_000),
    )


def discover(
    config: Config,
    secrets: Secrets,
    http: Http,
    ytdlp: YtDlp,
    *,
    now: datetime,
    window: timedelta,
    per_streamer: int | None = None,
    queue: Path | None = None,
    include_youtube: bool = True,
    memory: dict[str, str] | None = None,
    youtube_offset: int = 0,
    youtube_related: dict[str, dict[str, Any]] | None = None,
    creators: Any = None,
    youtube_history: dict[str, dict[str, Any]] | None = None,
    known: set[str] | None = None,
) -> list[Candidate]:
    """Every candidate moment: the manual queue first, then Twitch clips, then YouTube peaks."""
    candidates: list[Candidate] = []
    if queue is not None:
        candidates.extend(load_queue(queue))
    candidates.extend(
        discover_twitch(config, secrets, http, ytdlp, now=now, window=window, per_streamer=per_streamer, memory=memory)
    )
    if include_youtube:
        candidates.extend(discover_youtube(config, ytdlp, now=now, offset=youtube_offset, related=youtube_related, http=http,
                                           creators=creators, history=youtube_history, twitch_seen=memory, known=known))
    log.info("%d candidat(s) trouvé(s)", len(candidates))
    return candidates
