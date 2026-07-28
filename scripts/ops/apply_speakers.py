"""Apply manifest speaker tags to the archive transcript .md files.

The manifest's `speakers` field is the source of truth; this maps those names
into the note files' transcript labels (`[ts] SPEAKER_1:` → `[ts] 妍子:`).
Reversible: `speakers_applied` in the manifest records what the file currently
carries, so renames and clears rewrite from the previous display form back to
the new one. Idempotent — entries whose applied state matches are skipped.

The desktop app invokes this after every /api/speakers write; reprocessed
notes come back with raw SPEAKER_N labels and get re-mapped on the next run
(or via --all).

Usage:
    python3 scripts/ops/apply_speakers.py [--all] [--no-viewer] [KEY ...]
"""

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from deliveries import archive_root, manifest, viewer  # noqa: E402


def apply_keys(keys: list[str] | None = None, rebuild_viewer: bool = True) -> int:
    """Rewrite transcript labels for the given manifest keys (None = all).
    Returns the number of note files changed."""
    m = manifest.load()
    changed = 0
    for key in keys if keys is not None else list(m):
        entry = m.get(key)
        if not entry or not entry.get("note"):
            continue
        desired = entry.get("speakers") or {}
        applied = entry.get("speakers_applied") or {}
        md_path = archive_root() / entry["note"]
        if not md_path.exists():
            continue
        text = md_path.read_text(encoding="utf-8")
        # A reprocess regenerates the note with raw SPEAKER_N labels; detect
        # that and treat the file as unmapped regardless of speakers_applied.
        if applied and not any(
            re.search(rf"^\[[^\]]+\]\s*{re.escape(n)}\s*[:：]", text, re.M)
            for n in applied.values()
        ):
            applied = {}
        if desired == applied:
            continue

        for slot in sorted(set(desired) | set(applied)):
            old = applied.get(slot) or slot
            new = desired.get(slot) or slot
            if old == new:
                continue
            # Shared-display guard: if another slot still maps to this display
            # name, rewriting by name would clobber that slot's lines too
            # (seen with a phantom slot from an old-export labeling scheme).
            if any(s != slot and desired.get(s) == old for s in desired):
                print(f"  WARN {key}: skip rewriting {slot} — display {old!r} "
                      f"still owned by another slot")
                continue
            text = re.sub(
                rf"^(\[[^\]]+\])\s*{re.escape(old)}\s*[:：]",
                rf"\1 {new}:",
                text,
                flags=re.M,
            )

        tmp = md_path.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, md_path)
        # Record only what the file actually carries — a tag whose display
        # never occurs in the transcript (phantom slot) must not be recorded,
        # or a later clear would try to un-rewrite lines it never touched.
        new_applied = {
            slot: name for slot, name in desired.items()
            if re.search(rf"^\[[^\]]+\]\s*{re.escape(name)}\s*[:：]", text, re.M)
        }
        if new_applied:
            entry["speakers_applied"] = new_applied
        else:
            entry.pop("speakers_applied", None)
        changed += 1
        print(f"applied speakers to {entry['note']}: {desired or '(cleared)'}")

    if changed:
        manifest.save(m)
        if rebuild_viewer:
            viewer.build()
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("keys", nargs="*", help='manifest keys ("YYYY-MM-DD HHMMSS")')
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-viewer", action="store_true")
    args = ap.parse_args()
    if not args.keys and not args.all:
        ap.error("pass KEY... or --all")
    os.chdir(REPO_ROOT)
    n = apply_keys(None if args.all else args.keys, rebuild_viewer=not args.no_viewer)
    print(f"{n} note file(s) updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
