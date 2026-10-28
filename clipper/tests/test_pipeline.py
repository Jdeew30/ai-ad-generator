"""End-to-end runs: fake network (listing, download, publishers), real ffmpeg editing."""

import json
import re
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from clipper.ai import Review
from clipper.config import Config, Paths, Secrets, build
from clipper.models import PublishResult, Transcript, Word
from clipper.pipeline import Clipper, refusal
from clipper.state import State

from .conftest import NOW, requires_ffmpeg

pytestmark = requires_ffmpeg


class FakeYtDlp:
    def __init__(self, media: dict[str, Path], clips: dict[str, list[dict]]):
        self.media = media
        self.clips = clips
        self.downloads: list[str] = []

    def list_flat(self, url, limit):
        login = url.split("twitch.tv/")[1].split("/")[0]
        return self.clips.get(login, [])[:limit]

    def download(self, url, dest_dir, name, *, start=None, end=None, exact=True):
        self.downloads.append(url)
        slug = url.rsplit("/", 1)[-1]
        source = self.media["facecam" if "Cam" in slug else "noface"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{name}.mp4"
        shutil.copyfile(source, target)
        return target, {"title": f"titre {slug}", "channel": slug.split("-")[0].capitalize(), "categories": ["Just Chatting"]}


class FakeTranscriber:
    def __init__(self):
        self.calls = 0

    def transcribe(self, media, *, prompt=None):
        self.calls += 1
        return Transcript([Word(0.3, 0.7, "Attends"), Word(0.7, 1.2, "regarde"), Word(1.2, 1.8, "ça !")], "fr")


class FakePublisher:
    def __init__(self, name, ok=True):
        self.name = name
        self.ok = ok
        self.calls = []
        self.messages = []

    def message(self, text):
        self.messages.append(text)

    def publish_short(self, video, meta, candidate, *published):
        self.calls.append((video, meta, candidate))
        if not self.ok:
            from clipper.publishers import PublishError

            raise PublishError(f"{self.name} en panne")
        return PublishResult(self.name, True, f"{self.name}-{len(self.calls)}", f"https://{self.name}/{len(self.calls)}")

    def publish_compilation(self, video, meta, description, thumbnail, *published):
        self.calls.append((video, meta, description, thumbnail))
        return PublishResult(self.name, True, "comp", f"https://{self.name}/comp")


class RejectFirst:
    """Curator double: rejects the candidate whose title mentions 'Toxic'."""

    def review(self, items, *, now):
        from clipper.ai import template_review

        reviews = {}
        for item in items:
            base = template_review(item, build(Config, {}))
            if "Toxic" in item.candidate.title:
                reviews[item.candidate.key] = Review("reject", "insulte un viewer", 0, base.meta)
            else:
                base.meta.ai = True
                reviews[item.candidate.key] = Review("publish", "ok", 80, base.meta)
        return reviews


class Marks:
    """Curator double: Claude approves every clip, with the mark set for a word of its title (80 otherwise)."""

    def __init__(self, marks):
        self.marks = marks

    def review(self, items, *, now):
        from clipper.ai import template_review

        reviews = {}
        for item in items:
            meta = template_review(item, build(Config, {})).meta
            meta.ai = True
            mark = next((m for word, m in self.marks.items() if word in item.candidate.title), 80)
            reviews[item.candidate.key] = Review("publish", "rien de notable", mark, meta)
        return reviews


def entry(slug, views, hours_ago, title=None):
    return {"url": f"https://clips.twitch.tv/{slug}", "title": title or f"clip {slug}", "view_count": views,
            "duration": 6, "timestamp": (NOW - timedelta(hours=hours_ago)).timestamp()}  # fmt: skip


@pytest.fixture
def paths(tmp_path) -> Paths:
    data = tmp_path / "data"
    return Paths(data=data, state=data / "state.json", work=data / "work", output=tmp_path / "out")


def clipper(media, paths, *, clips, publishers=None, curator=None, **config) -> Clipper:
    settings = build(Config, {"twitch": {"streamers": ["alpha", "beta"], "min_views": 100}, "shorts": {"min_duration": 3}, **config})
    return Clipper(settings, Secrets(), paths, ytdlp=FakeYtDlp(media, clips), transcriber=FakeTranscriber(), curator=curator,
                   publishers=publishers, now=NOW)  # fmt: skip


CLIPS = {
    "alpha": [entry("Alpha-Cam1", 5000, 2), entry("Alpha-2", 400, 30)],
    "beta": [entry("Beta-1", 1500, 3)],
}


def test_dry_run_renders_without_publishing_or_remembering(media, paths):
    youtube = FakePublisher("youtube")
    run = clipper(media, paths, clips=CLIPS, publishers={"youtube": youtube})
    report = run.run_shorts(dry_run=True)
    [outcome] = report.published
    assert outcome.candidate.key == "twitch:Alpha-Cam1" and outcome.layout == "split"
    assert outcome.video.exists() and outcome.video.with_suffix(".txt").exists()
    sidecar = json.loads(outcome.video.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["tiktok"]["caption"].endswith("🎥 twitch.tv/alpha") and sidecar["ai"] is False
    assert youtube.calls == [] and not paths.state.exists()
    assert not paths.work.exists()  # downloads cleaned up


def test_publishing_records_state_and_never_reposts(media, paths):
    youtube, tiktok, telegram = FakePublisher("youtube"), FakePublisher("tiktok", ok=False), FakePublisher("telegram")
    publishers = {"youtube": youtube, "tiktok": tiktok, "telegram": telegram}
    report = clipper(media, paths, clips=CLIPS, publishers=publishers).run_shorts()
    [outcome] = report.published
    assert [r.platform for r in outcome.results] == ["youtube", "tiktok", "telegram"]
    assert report.errors == ["tiktok : tiktok en panne"]
    state = State.load(paths.state)
    assert state.posted["twitch:Alpha-Cam1"]["results"]["youtube"]["url"] == "https://youtube/1"
    assert "Clipper — shorts" in report.markdown() and "✅ youtube" in report.markdown()

    second = clipper(media, paths, clips=CLIPS, publishers=publishers).run_shorts()
    assert [o.candidate.key for o in second.published] == ["twitch:Beta-1"]
    third = clipper(media, paths, clips=CLIPS, publishers=publishers).run_shorts()
    # Alpha-2 is 30 h old but a different moment: allowed, "alpha" has 1 of its 2 daily slots left.
    assert [o.candidate.key for o in third.published] == ["twitch:Alpha-2"]
    fourth = clipper(media, paths, clips=CLIPS, publishers=publishers).run_shorts()
    assert fourth.published == [] and "Rien de nouveau" in fourth.markdown()


def test_without_publishers_the_artifact_counts_as_posted(media, paths):
    report = clipper(media, paths, clips=CLIPS, publishers={}).run_shorts()
    assert len(report.published) == 1 and not report.errors
    assert "twitch:Alpha-Cam1" in State.load(paths.state).posted


def test_all_publishers_failing_is_retried_later(media, paths):
    broken = {"youtube": FakePublisher("youtube", ok=False)}
    clipper(media, paths, clips=CLIPS, publishers=broken).run_shorts()
    state = State.load(paths.state)
    assert "twitch:Alpha-Cam1" not in state.posted and state.failures["twitch:Alpha-Cam1"]["count"] == 1


def test_review_rejections_are_remembered(media, paths):
    clips = {"alpha": [entry("Alpha-Toxic", 9000, 1, "Toxic moment")], "beta": [entry("Beta-1", 1500, 3)]}
    youtube = FakePublisher("youtube")
    run = clipper(media, paths, clips=clips, publishers={"youtube": youtube}, curator=RejectFirst())
    report = run.run_shorts()
    assert [o.candidate.key for o in report.published] == ["twitch:Beta-1"]
    assert report.rejected[0][1] == "insulte un viewer"
    state = State.load(paths.state)
    assert "twitch:Alpha-Toxic" in state.rejected
    assert run.transcriber.calls == 2  # both candidates reviewed (pool = 1 short x 3)


def test_a_second_batch_is_reviewed_when_the_first_is_rejected(media, paths):
    clips = {"alpha": [entry("Alpha-Toxic", 9000, 1, "Toxic moment")], "beta": [entry("Beta-1", 1500, 3)]}
    run = clipper(media, paths, clips=clips, publishers={}, curator=RejectFirst(), shorts={"min_duration": 3, "candidates_factor": 1})
    report = run.run_shorts()
    assert [o.candidate.key for o in report.published] == ["twitch:Beta-1"]
    assert [c.key for c, _ in report.rejected] == ["twitch:Alpha-Toxic"] and run.transcriber.calls == 2


def test_clips_marked_under_the_bar_are_not_posted(media, paths):
    clips = {"alpha": [entry("Alpha-Dull", 9000, 1, "Dull moment")], "beta": [entry("Beta-1", 1500, 3)]}
    telegram = FakePublisher("telegram")
    report = clipper(media, paths, clips=clips, publishers={"telegram": telegram}, curator=Marks({"Dull": 35})).run_shorts()
    assert [o.candidate.key for o in report.published] == ["twitch:Beta-1"]
    assert [(c.key, reason) for c, reason in report.rejected] == [("twitch:Alpha-Dull", "note 35/100 (minimum 60). rien de notable")]
    assert "twitch:Alpha-Dull" in State.load(paths.state).rejected and telegram.messages == []
    assert "· note 80/100" in report.markdown() and re.search(r"mise en page `\w+` · 6 s", report.markdown())


def test_a_slot_without_a_good_clip_is_announced(media, paths):
    telegram = FakePublisher("telegram")
    run = clipper(media, paths, clips=CLIPS, publishers={"telegram": telegram}, curator=Marks({"clip": 59}))
    report = run.run_shorts()
    assert report.published == [] and len(report.rejected) == 3 and telegram.calls == []
    assert telegram.messages == ["⏭️ Pas de short à ce passage : aucun des 3 clips examinés n'a été retenu."]
    dry = clipper(media, paths, clips=CLIPS, publishers={"telegram": telegram}, curator=Marks({"clip": 59}))
    dry.state.rejected.clear()
    assert dry.run_shorts(dry_run=True).published == [] and len(telegram.messages) == 1


def test_the_mark_only_gates_claude_reviews_of_discovered_clips():
    from clipper.models import Candidate, ShortMeta

    def candidate(slug, manual=False):
        return Candidate(f"twitch:{slug}", "twitch", f"https://clips.twitch.tv/{slug}", slug, "Alpha", "alpha",
                         "https://www.twitch.tv/alpha", manual=manual)  # fmt: skip

    meta = ShortMeta(hook="H", title="T", description="D", hashtags=[], ai=True)
    found = candidate("x")
    assert refusal(Review("publish", "bof", 59, meta), found, 60) == "note 59/100 (minimum 60). bof"
    assert refusal(Review("publish", "bien", 60, meta), found, 60) is None
    assert refusal(Review("reject", "insulte", 90, meta), found, 60) == "insulte"
    assert refusal(Review("reject", "", 90, meta), found, 60) == "refusé par Claude"
    template = ShortMeta(hook="H", title="T", description="D", hashtags=[], ai=False)
    assert refusal(Review("publish", "gabarit", 12, template), found, 60) is None  # popularity, not a mark
    assert refusal(Review("reject", "insulte", 10, meta), candidate("y", manual=True), 60) is None


class ReplayYtDlp(FakeYtDlp):
    """Serves VOD sections from a given file, or fails like a deleted or subscribers-only replay."""

    def __init__(self, media, replay=None):
        super().__init__(media, {})
        self.replay = replay
        self.sections = []

    def download(self, url, dest_dir, name, *, start=None, end=None, exact=True):
        if "/videos/" not in url:
            return super().download(url, dest_dir, name, start=start, end=end)
        self.sections.append((url, start, end, exact))
        if self.replay is None:
            from clipper.ytdlp import DownloadError

            raise DownloadError("vidéo réservée aux abonnés")
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{name}.mp4"
        shutil.copyfile(self.replay, target)
        return target, {"title": "replay"}


def found_clip(**fields):
    from clipper.models import Candidate

    values = {"key": "twitch:Alpha-Cam1", "source": "twitch", "url": "https://clips.twitch.tv/Alpha-Cam1", "title": "clip",
              "streamer": "Alpha", "streamer_login": "alpha", "channel_url": "https://www.twitch.tv/alpha", "views": 500,
              "duration": 3.0, "vod_id": "123", "vod_offset": 10.0}  # fmt: skip
    return Candidate(**{**values, **fields})


def with_replay(media, paths, replay, **shorts):
    run = clipper(media, paths, clips={}, shorts={"min_duration": 3, "context_before": 2, "context_after": 1, **shorts})
    run.ytdlp = ReplayYtDlp(media, replay)
    return run


def test_twitch_clips_come_with_the_replay_around_them(media, paths):
    run = with_replay(media, paths, media["noface"])
    item = run.prepare(found_clip(), transcribe=False, sheet=False, context=True)
    assert run.ytdlp.sections == [("https://www.twitch.tv/videos/123", 8.0, 14.0, False)] and run.ytdlp.downloads == []
    assert item.clip_span == (2.0, 5.0) and item.duration == pytest.approx(6, abs=0.2)


@pytest.fixture
def hushed(tmp_path) -> Path:
    """A replay whose sound Twitch muted (copyrighted music): an audio track of pure silence."""
    from .conftest import ffmpeg

    path = tmp_path / "hushed.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=6", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
           "-t", "6", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path))  # fmt: skip
    return path


@pytest.mark.parametrize("case", ["unavailable", "muted", "too short"])
def test_the_clip_alone_is_used_when_the_replay_does_not_fit(media, paths, hushed, case):
    replay = {"unavailable": None, "muted": hushed, "too short": media["noface"]}[case]
    run = with_replay(media, paths, replay, context_before=5 if case == "too short" else 2)  # 5 s before: clip at 5-8 s of 6
    item = run.prepare(found_clip(), transcribe=False, sheet=False, context=True)
    assert len(run.ytdlp.sections) == 1 and run.ytdlp.downloads == ["https://clips.twitch.tv/Alpha-Cam1"]
    assert item.clip_span is None


def test_no_replay_without_its_position_or_for_hand_picked_urls(media, paths):
    run = with_replay(media, paths, media["noface"])
    for candidate in (found_clip(vod_offset=None), found_clip(key="twitch:B-1", manual=True)):
        assert run.prepare(candidate, transcribe=False, sheet=False, context=True).clip_span is None
    assert run.prepare(found_clip(key="twitch:C-1"), transcribe=False, sheet=False).clip_span is None  # compilations
    assert run.ytdlp.sections == []


def test_a_review_is_only_reused_for_the_same_extract(media, paths):
    class Counting(Marks):
        def __init__(self):
            super().__init__({})
            self.extracts = []

        def review(self, items, *, now):
            self.extracts += [item.clip_span for item in items]
            return super().review(items, now=now)

    run = with_replay(media, paths, media["noface"])
    run.curator = Counting()
    replayed = run.prepare(found_clip(), transcribe=False, sheet=False, context=True)
    assert run.review([replayed])[replayed.candidate.key].meta.ai
    run.review([replayed])
    clip_only = run.prepare(found_clip(), transcribe=False, sheet=False)
    run.review([clip_only])
    assert run.curator.extracts == [(2.0, 5.0), None]


def test_single_url_mode(media, paths):
    youtube = FakePublisher("youtube")
    run = clipper(media, paths, clips={}, publishers={"youtube": youtube})
    report = run.run_shorts(url="https://clips.twitch.tv/Gamma-Cam9")
    [outcome] = report.published
    assert outcome.candidate.manual and outcome.candidate.streamer == "Gamma"
    assert youtube.calls and State.load(paths.state).posted["twitch:Gamma-Cam9"]


def test_weekly_compilation(media, paths):
    clips = {
        "alpha": [entry("Alpha-Cam1", 5000, 20), entry("Alpha-2", 3000, 40)],
        "beta": [entry("Beta-1", 1500, 50), entry("Beta-Cam2", 900, 70, title="f")],
    }
    youtube, telegram = FakePublisher("youtube"), FakePublisher("telegram")
    run = clipper(media, paths, clips=clips, publishers={"youtube": youtube, "telegram": telegram},
                  compilation={"clips": 4, "min_clips": 3})  # fmt: skip
    report = run.run_compilation()
    assert report.compilation and report.compilation["title"].startswith("TOP 4")
    summary = report.markdown()
    assert "- #1 [Alpha](https://clips.twitch.tv/Alpha-Cam1) — clip Alpha-Cam1 (5000 vues)" in summary
    assert "- #4 [Beta](https://clips.twitch.tv/Beta-Cam2) — Moment fort de Beta (900 vues)" in summary  # not "f"
    assert "- Durée : 0 min" in summary
    video, meta, description, thumbnail = youtube.calls[0]
    assert video.exists() and thumbnail is not None and thumbnail.exists()
    assert "#1 Alpha — clip Alpha-Cam1 : https://clips.twitch.tv/Alpha-Cam1" in description
    assert description.count("https://clips.twitch.tv/") == 4
    state = State.load(paths.state)
    assert set(state.compiled) == {"twitch:Alpha-Cam1", "twitch:Alpha-2", "twitch:Beta-1", "twitch:Beta-Cam2"}
    again = clipper(media, paths, clips=clips, publishers={"youtube": youtube}, compilation={"clips": 4, "min_clips": 3}).run_compilation()
    assert again.compilation is None  # the same clips are never compiled twice


def test_outage_and_bad_url_are_reported_as_errors(media, paths):
    class Down(FakeYtDlp):
        def list_flat(self, url, limit):
            from clipper.ytdlp import DownloadError

            raise DownloadError("Tunnel connection failed")

    run = clipper(media, paths, clips={}, publishers={})
    run.ytdlp = Down(media, {})
    report = run.run_shorts()
    assert report.errors and report.errors[0].startswith("découverte : Twitch injoignable")
    bad = clipper(media, paths, clips={}, publishers={}).run_shorts(url="pas une url")
    assert bad.errors == ["URL invalide : 'pas une url'"]


def test_required_platform_without_credentials_fails_before_any_work(media, paths):
    from clipper.publishers import PublishError

    settings = build(Config, {"twitch": {"streamers": ["alpha"], "min_views": 100}, "shorts": {"min_duration": 3},
                              "publish": {"youtube": {"enabled": True}}})  # fmt: skip
    run = Clipper(settings, Secrets(), paths, ytdlp=FakeYtDlp(media, CLIPS), transcriber=FakeTranscriber(), now=NOW)
    with pytest.raises(PublishError, match="YouTube est activé"):
        run.run_shorts()
    assert run.ytdlp.downloads == []
    run.run_shorts(dry_run=True)  # a dry run needs no credentials
    assert run.ytdlp.downloads


def test_twitch_clipper_name_is_never_used_as_the_streamer(media, paths):
    class CuratorOnly(FakeYtDlp):
        def download(self, url, dest_dir, name, *, start=None, end=None):
            path, _ = super().download(url, dest_dir, name, start=start, end=end)
            return path, {"title": "x", "uploader": "viewer42"}  # yt-dlp without broadcaster details

    run = clipper(media, paths, clips=CLIPS, publishers={}, streamers={"alpha": {"name": "Alpha Le Vrai"}})
    run.ytdlp = CuratorOnly(media, CLIPS)
    [outcome] = run.run_shorts(dry_run=True).published
    assert outcome.candidate.streamer == "Alpha Le Vrai" and outcome.candidate.clipper == "viewer42"
    assert outcome.meta.title.startswith("Alpha Le Vrai : ")
