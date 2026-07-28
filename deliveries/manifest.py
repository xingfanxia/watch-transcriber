"""Manifest delivery — the 1:1 note↔audio map plus topic views.

Maintains data/manifest.json, keyed by "YYYY-MM-DD HHMMSS" (recording start,
the same deterministic key the archive filenames carry):

    {
      "2026-07-26 013421": {
        "original": "20260726 013421-44BB9B24.m4a",   # Voice Memos source file
        "title": "2026-07-26 01:34 银发大基建中控平台合作",
        "category": "工作商务",                        # one of CATEGORIES
        "note": "2026-07-26/013421-银发大基建中控平台合作.md",
        "audio": "2026-07-26/013421-银发大基建中控平台合作.m4a"  # null if no copy
      }, ...
    }

After every update it regenerates data/by-topic/<category>/ — relative
symlinks to the real note+audio pairs, so Finder gets a topic-organized view
without duplicating files. by-topic/ is generated output: never hand-edit.

Run this delivery AFTER local_archive and audio_archive in DELIVERY_TARGETS —
it locates their outputs on disk by the shared HHMMSS-stem.
"""

import json
import os
from pathlib import Path

from . import archive_root, parse_note_dt, recording_stem, safe_filename

# Fixed taxonomy for the summarize stage's category field. Order matters only
# for prompt display. Keep in sync with nothing — this list IS the source;
# the summarize prompt and all validation derive from it.
CATEGORIES = ["亲密关系", "自我成长", "学习认知", "工作商务", "生活日常", "其他"]
FALLBACK_CATEGORY = "其他"


def clean_category(raw) -> str:
    """Tolerant whitelist validation for a model-emitted category."""
    c = str(raw or "").strip()
    return c if c in CATEGORIES else FALLBACK_CATEGORY


def manifest_path() -> Path:
    return archive_root() / "manifest.json"


def load() -> dict:
    p = manifest_path()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def save(manifest: dict) -> None:
    p = manifest_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(dict(sorted(manifest.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, p)


def deliver(note: dict) -> bool:
    dt = parse_note_dt(note)
    key = dt.strftime("%Y-%m-%d %H%M%S")
    date_dir = archive_root() / dt.strftime("%Y-%m-%d")
    stem = recording_stem(note)

    note_file = date_dir / f"{stem}.md"
    audio_file = next(
        (p for p in sorted(date_dir.glob(f"{stem}.*"))
         if p.suffix not in (".md", ".tmp")),
        None,
    )

    manifest = load()
    entry = {
        "original": Path(note["audio_path"]).name,
        "title": note["title"],
        "category": clean_category(note.get("category")),
        "note": _rel(note_file) if note_file.exists() else None,
        "audio": _rel(audio_file) if audio_file else None,
    }
    # User-authored fields (desktop app writes them) survive a reprocess.
    prior = manifest.get(key) or {}
    for field in ("speakers", "speakers_applied", "attachments"):
        if prior.get(field):
            entry[field] = prior[field]
    manifest[key] = entry
    save(manifest)
    rebuild_views(manifest)
    print(f"[delivery:manifest] {key} -> {manifest[key]['category']} "
          f"(audio: {'yes' if audio_file else 'no'})")
    return True


def _rel(p: Path) -> str:
    return str(p.relative_to(archive_root()))


def rebuild_views(manifest: dict) -> None:
    """Regenerate data/by-topic/ from scratch — it only ever contains symlinks,
    so a full wipe is safe and keeps renames/recategorizations clean.

    Deliberately full-rebuild, not incremental: the whole tree is ~2 symlinks
    per recording (a few hundred total, milliseconds to recreate), and a wipe
    is the only approach that needs no bookkeeping when a reprocess changes an
    entry's title or category. Revisit only if the archive grows ~10x."""
    views = archive_root() / "by-topic"
    if views.exists():
        for entry in views.rglob("*"):
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
        for d in sorted((d for d in views.rglob("*") if d.is_dir()), reverse=True):
            d.rmdir()
    for key, entry in manifest.items():
        cat_dir = views / (entry.get("category") or FALLBACK_CATEGORY)
        cat_dir.mkdir(parents=True, exist_ok=True)
        for field in ("note", "audio"):
            rel = entry.get(field)
            if not rel:
                continue
            target = archive_root() / rel
            if not target.exists():
                continue
            link = cat_dir / f"{safe_filename(entry['title'])}{target.suffix}"
            if link.exists():
                # Same category + same displayed minute + same AI title:
                # disambiguate with the seconds-precision key.
                link = cat_dir / f"{safe_filename(entry['title'])}-{key.split()[1]}{target.suffix}"
            if link.exists():
                print(f"[manifest] WARN duplicate view name, skipped: {link.name}")
                continue
            link.symlink_to(os.path.relpath(target, cat_dir))
