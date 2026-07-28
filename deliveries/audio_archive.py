"""Audio archive delivery — AI-titled copy of the original recording.

Copies the source .m4a next to the note that local_archive writes, sharing
the recording_stem naming convention:

    data/YYYY-MM-DD/
      HHMMSS-<title-slug>.md    # local_archive
      HHMMSS-<title-slug>.m4a   # this delivery

The Voice Memos original is never touched (renaming it in place would break
CloudRecordings.db references and iCloud sync). Reprocess-safe: stale copies
from a run that produced a different AI title are removed (the HHMMSS prefix
is the deterministic key — the whole archive assumes at most one recording
per wall-clock second, which a single Watch can't violate).
"""

import os
import shutil
from pathlib import Path

from . import archive_root, parse_note_dt, recording_stem


def deliver(note: dict) -> bool:
    src = Path(note["audio_path"])
    if not src.exists():
        print(f"[delivery:audio_archive] source missing: {src}")
        return False

    dt = parse_note_dt(note)
    date_dir = archive_root() / dt.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    dest = date_dir / f"{recording_stem(note)}{src.suffix}"

    # Clear tmp orphans from any crashed run for this recording, whatever
    # title it had then (they'd otherwise never match dest or the stale glob).
    for orphan in date_dir.glob(f"{dt.strftime('%H%M%S')}-*{src.suffix}.tmp"):
        orphan.unlink()

    # Size-only compare is enough: Voice Memos originals are immutable once
    # synced, and a partial copy from a crashed run can't match the source size.
    if not (dest.exists() and dest.stat().st_size == src.stat().st_size):
        # Copy via tmp + rename so concurrent readers of data/ (cloud backup,
        # Obsidian sync) never see a half-written recording.
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
        print(f"[delivery:audio_archive] copied {dest}")
    else:
        print(f"[delivery:audio_archive] up-to-date {dest}")

    # Drop stale copies from a reprocess that produced a different title —
    # only after the fresh copy has landed, so a crash never leaves the
    # recording with no archived audio at all.
    for old in date_dir.glob(f"{dt.strftime('%H%M%S')}-*{src.suffix}"):
        if old != dest:
            old.unlink()
    return True
