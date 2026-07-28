"""Delete a recording from the archive — the ONE sanctioned deletion path.

Removes, for a manifest key: the note .md, the archived audio copy, the
attachments directory, any stale same-stem siblings, the manifest entry,
its by-topic symlinks, and (best-effort) the R2 backup object + upload
ledger row. The date dir's daily.md/daily.html rollup is regenerated, or
the whole dir removed when this was its last recording. Ends with an
auto-commit/push of the private notes repo.

Deliberately NOT touched: the Voice Memos original (delete in the app if
wanted), Apple Notes / 飞书 copies (external surfaces), and git history
(append-only — the note remains recoverable from the private repo history).

Usage:
    python3 scripts/ops/delete_recording.py "YYYY-MM-DD HHMMSS" [--dry-run] [--keep-r2]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from deliveries import archive_git, archive_root, manifest, r2_backup, viewer  # noqa: E402
from deliveries.local_archive import _build_daily_md, _md_to_html  # noqa: E402

LEDGER = REPO_ROOT / "state" / "r2_uploaded.json"


def delete_recording(key: str, dry_run: bool = False, keep_r2: bool = False) -> dict:
    m = manifest.load()
    entry = m.get(key)
    if not entry:
        return {"ok": False, "error": f"unknown key {key!r}"}
    date, hhmmss = key.split(" ")
    date_dir = archive_root() / date

    victims = sorted({
        *(archive_root() / rel for rel in
          [entry.get("note"), entry.get("audio"), *(entry.get("attachments") or [])] if rel),
        *date_dir.glob(f"{hhmmss}-*"),
    })
    if dry_run:
        return {"ok": True, "dry_run": True,
                "would_delete": [str(v.relative_to(archive_root())) for v in victims]}

    for v in victims:
        if v.is_dir():
            shutil.rmtree(v, ignore_errors=True)
        elif v.exists():
            v.unlink()

    del m[key]
    manifest.save(m)
    manifest.rebuild_views(m)

    # Refresh or retire the day's rollup.
    remaining = [p for p in date_dir.glob("*.md") if p.name != "daily.md"] if date_dir.exists() else []
    if not remaining:
        shutil.rmtree(date_dir, ignore_errors=True)
    else:
        dt = datetime.fromisoformat(f"{date}T00:00:00")
        daily = _build_daily_md(date_dir, dt)
        (date_dir / "daily.md").write_text(daily, encoding="utf-8")
        (date_dir / "daily.html").write_text(_md_to_html(daily, date), encoding="utf-8")

    r2_status = "kept"
    audio_rel = entry.get("audio")
    if audio_rel and not keep_r2:
        wrangler = r2_backup.wrangler_bin()
        if wrangler:
            r = subprocess.run(
                [wrangler, "r2", "object", "delete",
                 f"{r2_backup.bucket()}/{audio_rel}", "--remote"],
                capture_output=True, text=True, timeout=120, env=r2_backup.wrangler_env(),
            )
            r2_status = "deleted" if r.returncode == 0 else "delete-failed"
        else:
            r2_status = "no-wrangler"
        if LEDGER.exists():
            ledger = json.loads(LEDGER.read_text())
            if ledger.pop(audio_rel, None) is not None:
                LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1))

    viewer.build()
    archive_git.deliver({"title": f"delete: {entry.get('title') or key}"})
    return {"ok": True, "deleted": [str(v.relative_to(archive_root())) for v in victims],
            "r2": r2_status if audio_rel else "no-audio"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("key")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-r2", action="store_true")
    args = ap.parse_args()
    os.chdir(REPO_ROOT)
    result = delete_recording(args.key, dry_run=args.dry_run, keep_r2=args.keep_r2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
