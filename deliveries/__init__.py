"""Pluggable delivery layer for watch-transcriber.

Each delivery module must implement:
    deliver(note: dict) -> bool

Where note has:
    - title: str (timestamp + AI-generated topic, e.g. "2026-03-29 21:25 项目排期讨论"
      so name-sorted lists order chronologically; falls back to
      "2026-03-29 21:25 Voice Note" when the summarize stage yields no title)
    - transcript: str (raw transcript with timestamps/speakers)
    - summary: str (AI-generated summary, may be empty)
    - todos: list[str] (extracted action items, may be empty)
    - category: str (AI-assigned topic, one of manifest.CATEGORIES)
    - audio_path: str (path to original .m4a file)
    - timestamp: str (ISO format)
    - markdown: str (formatted markdown combining all fields)
"""

import importlib
import os
import re
from datetime import datetime
from pathlib import Path


def safe_filename(title: str) -> str:
    """Whitelist-sanitize a note title for cross-platform filenames.

    Windows / Obsidian sync reject ? * " | < > : (and fullwidth variants are
    confusing); AI-generated titles can contain any of these. Keeps word chars,
    CJK, spaces, parens, dots, hyphens; everything else becomes a hyphen.
    """
    s = re.sub(r"[^\w一-鿿《》「」【】· ().-]", "-", title)
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"\s+", " ", s).strip(" -.")
    return s[:80] or "note"


def slug(s: str) -> str:
    """Archive filename slug. Shared by every per-recording artifact — the .md
    note, the .m4a copy, and the manifest all derive the same stem, so this
    must stay the single implementation or the pairing silently breaks."""
    s = re.sub(r"[^\w一-鿿 -]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s[:60] or "note"


def archive_root() -> Path:
    return Path(os.environ.get("LOCAL_ARCHIVE_DIR", "./data")).expanduser().resolve()


def parse_note_dt(note: dict) -> datetime:
    ts = note.get("timestamp")
    if ts:
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            pass
    return datetime.now()


def recording_stem(note: dict) -> str:
    """Deterministic "HHMMSS-<title-slug>" stem pairing all archive artifacts
    of one recording. The archive filename already carries HHMMSS and the date
    dir carries the day, so the title's leading timestamp is stripped before
    slugging to avoid duplicating the date."""
    dt = parse_note_dt(note)
    display = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}\s*", "", note["title"])
    return f"{dt.strftime('%H%M%S')}-{slug(display or note['title'])}"


BUILTIN_DELIVERIES = ["file", "local_archive", "audio_archive", "manifest", "viewer", "archive_git", "r2_backup", "apple_notes", "feishu", "feishu_notify", "obsidian_git", "agent"]


def get_active_deliveries() -> list[str]:
    targets = os.environ.get("DELIVERY_TARGETS", "file")
    return [t.strip() for t in targets.split(",") if t.strip()]


def deliver_all(note: dict) -> dict[str, bool]:
    results = {}
    for target in get_active_deliveries():
        try:
            mod = importlib.import_module(f"deliveries.{target}")
            results[target] = mod.deliver(note)
        except Exception as e:
            print(f"[delivery:{target}] failed: {e}")
            results[target] = False
    return results
