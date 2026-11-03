from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import threading
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .ai import ClaudeCurator, Review, ReviewItem, clip_title, template_compilation_meta, template_review
from .captions import clean
from .compilation import Part, chapters_text, clock, render_compilation
from .config import Config, ConfigError, Paths, Secrets
from .facecam import best_frame, face_stats, faces_per_frame, plan_layout
from .github_secrets import secret_saver
from .http import Http
from .media import MediaError, contact_sheet, extract_frames, join_overlapping, max_volume, probe
from .models import Candidate, CompilationMeta, PublishResult, ShortMeta, Transcript, parse_time, utcnow
from .publishers import PublishError, build_publishers
from .publishers.tiktok import TikTokPublisher, caption as tiktok_caption
from .publishers.youtube import short_description
from .render import render_short
from . import dashboard
from .selection import rank, spread
from .sources import discover, focus_config
from .sources.watch import Creators
from .sources.manual import QueueError, joined_candidate, links, manual_candidate, parse_timestamp
from .sources.twitch import SourceError
from .state import State
from .thumbnail import make_thumbnail
from .transcribe import Transcriber, TranscriptionError, context, locate, occurrences
from .ytdlp import DownloadError, YtDlp

log = logging.getLogger(__name__)

# With Claude, more batches of candidates are reviewed while the previous ones do not give enough shorts.
REVIEW_ROUNDS = 4
FIND_BEFORE, FIND_AFTER = 15.0, 35.0  # passage kept around a phrase looked for: its setup, then what it leads to
# Claude sees what was published lately: never the same moment twice, and "(partie 2)" for the next part of a story.
RECENT_DAYS = 3
RECENT_SHORTS = 15
MUTED_DB = -50.0  # a replay whose loudest sound is quieter than this was muted by Twitch (copyrighted music)


@dataclass
class Prepared:
    candidate: Candidate
    media: Path
    duration: float
    frames: list[Path] = field(default_factory=list)
    transcript: Transcript | None = None
    sheet: Path | None = None
    clip_span: tuple[float, float] | None = None  # where the original clip sits in `media` (None: media is the clip)


@dataclass
class ShortOutcome:
    candidate: Candidate
    video: Path
    meta: ShortMeta
    layout: str
    results: list[PublishResult] = field(default_factory=list)
    score: float | None = None  # Claude's mark out of 100 (None with the template)
    duration: float = 0.0  # length of the finished short, in seconds


@dataclass
class RunReport:
    mode: str
    dry_run: bool
    published: list[ShortOutcome] = field(default_factory=list)
    rejected: list[tuple[Candidate, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    compilation: dict[str, Any] | None = None
    detected: dict[str, int] = field(default_factory=dict)  # moments found, by source
    prepared: int = 0  # downloaded and prepared (frames, transcript)
    failed: int = 0  # downloads, edits or sends that failed

    def counts(self, at: datetime) -> dict[str, Any]:
        """What the run did, for the dashboard."""
        return {"at": at.isoformat(), "mode": self.mode, "detected": sum(self.detected.values()), "by_source": self.detected,
                "prepared": self.prepared, "rejected": len(self.rejected), "published": len(self.published),
                "failed": self.failed, "errors": len(self.errors)}  # fmt: skip

    def markdown(self) -> str:
        lines = [f"## Clipper — {self.mode}{' (essai à blanc)' if self.dry_run else ''}", ""]
        for outcome in self.published:
            lines.append(f"### {outcome.meta.title}")
            lines.append(f"- Source : [{outcome.candidate.streamer}]({outcome.candidate.url}) · {outcome.candidate.views} vues · "
                         f"mise en page `{outcome.layout}` · {outcome.duration:.0f} s")
            mark = f" · note {outcome.score:.0f}/100" if outcome.score is not None else ""
            lines.append(f"- Accroche : « {outcome.meta.hook} » · textes {'Claude' if outcome.meta.ai else 'gabarit (sans IA)'}{mark}")
            for result in outcome.results:
                status = "✅" if result.ok else "❌"
                target = f" — {result.url}" if result.url else ""
                lines.append(f"- {status} {result.platform}{target}{f' ({result.detail})' if result.detail else ''}")
            lines.append("")
        if self.compilation:
            lines.append(f"### Compilation : {self.compilation['title']}")
            if self.compilation.get("duration"):
                minutes, seconds = divmod(int(self.compilation["duration"]), 60)
                lines.append(f"- Durée : {minutes} min {seconds:02d} s · textes {'Claude' if self.compilation.get('ai') else 'gabarit (sans IA)'}")
            for clip in self.compilation.get("clips", []):
                lines.append(f"- #{clip['rank']} [{clip['streamer']}]({clip['url']}) — {clip['label']} ({clip['views']} vues)")
            for result in self.compilation.get("results", []):
                status = "✅" if result.ok else "❌"
                lines.append(f"- {status} {result.platform}{f' — {result.url}' if result.url else ''}{f' ({result.detail})' if result.detail else ''}")
            lines.append("")
        if self.rejected:
            lines.append("### Écartés par la revue")
            lines += [f"- {c.streamer} — {c.title or c.url} : {reason}" for c, reason in self.rejected]
            lines.append("")
        if self.errors:
            lines.append("### Erreurs")
            lines += [f"- {error}" for error in self.errors]
            lines.append("")
        if not (self.published or self.compilation or self.rejected or self.errors):
            lines.append("Rien de nouveau à publier pour ce passage.")
        return "\n".join(lines)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:80] or "clip"


SOURCE_NAMES = {"twitch": "Twitch", "youtube": "YouTube"}
COMPILATION_BATCH = 10  # clips reviewed together while the best-of is filled up to its length
COMPILATION_PREPARE_SECONDS = 50 * 60
# A download that stalls (a replay served at a crawl, ffmpeg waiting on a dead stream) once held a run for 71 minutes
# until the job's timeout cancelled it with nothing sent: a clip is given up after PREPARE_SECONDS, and no new clip is
# prepared once the run has spent SHORTS_PREPARE_SECONDS, so that the clips already approved still go out in time.
PREPARE_SECONDS = 4 * 60  # a clip takes 20 s to 2 min to prepare: one still at it after 4 min is stalled
SHORTS_PREPARE_SECONDS = 55 * 60
# The replay around a Twitch clip comes in 5 to 20 s. Nearly every run of 03/11 had one that never came, which cost
# 4 minutes and the clip: after REPLAY_SECONDS the replay is dropped and the clip goes alone.
REPLAY_SECONDS = 60


class _Overtime(BaseException):
    """Raised by the watchdog in the middle of a preparation that takes too long (not an Exception: yt-dlp, which
    catches those, lets it through)."""


class _SlowReplay(BaseException):
    """Raised by the watchdog when the replay around a Twitch clip does not come in time."""


@contextmanager
def _watchdog(seconds: float, overtime: type[BaseException] = _Overtime) -> Iterator[None]:
    """Interrupts the block with `overtime` after `seconds` (main thread only, where signals are delivered).

    Inside another watchdog (the replay within a clip's preparation), the outer one rings instead if it is due
    first, and keeps the time it had left once the block is over.
    """
    if seconds <= 0 or not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return
    outer, _ = signal.getitimer(signal.ITIMER_REAL)
    if outer and outer <= seconds:
        yield
        return

    def ring(_signum: int, _frame: Any) -> None:
        raise overtime()

    previous = signal.signal(signal.SIGALRM, ring)
    started = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if outer:
            signal.setitimer(signal.ITIMER_REAL, max(outer - (time.monotonic() - started), 0.001))


def _stop_children() -> None:
    """Kills what a stopped preparation left running (ffmpeg started by yt-dlp keeps reading a stalled stream)."""
    me = os.getpid()
    try:
        pids = [int(name) for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return
    for pid in pids:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            parent = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        if parent == me:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _kept_length(item: Prepared, review: Review) -> float:
    """How long the clip lasts in the best-of, once trimmed by the review."""
    start = review.meta.trim_start or 0.0
    end = review.meta.trim_end if review.meta.trim_end is not None else item.duration
    return max(0.0, end - start)


def _platform(candidate: Candidate) -> str:
    """twitch or youtube, a link added by hand included."""
    if candidate.source != "manual":
        return candidate.source
    return "youtube" if re.search(r"youtube\.com|youtu\.be", candidate.url) else "twitch"


def refusal(review: Review, candidate: Candidate, min_score: float) -> str | None:
    """Why a reviewed clip must not be posted: Claude's veto, or a mark under the bar.

    URLs added by hand always pass. Template reviews are not marked on quality (their score is popularity).
    """
    if candidate.manual:
        return None
    if review.verdict == "reject":
        return review.reason or "refusé par Claude"
    if review.meta.ai and review.score < min_score:
        return f"note {review.score:.0f}/100 (minimum {min_score:.0f}). {review.reason}".strip()
    return None


class Clipper:
    """Discovery -> download -> transcription -> review -> edit -> publish, with the state kept between runs."""

    def __init__(
        self,
        config: Config,
        secrets: Secrets,
        paths: Paths,
        *,
        http: Http | None = None,
        ytdlp: YtDlp | None = None,
        transcriber: Transcriber | None = None,
        curator: Any = None,
        publishers: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        self.config = config
        self.secrets = secrets
        self.paths = paths
        self.http = http or Http()
        self.ytdlp = ytdlp or YtDlp(cookies_file=self._cookies_file())
        self.now = now or utcnow()
        self.state = State.load(paths.state)
        self.creators = self._creators()
        self.transcriber = transcriber or Transcriber(
            config.shorts.whisper_model, config.channel.language, cache_dir=paths.data / "models"
        )
        self.curator = curator if curator is not None else self._curator()
        self._publishers = publishers

    # --- setup -------------------------------------------------------------------------------

    def _cookies_file(self) -> Path | None:
        cookies = self.secrets.ytdlp_cookies
        if not cookies:
            return None
        if Path(cookies).is_file():
            return Path(cookies)
        target = self.paths.data / "cookies.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(cookies if cookies.endswith("\n") else cookies + "\n", encoding="utf-8")
        target.chmod(0o600)
        return target

    def _curator(self) -> ClaudeCurator | None:
        setting = self.config.ai.enabled
        if setting is False:
            return None
        if not self.secrets.anthropic_api_key:
            if setting is True:
                raise ConfigError("ai.enabled vaut true mais ANTHROPIC_API_KEY est absente")
            log.info("ANTHROPIC_API_KEY absente : sélection par popularité et textes par gabarit (sans IA).")
            return None
        return ClaudeCurator(self.secrets.anthropic_api_key, self.config)

    @property
    def publishers(self) -> dict[str, Any]:
        if self._publishers is None:
            self._publishers = build_publishers(self.config, self.secrets, self.http)
            tiktok = self._publishers.get("tiktok")
            if isinstance(tiktok, TikTokPublisher):
                tiktok.on_new_refresh_token = secret_saver(
                    self.http, self.secrets.gh_secrets_token, self.secrets.github_repository, "TIKTOK_REFRESH_TOKEN"
                )
        return self._publishers

    def _workdir(self, name: str) -> Path:
        path = self.paths.work / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        return path

    # --- preparation -------------------------------------------------------------------------

    def _enrich(self, candidate: Candidate, info: dict[str, Any]) -> None:
        """Fills what the listing did not know (yt-dlp metadata of the downloaded media)."""
        if not candidate.title:
            candidate.title = (info.get("title") or "").strip()
        channel = info.get("channel")
        if not channel and "twitch.tv" not in candidate.url:
            channel = info.get("uploader")  # on Twitch clips the uploader is the viewer who clipped, not the streamer
        if channel and (not candidate.streamer or candidate.streamer == candidate.streamer_login):
            candidate.streamer = self.config.override_for(candidate.streamer_login).name or channel
        if not candidate.streamer_login:
            handle = (info.get("uploader_id") or info.get("channel_id") or "").lstrip("@")
            candidate.streamer_login = (handle or channel or "").lower()
        if not candidate.channel_url:
            candidate.channel_url = info.get("channel_url") or info.get("uploader_url") or ""
            if not candidate.channel_url and "twitch.tv" in candidate.url and candidate.streamer_login:
                candidate.channel_url = f"https://www.twitch.tv/{candidate.streamer_login}"
        if not candidate.game and info.get("categories"):
            candidate.game = info["categories"][0]
        if not candidate.views and info.get("view_count"):
            candidate.views = int(info["view_count"])
        if candidate.created_at is None:
            candidate.created_at = parse_time(info.get("timestamp"))
        if not candidate.clipper and candidate.source == "twitch":
            candidate.clipper = info.get("uploader")

    def _replay(self, candidate: Candidate, workdir: Path) -> tuple[Path, tuple[float, float]] | None:
        """The clip with some of the live around it, cut from the replay (VOD): the setup and the end of the moment.

        None when the clip's place in the replay is unknown, or when the replay is unavailable (deleted, reserved
        for subscribers), incomplete or muted by Twitch: the clip alone is used then.
        """
        rules = self.config.shorts
        if candidate.manual or candidate.source != "twitch" or not candidate.vod_id or candidate.vod_offset is None:
            return None
        if not candidate.duration or rules.context_before + rules.context_after <= 0:
            return None
        start = max(0.0, candidate.vod_offset - rules.context_before)
        end = candidate.vod_offset + candidate.duration + rules.context_after
        clip = (round(candidate.vod_offset - start, 2), round(candidate.vod_offset - start + candidate.duration, 2))
        try:
            # Copied, not re-encoded (minutes for a 1080p60 section): the render re-encodes it anyway.
            with _watchdog(REPLAY_SECONDS, _SlowReplay):
                media, _ = self.ytdlp.download(
                    f"https://www.twitch.tv/videos/{candidate.vod_id}", workdir, "replay", start=start, end=end, exact=False
                )
            duration = probe(media).duration
            level = max_volume(media)
        except _SlowReplay:
            _stop_children()  # the ffmpeg still waiting on the replay's stream
            log.info("Replay toujours pas là au bout de %d s, clip seul.", REPLAY_SECONDS)
            return None
        except (DownloadError, MediaError) as exc:
            log.info("Replay indisponible, clip seul : %s", exc)
            return None
        if duration < clip[1] - 1.0:
            log.info("Replay incomplet (%.0f s), clip seul.", duration)
            return None
        if level is None or level < MUTED_DB:
            log.info("Replay muet (son coupé par Twitch), clip seul.")
            return None
        log.info("Replay : %.0f s de contexte avant le clip, %.0f s après.", clip[0], duration - clip[1])
        return media, clip

    def _joined(self, candidate: Candidate, workdir: Path) -> tuple[Path, dict[str, Any]]:
        """The clips of one moment, downloaded and put back together; the first one's details."""
        downloads = [self.ytdlp.download(url, workdir, f"part{index}") for index, url in enumerate(candidate.parts)]
        media = join_overlapping([path for path, _ in downloads], workdir / "joined.mp4")
        log.info("%d clips recollés : %.0f s", len(downloads), probe(media).duration)
        return media, downloads[0][1]

    def prepare(self, candidate: Candidate, *, transcribe: bool, sheet: bool, context: bool = False) -> Prepared | None:
        """Downloads, then frames and transcript. With `context`, a Twitch clip comes with the replay around it."""
        workdir = self._workdir(_slug(candidate.key))
        replay = self._replay(candidate, workdir) if context else None
        try:
            if replay:
                media, clip_span = replay
            elif candidate.parts:
                media, info = self._joined(candidate, workdir)
                self._enrich(candidate, info)
                clip_span = None
            else:
                # A section is copied, not re-encoded (minutes, or more on a long live's replay): the render re-encodes it.
                media, info = self.ytdlp.download(candidate.url, workdir, "source", start=candidate.start, end=candidate.end,
                                                  exact=False)
                self._enrich(candidate, info)
                clip_span = None
            if candidate.streamer_login and self.config.is_blocked(candidate.streamer_login):
                self.state.record_rejection(candidate, "chaîne sur liste noire")
                return None
            details = probe(media)
        except DownloadError as exc:
            log.warning("Téléchargement impossible (%s) : %s", candidate.url, exc)
            if exc.permanent:
                self.state.record_rejection(candidate, f"indisponible : {exc}")
            else:
                self.state.record_failure(candidate, str(exc))
            return None
        except MediaError as exc:
            log.warning("Fichier illisible (%s) : %s", candidate.url, exc)
            self.state.record_failure(candidate, str(exc))
            return None
        prepared = Prepared(candidate=candidate, media=media, duration=details.duration, clip_span=clip_span)
        try:
            prepared.frames = extract_frames(media, workdir / "frames", count=8, duration=details.duration)
            if sheet and prepared.frames:
                prepared.sheet = contact_sheet(prepared.frames[1::2][:4], workdir / "sheet.jpg")
        except MediaError as exc:
            log.warning("Images de %s indisponibles : %s", candidate.key, exc)
        if transcribe:
            hint = f"Live de {candidate.streamer}" + (f" sur {candidate.game}" if candidate.game else "") + ". "
            hint += clean(candidate.title)
            try:
                prepared.transcript = self.transcriber.transcribe(media, prompt=hint)
            except TranscriptionError as exc:
                log.warning("Sous-titres indisponibles : %s", exc)
            else:
                text = prepared.transcript.text
                excerpt = text if len(text) <= 160 else text[:159] + "…"
                log.info("Transcription : %d mot(s)%s", len(prepared.transcript.words), f" — « {excerpt} »" if text else "")
        return prepared

    # --- review ------------------------------------------------------------------------------

    def recent_shorts(self) -> list[tuple[datetime | None, str]]:
        """(date, title) of the latest shorts, most recent first."""
        entries = self.state.recent_posts(self.now - timedelta(days=RECENT_DAYS))[:RECENT_SHORTS]
        return [(parse_time(entry.get("at")), entry.get("title") or entry.get("streamer") or "?") for entry in entries]

    def review(self, prepared: list[Prepared], *, recent: list[tuple[datetime | None, str]] | None = None) -> dict[str, Review]:
        reviews: dict[str, Review] = {}
        pending: list[ReviewItem] = []
        known = [title for _, title in recent or []]
        for item in prepared:
            cached = self.state.cached_review(item.candidate.key)
            span = list(item.clip_span) if item.clip_span else None
            # Trims only fit the extract they were chosen on, and a verdict only holds for the shorts published by then.
            if cached and cached.get("clip_span") == span and cached.get("recent", []) == known:
                reviews[item.candidate.key] = Review.from_dict(cached)
            else:
                pending.append(ReviewItem(item.candidate, item.duration, item.transcript, item.sheet, item.clip_span))
        if pending and self.curator is not None:
            reviews.update(self.curator.review(pending, now=self.now, recent=recent))
        for item in pending:
            reviews.setdefault(item.candidate.key, template_review(item, self.config))
        for item in pending:
            review = reviews[item.candidate.key]
            if review.meta.ai:  # only Claude's opinion is worth remembering between runs
                span = list(item.clip_span) if item.clip_span else None
                self.state.store_review(item.candidate.key, {**review.to_dict(), "clip_span": span, "recent": known})
        return reviews

    # --- shorts ------------------------------------------------------------------------------

    def _render(self, item: Prepared, meta: ShortMeta) -> tuple[Path, str]:
        candidate = item.candidate
        info = probe(item.media)
        override = self.config.override_for(candidate.streamer_login)
        requested = override.layout or self.config.shorts.layout
        stats = None
        if item.frames and requested != "blur" and override.facecam is None:
            faces = [face for _, face in faces_per_frame(item.frames) if face is not None]
            stats = face_stats(faces, len(item.frames))
            log.info("Visage trouvé sur %d image(s) sur %d", len(faces), len(item.frames))
        plan = plan_layout(info, stats, requested=requested, facecam=override.facecam)
        log.info("Mise en page « %s » : %s", plan.kind, plan.reason)
        stamp = self.now.strftime("%Y%m%d-%H%M")
        out = self.paths.output / f"{stamp}_{_slug(candidate.streamer_login or candidate.streamer)}_{_slug(candidate.key.split(':', 1)[-1])}.mp4"
        render_short(
            item.media,
            out,
            info=info,
            plan=plan,
            settings=self.config.shorts,
            workdir=item.media.parent / "render",
            words=item.transcript.words if item.transcript else None,
            hook=meta.hook,
            credit=candidate.credit,
            start=meta.trim_start,
            end=meta.trim_end,
        )
        return out, plan.kind

    def _write_sidecar(self, video: Path, candidate: Candidate, meta: ShortMeta) -> None:
        """Everything needed to post by hand, next to the video."""
        data = {
            "source": candidate.to_dict(),
            "hook": meta.hook,
            "youtube": {"title": meta.title, "description": short_description(meta, candidate)},
            "tiktok": {"caption": tiktok_caption(meta, candidate)},
            "ai": meta.ai,
        }
        video.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        video.with_suffix(".txt").write_text(
            f"{meta.title}\n\n--- YouTube ---\n{short_description(meta, candidate)}\n\n--- TikTok ---\n{tiktok_caption(meta, candidate)}\n",
            encoding="utf-8",
        )

    def publish_short(self, video: Path, meta: ShortMeta, candidate: Candidate) -> list[PublishResult]:
        results: list[PublishResult] = []
        for name in ("youtube", "tiktok"):
            publisher = self.publishers.get(name)
            if publisher is None:
                continue
            if name == "youtube" and candidate.source == "youtube" and not self.config.publish.youtube.youtube_sources:
                log.info("youtube : extrait d'une vidéo YouTube, pas republié sur YouTube (Content ID).")
                continue
            try:
                result = publisher.publish_short(video, meta, candidate)
                log.info("%s : publié %s", name, result.url or result.id or "")
            except PublishError as exc:
                log.error("%s : %s", name, exc)
                result = PublishResult(name, False, detail=str(exc))
            results.append(result)
        telegram = self.publishers.get("telegram")
        if telegram is not None:
            try:
                results.append(telegram.publish_short(video, meta, candidate, results))
            except PublishError as exc:
                log.error("telegram : %s", exc)
                results.append(PublishResult("telegram", False, detail=str(exc)))
        return results

    def notify(self, text: str) -> None:
        """A note on Telegram, so that a slot without a video is not mistaken for a breakdown."""
        telegram = self.publishers.get("telegram")
        if telegram is None:
            return
        try:
            telegram.message(text)
        except PublishError as exc:
            log.error("telegram : %s", exc)

    def _creators(self) -> Creators:
        """The base of creators and their YouTube channels, kept next to the state (creators.json)."""
        path = self.paths.state.with_name("creators.json")
        try:
            return Creators.load(path)
        except (ValueError, OSError) as exc:
            log.error("Base des créateurs illisible (%s) : repartie de la liste de config.yaml.", exc)
            return Creators(path)

    def _keep_track(self, report: RunReport) -> None:
        """The run's counts in the state, the base of creators saved and the dashboard rewritten."""
        self.state.runs.append(report.counts(self.now))
        self.state.save()
        try:
            self.creators.save()
            self.paths.state.with_name("dashboard.md").write_text(dashboard.render(self.state, self.creators, self.now),
                                                                  encoding="utf-8")
        except OSError as exc:
            log.error("Tableau de bord non écrit : %s", exc)

    def run_shorts(self, *, dry_run: bool = False, count: int | None = None, url: str | None = None,
                   start: str | None = None, end: str | None = None, queue: Path | None = None,
                   focus: list[str] | None = None, force: bool = False, find: str | None = None) -> RunReport:
        """With `force` (a clip asked for by hand), the best clip Claude marked goes out even under the bar.

        With `find`, the passage of `url` where that phrase is said is cut out (searched from `start` to `end`).
        """
        report = RunReport("shorts", dry_run)
        if not dry_run:
            _ = self.publishers  # missing credentials for a required platform: fail before any work
        self._prepare_until = time.monotonic() + SHORTS_PREPARE_SECONDS
        try:
            self._shorts(report, dry_run=dry_run, count=count, url=url, start=start, end=end, queue=queue, focus=focus,
                         force=force, find=find)
        finally:
            if not dry_run:
                self._keep_track(report)
            self._cleanup()
        return report

    def run_compilation(self, *, dry_run: bool = False, count: int | None = None) -> RunReport:
        report = RunReport("compilation", dry_run)
        if not dry_run:
            _ = self.publishers
        try:
            self._compilation(report, dry_run=dry_run, count=count)
        finally:
            if not dry_run:
                self.state.save()
            self._cleanup()
        return report

    def _cleanup(self) -> None:
        """Downloads are only needed during the run; the finished videos stay in the output folder."""
        shutil.rmtree(self.paths.work, ignore_errors=True)

    def _shorts(self, report: RunReport, *, dry_run: bool, count: int | None, url: str | None,
                start: str | None, end: str | None, queue: Path | None, focus: list[str] | None = None,
                force: bool = False, find: str | None = None) -> None:
        wanted = self.config.shorts.per_run if count is None else count
        # A scheduled run takes so many shorts of each source; a count, a focus or an URL asked for by hand do not.
        quotas = self.config.shorts.per_source if count is None and not focus and not url else {}
        if url:
            try:
                urls = links(url)
                joined = len(urls) > 1 and all(link.startswith(("http://", "https://")) for link in urls)
                if joined and (start or end or find):
                    raise QueueError("début, fin et phrase ne s'appliquent pas à plusieurs clips recollés")
                if find:
                    start, end = self._locate(url, find, start, end)
                ranked = [joined_candidate(urls) if joined else manual_candidate(url, start, end)]
            except QueueError as exc:
                report.errors.append(str(exc))
                return
            wanted = 1
        else:
            window = timedelta(hours=self.config.twitch.window_hours)
            config = self.config
            try:
                if focus:
                    config = focus_config(self.config, focus)
                    log.info("Passage ciblé : %s", ", ".join(focus))
                    found = discover(config, self.secrets, self.http, self.ytdlp, now=self.now, window=window)
                else:
                    found = discover(config, self.secrets, self.http, self.ytdlp, now=self.now, window=window, queue=queue,
                                     memory=self.state.streamers_seen, youtube_offset=self.state.youtube_cursor,
                                     youtube_related=self.state.youtube_related, creators=self.creators,
                                     youtube_history=self.state.youtube_videos,
                                     known=set(self.state.posted) | set(self.state.rejected))
                    self.state.youtube_cursor += config.youtube.channels_per_run
            except (SourceError, QueueError, ConfigError) as exc:
                log.error("Découverte impossible : %s", exc)
                report.errors.append(f"découverte : {exc}")
                return
            report.detected = dict(Counter(_platform(c) for c in found))
            ranked = spread(rank(found, self.state, config, now=self.now))
        if not quotas:
            self._publish_best(report, ranked, wanted, dry_run=dry_run, force=force)
            return
        guarantee = self.config.shorts.guarantee
        for source, quota in quotas.items():
            # A scheduled run keeps its short of each source: under the bar, its best one if marked `guarantee` or more.
            self._publish_best(report, [c for c in ranked if _platform(c) == source], quota, dry_run=dry_run, source=source,
                               force=force or source in guarantee, floor=0.0 if force else guarantee.get(source, 0.0),
                               at_least=None if force else 1)

    def _locate(self, url: str, phrase: str, start: str | None, end: str | None) -> tuple[float, float]:
        """The passage of `url` around `phrase`, which Whisper listens for from `start` to `end`."""
        begin, finish = parse_timestamp(start), parse_timestamp(end)
        where = f"de {clock(begin or 0.0)} à {clock(finish) if finish is not None else 'la fin'}"
        log.info("Recherche de « %s » %s…", phrase, where)
        try:
            # The whole sound comes at once (a section is fetched by ffmpeg at playing speed): only the part is heard.
            media, _ = self.ytdlp.download(url.strip(), self.paths.work / "find", "search", audio_only=True)
            words = self.transcriber.transcribe(media, start=begin, end=finish, quick=True).words
        except (DownloadError, TranscriptionError, MediaError) as exc:
            raise QueueError(f"recherche de « {phrase} » impossible : {exc}") from exc
        log.info("Whisper a entendu %d mot(s)%s.", len(words),
                 f", de {clock((begin or 0.0) + words[0].start)} à {clock((begin or 0.0) + words[-1].end)}" if words else "")
        for at, ratio in occurrences(words, phrase):  # several? the log shows each, to search a narrower part
            log.info("  %s (%.0f %%) : « %s »", clock((begin or 0.0) + at), ratio * 100, context(words, at))
        heard = locate(words, phrase)
        if heard is None:
            raise QueueError(f"« {phrase} » introuvable {where} : une autre phrase, ou un autre passage ?")
        at = (begin or 0.0) + heard
        log.info("« %s » entendu à %s.", phrase, clock(at))
        return max(0.0, at - FIND_BEFORE), at + FIND_AFTER

    def _prepare_in_time(self, candidate: Candidate, *, dry_run: bool, **options: Any) -> Prepared | None:
        """prepare(), given up after PREPARE_SECONDS: a stalled download never holds the whole run."""
        try:
            with _watchdog(PREPARE_SECONDS):
                return self.prepare(candidate, **options)
        except _Overtime:
            _stop_children()
            log.warning("Préparation abandonnée au bout de %d min (téléchargement bloqué ?) : %s — clip suivant.",
                        PREPARE_SECONDS // 60, candidate.key)
            if not dry_run:
                # Its replay stalled at every run (6 min lost three times in a row on 03/11): it is not tried again.
                self.state.record_failure(candidate, f"préparation abandonnée au bout de {PREPARE_SECONDS // 60} min",
                                          final=True)
            return None

    def _publish_best(self, report: RunReport, ranked: list[Candidate], wanted: int, *, dry_run: bool,
                      source: str | None = None, force: bool = False, floor: float = 0.0, at_least: int | None = None) -> None:
        """Reviews `ranked` until `wanted` clips are approved, then edits and publishes them.

        With `force`, when too few clips reach the bar, the best marked ones Claude did not refuse outright go out too,
        if marked `floor` or more, until `at_least` clips (`wanted` by default) go out.
        """
        label = f" {SOURCE_NAMES[source]}" if source else ""
        if wanted <= 0 or not ranked:
            log.info("Aucun candidat%s à traiter.", label)
            if wanted > 0 and not dry_run:
                self.notify(f"⏭️ Pas de short{label} à ce passage : aucun nouveau clip trouvé." + (
                    " YouTube bloque le serveur (vérification anti-robot) : des cookies YouTube (secret YTDLP_COOKIES) "
                    "lèvent ce blocage." if source != "twitch" and getattr(self.ytdlp, "blocked", False) else ""
                ))
            return

        use_ai = self.curator is not None
        examined_before = len(report.rejected)  # the other source's refusals are not this one's
        # Without AI the popularity order is final: only the clips that will be posted are prepared.
        batch = wanted * self.config.shorts.candidates_factor if use_ai else wanted
        remaining = iter(ranked)
        recent = self.recent_shorts()
        approved: list[tuple[Prepared, Review]] = []
        under_bar: list[tuple[Prepared, Review]] = []  # judged and allowed, but marked under the bar
        forced: set[str] = set()
        unjudged = 0
        for round_number in range(REVIEW_ROUNDS if use_ai else 1):
            if use_ai and getattr(self.curator, "disabled_reason", None):
                break  # Claude can judge nothing more in this run: no clip is prepared for nothing
            if round_number:
                log.info("Pas assez de clips retenus : revue d'une nouvelle série de candidats.")
            prepared: list[Prepared] = []
            for candidate in remaining:
                if time.monotonic() > getattr(self, "_prepare_until", float("inf")):
                    log.warning("Passage bientôt à court de temps : plus de nouvelle préparation, les clips retenus partent.")
                    break
                log.info("Préparation : %s — %s (%s vues)", candidate.streamer or candidate.url, candidate.title or "?", candidate.views)
                item = self._prepare_in_time(candidate, dry_run=dry_run, transcribe=self.config.shorts.captions or use_ai,
                                             sheet=use_ai and self.config.ai.review_frames, context=use_ai)
                if item is None:
                    report.failed += 1  # the next candidate takes its place
                    continue
                report.prepared += 1
                prepared.append(item)
                if len(prepared) >= batch:
                    break
            if not prepared:
                break
            # The clips approved in an earlier round count as published: their moment is taken too.
            reviews = self.review(prepared, recent=[(self.now, review.meta.title) for _, review in reversed(approved)] + recent)
            for item in prepared:
                review = reviews[item.candidate.key]
                if use_ai and not review.meta.ai and not item.candidate.manual:
                    # Claude skipped it or did not answer: a clip it has not judged (quality, rights) is never
                    # posted. It counts as a failure, so it is reviewed again later, three times at most.
                    unjudged += 1
                    log.info("Non évalué par Claude : %s — réexaminé à un prochain passage.", item.candidate.title)
                    report.rejected.append((item.candidate, "pas évalué par Claude, réexaminé à un prochain passage"))
                    if not dry_run:
                        self.state.record_failure(item.candidate, "non évalué par Claude")
                    continue
                reason = refusal(review, item.candidate, self.config.shorts.min_score)
                if reason:
                    log.info("Écarté par la revue : %s — %s", item.candidate.title, reason)
                    report.rejected.append((item.candidate, reason))
                    if not dry_run:
                        self.state.record_rejection(item.candidate, f"revue : {reason}")
                    if review.verdict != "reject":
                        under_bar.append((item, review))
                    continue
                approved.append((item, review))
            if len(approved) >= wanted:
                break
        under_bar = [pair for pair in under_bar if pair[1].score >= floor]
        enough = wanted if at_least is None else min(at_least, wanted)
        if force and len(approved) < enough and under_bar:
            under_bar.sort(key=lambda pair: pair[1].score, reverse=True)
            for item, review in under_bar[: enough - len(approved)]:
                key = item.candidate.key
                log.info("Publié quand même, à la demande : %s — note %.0f/100", item.candidate.title, review.score)
                report.rejected = [(c, why) for c, why in report.rejected if c.key != key]
                if not dry_run:
                    self.state.rejected.pop(key, None)
                approved.append((item, review))
                forced.add(key)
        approved.sort(key=lambda pair: (pair[0].candidate.manual, pair[1].score), reverse=True)
        examined = len(report.rejected) - examined_before
        stopped = getattr(self.curator, "disabled_reason", None) if use_ai else None
        if stopped and not approved and not dry_run:
            if not getattr(self, "_told_stopped", False):  # once a run, whatever the source
                log.error("Claude hors service pour ce passage : %s", stopped)
                self.notify(f"⚠️ Pas de short à ce passage : {stopped}. Les clips seront réexaminés ensuite.")
                self._told_stopped = True
        elif not approved and examined and not dry_run:
            if unjudged == examined:
                self.notify(f"⚠️ Pas de short{label} à ce passage : Claude n'a pas répondu (clé, crédits ou panne ?). "
                            "Les clips seront réexaminés.")
            else:
                self.notify(f"⏭️ Pas de short{label} à ce passage : " + (
                    f"aucun des {examined} clips examinés n'a été retenu." if examined > 1 else "le seul clip examiné n'a pas été retenu."
                ))

        for item, review in approved[:wanted]:
            candidate = item.candidate
            try:
                video, layout = self._render(item, review.meta)
                length = probe(video).duration
            except MediaError as exc:
                log.error("Montage impossible (%s) : %s", candidate.key, exc)
                report.errors.append(f"{candidate.key} : montage impossible ({exc})")
                report.failed += 1
                if not dry_run:
                    self.state.record_failure(candidate, str(exc))
                continue
            if review.meta.ai:  # Claude's mark and why, shown with the video on Telegram
                review.meta.score, review.meta.reason = review.score, review.reason
            self._write_sidecar(video, candidate, review.meta)
            outcome = ShortOutcome(candidate, video, review.meta, layout, score=review.score if review.meta.ai else None, duration=length)
            if not dry_run:
                outcome.results = self.publish_short(video, review.meta, candidate)
                failed = [r for r in outcome.results if not r.ok]
                report.errors += [f"{r.platform} : {r.detail}" for r in failed]
                if not outcome.results or any(r.ok for r in outcome.results):
                    # Without publishers the video is an artifact to post by hand: it counts as done.
                    self.state.record_post(candidate, review.meta, outcome.results)
                else:
                    self.state.record_failure(candidate, "; ".join(r.detail or r.platform for r in failed))
                    report.failed += 1
            report.published.append(outcome)
            log.info("Prêt : %s", video)
            if candidate.key in forced and not dry_run:
                why = f"pour garder un short{label} à ce passage" if floor else "à ta demande"
                self.notify(f"ℹ️ Short{label} envoyé {why} : aucun clip n'atteignait la note minimale "
                            f"({self.config.shorts.min_score:.0f}/100), celui-ci est le mieux noté ({review.score:.0f}/100).")

    # --- compilation -------------------------------------------------------------------------

    def _compilation(self, report: RunReport, *, dry_run: bool, count: int | None) -> None:
        settings = self.config.compilation
        wanted = count or settings.clips
        window = timedelta(days=settings.window_days)
        try:
            # A Twitch best-of: the weekly video goes to YouTube, where Content ID would claim YouTube extracts.
            found = discover(self.config, self.secrets, self.http, self.ytdlp, now=self.now, window=window, per_streamer=10,
                             memory=self.state.streamers_seen, include_youtube=False)
        except SourceError as exc:
            log.error("Découverte impossible : %s", exc)
            report.errors.append(f"découverte : {exc}")
            return
        ranked = [c for c in rank(found, self.state, self.config, now=self.now, compilation=True) if not c.manual]
        if len(ranked) < settings.min_clips:
            log.info("Pas assez de clips pour une compilation (%d sur %d).", len(ranked), settings.min_clips)
            return

        use_ai = self.curator is not None
        target, longest = settings.target_minutes * 60, settings.max_minutes * 60
        kept: list[tuple[Prepared, Review]] = []
        length = 0.0
        remaining = iter(ranked)
        deadline = time.monotonic() + COMPILATION_PREPARE_SECONDS  # the long render still has to fit in the job
        while len(kept) < wanted and length < target and time.monotonic() < deadline:
            prepared: list[Prepared] = []
            for candidate in remaining:
                log.info("Préparation : %s — %s (%s vues)", candidate.streamer or candidate.url, candidate.title or "?", candidate.views)
                item = self._prepare_in_time(candidate, dry_run=dry_run, transcribe=use_ai,
                                             sheet=use_ai and self.config.ai.review_frames)
                if item is not None:
                    prepared.append(item)
                if len(prepared) >= COMPILATION_BATCH or time.monotonic() >= deadline:
                    break
            if not prepared:
                break
            reviews = self.review(prepared)
            for item in prepared:
                review = reviews[item.candidate.key]
                if review.verdict == "reject":
                    report.rejected.append((item.candidate, review.reason))
                    continue
                part = _kept_length(item, review)
                if len(kept) < wanted and length < target and length + part <= longest:
                    kept.append((item, review))
                    length += part
        log.info("Compilation : %d clip(s) retenu(s), %.1f min.", len(kept), length / 60)
        if len(kept) < settings.min_clips:
            log.info("Pas assez de clips retenus pour une compilation (%d).", len(kept))
            return

        # Countdown: weakest first, the best moment last.
        kept.sort(key=lambda pair: pair[1].score)
        labels = [(item.candidate, clip_title(item.candidate) or review.meta.hook) for item, review in kept]
        first_day = (self.now - window).strftime("%d/%m")
        period = f"du {first_day} au {self.now.strftime('%d/%m/%Y')}"
        meta: CompilationMeta = (
            self.curator.compilation_meta(labels, period=period) if use_ai else template_compilation_meta(labels, self.config, period=period)
        )
        workdir = self._workdir("compilation")
        stamp = self.now.strftime("%Y%m%d")
        video = self.paths.output / f"{stamp}_compilation.mp4"
        parts = [
            Part(item.media, item.candidate, label, review.meta.trim_start, review.meta.trim_end)
            for (item, review), (_, label) in zip(kept, labels)
        ]
        try:
            chapters = render_compilation(
                parts,
                video,
                workdir=workdir,
                title=meta.thumbnail_text,
                subtitle=period,
                intro=settings.intro,
                highlight=self.config.shorts.highlight_color,
            )
        except MediaError as exc:
            report.errors.append(f"compilation : montage impossible ({exc})")
            return
        best = kept[-1][0]
        thumbnail = None
        frame = best_frame(best.frames)
        if frame is not None:
            names = " · ".join(dict.fromkeys(c.streamer for c, _ in reversed(labels)))
            thumbnail = make_thumbnail(frame, video.with_suffix(".jpg"), headline=meta.thumbnail_text, subline=names,
                                       highlight=self.config.shorts.highlight_color)
        duration = probe(video).duration
        description = self._compilation_description(meta, chapters, labels, duration)
        video.with_suffix(".txt").write_text(f"{meta.title}\n\n{description}\n", encoding="utf-8")
        results: list[PublishResult] = []
        if not dry_run:
            youtube = self.publishers.get("youtube")
            if youtube is not None and self.config.publish.youtube.compilations:
                try:
                    results.append(youtube.publish_compilation(video, meta, description, thumbnail))
                except PublishError as exc:
                    log.error("youtube : %s", exc)
                    results.append(PublishResult("youtube", False, detail=str(exc)))
            telegram = self.publishers.get("telegram")
            if telegram is not None:
                try:
                    results.append(telegram.publish_compilation(video, meta, description, thumbnail, results))
                except PublishError as exc:
                    results.append(PublishResult("telegram", False, detail=str(exc)))
            report.errors += [f"{r.platform} : {r.detail}" for r in results if not r.ok]
            if not results or any(r.ok for r in results):
                self.state.record_compilation([c.key for c, _ in labels], meta.title, results)
        clips = [
            {"rank": len(labels) - index, "streamer": candidate.streamer, "label": label, "url": candidate.url, "views": candidate.views}
            for index, (candidate, label) in enumerate(labels)
        ]
        report.compilation = {
            "title": meta.title, "video": str(video), "results": results, "duration": duration, "clips": clips, "ai": meta.ai,
        }  # fmt: skip

    def _compilation_description(self, meta: CompilationMeta, chapters: list[tuple[float, str]],
                                 labels: list[tuple[Candidate, str]], total: float) -> str:
        parts = [meta.description, ""]
        timeline = chapters_text(chapters, total)
        if timeline:
            parts += [timeline, ""]
        parts.append("🎥 Clips originaux (merci aux créateurs, allez les suivre !) :")
        for index, (candidate, label) in enumerate(labels):
            rank_number = len(labels) - index
            parts.append(f"#{rank_number} {candidate.streamer} — {label} : {candidate.url}")
        parts += ["", " ".join(f"#{tag.lstrip('#')}" for tag in meta.hashtags)]
        return "\n".join(parts)
