#!/usr/bin/env python3
"""watch-transcriber: Voice Memos → Gemini STT → pluggable delivery.

Monitors Apple Voice Memos recordings directory for new .m4a files,
transcribes and summarizes them using Gemini 3 Flash in a single
multimodal call, then delivers structured notes to configurable targets.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# Load .env without external dependency
SCRIPT_DIR = Path(__file__).parent


def load_dotenv_simple(path: Path):
    """Load .env file into os.environ (no third-party dependency)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv_simple(SCRIPT_DIR / ".env")

# Fallback: check common locations for Gemini key
for fallback in [
    Path.home() / ".claude/plugins/transcribe/.env",
    Path.home() / ".claude/plugins/nanobanana/.env",
]:
    if not os.environ.get("GEMINI_API_KEY"):
        load_dotenv_simple(fallback)

# Voice Memos recordings directory
VOICE_MEMOS_DIR = Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"

# State file to track processed recordings
STATE_DIR = SCRIPT_DIR / "state"
STATE_FILE = STATE_DIR / "processed.json"

# Gemini model
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# Skip recordings shorter than this (seconds). Voice Memos sometimes captures
# brief accidental taps; transcribing them wastes API quota.
MIN_DURATION_SECONDS = int(os.environ.get("MIN_DURATION_SECONDS", "60"))

# Provider selection: "gemini" (default) or "openai" (gpt-4o-transcribe-diarize)
STT_PROVIDER = os.environ.get("STT_PROVIDER", "gemini").lower()

# Chunking thresholds (used by both providers).
# Gemini 3 Flash loses coherence on >15min audio (loops, fabricated timestamps).
# OpenAI gpt-4o-transcribe-diarize has 25MB / 1500s per-request limits.
# Target chunk size sits comfortably under both, with silence-aware boundaries
# so we don't cut mid-sentence.
CHUNK_THRESHOLD_SEC = int(os.environ.get("CHUNK_THRESHOLD_SEC", "1080"))  # only chunk if audio > 18 min
CHUNK_TARGET_SEC = int(os.environ.get("CHUNK_TARGET_SEC", "720"))          # aim for ~12 min chunks
CHUNK_MIN_SEC = int(os.environ.get("CHUNK_MIN_SEC", "480"))                # but at least 8 min
CHUNK_MAX_SEC = int(os.environ.get("CHUNK_MAX_SEC", "900"))                # and at most 15 min (Gemini coherence edge)
CHUNK_PARALLELISM = int(os.environ.get("CHUNK_PARALLELISM", "8"))          # concurrent chunk API calls
# Overlap between adjacent chunks (in seconds). Lets us reconcile speaker
# labels across chunks: the overlap region is transcribed in BOTH chunks,
# so we can match SPEAKER_X in chunk k to SPEAKER_Y in chunk k-1 by
# timestamp proximity of turns in the overlap. Longer overlap = more
# voting evidence per speaker, more robust mapping. 60s gives ~6-10 turns
# of evidence per boundary in typical conversation. Set 0 to disable.
CHUNK_OVERLAP_SEC = int(os.environ.get("CHUNK_OVERLAP_SEC", "60"))


def load_state() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_state(processed: set[str]):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(processed), indent=2))


def find_new_recordings(processed: set[str]) -> list[Path]:
    if not VOICE_MEMOS_DIR.exists():
        print(f"Voice Memos directory not found: {VOICE_MEMOS_DIR}")
        return []

    new_files = []
    for f in VOICE_MEMOS_DIR.iterdir():
        if f.suffix == ".m4a" and f.name not in processed:
            new_files.append(f)

    return sorted(new_files, key=lambda p: p.stat().st_mtime)


def find_recordings_by_date(date_str: str) -> list[Path]:
    """Find all .m4a files for a given date (YYYY-MM-DD or YYYYMMDD).

    Matches by Voice Memos filename prefix, e.g. "20260513 184600 watch.m4a".
    Ignores the processed-state set — caller decides whether to skip duplicates.
    """
    yyyymmdd = date_str.replace("-", "")
    if len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        raise ValueError(f"Bad date: {date_str!r} (expected YYYY-MM-DD or YYYYMMDD)")
    if not VOICE_MEMOS_DIR.exists():
        return []
    matches = [
        f for f in VOICE_MEMOS_DIR.iterdir()
        if f.suffix == ".m4a" and f.name.startswith(yyyymmdd)
    ]
    return sorted(matches, key=lambda p: p.stat().st_mtime)


def get_audio_duration(audio_path: Path):
    """Return audio duration in seconds via macOS `afinfo`. None if unparseable."""
    try:
        out = subprocess.run(
            ["afinfo", str(audio_path)],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if "estimated duration:" in line:
            try:
                return float(line.split(":", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                return None
    return None


def ensure_genai():
    """Import google-genai, installing into project venv if needed."""
    try:
        from google import genai
        return genai
    except ImportError:
        pass

    # Auto-install into project-local venv
    import subprocess
    venv_dir = SCRIPT_DIR / "venv"
    if not venv_dir.exists():
        print("  Creating venv and installing google-genai...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    pip = venv_dir / "bin" / "pip"
    subprocess.run([str(pip), "install", "-q", "google-genai"], check=True)

    # Add to path and import
    for p in (venv_dir / "lib").glob("python*/site-packages"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    try:
        from google import genai
        return genai
    except ImportError:
        print("Failed to install google-genai. Run: pip install google-genai")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Audio chunking — silence-aware, used by both Gemini and OpenAI providers
# ---------------------------------------------------------------------------

def _fmt_time(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _parse_time(ts: str) -> int:
    parts = ts.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


_TIMESTAMP_RE = re.compile(r"\[(\d{1,2}:\d{1,2}:\d{1,2})\s*-\s*(\d{1,2}:\d{1,2}:\d{1,2})\]")
# Matches a single diarized turn line like:
#   [00:00:20 - 00:00:45] SPEAKER_0: content
# Tolerates 1-2 digit timestamp components and either SPEAKER_N or single-letter (OpenAI) labels.
_TURN_RE = re.compile(
    r"^\[(\d{1,2}:\d{1,2}:\d{1,2})\s*-\s*(\d{1,2}:\d{1,2}:\d{1,2})\]\s+(SPEAKER_\d+|[A-Z]):\s*(.*)$"
)


def _offset_timestamps(transcript: str, offset_sec: float) -> str:
    """Shift every `[HH:MM:SS - HH:MM:SS]` timestamp by offset_sec."""

    def replace(m):
        start = _parse_time(m.group(1)) + offset_sec
        end = _parse_time(m.group(2)) + offset_sec
        return f"[{_fmt_time(start)} - {_fmt_time(end)}]"

    return _TIMESTAMP_RE.sub(replace, transcript)


def _offset_and_filter_timestamps(transcript: str, offset_sec: float, max_internal_sec: float) -> str:
    """Shift timestamps by offset; drop turn lines whose internal start > max_internal_sec.

    Gemini occasionally hallucinates timestamps far past the chunk's actual
    duration (e.g., a 12-min chunk emits `[10:20:00]`). After offset these
    end up as obviously-bogus absolute times. Drop them so downstream
    reconciliation and the final transcript stay clean.
    """
    out_lines = []
    tolerance = 30.0  # seconds — keep turns slightly past chunk end (Gemini may extend)
    for line in transcript.splitlines():
        m = _TURN_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        internal_start = _parse_time(m.group(1))
        if internal_start > max_internal_sec + tolerance:
            continue  # hallucinated past chunk end — drop
        internal_end = _parse_time(m.group(2))
        new_start = internal_start + offset_sec
        new_end = internal_end + offset_sec
        new_line = _TIMESTAMP_RE.sub(
            f"[{_fmt_time(new_start)} - {_fmt_time(new_end)}]", line, count=1
        )
        out_lines.append(new_line)
    return "\n".join(out_lines)


def _parse_diarized_lines(transcript: str):
    """Return list of dicts per line; turn lines have start/end/speaker/content."""
    out = []
    for line in transcript.splitlines():
        m = _TURN_RE.match(line)
        if m:
            out.append({
                "is_turn": True,
                "start": _parse_time(m.group(1)),
                "end": _parse_time(m.group(2)),
                "speaker": m.group(3),
                "content": m.group(4),
                "raw": line,
            })
        else:
            out.append({"is_turn": False, "raw": line})
    return out


def _reconcile_speakers_across_chunks(chunk_records):
    """Align speaker labels across chunks.

    Each chunk except the first overlaps the previous by ~CHUNK_OVERLAP_SEC.
    For chunks 1..N: find diarized turns in the overlap region [chunk.offset,
    prev.end_abs] in BOTH chunks; for each cur turn, find the prev turn whose
    start time is closest. Each match votes "cur SPEAKER_X == prev SPEAKER_Y".
    Most-voted mapping per cur speaker wins; apply to all of cur's turns.

    Mapping is applied cumulatively — chunk 0 is canonical, chunk 1 maps to
    chunk 0, chunk 2 maps to (already-remapped) chunk 1, etc. — so labels
    flow from chunk 0 forward.

    `chunk_records` is mutated in-place: each entry's `parsed` is updated.
    """
    if len(chunk_records) <= 1:
        return

    # Parse each chunk once
    for rec in chunk_records:
        rec["parsed"] = _parse_diarized_lines(rec["text"])

    for k in range(1, len(chunk_records)):
        prev = chunk_records[k - 1]
        cur = chunk_records[k]
        overlap_start = cur["offset"]
        overlap_end = prev["end_abs"]  # absolute time at which previous chunk ended
        if overlap_end <= overlap_start:
            continue  # no overlap window (shouldn't happen if CHUNK_OVERLAP_SEC > 0)

        prev_turns = [t for t in prev["parsed"]
                      if t["is_turn"] and overlap_start <= t["start"] < overlap_end]
        cur_turns = [t for t in cur["parsed"]
                     if t["is_turn"] and overlap_start <= t["start"] < overlap_end]

        if not prev_turns or not cur_turns:
            continue  # nothing to match in the overlap

        # Vote: for each cur turn, nearest prev turn casts a vote for that speaker mapping.
        votes = {}  # cur_speaker -> {prev_speaker: count}
        for ct in cur_turns:
            closest = min(prev_turns, key=lambda pt: abs(pt["start"] - ct["start"]))
            if abs(closest["start"] - ct["start"]) > 5.0:
                continue  # too far apart, probably not the same audio moment
            votes.setdefault(ct["speaker"], {}).setdefault(closest["speaker"], 0)
            votes[ct["speaker"]][closest["speaker"]] += 1

        mapping = {}
        for cur_sp, prev_votes in votes.items():
            best_prev, _ = max(prev_votes.items(), key=lambda x: x[1])
            if cur_sp != best_prev:
                mapping[cur_sp] = best_prev

        if not mapping:
            continue

        # Detect swap collisions: if mapping says cur SPEAKER_X -> SPEAKER_Y
        # but the cur chunk also has un-mapped turns labeled SPEAKER_Y (a
        # different actual speaker), we'd merge two speakers into one. Add
        # the inverse mapping (SPEAKER_Y -> SPEAKER_X) so they swap atomically.
        cur_speakers = {t["speaker"] for t in cur["parsed"] if t["is_turn"]}
        for src, dst in list(mapping.items()):
            if dst in cur_speakers and dst not in mapping:
                mapping[dst] = src

        # Apply mapping atomically via a temp-prefix two-pass so renames
        # don't collide mid-walk (e.g. SPEAKER_0->SPEAKER_1 and SPEAKER_1->SPEAKER_0).
        TMP = "__RECONCILE_TMP_"
        for t in cur["parsed"]:
            if t["is_turn"] and t["speaker"] in mapping:
                old = t["speaker"]
                tmp = TMP + mapping[old]
                t["raw"] = t["raw"].replace(f"] {old}:", f"] {tmp}:", 1)
                t["speaker"] = tmp
        for t in cur["parsed"]:
            if t["is_turn"] and t["speaker"].startswith(TMP):
                final = t["speaker"][len(TMP):]
                t["raw"] = t["raw"].replace(f"] {t['speaker']}:", f"] {final}:", 1)
                t["speaker"] = final


def _join_chunks_dropping_overlap(chunk_records) -> str:
    """Concatenate chunk texts. For chunks 1..N, drop turn lines that fall in
    the overlap region (start < previous chunk's end_abs) — those are already
    in the previous chunk's output. Non-turn lines (preambles, blanks) in the
    overlap region are dropped too.
    """
    parts = []
    for k, rec in enumerate(chunk_records):
        if "parsed" not in rec:
            rec["parsed"] = _parse_diarized_lines(rec["text"])
        if k == 0:
            parts.append("\n".join(t["raw"] for t in rec["parsed"]))
            continue
        prev_end = chunk_records[k - 1]["end_abs"]
        kept = []
        # Find the first turn that's past the overlap; from there, keep everything
        first_past_overlap = None
        for i, t in enumerate(rec["parsed"]):
            if t["is_turn"] and t["start"] >= prev_end:
                first_past_overlap = i
                break
        if first_past_overlap is None:
            continue  # whole chunk is overlap (shouldn't happen)
        kept = [t["raw"] for t in rec["parsed"][first_past_overlap:]]
        parts.append("\n".join(kept))
    return "\n\n".join(parts)


def _detect_silence(audio_path: Path, noise_db: int = -30, min_duration: float = 0.5):
    """Run ffmpeg silencedetect; return list of (start_sec, end_sec)."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-i", str(audio_path),
                "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=180,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    intervals = []
    pending_start = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                pending_start = float(line.split("silence_start:")[1].strip())
            except (ValueError, IndexError):
                pending_start = None
        elif "silence_end:" in line and pending_start is not None:
            try:
                end_part = line.split("silence_end:")[1].split("|")[0].strip()
                intervals.append((pending_start, float(end_part)))
            except (ValueError, IndexError):
                pass
            pending_start = None
    return intervals


def _pick_split_points(total_sec: float, silences):
    """Pick split timestamps near each CHUNK_TARGET_SEC boundary, preferring
    a silence midpoint within [CHUNK_MIN_SEC, CHUNK_MAX_SEC] of the previous split.
    """
    splits = [0.0]
    current = 0.0
    while current + CHUNK_MAX_SEC < total_sec:
        target = current + CHUNK_TARGET_SEC
        best = None
        for s, e in silences:
            mid = (s + e) / 2
            if current + CHUNK_MIN_SEC <= mid <= current + CHUNK_MAX_SEC:
                if best is None or abs(mid - target) < abs(best - target):
                    best = mid
        if best is None:
            best = target  # no silence found — hard split
        splits.append(best)
        current = best
    splits.append(total_sec)
    return splits


def _chunk_audio_at_silence(audio_path: Path):
    """Split audio into chunks at silence near each target boundary.

    Returns list of (chunk_path, start_offset_sec, duration_sec).
    Caller must invoke cleanup() on the returned helper to remove temp files.
    """
    total = get_audio_duration(audio_path)
    if total is None or total <= CHUNK_THRESHOLD_SEC:
        return [(audio_path, 0.0, total or 0.0)], lambda: None

    silences = _detect_silence(audio_path)
    splits = _pick_split_points(total, silences)

    tmp_dir = Path(tempfile.mkdtemp(prefix="watch_chunk_"))
    chunks = []
    for i in range(len(splits) - 1):
        # Overlap with previous chunk so speaker-label reconciliation has
        # a shared region to match on. Chunk i transcribes audio from
        # split[i] - overlap → split[i+1]; offset stays at split[i] - overlap
        # so reassembled timestamps are still on the absolute timeline.
        nominal_start = splits[i]
        actual_start = nominal_start if i == 0 else max(0.0, nominal_start - CHUNK_OVERLAP_SEC)
        end = splits[i + 1]
        chunk_path = tmp_dir / f"chunk_{i:02d}.m4a"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(actual_start), "-to", str(end),
                    "-i", str(audio_path),
                    "-c", "copy",
                    str(chunk_path),
                ],
                check=True, timeout=180,
            )
            chunks.append((chunk_path, actual_start, end - actual_start))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"  [chunk] failed to extract chunk {i} ({actual_start:.0f}-{end:.0f}s): {e}")

    def cleanup():
        for cp, _, _ in chunks:
            try:
                cp.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            tmp_dir.rmdir()
        except Exception:
            pass

    return chunks, cleanup


# ---------------------------------------------------------------------------
# Provider: Gemini
# ---------------------------------------------------------------------------

def _gemini_transcribe_one(client, audio_path: Path) -> str:
    """Upload + transcribe a single audio file via Gemini. Returns plain text."""
    uploaded = client.files.upload(file=str(audio_path))
    while uploaded.state.name == "PROCESSING":
        time.sleep(2)
        uploaded = client.files.get(name=uploaded.name)
    if uploaded.state.name == "FAILED":
        print(f"  Gemini file processing failed: {uploaded.state}")
        return ""

    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[uploaded, TRANSCRIBE_PROMPT],
        config={
            "thinking_config": {"thinking_level": "minimal"},
            "max_output_tokens": 65536,
        },
    )
    text = (resp.text or "").strip()
    try:
        finish = resp.candidates[0].finish_reason
        if finish and str(finish).upper().endswith("MAX_TOKENS"):
            print(f"  [warn] chunk may be truncated (finish_reason={finish})")
    except (AttributeError, IndexError, TypeError):
        pass
    try:
        client.files.delete(name=uploaded.name)
    except Exception:
        pass
    return text


def _gemini_transcribe(client, audio_path: Path) -> str:
    """Transcribe via Gemini, chunking long audio at silence boundaries.

    Chunks run in parallel (up to CHUNK_PARALLELISM concurrent API calls).
    Order is preserved by indexing — output is concatenated in chunk order.
    """
    duration = get_audio_duration(audio_path)
    chunks, cleanup = _chunk_audio_at_silence(audio_path)
    try:
        if len(chunks) == 1:
            print(f"  Transcribing with {GEMINI_MODEL} (single chunk, {duration:.0f}s)...")
            return _gemini_transcribe_one(client, chunks[0][0])

        n = len(chunks)
        workers = min(CHUNK_PARALLELISM, n)
        print(f"  Transcribing with {GEMINI_MODEL} in {n} chunks (silence-aware, {workers} parallel, {CHUNK_OVERLAP_SEC}s overlap)...")

        def run(idx_chunk):
            i, (chunk_path, offset, chunk_dur) = idx_chunk
            print(f"    chunk {i+1}/{n} START: {_fmt_time(offset)} → {_fmt_time(offset + chunk_dur)} ({chunk_dur:.0f}s)")
            t0 = time.time()
            text = _gemini_transcribe_one(client, chunk_path)
            print(f"    chunk {i+1}/{n} DONE  ({time.time()-t0:.0f}s, {len(text)} chars)")
            # Filter out hallucinated out-of-bounds timestamps, then offset.
            return i, _offset_and_filter_timestamps(text, offset, chunk_dur)

        results = [None] * n
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, text in pool.map(run, enumerate(chunks)):
                results[i] = text

        # Build chunk_records preserving offset / end_abs, then reconcile
        # speaker labels across chunks via the overlap region.
        chunk_records = [
            {
                "offset": chunks[i][1],
                "end_abs": chunks[i][1] + chunks[i][2],
                "text": results[i] or "",
            }
            for i in range(n)
        ]
        _reconcile_speakers_across_chunks(chunk_records)
        return _join_chunks_dropping_overlap(chunk_records)
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# Provider: OpenAI gpt-4o-transcribe-diarize
# ---------------------------------------------------------------------------

def _ensure_openai():
    try:
        from openai import OpenAI
        return OpenAI
    except ImportError:
        pass
    venv_dir = SCRIPT_DIR / "venv"
    pip = venv_dir / "bin" / "pip"
    if pip.exists():
        subprocess.run([str(pip), "install", "-q", "openai"], check=True)
    try:
        from openai import OpenAI
        return OpenAI
    except ImportError:
        print("Failed to install openai. Run: pip install openai")
        sys.exit(1)


def _openai_transcribe(audio_path: Path) -> str:
    """Transcribe via OpenAI gpt-4o-transcribe-diarize.

    Always chunks to stay under OpenAI's 25MB / ~25min per-request limit;
    converts the diarized_json segments into our standard
    `[HH:MM:SS - HH:MM:SS] SPEAKER_X: content` format.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        for fallback in [
            Path.home() / ".claude/plugins/transcribe/.env",
            Path.home() / ".claude/plugins/nanobanana/.env",
        ]:
            load_dotenv_simple(fallback)
        api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set")
        return ""

    OpenAI = _ensure_openai()
    client = OpenAI(api_key=api_key)

    # OpenAI per-request limits are tight (25MB / 1500s), so always chunk long audio.
    # Chunks run in parallel up to CHUNK_PARALLELISM workers.
    chunks, cleanup = _chunk_audio_at_silence(audio_path)
    try:
        n = len(chunks)
        if n == 1:
            print(f"  Transcribing with gpt-4o-transcribe-diarize (single chunk)...")
        else:
            workers = min(CHUNK_PARALLELISM, n)
            print(f"  Transcribing with gpt-4o-transcribe-diarize in {n} chunks ({workers} parallel)...")

        def run(idx_chunk):
            i, (chunk_path, offset, chunk_dur) = idx_chunk
            print(f"    chunk {i+1}/{n} START: {_fmt_time(offset)} → {_fmt_time(offset + chunk_dur)} ({chunk_dur:.0f}s)")
            t0 = time.time()
            with open(chunk_path, "rb") as f:
                resp = client.audio.transcriptions.create(
                    model="gpt-4o-transcribe-diarize",
                    file=f,
                    response_format="diarized_json",
                    chunking_strategy="auto",
                )
            lines = []
            for seg in getattr(resp, "segments", []) or []:
                start = float(getattr(seg, "start", 0.0)) + offset
                end = float(getattr(seg, "end", 0.0)) + offset
                speaker = getattr(seg, "speaker", None) or "SPEAKER"
                text = (getattr(seg, "text", "") or "").strip()
                if text:
                    lines.append(f"[{_fmt_time(start)} - {_fmt_time(end)}] {speaker}: {text}")
            print(f"    chunk {i+1}/{n} DONE  ({time.time()-t0:.0f}s, {len(lines)} segments)")
            return i, "\n".join(lines)

        results = [None] * n
        workers = min(CHUNK_PARALLELISM, n)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, text in pool.map(run, enumerate(chunks)):
                results[i] = text

        if n == 1:
            return results[0] or ""

        # Reconcile speaker labels across chunks via the overlap region.
        chunk_records = [
            {
                "offset": chunks[i][1],
                "end_abs": chunks[i][1] + chunks[i][2],
                "text": results[i] or "",
            }
            for i in range(n)
        ]
        _reconcile_speakers_across_chunks(chunk_records)
        return _join_chunks_dropping_overlap(chunk_records)
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

TRANSCRIBE_PROMPT = """请将这段音频转录成文字，并标注说话人。

要求：
1. 识别不同的说话人，用 SPEAKER_0、SPEAKER_1 等标签区分
2. 每段对话用时间戳标注，格式：[HH:MM:SS - HH:MM:SS] SPEAKER_X: 内容
3. **重要**：保持自然的句子边界，不要在句子中间断开
4. 使用正确的标点符号（句号、逗号、问号等）
5. 每个说话人的一段完整发言作为一个段落，不要把一句话拆成多行
6. 逐字转录，保留口语特点但使用正确标点
7. **保持源语言**：中文部分用中文，英文部分用英文，不要翻译

示例输出格式：
[00:00:20 - 00:00:45] SPEAKER_0: 大家好，欢迎收听本期节目。
[00:00:45 - 00:01:05] SPEAKER_1: Hello, my name is Zhang San.
"""

SUMMARIZE_PROMPT = """You are analyzing a diarized transcript. Produce a JSON object with these fields:

- summary_en: 2-3 sentence summary in English
- summary_zh: 2-3 sentence summary in Chinese (中文摘要)
- key_points_en: 3-7 key points in English
- key_points_zh: 3-7 key points in Chinese (中文要点)
- action_items: todos or follow-ups mentioned (bilingual list; empty list if none)

Return ONLY valid JSON, no markdown fences:
{
  "summary_en": "...",
  "summary_zh": "...",
  "key_points_en": ["...", "..."],
  "key_points_zh": ["...", "..."],
  "action_items": ["...", "..."]
}

Transcript:
"""


def transcribe_and_summarize(audio_path: Path) -> dict:
    """Two-stage pipeline:
      1. Transcribe (provider = STT_PROVIDER, default "gemini")
         - Gemini: chunk at silence boundaries when audio > CHUNK_THRESHOLD_SEC
                   (Gemini 3 Flash loses coherence on long audio in a single call:
                    loops, fabricates timestamps).
         - OpenAI: always chunk to respect 25MB / 1500s per-request limits.
      2. Summarize the transcript text via Gemini → JSON summary/key_points/action_items.
    """
    genai = ensure_genai()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set")
        sys.exit(1)
    client = genai.Client(api_key=api_key)

    # Stage 1: transcribe
    if STT_PROVIDER == "openai":
        transcript = _openai_transcribe(audio_path)
    else:
        transcript = _gemini_transcribe(client, audio_path)

    if not transcript:
        print("  Transcription returned empty text")
        return {"transcript": ""}

    # Stage 2: summarize (text input → JSON output, no loop risk)
    print(f"  Summarizing with {GEMINI_MODEL}...")
    summary_resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[SUMMARIZE_PROMPT + transcript],
        config={"response_mime_type": "application/json"},
    )
    result = _parse_gemini_json(summary_resp.text or "")
    result["transcript"] = transcript
    return result


def _parse_gemini_json(text: str) -> dict:
    """Parse Gemini's JSON response, with repair for known malformations.

    Gemini 3 Flash Preview occasionally emits invalid JSON on long outputs:
      - stray closing quote before `}` (e.g. `..."\n"\n}`)
      - duplicated closing brace (`}\n}`)
      - control characters inside transcript strings
    """
    import re

    def _try_parse(s: str):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, AttributeError):
            return None

    # 1. Direct parse
    parsed = _try_parse(text)
    if parsed is not None:
        return parsed

    # 2. Strip markdown fences if present
    stripped = re.sub(r"^```(?:json)?\s*", "", text.strip())
    stripped = re.sub(r"\s*```$", "", stripped)
    parsed = _try_parse(stripped)
    if parsed is not None:
        return parsed

    # 3. Clean control chars (linebreaks inside strings, etc.)
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", stripped)
    parsed = _try_parse(cleaned)
    if parsed is not None:
        return parsed

    # 4. Repair: trim to first `{` and balanced closing `}`
    repaired = stripped
    start = repaired.find("{")
    if start > 0:
        repaired = repaired[start:]
    # Drop orphan `"` between a string value and the closing brace
    repaired = re.sub(r'"(\s*)"(\s*})', r'"\1\2', repaired)
    # Drop duplicated closing braces at the very end
    repaired = re.sub(r"}\s*}+\s*$", "}", repaired)
    # Trailing commas
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    # Truncate after the LAST balanced `}` so trailing garbage doesn't break parsing
    depth = 0
    last_balanced = -1
    in_string = False
    escape_next = False
    for i, ch in enumerate(repaired):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                last_balanced = i
    if last_balanced > 0:
        repaired = repaired[: last_balanced + 1]
    parsed = _try_parse(repaired)
    if parsed is not None:
        return parsed

    # 5. Last resort: surface raw text so the caller at least gets something
    print("[parse] WARN: JSON repair failed; falling back to raw text in transcript field")
    return {"summary": "", "key_points": [], "action_items": [], "transcript": text}


def format_note(audio_path: Path, result: dict) -> dict:
    """Format Gemini result into a structured note."""
    name = audio_path.stem
    try:
        date_part = name[:15]  # "YYYYMMDD HHMMSS"
        dt = datetime.strptime(date_part, "%Y%m%d %H%M%S")
        title = f"Voice Note {dt.strftime('%Y-%m-%d %H:%M')}"
        timestamp = dt.isoformat()
    except (ValueError, IndexError):
        title = f"Voice Note {name}"
        timestamp = datetime.now().isoformat()

    summary_en = result.get("summary_en", result.get("summary", ""))
    summary_zh = result.get("summary_zh", "")
    key_points_en = result.get("key_points_en", result.get("key_points", []))
    key_points_zh = result.get("key_points_zh", [])
    action_items = result.get("action_items", [])
    transcript = result.get("transcript", "")

    # Build markdown
    lines = [
        f"# {title}",
        "",
        f"**Recorded:** {timestamp}",
        f"**Source:** Apple Watch Voice Memo",
        f"**File:** `{audio_path.name}`",
        "",
    ]

    if summary_en or summary_zh:
        lines += ["## Summary"]
        if summary_en:
            lines += ["", summary_en]
        if summary_zh:
            lines += ["", summary_zh]
        lines.append("")

    if key_points_en or key_points_zh:
        lines += ["## Key Points"]
        if key_points_en:
            lines.append("")
            for point in key_points_en:
                lines.append(f"- {point}")
        if key_points_zh:
            lines.append("")
            for point in key_points_zh:
                lines.append(f"- {point}")
        lines.append("")

    if action_items:
        lines += ["## Action Items", ""]
        for item in action_items:
            lines.append(f"- [ ] {item}")
        lines.append("")

    lines += [
        "---",
        "",
        "## Transcript",
        "",
        transcript.strip(),
        "",
    ]

    return {
        "title": title,
        "transcript": transcript.strip(),
        "summary": f"{summary_en}\n\n{summary_zh}".strip(),
        "todos": action_items,
        "audio_path": str(audio_path),
        "timestamp": timestamp,
        "markdown": "\n".join(lines),
    }


def process_recording(audio_path: Path, dry_run: bool = False) -> bool:
    """Process a single recording: transcribe + summarize → deliver."""
    print(f"\n{'='*60}")
    print(f"Processing: {audio_path.name}")
    print(f"Size: {audio_path.stat().st_size / 1024:.1f} KB")
    print(f"{'='*60}")

    # Skip very small files (likely incomplete syncs)
    if audio_path.stat().st_size < 1000:
        print("Skipping: file too small (likely incomplete sync)")
        return False

    # Skip recordings shorter than MIN_DURATION_SECONDS (default 60s)
    duration = get_audio_duration(audio_path)
    if duration is not None and duration < MIN_DURATION_SECONDS:
        print(f"Skipping (too short): {duration:.1f}s < {MIN_DURATION_SECONDS}s threshold")
        return True  # mark processed so we don't retry

    if dry_run:
        from deliveries import get_active_deliveries
        targets = ", ".join(get_active_deliveries())
        print(f"  [DRY-RUN] would transcribe with {GEMINI_MODEL}")
        print(f"  [DRY-RUN] would deliver to: {targets}")
        return False

    # Single Gemini call for everything
    result = transcribe_and_summarize(audio_path)
    if not result or not result.get("transcript"):
        print("Transcription returned empty result")
        return False

    transcript = result.get("transcript", "")
    summary_en = result.get("summary_en", result.get("summary", ""))
    summary_zh = result.get("summary_zh", "")
    print(f"  Transcript: {len(transcript)} chars")
    if summary_en:
        print(f"  Summary (EN): {summary_en[:100]}...")
    if summary_zh:
        print(f"  Summary (ZH): {summary_zh[:100]}...")

    # Format note
    note = format_note(audio_path, result)

    # Deliver
    from deliveries import deliver_all
    results = deliver_all(note)

    for target, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  [{target}] {status}")

    return any(results.values())


def run_doctor() -> int:
    """Validate setup. Returns 0 if all critical checks pass, 1 otherwise."""
    from deliveries import BUILTIN_DELIVERIES, get_active_deliveries

    failed: list[str] = []
    warned: list[str] = []
    passed = 0

    def check(label: str, ok: bool, *, fatal: bool = True, note: str = ""):
        nonlocal passed
        suffix = f" — {note}" if note else ""
        if ok:
            print(f"  [OK]   {label}{suffix}")
            passed += 1
        elif fatal:
            print(f"  [FAIL] {label}{suffix}")
            failed.append(label)
        else:
            print(f"  [WARN] {label}{suffix}")
            warned.append(label)

    print("== Configuration ==")
    check("GEMINI_API_KEY set", bool(os.environ.get("GEMINI_API_KEY")))
    check("GEMINI_MODEL", True, note=GEMINI_MODEL)
    targets = get_active_deliveries()
    check("DELIVERY_TARGETS", bool(targets), note=", ".join(targets) or "(empty)")
    unknown = [t for t in targets if t not in BUILTIN_DELIVERIES]
    if unknown:
        check(f"Unknown delivery targets: {', '.join(unknown)}", False, fatal=False,
              note="custom modules must exist under deliveries/")

    print("\n== Filesystem ==")
    if VOICE_MEMOS_DIR.exists():
        try:
            m4a_count = sum(1 for f in VOICE_MEMOS_DIR.iterdir() if f.suffix == ".m4a")
            check(f"Voice Memos directory readable", True,
                  note=f"{m4a_count} .m4a files at {VOICE_MEMOS_DIR}")
        except PermissionError:
            check("Voice Memos directory readable", False,
                  note="permission denied — grant Full Disk Access to your terminal")
    else:
        check("Voice Memos directory exists", False, note=str(VOICE_MEMOS_DIR))

    state_writable = True
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        probe = STATE_DIR / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        state_writable = False
        check("State directory writable", False, note=f"{STATE_DIR}: {e}")
    if state_writable:
        check("State directory writable", True, note=str(STATE_DIR))

    print("\n== Deliveries ==")
    for target in targets:
        _check_delivery(target, check)

    print("\n== LaunchAgent ==")
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=5
        ).stdout
        loaded = "com.watch-transcriber" in out
        check("com.watch-transcriber loaded", loaded, fatal=False,
              note="run setup.sh to install" if not loaded else "")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        check("launchctl available", False, fatal=False, note="non-macOS platform?")

    print(f"\n== Summary ==\n  {passed} passed, {len(failed)} failed, {len(warned)} warning(s)")
    return 0 if not failed else 1


def _check_delivery(target: str, check) -> None:
    if target == "file":
        out = Path(os.environ.get("OUTPUT_DIR", "~/Documents/VoiceNotes")).expanduser()
        ok = True
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError:
            ok = False
        check(f"file: OUTPUT_DIR writable", ok, note=str(out))
    elif target == "local_archive":
        out = Path(os.environ.get("LOCAL_ARCHIVE_DIR", "./data")).expanduser().resolve()
        ok = True
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError:
            ok = False
        check(f"local_archive: LOCAL_ARCHIVE_DIR writable", ok, note=str(out))
    elif target == "apple_notes":
        check("apple_notes: osascript available", bool(shutil.which("osascript")))
    elif target == "feishu":
        check("feishu: lark-cli in PATH", bool(shutil.which("lark-cli")), fatal=False)
        has_dest = bool(os.environ.get("FEISHU_FOLDER_TOKEN") or os.environ.get("FEISHU_WIKI_SPACE"))
        check("feishu: FEISHU_FOLDER_TOKEN or FEISHU_WIKI_SPACE set", has_dest, fatal=False)
    elif target == "feishu_notify":
        check("feishu_notify: FEISHU_NOTIFY_USER_ID set",
              bool(os.environ.get("FEISHU_NOTIFY_USER_ID")), fatal=False)
        check("feishu_notify: lark-cli in PATH", bool(shutil.which("lark-cli")), fatal=False)
    elif target == "obsidian_git":
        check("obsidian_git: OBSIDIAN_REPO set", bool(os.environ.get("OBSIDIAN_REPO")), fatal=False)
        check("obsidian_git: GITHUB_TOKEN set", bool(os.environ.get("GITHUB_TOKEN")), fatal=False)
    elif target == "agent":
        check("agent: AGENT_DELIVERY_PROMPT set",
              bool(os.environ.get("AGENT_DELIVERY_PROMPT")), fatal=False)
        check("agent: claude CLI in PATH", bool(shutil.which("claude")), fatal=False)
    else:
        check(f"{target}: custom delivery", True, note="not validated by doctor")


def main():
    parser = argparse.ArgumentParser(
        description="watch-transcriber — Apple Voice Memos → Gemini STT → pluggable delivery"
    )
    parser.add_argument("--doctor", action="store_true",
                        help="Run setup checks and exit")
    parser.add_argument("--reprocess", metavar="YYYY-MM-DD",
                        help="Reprocess all recordings from this date (ignores state)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be processed; skip Gemini and delivery")
    args = parser.parse_args()

    if args.doctor:
        sys.exit(run_doctor())

    mode = "reprocess" if args.reprocess else "scan"
    dry = " [dry-run]" if args.dry_run else ""
    print(f"watch-transcriber {mode}{dry} at {datetime.now().isoformat()}")
    print(f"Monitoring: {VOICE_MEMOS_DIR}")

    processed = load_state()

    if args.reprocess:
        try:
            recordings = find_recordings_by_date(args.reprocess)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(2)
        print(f"Reprocess mode: found {len(recordings)} recording(s) for {args.reprocess}")
    else:
        recordings = find_new_recordings(processed)

    if not recordings:
        print("No recordings to process.")
        return

    if not args.reprocess:
        print(f"Found {len(recordings)} new recording(s)")

    for audio_path in recordings:
        try:
            ok = process_recording(audio_path, dry_run=args.dry_run)
            if ok and not args.dry_run:
                processed.add(audio_path.name)
                save_state(processed)
        except Exception as e:
            print(f"Error processing {audio_path.name}: {e}")

    print(f"\nDone. {len(recordings)} recording(s) processed.")


if __name__ == "__main__":
    main()
