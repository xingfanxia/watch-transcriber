#!/usr/bin/env python3
"""One-shot retitle: "标题 (2026-07-20 01:40)" → "2026-07-20 01:40 标题".

Pure string transform across all four surfaces (no re-summarization):
  1. Feishu doc titles — PATCH the page block (rename in place, bot identity)
  2. Apple Notes — one AppleScript batch setting each note's name
  3. ~/Documents/VoiceNotes files — rename to the new safe filename
  4. data/ archives — rewrite the H1 + regenerate daily.md/html per date dir
  5. ledger — update new_title so future resumes see the current truth

Usage: venv/bin/python3 scripts/backfill/retitle_timestamp_first.py [--yes]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEDGER = Path(__file__).parent / "backfill_ledger.jsonl"
sys.path.insert(0, str(REPO))

import transcribe  # noqa: E402
from deliveries import safe_filename, local_archive  # noqa: E402

transcribe.load_dotenv_simple(REPO / ".env")

OLD_RE = re.compile(r"^(.*) \((\d{4}-\d{2}-\d{2} \d{2}:\d{2})\)$")


def to_new(old_title: str):
    m = OLD_RE.match(old_title)
    if not m:
        return None
    return f"{m.group(2)} {m.group(1)}"


def rename_feishu(token: str, new_title: str) -> bool:
    r = subprocess.run(
        ["lark-cli", "api", "PATCH",
         f"/open-apis/docx/v1/documents/{token}/blocks/{token}",
         "--data", json.dumps(
             {"update_text_elements": {"elements": [{"text_run": {"content": new_title}}]}},
             ensure_ascii=False)],
        capture_output=True, text=True, timeout=30)
    return r.returncode == 0 and '"ok": true' in (r.stdout or "")


def rename_apple_notes(pairs) -> int:
    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    lines = [f'set renames to {{{", ".join(f'{{"{esc(o)}", "{esc(n)}"}}' for o, n in pairs)}}}']
    script = "\n".join(lines) + '''
    set done to 0
    tell application "Notes"
        repeat with f in folders of default account
            if name of f is "Voice Transcripts" then
                repeat with pair in renames
                    set oldN to item 1 of pair
                    set newN to item 2 of pair
                    repeat with n in (notes of f whose name is oldN)
                        set name of n to newN
                        set done to done + 1
                    end repeat
                end repeat
                exit repeat
            end if
        end repeat
    end tell
    return done
    '''
    r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(f"  [apple_notes] error: {r.stderr.strip()[:300]}")
        return -1
    return int(r.stdout.strip() or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(ln) for ln in LEDGER.read_text().splitlines() if ln.strip()]
    plan = []
    for r in rows:
        new = to_new(r["new_title"])
        if new:
            plan.append((r, r["new_title"], new))
    print(f"{len(rows)} ledger rows, {len(plan)} to retitle")
    if not args.yes:
        for _, o, n in plan[:5]:
            print(f"  {o}  →  {n}")
        print("Dry-run only; rerun with --yes")
        return 0

    out_dir = Path(os.environ.get("OUTPUT_DIR", "~/Documents/VoiceNotes")).expanduser()
    fs_fail = 0
    touched_dates = set()
    for r, old, new in plan:
        token = r["feishu_url"].rsplit("/", 1)[-1] if r.get("feishu_url") else ""
        if token:
            if not rename_feishu(token, new):
                fs_fail += 1
                print(f"  [feishu] FAILED: {old}")
            time.sleep(0.3)

        oldf = out_dir / f"{safe_filename(old)}.md"
        newf = out_dir / f"{safe_filename(new)}.md"
        if oldf.exists():
            content = oldf.read_text(encoding="utf-8").replace(f"# {old}", f"# {new}", 1)
            newf.write_text(content, encoding="utf-8")
            oldf.unlink()

        key_dir, key_hms = Path(r["archive"]).parent.name, Path(r["archive"]).name[:6]
        ddir = REPO / "data" / key_dir
        for md in ddir.glob(f"{key_hms}-*.md"):
            body = md.read_text(encoding="utf-8")
            if f"# {old}" in body:
                md.write_text(body.replace(f"# {old}", f"# {new}", 1), encoding="utf-8")
            touched_dates.add(ddir)

        r["new_title"] = new

    n = rename_apple_notes([(o, new) for _, o, new in plan])
    print(f"[apple_notes] renamed {n}/{len(plan)}")

    from datetime import datetime
    for ddir in sorted(touched_dates):
        dt = datetime.strptime(ddir.name, "%Y-%m-%d")
        daily = local_archive._build_daily_md(ddir, dt)
        (ddir / "daily.md").write_text(daily, encoding="utf-8")
        (ddir / "daily.html").write_text(
            local_archive._md_to_html(daily, ddir.name), encoding="utf-8")

    LEDGER.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    print(f"Done. feishu failures: {fs_fail}; daily.md rebuilt for {len(touched_dates)} dates")
    return 1 if fs_fail else 0


if __name__ == "__main__":
    sys.exit(main())
