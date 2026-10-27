from __future__ import annotations

import shutil
from pathlib import Path

from .captions import TextLayout, build_short_ass
from .config import ASSETS_DIR, ShortsConfig
from .facecam import OUT_H, OUT_W, SPLIT_TOP, LayoutPlan
from .media import MediaError, MediaInfo, run_ffmpeg
from .models import Word
from .transcribe import shift_words

FONT_FILE = ASSETS_DIR / "fonts" / "Anton-Regular.ttf"
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
MAX_SHORT_SECONDS = 180.0  # YouTube Shorts limit


def ensure_fonts(workdir: Path) -> None:
    """libass reads fonts from ./fonts: a relative path keeps the filter string free of escaping."""
    fonts = workdir / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    target = fonts / FONT_FILE.name
    if not target.exists():
        shutil.copyfile(FONT_FILE, target)


def target_fps(source_fps: float, max_fps: int) -> float:
    if source_fps <= 1:
        return 30.0
    fps = min(source_fps, float(max_fps))
    return float(round(fps)) if abs(fps - round(fps)) < 0.05 else round(fps, 3)


def encode_args(fps: float, *, crf: int = 21, maxrate: str = "8M") -> list[str]:
    gop = str(int(round(fps * 2)))
    return [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-profile:v", "high", "-level:v", "4.2",
        "-pix_fmt", "yuv420p", "-g", gop, "-maxrate", maxrate, "-bufsize", "16M",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
    ]  # fmt: skip


def fit_blur_graph(out_w: int, out_h: int) -> str:
    """Whole frame fitted in the output, over a blurred, zoomed copy of itself."""
    small_w, small_h = max(out_w // 4 // 2 * 2, 2), max(out_h // 4 // 2 * 2, 2)
    return (
        "[0:v]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={small_w}:{small_h}:force_original_aspect_ratio=increase,crop={small_w}:{small_h},"
        f"gblur=sigma=10,scale={out_w}:{out_h},eq=brightness=-0.08:saturation=1.2[bg];"
        f"[fgsrc]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
    )


def layout_graph(plan: LayoutPlan) -> str:
    if plan.kind == "split" and plan.face_box and plan.game_box:
        fx, fy, fw, fh = plan.face_box
        gx, gy, gw, gh = plan.game_box
        bottom = OUT_H - SPLIT_TOP
        return (
            "[0:v]split=2[cam][game];"
            f"[cam]crop={fw}:{fh}:{fx}:{fy},scale={OUT_W}:{SPLIT_TOP}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{SPLIT_TOP},setsar=1[top];"
            f"[game]crop={gw}:{gh}:{gx}:{gy},scale={OUT_W}:{bottom}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{bottom},setsar=1[bottom];"
            "[top][bottom]vstack=inputs=2[base]"
        )
    if plan.kind == "crop" and plan.crop_box:
        cx, cy, cw, ch = plan.crop_box
        return (
            f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},setsar=1[base]"
        )
    return fit_blur_graph(OUT_W, OUT_H)


def text_layout(plan: LayoutPlan, info: MediaInfo, *, hook_seconds: float) -> TextLayout:
    if plan.kind == "split":
        # Captions on the seam between facecam and gameplay; the hook briefly over the gameplay.
        return TextLayout(caption_y=SPLIT_TOP, hook_y=SPLIT_TOP + 300, hook_until=hook_seconds, credit_y=SPLIT_TOP + 100)
    if plan.kind == "crop":
        caption_y = 1250
        hook_y = 330
        if plan.face_band:  # keep the hook box (~220 px tall) off the face
            top, bottom = plan.face_band
            for option in (330, bottom + 200, 1560):
                clear_of_face = option + 110 < top - 30 or option - 110 > bottom + 30
                if clear_of_face and abs(option - caption_y) > 230:
                    hook_y = option
                    break
        return TextLayout(caption_y=caption_y, hook_y=hook_y, hook_until=hook_seconds, credit_y=caption_y + 110)
    video_h = min(OUT_H, round(OUT_W / info.aspect)) if info.aspect else OUT_W * 9 // 16
    top = (OUT_H - video_h) // 2
    bottom = top + video_h
    # Video in the middle: hook title stays above it, captions and credit below.
    return TextLayout(caption_y=min(bottom + 170, 1560), hook_y=max(top - 190, 300), hook_until=None, credit_y=min(bottom + 45, 1700))


def render_short(
    source: Path,
    out: Path,
    *,
    info: MediaInfo,
    plan: LayoutPlan,
    settings: ShortsConfig,
    workdir: Path,
    words: list[Word] | None = None,
    hook: str | None = None,
    credit: str | None = None,
    start: float | None = None,
    end: float | None = None,
) -> Path:
    """1080x1920 short: layout, animated captions, hook, credit, loudness-normalised audio."""
    begin = max(0.0, start or 0.0)
    finish = min(end if end is not None else info.duration, info.duration) if info.duration else (end or 0.0)
    duration = min(finish - begin, MAX_SHORT_SECONDS)
    if duration < 1.0:
        raise MediaError(f"extrait trop court ({duration:.1f}s)")
    workdir.mkdir(parents=True, exist_ok=True)
    ensure_fonts(workdir)
    fps = target_fps(info.fps, settings.max_fps)
    layout = text_layout(plan, info, hook_seconds=settings.hook_seconds)
    captions = shift_words(words or [], begin, begin + duration) if settings.captions else []
    ass = build_short_ass(
        captions,
        layout,
        duration=duration,
        hook=hook if settings.hook else None,
        credit=credit if settings.credit else None,
        highlight=settings.highlight_color,
    )
    (workdir / "short.ass").write_text(ass, encoding="utf-8")

    args: list[str] = []
    if begin > 0:
        args += ["-ss", f"{begin:.3f}"]
    args += ["-t", f"{duration:.3f}", "-i", str(source.resolve())]
    if not info.has_audio:
        args += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    graph = f"{layout_graph(plan)};[base]fps={fps},subtitles=short.ass:fontsdir=fonts,format=yuv420p[v]"
    args += ["-filter_complex", graph, "-map", "[v]"]
    if info.has_audio:
        args += ["-map", "0:a:0", "-af", LOUDNORM]
    else:
        args += ["-map", "1:a:0"]
    args += encode_args(fps) + ["-t", f"{duration:.3f}", str(out.resolve())]
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args, cwd=workdir)
    return out
