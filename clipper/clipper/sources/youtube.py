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
from ..http import Http, HttpError
from ..models import Candidate, parse_time
from ..ytdlp import DownloadError, YtDlp
from . import watch

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
            likes=info.get("like_count"),
            comments=info.get("comment_count"),
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

    Only a channel titled with that very name counts ("&" read as "et"), with enough subscribers: the title alone first,
    then a title with a subtitle ("Wankil Studio - Laink et Terracid"); the most followed if several.
    """
    name = _name(ref)
    wanted = _plain(name)
    if not wanted:
        return None

    def named(title: str) -> bool:
        return wanted in {_plain(title.replace("&", word)) for word in ("&", "et", "and")}

    best: tuple[tuple[bool, int], str] | None = None
    for entry in ytdlp.list_flat(SEARCH_CHANNELS.format(quote(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name))), 10):
        title = entry.get("channel") or entry.get("title") or ""
        exact = named(title)
        if not exact and not named(re.split(r"\s+[-–|(]", title)[0]):
            continue
        followers = entry.get("channel_follower_count")
        link = _link(entry)
        if not link or (followers is not None and followers < MIN_FOLLOWERS) or _name(link).lower() == name.lower():
            continue
        rank = (exact, followers or 0)
        if best is None or rank > best[0]:
            best = (rank, link)
    return best[1] if best else None


def _name(ref: str) -> str:
    """'https://www.youtube.com/@Squeezie' or '@Squeezie' -> 'Squeezie'."""
    return ref.strip().rstrip("/").rsplit("/", 1)[-1].lstrip("@")


def _link(entry: dict[str, Any]) -> str | None:
    """A channel search result's handle, or its address."""
    handle = entry.get("uploader_id") or ""
    return handle if handle.startswith("@") else entry.get("url")


def _channel_tab(ytdlp: YtDlp, ref: str, tab: str, count: int) -> tuple[str, list[dict[str, Any]]]:
    """The channel and the `count` latest entries of its `tab` (see _channel_listing)."""
    ref, info = _channel_listing(ytdlp, ref, tab, count)
    return ref, info["entries"]


def _channel_listing(ytdlp: YtDlp, ref: str, tab: str, count: int) -> tuple[str, dict[str, Any]]:
    """The channel and its `tab` listing: the channel's details (id, title, subscribers, description) and its `count`
    latest entries.

    Failing on the videos tab means a channel of lives only, whose lives are read, or a handle that does not exist,
    looked up by its name.
    """
    try:
        return ref, ytdlp.listing(f"{channel_url(ref)}/{tab}", count)
    except DownloadError as exc:
        if exc.blocked or tab != "videos":
            raise
        if NO_VIDEOS_TAB in str(exc):  # the handle is right: another channel of that name is not this one
            return ref, ytdlp.listing(f"{channel_url(ref)}/streams", count)
        renamed = None
        if "/@" in channel_url(ref):
            try:
                renamed = renamed_channel(ytdlp, ref)
            except DownloadError:
                pass
        if not renamed:
            raise exc
    log.warning("Chaîne YouTube %s introuvable : lue sous le nom %s, à corriger dans config.yaml", ref, renamed)
    return renamed, ytdlp.listing(f"{channel_url(renamed)}/{tab}", count)


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
    config: Config, ytdlp: YtDlp, *, now: datetime, offset: int = 0, related: dict[str, dict[str, Any]] | None = None,
    http: Http | None = None, creators: watch.Creators | None = None, history: dict[str, dict[str, Any]] | None = None,
    twitch_seen: dict[str, str] | None = None,
) -> list[Candidate]:
    """Peaks of the channels' recent videos.

    With watch « rss » (and `http`, `creators`, `history` from the state), every channel's feed is read at each run
    and only the most promising new videos are looked into (see _watch). Otherwise, with channels_per_run, the
    channels take turns from `offset`, and with related_channels, `related` adds each YouTuber's other channels.
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
    if settings.watch == "rss" and http is not None and creators is not None and history is not None:
        try:
            if twitch_seen:
                _discover_from_twitch(creators, twitch_seen, ytdlp, now, budget=settings.discover_per_run)
            candidates.extend(_watch(config, ytdlp, http, creators, history, related, now, skipped))
        except DownloadError as exc:
            if not exc.blocked:
                raise
            log.warning(BLOCKED)
        _tell(candidates, skipped)
        return candidates
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
    _tell(candidates, skipped)
    return candidates


def _tell(candidates: list[Candidate], skipped: Counter[str]) -> None:
    if skipped:
        log.info("YouTube : %d passage(s) retenu(s) ; vidéos écartées : %s", len(candidates),
                 ", ".join(f"{reason} ({count})" for reason, count in skipped.most_common()))


def _watch(
    config: Config, ytdlp: YtDlp, http: Http, creators: watch.Creators, history: dict[str, dict[str, Any]],
    related: dict[str, dict[str, Any]] | None, now: datetime, skipped: Counter[str],
) -> list[Candidate]:
    """Every watched channel's RSS feed, then the details and moments of the most promising new videos only.

    The owner's list makes the creators; their other channels join once verified (_adopt). A channel's YouTube id is
    looked up once (_resolve). Each feed adds its uploads to `history` with their view counts; the videos due (fresh,
    not looked into yet) are pre-scored and the best `videos_per_run` get their « most replayed » peaks or, without
    them, the moments their comments point at.
    """
    settings = config.youtube
    creators.sync(settings.channels, now)
    if settings.related_channels and related is not None:
        _refresh_related(settings.channels, ytdlp, related, now)
        _adopt(creators, related, ytdlp, now, budget=settings.secondary_checks_per_run)
    _resolve(creators, ytdlp, now, budget=settings.resolve_per_run)
    read = 0
    unread: list[str] = []
    for creator, channel in creators.watched():
        if not channel.get("id"):
            continue
        try:
            videos = watch.read_feed(http, channel["id"])
        except HttpError as exc:
            watch.failed(channel, f"flux RSS : {exc}")
            unread.append(channel.get("ref") or channel["id"])
            continue
        read += 1
        channel["checked"], channel["errors"] = now.isoformat(), 0
        if videos and videos[0].published:
            channel["last_upload"] = videos[0].published.isoformat()
        for video in videos:
            watch.remember(history, video, creator, now)
    window = timedelta(days=settings.max_age_days)
    history_kept = watch.prune(history, now, max_age=window)
    history.clear()
    history.update(history_kept)
    paces = watch.channel_paces(history, now)
    due = sorted(((watch.pre_score(entry, paces.get(entry.get("channel") or ""), now), video_id, entry)
                  for video_id, entry in history.items() if watch.due(entry, now, max_age=window)),
                 key=lambda item: -item[0])  # fmt: skip
    log.info("YouTube : %d chaîne(s) lue(s) par flux RSS%s, %d vidéo(s) récente(s) à regarder, les %d plus prometteuses ce "
             "passage.", read, f" ({len(unread)} illisible(s) : {', '.join(unread[:8])})" if unread else "", len(due),
             min(len(due), settings.videos_per_run))  # fmt: skip
    candidates: list[Candidate] = []
    for momentum, video_id, entry in due[: settings.videos_per_run]:
        url = f"https://www.youtube.com/watch?v={video_id}"
        entry["examined"] = int(entry.get("examined") or 0) + 1
        entry["examined_at"] = now.isoformat()
        try:
            info = ytdlp.info(url)
            if config.is_blocked((info.get("uploader_id") or "").lstrip("@")):
                entry["outcome"] = "blocked"
                continue
            found, reason = _video_moments(info, url, settings, config, ytdlp, now)
        except DownloadError as exc:
            if exc.blocked:
                entry["examined"] -= 1  # YouTube blocked the server, not this video: its turn comes again
                raise
            log.warning("Vidéo YouTube %s ignorée : %s", video_id, exc)
            entry["outcome"] = "error"
            skipped["illisible"] += 1
            continue
        entry["outcome"] = "moments" if found else "none"
        _note_category(creators, info)
        if reason:
            skipped[reason] += 1
        pace = watch.velocity(entry, now)
        creator = creators.creators.get(entry.get("creator") or "", {}).get("name")
        for candidate in found:
            candidate.momentum, candidate.pace, candidate.creator = momentum, pace, creator
        log.info("  %.0f/100 %s — %s%s", momentum, info.get("channel") or entry.get("creator"), entry.get("title"),
                 f" : {len(found)} passage(s)" if found else f" : {reason}")
        candidates.extend(found)
    return candidates


def _resolve(creators: watch.Creators, ytdlp: YtDlp, now: datetime, *, budget: int) -> None:
    """Looks up the YouTube id (and title, subscribers) of watched channels that have none yet, `budget` at most.

    A handle that does not exist is looked up by its name, a channel without videos is read through its lives (see
    _channel_listing). The 2 latest uploads come with it: a feed is read from the next run on anyway.
    """
    for creator, channel in [(key, channel) for key, channel in creators.watched() if not channel.get("id")][:budget]:
        try:
            found_as, info = _channel_listing(ytdlp, channel["ref"], "videos", 2)
        except DownloadError as exc:
            if exc.blocked:
                raise
            watch.failed(channel, str(exc))
            continue
        if not info.get("channel_id"):
            watch.failed(channel, "identifiant de chaîne absent de la page")
            continue
        watch.record_details(channel, info, now)
        channel["description_handles"] = sorted(set(re.findall(r"@[\w.-]+", info.get("description") or "")))[:20]
        if found_as != channel["ref"]:
            channel["found_as"] = found_as
        record = creators.creators[creator]
        default_names = {watch.key_of(channel["ref"]), channel["ref"].strip().rstrip("/").rsplit("/", 1)[-1].lstrip("@")}
        if channel.get("role") == "main" and record.get("name") in default_names:
            record["name"] = channel.get("title") or record["name"]  # « Domingo » rather than « pa.domingo »


def _adopt(creators: watch.Creators, related: dict[str, dict[str, Any]], ytdlp: YtDlp, now: datetime, *, budget: int) -> None:
    """Adds a YouTuber's other channels found by search (« Squeezie Gaming ») once one of them names the other.

    The search keeps « <name> <suffix> » titles with enough subscribers; that is not enough to call them the
    creator's own. A channel is adopted when its description gives the main channel (handle, id or address) or the
    main channel's description gives it. The others are noted as unverified and checked again two weeks later.
    """
    for key, creator in creators.creators.items():
        if budget <= 0:
            return
        main = creator.get("channels", {}).get(key)
        if creator.get("status") != "active" or not main:
            continue
        unverified = creator.setdefault("unverified", {})
        for link in (related.get(main["ref"].lower()) or {}).get("channels", []):
            if any(watch.key_of(link) in (ref, watch.key_of(channel.get("ref", ""))) for _, ref, channel in creators.channels()):
                continue
            seen = parse_time((unverified.get(link) or {}).get("at"))
            if seen is not None and now - seen < timedelta(days=14):
                continue
            if budget <= 0:
                return
            budget -= 1
            try:
                _found_as, info = _channel_listing(ytdlp, link, "videos", 1)
            except DownloadError as exc:
                if exc.blocked:
                    raise
                unverified[link] = {"at": now.isoformat(), "why": str(exc)[:150]}
                continue
            handle = (info.get("uploader_id") or link).lower()
            named_by_main = handle in {h.lower() for h in main.get("description_handles") or []}
            if watch.mentions(info.get("description") or "", main) or named_by_main:
                proof = f"nommée par la chaîne principale ({main['ref']})" if named_by_main else f"sa description cite {main['ref']}"
                creators.add_secondary(key, link, verified=proof, now=now)
                watch.record_details(creator["channels"][watch.key_of(link)], info, now)
                unverified.pop(link, None)
                log.info("Chaîne secondaire de %s vérifiée : %s (%s)", creator.get("name"), link, proof)
            else:
                unverified[link] = {"at": now.isoformat(), "why": "aucun lien avec la chaîne principale"}


def _discover_from_twitch(creators: watch.Creators, twitch_seen: dict[str, str], ytdlp: YtDlp, now: datetime, *,
                          budget: int) -> None:
    """New creators: the YouTube channels of French streamers seen live on Twitch lately, `budget` lookups a run.

    The search keeps channels titled with the streamer's very name; one is only taken when its description links to
    that Twitch channel (twitch.tv/<login>), so that a namesake is never taken for the streamer. Each login is looked
    up once a month at most.
    """
    tried = creators.lookups
    recent = sorted((at, login) for login, at in twitch_seen.items()
                    if (parse_time(at) or now - timedelta(days=99)) >= now - timedelta(days=2))  # fmt: skip
    for _at, login in reversed(recent):
        if budget <= 0:
            return
        last = parse_time(tried.get(login))
        if login in creators.creators or (last is not None and now - last < timedelta(days=30)):
            continue
        budget -= 1
        tried[login] = now.isoformat()
        try:
            results = ytdlp.list_flat(SEARCH_CHANNELS.format(quote(login)), 8)
        except DownloadError as exc:
            if exc.blocked:
                raise
            continue
        for entry in results[:8]:
            title = entry.get("channel") or entry.get("title") or ""
            link = _link(entry)
            if not link or _plain(title) != _plain(login):
                continue
            try:
                _found_as, info = _channel_listing(ytdlp, link, "videos", 1)
            except DownloadError as exc:
                if exc.blocked:
                    raise
                continue
            if f"twitch.tv/{login}" not in (info.get("description") or "").lower().replace("www.", ""):
                continue
            key = creators.add_creator(link, source="twitch", verified=f"sa description renvoie vers twitch.tv/{login}",
                                       now=now, name=info.get("channel") or title)
            watch.record_details(creators.creators[key]["channels"][watch.key_of(link)], info, now)
            log.info("Nouveau créateur suivi sur YouTube : %s (%s), vu en live sur Twitch", info.get("channel") or title, link)
            break


def _note_category(creators: watch.Creators, info: dict[str, Any]) -> None:
    """The YouTube categories of a channel's videos (Gaming, Entertainment…), counted on its record."""
    found = creators.find_channel(info.get("channel_id") or "")
    if found is None:
        return
    channel = found[1]
    for category in info.get("categories") or []:
        channel.setdefault("categories", {})[category] = channel.get("categories", {}).get(category, 0) + 1
