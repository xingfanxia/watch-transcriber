"""Feishu/Lark delivery — creates a document via lark-cli.

Requires lark-cli installed and authenticated:
  npm install -g @larksuite/cli
  lark-cli config init
  lark-cli auth login --recommend

Config (in .env):
  FEISHU_FOLDER_TOKEN  — optional, parent folder token
  FEISHU_WIKI_SPACE    — optional, wiki space ID (use "my_library" for personal)
  FEISHU_DOC_OWNER_ID  — optional, user open_id; when set, doc ownership is
                         transferred to this user after creation (docs are
                         otherwise owned by the lark-cli bot). The doc stays in
                         FEISHU_FOLDER_TOKEN's folder across the transfer, and
                         the bot keeps enough permission to manage it later.
"""

import json
import os
import shutil
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
    # Existence check only — never spawn lark-cli for it: `--version` phones
    # home for update checks and a slow network turns that into a spurious
    # delivery failure (observed 2026-07-24: 5s timeout → doc never created).
    if not shutil.which("lark-cli"):
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

    # Best-effort ownership transfer: delivery success is judged on doc
    # creation; a failed transfer is logged loudly but never fails the note.
    # With a valid USER token lark-cli's auto identity creates the doc AS the
    # user, so ownership is often already correct — check first and only
    # transfer when the bot token created it (user token expired/absent).
    owner = os.environ.get("FEISHU_DOC_OWNER_ID", "")
    if owner and doc_url:
        token = doc_url.rsplit("/", 1)[-1]
        meta = subprocess.run(
            ["lark-cli", "api", "POST", "/open-apis/drive/v1/metas/batch_query",
             "--data", json.dumps({"request_docs": [{"doc_token": token, "doc_type": "docx"}],
                                   "with_url": False})],
            capture_output=True, text=True, timeout=30,
        )
        try:
            metas = json.loads(meta.stdout)["data"]["metas"]
            if metas and metas[0].get("owner_id") == owner:
                print(f"[delivery:feishu] doc already owned by {owner}")
                return True
        except (json.JSONDecodeError, KeyError, TypeError):
            pass  # owner unknown — fall through and attempt the transfer
        # --as bot is required: the doc's owner is the bot, and only the owner
        # can transfer. lark-cli's "auto" identity prefers a valid USER token,
        # which fails with 1063002 Permission denied (observed 2026-07-21+ once
        # the user re-authorized and their token became valid).
        xfer = subprocess.run(
            ["lark-cli", "api", "POST",
             f"/open-apis/drive/v1/permissions/{token}/members/transfer_owner",
             "--params", '{"type":"docx"}', "--as", "bot",
             "--data", json.dumps({"member_type": "openid", "member_id": owner})],
            capture_output=True, text=True, timeout=30,
        )
        if xfer.returncode == 0 and '"ok": true' in (xfer.stdout or ""):
            print(f"[delivery:feishu] ownership transferred to {owner}")
        else:
            print(f"[delivery:feishu] WARN: owner transfer failed: "
                  f"{(xfer.stdout or xfer.stderr)[:200]}")
    return True
