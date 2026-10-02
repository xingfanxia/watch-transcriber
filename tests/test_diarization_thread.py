import threading
import time
from pathlib import Path

import pytest

import transcribe


def test_failed_transcription_waits_for_background_diarization(monkeypatch):
    finished = threading.Event()

    def slow_diarize(_path):
        time.sleep(0.5)
        finished.set()
        return []

    def failing_transcribe(*_args, **_kwargs):
        raise RuntimeError("simulated Gemini failure")

    monkeypatch.setattr(transcribe, "_pyannote_available", lambda: True)
    monkeypatch.setattr(transcribe, "_pyannote_diarize", slow_diarize)
    monkeypatch.setattr(transcribe, "get_audio_duration", lambda _p: 30.0)
    monkeypatch.setattr(transcribe, "_detect_speech_end", lambda _p: 30.0)
    monkeypatch.setattr(transcribe, "_chunk_audio_at_silence",
                        lambda p: ([(p, 0.0, 30.0)], lambda: None))
    monkeypatch.setattr(transcribe, "_gemini_transcribe_one", failing_transcribe)

    with pytest.raises(RuntimeError):
        transcribe._gemini_transcribe(None, Path("20260712 093015-1A2B3C4D.m4a"))

    # The next recording must not start while this diarization still runs:
    # two concurrent Senko runs abort the process.
    assert finished.is_set()
