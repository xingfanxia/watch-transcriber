"""Apple Notes delivery — creates a note via AppleScript."""

import os
import subprocess


def deliver(note: dict) -> bool:
    folder = os.environ.get("APPLE_NOTES_FOLDER", "Voice Transcripts")
    title = note["title"]
    # Apple Notes expects HTML body
    body = note["markdown"].replace("\n", "<br>")

    # Escape for AppleScript string literals (backslash, then double-quote)
    title_esc = title.replace("\\", "\\\\").replace('"', '\\"')
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
        make new note at targetFolder with properties {{name:"{title_esc}", body:"{body_esc}"}}
    end tell
    '''

    # Pipe script via stdin to avoid command-line argument length limits
    result = subprocess.run(
        ["osascript", "-"],
        input=script, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"[delivery:apple_notes] error: {result.stderr}")
        return False

    print(f"[delivery:apple_notes] created note '{title}' in folder '{folder}'")
    return True
