"""Pure planning plus local ffmpeg helpers for conservative audio compaction."""

import shutil
import subprocess
import tempfile
from pathlib import Path


class AudioCompactionError(RuntimeError):
    """The temporary audio copy could not be prepared or validated."""


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True,
    )
    try:
        duration = float(proc.stdout.strip())
    except ValueError as e:
        raise AudioCompactionError(
            f"ffprobe could not read duration for {path.name}"
        ) from e
    if proc.returncode != 0 or duration <= 0:
        raise AudioCompactionError(
            f"ffprobe could not read duration for {path.name}"
        )
    return duration


def build_compaction_plan(
    duration_sec: float,
    speech_segments,
    min_silence_sec: float,
    safety_margin_sec: float,
):
    """Return kept source ranges plus a compacted→original timeline.

    Speech ranges come from a local VAD/diarizer. Only gaps strictly longer
    than the configured minimum are shortened, and every gap keeps a safety
    margin at both ends. Timeline entries are:
      (compacted_start, compacted_end, original_start, original_end)
    """
    if duration_sec <= 0 or not speech_segments:
        return None

    speech = []
    for segment in speech_segments:
        try:
            start = max(0.0, float(segment[0]))
            end = min(duration_sec, float(segment[1]))
        except (TypeError, ValueError, IndexError):
            return None
        if end > start:
            speech.append((start, end))
    if not speech:
        return None

    merged = []
    for start, end in sorted(speech):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    gaps = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration_sec:
        gaps.append((cursor, duration_sec))

    cuts = []
    for start, end in gaps:
        if end - start <= min_silence_sec:
            continue
        cut_start = start + safety_margin_sec
        cut_end = end - safety_margin_sec
        if cut_end > cut_start:
            cuts.append((cut_start, cut_end))
    if not cuts:
        return None

    kept = []
    cursor = 0.0
    for start, end in cuts:
        if start > cursor:
            kept.append((cursor, start))
        cursor = end
    if cursor < duration_sec:
        kept.append((cursor, duration_sec))
    if not kept:
        return None

    timeline = []
    compacted_cursor = 0.0
    for original_start, original_end in kept:
        segment_duration = original_end - original_start
        timeline.append(
            (
                compacted_cursor,
                compacted_cursor + segment_duration,
                original_start,
                original_end,
            )
        )
        compacted_cursor += segment_duration
    return kept, timeline


def encode_mp3(
    src: Path, bitrate: str, kept_segments=None
) -> Path:
    """Encode a complete or range-compacted temporary 16kHz mono MP3."""
    temp_dir = Path(tempfile.mkdtemp(prefix="lark_"))
    dst = temp_dir / (src.stem.replace(" ", "_") + ".mp3")
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
    ]
    if kept_segments:
        filters = []
        labels = []
        for index, (start, end) in enumerate(kept_segments):
            label = f"kept{index}"
            filters.append(
                f"[0:a]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
            labels.append(f"[{label}]")
        filters.append(
            "".join(labels)
            + f"concat=n={len(labels)}:v=0:a=1[compacted]"
        )
        command += [
            "-filter_complex", ";".join(filters),
            "-map", "[compacted]",
        ]
    command += ["-ac", "1", "-ar", "16000", "-b:a", bitrate, str(dst)]
    try:
        proc = subprocess.run(command, capture_output=True, text=True)
        if proc.returncode != 0 or not dst.exists():
            raise AudioCompactionError(
                f"ffmpeg conversion failed for {src.name}: {proc.stderr[-400:]}"
            )
        return dst
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def restore_timestamp_ms(ms, timeline, prefer_later: bool = False):
    """Map one compacted-audio timestamp back onto the original recording."""
    if not timeline:
        return ms
    try:
        compacted_sec = max(0.0, float(ms or 0) / 1000)
    except (TypeError, ValueError):
        return ms

    epsilon = 1e-6
    for index, (
        compacted_start,
        compacted_end,
        original_start,
        original_end,
    ) in enumerate(timeline):
        is_last = index == len(timeline) - 1
        before_end = compacted_sec < compacted_end - epsilon
        at_end_for_earlier = (
            not prefer_later and compacted_sec <= compacted_end + epsilon
        )
        if before_end or at_end_for_earlier or is_last:
            offset = min(
                max(0.0, compacted_sec - compacted_start),
                original_end - original_start,
            )
            return round((original_start + offset) * 1000)
    return round(timeline[-1][3] * 1000)
