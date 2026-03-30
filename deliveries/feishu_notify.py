"""Feishu IM notification — DMs you a summary after delivery via lark-cli.

Requires lark-cli with bot identity configured.

Config (in .env):
  FEISHU_NOTIFY_USER_ID — your open_id (ou_xxx), get it from: lark-cli auth status
"""

import os
import subprocess


def deliver(note: dict) -> bool:
    user_id = os.environ.get("FEISHU_NOTIFY_USER_ID")
    if not user_id:
        print("[delivery:feishu_notify] FEISHU_NOTIFY_USER_ID not set")
        return False

    try:
        subprocess.run(["lark-cli", "--version"], capture_output=True, timeout=5)
    except FileNotFoundError:
        print("[delivery:feishu_notify] lark-cli not found")
        return False

    # Build a concise notification message
    title = note["title"]
    summary = note.get("summary", "").split("\n\n")[0][:200]  # first language, truncated
    todos = note.get("todos", [])

    lines = [f"**{title}**", ""]
    if summary:
        lines.append(summary)
        lines.append("")
    if todos:
        lines.append("**Action Items:**")
        for item in todos:
            lines.append(f"- {item}")

    msg = "\n".join(lines)

    result = subprocess.run(
        [
            "lark-cli", "im", "+messages-send",
            "--as", "bot",
            "--user-id", user_id,
            "--markdown", msg,
        ],
        capture_output=True, text=True, timeout=15,
    )

    if result.returncode != 0:
        print(f"[delivery:feishu_notify] error: {result.stderr[:300]}")
        return False

    print(f"[delivery:feishu_notify] DM sent to {user_id}")
    return True
