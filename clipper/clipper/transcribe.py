from __future__ import annotations

import logging
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np

from .media import MediaError, ffmpeg_bin
from .models import Transcript, Word

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # what Whisper expects

# Phrases Whisper invents on music, silence or crowd noise (mostly from subtitled TV in its training data).
_HALLUCINATIONS = re.compile(
    r"amara\.org|sous-titr|merci d'avoir regardé|abonnez-vous|thanks for watching|subtitles by|♪",
    re.IGNORECASE,
)
# This many words in a row without a single letter are Whisper looping on game sound (« 9 .9 9 .9 … »), not speech.
_LOOP = 4
# Whisper cuts « c'est », « celle-ci » and « 1.6 » before the mark, with no space: that piece belongs to the word before.
_ATTACHED = ("'", "’", "-", ".")


class TranscriptionError(Exception):
    pass


def load_audio(media: Path, *, start: float | None = None, end: float | None = None) -> np.ndarray:
    """Mono 16 kHz float32 samples, decoded by ffmpeg: the whole media, or from `start` to `end` (seconds).

    faster-whisper decodes files with PyAV, and its call breaks on recent PyAV releases
    (`metadata_errors` was removed); handing it samples sidesteps PyAV entirely.
    """
    try:
        section = ["-ss", f"{start:.3f}"] if start else []
        length = ["-t", f"{end - (start or 0.0):.3f}"] if end is not None else []
        command = [ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error", *section, "-i", str(media), *length,
                   "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]  # fmt: skip
        result = subprocess.run(command, capture_output=True, timeout=900)
    except (MediaError, subprocess.TimeoutExpired) as exc:
        raise TranscriptionError(f"audio de {media.name} illisible : {exc}") from exc
    if result.returncode != 0:
        raise TranscriptionError(f"audio de {media.name} illisible : {result.stderr.decode(errors='replace').strip()[-300:]}")
    return np.frombuffer(result.stdout, dtype=np.float32)


class Transcriber:
    """faster-whisper on CPU (int8). The model is loaded lazily, once per run."""

    def __init__(self, model: str, language: str | None, *, cache_dir: Path | None = None, compute_type: str = "int8") -> None:
        self.model_name = model
        self.language = language
        self.cache_dir = cache_dir
        self.compute_type = compute_type
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError("faster-whisper n'est pas installé (pip install -r requirements.txt)") from exc
            log.info("Chargement du modèle Whisper « %s »…", self.model_name)
            try:
                self._model = WhisperModel(
                    self.model_name,
                    device="cpu",
                    compute_type=self.compute_type,
                    download_root=str(self.cache_dir) if self.cache_dir else None,
                )
            except Exception as exc:  # network, disk, unknown model name: captions are optional
                raise TranscriptionError(f"modèle Whisper « {self.model_name} » indisponible : {exc}") from exc
        return self._model

    def transcribe(self, media: Path, *, prompt: str | None = None, start: float | None = None,
                   end: float | None = None, quick: bool = False) -> Transcript:
        """The words said in `media`, or from `start` to `end`: their times then count from `start`.

        `quick` decodes greedily (about twice as fast): enough to find a phrase in a long video.
        """
        audio = load_audio(media, start=start, end=end)
        if audio.size < SAMPLE_RATE // 2:
            return Transcript(words=[], language=self.language)
        model = self._load()
        try:
            segments, info = model.transcribe(
                audio,
                language=self.language,
                word_timestamps=True,
                vad_filter=True,
                beam_size=1 if quick else 5,
                condition_on_previous_text=False,
                initial_prompt=prompt,
            )
            words: list[Word] = []
            for segment in segments:
                if _HALLUCINATIONS.search(segment.text or "") or not _lettered(segment.text or ""):
                    continue
                if segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0:
                    continue
                first = len(words)
                for word in segment.words or []:
                    text = word.word.strip()
                    if not text or not _latin(text):
                        continue
                    end = round(max(word.end, word.start + 0.05), 3)
                    if word.word.startswith(_ATTACHED) and len(words) > first:
                        words[-1] = Word(start=words[-1].start, end=end, text=words[-1].text + text)
                    else:
                        words.append(Word(start=round(word.start, 3), end=end, text=text))
        except Exception as exc:
            raise TranscriptionError(f"transcription de {media.name} impossible : {exc}") from exc
        return Transcript(words=_without_loops(words), language=getattr(info, "language", None))


def _lettered(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def _latin(text: str) -> bool:
    """Whether every letter is a Latin one, accents included: captions are drawn with a Latin font, and the
    other scripts only come out of Whisper as junk on game sound (« Fait는ält »)."""
    return all("LATIN" in unicodedata.name(ch, "") for ch in text if ch.isalpha())


def _without_loops(words: list[Word]) -> list[Word]:
    """`words` without the runs of _LOOP or more words that have no letter."""
    kept: list[Word] = []
    run: list[Word] = []
    for word in [*words, None]:
        if word is not None and not _lettered(word.text):
            run.append(word)
            continue
        if len(run) < _LOOP:
            kept.extend(run)
        run = []
        if word is not None:
            kept.append(word)
    return kept


def shift_words(words: list[Word], start: float, end: float | None) -> list[Word]:
    """Words inside [start, end], re-timed so that `start` becomes 0 (after trimming the media)."""
    shifted = []
    for word in words:
        if word.end <= start or (end is not None and word.start >= end):
            continue
        new_start = max(word.start, start) - start
        new_end = (min(word.end, end) if end is not None else word.end) - start
        if new_end > new_start:
            shifted.append(Word(start=round(new_start, 3), end=round(new_end, 3), text=word.text))
    return shifted


def locate(words: list[Word], phrase: str, *, min_ratio: float = 0.6) -> float | None:
    """When `phrase` is said among `words`: the start of the first of its words heard, or None.

    Said loosely counts too (« que tu connais » for « que vous connaissez »): the passage that holds the most of its
    words, in order, wins if it holds at least `min_ratio` of them; the earliest one on a tie.
    """
    found = occurrences(words, phrase, min_ratio=min_ratio)
    return max(found, key=lambda pair: (pair[1], -pair[0]))[0] if found else None


def occurrences(words: list[Word], phrase: str, *, min_ratio: float = 0.6) -> list[tuple[float, float]]:
    """Every time `phrase` is said, loosely or not: (start, share of its words heard), a few seconds apart at least."""
    wanted = _tokens(phrase)
    heard = [(word.start, token) for word in words for token in _tokens(word.text)]
    if not wanted:
        return []
    span = len(wanted) + 3  # a few words more or less said in between
    found: list[tuple[float, float]] = []
    for index, (at, token) in enumerate(heard):
        if token not in wanted:
            continue
        ratio = _in_order(wanted, [t for _, t in heard[index:index + span]]) / len(wanted)
        if ratio < min_ratio:
            continue
        if found and at - found[-1][0] < 5.0:  # the same saying, caught from one of its later words
            if ratio > found[-1][1]:
                found[-1] = (at, ratio)
            continue
        found.append((at, ratio))
    return found


def context(words: list[Word], at: float, *, before: float = 4.0, after: float = 8.0) -> str:
    """What is said around `at`, for the log."""
    return " ".join(word.text for word in words if at - before <= word.start <= at + after)


def _tokens(text: str) -> list[str]:
    """« C'est quoi, la grand-mère ? » -> ['c', 'est', 'quoi', 'la', 'grand', 'mere']."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", plain)


def _in_order(wanted: list[str], heard: list[str]) -> int:
    """How many of `wanted` are in `heard`, in the same order (longest common subsequence)."""
    row = [0] * (len(heard) + 1)
    for token in wanted:
        previous = row[:]
        for index, other in enumerate(heard, start=1):
            row[index] = previous[index - 1] + 1 if token == other else max(previous[index], row[index - 1])
    return row[-1]
