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
import tempfile


def _parse_doc_url(stdout: str) -> str:
    """Extract doc_url from lark-cli JSON output."""
    try:
        data = json.loads(stdout)
        return data.get("data", {}).get("doc_url", "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def deliver(note: dict) -> bool:
    # Check lark-cli exists
    try:
        subprocess.run(["lark-cli", "--version"], capture_output=True, timeout=5)
    except FileNotFoundError:
        print("[delivery:feishu] lark-cli not found. Install: npm install -g @larksuite/cli")
        return False

    title = note["title"]
    markdown = note["markdown"]

    # Build command
    cmd = [
        "lark-cli", "docs", "+create",
        "--title", title,
        "--markdown", markdown,
    ]

    folder_token = os.environ.get("FEISHU_FOLDER_TOKEN")
    wiki_space = os.environ.get("FEISHU_WIKI_SPACE")

    if wiki_space:
        cmd += ["--wiki-space", wiki_space]
    elif folder_token:
        cmd += ["--folder-token", folder_token]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        if "argument list too long" in result.stderr.lower() or len(markdown) > 50000:
            return _deliver_via_stdin(note, title, markdown, folder_token, wiki_space)
        print(f"[delivery:feishu] error: {result.stderr[:500]}")
        return False

    doc_url = _parse_doc_url(result.stdout)
    if doc_url:
        note["feishu_doc_url"] = doc_url

    print(f"[delivery:feishu] created doc '{title}'")
    if result.stdout.strip():
        print(f"[delivery:feishu] {result.stdout.strip()}")
    return True


def _deliver_via_stdin(note: dict, title: str, markdown: str, folder_token: str | None, wiki_space: str | None) -> bool:
    """Fallback for very long content — write markdown to temp file and use --markdown @file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(markdown)
        tmp_path = f.name

    try:
        cmd = [
            "lark-cli", "docs", "+create",
            "--title", title,
            "--markdown", f"@{tmp_path}",
        ]
        if wiki_space:
            cmd += ["--wiki-space", wiki_space]
        elif folder_token:
            cmd += ["--folder-token", folder_token]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"[delivery:feishu] error: {result.stderr[:500]}")
            return False

        doc_url = _parse_doc_url(result.stdout)
        if doc_url:
            note["feishu_doc_url"] = doc_url

        print(f"[delivery:feishu] created doc '{title}'")
        return True
    finally:
        os.unlink(tmp_path)
