"""Import a ChatGPT (GPT Pro) thread export into the archive.

For every user message that uploaded a recording transcript (VoiceNotes /
legacy filename formats), this: (1) resolves the file back to its manifest
key, (2) captures the assistant's analysis that followed, (3) attaches that
analysis as a per-recording markdown note, and (4) asks Gemini to extract the
"who is which SPEAKER_N" mapping from the analysis and tags the recording —
never overwriting a speaker you already tagged by hand.

Re-uploads of the same recording keep only the LATEST analysis; identical
content is never attached twice (idempotent by exact content match). Ends by
mapping tags into the transcript files (apply_speakers), rebuilding the
viewer, and committing/pushing the private notes repo.

Usage:
    venv/bin/python3 scripts/ops/import_gpt_thread.py <export.json> [--dry-run]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "ops"))

import transcribe  # noqa: E402  (loads .env, Gemini helpers)
from apply_speakers import apply_keys  # noqa: E402
from deliveries import archive_git, archive_root, manifest, safe_filename  # noqa: E402

MIN_RESPONSE_CHARS = 200
ATT_BASENAME = "GPT-Pro-分析"

# "2026-07-25 05_07 探讨….md" / "2026-07-25 05_07 探讨…(1).md"
_VN_NEW = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2})_(\d{2})\b.*\.md$")
# "Voice Note 2026-07-10 03_48.md"
_VN_OLD = re.compile(r"^Voice Note (\d{4}-\d{2}-\d{2}) (\d{2})_(\d{2})\.md$")
# "015728-Voice-Note-2026-05-18-0157.md"
_ARCH = re.compile(r"^(\d{6})-.+-(\d{4}-\d{2}-\d{2})-\d{4}\.md$")

SPEAKER_MAP_PROMPT = """下面是一段对某个「对话录音转写」的分析文字。分析中通常会指出 SPEAKER_1 / SPEAKER_2 分别是谁。
提取这个身份映射,只返回 JSON(无 markdown 围栏),例如 {"SPEAKER_1": "妍子", "SPEAKER_2": "言泽"}。
只包含分析中明确指认的槽位;没有明确指认就返回 {}。人名用分析中使用的称呼,不要造名字。

分析文字:
"""


def resolve_key(filename: str, keys: list[str]) -> str | None:
    """Map an uploaded transcript filename back to a manifest key."""
    if m := _ARCH.match(filename):
        k = f"{m.group(2)} {m.group(1)}"
        return k if k in keys else None
    m = _VN_NEW.match(filename) or _VN_OLD.match(filename)
    if m:
        prefix = f"{m.group(1)} {m.group(2)}{m.group(3)}"
        hits = [k for k in keys if k.startswith(prefix)]
        return hits[0] if len(hits) == 1 else None
    return None


def message_text(msg: dict) -> str:
    content = msg.get("content") or {}
    if content.get("content_type") != "text":
        return ""
    return "\n".join(p for p in content.get("parts") or [] if isinstance(p, str))


def response_below(mapping: dict, nid: str) -> str:
    """Assistant analysis under a user node: follow the LATEST branch (edited
    turns fork the tree, and re-asks are children[-1]) until the next user
    message, concatenating assistant text along the way."""
    parts, cur = [], mapping.get(nid) or {}
    while True:
        kids = cur.get("children") or []
        if not kids:
            break
        cur = mapping.get(kids[-1]) or {}
        msg = cur.get("message") or {}
        role = (msg.get("author") or {}).get("role")
        if role == "user":
            break
        if role == "assistant":
            t = message_text(msg)
            if t.strip():
                parts.append(t)
    return "\n\n".join(parts).strip()


def collect_uploads(export: dict, keys: list[str]) -> dict:
    """{key: {"file": name, "response": md, "time": ts}} — scans the WHOLE
    conversation tree (uploads on abandoned/edited branches count too); when
    the same recording was uploaded more than once, the latest upload wins."""
    mapping = export["mapping"]
    nodes = []
    for nid, node in mapping.items():
        msg = (node or {}).get("message") or {}
        if (msg.get("author") or {}).get("role") == "user":
            nodes.append((msg.get("create_time") or 0, nid, msg))
    out, skipped = {}, []
    for ts, nid, msg in sorted(nodes):
        matched = {}
        for a in (msg.get("metadata") or {}).get("attachments") or []:
            name = a.get("name") or ""
            if not name.endswith((".md", ".txt")):
                continue
            key = resolve_key(name, keys)
            if key:
                matched[key] = name
            elif _VN_NEW.match(name) or _VN_OLD.match(name) or _ARCH.match(name):
                skipped.append(name)
        if not matched:
            continue
        response = response_below(mapping, nid)
        if len(response) < MIN_RESPONSE_CHARS:
            continue
        shared = len(matched) > 1
        for key, fname in matched.items():
            body = response
            if shared:
                body = (f"> 该分析在同一条消息里覆盖了 {len(matched)} 条录音"
                        f"({', '.join(sorted(matched))})。\n\n" + response)
            out[key] = {"file": fname, "response": body, "time": ts}
    if skipped:
        print(f"unmatched transcript-looking uploads (no manifest entry): {sorted(set(skipped))}")
    return out


def extract_speakers(client, response: str) -> dict:
    try:
        r = client.models.generate_content(
            model=transcribe.GEMINI_MODEL,
            contents=[SPEAKER_MAP_PROMPT + response[:12000]],
            config={"response_mime_type": "application/json"},
        )
        parsed = transcribe._parse_gemini_json(r.text or "")
    except Exception as e:
        print(f"  WARN speaker extraction failed: {e}")
        return {}
    out = {}
    for slot, name in (parsed.items() if isinstance(parsed, dict) else []):
        if re.fullmatch(r"SPEAKER_\d+", str(slot)) and isinstance(name, str):
            name = name.strip()
            # GPT analyses address the archive owner in second person.
            name = {"你": "AX", "我": "AX", "用户": "AX", "you": "AX"}.get(name, name)
            if 0 < len(name) <= 20 and "\n" not in name:
                out[slot] = name
    return out


def attach(entry: dict, key: str, content: str, dry: bool) -> str:
    """Write the analysis attachment unless identical content already exists."""
    root = archive_root()
    for rel in entry.get("attachments") or []:
        p = root / rel
        if p.exists() and p.read_text(encoding="utf-8") == content:
            return "already-attached"
    date, hhmmss = key.split(" ")
    att_dir = root / date / f"{hhmmss}-attachments"
    path = att_dir / f"{safe_filename(ATT_BASENAME)}.md"
    n = 2
    while path.exists():
        if path.read_text(encoding="utf-8") == content:
            return "already-attached"
        path = att_dir / f"{safe_filename(ATT_BASENAME)}-{n}.md"
        n += 1
    if dry:
        return f"would-attach {path.name}"
    att_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    entry.setdefault("attachments", []).append(str(path.relative_to(root)))
    return f"attached {path.name}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("export_json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    os.chdir(REPO_ROOT)

    export = json.load(open(args.export_json, encoding="utf-8"))
    m = manifest.load()
    uploads = collect_uploads(export, list(m))
    print(f"{len(uploads)} recording(s) matched in thread "
          f"'{export.get('title')}'\n")

    client = None
    if not args.dry_run:
        genai = transcribe.ensure_genai()
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    tagged, conflicts = [], []
    for key in sorted(uploads):
        entry = m[key]
        info = uploads[key]
        att_status = attach(entry, key, info["response"], args.dry_run)

        spk_status = "dry-run"
        if client:
            extracted = extract_speakers(client, info["response"])
            existing = entry.get("speakers") or {}
            merged, conflict = dict(existing), []
            for slot, name in extracted.items():
                if slot in existing and existing[slot] != name:
                    conflict.append(f"{slot}: kept {existing[slot]!r}, gpt said {name!r}")
                else:
                    merged[slot] = name
            if merged != existing:
                entry["speakers"] = merged
                tagged.append(key)
            spk_status = json.dumps(merged, ensure_ascii=False) if merged else "(none)"
            if conflict:
                conflicts.append(f"{key}: " + "; ".join(conflict))
        print(f"{key}  [{info['file']}]\n    attach: {att_status}  speakers: {spk_status}")

    if args.dry_run:
        return 0

    manifest.save(m)
    changed = apply_keys(sorted(set(tagged) | set(uploads)), rebuild_viewer=True)
    archive_git.deliver({"title": f"import: GPT Pro thread ({len(uploads)} recordings)"})
    if conflicts:
        print("\nconflicts (manual tags kept):\n  " + "\n  ".join(conflicts))
    print(f"\ndone: {len(uploads)} attachments processed, "
          f"{len(tagged)} entries tagged, {changed} transcripts rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
