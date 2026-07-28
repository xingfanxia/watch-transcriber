"""Backfill data/manifest.json (note↔audio mapping + AI topic categories).

Walks every per-recording note under data/<YYYY-MM-DD>/, parses title +
original Voice Memos filename out of the note itself, batch-classifies
uncategorized entries with Gemini against manifest.CATEGORIES, then writes
the manifest and regenerates the data/by-topic/ symlink views.

Idempotent: entries that already carry a category are not re-sent to the
model (use --reclassify to force). Classification failures fall back to
其他 and are reported, never dropped.

Usage:
    venv/bin/python3 scripts/backfill/backfill_manifest.py [--reclassify] [--dry-run]
"""

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import transcribe  # noqa: E402  (loads .env at import; also lends its Gemini helpers)
from deliveries import archive_root, manifest  # noqa: E402

BATCH_SIZE = 40

CLASSIFY_PROMPT = f"""You are classifying personal voice-note recordings by dominant topic/scene.
Allowed categories, pick EXACTLY one per item: {" / ".join(manifest.CATEGORIES)}

Return ONLY valid JSON mapping every item id to its category:
{{"<id>": "<category>", ...}}

Items:
"""


def scan_notes() -> dict:
    """Parse every per-recording note into a manifest-shaped entry (no category)."""
    entries = {}
    for day_dir in sorted(archive_root().iterdir()):
        if not day_dir.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_dir.name):
            continue
        for md in sorted(day_dir.glob("*.md")):
            m = re.fullmatch(r"(\d{6})-(.+)\.md", md.name)
            if not m:
                continue  # daily.md
            hhmmss = m.group(1)
            text = md.read_text(encoding="utf-8")
            title = text.split("\n", 1)[0].lstrip("# ").strip()
            fm = re.search(r"\*\*File:\*\* `([^`]+)`", text)
            sm = re.search(r"## Summary\s*\n+(.+)", text)
            audio = next(
                (p for p in sorted(day_dir.glob(f"{md.stem}.*"))
                 if p.suffix not in (".md", ".tmp")),
                None,
            )
            entries[f"{day_dir.name} {hhmmss}"] = {
                "original": fm.group(1) if fm else None,
                "title": title,
                "category": None,  # filled from existing manifest or classification
                "note": str(md.relative_to(archive_root())),
                "audio": str(audio.relative_to(archive_root())) if audio else None,
                "_summary": (sm.group(1).strip()[:160] if sm else ""),
            }
    return entries


def classify(client, pending: dict) -> dict:
    """Batch-classify {key: entry} → {key: category}. Tolerant: anything the
    model gets wrong (missing id, unknown label) falls back to 其他."""
    out = {}
    keys = list(pending)
    fallbacks = 0
    for i in range(0, len(keys), BATCH_SIZE):
        batch = keys[i:i + BATCH_SIZE]
        lines = [
            f"{k}: {pending[k]['title']}"
            + (f" — {pending[k]['_summary']}" if pending[k]["_summary"] else "")
            for k in batch
        ]
        # One dead batch (rate limit, network) must not abort the run or lose
        # the other batches — fall back to 其他 and keep going.
        try:
            resp = client.models.generate_content(
                model=transcribe.GEMINI_MODEL,
                contents=[CLASSIFY_PROMPT + "\n".join(lines)],
                config={"response_mime_type": "application/json"},
            )
            parsed = transcribe._parse_gemini_json(resp.text or "")
        except Exception as e:
            print(f"  WARN batch {i // BATCH_SIZE + 1} failed ({e}); falling back to 其他")
            parsed = {}
        for k in batch:
            raw = parsed.get(k)
            out[k] = manifest.clean_category(raw)
            if raw not in manifest.CATEGORIES:
                fallbacks += 1
                print(f"  WARN {k}: model returned {raw!r} -> 其他")
        done = min(i + BATCH_SIZE, len(keys))
        print(f"  classified {done}/{len(keys)}")
    if fallbacks:
        print(f"  {fallbacks} entries fell back to 其他 (missing/invalid model output)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reclassify", action="store_true",
                    help="re-run AI classification even for already-categorized entries")
    ap.add_argument("--dry-run", action="store_true",
                    help="scan and report; no model calls, no writes")
    args = ap.parse_args()

    os.chdir(REPO_ROOT)
    existing = manifest.load()
    entries = scan_notes()

    for key, entry in entries.items():
        prior = existing.get(key, {})
        if not args.reclassify and prior.get("category") in manifest.CATEGORIES:
            entry["category"] = prior["category"]
        if entry["original"] is None and prior.get("original"):
            entry["original"] = prior["original"]

    pending = {k: e for k, e in entries.items() if e["category"] is None}
    print(f"{len(entries)} notes scanned, {len(pending)} to classify, "
          f"{sum(1 for e in entries.values() if not e['audio'])} without audio")

    if args.dry_run:
        for k, e in list(pending.items())[:10]:
            print(f"  would classify {k}: {e['title']}")
        return 0

    if pending:
        genai = transcribe.ensure_genai()
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        for key, cat in classify(client, pending).items():
            entries[key]["category"] = cat

    for e in entries.values():
        e.pop("_summary", None)
        e["category"] = e["category"] or manifest.FALLBACK_CATEGORY

    manifest.save(entries)
    manifest.rebuild_views(entries)

    counts = {}
    for e in entries.values():
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    print(f"manifest written: {manifest.manifest_path()}")
    for cat in manifest.CATEGORIES:
        if counts.get(cat):
            print(f"  {cat}: {counts[cat]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
