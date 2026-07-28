"""Restore the data/ archive on a fresh machine.

Fresh-clone bootstrap for the desktop app and pipeline:
  1. data/ missing → clone the PRIVATE notes repo (needs `gh auth login`
     or ssh access to github.com/xingfanxia/watch-transcriber-data).
  2. For every manifest entry whose audio copy is absent → download it from
     the private R2 bucket (needs `wrangler login`, or CLOUDFLARE_ACCOUNT_ID +
     CLOUDFLARE_API_TOKEN[_FILE] in the environment / .env).
  3. Rebuild data/index.html so the desktop app has something to show.

Idempotent — safe to rerun; already-present files are skipped and the R2
upload ledger is seeded so the next backfill_r2_audio doesn't re-upload.

Usage:
    python3 scripts/ops/restore_archive.py [--notes-only] [--dry-run]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

DATA_REPO = "https://github.com/xingfanxia/watch-transcriber-data.git"
LEDGER = REPO_ROOT / "state" / "r2_uploaded.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--notes-only", action="store_true", help="skip audio download")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    os.chdir(REPO_ROOT)
    data = REPO_ROOT / "data"

    if not (data / ".git").exists():
        if data.exists() and any(data.iterdir()):
            print(f"{data} exists but is not the notes repo — refusing to touch it")
            return 1
        print(f"cloning notes repo -> {data}")
        if not args.dry_run:
            subprocess.run(["git", "clone", DATA_REPO, str(data)], check=True)
    else:
        print("notes repo present — pulling latest")
        if not args.dry_run:
            subprocess.run(["git", "-C", str(data), "pull", "--ff-only"], check=True)

    # Imports come after the clone: deliveries reads the archive lazily, and
    # transcribe's .env load is wanted for CLOUDFLARE_* / R2_BUCKET config.
    import transcribe  # noqa: F401
    from deliveries import manifest, r2_backup, viewer

    downloaded = failed = present = 0
    if not args.notes_only:
        wrangler = r2_backup.wrangler_bin()
        env = r2_backup.wrangler_env()
        ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
        for key, entry in sorted(manifest.load().items()):
            rel = entry.get("audio")
            if not rel:
                continue
            dest = data / rel
            if dest.exists():
                present += 1
                continue
            if args.dry_run:
                print(f"would download {rel}")
                downloaded += 1
                continue
            if not wrangler:
                print("wrangler not found — cannot download audio")
                return 1
            r = subprocess.run(
                [wrangler, "r2", "object", "get", f"{r2_backup.bucket()}/{rel}",
                 "--file", str(dest), "--remote"],
                capture_output=True, text=True, timeout=900, env=env,
            )
            if r.returncode != 0:
                print(f"  FAILED {rel}: {(r.stderr or r.stdout).strip().splitlines()[-1][:120]}")
                failed += 1
                continue
            ledger[rel] = dest.stat().st_size
            downloaded += 1
            print(f"  downloaded {rel}")
        if not args.dry_run and downloaded:
            LEDGER.parent.mkdir(exist_ok=True)
            LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1))

    if not args.dry_run:
        print(f"viewer: {viewer.build()}")

    print(f"\naudio — downloaded: {downloaded}, already present: {present}, failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
