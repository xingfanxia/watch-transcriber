"""Viewer delivery — regenerates data/index.html, the archive's local UI.

A single self-contained dark-mode-first page (template: viewer_template.html)
with every note's summary/transcript embedded as JSON and audio referenced by
relative path, so it works from file:// with no server and no network.
Regenerated from manifest.json + note .md files on every delivery; also
runnable manually:

    venv/bin/python3 -m deliveries.viewer     (from the repo root)
"""

import json
import os
import re
import shutil
from datetime import date
from pathlib import Path

from . import archive_root
from . import manifest as manifest_mod

_TEMPLATE = Path(__file__).with_name("viewer_template.html")
_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_CJK = re.compile(r"[一-鿿]")
_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})")


def _sections(md_text: str) -> dict:
    """Split a note .md into its ## sections (text between headers)."""
    out = {}
    current = "_head"
    buf = []
    for line in md_text.split("\n"):
        if line.startswith("## "):
            out[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    out[current] = "\n".join(buf).strip()
    return out


def _parse_note(md_path: Path) -> dict:
    text = md_path.read_text(encoding="utf-8")
    sec = _sections(text)

    paras = [p.strip() for p in re.split(r"\n\s*\n", sec.get("Summary", "")) if p.strip()]
    summary_zh = next((p for p in paras if _CJK.search(p)), "")
    summary_en = next((p for p in paras if not _CJK.search(p)), "")

    bullets = [ln[2:].strip() for ln in sec.get("Key Points", "").split("\n")
               if ln.startswith("- ")]
    zh_bullets = [b for b in bullets if _CJK.search(b)]
    key_points = zh_bullets or bullets

    todos = [ln[6:].strip() for ln in sec.get("Action Items", "").split("\n")
             if ln.startswith("- [ ] ")]

    transcript = re.sub(r"^```[^\n]*\n|\n```\s*$", "", sec.get("Transcript", "").strip()).strip()
    stamps = [int(h) * 3600 + int(m) * 60 + int(s)
              for h, m, s in _TS.findall(transcript)]
    return {
        "summary_zh": summary_zh,
        "summary_en": summary_en,
        "key_points": key_points,
        "todos": todos,
        "transcript": transcript,
        "duration": max(stamps) if stamps else None,
    }


def _load_attachments(root: Path, entry: dict) -> list:
    out = []
    for rel in entry.get("attachments") or []:
        p = root / rel
        if p.exists():
            out.append({"name": p.stem, "rel": rel,
                        "content": p.read_text(encoding="utf-8")})
    return out


def build() -> Path:
    root = archive_root()
    # marked.min.js is served/bundled next to index.html so attachment
    # markdown renders identically over http and file://.
    vendor = Path(__file__).with_name("vendor") / "marked.min.js"
    if vendor.exists():
        shutil.copy2(vendor, root / "marked.min.js")
    entries = []
    for key, entry in sorted(manifest_mod.load().items(), reverse=True):
        md_path = root / entry["note"] if entry.get("note") else None
        if not md_path or not md_path.exists():
            continue
        day, hhmmss = key.split(" ")
        parsed = _parse_note(md_path)
        ai_title = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}\s*", "", entry["title"])
        entries.append({
            "key": key,
            "date": day,
            "weekday": _WEEKDAYS[date.fromisoformat(day).weekday()],
            "time": f"{hhmmss[:2]}:{hhmmss[2:4]}",
            "hour_frac": int(hhmmss[:2]) + int(hhmmss[2:4]) / 60,
            "title": entry["title"],
            "ai_title": ai_title or entry["title"],
            "category": entry.get("category") or manifest_mod.FALLBACK_CATEGORY,
            "original": entry.get("original"),
            "audio": entry.get("audio"),
            "speakers": entry.get("speakers") or {},
            "attachments": _load_attachments(root, entry),
            **parsed,
        })

    years = sorted({e["date"][:4] for e in entries})
    payload = {
        "span": f"{years[0]}–{years[-1]}" if years else "",
        "categories": manifest_mod.CATEGORIES,
        "entries": entries,
    }
    html = _TEMPLATE.read_text(encoding="utf-8").replace(
        "__PAYLOAD__",
        json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"),
    )
    dest = root / "index.html"
    tmp = dest.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, dest)
    return dest


def deliver(note: dict) -> bool:
    dest = build()
    print(f"[delivery:viewer] rebuilt {dest}")
    return True


if __name__ == "__main__":
    print(f"viewer: {build()}")
