#!/usr/bin/env python3
"""Move the backfilled Feishu docs into the Voice Transcripts folder.

Docs became user-owned via transfer_owner, which parked them in the user's
My Space root; the bot has no permission on that source parent, so the move
MUST run with USER identity (`--as user`) — requires a valid user token
(`lark-cli auth login --recommend`).

Usage:
  venv/bin/python3 scripts/backfill/move_docs_to_folder.py           # dry-run
  venv/bin/python3 scripts/backfill/move_docs_to_folder.py --yes
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEDGER = Path(__file__).parent / "backfill_ledger.jsonl"

sys.path.insert(0, str(REPO))
import transcribe  # noqa: E402  (for load_dotenv_simple)

transcribe.load_dotenv_simple(REPO / ".env")
FOLDER = os.environ.get("FEISHU_FOLDER_TOKEN", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually move (default: dry-run)")
    args = ap.parse_args()
    if not FOLDER:
        print("FEISHU_FOLDER_TOKEN not set in .env")
        return 1

    rows = [json.loads(ln) for ln in LEDGER.read_text().splitlines() if ln.strip()]
    docs = [(r["new_title"], r["feishu_url"].rsplit("/", 1)[-1])
            for r in rows if r.get("feishu_url")]
    print(f"{len(docs)} docs → folder {FOLDER}")
    failed = 0
    for title, token in docs:
        if not args.yes:
            print(f"  would move: {title}")
            continue
        r = subprocess.run(
            ["lark-cli", "api", "POST", f"/open-apis/drive/v1/files/{token}/move",
             "--data", json.dumps({"type": "docx", "folder_token": FOLDER}),
             "--as", "user"],
            capture_output=True, text=True, timeout=30,
        )
        out = (r.stdout or r.stderr)
        if r.returncode == 0 and '"ok": true' in out:
            print(f"  moved: {title}")
        else:
            failed += 1
            print(f"  FAILED: {title} — {out[:200]}")
        time.sleep(0.4)
    if not args.yes:
        print("\nDry-run only. Re-run with --yes after `lark-cli auth login --recommend`.")
    else:
        print(f"\nDone, {failed} failures.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
