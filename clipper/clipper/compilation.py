from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .captions import build_card_ass, build_lower_third_ass
from .media import MediaError, probe, run_ffmpeg
from .models import Candidate
from .render import LOUDNORM, encode_args, ensure_fonts, fit_blur_graph

WIDTH, HEIGHT, FPS = 1920, 1080, 30
FADE = 0.25


@dataclass
class Part:
    media: Path
    candidate: Candidate
    label: str  # shown in the lower third and in the chapters
    start: float | None = None  # trim inside `media`
    end: float | None = None


def _fades(duration: float) -> tuple[str, str]:
    out_at = max(duration - FADE - 0.05, 0.0)
    video = f"fade=t=in:st=0:d={FADE},fade=t=out:st={out_at:.3f}:d={FADE}"
    audio = f"afade=t=in:d={FADE},afade=t=out:st={out_at:.3f}:d={FADE}"
    return video, audio


def render_part(part: Part, out: Path, *, rank: int | None, workdir: Path, highlight: str) -> float:
    """One clip at 1920x1080/30 fps with its lower third; identical encoding for every part so they concat losslessly."""
    info = probe(part.media)
    begin = max(0.0, part.start or 0.0)
    finish = min(part.end if part.end is not None else info.duration, info.duration)
    duration = finish - begin
    if duration < 1.0:
        raise MediaError(f"{part.media.name} : extrait trop court ({duration:.1f}s)")
    ass_name = f"{out.stem}.ass"
    (workdir / ass_name).write_text(
        build_lower_third_ass(
            rank=rank,
            name=part.candidate.streamer,
            label=part.label,
            credit=part.candidate.credit,
            duration=duration,
            highlight=highlight,
        ),
        encoding="utf-8",
    )
    video_fades, audio_fades = _fades(duration)
    args: list[str] = []
    if begin > 0:
        args += ["-ss", f"{begin:.3f}"]
    args += ["-t", f"{duration:.3f}", "-i", str(part.media.resolve())]
    if not info.has_audio:
        args += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    graph = f"{fit_blur_graph(WIDTH, HEIGHT)};[base]fps={FPS},subtitles={ass_name}:fontsdir=fonts,{video_fades},format=yuv420p[v]"
    args += ["-filter_complex", graph, "-map", "[v]"]
    args += ["-map", "0:a:0", "-af", f"{LOUDNORM},{audio_fades}"] if info.has_audio else ["-map", "1:a:0"]
    args += encode_args(FPS, crf=20, maxrate="10M") + ["-t", f"{duration:.3f}", str(out.resolve())]
    run_ffmpeg(args, cwd=workdir)
    return probe(out).duration


def render_card(out: Path, *, title: str, subtitle: str | None, workdir: Path, highlight: str, duration: float = 2.6) -> float:
    (workdir / "card.ass").write_text(
        build_card_ass(title=title, subtitle=subtitle, duration=duration, highlight=highlight), encoding="utf-8"
    )
    video_fades, _ = _fades(duration)
    args = [
        "-f", "lavfi", "-i", f"color=c=0x101018:s={WIDTH}x{HEIGHT}:r={FPS}:d={duration}",
        "-f", "lavfi", "-t", f"{duration}", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex", f"[0:v]vignette=PI/4,subtitles=card.ass:fontsdir=fonts,{video_fades},format=yuv420p[v]",
        "-map", "[v]", "-map", "1:a:0",
        *encode_args(FPS, crf=20, maxrate="10M"), "-t", f"{duration}", str(out.resolve()),
    ]  # fmt: skip
    run_ffmpeg(args, cwd=workdir)
    return probe(out).duration


def clock(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def render_compilation(
    parts: list[Part],
    out: Path,
    *,
    workdir: Path,
    title: str,
    subtitle: str | None,
    intro: bool = True,
    highlight: str = "#FFE600",
) -> list[tuple[float, str]]:
    """Countdown compilation (#N ... #1) with an intro card. Returns YouTube chapters (start, label)."""
    if not parts:
        raise MediaError("aucun clip à compiler")
    workdir.mkdir(parents=True, exist_ok=True)
    ensure_fonts(workdir)
    files: list[Path] = []
    chapters: list[tuple[float, str]] = []
    elapsed = 0.0
    if intro:
        card = workdir / "part_000.mp4"
        chapters.append((0.0, "Intro"))
        elapsed += render_card(card, title=title, subtitle=subtitle, workdir=workdir, highlight=highlight)
        files.append(card)
    total = len(parts)
    for index, part in enumerate(parts):
        rank = total - index
        target = workdir / f"part_{index + 1:03d}.mp4"
        length = render_part(part, target, rank=rank, workdir=workdir, highlight=highlight)
        chapters.append((elapsed, f"#{rank} {part.candidate.streamer} — {part.label}".strip(" —")))
        elapsed += length
        files.append(target)
    listing = workdir / "parts.txt"
    listing.write_text("".join(f"file '{f.name}'\n" for f in files), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", "-movflags", "+faststart", str(out.resolve())], cwd=workdir)
    return chapters


def chapters_text(chapters: list[tuple[float, str]], total: float) -> str:
    """YouTube only shows chapters when the first is at 0:00, there are 3+, and each lasts 10 s or more.

    The short intro card is folded into the first clip, and a clip shorter than 10 s loses its
    timestamp (it stays in the credits). Returns "" when fewer than 3 chapters remain.
    """
    items = [(start, label) for start, label in chapters if label != "Intro"]
    if not items:
        return ""
    items[0] = (0.0, items[0][1])
    kept = [items[0]]
    for index, (start, label) in enumerate(items[1:], start=1):
        end = items[index + 1][0] if index + 1 < len(items) else total
        if end - start >= 10:
            kept.append((start, label))
    if len(kept) >= 2 and total - kept[-1][0] < 10:
        kept.pop()
    while len(kept) >= 2 and kept[1][0] < 10:  # the first chapter must last 10 s too
        kept.pop(1)
    if len(kept) < 3:
        return ""
    return "\n".join(f"{clock(start)} {label}" for start, label in kept)
