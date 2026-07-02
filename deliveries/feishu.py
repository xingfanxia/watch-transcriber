"""Feishu/Lark delivery — creates a document via lark-cli.

Requires lark-cli installed and authenticated:
  npm install -g @larksuite/cli
  lark-cli config init
  lark-cli auth login --recommend

Config (in .env):
  FEISHU_FOLDER_TOKEN  — optional, parent folder token
  FEISHU_WIKI_SPACE    — optional, wiki space ID (use "my_library" for personal)
"""

import json
import os
import subprocess


def _parse_doc_url(stdout: str) -> str:
    """Extract doc_url from lark-cli JSON output."""
    try:
        data = json.loads(stdout)
        d = data.get("data", {})
        # v2 schema: data.document.url ; v1 (legacy): data.doc_url
        return d.get("document", {}).get("url", "") or d.get("doc_url", "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def deliver(note: dict) -> bool:
    try:
        subprocess.run(["lark-cli", "--version"], capture_output=True, timeout=5)
    except FileNotFoundError:
        print("[delivery:feishu] lark-cli not found. Install: npm install -g @larksuite/cli")
        return False

    title = note["title"]
    markdown = note["markdown"]

    # lark-cli docs +create is v2-only: --markdown was removed in favor of
    # --content with --doc-format markdown (v1 interface shut down). Pass the
    # body via --content - (stdin), no shell: avoids ARG_MAX limits on long
    # transcripts and shell-quoting/injection via title or content. (@file only
    # accepts a relative path inside the cwd, so stdin is the robust large payload.)
    cmd = [
        "lark-cli", "docs", "+create",
        "--title", title,
        "--doc-format", "markdown",
        "--content", "-",
    ]
    wiki_space = os.environ.get("FEISHU_WIKI_SPACE", "")
    folder_token = os.environ.get("FEISHU_FOLDER_TOKEN", "")
    if wiki_space:
        cmd += ["--parent-position", wiki_space]
    elif folder_token:
        cmd += ["--parent-token", folder_token]

    result = subprocess.run(cmd, input=markdown, capture_output=True, text=True, timeout=60)

    if result.returncode != 0:
        print(f"[delivery:feishu] error: {(result.stderr or result.stdout)[:500]}")
        return False

    doc_url = _parse_doc_url(result.stdout)
    if doc_url:
        note["feishu_doc_url"] = doc_url

    print(f"[delivery:feishu] created doc '{title}'")
    if result.stdout.strip():
        print(f"[delivery:feishu] {result.stdout.strip()}")
    return True
