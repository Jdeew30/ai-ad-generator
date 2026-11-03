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

from .conftest import NOW, make_candidate, requires_ffmpeg

pytestmark = requires_ffmpeg


class FakeYtDlp:
    def __init__(self, media: dict[str, Path], clips: dict[str, list[dict]]):
        self.media = media
        self.clips = clips
        self.downloads: list[str] = []
        self.asked: list[tuple] = []  # (url, start, end, audio_only) of each download

    def list_flat(self, url, limit):
        login = url.split("twitch.tv/")[1].split("/")[0]
        return self.clips.get(login, [])[:limit]

    def download(self, url, dest_dir, name, *, start=None, end=None, exact=True, audio_only=False):
        self.downloads.append(url)
        self.asked.append((url, start, end, audio_only))
        slug = url.rsplit("/", 1)[-1]
        source = self.media["facecam" if "Cam" in slug else "noface"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{name}.mp4"
        shutil.copyfile(source, target)
        return target, {"title": f"titre {slug}", "channel": slug.split("-")[0].capitalize(), "categories": ["Just Chatting"]}


class FakeTranscriber:
    def __init__(self):
        self.calls = 0
        self.parts: list[tuple] = []  # (start, end) of the media listened to

    def transcribe(self, media, *, prompt=None, start=None, end=None, quick=False):
        self.calls += 1
        self.parts.append((start, end))
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

    def review(self, items, *, now, recent=None):
        from clipper.ai import template_review

        reviews = {}
        for item in items:
            base = template_review(item, build(Config, {}))
            base.meta.ai = True  # Claude's answer, verdict included
            if "Toxic" in item.candidate.title:
                reviews[item.candidate.key] = Review("reject", "insulte un viewer", 0, base.meta)
            else:
                reviews[item.candidate.key] = Review("publish", "ok", 80, base.meta)
        return reviews


class Marks:
    """Curator double: Claude approves every clip, with the mark set for a word of its title (80 otherwise)."""

    def __init__(self, marks):
        self.marks = marks

    def review(self, items, *, now, recent=None):
        from clipper.ai import template_review

        reviews = {}
        for item in items:
            meta = template_review(item, build(Config, {})).meta
            meta.ai = True
            mark = next((m for word, m in self.marks.items() if word in item.candidate.title), 80)
            reviews[item.candidate.key] = Review("publish", "rien de notable", mark, meta)
        return reviews


class Partial(Marks):
    """Curator double: like Claude skipping candidates, it only answers for the clips whose key holds a `judged` word."""

    def __init__(self, judged, marks=None):
        super().__init__(marks or {})
        self.judged = judged

    def review(self, items, *, now, recent=None):
        return {key: review for key, review in super().review(items, now=now).items() if any(word in key for word in self.judged)}


class Recording(Marks):
    """Curator double: marks like Marks, and remembers each request (the clips, and the shorts said to be published)."""

    def __init__(self, marks=None):
        super().__init__(marks or {})
        self.requests = []

    def review(self, items, *, now, recent=None):
        self.requests.append(([item.candidate.key for item in items], recent))
        return super().review(items, now=now)


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


def test_each_run_is_counted_and_the_dashboard_rewritten(media, paths):
    telegram = FakePublisher("telegram")
    clipper(media, paths, clips=CLIPS, publishers={"telegram": telegram}, curator=Marks({"Alpha-2": 30})).run_shorts()
    state = State.load(paths.state)
    [run] = state.runs
    assert (run["mode"], run["detected"], run["by_source"], run["published"], run["failed"]) == ("shorts", 3, {"twitch": 3}, 1, 0)
    assert run["prepared"] >= 1 and next(iter(state.posted.values()))["score"] == 80
    board = paths.state.with_name("dashboard.md").read_text(encoding="utf-8")
    assert "| Shorts envoyés | 1 | 1 |" in board and "Note moyenne de Claude : 80/100" in board
    assert "Twitch 1" in board and "## Derniers shorts envoyés" in board
    assert paths.state.with_name("creators.json").exists()
    dry = clipper(media, paths, clips=CLIPS, publishers={"telegram": telegram})
    dry.run_shorts(dry_run=True)
    assert len(State.load(paths.state).runs) == 1  # a dry run leaves no trace


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


def test_a_clip_asked_for_by_hand_goes_out_even_under_the_bar(media, paths):
    class Graded:
        """Curator double: Claude's verdict and mark for a word of the title."""

        def review(self, items, *, now, recent=None):
            from clipper.ai import template_review

            grades = {"Dull": ("publish", 45), "Weak": ("publish", 52), "Toxic": ("reject", 70)}
            reviews = {}
            for item in items:
                meta = template_review(item, build(Config, {})).meta
                meta.ai = True
                verdict, mark = next(g for word, g in grades.items() if word in item.candidate.title)
                reviews[item.candidate.key] = Review(verdict, "rien de notable", mark, meta)
            return reviews

    clips = {"alpha": [entry("Alpha-Dull", 9000, 1, "Dull moment"), entry("Alpha-Weak", 800, 2, "Weak moment")],
             "beta": [entry("Beta-Toxic", 5000, 3, "Toxic moment")]}  # fmt: skip
    telegram = FakePublisher("telegram")
    report = clipper(media, paths, clips=clips, publishers={"telegram": telegram}, curator=Graded()).run_shorts(force=True)
    # The best mark under the bar goes out, never a clip Claude refused, whatever its mark.
    assert [o.candidate.key for o in report.published] == ["twitch:Alpha-Weak"]
    assert sorted(c.key for c, _ in report.rejected) == ["twitch:Alpha-Dull", "twitch:Beta-Toxic"]
    state = State.load(paths.state)
    assert "twitch:Alpha-Weak" in state.posted and "twitch:Alpha-Weak" not in state.rejected
    assert telegram.messages == ["ℹ️ Short envoyé à ta demande : aucun clip n'atteignait la note minimale (60/100), "
                                 "celui-ci est le mieux noté (52/100)."]  # fmt: skip


def test_a_scheduled_run_keeps_its_short_of_each_source(media, paths):
    shorts = {"min_duration": 3, "per_source": {"twitch": 2}, "guarantee": {"twitch": 45}}
    clips = {"alpha": [entry("Alpha-Dull", 9000, 1, "Dull moment"), entry("Alpha-Weak", 800, 2, "Weak moment")],
             "beta": [entry("Beta-1", 1500, 3, "Flat moment")]}  # fmt: skip
    telegram = FakePublisher("telegram")
    marks = Marks({"Dull": 44, "Weak": 52, "Flat": 50})
    report = clipper(media, paths, clips=clips, publishers={"telegram": telegram}, curator=marks, shorts=shorts).run_shorts()
    # Nothing reached 60: the best marked 45 or more goes out, one only, never the whole quota.
    assert [o.candidate.key for o in report.published] == ["twitch:Alpha-Weak"]
    assert telegram.messages == ["ℹ️ Short Twitch envoyé pour garder un short Twitch à ce passage : aucun clip n'atteignait "
                                 "la note minimale (60/100), celui-ci est le mieux noté (52/100)."]  # fmt: skip
    paths.state.unlink()
    poor = clipper(media, paths, clips=clips, publishers={"telegram": telegram}, curator=Marks({"moment": 40}), shorts=shorts)
    assert poor.run_shorts().published == []  # all under 45: no short rather than a poor one
    assert telegram.messages[-1] == "⏭️ Pas de short Twitch à ce passage : aucun des 3 clips examinés n'a été retenu."


def test_a_clip_claude_did_not_judge_is_never_posted(media, paths):
    telegram = FakePublisher("telegram")
    clips = {"alpha": [entry("Alpha-Cam1", 9000, 1)], "beta": [entry("Beta-1", 1500, 3, "Beta moment")]}
    curator = Partial(["Beta"], {"Beta": 50})  # Alpha, the most viewed, gets no answer: the template must not let it through
    report = clipper(media, paths, clips=clips, publishers={"telegram": telegram}, curator=curator).run_shorts()
    assert report.published == [] and telegram.calls == []
    assert telegram.messages == ["⏭️ Pas de short à ce passage : aucun des 2 clips examinés n'a été retenu."]
    state = State.load(paths.state)
    assert "twitch:Beta-1" in state.rejected
    assert "twitch:Alpha-Cam1" not in state.rejected and state.failures["twitch:Alpha-Cam1"]["count"] == 1  # reviewed again later


def test_a_run_without_any_clip_tells_the_owner(media, paths):
    telegram = FakePublisher("telegram")
    run = clipper(media, paths, clips={}, publishers={"telegram": telegram}, curator=Recording())
    run.run_shorts()
    run.run_shorts(dry_run=True)
    run.ytdlp.blocked = True  # YouTube asked to prove the server is not a bot
    run.run_shorts()
    assert telegram.messages == [
        "⏭️ Pas de short à ce passage : aucun nouveau clip trouvé.",
        "⏭️ Pas de short à ce passage : aucun nouveau clip trouvé. YouTube bloque le serveur (vérification anti-robot) : "
        "des cookies YouTube (secret YTDLP_COOKIES) lèvent ce blocage.",
    ]


def test_without_any_answer_from_claude_the_owner_is_told(media, paths):
    telegram = FakePublisher("telegram")
    report = clipper(media, paths, clips=CLIPS, publishers={"telegram": telegram}, curator=Partial([])).run_shorts()
    assert report.published == [] and not State.load(paths.state).rejected
    assert telegram.messages == ["⚠️ Pas de short à ce passage : Claude n'a pas répondu (clé, crédits ou panne ?). Les clips seront réexaminés."]


def test_each_review_compares_clips_of_different_creators(media, paths):
    clips = {"alpha": [entry("Alpha-Cam1", 5000, 2), entry("Alpha-3", 4000, 5)], "beta": [entry("Beta-1", 1500, 3)]}
    curator = Recording({"clip": 59})
    clipper(media, paths, clips=clips, publishers={}, curator=curator, shorts={"min_duration": 3, "candidates_factor": 2}).run_shorts()
    assert [keys for keys, _ in curator.requests] == [["twitch:Alpha-Cam1", "twitch:Beta-1"], ["twitch:Alpha-3"]]


def test_a_focus_run_only_takes_the_channels_asked_for(media, paths):
    curator = Recording({"clip": 59})
    run = clipper(media, paths, clips=CLIPS, publishers={}, curator=curator)
    for n in range(2):  # alpha already had its two shorts of the day: asked for by name, it is not capped
        run.state.posted[f"twitch:old{n}"] = {"at": (NOW - timedelta(hours=1 + n)).isoformat(), "streamer_login": "alpha", "title": f"short {n}"}
    assert not clipper(media, paths, clips=CLIPS, publishers={}, curator=Recording()).run_shorts(focus=["nobody"]).published
    run.run_shorts(focus=["alpha"])
    assert [keys for keys, _ in curator.requests] == [["twitch:Alpha-Cam1", "twitch:Alpha-2"]]
    report = clipper(media, paths, clips=CLIPS, publishers={}, curator=Recording()).run_shorts(focus=["clips.twitch.tv/Foo"])
    assert report.errors and "cible inconnue" in report.errors[0]


def test_claude_knows_the_latest_shorts_and_a_verdict_only_holds_for_them(media, paths):
    run = clipper(media, paths, clips=CLIPS, publishers={}, curator=Recording())
    for key, hours, title in [("old", 96, "Vieux short"), ("b", 20, "Delta : record du monde"), ("a", 2, "Gamma : le skin d'Inox")]:
        run.state.posted[f"twitch:{key}"] = {"at": (NOW - timedelta(hours=hours)).isoformat(), "streamer_login": "gamma", "title": title}
    published = run.run_shorts().published[0].meta.title
    [(keys, recent)] = run.curator.requests
    assert keys == ["twitch:Alpha-Cam1", "twitch:Beta-1", "twitch:Alpha-2"]  # all approved, only the first one published
    assert recent == [(NOW - timedelta(hours=2), "Gamma : le skin d'Inox"), (NOW - timedelta(hours=20), "Delta : record du monde")]

    again = clipper(media, paths, clips=CLIPS, publishers={}, curator=Recording())
    again.run_shorts()
    [(keys, recent)] = again.curator.requests
    assert "twitch:Beta-1" in keys and recent[0][1] == published  # judged again, now that Alpha's short is out


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

        def review(self, items, *, now, recent=None):
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


def test_youtube_extracts_are_not_reuploaded_to_youtube(media, paths):
    from clipper.models import Candidate, ShortMeta

    youtube, tiktok, telegram = FakePublisher("youtube"), FakePublisher("tiktok"), FakePublisher("telegram")
    run = clipper(media, paths, clips={}, publishers={"youtube": youtube, "tiktok": tiktok, "telegram": telegram})
    extract = Candidate("youtube:abc@10-40", "youtube", "https://www.youtube.com/watch?v=abc", "Vidéo", "Squeezie", "squeezie",
                        "https://www.youtube.com/@Squeezie")  # fmt: skip
    results = run.publish_short(media["noface"], ShortMeta(hook="H", title="T", description="D", hashtags=[]), extract)
    assert [r.platform for r in results] == ["tiktok", "telegram"] and youtube.calls == []  # Content ID would claim it


def test_single_url_mode(media, paths):
    youtube = FakePublisher("youtube")
    run = clipper(media, paths, clips={}, publishers={"youtube": youtube})
    report = run.run_shorts(url="https://clips.twitch.tv/Gamma-Cam9")
    [outcome] = report.published
    assert outcome.candidate.manual and outcome.candidate.streamer == "Gamma"
    assert youtube.calls and State.load(paths.state).posted["twitch:Gamma-Cam9"]


def test_a_passage_is_found_by_what_is_said_in_it(media, paths):
    run = clipper(media, paths, clips={}, publishers={"telegram": FakePublisher("telegram")})
    video = "https://www.youtube.com/watch?v=abcdefghijk"
    report = run.run_shorts(url=video, find="Regarde ça !", start="45:00", end="1:08:00")
    [outcome] = report.published
    # The whole sound came, Whisper listened from 45:00 and heard « regarde » 0.7 s in: 15 s before it, 35 s after.
    assert run.ytdlp.asked[0] == (video, None, None, True) and run.transcriber.parts[0] == (2700.0, 4080.0)
    assert outcome.candidate.key == "youtube:abcdefghijk@2685-2735" and outcome.candidate.manual
    missing = clipper(media, paths, clips={}).run_shorts(url=video, find="bonjour madame")
    assert missing.published == [] and missing.errors == [
        "« bonjour madame » introuvable de 0:00 à la fin : une autre phrase, ou un autre passage ?"]


def test_clips_of_one_moment_given_together_are_joined_and_kept_whole(media, paths):
    telegram = FakePublisher("telegram")
    run = clipper(media, paths, clips={}, publishers={"telegram": telegram}, curator=Marks({}))
    report = run.run_shorts(url="https://www.twitch.tv/gamma/clip/Gamma-Cam9  https://www.twitch.tv/gamma/clip/Gamma-2")
    [outcome] = report.published
    assert outcome.candidate.parts == ["https://www.twitch.tv/gamma/clip/Gamma-Cam9", "https://www.twitch.tv/gamma/clip/Gamma-2"]
    assert outcome.candidate.key.startswith("manual:") and outcome.candidate.streamer_login == "gamma"
    assert outcome.duration == pytest.approx(12.0, abs=0.5)  # two 6 s clips that do not overlap, nothing cut by the review
    assert run.ytdlp.downloads[-2:] == outcome.candidate.parts
    report = run.run_shorts(url="https://clips.twitch.tv/A https://clips.twitch.tv/B", start="10", end="20")
    assert report.errors and "plusieurs clips" in report.errors[0]


def test_a_section_of_a_replay_is_copied_not_re_encoded(media, paths):
    run = clipper(media, paths, clips={}, publishers={})
    calls = []
    download = run.ytdlp.download

    def spy(url, dest_dir, name, *, start=None, end=None, exact=True):
        calls.append((start, end, exact))
        return download(url, dest_dir, name, start=start, end=end, exact=exact)

    run.ytdlp.download = spy
    assert run.run_shorts(url="https://www.twitch.tv/videos/2913268775", start="92030", end="92120").published
    assert calls == [(92030.0, 92120.0, False)]  # a 25 h replay re-encoded from the cut point took over 30 minutes


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


def test_the_best_of_is_filled_up_to_its_length(media, paths):
    clips = {
        "alpha": [entry("Alpha-Cam1", 5000, 20), entry("Alpha-2", 3000, 40)],
        "beta": [entry("Beta-1", 1500, 50), entry("Beta-Cam2", 900, 70)],
    }
    youtube = FakePublisher("youtube")
    # 6 s clips: two of them reach 12 s, the most watched ones are kept.
    run = clipper(media, paths, clips=clips, publishers={"youtube": youtube},
                  compilation={"clips": 10, "min_clips": 2, "target_minutes": 0.2})  # fmt: skip
    report = run.run_compilation()
    assert report.compilation and report.compilation["title"].startswith("TOP 2")
    assert set(State.load(paths.state).compiled) == {"twitch:Alpha-Cam1", "twitch:Alpha-2"}


def test_a_run_takes_one_short_of_each_source(media, paths, monkeypatch):
    import clipper.pipeline as pipeline

    twitch = [make_candidate(f"twitch:Alpha-Cam{i}", "alpha", views=5000 - i, created_at=NOW - timedelta(hours=4 * i)) for i in (1, 2)]
    youtube = make_candidate("youtube:abcdefghijk@60-120", "squeezie", source="youtube", start=60.0, end=120.0,
                             url="https://www.youtube.com/watch?v=abcdefghijk", channel_url="https://www.youtube.com/@Squeezie")  # fmt: skip
    found = [*twitch, youtube]
    monkeypatch.setattr(pipeline, "discover", lambda *args, **kwargs: list(found))
    telegram = FakePublisher("telegram")
    run = clipper(media, paths, clips={}, publishers={"telegram": telegram},
                  shorts={"min_duration": 3, "per_source": {"twitch": 1, "youtube": 1}})  # fmt: skip
    report = run.run_shorts()
    assert [outcome.candidate.key for outcome in report.published] == ["twitch:Alpha-Cam1", "youtube:abcdefghijk@60-120"]

    found = twitch[1:]  # no YouTube moment this time: the Twitch short goes out, and the owner is told
    run = clipper(media, paths, clips={}, publishers={"telegram": telegram},
                  shorts={"min_duration": 3, "per_source": {"twitch": 1, "youtube": 1}})  # fmt: skip
    report = run.run_shorts()
    assert [outcome.candidate.key for outcome in report.published] == ["twitch:Alpha-Cam2"]
    assert telegram.messages == ["⏭️ Pas de short YouTube à ce passage : aucun nouveau clip trouvé."]
    assert len(clipper(media, paths, clips={}, shorts={"min_duration": 3, "per_source": {"twitch": 1, "youtube": 1}},
                       publishers={}).run_shorts(count=1).published) <= 1  # a count asked for by hand is a total  # fmt: skip


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
        def download(self, url, dest_dir, name, *, start=None, end=None, exact=True):
            path, _ = super().download(url, dest_dir, name, start=start, end=end, exact=exact)
            return path, {"title": "x", "uploader": "viewer42"}  # yt-dlp without broadcaster details

    run = clipper(media, paths, clips=CLIPS, publishers={}, streamers={"alpha": {"name": "Alpha Le Vrai"}})
    run.ytdlp = CuratorOnly(media, CLIPS)
    [outcome] = run.run_shorts(dry_run=True).published
    assert outcome.candidate.streamer == "Alpha Le Vrai" and outcome.candidate.clipper == "viewer42"
    assert outcome.meta.title.startswith("Alpha Le Vrai : ")


def test_a_stalled_download_is_given_up_and_the_next_clip_goes_out(media, paths, monkeypatch, caplog):
    import time as time_module

    import clipper.pipeline as pipeline_module

    class Stalling(FakeYtDlp):
        def download(self, url, dest_dir, name, **options):
            if "Alpha-Cam1" in url:
                time_module.sleep(60)  # a replay served at a crawl: the watchdog stops it
            return super().download(url, dest_dir, name, **options)

    monkeypatch.setattr(pipeline_module, "PREPARE_SECONDS", 4)
    telegram = FakePublisher("telegram")
    run = clipper(media, paths, clips=CLIPS, publishers={"telegram": telegram})
    run.ytdlp = Stalling(media, CLIPS)
    started = time_module.monotonic()
    with caplog.at_level("WARNING", logger="clipper"):
        report = run.run_shorts()
    assert time_module.monotonic() - started < 40  # not the minute the download would have taken
    assert [o.candidate.key for o in report.published] == ["twitch:Beta-1"]
    assert "Préparation abandonnée au bout de 0 min" in caplog.text and "twitch:Beta-1 — clip suivant" not in caplog.text
    state = State.load(paths.state)
    assert state.failures["twitch:Alpha-Cam1"]["error"].startswith("préparation abandonnée")
    assert state.seen("twitch:Alpha-Cam1")  # left aside at once: it does not stall the next runs too


def test_the_watchdog_interrupts_only_what_runs_too_long():
    import time as time_module

    from clipper.pipeline import _Overtime, _watchdog

    with _watchdog(2):
        time_module.sleep(0.1)  # in time: nothing happens, and nothing is left armed
    time_module.sleep(0.1)
    started = time_module.monotonic()
    with pytest.raises(_Overtime), _watchdog(0.3):
        time_module.sleep(10)
    assert time_module.monotonic() - started < 2


def test_a_watchdog_inside_another_one_leaves_it_its_time():
    import signal as signal_module
    import time as time_module

    from clipper.pipeline import _Overtime, _SlowReplay, _watchdog

    started = time_module.monotonic()
    with pytest.raises(_Overtime), _watchdog(1.0):
        with pytest.raises(_SlowReplay), _watchdog(0.2, _SlowReplay):
            time_module.sleep(10)  # the inner one rings first…
        time_module.sleep(10)  # …and the outer one still rings on time
    assert time_module.monotonic() - started < 3
    started = time_module.monotonic()
    with pytest.raises(_Overtime), _watchdog(0.3):
        with _watchdog(5, _SlowReplay):  # due after the outer one: the outer one rings
            time_module.sleep(10)
    assert time_module.monotonic() - started < 2
    assert signal_module.getitimer(signal_module.ITIMER_REAL)[0] == 0  # nothing left armed


def test_a_replay_that_does_not_come_gives_way_to_the_clip_alone(media, paths, monkeypatch, caplog):
    import time as time_module

    import clipper.pipeline as pipeline_module

    class SlowReplay(ReplayYtDlp):
        def download(self, url, dest_dir, name, *, start=None, end=None, exact=True):
            if "/videos/" in url:
                time_module.sleep(30)  # a replay served at a crawl
            return super().download(url, dest_dir, name, start=start, end=end, exact=exact)

    monkeypatch.setattr(pipeline_module, "REPLAY_SECONDS", 1)
    run = with_replay(media, paths, media["noface"])
    run.ytdlp = SlowReplay(media, media["noface"])
    started = time_module.monotonic()
    with caplog.at_level("INFO", logger="clipper"):
        item = run._prepare_in_time(found_clip(), dry_run=True, transcribe=False, sheet=False, context=True)
    assert time_module.monotonic() - started < 20  # not the 30 s of the replay, nor the 4 min of the whole clip
    assert item is not None and item.clip_span is None  # the clip alone, not given up
    assert run.ytdlp.downloads == ["https://clips.twitch.tv/Alpha-Cam1"]
    assert "Replay toujours pas là au bout de 1 s, clip seul." in caplog.text and "abandonnée" not in caplog.text


def test_a_run_short_of_time_prepares_no_new_clip(media, paths, monkeypatch, caplog):
    import clipper.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "SHORTS_PREPARE_SECONDS", 0)
    run = clipper(media, paths, clips=CLIPS, publishers={"telegram": FakePublisher("telegram")})
    with caplog.at_level("WARNING", logger="clipper"):
        assert run.run_shorts().published == [] and run.ytdlp.downloads == []
    assert "plus de nouvelle préparation" in caplog.text
