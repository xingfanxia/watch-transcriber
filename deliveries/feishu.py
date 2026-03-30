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
    try:
        subprocess.run(["lark-cli", "--version"], capture_output=True, timeout=5)
    except FileNotFoundError:
        print("[delivery:feishu] lark-cli not found. Install: npm install -g @larksuite/cli")
        return False

    title = note["title"]
    markdown = note["markdown"]

    # Always write to temp file to avoid CLI arg length limits
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(markdown)
        tmp_path = f.name

    try:
        # Use shell to expand $(cat file) since lark-cli doesn't support @file
        folder_token = os.environ.get("FEISHU_FOLDER_TOKEN", "")
        wiki_space = os.environ.get("FEISHU_WIKI_SPACE", "")

        extra_args = ""
        if wiki_space:
            extra_args = f'--wiki-space "{wiki_space}"'
        elif folder_token:
            extra_args = f'--folder-token "{folder_token}"'

        cmd = f'lark-cli docs +create --title "{title}" --markdown "$(cat {tmp_path})" {extra_args}'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            print(f"[delivery:feishu] error: {result.stderr[:500]}")
            return False

        doc_url = _parse_doc_url(result.stdout)
        if doc_url:
            note["feishu_doc_url"] = doc_url

        print(f"[delivery:feishu] created doc '{title}'")
        if result.stdout.strip():
            print(f"[delivery:feishu] {result.stdout.strip()}")
        return True
    finally:
        os.unlink(tmp_path)
