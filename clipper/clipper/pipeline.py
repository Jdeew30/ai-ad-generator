from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .ai import ClaudeCurator, Review, ReviewItem, clip_title, template_compilation_meta, template_review
from .captions import clean
from .compilation import Part, chapters_text, render_compilation
from .config import Config, ConfigError, Paths, Secrets
from .facecam import best_frame, face_stats, faces_per_frame, plan_layout
from .github_secrets import secret_saver
from .http import Http
from .media import MediaError, contact_sheet, extract_frames, max_volume, probe
from .models import Candidate, CompilationMeta, PublishResult, ShortMeta, Transcript, parse_time, utcnow
from .publishers import PublishError, build_publishers
from .publishers.tiktok import TikTokPublisher, caption as tiktok_caption
from .publishers.youtube import short_description
from .render import render_short
from .selection import rank
from .sources import discover
from .sources.manual import QueueError, manual_candidate
from .sources.twitch import SourceError
from .state import State
from .thumbnail import make_thumbnail
from .transcribe import Transcriber, TranscriptionError
from .ytdlp import DownloadError, YtDlp

log = logging.getLogger(__name__)

# With Claude, more batches of candidates are reviewed while the previous ones do not give enough shorts.
REVIEW_ROUNDS = 3
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
            media, _ = self.ytdlp.download(
                f"https://www.twitch.tv/videos/{candidate.vod_id}", workdir, "replay", start=start, end=end, exact=False
            )
            duration = probe(media).duration
            level = max_volume(media)
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

    def prepare(self, candidate: Candidate, *, transcribe: bool, sheet: bool, context: bool = False) -> Prepared | None:
        """Downloads, then frames and transcript. With `context`, a Twitch clip comes with the replay around it."""
        workdir = self._workdir(_slug(candidate.key))
        replay = self._replay(candidate, workdir) if context else None
        try:
            if replay:
                media, clip_span = replay
            else:
                media, info = self.ytdlp.download(candidate.url, workdir, "source", start=candidate.start, end=candidate.end)
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

    def review(self, prepared: list[Prepared]) -> dict[str, Review]:
        reviews: dict[str, Review] = {}
        pending: list[ReviewItem] = []
        for item in prepared:
            cached = self.state.cached_review(item.candidate.key)
            span = list(item.clip_span) if item.clip_span else None
            if cached and cached.get("clip_span") == span:  # trims only fit the extract they were chosen on
                reviews[item.candidate.key] = Review.from_dict(cached)
            else:
                pending.append(ReviewItem(item.candidate, item.duration, item.transcript, item.sheet, item.clip_span))
        if pending and self.curator is not None:
            reviews.update(self.curator.review(pending, now=self.now))
        for item in pending:
            reviews.setdefault(item.candidate.key, template_review(item, self.config))
        for item in pending:
            review = reviews[item.candidate.key]
            if review.meta.ai:  # only Claude's opinion is worth remembering between runs
                span = list(item.clip_span) if item.clip_span else None
                self.state.store_review(item.candidate.key, {**review.to_dict(), "clip_span": span})
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

    def run_shorts(self, *, dry_run: bool = False, count: int | None = None, url: str | None = None,
                   start: str | None = None, end: str | None = None, queue: Path | None = None) -> RunReport:
        report = RunReport("shorts", dry_run)
        if not dry_run:
            _ = self.publishers  # missing credentials for a required platform: fail before any work
        try:
            self._shorts(report, dry_run=dry_run, count=count, url=url, start=start, end=end, queue=queue)
        finally:
            if not dry_run:
                self.state.save()
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
                start: str | None, end: str | None, queue: Path | None) -> None:
        wanted = self.config.shorts.per_run if count is None else count
        if url:
            try:
                ranked = [manual_candidate(url, start, end)]
            except QueueError as exc:
                report.errors.append(str(exc))
                return
            wanted = 1
        else:
            window = timedelta(hours=self.config.twitch.window_hours)
            try:
                found = discover(self.config, self.secrets, self.http, self.ytdlp, now=self.now, window=window, queue=queue,
                                 memory=self.state.streamers_seen)
            except (SourceError, QueueError) as exc:
                log.error("Découverte impossible : %s", exc)
                report.errors.append(f"découverte : {exc}")
                return
            ranked = rank(found, self.state, self.config, now=self.now)
        if wanted <= 0 or not ranked:
            log.info("Aucun candidat à traiter.")
            return

        use_ai = self.curator is not None
        # Without AI the popularity order is final: only the clips that will be posted are prepared.
        batch = wanted * self.config.shorts.candidates_factor if use_ai else wanted
        remaining = iter(ranked)
        approved: list[tuple[Prepared, Review]] = []
        unjudged = 0
        for round_number in range(REVIEW_ROUNDS if use_ai else 1):
            if round_number:
                log.info("Pas assez de clips retenus : revue d'une nouvelle série de candidats.")
            prepared: list[Prepared] = []
            for candidate in remaining:
                log.info("Préparation : %s — %s (%s vues)", candidate.streamer or candidate.url, candidate.title or "?", candidate.views)
                item = self.prepare(candidate, transcribe=self.config.shorts.captions or use_ai,
                                    sheet=use_ai and self.config.ai.review_frames, context=use_ai)
                if item is not None:
                    prepared.append(item)
                    if len(prepared) >= batch:
                        break
            if not prepared:
                break
            reviews = self.review(prepared)
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
                    continue
                approved.append((item, review))
            if len(approved) >= wanted:
                break
        approved.sort(key=lambda pair: (pair[0].candidate.manual, pair[1].score), reverse=True)
        if not approved and report.rejected and not dry_run:
            examined = len(report.rejected)
            if unjudged == examined:
                self.notify("⚠️ Pas de short à ce passage : Claude n'a pas répondu (clé, crédits ou panne ?). "
                            "Les clips seront réexaminés.")
            else:
                self.notify("⏭️ Pas de short à ce passage : " + (
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
                if not dry_run:
                    self.state.record_failure(candidate, str(exc))
                continue
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
            report.published.append(outcome)
            log.info("Prêt : %s", video)

    # --- compilation -------------------------------------------------------------------------

    def _compilation(self, report: RunReport, *, dry_run: bool, count: int | None) -> None:
        settings = self.config.compilation
        wanted = count or settings.clips
        window = timedelta(days=settings.window_days)
        try:
            found = discover(self.config, self.secrets, self.http, self.ytdlp, now=self.now, window=window, per_streamer=10,
                             memory=self.state.streamers_seen)
        except SourceError as exc:
            log.error("Découverte impossible : %s", exc)
            report.errors.append(f"découverte : {exc}")
            return
        ranked = [c for c in rank(found, self.state, self.config, now=self.now, compilation=True) if not c.manual]
        if len(ranked) < settings.min_clips:
            log.info("Pas assez de clips pour une compilation (%d sur %d).", len(ranked), settings.min_clips)
            return

        use_ai = self.curator is not None
        prepared: list[Prepared] = []
        budget = settings.max_minutes * 60
        used = 0.0
        for candidate in ranked:
            if len(prepared) >= wanted or used >= budget:
                break
            log.info("Préparation : %s — %s (%s vues)", candidate.streamer or candidate.url, candidate.title or "?", candidate.views)
            item = self.prepare(candidate, transcribe=use_ai, sheet=use_ai and self.config.ai.review_frames)
            if item is None:
                continue
            prepared.append(item)
            used += item.duration
        reviews = self.review(prepared)
        kept = [(item, reviews[item.candidate.key]) for item in prepared if reviews[item.candidate.key].verdict != "reject"]
        for item, review in ((i, reviews[i.candidate.key]) for i in prepared if reviews[i.candidate.key].verdict == "reject"):
            report.rejected.append((item.candidate, review.reason))
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
