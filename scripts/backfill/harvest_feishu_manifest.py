#!/usr/bin/env python3
"""Harvest old Feishu doc title→URL pairs from state/transcriber.log.

The feishu delivery prints "created doc '<title>'" followed by the lark-cli
JSON response containing the doc URL. This pairs them into a manifest used to
delete the old timestamp-titled docs once the lark-cli app has drive:drive
scope (it currently does not — deletion is blocked, creation is not).

Note: the log has been truncated in the past, so this manifest is a lower
bound on the old docs that exist.
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOG = REPO / "state" / "transcriber.log"
OUT = Path(__file__).parent / "feishu_old_docs_manifest.json"

pairs = []
pending_title = None
for line in LOG.read_text(errors="replace").splitlines():
    m = re.search(r"\[delivery:feishu\] created doc '(.+)'$", line)
    if m:
        pending_title = m.group(1)
        continue
    if pending_title:
        u = re.search(r"https://[a-z0-9.]+/docx/([A-Za-z0-9]+)", line)
        if u:
            pairs.append({"title": pending_title, "url": u.group(0), "token": u.group(1)})
            pending_title = None

# Old-format docs only; keep the last URL per title (retries recreate docs,
# but earlier duplicates of the same title are still worth deleting — keep all).
old = [p for p in pairs if p["title"].startswith("Voice Note ")]
OUT.write_text(json.dumps(old, ensure_ascii=False, indent=2) + "\n")
print(f"{len(pairs)} doc creations in log, {len(old)} old-format entries → {OUT}")
uniq = len({p["title"] for p in old})
print(f"({uniq} unique old titles; duplicates are prior reprocess artifacts)")
