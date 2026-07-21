#!/usr/bin/env python3
"""Backfill AI-generated titles across all archived recordings.

For each data/YYYY-MM-DD/HHMMSS-*.md (excluding daily.md):
  1. Parse the original m4a filename + transcript out of the archive file.
  2. Skip if already done per the ledger (idempotent resume).
  3. Re-run the summarize stage (same prompt as the live pipeline) → title etc.
  4. format_note() → new note dict.
  5. Apple Notes: delete this recording's prior notes (old timestamp title OR
     any earlier backfill attempt, matched by the deterministic "(<ts>)" suffix),
     then create the new note.
  6. Feishu: create a new doc. Old "Voice Note ..." docs are NOT deleted here —
     the lark-cli app currently lacks drive:drive scope; see the manifest built
     by harvest_feishu_manifest.py for later cleanup.
  7. file delivery: remove the legacy "Voice Note ....md" then write the new file.
  8. local_archive delivery LAST (rewrites the per-recording file + daily.md);
     it is the per-recording commit point.

Per-recording failures are recorded in the ledger and do not abort the run
(annotate-and-continue). Rerunning resumes from the ledger.

Usage: venv/bin/python3 scripts/backfill/backfill_ai_titles.py [--limit N] [--dry-run]
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import transcribe  # noqa: E402
from deliveries import safe_filename  # noqa: E402
from deliveries import file as file_delivery  # noqa: E402
from deliveries import local_archive  # noqa: E402
from deliveries import feishu as feishu_delivery  # noqa: E402
from deliveries import apple_notes  # noqa: E402

LEDGER = Path(__file__).parent / "backfill_ledger.jsonl"
DATA = REPO / "data"

import os  # noqa: E402

transcribe.load_dotenv_simple(REPO / ".env")


def stable_key(archive_path: str) -> str:
    """Immutable per-recording key: 'YYYY-MM-DD/HHMMSS'. The archive FILENAME
    changes when the backfill rewrites it with the new title slug, so the path
    itself must never be used as the resume key."""
    p = Path(archive_path)
    return f"{p.parent.name}/{p.name[:6]}"


def load_ledger() -> dict:
    done = {}
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done[stable_key(rec["archive"])] = rec
    return done


def append_ledger(rec: dict):
    with LEDGER.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def parse_archive(md_path: Path):
    """Extract original m4a filename, old title, and transcript."""
    text = md_path.read_text(encoding="utf-8")
    old_title = text.splitlines()[0].lstrip("# ").strip()
    m_file = re.search(r"\*\*File:\*\* `([^`]+)`", text)
    m_ts = re.search(r"```\n(.*?)\n```", text, re.S)
    if m_ts:
        transcript = m_ts.group(1).strip()
    else:
        # Pre-fencing archive format: bare transcript after the header.
        m_hdr = re.search(r"^## Transcript\s*\n(.*)\Z", text, re.S | re.M)
        transcript = m_hdr.group(1).strip() if m_hdr else ""
    if not m_file or not transcript:
        raise ValueError(f"unparseable archive file (File line: {bool(m_file)}, transcript: {bool(transcript)})")
    return m_file.group(1), old_title, transcript


def summarize(client, transcript: str, attempts: int = 3) -> dict:
    """Summarize with retry: at temp=1.0 Gemini occasionally emits unparseable
    JSON or omits the title; a fresh sample nearly always fixes it."""
    for i in range(attempts):
        resp = client.models.generate_content(
            model=transcribe.GEMINI_MODEL,
            contents=[transcribe.SUMMARIZE_PROMPT + transcript],
            config={"response_mime_type": "application/json"},
        )
        result = transcribe._parse_gemini_json(resp.text or "")
        if transcribe._clean_ai_title(result.get("title", "")):
            result["transcript"] = transcript
            return result
        print(f"  [summarize] unusable output (attempt {i + 1}/{attempts})")
    raise ValueError(f"summarize returned no usable title after {attempts} attempts")


def apple_notes_recreate(note: dict, old_title: str, ts_label: str) -> bool:
    """Delete this recording's prior notes (old title or a previous backfill's
    AI title, keyed by the deterministic timestamp suffix), then create anew."""
    folder = os.environ.get("APPLE_NOTES_FOLDER", "Voice Transcripts")

    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'''
    tell application "Notes"
        set n to 0
        repeat with f in folders of default account
            if name of f is "{esc(folder)}" then
                delete (every note of f whose name is "{esc(old_title)}")
                delete (every note of f whose name ends with "({esc(ts_label)})")
                exit repeat
            end if
        end repeat
    end tell
    '''
    r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print(f"  [apple_notes] delete error: {r.stderr.strip()[:200]}")
        return False
    return apple_notes.deliver(note)


def file_recreate(note: dict, old_title: str) -> bool:
    output_dir = Path(os.environ.get("OUTPUT_DIR", "~/Documents/VoiceNotes")).expanduser()
    legacy = output_dir / f"{safe_filename(old_title)}.md"
    if legacy.exists():
        legacy.unlink()
    return file_delivery.deliver(note)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="process at most N recordings")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    genai = transcribe.ensure_genai()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    done = load_ledger()
    archives = sorted(
        p for p in DATA.glob("*/??????-*.md") if p.name != "daily.md"
    )
    todo = [p for p in archives if stable_key(str(p)) not in done
            or done[stable_key(str(p))].get("status") != "ok"]
    print(f"{len(archives)} archived recordings, {len(todo)} to process")
    if args.limit:
        todo = todo[: args.limit]

    ok = failed = 0
    for md_path in todo:
        rel = str(md_path.relative_to(REPO))
        prior = done.get(stable_key(rel), {})
        print(f"\n--- {rel}")
        try:
            m4a_name, old_title, transcript = parse_archive(md_path)
            if args.dry_run:
                print(f"  would backfill: {old_title!r} ({len(transcript)} chars)")
                continue

            # Reuse the stored summarize result from a prior partial run so a
            # resume can't re-sample a different title than the surfaces that
            # were already written (Feishu docs can't be retitled without
            # drive:drive scope).
            if prior.get("result"):
                result = dict(prior["result"], transcript=transcript)
                print("  [summarize] reusing prior result")
            else:
                result = summarize(client, transcript)
            note = transcribe.format_note(Path(m4a_name), result)
            ts_label = note["title"].rsplit("(", 1)[-1].rstrip(")")
            if not transcribe._clean_ai_title(result.get("title", "")):
                raise ValueError("summarize returned no usable title")
            print(f"  new title: {note['title']}")

            an_ok = apple_notes_recreate(note, old_title, ts_label)
            # Resume safety: don't create a second Feishu doc if a prior
            # partial run already created one for this recording.
            if prior.get("feishu_url"):
                fs_ok, note["feishu_doc_url"] = True, prior["feishu_url"]
                print(f"  [feishu] kept from prior run: {prior['feishu_url']}")
            else:
                fs_ok = feishu_delivery.deliver(note)
                time.sleep(1)  # be polite to the API
            f_ok = file_recreate(note, old_title)
            la_ok = local_archive.deliver(note)

            status = "ok" if (an_ok and fs_ok and f_ok and la_ok) else "partial"
            append_ledger({
                "archive": rel, "status": status, "old_title": old_title,
                "new_title": note["title"], "feishu_url": note.get("feishu_doc_url", ""),
                "apple_notes": an_ok, "feishu": fs_ok, "file": f_ok, "local_archive": la_ok,
                "result": {k: result.get(k) for k in
                           ("title", "summary_en", "summary_zh", "key_points_en",
                            "key_points_zh", "action_items")},
                "at": datetime.now().isoformat(timespec="seconds"),
            })
            ok += status == "ok"
            failed += status != "ok"
        except Exception as e:  # noqa: BLE001 — annotate-and-continue per recording
            print(f"  ERROR: {e}")
            if not args.dry_run:
                append_ledger({"archive": rel, "status": "error", "error": str(e)[:300],
                               "at": datetime.now().isoformat(timespec="seconds")})
            failed += 1

    print(f"\nDone: {ok} ok, {failed} failed/partial (ledger: {LEDGER})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
