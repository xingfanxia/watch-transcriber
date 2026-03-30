"""Pluggable delivery layer for watch-transcriber.

Each delivery module must implement:
    deliver(note: dict) -> bool

Where note has:
    - title: str (e.g. "Voice Note 2026-03-29 21:25")
    - transcript: str (raw transcript with timestamps/speakers)
    - summary: str (AI-generated summary, may be empty)
    - todos: list[str] (extracted action items, may be empty)
    - audio_path: str (path to original .m4a file)
    - timestamp: str (ISO format)
    - markdown: str (formatted markdown combining all fields)
"""

import importlib
import os


BUILTIN_DELIVERIES = ["file", "apple_notes", "obsidian_git", "agent"]


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
