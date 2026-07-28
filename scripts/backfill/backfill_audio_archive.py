"""Backfill the audio_archive delivery for existing archive entries.

For each per-recording note under data/<YYYY-MM-DD>/HHMMSS-<slug>.md, finds the
matching Voice Memos original (`YYYYMMDD HHMMSS-*.m4a`) and runs the normal
audio_archive delivery, so backfill and live pipeline share one code path.
Idempotent — reruns skip up-to-date copies. Originals whose recording was
deleted from Voice Memos are reported and skipped.

Usage:
    venv/bin/python3 scripts/backfill/backfill_audio_archive.py [--since YYYY] [--dry-run]
"""

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import transcribe  # noqa: E402,F401  (loads .env at import — LOCAL_ARCHIVE_DIR etc.)
from deliveries import archive_root, audio_archive  # noqa: E402

RECORDINGS_DIR = (
    Path.home()
    / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
)

# The audio_archive delivery went live 2026-07-28; earlier years are legacy
# notes whose originals mostly predate Voice Memos and were only backfilled
# on explicit request ("仅backfill今年的").
DEFAULT_SINCE_YEAR = 2026


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", type=int, default=DEFAULT_SINCE_YEAR,
                    help="only backfill years >= this")
    ap.add_argument("--dry-run", action="store_true", help="report matches without copying")
    args = ap.parse_args()

    # LOCAL_ARCHIVE_DIR defaults to ./data, relative to the repo root.
    os.chdir(REPO_ROOT)

    copied = missing = failed = up_to_date = 0
    for day_dir in sorted(archive_root().iterdir()):
        if not day_dir.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_dir.name):
            continue
        if int(day_dir.name[:4]) < args.since:
            continue
        compact_date = day_dir.name.replace("-", "")
        for md in sorted(day_dir.glob("*.md")):
            m = re.fullmatch(r"(\d{6})-(.+)\.md", md.name)
            if not m:
                continue  # daily.md
            hhmmss, title_slug = m.groups()
            # No hyphen before the wildcard: Voice Memos writes both
            # "YYYYMMDD HHMMSS-<HEXID>.m4a" and bare "YYYYMMDD HHMMSS.m4a".
            # (.qta orphans from interrupted recordings are intentionally
            # excluded — they never produced a note.)
            sources = sorted(RECORDINGS_DIR.glob(f"{compact_date} {hhmmss}*.m4a"))
            if not sources:
                print(f"MISSING original: {day_dir.name} {hhmmss} ({title_slug})")
                missing += 1
                continue
            if len(sources) > 1:
                print(f"WARN multiple originals for {day_dir.name} {hhmmss}, using {sources[0].name}")
            note = {
                "title": title_slug,
                "timestamp": f"{day_dir.name}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}",
                "audio_path": str(sources[0]),
            }
            if args.dry_run:
                dest = day_dir / f"{hhmmss}-{title_slug}{sources[0].suffix}"
                if dest.exists() and dest.stat().st_size == sources[0].stat().st_size:
                    print(f"up-to-date: {day_dir.name}/{dest.name}")
                    up_to_date += 1
                else:
                    print(f"would copy {sources[0].name} -> {day_dir.name}/{dest.name}")
                    copied += 1
                continue
            try:
                ok = audio_archive.deliver(note)
            except Exception as e:
                print(f"FAILED {day_dir.name} {hhmmss}: {e}")
                ok = False
            copied += 1 if ok else 0
            failed += 0 if ok else 1

    print(f"\n{'would copy' if args.dry_run else 'delivered'}: {copied}, "
          f"up-to-date: {up_to_date}, missing originals: {missing}, failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
