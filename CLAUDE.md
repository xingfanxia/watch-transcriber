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
cd desktop/src-tauri && cargo check && cargo test   # Rust shell + sync-core tests
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

README screenshots (`docs/screenshots/`) come from a fabricated demo archive —
regenerate with `scripts/demo/make_demo_data.py <dest>` + `shoot_screenshots.py`
(usage in the latter's docstring). Never screenshot real `data/`.

## Mobile (iOS / Android)

Same crate, `#[cfg(mobile)]` paths: read-only viewer + direct-pull sync
(`src/sync.rs` tarball pull, `src/r2.rs` SigV4 streaming + LRU cache,
`src/secrets.rs` Keychain/Keystore tokens, `src/setup.html` first-run).
Mobile landing URL is `/index.html?m=1` — that param IS the read-only/sync-UI
switch in the viewer template. Dev: `npm run tauri ios dev` (boot the sim
first) / `npm run tauri android dev`. Landmines, all learned the hard way
(details in `docs/mobile/PLAN.md` Retro + Autonomous decisions):

- **Every HTTP call goes through `r2::http()`** — a bare
  `reqwest::Client::new()` panics on Android (rustls-platform-verifier needs
  JNI init we deliberately don't do; the shared client uses webpki roots).
- **`gen/android/.../io/crates/keyring/Keyring.kt` is hand-added** and its
  `initializeNdkContext` call in `MainActivity.onCreate` must stay BEFORE
  `super.onCreate()` — without it the Keystore store panics at startup.
  Proguard keeps for that class live in `app/proguard-rules.pro`.
- **Android release keeps `usesCleartextTraffic=true`** (loopback-only
  serving; flipping it back white-screens release builds).
- **`WebviewWindowBuilder.inner_size` stays `#[cfg(not(mobile))]`** — on
  mobile it leaks into the CSS viewport and the phone renders desktop layout.
- **The private data repo commits built `index.html` + `marked.min.js`** —
  mobile serves the page from the tarball; never re-implement the viewer
  builder in Rust.
- `gen/apple/ExportOptions.plist` must keep the manual-signing profile map;
  tauri's own export step writes one without it and fails.
- `tauri android dev` redeploys can leave the OLD process running on the old
  port — `adb shell pidof` + `am force-stop` before judging a change.
- **Provision credentials at runtime** through the existing first-run setup
  screen (`desktop/src-tauri/src/setup.html`) and secure-store implementation
  (`desktop/src-tauri/src/secrets.rs`, Keychain/Keystore). Do not supply real
  API keys or owner tokens through compile arguments, `ECHOWALL_SEED_*`, or
  `.cargo/config.toml`; gitignored build inputs can still embed secrets in
  distributable binaries. Never print extracted binary secrets as verification.
  Version bumps for ad-hoc uploads go in `tauri.conf.json` (tauri rewrites
  the Info.plist from it every build).

## Releasing the app

Push a `v*` tag → `.github/workflows/release.yml` runs three lanes:
macOS universal dmg (**Developer ID signed + notarized**) → Release; iOS App
Store ipa (Apple Distribution + "EchoWall App Store" profile) → TestFlight
upload that skips cleanly until the ASC app record exists; Android signed
universal APK → Release. Secrets: `APPLE_*`, `APPLE_DIST_*`,
`APPLE_PROVISIONING_PROFILE`, `ANDROID_KEYSTORE(_PASSWORD)`; local kits in
`~/creds/apple/` + `~/creds/android/` (see `~/creds/README.md`). Version bump
before tagging touches THREE spots: `tauri.conf.json` `version`,
`gen/apple/desktop_iOS/Info.plist`, `gen/apple/project.yml` (Android reads
tauri.properties at build). Local signed build: same `APPLE_*` env vars on
`npm run tauri build`.
