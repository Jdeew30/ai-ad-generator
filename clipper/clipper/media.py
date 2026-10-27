from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


class MediaError(Exception):
    pass


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    duration: float
    fps: float
    has_audio: bool

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0


def _binary(name: str) -> str:
    override = os.environ.get(f"{name.upper()}_PATH")
    found = override or shutil.which(name)
    if not found:
        raise MediaError(f"{name} introuvable : installez ffmpeg (apt install ffmpeg, brew install ffmpeg, winget install ffmpeg).")
    return found


def ffmpeg_bin() -> str:
    return _binary("ffmpeg")


def ffprobe_bin() -> str:
    return _binary("ffprobe")


def _fps(rate: str | None) -> float:
    if not rate or rate in {"0/0", "N/A"}:
        return 0.0
    num, _, den = rate.partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path) -> MediaInfo:
    result = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise MediaError(f"ffprobe n'a pas pu lire {path.name} : {result.stderr.strip()[-500:]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError(f"{path.name} ne contient pas de piste vidéo")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = abs(int(side["rotation"]))
    if rotation in {90, 270}:
        width, height = height, width
    return MediaInfo(
        width=width,
        height=height,
        duration=duration,
        fps=_fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate")),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def run_ffmpeg(args: list[str], *, cwd: Path | None = None, timeout: float = 3600) -> None:
    command = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffmpeg a dépassé {timeout:.0f}s") from exc
    if result.returncode != 0:
        raise MediaError(f"ffmpeg a échoué ({result.returncode}) : {result.stderr.strip()[-2000:]}")


def extract_frames(path: Path, out_dir: Path, *, count: int = 8, width: int = 1280, duration: float | None = None) -> list[Path]:
    """`count` frames spread evenly over the video (skipping the very first/last instants)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = duration if duration is not None else probe(path).duration
    frames: list[Path] = []
    for index in range(count):
        at = duration * (index + 0.5) / count
        target = out_dir / f"frame_{index:02d}.jpg"
        run_ffmpeg(
            ["-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(target)],
            timeout=120,
        )
        if target.exists():
            frames.append(target)
    return frames


def contact_sheet(frames: list[Path], out_path: Path, *, columns: int = 2, tile: tuple[int, int] = (512, 288)) -> Path:
    """Tiles frames into one JPEG: one image per clip keeps the AI review cheap."""
    if not frames:
        raise MediaError("aucune image à assembler")
    rows = -(-len(frames) // columns)
    sheet = Image.new("RGB", (tile[0] * columns, tile[1] * rows), "black")
    for index, frame in enumerate(frames):
        with Image.open(frame) as image:
            image = image.convert("RGB")
            image.thumbnail(tile)
            x = (index % columns) * tile[0] + (tile[0] - image.width) // 2
            y = (index // columns) * tile[1] + (tile[1] - image.height) // 2
            sheet.paste(image, (x, y))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "JPEG", quality=82)
    return out_path
