"""File delivery — saves transcript as markdown to OUTPUT_DIR."""

import os
import re
from pathlib import Path

from . import safe_filename


def deliver(note: dict) -> bool:
    output_dir = Path(os.environ.get("OUTPUT_DIR", "~/Documents/VoiceNotes")).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_title = safe_filename(note["title"])
    path = output_dir / f"{safe_title}.md"

    # The AI-generated title part can differ between reprocess runs; the
    # leading timestamp is the deterministic per-recording key. Drop any
    # prior output for the same recording so reprocessing overwrites instead
    # of accumulating orphans.
    prefix = re.match(r"\d{4}-\d{2}-\d{2} \d{2}-\d{2}", safe_title)
    if prefix:
        for old in output_dir.glob(f"{prefix.group(0)}*.md"):
            if old != path:
                old.unlink()

    path.write_text(note["markdown"], encoding="utf-8")
    print(f"[delivery:file] saved to {path}")
    return True
