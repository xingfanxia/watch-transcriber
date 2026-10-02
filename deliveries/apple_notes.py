"""Apple Notes delivery — creates a note via AppleScript."""

import os
import re
import subprocess

# Apple Notes ignores CSS margins, so headings and paragraphs render with no
# vertical space between them. A blank line in Notes is an empty <div><br></div>.
_BLANK_LINE = "<div><br></div>"


def notes_body(html: str) -> str:
    """Add the blank lines Apple Notes needs between sections and paragraphs."""
    html = re.sub(r"(<h2>)", _BLANK_LINE + r"\1", html)
    return re.sub(r"(</p>\s*)(<p>)", r"\1" + _BLANK_LINE + r"\2", html)


def deliver(note: dict) -> bool:
    folder = os.environ.get("APPLE_NOTES_FOLDER", "Voice Transcripts")
    # Apple Notes expects an HTML body. Prefer the pre-rendered HTML (proper
    # headers, bullets, line-broken monospace transcript); fall back to a crude
    # markdown→<br> conversion only if it's missing. The body opens with the
    # title as <h1>, which Notes uses as the note's name; also setting `name`
    # would show the title twice.
    body = note.get("html") or note["markdown"].replace("\n", "<br>")
    body = notes_body(body)

    # Escape for AppleScript string literals (backslash, then double-quote)
    body_esc = body.replace("\\", "\\\\").replace('"', '\\"')

    script = f'''
    tell application "Notes"
        set targetFolder to missing value
        repeat with f in folders of default account
            if name of f is "{folder}" then
                set targetFolder to f
                exit repeat
            end if
        end repeat
        if targetFolder is missing value then
            set targetFolder to (make new folder at default account with properties {{name:"{folder}"}})
        end if
        make new note at targetFolder with properties {{body:"{body_esc}"}}
    end tell
    '''

    # Pipe script via stdin to avoid command-line argument length limits
    result = subprocess.run(
        ["osascript", "-"],
        input=script, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print("[delivery:apple_notes] create failed")
        return False

    print("[delivery:apple_notes] note created")
    return True
