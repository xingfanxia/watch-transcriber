"""File delivery — saves transcript as markdown to OUTPUT_DIR."""

import os
from pathlib import Path


def deliver(note: dict) -> bool:
    output_dir = Path(os.environ.get("OUTPUT_DIR", "~/Documents/VoiceNotes")).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_title = note["title"].replace("/", "-").replace(":", "-")
    path = output_dir / f"{safe_title}.md"

    path.write_text(note["markdown"], encoding="utf-8")
    print(f"[delivery:file] saved to {path}")
    return True
