from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .ai import ClaudeCurator, Review, ReviewItem, template_compilation_meta, template_review
from .captions import clean
from .compilation import Part, chapters_text, render_compilation
from .config import Config, ConfigError, Paths, Secrets
from .facecam import best_frame, face_stats, faces_per_frame, plan_layout
from .github_secrets import secret_saver
from .http import Http
from .media import MediaError, contact_sheet, extract_frames, probe
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


@dataclass
class Prepared:
    candidate: Candidate
    media: Path
    duration: float
    frames: list[Path] = field(default_factory=list)
    transcript: Transcript | None = None
    sheet: Path | None = None


@dataclass
class ShortOutcome:
    candidate: Candidate
    video: Path
    meta: ShortMeta
    layout: str
    results: list[PublishResult] = field(default_factory=list)


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
            lines.append(f"- Source : [{outcome.candidate.streamer}]({outcome.candidate.url}) · {outcome.candidate.views} vues · mise en page `{outcome.layout}`")
            lines.append(f"- Accroche : « {outcome.meta.hook} » · textes {'Claude' if outcome.meta.ai else 'gabarit (sans IA)'}")
            for result in outcome.results:
                status = "✅" if result.ok else "❌"
                target = f" — {result.url}" if result.url else ""
                lines.append(f"- {status} {result.platform}{target}{f' ({result.detail})' if result.detail else ''}")
            lines.append("")
        if self.compilation:
            lines.append(f"### Compilation : {self.compilation['title']}")
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

    def prepare(self, candidate: Candidate, *, transcribe: bool, sheet: bool) -> Prepared | None:
        workdir = self._workdir(_slug(candidate.key))
        try:
            media, info = self.ytdlp.download(candidate.url, workdir, "source", start=candidate.start, end=candidate.end)
            self._enrich(candidate, info)
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
        prepared = Prepared(candidate=candidate, media=media, duration=details.duration)
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
            if cached:
                reviews[item.candidate.key] = Review.from_dict(cached)
            else:
                pending.append(ReviewItem(item.candidate, item.duration, item.transcript, item.sheet))
        if pending and self.curator is not None:
            reviews.update(self.curator.review(pending, now=self.now))
        for item in pending:
            reviews.setdefault(item.candidate.key, template_review(item, self.config))
        for item in pending:
            review = reviews[item.candidate.key]
            if review.meta.ai:  # only Claude's opinion is worth remembering between runs
                self.state.store_review(item.candidate.key, review.to_dict())
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
                found = discover(self.config, self.secrets, self.http, self.ytdlp, now=self.now, window=window, queue=queue)
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
        pool_size = wanted * self.config.shorts.candidates_factor if use_ai else wanted
        prepared: list[Prepared] = []
        for candidate in ranked:
            if len(prepared) >= pool_size:
                break
            log.info("Préparation : %s — %s (%s vues)", candidate.streamer or candidate.url, candidate.title or "?", candidate.views)
            item = self.prepare(candidate, transcribe=self.config.shorts.captions or use_ai, sheet=use_ai and self.config.ai.review_frames)
            if item is not None:
                prepared.append(item)

        reviews = self.review(prepared)
        approved: list[tuple[Prepared, Review]] = []
        for item in prepared:
            review = reviews[item.candidate.key]
            if review.verdict == "reject" and not item.candidate.manual:
                log.info("Écarté par la revue : %s — %s", item.candidate.title, review.reason)
                report.rejected.append((item.candidate, review.reason))
                if not dry_run:
                    self.state.record_rejection(item.candidate, f"revue : {review.reason}")
                continue
            approved.append((item, review))
        approved.sort(key=lambda pair: (pair[0].candidate.manual, pair[1].score), reverse=True)

        for item, review in approved[:wanted]:
            candidate = item.candidate
            try:
                video, layout = self._render(item, review.meta)
            except MediaError as exc:
                log.error("Montage impossible (%s) : %s", candidate.key, exc)
                report.errors.append(f"{candidate.key} : montage impossible ({exc})")
                if not dry_run:
                    self.state.record_failure(candidate, str(exc))
                continue
            self._write_sidecar(video, candidate, review.meta)
            outcome = ShortOutcome(candidate, video, review.meta, layout)
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
            found = discover(self.config, self.secrets, self.http, self.ytdlp, now=self.now, window=window, per_streamer=10)
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
        labels = [(item.candidate, clean(item.candidate.title) or review.meta.hook) for item, review in kept]
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
        description = self._compilation_description(meta, chapters, labels, probe(video).duration)
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
        report.compilation = {"title": meta.title, "video": str(video), "results": results}

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
