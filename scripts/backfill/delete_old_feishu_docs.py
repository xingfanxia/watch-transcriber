#!/usr/bin/env python3
"""Delete old timestamp-titled Feishu docs listed in feishu_old_docs_manifest.json.

PREREQUISITE: the lark-cli app (cli_a942426c0ab81cdd) must have the
drive:drive scope applied + approved, or every DELETE returns 99991672.
Apply at: https://open.feishu.cn/app/cli_a942426c0ab81cdd/auth?q=drive%3Adrive

The manifest is built by harvest_feishu_manifest.py from transcriber.log and is
a LOWER BOUND — docs created before the log was truncated are not in it; sweep
leftovers by searching "Voice Note" in Feishu 云文档 manually.

Usage:
  venv/bin/python3 scripts/backfill/delete_old_feishu_docs.py           # dry-run
  venv/bin/python3 scripts/backfill/delete_old_feishu_docs.py --yes    # delete
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

MANIFESTS = [
    # Ground truth: full drive listing reconciled against the canonical ledger
    # (supersedes the log-harvested feishu_old_docs / feishu_dup_docs manifests).
    Path(__file__).parent / "feishu_drive_reconciled_manifest.json",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    docs = []
    for m in MANIFESTS:
        if m.exists():
            docs.extend(json.loads(m.read_text()))
    seen = set()
    docs = [d for d in docs if not (d["token"] in seen or seen.add(d["token"]))]
    print(f"{len(docs)} docs across manifests")
    failed = 0
    for d in docs:
        if not args.yes:
            print(f"  would delete: {d['title']}  {d['url']}")
            continue
        # Query params must go via --params: an inline "?type=docx" on the path
        # is dropped by lark-cli api for DELETE → 99992402 field validation.
        r = subprocess.run(
            ["lark-cli", "api", "DELETE",
             f"/open-apis/drive/v1/files/{d['token']}",
             "--params", '{"type":"docx"}'],
            capture_output=True, text=True, timeout=30,
        )
        out = (r.stdout or r.stderr).strip()
        ok = r.returncode == 0 and '"ok": true' in out
        # Already-deleted docs return 1061007 "file has been delete." —
        # treat as success (idempotent).
        gone = ("1061007" in out or "has been delete" in out
                or "not found" in out.lower())
        if ok or gone:
            print(f"  deleted: {d['title']}")
        else:
            failed += 1
            print(f"  FAILED: {d['title']} — {out[:200]}")
        time.sleep(0.5)
    if not args.yes:
        print("\nDry-run only. Re-run with --yes to delete.")
    else:
        print(f"\nDone, {failed} failures.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
