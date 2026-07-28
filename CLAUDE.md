# watch-transcriber — agent notes

Voice Memos → 妙记 STT → pluggable deliveries → local archive + 回音壁 EchoWall
desktop app. Feature docs live in `README.md` / `README.zh.md` (bilingual —
**edit both**); this file is only what an agent needs before touching code.

## Red line: this repo is PUBLIC

`data/` (notes, transcripts, audio, manifest, speakers.json), `.env`, and
`state/` are personal and gitignored — they must NEVER be committed or pushed
here. `data/` is its own nested git repo with a PRIVATE remote; audio backs up
to a private R2 bucket. Run a leak scan (`git ls-files | grep -E "^data/|\.env$|^state/"`
must be empty) before any push.

## Verify

```bash
venv/bin/python3 -m pytest tests/ -q      # 22 tests
ruff check deliveries/ scripts/ tests/ transcribe.py
cd desktop/src-tauri && cargo check       # Rust shell
venv/bin/python3 transcribe.py --doctor   # config/env sanity
```

## Contracts that are easy to break

- **`DELIVERY_TARGETS` order is load-bearing**: `local_archive, audio_archive,
  manifest, viewer, archive_git, r2_backup` — later ones read the earlier
  ones' on-disk output.
- **Archive naming has ONE source**: `deliveries/__init__.py` (`slug`,
  `archive_root`, `parse_note_dt`, `recording_stem`). The `.md`/`.m4a` pair
  and the manifest all derive the same `HHMMSS-<slug>` stem — never re-derive
  it locally.
- **`data/manifest.json` is the state authority** (key `"YYYY-MM-DD HHMMSS"`).
  User-authored fields (`speakers`, `speakers_applied`, `attachments`) must
  survive reprocess — `manifest.deliver()` merges them; keep it that way.
- **Speaker tags rewrite transcript files** via `scripts/ops/apply_speakers.py`
  (reversible through `speakers_applied`). Never rewrite labels ad hoc.
- **Deletion has ONE path**: `scripts/ops/delete_recording.py` (the app's
  `/api/delete` calls it). Don't hand-delete archive files.
- **Gemini model id comes from `.env`** (`GEMINI_MODEL`); never hardcode.
  Summarize JSON can fail 3/3 attempts on some transcripts — the plain-text
  title fallback in `transcribe.py` exists for that; don't remove it.

## Desktop app

`desktop/` — see `desktop/README.md`. The webview page is generated from
`deliveries/viewer_template.html`; after editing the template run
`venv/bin/python3 -m deliveries.viewer` and reload/relaunch the app
(template-only changes don't trigger the page's auto-refresh, which watches
`manifest.json`).

## Releasing the app

Push a `v*` tag → `.github/workflows/release.yml` builds the universal dmg
and uploads it to the GitHub Release (idempotent `--clobber`). Builds are
**ad-hoc signed** — there is no Developer ID certificate (keychain only has an
Apple Development cert); release notes and README must keep the Gatekeeper
"Open Anyway" / `xattr -cr` instructions until signing + notarization lands.
Bump `desktop/src-tauri/tauri.conf.json` `version` before tagging.
