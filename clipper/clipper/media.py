from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
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


def max_volume(path: Path) -> float | None:
    """Loudest level of the audio track, in dB (0 = full scale, about -91 = digital silence); None without audio."""
    command = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("ffmpeg a dépassé 600s") from exc
    match = re.search(r"max_volume:\s*(-?inf|-?[\d.]+) dB", result.stderr)
    if result.returncode != 0 or match is None:
        return None
    return float(match.group(1))


JOIN_RATE = 8000  # Hz: plenty to line clips up on their sound


def _sound(path: Path) -> np.ndarray:
    """The audio track as mono samples at JOIN_RATE."""
    command = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
               "-map", "0:a:0", "-ac", "1", "-ar", str(JOIN_RATE), "-f", "f32le", "-"]  # fmt: skip
    try:
        result = subprocess.run(command, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("ffmpeg a dépassé 600s") from exc
    if result.returncode != 0:
        raise MediaError(f"son illisible dans {path.name} : {result.stderr.decode(errors='replace').strip()[-300:]}")
    return np.frombuffer(result.stdout, dtype=np.float32)


def overlap_start(first: np.ndarray, second: np.ndarray, *, probe_seconds: float = 4.0, min_match: float = 0.8) -> float | None:
    """Where, in `first`, the sound of `second` begins (seconds), or None when they do not overlap.

    The first seconds of `second` are looked for in `first` by normalised cross-correlation: two clips of one
    live share the very same samples, so the match is near-perfect where they overlap and weak elsewhere.
    """
    size = int(probe_seconds * JOIN_RATE)
    template = second[:size].astype(np.float64)
    if len(template) < size or len(first) < size or not np.any(template):
        return None
    signal = first.astype(np.float64)
    length = 1 << int(np.ceil(np.log2(len(signal) + size)))
    products = np.fft.irfft(np.fft.rfft(signal, length) * np.conj(np.fft.rfft(template, length)), length)
    products = products[: len(signal) - size + 1]
    energy = np.cumsum(np.concatenate(([0.0], signal**2)))
    windows = np.sqrt(np.maximum(energy[size:] - energy[:-size], 1e-12))
    scores = products / (windows * np.linalg.norm(template))
    best = int(np.argmax(scores))
    return best / JOIN_RATE if scores[best] >= min_match else None


def join_overlapping(paths: list[Path], out: Path) -> Path:
    """Clips of one moment made one after the other by viewers, as a single video.

    Each clip is cut where the next one begins (found on their sound), so that nothing is shown twice; clips that
    do not overlap simply follow each other. The result takes the size and frame rate of the first clip.
    """
    infos = [probe(path) for path in paths]
    if not all(info.has_audio for info in infos):
        raise MediaError("un des clips à recoller n'a pas de son")
    sounds = [_sound(path) for path in paths]
    ends = [info.duration for info in infos]
    for index in range(len(paths) - 1):
        begins = overlap_start(sounds[index], sounds[index + 1])
        if begins is not None:
            ends[index] = begins
    kept = [(index, end) for index, end in enumerate(ends) if end >= 0.5]  # a clip that the next one covers entirely goes
    width, height, fps = infos[0].width, infos[0].height, infos[0].fps or 30.0
    graph = []
    for slot, (index, end) in enumerate(kept):
        graph.append(f"[{index}:v]trim=0:{end:.3f},setpts=PTS-STARTPTS,scale={width}:{height},setsar=1,fps={fps:.3f}[v{slot}]")
        graph.append(f"[{index}:a]atrim=0:{end:.3f},asetpts=PTS-STARTPTS,aresample=48000[a{slot}]")
    graph.append("".join(f"[v{slot}][a{slot}]" for slot in range(len(kept))) + f"concat=n={len(kept)}:v=1:a=1[v][a]")
    inputs = [arg for path in paths for arg in ("-i", str(path))]
    run_ffmpeg([*inputs, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset",
                "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)])  # fmt: skip
    return out


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
