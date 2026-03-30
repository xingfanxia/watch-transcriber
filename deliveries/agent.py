"""Agent delivery — delegates to claude -p (or any CLI agent) for flexible output.

This is the most powerful delivery mode. By changing AGENT_DELIVERY_PROMPT,
you can send transcripts to any destination that Claude Code skills support:

  - Feishu docs: "use lark-doc skill to create a doc titled '{title}' ..."
  - Google Docs: "use gws-docs skill to create a doc titled '{title}' ..."
  - Slack:       "use slack skill to post to #voice-notes: {content}"
  - Email:       "use gws-gmail-send skill to email me the transcript..."
  - Custom:      any prompt that claude -p can handle
"""

import os
import subprocess
import tempfile


def deliver(note: dict) -> bool:
    prompt_template = os.environ.get("AGENT_DELIVERY_PROMPT")
    if not prompt_template:
        print("[delivery:agent] AGENT_DELIVERY_PROMPT not set")
        return False

    prompt = prompt_template.replace("{title}", note["title"]).replace(
        "{content}", note["markdown"]
    )

    # Write prompt to temp file to avoid shell escaping issues
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        result = subprocess.run(
            ["claude", "-p", "--input-file", prompt_file],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            print(f"[delivery:agent] claude -p failed: {result.stderr[:500]}")
            return False

        print(f"[delivery:agent] delivered via claude -p")
        return True
    except FileNotFoundError:
        print("[delivery:agent] 'claude' CLI not found in PATH")
        return False
    finally:
        os.unlink(prompt_file)
