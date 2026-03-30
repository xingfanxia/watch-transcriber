#!/usr/bin/env python3
"""watch-transcriber: Voice Memos → Gemini STT → pluggable delivery.

Monitors Apple Voice Memos recordings directory for new .m4a files,
transcribes them using Gemini 3 Flash with speaker diarization,
and delivers structured notes to configurable destinations.
"""

import json
import os
import subprocess
import sys
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

# Voice Memos recordings directory
VOICE_MEMOS_DIR = Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"

# State file to track processed recordings
STATE_DIR = SCRIPT_DIR / "state"
STATE_FILE = STATE_DIR / "processed.json"

# Transcribe plugin path
TRANSCRIBE_PYTHON = Path.home() / ".claude/plugins/transcribe/venv/bin/python"
TRANSCRIBE_SCRIPT = Path.home() / ".claude/plugins/transcribe/transcribe_any.py"


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


def transcribe(audio_path: Path) -> str:
    """Transcribe audio using Gemini 3 Flash via the transcribe plugin."""
    if not TRANSCRIBE_PYTHON.exists() or not TRANSCRIBE_SCRIPT.exists():
        print("Transcribe plugin not found. Install it first.")
        print(f"  Expected: {TRANSCRIBE_SCRIPT}")
        sys.exit(1)

    # Use a temp file for output to avoid path issues
    out_path = Path("/tmp") / f"{audio_path.stem}_transcript.txt"

    result = subprocess.run(
        [
            str(TRANSCRIBE_PYTHON),
            str(TRANSCRIBE_SCRIPT),
            str(audio_path),
            "--diarize",
            "--out_txt", str(out_path),
        ],
        capture_output=True, text=True, timeout=600,
    )

    if result.returncode != 0:
        print(f"Transcription failed: {result.stderr[:500]}")
        return ""

    if out_path.exists():
        transcript = out_path.read_text(encoding="utf-8")
        out_path.unlink()
        return transcript

    return result.stdout


def format_note(audio_path: Path, transcript: str) -> dict:
    """Format transcript into a structured note."""
    # Parse timestamp from filename (format: YYYYMMDD HHMMSS-UUID.m4a)
    name = audio_path.stem
    try:
        date_part = name[:15]  # "YYYYMMDD HHMMSS"
        dt = datetime.strptime(date_part, "%Y%m%d %H%M%S")
        title = f"Voice Note {dt.strftime('%Y-%m-%d %H:%M')}"
        timestamp = dt.isoformat()
    except (ValueError, IndexError):
        title = f"Voice Note {name}"
        timestamp = datetime.now().isoformat()

    # Build markdown
    lines = [
        f"# {title}",
        "",
        f"**Recorded:** {timestamp}",
        f"**Source:** Apple Watch Voice Memo",
        f"**File:** `{audio_path.name}`",
        "",
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
        "summary": "",
        "todos": [],
        "audio_path": str(audio_path),
        "timestamp": timestamp,
        "markdown": "\n".join(lines),
    }


def process_recording(audio_path: Path) -> bool:
    """Process a single recording: transcribe → deliver."""
    print(f"\n{'='*60}")
    print(f"Processing: {audio_path.name}")
    print(f"Size: {audio_path.stat().st_size / 1024:.1f} KB")
    print(f"{'='*60}")

    # Skip very small files (likely incomplete syncs)
    if audio_path.stat().st_size < 1000:
        print("Skipping: file too small (likely incomplete sync)")
        return False

    # Transcribe
    print("Transcribing with Gemini 3 Flash (diarization enabled)...")
    transcript = transcribe(audio_path)
    if not transcript:
        print("Transcription returned empty result")
        return False

    print(f"Transcript: {len(transcript)} chars")

    # Format note
    note = format_note(audio_path, transcript)

    # Deliver
    from deliveries import deliver_all
    results = deliver_all(note)

    for target, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  [{target}] {status}")

    return any(results.values())


def main():
    print(f"watch-transcriber starting at {datetime.now().isoformat()}")
    print(f"Monitoring: {VOICE_MEMOS_DIR}")

    processed = load_state()
    new_recordings = find_new_recordings(processed)

    if not new_recordings:
        print("No new recordings found.")
        return

    print(f"Found {len(new_recordings)} new recording(s)")

    for audio_path in new_recordings:
        try:
            if process_recording(audio_path):
                processed.add(audio_path.name)
                save_state(processed)
        except Exception as e:
            print(f"Error processing {audio_path.name}: {e}")

    print(f"\nDone. {len(new_recordings)} recording(s) processed.")


if __name__ == "__main__":
    main()
