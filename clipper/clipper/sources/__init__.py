from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..config import Config, Secrets
from ..http import Http
from ..models import Candidate
from ..ytdlp import YtDlp
from .manual import load_queue
from .twitch import discover_twitch
from .youtube import discover_youtube

log = logging.getLogger(__name__)


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
) -> list[Candidate]:
    """Every candidate moment: the manual queue first, then Twitch clips, then YouTube peaks."""
    candidates: list[Candidate] = []
    if queue is not None:
        candidates.extend(load_queue(queue))
    candidates.extend(
        discover_twitch(config, secrets, http, ytdlp, now=now, window=window, per_streamer=per_streamer, memory=memory)
    )
    if include_youtube:
        candidates.extend(discover_youtube(config, ytdlp, now=now, offset=youtube_offset))
    log.info("%d candidat(s) trouvé(s)", len(candidates))
    return candidates
