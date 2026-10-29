from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from ..config import Config, YouTubeSourceConfig
from ..models import Candidate, parse_time
from ..ytdlp import DownloadError, YtDlp

log = logging.getLogger(__name__)

MAX_WINDOW = 150.0  # longest section downloaded around a peak; the AI (or the template) trims it


def channel_url(ref: str) -> str:
    ref = ref.strip().rstrip("/")
    if ref.startswith(("http://", "https://")):
        return ref
    if ref.startswith("@"):
        return f"https://www.youtube.com/{ref}"
    if re.fullmatch(r"UC[\w-]{22}", ref):
        return f"https://www.youtube.com/channel/{ref}"
    return f"https://www.youtube.com/@{ref}"


def most_replayed(
    heatmap: list[dict[str, Any]],
    duration: float,
    *,
    count: int,
    min_peak: float,
    seg_min: float,
    seg_max: float,
) -> list[tuple[float, float, float]]:
    """Picks the hottest "most replayed" peaks of a video as (start, end, intensity) windows.

    Markers are 1% of the video long, so on a long VOD a peak can be wider than a short: the
    window then keeps up to MAX_WINDOW seconds and trimming picks the exact moment later.
    """
    markers = sorted(
        (float(m["start_time"]), float(m["end_time"]), float(m["value"]))
        for m in heatmap
        if m.get("start_time") is not None and m.get("end_time") is not None and m.get("value") is not None
    )
    if not markers or duration <= 0:
        return []
    intro_end = max(duration * 0.04, 15.0)  # everyone watches the start: not a moment
    outro_start = duration - max(duration * 0.03, 10.0)
    windows: list[tuple[float, float, float]] = []
    taken: list[tuple[float, float]] = []
    by_heat = sorted(range(len(markers)), key=lambda i: -markers[i][2])
    for index in by_heat:
        start, end, value = markers[index]
        if value < min_peak or len(windows) >= count:
            break
        if start < intro_end or end > outro_start:
            continue
        if any(start < t_end and end > t_start for t_start, t_end in taken):
            continue
        region_start, region_end = start, end
        left = index - 1
        while left >= 0 and markers[left][2] >= value * 0.75 and region_end - markers[left][0] <= MAX_WINDOW:
            region_start = markers[left][0]
            left -= 1
        right = index + 1
        while right < len(markers) and markers[right][2] >= value * 0.75 and markers[right][1] - region_start <= MAX_WINDOW:
            region_end = markers[right][1]
            right += 1
        length = region_end - region_start
        length = max(length, seg_min) if length <= seg_max else min(length, MAX_WINDOW)
        # Viewers rewind to the beginning of a moment: start a little before the middle.
        center = (region_start + region_end) / 2
        window_start = max(0.0, center - length * 0.55)
        window_end = min(duration, window_start + length)
        window_start = max(0.0, window_end - length)
        windows.append((round(window_start, 1), round(window_end, 1), value))
        taken.append((window_start - 5.0, window_end + 5.0))
    return windows


def candidates_from_info(info: dict[str, Any], settings: YouTubeSourceConfig, config: Config, now: datetime) -> list[Candidate]:
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        return []
    uploaded = parse_time(info.get("timestamp") or info.get("release_timestamp"))
    if uploaded is None and info.get("upload_date"):
        uploaded = parse_time(f"{info['upload_date'][:4]}-{info['upload_date'][4:6]}-{info['upload_date'][6:8]}")
    if uploaded and now - uploaded > timedelta(days=settings.max_age_days):
        return []
    views = int(info.get("view_count") or 0)
    duration = float(info.get("duration") or 0.0)
    if views < settings.min_views or duration < settings.segment_min * 3:
        return []
    windows = most_replayed(
        info.get("heatmap") or [],
        duration,
        count=settings.segments_per_video,
        min_peak=settings.min_peak,
        seg_min=settings.segment_min,
        seg_max=settings.segment_max,
    )
    handle = (info.get("uploader_id") or "").lstrip("@") or info.get("channel_id") or ""
    login = handle.lower()
    channel = info.get("channel_url") or info.get("uploader_url") or ""
    name = config.override_for(login).name or info.get("channel") or info.get("uploader") or handle
    video_url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}"
    return [
        Candidate(
            key=f"youtube:{info['id']}@{int(start)}-{int(end)}",
            source="youtube",
            url=video_url,
            title=(info.get("title") or "").strip(),
            streamer=name,
            streamer_login=login,
            channel_url=channel,
            views=views,
            duration=end - start,
            created_at=uploaded,
            game=(info.get("categories") or [None])[0] if info.get("categories") else None,
            start=start,
            end=end,
            heat=heat,
            thumbnail_url=info.get("thumbnail"),
        )
        for start, end, heat in windows
    ]


def discover_youtube(config: Config, ytdlp: YtDlp, *, now: datetime, offset: int = 0) -> list[Candidate]:
    """Peaks of the channels' recent videos. With channels_per_run, the channels take turns from `offset`."""
    settings = config.youtube
    if not settings.enabled or not settings.channels:
        return []
    channels = settings.channels
    if 0 < settings.channels_per_run < len(channels):
        start = offset % len(channels)
        channels = (channels[start:] + channels[:start])[: settings.channels_per_run]
        log.info("YouTube : %s", ", ".join(channels))
    candidates: list[Candidate] = []
    for ref in channels:
        base = channel_url(ref)
        seen: set[str] = set()
        for tab in settings.tabs:
            try:
                entries = ytdlp.list_flat(f"{base}/{tab}", settings.videos_per_channel)
            except DownloadError as exc:
                log.debug("Onglet %s de %s indisponible : %s", tab, base, exc)
                continue
            for entry in entries:
                video_id = entry.get("id")
                if not video_id or video_id in seen:
                    continue
                seen.add(video_id)
                try:
                    info = ytdlp.info(entry.get("url") or f"https://www.youtube.com/watch?v={video_id}")
                except DownloadError as exc:
                    log.warning("Vidéo YouTube %s ignorée : %s", video_id, exc)
                    continue
                if config.is_blocked((info.get("uploader_id") or "").lstrip("@")):
                    continue
                candidates.extend(candidates_from_info(info, settings, config, now))
    return candidates
