"""Archive git delivery — auto-commit the data/ repo after each recording.

data/ is its own LOCAL git repository (the project repo is public on GitHub,
so personal notes/audio are gitignored there and must never reach it; this
nested repo has no remote unless the user adds a PRIVATE one deliberately).
data/.gitignore keeps audio and generated files out, so a commit is just the
new/changed notes + manifest.json. No-op when there is nothing to commit or
when data/ isn't a git repo.
"""

import subprocess

from . import archive_root

# Hard floor for what the nested data repo may ever contain: no audio, no
# generated output. Written idempotently before every commit so a fresh
# `git init` in data/ can never sweep multi-GB recordings into git history
# (append-only — one later remote push would ship them somewhere).
GITIGNORE = """\
*.m4a
*.qta
*.tmp
.DS_Store
by-topic/
daily.md
daily.html
index.html
"""


def deliver(note: dict) -> bool:
    root = archive_root()
    if not (root / ".git").exists():
        print(f"[delivery:archive_git] skipped — {root} is not a git repo")
        return True
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE, encoding="utf-8")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=60,
        )

    git("add", "-A")
    if not git("status", "--porcelain").stdout.strip():
        print("[delivery:archive_git] nothing to commit")
        return True
    r = git("commit", "-m", f"add: {note['title']}")
    if r.returncode != 0:
        print(f"[delivery:archive_git] commit failed: {r.stderr.strip()[:200]}")
        return False
    print(f"[delivery:archive_git] committed: {note['title']}")

    # Push if a remote exists (private backup repo). Offline is fine — the
    # commit is safe locally and the next successful push carries it along.
    if git("remote").stdout.strip():
        p = git("push")
        if p.returncode != 0:
            print(f"[delivery:archive_git] push failed (will retry next run): "
                  f"{p.stderr.strip().splitlines()[-1][:120] if p.stderr.strip() else 'unknown'}")
        else:
            print("[delivery:archive_git] pushed")
    return True
