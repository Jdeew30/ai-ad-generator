import subprocess

import pytest

from clipper.transcribe import Transcriber, TranscriptionError

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
