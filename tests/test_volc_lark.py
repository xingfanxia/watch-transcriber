import hashlib
import math
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import audio_compaction
import transcribe
import volc_lark


class CompactionPlanTests(unittest.TestCase):
    def test_does_not_compact_a_gap_of_exactly_ten_seconds(self):
        plan = audio_compaction.build_compaction_plan(
            duration_sec=30,
            speech_segments=[(0, 5, "speaker"), (15, 30, "speaker")],
            min_silence_sec=10,
            safety_margin_sec=3,
        )

        self.assertIsNone(plan)

    def test_keeps_safety_margins_and_builds_original_timeline(self):
        kept, timeline = audio_compaction.build_compaction_plan(
            duration_sec=40,
            speech_segments=[
                (0, 5, "speaker"),
                (20, 25, "speaker"),
                (35, 40, "speaker"),
            ],
            min_silence_sec=10,
            safety_margin_sec=3,
        )

        self.assertEqual(kept, [(0.0, 8.0), (17.0, 40)])
        self.assertEqual(
            timeline,
            [(0.0, 8.0, 0.0, 8.0), (8.0, 31.0, 17.0, 40)],
        )

    def test_timestamp_at_cut_boundary_uses_correct_side(self):
        timeline = [(0.0, 8.0, 0.0, 8.0), (8.0, 31.0, 17.0, 40.0)]

        self.assertEqual(
            audio_compaction.restore_timestamp_ms(
                8_000, timeline, prefer_later=True
            ),
            17_000,
        )
        self.assertEqual(
            audio_compaction.restore_timestamp_ms(
                8_000, timeline, prefer_later=False
            ),
            8_000,
        )
        self.assertEqual(
            audio_compaction.restore_timestamp_ms(
                10_000, timeline, prefer_later=True
            ),
            19_000,
        )

    def test_format_restores_provider_timestamps_to_original_audio(self):
        timeline = [(0.0, 8.0, 0.0, 8.0), (8.0, 31.0, 17.0, 40.0)]
        sentences = [
            {
                "speaker": {"id": "2"},
                "content": "hello",
                "start_time": 10_000,
                "end_time": 12_000,
            }
        ]

        self.assertEqual(
            volc_lark._format(sentences, timeline),
            "[00:00:19 - 00:00:21] SPEAKER_2: hello",
        )

    def test_environment_cannot_lower_safety_floors(self):
        speech = [(0, 5, "speaker"), (20, 25, "speaker")]
        with (
            patch.dict(
                "os.environ",
                {
                    "LARK_SILENCE_MIN_SEC": "1",
                    "LARK_SILENCE_SAFETY_MARGIN_SEC": "0",
                },
            ),
            patch.object(volc_lark, "probe_duration", return_value=30),
            patch.object(
                volc_lark, "build_compaction_plan", return_value=None
            ) as build,
            patch.object(
                volc_lark, "_to_mp3", return_value=Path("/tmp/full.mp3")
            ),
        ):
            _, timeline = volc_lark._prepare_mp3(Path("source.m4a"), speech)

        self.assertIsNone(timeline)
        build.assert_called_once_with(30, speech, 10.0, 3.0)

    def test_compaction_error_falls_back_to_complete_recording(self):
        compacted_error = volc_lark.LarkError("synthetic failure")
        complete_mp3 = Path("/tmp/complete.mp3")
        with (
            patch.object(volc_lark, "probe_duration", return_value=30),
            patch.object(
                volc_lark,
                "build_compaction_plan",
                return_value=(
                    [(0, 8), (17, 30)],
                    [(0, 8, 0, 8), (8, 21, 17, 30)],
                ),
            ),
            patch.object(
                volc_lark,
                "_to_mp3",
                side_effect=[compacted_error, complete_mp3],
            ),
        ):
            mp3, timeline = volc_lark._prepare_mp3(
                Path("source.m4a"),
                [(0, 5, "speaker"), (20, 25, "speaker")],
            )

        self.assertEqual(mp3, complete_mp3)
        self.assertIsNone(timeline)


class LarkPipelineTests(unittest.TestCase):
    def test_lark_path_passes_local_speech_timeline_to_provider(self):
        source = Path("source.m4a")
        speech = [(1, 2, "speaker")]
        with (
            patch.dict(
                "os.environ",
                {
                    "LARK_TRIM_LONG_SILENCE": "1",
                    "LARK_NUM_SPEAKERS": "0",
                },
            ),
            patch.object(transcribe, "_senko_installed", return_value=True),
            patch.object(transcribe, "_senko_diarize", return_value=speech),
            patch.object(
                volc_lark, "lark_transcribe", return_value="transcript"
            ) as provider,
        ):
            result = transcribe._lark_transcribe(source)

        self.assertEqual(result, "transcript")
        provider.assert_called_once_with(source, 0, speech)


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "ffmpeg and ffprobe are required",
)
class AudioCompactionIntegrationTests(unittest.TestCase):
    @staticmethod
    def _write_tone_silence_tone(path: Path):
        sample_rate = 8_000
        amplitude = 16_000
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            for second in range(16):
                for sample in range(sample_rate):
                    value = (
                        int(amplitude * math.sin(2 * math.pi * 440 * sample / sample_rate))
                        if second in (0, 15)
                        else 0
                    )
                    wav.writeframesraw(struct.pack("<h", value))

    @staticmethod
    def _max_volume_db(path: Path, start_sec: float) -> float:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats",
                "-ss", str(start_sec), "-t", "0.8", "-i", str(path),
                "-af", "volumedetect", "-f", "null", "-",
            ],
            capture_output=True, text=True, check=True,
        )
        match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", proc.stderr)
        if not match:
            raise AssertionError("ffmpeg did not report max_volume")
        return float(match.group(1))

    def test_compacts_only_the_planned_gap_and_never_changes_original(self):
        with tempfile.TemporaryDirectory(prefix="lark_test_") as temp_dir:
            source = Path(temp_dir) / "source.wav"
            self._write_tone_silence_tone(source)
            original_hash = hashlib.sha256(source.read_bytes()).digest()

            with patch.dict(
                "os.environ",
                {
                    "LARK_SILENCE_MIN_SEC": "10",
                    "LARK_SILENCE_SAFETY_MARGIN_SEC": "3",
                },
            ):
                mp3, timeline = volc_lark._prepare_mp3(
                    source,
                    speech_segments=[
                        (0, 1, "speaker"),
                        (15, 16, "speaker"),
                    ],
                )

            try:
                self.assertEqual(
                    timeline,
                    [(0.0, 4.0, 0.0, 4.0), (4.0, 8.0, 12.0, 16.0)],
                )
                self.assertAlmostEqual(
                    audio_compaction.probe_duration(mp3), 8.0, delta=0.5
                )
                self.assertGreater(self._max_volume_db(mp3, 0), -12)
                self.assertGreater(self._max_volume_db(mp3, 7.1), -12)
                self.assertEqual(
                    hashlib.sha256(source.read_bytes()).digest(),
                    original_hash,
                )
            finally:
                shutil.rmtree(mp3.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
