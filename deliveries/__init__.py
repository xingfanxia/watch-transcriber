"""Pluggable delivery layer for watch-transcriber.

Each delivery module must implement:
    deliver(note: dict) -> bool

Where note has:
    - title: str (AI-generated topic + timestamp, e.g. "项目排期讨论 (2026-03-29 21:25)";
      falls back to "Voice Note 2026-03-29 21:25" when the summarize stage yields no title)
    - transcript: str (raw transcript with timestamps/speakers)
    - summary: str (AI-generated summary, may be empty)
    - todos: list[str] (extracted action items, may be empty)
    - audio_path: str (path to original .m4a file)
    - timestamp: str (ISO format)
    - markdown: str (formatted markdown combining all fields)
"""

import importlib
import os
import re


def safe_filename(title: str) -> str:
    """Whitelist-sanitize a note title for cross-platform filenames.

    Windows / Obsidian sync reject ? * " | < > : (and fullwidth variants are
    confusing); AI-generated titles can contain any of these. Keeps word chars,
    CJK, spaces, parens, dots, hyphens; everything else becomes a hyphen.
    """
    s = re.sub(r"[^\w一-鿿 ().-]", "-", title)
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"\s+", " ", s).strip(" -.")
    return s[:80] or "note"


BUILTIN_DELIVERIES = ["file", "local_archive", "apple_notes", "feishu", "feishu_notify", "obsidian_git", "agent"]


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
