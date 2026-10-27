from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .models import Transcript, Word

log = logging.getLogger(__name__)

# Phrases Whisper invents on music, silence or crowd noise (mostly from subtitled TV in its training data).
_HALLUCINATIONS = re.compile(
    r"amara\.org|sous-titr|merci d'avoir regardé|abonnez-vous|thanks for watching|subtitles by|♪",
    re.IGNORECASE,
)


class TranscriptionError(Exception):
    pass


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

    def transcribe(self, media: Path, *, prompt: str | None = None) -> Transcript:
        model = self._load()
        try:
            segments, info = model.transcribe(
                str(media),
                language=self.language,
                word_timestamps=True,
                vad_filter=True,
                beam_size=5,
                condition_on_previous_text=False,
                initial_prompt=prompt,
            )
            words: list[Word] = []
            for segment in segments:
                if _HALLUCINATIONS.search(segment.text or ""):
                    continue
                if segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0:
                    continue
                for word in segment.words or []:
                    text = word.word.strip()
                    if text:
                        words.append(Word(start=round(word.start, 3), end=round(max(word.end, word.start + 0.05), 3), text=text))
        except Exception as exc:
            raise TranscriptionError(f"transcription de {media.name} impossible : {exc}") from exc
        return Transcript(words=words, language=getattr(info, "language", None))


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
