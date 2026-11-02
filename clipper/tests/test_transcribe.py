import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from clipper.transcribe import SAMPLE_RATE, Transcriber, TranscriptionError, load_audio

from .conftest import ffmpeg, requires_ffmpeg


@requires_ffmpeg
@pytest.mark.network
def test_whisper_transcribes_real_speech(tmp_path):
    """Downloads the tiny Whisper model (~75 MB): skipped where Hugging Face is unreachable."""
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    if " flite " not in filters:
        pytest.skip("ffmpeg sans synthèse vocale (flite)")
    speech = tmp_path / "speech.wav"
    ffmpeg("-f", "lavfi", "-i", "flite=text='Hello world. This is a test of the automatic clipper.':voice=slt", str(speech))
    transcriber = Transcriber("tiny.en", "en", cache_dir=tmp_path / "models")
    try:
        transcript = transcriber.transcribe(speech, prompt="Clipper test.")
    except TranscriptionError as exc:
        if "indisponible" in str(exc):
            pytest.skip(f"modèle Whisper non téléchargeable ici : {exc}")
        raise
    text = transcript.text.lower()
    assert "hello" in text and "test" in text
    assert all(b.start >= a.start for a, b in zip(transcript.words, transcript.words[1:]))
    assert transcript.words[0].start < 1.5


@requires_ffmpeg
def test_audio_is_decoded_by_ffmpeg(tmp_path):
    tone = tmp_path / "tone.mp4"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=2", "-ar", "44100", "-ac", "2", "-c:a", "aac", str(tone))
    audio = load_audio(tone)
    assert audio.dtype == np.float32
    assert abs(audio.size - 2 * SAMPLE_RATE) < SAMPLE_RATE // 10
    assert 0.05 < float(np.abs(audio).max()) <= 1.0


@requires_ffmpeg
def test_media_without_audio_is_a_transcription_error(media):
    with pytest.raises(TranscriptionError, match="illisible"):
        load_audio(media["silent"])


def segment(text, words, no_speech=0.05, logprob=-0.2):
    return SimpleNamespace(
        text=text, no_speech_prob=no_speech, avg_logprob=logprob,
        words=[SimpleNamespace(start=s, end=e, word=w) for s, e, w in words],
    )  # fmt: skip


@requires_ffmpeg
def test_transcriber_feeds_samples_and_drops_hallucinations(tmp_path):
    tone = tmp_path / "tone.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=3", str(tone))

    class FakeModel:
        def transcribe(self, audio, **options):
            self.audio, self.options = audio, options
            return iter([
                segment(" Bonjour à tous", [(0.1, 0.5, " Bonjour"), (0.5, 0.6, " à"), (0.6, 0.6, " tous")]),
                segment(" Sous-titres réalisés par la communauté d'Amara.org", [(1.0, 2.0, " Sous-titres")]),
                segment(" euh", [(2.1, 2.2, " euh")], no_speech=0.9, logprob=-1.5),
            ]), SimpleNamespace(language="fr")  # fmt: skip

    transcriber = Transcriber("small", "fr")
    transcriber._model = FakeModel()
    transcript = transcriber.transcribe(tone, prompt="Live de Squeezie.")
    assert isinstance(transcriber._model.audio, np.ndarray)
    assert transcriber._model.options["word_timestamps"] and transcriber._model.options["initial_prompt"] == "Live de Squeezie."
    assert [(w.text, w.start, w.end) for w in transcript.words] == [("Bonjour", 0.1, 0.5), ("à", 0.5, 0.6), ("tous", 0.6, 0.65)]


@requires_ffmpeg
def test_whisper_junk_on_game_sound_is_not_captioned(tmp_path):
    tone = tmp_path / "tone.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=3", str(tone))
    loop = [(0.1 * i, 0.1 * i + 0.1, token) for i, token in enumerate([" 9", " .9"] * 3)]

    class FakeModel:
        def transcribe(self, audio, **options):
            return iter([
                segment(" 9 .9 9 .9 9 .9 Nan, nan ?", [*loop, (1.0, 1.2, " Nan,"), (1.2, 1.4, " nan"), (1.4, 1.5, " ?")]),
                segment(" 9 .9", [(1.6, 1.7, " 9"), (1.7, 1.8, " .9")]),  # not a single letter
                segment(" Fait는ält Aïe, il a 16 ans", [(2.0, 2.3, " Fait는ält"), (2.3, 2.5, " Aïe,"), (2.5, 2.6, " il"),
                                                    (2.6, 2.7, " a"), (2.7, 2.8, " 16"), (2.8, 2.9, " ans")]),
            ]), SimpleNamespace(language="fr")  # fmt: skip

    transcriber = Transcriber("small", "fr")
    transcriber._model = FakeModel()
    transcript = transcriber.transcribe(tone)
    assert [w.text for w in transcript.words] == ["Nan,", "nan", "?", "Aïe,", "il", "a", "16", "ans"]


@requires_ffmpeg
def test_french_elisions_stay_one_word(tmp_path):
    tone = tmp_path / "tone.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=2", str(tone))

    class FakeModel:
        def transcribe(self, audio, **options):
            return iter([
                segment(" C'est celle-ci, la 1.6", [(0.0, 0.1, " C"), (0.1, 0.3, "'est"), (0.3, 0.5, " celle"), (0.5, 0.7, "-ci,"),
                                                   (0.7, 0.8, " la"), (0.8, 0.9, " 1"), (0.9, 1.0, ".6")]),
                segment("'Allô ?", [(1.0, 1.2, "'Allô"), (1.2, 1.3, " ?")]),  # nothing before it in its sentence
            ]), SimpleNamespace(language="fr")  # fmt: skip

    transcriber = Transcriber("small", "fr")
    transcriber._model = FakeModel()
    words = transcriber.transcribe(tone).words
    assert [(w.text, w.start, w.end) for w in words] == [
        ("C'est", 0.0, 0.3), ("celle-ci,", 0.3, 0.7), ("la", 0.7, 0.8), ("1.6", 0.8, 1.0), ("'Allô", 1.0, 1.2), ("?", 1.2, 1.3),
    ]  # fmt: skip


def test_a_phrase_is_located_even_said_loosely():
    from clipper.models import Word
    from clipper.transcribe import locate

    said = "Bon alors petit C'est quoi la plus grosse insulte que vous connaissez ? Nique ta grand-mère"
    words = [Word(i * 0.5, i * 0.5 + 0.4, text) for i, text in enumerate(said.split())]
    assert locate(words, "la plus grosse insulte que vous connaissez") == 2.5
    assert locate(words, "c'est quoi la plus grosse insulte que tu connais") == 1.5  # not word for word
    assert locate(words, "NIQUE TA GRAND MERE") == 6.5  # case, accents and hyphens aside
    assert locate(words, "bonjour madame") is None and locate(words, "?") is None
    assert locate(words, "la plus belle insulte du monde entier") is None  # too few of its words


@requires_ffmpeg
def test_only_the_part_asked_for_is_heard(tmp_path):
    tone = tmp_path / "tone.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=3", str(tone))
    assert abs(load_audio(tone).size - 3 * SAMPLE_RATE) < SAMPLE_RATE // 10
    assert abs(load_audio(tone, start=1.0, end=2.5).size - 1.5 * SAMPLE_RATE) < SAMPLE_RATE // 10
    assert abs(load_audio(tone, start=2.0).size - SAMPLE_RATE) < SAMPLE_RATE // 10
