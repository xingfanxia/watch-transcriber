"""Obsidian Git delivery — commits a markdown note to a GitHub repo."""

import os
import base64
import json
import urllib.request
from datetime import datetime


def deliver(note: dict) -> bool:
    repo = os.environ.get("OBSIDIAN_REPO")
    token = os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        print("[delivery:obsidian_git] OBSIDIAN_REPO and GITHUB_TOKEN required")
        return False

    # Deterministic path keyed on the recording timestamp: AI titles vary
    # between reprocess runs and the GitHub contents API can't rename, so the
    # descriptive title lives in the markdown H1 / commit message instead.
    try:
        dt = datetime.fromisoformat(note.get("timestamp", ""))
    except ValueError:
        dt = datetime.now()
    path = f"Voice Notes/{dt.strftime('%Y-%m-%d')}/{dt.strftime('%H%M%S')}-voice-note.md"

    content_b64 = base64.b64encode(note["markdown"].encode("utf-8")).decode("ascii")

    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    data = json.dumps({
        "message": f"voice note: {note['title']}",
        "content": content_b64,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 201):
                print(f"[delivery:obsidian_git] committed to {repo}/{path}")
                return True
    except urllib.error.HTTPError as e:
        print(f"[delivery:obsidian_git] error {e.code}: {e.read().decode()[:200]}")
    return False
