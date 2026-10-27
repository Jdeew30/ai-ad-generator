"""Real ffmpeg renders of synthetic sources, checked by probing and by looking at the pixels."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from clipper.compilation import Part, chapters_text, render_compilation
from clipper.config import ShortsConfig
from clipper.facecam import best_frame, detect_faces, face_stats, plan_layout
from clipper.media import MediaInfo, contact_sheet, extract_frames, probe, run_ffmpeg
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
