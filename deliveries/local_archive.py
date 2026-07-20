"""Local archive delivery — structured date-bucketed output.

Layout (LOCAL_ARCHIVE_DIR defaults to ./data, relative to repo root):

    data/YYYY-MM-DD/
      HHMMSS-<title-slug>.md   # one file per recording
      daily.md                 # day rollup, regenerated from per-recording files
      daily.html               # rendered HTML rollup (LOCAL_ARCHIVE_HTML=0 to skip)

Audio files are NOT copied (Voice Memos keeps the originals).
"""

import html
import os
import re
from datetime import datetime
from pathlib import Path


def _slug(s: str) -> str:
    s = re.sub(r"[^\w一-鿿 -]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s[:60] or "note"


def _archive_root() -> Path:
    return Path(os.environ.get("LOCAL_ARCHIVE_DIR", "./data")).expanduser().resolve()


def _parse_dt(note: dict) -> datetime:
    ts = note.get("timestamp")
    if ts:
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            pass
    return datetime.now()


def deliver(note: dict) -> bool:
    dt = _parse_dt(note)
    date_dir = _archive_root() / dt.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    recording_name = f"{dt.strftime('%H%M%S')}-{_slug(note['title'])}.md"
    recording_path = date_dir / recording_name
    # AI titles can differ between reprocess runs; the HHMMSS prefix is the
    # deterministic key. Drop stale outputs for the same recording so the
    # daily.md rollup doesn't accumulate duplicates.
    for old in date_dir.glob(f"{dt.strftime('%H%M%S')}-*.md"):
        if old != recording_path:
            old.unlink()
    recording_path.write_text(note["markdown"], encoding="utf-8")
    print(f"[delivery:local_archive] wrote {recording_path}")

    daily_md_path = date_dir / "daily.md"
    daily_md = _build_daily_md(date_dir, dt)
    daily_md_path.write_text(daily_md, encoding="utf-8")

    if os.environ.get("LOCAL_ARCHIVE_HTML", "1") not in ("0", "false", "no"):
        daily_html_path = date_dir / "daily.html"
        daily_html_path.write_text(_md_to_html(daily_md, dt.strftime("%Y-%m-%d")), encoding="utf-8")

    return True


def _build_daily_md(date_dir: Path, dt: datetime) -> str:
    recordings = sorted(p for p in date_dir.iterdir() if p.suffix == ".md" and p.name != "daily.md")
    parts = [f"# {dt.strftime('%Y-%m-%d')}", "", f"_{len(recordings)} recording(s)_", ""]
    for i, rec in enumerate(recordings):
        if i > 0:
            parts.append("---")
            parts.append("")
        parts.append(rec.read_text(encoding="utf-8").rstrip())
        parts.append("")
    return "\n".join(parts)


def _md_to_html(md: str, title: str) -> str:
    body = _convert_md_body(md)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 740px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #1a1a1a; }}
  h1, h2, h3 {{ line-height: 1.25; }}
  h1 {{ font-size: 1.8rem; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3rem; }}
  h2 {{ font-size: 1.3rem; margin-top: 1.8rem; }}
  hr {{ border: 0; border-top: 1px solid #d0d7de; margin: 2rem 0; }}
  code {{ background: #f6f8fa; padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.9em; }}
  ul.todo {{ list-style: none; padding-left: 1rem; }}
  ul.todo li {{ margin: 0.25rem 0; }}
  .transcript {{ white-space: pre-wrap; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 0.85rem; background: #f6f8fa; padding: 1rem; border-radius: 6px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _convert_md_body(md: str) -> str:
    out = []
    in_ul = False
    in_todo = False
    in_transcript = False
    transcript_buf = []

    def close_lists():
        nonlocal in_ul, in_todo
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_todo:
            out.append("</ul>")
            in_todo = False

    def flush_transcript():
        nonlocal in_transcript, transcript_buf
        if in_transcript:
            out.append(f'<div class="transcript">{html.escape(chr(10).join(transcript_buf).strip())}</div>')
            in_transcript = False
            transcript_buf = []

    for line in md.split("\n"):
        s = line.rstrip()

        if in_transcript:
            if s.startswith("#") or s.startswith("---"):
                flush_transcript()
            elif s.strip() == "```":
                continue  # drop the markdown code fences; HTML uses the styled div
            else:
                transcript_buf.append(s)
                continue

        if not s.strip():
            close_lists()
            continue

        if s.startswith("## Transcript"):
            close_lists()
            out.append("<h2>Transcript</h2>")
            in_transcript = True
            continue

        if s.startswith("### "):
            close_lists()
            out.append(f"<h3>{_inline(s[4:])}</h3>")
        elif s.startswith("## "):
            close_lists()
            out.append(f"<h2>{_inline(s[3:])}</h2>")
        elif s.startswith("# "):
            close_lists()
            out.append(f"<h1>{_inline(s[2:])}</h1>")
        elif s == "---":
            close_lists()
            out.append("<hr>")
        elif s.lstrip().startswith("- [ ] "):
            if not in_todo:
                close_lists()
                out.append('<ul class="todo">')
                in_todo = True
            item = s.lstrip()[6:]
            out.append(f'<li><input type="checkbox" disabled> {_inline(item)}</li>')
        elif s.lstrip().startswith("- "):
            if not in_ul:
                close_lists()
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(s.lstrip()[2:])}</li>")
        else:
            close_lists()
            out.append(f"<p>{_inline(s)}</p>")

    flush_transcript()
    close_lists()
    return "\n".join(out)


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s
