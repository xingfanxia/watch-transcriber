"""Apple Notes delivery — creates a note via AppleScript."""

import os
import subprocess


def deliver(note: dict) -> bool:
    folder = os.environ.get("APPLE_NOTES_FOLDER", "Voice Transcripts")
    title = note["title"]
    # Apple Notes expects HTML body
    body = note["markdown"].replace("\n", "<br>").replace('"', '\\"')

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
        make new note at targetFolder with properties {{name:"{title}", body:"{body}"}}
    end tell
    '''

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"[delivery:apple_notes] error: {result.stderr}")
        return False

    print(f"[delivery:apple_notes] created note '{title}' in folder '{folder}'")
    return True
