"""Backfill / catch-up sync of archive audio to the private R2 bucket.

Walks manifest.json for entries with an audio copy, skips files already
recorded in the local upload ledger (state/r2_uploaded.json — R2 has no
cheap server-side listing via wrangler), uploads the rest through the same
code path the live r2_backup delivery uses. Rerun-safe; failures are
reported and retried on the next run.

Usage:
    venv/bin/python3 scripts/backfill/backfill_r2_audio.py [--dry-run]
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import transcribe  # noqa: E402,F401  (loads .env)
from deliveries import archive_root, manifest, r2_backup  # noqa: E402

LEDGER = REPO_ROOT / "state" / "r2_uploaded.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    os.chdir(REPO_ROOT)
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}

    todo = []
    for key, entry in sorted(manifest.load().items()):
        rel = entry.get("audio")
        if not rel or not (archive_root() / rel).exists():
            continue
        size = (archive_root() / rel).stat().st_size
        if ledger.get(rel) == size:
            continue
        todo.append((rel, size))

    print(f"{len(todo)} file(s) to upload ({sum(s for _, s in todo) / 1e6:.0f} MB)")
    if args.dry_run:
        for rel, size in todo:
            print(f"  would upload {rel} ({size / 1e6:.1f} MB)")
        return 0

    uploaded = failed = 0
    for i, (rel, size) in enumerate(todo, 1):
        if r2_backup.upload(rel):
            ledger[rel] = size
            uploaded += 1
            LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1))
        else:
            failed += 1
        print(f"  [{i}/{len(todo)}] {'ok' if ledger.get(rel) == size else 'FAILED'} {rel}")

    print(f"\nuploaded: {uploaded}, failed: {failed}, already-synced: "
          f"{len(ledger) - uploaded}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
