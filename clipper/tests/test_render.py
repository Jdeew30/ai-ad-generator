"""Real ffmpeg renders of synthetic sources, checked by probing and by looking at the pixels."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from clipper.compilation import Part, chapters_text, render_compilation
from clipper.config import ShortsConfig
from clipper.facecam import best_frame, detect_faces, face_stats, plan_layout
from clipper.media import JOIN_RATE, MediaInfo, contact_sheet, extract_frames, join_overlapping, overlap_start, probe, run_ffmpeg
from clipper.models import Word
from clipper.render import render_short, target_fps
from clipper.thumbnail import make_thumbnail

from .conftest import make_candidate, requires_ffmpeg

pytestmark = requires_ffmpeg

WORDS = [Word(0.2, 0.6, "Regardez"), Word(0.6, 1.0, "ça"), Word(1.0, 1.6, "maintenant"), Word(2.0, 2.6, "incroyable")]


def frame_at(video: Path, seconds: float, out: Path) -> np.ndarray:
    run_ffmpeg(["-ss", f"{seconds}", "-i", str(video), "-frames:v", "1", str(out)])
    with Image.open(out) as image:
        return np.asarray(image.convert("RGB")).astype(int)


def yellow_pixels(region: np.ndarray) -> int:
    r, g, b = region[..., 0], region[..., 1], region[..., 2]
    return int(((r > 200) & (g > 180) & (b < 90)).sum())


def analyse(video: Path, tmp: Path):
    info = probe(video)
    frames = extract_frames(video, tmp / f"frames_{video.stem}", count=6, duration=info.duration)
    return info, frames, face_stats(detect_faces(frames), len(frames))


def test_layout_detection(media, tmp_path):
    info, _, stats = analyse(media["facecam"], tmp_path)
    plan = plan_layout(info, stats)
    assert plan.kind == "split" and plan.face_box[0] < 200 and plan.face_box[1] > 250  # bottom-left facecam
    info, _, stats = analyse(media["fullcam"], tmp_path)
    plan = plan_layout(info, stats)
    assert plan.kind == "crop" and plan.face_band is not None
    info, _, stats = analyse(media["noface"], tmp_path)
    assert stats is None and plan_layout(info, stats).kind == "blur"
    # Explicit choices win; a configured facecam box needs no detection at all.
    assert plan_layout(info, None, requested="blur").kind == "blur"
    configured = plan_layout(info, None, facecam=[0.0, 0.6, 0.2, 0.4])
    assert configured.kind == "split" and configured.face_box[0] == 0
    assert plan_layout(MediaInfo(1080, 1920, 10, 30, True), None).kind == "crop"  # vertical source


def test_gameplay_panel_does_not_show_the_facecam_again():
    info = MediaInfo(1920, 1080, 30, 60, True)

    def span(box):
        return box[0], box[0] + box[2]

    def overlap(a, b):
        return max(0, min(span(a)[1], span(b)[1]) - max(span(a)[0], span(b)[0]))

    # Camera column on the left (a multi-camera stream): the gameplay window moves right of it.
    left = plan_layout(info, None, facecam=[0.0, 0.0, 0.4, 0.5])
    assert overlap(left.face_box, left.game_box) == 0 and span(left.game_box)[1] <= 1920
    # Small corner facecam on the right: the window moves left of it, as close to the centre as it can.
    right = plan_layout(info, None, facecam=[0.82, 0.7, 0.18, 0.3])
    assert overlap(right.face_box, right.game_box) == 0 and right.game_box[0] >= 0
    assert abs(right.game_box[0] + right.game_box[2] / 2 - 960) < 400
    # A facecam that cannot be avoided: the window takes the side with the most of the rest of the frame.
    wide = plan_layout(info, None, facecam=[0.3, 0.2, 0.5, 0.6])
    assert wide.game_box[0] + wide.game_box[2] == 1920 or wide.game_box[0] == 0
    assert overlap(wide.face_box, wide.game_box) < wide.game_box[2]


@pytest.mark.parametrize("source", ["facecam", "fullcam", "noface"])
def test_render_short_layouts(media, tmp_path, source):
    info, frames, stats = analyse(media[source], tmp_path)
    plan = plan_layout(info, stats)
    out = tmp_path / f"{source}.mp4"
    render_short(media[source], out, info=info, plan=plan, settings=ShortsConfig(), workdir=tmp_path / "work",
                 words=WORDS, hook="Il ne s'y attendait pas", credit="twitch.tv/test", start=0.5, end=5.5)
    result = probe(out)
    assert (result.width, result.height) == (1080, 1920)
    assert result.duration == pytest.approx(5.0, abs=0.15)
    assert result.has_audio
    # At t=0.4 s (source 0.9 s) "ça" is spoken: the highlighted word is drawn in yellow on the caption line.
    picture = frame_at(out, 0.4, tmp_path / f"{source}.png")
    caption_y = {"split": 768, "crop": 1250, "blur": 1434}[plan.kind]
    assert yellow_pixels(picture[caption_y - 60 : caption_y + 60]) > 300
    if plan.kind == "split":  # the facecam panel really shows the face
        top = tmp_path / "top.png"
        Image.fromarray(picture[:768].astype("uint8")).save(top)
        assert detect_faces([top])


def test_render_without_audio_track_adds_silence(media, tmp_path):
    info = probe(media["silent"])
    out = tmp_path / "silent.mp4"
    render_short(media["silent"], out, info=info, plan=plan_layout(info, None), settings=ShortsConfig(captions=False, hook=False),
                 workdir=tmp_path / "w")
    assert probe(out).has_audio


def test_target_fps():
    assert target_fps(59.94, 60) == 59.94
    assert target_fps(60.0, 30) == 30.0
    assert target_fps(0, 60) == 30.0


def test_compilation_and_chapters(media, tmp_path):
    parts = [
        Part(media["noface"], make_candidate("twitch:a", "gotaga"), "Le clutch", 0.0, 4.0),
        Part(media["facecam"], make_candidate("twitch:b", "squeezie"), "Rage quit"),
        Part(media["silent"], make_candidate("twitch:c", "kameto"), "Silence total"),
    ]
    out = tmp_path / "compil.mp4"
    chapters = render_compilation(parts, out, workdir=tmp_path / "c", title="Top 3", subtitle="semaine 43")
    info = probe(out)
    assert (info.width, info.height) == (1920, 1080) and info.has_audio
    assert info.duration == pytest.approx(2.6 + 4 + 6 + 5, abs=0.4)
    assert [label.split(" ")[0] for _, label in chapters] == ["Intro", "#3", "#2", "#1"]
    assert chapters_text(chapters, info.duration) == ""  # parts under 10 s: YouTube would reject the chapters
    long_chapters = [(0, "Intro"), (2.6, "#3 a"), (40, "#2 b"), (70, "#1 c")]
    assert chapters_text(long_chapters, 100) == "0:00 #3 a\n0:40 #2 b\n1:10 #1 c"
    assert chapters_text([(0, "Intro"), (2.6, "#3 a"), (40, "#2 b"), (45, "#1 c")], 80) == ""


def test_thumbnail_and_frames(media, tmp_path):
    frames = extract_frames(media["fullcam"], tmp_path / "f", count=4)
    assert len(frames) == 4
    sheet = contact_sheet(frames, tmp_path / "sheet.jpg")
    with Image.open(sheet) as image:
        assert image.size == (1024, 576)
    chosen = best_frame(frames)
    thumb = make_thumbnail(chosen, tmp_path / "thumb.jpg", headline="Top 12 des clips de la semaine", subline="Squeezie · Gotaga")
    with Image.open(thumb) as image:
        assert image.size == (1280, 720)
    assert thumb.stat().st_size < 2_000_000


def test_the_overlap_of_two_clips_is_found_on_their_sound():
    rng = np.random.default_rng(7)
    live = rng.standard_normal(40 * JOIN_RATE).astype(np.float32)
    first, second = live[: 30 * JOIN_RATE], live[18 * JOIN_RATE :]  # the second clip starts 18 s into the first
    assert overlap_start(first, second) == pytest.approx(18.0, abs=1 / JOIN_RATE)
    elsewhere = rng.standard_normal(20 * JOIN_RATE).astype(np.float32)
    assert overlap_start(first, elsewhere) is None and overlap_start(first, np.zeros(JOIN_RATE * 5, np.float32)) is None


def test_clips_of_one_moment_are_put_back_together(tmp_path):
    live = tmp_path / "live.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=50", "-f", "lavfi", "-i", "anoisesrc=d=50:c=pink:r=48000:a=0.4:s=11",
                "-t", "50", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(live)])  # fmt: skip

    def clip(name, start, length):
        target = tmp_path / f"{name}.mp4"
        run_ffmpeg(["-ss", str(start), "-i", str(live), "-t", str(length), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(target)])
        return target

    # Viewers clipped 0-30 s, then 20-45 s: the overlap shows once, 45 s in all.
    joined = join_overlapping([clip("a", 0, 30), clip("b", 20, 25)], tmp_path / "joined.mp4")
    assert probe(joined).duration == pytest.approx(45.0, abs=0.4) and probe(joined).has_audio
    # Two clips far apart simply follow each other.
    apart = join_overlapping([clip("c", 0, 10), clip("d", 35, 10)], tmp_path / "apart.mp4")
    assert probe(apart).duration == pytest.approx(20.0, abs=0.4)
