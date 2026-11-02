from __future__ import annotations

import dataclasses
import logging
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from ..config import Config, YouTubeSourceConfig
from ..models import Candidate, parse_time
from ..ytdlp import DownloadError, YtDlp

log = logging.getLogger(__name__)

MAX_WINDOW = 150.0  # longest section downloaded around a peak; the AI (or the template) trims it

SEARCH_CHANNELS = "https://www.youtube.com/results?search_query={}&sp=EgIQAg%253D%253D"  # the "Channels" filter
# "<name> <suffix>" titles of a YouTuber's own other channels: "Squeezie Gaming", "Djilsi Gaming", "X Vlog"...
SECOND_CHANNEL_SUFFIXES = {"gaming", "games", "jeux", "2", "bis", "vlog", "vlogs", "live", "lives", "replay", "replays",
                           "rediff", "rediffs", "tv", "plus", "extra", "bonus", "irl", "podcast"}  # fmt: skip
MIN_FOLLOWERS = 10_000  # a fan's channel with the same name stays out
NO_VIDEOS_TAB = "does not have a videos tab"  # yt-dlp, on a channel that only has lives (or shorts)
RELATED_LOOKUPS_PER_RUN = 8
RELATED_REFRESH_DAYS = 14
NO_HEATMAP = "sans « moments les plus revus »"
NO_MOMENT = "sans « moments les plus revus » ni minutage en commentaire"
COMMENTS_PER_VIDEO = 60  # top comments read when YouTube gives no "most replayed" data
MIN_CITED = 2.0  # weight a moment needs from the comments: two citations, or one comment that a few people liked
# "3:45", "1:02:13" in a comment, not part of a longer number, a date or a score
_TIMESTAMP = re.compile(r"(?<![\d:/])(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)(?![\d:/])")
BLOCKED = ("YouTube bloque ce serveur (« Sign in to confirm you're not a bot ») : pas de vidéo YouTube à ce passage. "
           "Des cookies YouTube (secret YTDLP_COOKIES) lèvent ce blocage.")


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


def _uploaded(info: dict[str, Any]) -> datetime | None:
    uploaded = parse_time(info.get("timestamp") or info.get("release_timestamp"))
    if uploaded is None and info.get("upload_date"):
        uploaded = parse_time(f"{info['upload_date'][:4]}-{info['upload_date'][4:6]}-{info['upload_date'][6:8]}")
    return uploaded


def skip_reason(info: dict[str, Any], settings: YouTubeSourceConfig, now: datetime) -> str | None:
    """Why a video gives no moment before its peaks are even looked at, or None."""
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        return "live"
    uploaded = _uploaded(info)
    if uploaded and now - uploaded > timedelta(days=settings.max_age_days):
        return "trop ancienne"
    if int(info.get("view_count") or 0) < settings.min_views:
        return "pas assez de vues"
    if float(info.get("duration") or 0.0) < settings.segment_min * 3:
        return "trop courte"
    if not info.get("heatmap"):
        return NO_HEATMAP
    return None


def moments_from_comments(comments: list[dict[str, Any]], duration: float, *, count: int) -> list[tuple[float, float, float]]:
    """The moments viewers point at in the comments (« 3:45 mdr »), as (start, end, weight) windows, most cited first.

    A comment counts more with its likes; one that lists many times (a summary of the video) counts for little.
    """
    cited: list[tuple[float, float]] = []
    intro_end = max(duration * 0.04, 15.0)
    for comment in comments:
        times = {int(h or 0) * 3600 + int(m) * 60 + int(s) for h, m, s in _TIMESTAMP.findall(str(comment.get("text") or ""))}
        times = {t for t in times if intro_end <= t < duration - 5}
        weight = (1.0 + math.log1p(max(0, int(comment.get("like_count") or 0)))) / max(1, len(times))
        cited += [(float(t), weight) for t in times]
    groups: list[list[tuple[float, float]]] = []
    for at, weight in sorted(cited):
        if groups and at - groups[-1][0][0] <= 20.0:
            groups[-1].append((at, weight))
        else:
            groups.append([(at, weight)])
    windows: list[tuple[float, float, float]] = []
    for group in sorted(groups, key=lambda g: -sum(w for _, w in g)):
        weight = sum(w for _, w in group)
        if weight < MIN_CITED or len(windows) >= count:
            break
        # Viewers give the start of a moment: take a little before it and its aftermath, the review trims it.
        start = max(0.0, group[0][0] - 15.0)
        end = min(duration, max(group[-1][0] + 30.0, start + 60.0), start + MAX_WINDOW)
        if any(start < taken_end and end > taken_start for taken_start, taken_end, _ in windows):
            continue
        windows.append((start, end, round(weight, 2)))
    return windows


def candidates_from_info(
    info: dict[str, Any], settings: YouTubeSourceConfig, config: Config, now: datetime, comments: list[dict[str, Any]] | None = None
) -> list[Candidate]:
    """The video's moments: its "most replayed" peaks, or else the times its `comments` point at."""
    reason = skip_reason(info, settings, now)
    if reason and not (reason == NO_HEATMAP and comments):
        return []
    uploaded = _uploaded(info)
    views = int(info.get("view_count") or 0)
    duration = float(info.get("duration") or 0.0)
    if info.get("heatmap"):
        windows = most_replayed(
            info["heatmap"],
            duration,
            count=settings.segments_per_video,
            min_peak=settings.min_peak,
            seg_min=settings.segment_min,
            seg_max=settings.segment_max,
        )
    else:
        cited = moments_from_comments(comments or [], duration, count=settings.segments_per_video)
        windows = [(start, end, min(1.0, weight / 8.0)) for start, end, weight in cited]
        if cited:
            log.info("Moments cités en commentaire sous « %s » : %s", info.get("title") or info.get("id"),
                     ", ".join(f"{_clock(start)}-{_clock(end)}" for start, end, _ in cited))
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


def _clock(seconds: float) -> str:
    """1:02:13, or 3:45 under an hour."""
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def _video_moments(
    info: dict[str, Any], url: str, settings: YouTubeSourceConfig, config: Config, ytdlp: YtDlp, now: datetime
) -> tuple[list[Candidate], str | None]:
    """The video's moments, and why it has none. Without "most replayed" data its top comments are read."""
    found = candidates_from_info(info, settings, config, now)
    if found:
        return found, None
    reason = skip_reason(info, settings, now)
    if reason != NO_HEATMAP:
        return [], reason or "aucun passage assez revu"
    try:
        comments = ytdlp.comments(url, COMMENTS_PER_VIDEO)
    except DownloadError as exc:
        if exc.blocked:
            raise
        log.warning("Commentaires de la vidéo YouTube %s illisibles : %s", info.get("id"), exc)
        comments = []
    found = candidates_from_info(info, settings, config, now, comments=comments)
    return found, None if found else NO_MOMENT


def _plain(text: str) -> str:
    """'Maxime Biaggi Gaming' -> 'maximebiaggigaming': lowercase letters and digits, accents removed."""
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def related_channels(ytdlp: YtDlp, ref: str) -> list[str]:
    """A YouTuber's other channels, found by searching their name ("Squeezie" -> "Squeezie Gaming").

    Only "<name> <suffix>" titles with a known suffix count, so fan channels ("<name> Clips", "Best of <name>") and
    the automatic "Topic" channels are left out, as are channels with few subscribers.
    """
    name = _name(ref)
    base = _plain(name)
    if not base:
        return []
    found: list[str] = []
    for entry in ytdlp.list_flat(SEARCH_CHANNELS.format(quote(name)), 15):
        title = _plain(entry.get("channel") or entry.get("title") or "")
        if not title.startswith(base) or title[len(base):] not in SECOND_CHANNEL_SUFFIXES:
            continue
        followers = entry.get("channel_follower_count")
        if followers is not None and followers < MIN_FOLLOWERS:
            continue
        link = _link(entry)
        if link and link not in found:
            found.append(link)
    return found


def renamed_channel(ytdlp: YtDlp, ref: str) -> str | None:
    """The channel a handle that does not exist stands for, found by searching its name: "@LeWoop" -> "Le Woop"'s handle.

    Only a channel titled with that very name counts ("&" read as "et", a " - subtitle" left out), with enough subscribers;
    the most followed if several.
    """
    name = _name(ref)
    wanted = _plain(name)
    if not wanted:
        return None
    best: tuple[int, str] | None = None
    for entry in ytdlp.list_flat(SEARCH_CHANNELS.format(quote(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name))), 10):
        title = entry.get("channel") or entry.get("title") or ""
        names = {title, re.split(r"\s+[-–|(]", title)[0]}  # "Wankil Studio - Laink et Terracid" is "Wankil Studio"
        if wanted not in {_plain(n.replace("&", word)) for n in names for word in ("&", "et", "and")}:
            continue
        followers = entry.get("channel_follower_count")
        link = _link(entry)
        if not link or (followers is not None and followers < MIN_FOLLOWERS) or _name(link).lower() == name.lower():
            continue
        if best is None or (followers or 0) > best[0]:
            best = (followers or 0, link)
    return best[1] if best else None


def _name(ref: str) -> str:
    """'https://www.youtube.com/@Squeezie' or '@Squeezie' -> 'Squeezie'."""
    return ref.strip().rstrip("/").rsplit("/", 1)[-1].lstrip("@")


def _link(entry: dict[str, Any]) -> str | None:
    """A channel search result's handle, or its address."""
    handle = entry.get("uploader_id") or ""
    return handle if handle.startswith("@") else entry.get("url")


def _channel_tab(ytdlp: YtDlp, ref: str, tab: str, count: int) -> tuple[str, list[dict[str, Any]]]:
    """The channel and the `count` latest entries of its `tab`.

    Failing on the videos tab means a wrong handle, looked up by its name, or a channel of lives only: its lives are read.
    """
    try:
        return ref, ytdlp.list_flat(f"{channel_url(ref)}/{tab}", count)
    except DownloadError as exc:
        if exc.blocked or tab != "videos":
            raise
        renamed = None
        if "/@" in channel_url(ref):
            try:
                renamed = renamed_channel(ytdlp, ref)
            except DownloadError:
                pass
        if not renamed:
            if NO_VIDEOS_TAB not in str(exc):
                raise exc
            return ref, ytdlp.list_flat(f"{channel_url(ref)}/streams", count)
    log.warning("Chaîne YouTube %s introuvable : lue sous le nom %s, à corriger dans config.yaml", ref, renamed)
    return renamed, ytdlp.list_flat(f"{channel_url(renamed)}/{tab}", count)


def _refresh_related(channels: list[str], ytdlp: YtDlp, related: dict[str, dict[str, Any]], now: datetime) -> None:
    """Looks a few YouTubers' other channels up each run; the answers are kept RELATED_REFRESH_DAYS days."""
    def stale(ref: str) -> bool:
        seen = parse_time((related.get(ref.lower()) or {}).get("at"))
        return seen is None or now - seen > timedelta(days=RELATED_REFRESH_DAYS)

    for ref in [ref for ref in channels if stale(ref)][:RELATED_LOOKUPS_PER_RUN]:
        try:
            found = related_channels(ytdlp, ref)
        except DownloadError as exc:
            log.warning("Recherche des autres chaînes de %s impossible : %s", ref, exc)
            continue  # tried again in a later run
        related[ref.lower()] = {"at": now.isoformat(), "channels": found}
        if found:
            log.info("Autres chaînes de %s : %s", ref, ", ".join(found))


def discover_youtube(
    config: Config, ytdlp: YtDlp, *, now: datetime, offset: int = 0, related: dict[str, dict[str, Any]] | None = None
) -> list[Candidate]:
    """Peaks of the channels' recent videos. With channels_per_run, the channels take turns from `offset`.

    With related_channels, `related` (kept in the state) adds each YouTuber's other channels to the turns.
    """
    settings = config.youtube
    if not settings.enabled or not (settings.channels or settings.videos):
        return []
    candidates: list[Candidate] = []
    skipped: Counter[str] = Counter()  # why the videos looked at gave no moment, told in the run's log
    asked = dataclasses.replace(settings, max_age_days=100_000, min_views=0)  # videos asked for by name: any age, any views
    for url in settings.videos:
        try:
            info = ytdlp.info(url)
            found, reason = _video_moments(info, url, asked, config, ytdlp, now)
        except DownloadError as exc:
            if exc.blocked:
                log.warning(BLOCKED)
                return candidates
            log.warning("Vidéo YouTube %s ignorée : %s", url, exc)
            continue
        if reason:
            skipped[reason] += 1
        candidates.extend(found)
    channels = list(settings.channels)
    if settings.related_channels and related is not None:
        _refresh_related(settings.channels, ytdlp, related, now)
        for ref in settings.channels:
            channels += (related.get(ref.lower()) or {}).get("channels", [])
        unique: list[str] = []
        for ref in channels:
            if ref.lower() not in {known.lower() for known in unique}:
                unique.append(ref)
        channels = unique
    if 0 < settings.channels_per_run < len(channels):
        start = offset % len(channels)
        channels = (channels[start:] + channels[:start])[: settings.channels_per_run]
        log.info("YouTube : %s", ", ".join(channels))
    for ref in channels:
        seen: set[str] = set()
        for tab in settings.tabs:
            try:
                ref, entries = _channel_tab(ytdlp, ref, tab, settings.videos_per_channel)
            except DownloadError as exc:
                (log.warning if tab == "videos" else log.debug)(
                    "Chaîne YouTube introuvable ou indisponible (%s/%s) : %s", channel_url(ref), tab, exc)
                continue
            for entry in entries:
                video_id = entry.get("id")
                if not video_id or video_id in seen:
                    continue
                seen.add(video_id)
                url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
                try:
                    info = ytdlp.info(url)
                    if config.is_blocked((info.get("uploader_id") or "").lstrip("@")):
                        continue
                    found, reason = _video_moments(info, url, settings, config, ytdlp, now)
                except DownloadError as exc:
                    if exc.blocked:
                        log.warning(BLOCKED)
                        return candidates
                    log.warning("Vidéo YouTube %s ignorée : %s", video_id, exc)
                    continue
                if reason:
                    skipped[reason] += 1
                candidates.extend(found)
    if skipped:
        log.info("YouTube : %d passage(s) retenu(s) ; vidéos écartées : %s", len(candidates),
                 ", ".join(f"{reason} ({count})" for reason, count in skipped.most_common()))
    return candidates
