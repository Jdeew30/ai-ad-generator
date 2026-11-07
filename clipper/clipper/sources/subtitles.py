"""YouTube captions (the channel's own or YouTube's automatic ones) turned into a timed transcript Claude can read.

A video that has neither « most replayed » peaks nor timestamped comments yet (most videos in their first days) still
has its captions: Claude reads them and picks the passages worth a short (see ClaudeCurator.find_moments).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_CUE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s+-->\s+")
_TAG = re.compile(r"<[^>]+>")
LINE_SECONDS = 15.0  # captions are grouped into lines of about this long, each with its time
MAX_CHARS = 45_000  # transcript sent to Claude at most (about 11 000 tokens, an hour and a half of speech)


def parse_captions(path: Path) -> list[tuple[float, str]]:
    """(start in seconds, text) of each caption of a json3 or vtt file, in order, empty ones left out."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json3":
        return _parse_json3(text)
    return _parse_vtt(text)


def _parse_json3(text: str) -> list[tuple[float, str]]:
    try:
        events = json.loads(text).get("events") or []
    except (json.JSONDecodeError, AttributeError):
        return []
    cues: list[tuple[float, str]] = []
    for event in events:
        if not isinstance(event, dict) or "segs" not in event:
            continue
        words = "".join(str(seg.get("utf8") or "") for seg in event["segs"] if isinstance(seg, dict))
        words = " ".join(words.split())
        if words:
            cues.append((float(event.get("tStartMs") or 0) / 1000, words))
    return cues


def _parse_vtt(text: str) -> list[tuple[float, str]]:
    """WebVTT cues; YouTube's automatic ones repeat the previous line in each cue, kept only once."""
    cues: list[tuple[float, str]] = []
    seen: set[str] = set()
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = block.strip().split("\n")
        for index, line in enumerate(lines):
            match = _CUE.match(line)
            if not match:
                continue
            hours, minutes, seconds, millis = match.groups()
            start = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000
            for raw in lines[index + 1:]:
                words = " ".join(_TAG.sub("", raw).split())
                if words and words not in seen:
                    seen.add(words)
                    cues.append((start, words))
            break
    return cues


def timed_transcript(cues: list[tuple[float, str]], *, every: float = LINE_SECONDS, max_chars: int = MAX_CHARS) -> str:
    """« [12:34] what is said » lines of about `every` seconds, cut at `max_chars` characters."""
    lines: list[str] = []
    start: float | None = None
    words: list[str] = []
    for at, text in cues:
        if start is None:
            start = at
        elif at - start >= every and words:
            lines.append(f"[{_clock(start)}] {' '.join(words)}")
            start, words = at, []
        words.append(text)
    if words and start is not None:
        lines.append(f"[{_clock(start)}] {' '.join(words)}")
    transcript, size = [], 0
    for line in lines:
        if size + len(line) + 1 > max_chars:
            transcript.append("[…] (suite coupée)")
            break
        transcript.append(line)
        size += len(line) + 1
    return "\n".join(transcript)


def _clock(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"
