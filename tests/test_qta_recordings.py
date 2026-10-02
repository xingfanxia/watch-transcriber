import shutil
import subprocess
from pathlib import Path

import pytest

import transcribe
from transcribe import (
    extract_qta_audio,
    find_new_recordings,
    find_recordings_by_date,
    is_recording,
)


def test_is_recording_accepts_m4a_and_qta():
    assert is_recording(Path("20260712 093015-1A2B3C4D.qta"))
    assert is_recording(Path("20260726 013421-44BB9B24.m4a"))
    assert not is_recording(Path("20261002 notes.txt"))


def test_scan_and_reprocess_pick_up_qta(tmp_path, monkeypatch):
    for name in (
        "20261002 125439-AAAA0001.qta",
        "20261002 130000-BBBB0002.m4a",
        "20261002 130500-CCCC0003.m4a",
        "20261002 notes.txt",
    ):
        (tmp_path / name).write_bytes(b"x")
    monkeypatch.setattr(transcribe, "VOICE_MEMOS_DIR", tmp_path)

    new = {p.name for p in find_new_recordings({"20261002 130500-CCCC0003.m4a"})}
    assert new == {"20261002 125439-AAAA0001.qta", "20261002 130000-BBBB0002.m4a"}

    by_date = {p.name for p in find_recordings_by_date("2026-10-02")}
    assert by_date == {
        "20261002 125439-AAAA0001.qta",
        "20261002 130000-BBBB0002.m4a",
        "20261002 130500-CCCC0003.m4a",
    }


def _probe(path: Path, entries: str) -> list[str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", entries, "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_extract_qta_audio_keeps_only_first_audio_track(tmp_path):
    # Synthetic stand-in for an iPhone .qta: QuickTime container holding a
    # stereo AAC track first and a second audio track after it.
    src = tmp_path / "20260712 093015-1A2B3C4D.qta"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
         "-map", "0:a", "-map", "1:a", "-c:a", "aac", "-ac:a:0", "2",
         "-f", "mov", str(src)],
        check=True,
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    out = extract_qta_audio(src, out_dir)

    assert out == out_dir / "20260712 093015-1A2B3C4D.m4a"
    assert _probe(out, "stream=codec_name,channels") == ["aac,2"]
