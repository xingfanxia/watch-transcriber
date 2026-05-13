#!/usr/bin/env python3
"""watch-transcriber: Voice Memos → Gemini STT → pluggable delivery.

Monitors Apple Voice Memos recordings directory for new .m4a files,
transcribes and summarizes them using Gemini 3 Flash in a single
multimodal call, then delivers structured notes to configurable targets.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
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


def transcribe_and_summarize(audio_path: Path) -> dict:
    """Single Gemini call: audio → summary + key points + action items + transcript."""
    genai = ensure_genai()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    # Upload audio to Gemini Files API
    print("  Uploading to Gemini Files API...")
    uploaded = client.files.upload(file=str(audio_path))
    while uploaded.state.name == "PROCESSING":
        time.sleep(2)
        uploaded = client.files.get(name=uploaded.name)
    if uploaded.state.name == "FAILED":
        print(f"  File processing failed: {uploaded.state}")
        return {}

    # Single multimodal prompt: transcribe + summarize (bilingual)
    prompt = """Listen to this audio and provide the following:

1. **English analysis:**
   - summary_en: 2-3 sentence summary in English
   - key_points_en: 3-7 key points in English

2. **Chinese analysis:**
   - summary_zh: 2-3 sentence summary in Chinese (中文摘要)
   - key_points_zh: 3-7 key points in Chinese (中文要点)

3. **Action items** (bilingual if the audio is mixed):
   - action_items: any todos or action items mentioned (empty list if none)

4. **Full transcript** with speaker diarization:
   - Identify speakers as SPEAKER_0, SPEAKER_1, etc.
   - Use timestamps: [HH:MM:SS - HH:MM:SS] SPEAKER_X: content
   - Keep natural sentence boundaries, proper punctuation
   - IMPORTANT: transcribe verbatim in the ORIGINAL language(s) spoken. Do NOT translate the transcript.

Respond in this EXACT JSON format:
{
  "summary_en": "English summary here",
  "summary_zh": "中文摘要",
  "key_points_en": ["point 1", "point 2"],
  "key_points_zh": ["要点一", "要点二"],
  "action_items": ["todo 1", "todo 2"],
  "transcript": "full transcript with timestamps and speakers in original language"
}"""

    print(f"  Transcribing + summarizing with {GEMINI_MODEL}...")
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[uploaded, prompt],
        config={
            "temperature": 0.2,
            "response_mime_type": "application/json",
        },
    )

    # Clean up uploaded file
    try:
        client.files.delete(name=uploaded.name)
    except Exception:
        pass

    try:
        return json.loads(response.text)
    except (json.JSONDecodeError, AttributeError):
        # Control characters in transcript can break JSON — try cleaning
        import re
        try:
            cleaned = re.sub(r'[\x00-\x1f\x7f]', ' ', response.text or "")
            return json.loads(cleaned)
        except (json.JSONDecodeError, AttributeError):
            # Last resort: return raw text as transcript
            return {"summary": "", "key_points": [], "action_items": [], "transcript": response.text or ""}


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
