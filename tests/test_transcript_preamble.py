from pathlib import Path

import transcribe

GEMINI_OUTPUT = (
    "以下是为您转录的音频文本：\n"
    "\n"
    "[00:00:00 - 00:00:12] SPEAKER_0: Okay, testing the watcher again.\n"
    "[00:00:12 - 00:00:20] SPEAKER_0: I work as an AI engineer, by the way.\n"
)


def test_single_chunk_transcript_drops_gemini_preamble(monkeypatch):
    monkeypatch.setattr(transcribe, "_pyannote_available", lambda: False)
    monkeypatch.setattr(transcribe, "get_audio_duration", lambda _p: 20.0)
    monkeypatch.setattr(transcribe, "_detect_speech_end", lambda _p: 20.0)
    monkeypatch.setattr(transcribe, "_chunk_audio_at_silence",
                        lambda p: ([(p, 0.0, 20.0)], lambda: None))
    monkeypatch.setattr(transcribe, "_gemini_transcribe_one", lambda *_a: GEMINI_OUTPUT)

    text = transcribe._gemini_transcribe(None, Path("20260712 093015-1A2B3C4D.m4a"))

    assert text == (
        "[00:00:00 - 00:00:12] SPEAKER_0: Okay, testing the watcher again.\n"
        "[00:00:12 - 00:00:20] SPEAKER_0: I work as an AI engineer, by the way."
    )
