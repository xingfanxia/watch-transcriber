# MOBILE-0: EchoWall iOS + Android port (read-only, direct-pull sync)

Owner decisions (AX, 2026-07-28): sync = **direct-pull from existing backups**
(GitHub notes-repo tarball + R2 audio, read-only tokens on device);
scope = **read-only browser** (manager ops stay on desktop);
distribution = **TestFlight + signed APK on GitHub Releases**.

Autonomous defaults (logged, reversible): audio = stream-on-demand with LRU
disk cache + per-recording offline pin; tokens = GitHub fine-grained PAT
(contents:read on watch-transcriber-data) + R2 read-only API token, stored in
iOS Keychain / Android Keystore via the Tauri stronghold/keychain plugin —
never in the app bundle or public repo.

Red lines: personal data and tokens never enter the public repo; demo data
(`scripts/demo/make_demo_data.py`) is the only archive that ever appears in
simulators, screenshots, or store/TestFlight metadata.

## Open unknowns (risk-ordered)

1. **Serving mechanism on mobile** — desktop uses a loopback axum server (HTTP
   Range for audio seek). iOS/Android may prefer Tauri's asset protocol; Range
   support there is unverified. MOBILE-1 answers this; fallback is running the
   same axum listener on 127.0.0.1 inside the app process (allowed on both
   platforms).
2. **Viewer reuse vs fork** — `deliveries/viewer_template.html` is a 3-pane
   desktop layout. Preferred: one template, responsive breakpoints (list⇄detail
   stack on narrow viewports), so desktop and mobile stay same-source. Fork
   only if responsive retrofit fights the desktop UX.
3. **Tarball incremental refresh** — GitHub tarball is full-archive each pull
   (notes corpus ≈ small MB, acceptable); switch to per-file contents API only
   if pull cost proves annoying.
4. **Apple Distribution cert via ASC API** — API can create `DISTRIBUTION`
   certs (only Developer ID is Account-Holder-gated). Verify in MOBILE-4;
   fallback is one more Account-Holder web step like the Developer ID cert.

## MOBILE-1: Feasibility spike (throwaway branch, findings land here)

`tauri ios init` + `tauri android init` on `desktop/`; boot the existing
viewer with demo data on iOS simulator + Android emulator.

- Accept: viewer renders and audio plays **with seek** on both simulators;
  serving mechanism decided and recorded under `## Retro`; go/no-go on
  responsive-retrofit vs fork recorded.

## MOBILE-2: Sync core (Rust, in `desktop/src-tauri`)

Token entry UI (minimal settings pane in the bootstrap page) → Keychain/
Keystore; pull pipeline: tarball download+extract → local `data/` in app
sandbox → manifest-driven R2 audio streaming with Range + LRU cache (cap
~500MB) + pin-offline per recording; manual refresh + pull-to-refresh.

- Accept: fresh install → paste two tokens → full archive browsable within
  60s on Wi-Fi; airplane mode → notes + pinned audio fully usable; tokens
  survive app restart; `git ls-files` leak scan still clean.

## MOBILE-3: Dedicated mobile layout (redesign, not retrofit)

**AX direction (2026-07-28): mobile gets its own purpose-designed layout** —
not a squeezed desktop retrofit. Same data payload, tokens, and render logic;
the layout tier is designed for portrait touch from scratch. Process: 2-3
divergent layout mockups rendered at phone viewport with demo data →
`[TASTE FORK]` for AX to pick → implement the winner. Also add a `<meta
viewport>` (mobile WebKit currently renders at ~980px virtual width).

**Light/dark switch (AX, 2026-07-28): manual theme toggle on BOTH desktop and
mobile** — one implementation in the shared template: toggle control +
`data-theme` attribute override + localStorage persistence; system preference
remains the default until the user flips it.

Design source of truth: the graphite token set already in
`viewer_template.html` (`--bg:#1a1a1a`, accent `#6cb0f5`, speaker palette,
facepiles, human-unit durations) — mobile reuses the same CSS vars and
signature elements; no new visual language.

Mobile IA (narrow viewport): one primary surface per screen —

```
[List] day-grouped rows (time · title · facepile · dur)   ← what you see first
   ├─ search field pinned top; 分类/说话人 filters → bottom sheet
   └─ tap row → [Detail] full-screen push (摘要|附注|转写 tabs), swipe-back
[Sync status] one-line pill under the title bar (synced ✓ / syncing / failed)
```

Interaction states (new on mobile, desktop never needed them):

| Surface | Loading | Empty | Error | Offline |
|---|---|---|---|---|
| First run | — | token setup page: two labeled paste fields + "从 README 获取 token" link; validate on paste → "✓ 已连接 · N 条录音" | inline per-field: 401 → "token 无效", 404 → "repo 访问被拒" | "需要网络完成首次同步" |
| Sync refresh | pill "同步中…" (list stays usable) | — | pill "同步失败 · 点击重试" (stale data stays) | pill "离线 · 上次同步 <time>" |
| Audio play | buffer spinner on play button | — | toast "音频拉取失败 · 重试" | uncached: player disabled + "离线未缓存"; pinned: plays normally |

A11y: player controls get VoiceOver/TalkBack labels; tap targets ≥44pt;
existing contrast tokens already tuned (#a8a8a8 floor); respect
prefers-reduced-motion for sheet/push transitions.

- Accept: chrome-devtools `emulate` sweep (iPhone 14 Pro / Pixel 7 / iPad)
  screenshots PASS for list/detail/filter-sheet/first-run/offline states;
  desktop viewer unchanged at 1440px (before/after screenshot diff);
  tabs/keyboard shortcuts still work on desktop.

## MOBILE-4: Distribution

iOS: bundle id stays `ai.ax.watch-transcriber` (AX 2026-07-29: invisible to
users, display name is EchoWall; changing later = new app record), ASC app
record, Apple Distribution cert + App Store provisioning profile (API-first,
web fallback), build + upload to TestFlight via ASC API key; internal testing
group. Android:
generate upload keystore → `~/creds/apple`-style `~/creds/android/`, signed
APK attached to GitHub Releases by the existing release workflow (new job).
App icons + splash from the 回音壁 mark (regenerate the icons/ set at mobile
sizes; TestFlight metadata + screenshots use demo data only). Token hygiene
documented: fine-grained PAT max-expiry 1yr → rotation note in ~/creds/README
and in-app "token 已过期" error path (from MOBILE-3 state table).

- Accept: TestFlight build installable on AX's iPhone; APK installs on a
  physical Android device; both CI lanes green on a `v*` tag; secrets only in
  GH Actions secrets + ~/creds (documented in both READMEs + ~/creds/README).

## MOBILE-5: Docs + wrapup

Bilingual READMEs (mobile section: screenshots from demo data, token setup
guide), CLAUDE.md agent notes (mobile build/verify commands), memory sync,
release notes.

- Accept: fresh-agent test — README alone suffices to build+run both mobile
  targets; neat-freak backstop returns zero findings.

## Deferred (considered, out of v1)

- QR-code token handoff (desktop app displays QR encoding both tokens; phone
  scans → zero typing). v1 = manual paste with validation; revisit if setup
  friction proves real.
- Auto background sync — v1 syncs on launch + pull-to-refresh only.
- Write-back (tags/attachments/delete), recording-upload, store listing —
  explicitly out per AX's v1 scope decision.

## Design review verdict (2026-07-28, autonomous per big-task Phase -1)

IA 8/10 · states 8/10 · journey 8/10 · slop-risk 8/10 (reuses established
graphite identity) · system-alignment 9/10 (viewer_template vars ARE the
design system) · responsive/a11y 8/10 · unresolved decisions: 0 critical,
3 deferred above. Plan is design-complete for implementation.

## Autonomous decisions (MOBILE-2, 2026-07-28)

1. **Secure store = keyring v4 ecosystem** (`keyring-core` + `apple-native-keyring-store`
   protected(iOS)/keychain(macOS) + `android-native-keyring-store` Keystore-encrypted
   SharedPreferences) — no hand-rolled JNI. Tokens = one JSON blob, entry
   `ai.ax.echowall`/`sync-tokens`, iOS access-policy `after-first-unlock`.
2. **R2 access = SigV4 S3 GET/HEAD** against `<account>.r2.cloudflarestorage.com`
   (region `auto`, UNSIGNED-PAYLOAD), no broker service. Test creds derived from
   the existing Cloudflare API token: access_key_id = token id, secret =
   sha256(token) — verified live with a HEAD 200. Real-device docs will say
   "create an R2 API token (Object Read only)" which shows exactly this pair.
3. **Data repo now commits `index.html` + `marked.min.js`** (`.gitignore`
   flipped, a3f0e36): the mobile app pulls the built viewer from the tarball
   instead of re-implementing viewer.py's payload builder in Rust. Pipeline's
   archive_git carries the page along on every delivery.
4. **Read-only gate = `?m=1` landing URL** — `CAN_EDIT = http && !IS_APP_MOBILE`;
   deterministic at first render, no async probe flicker.
5. **Pin = audio file exists under data/** (pull is overlay-only so pins
   survive; LRU eviction walks only `audio-cache/`). Pin from cache = rename,
   not re-download.
6. **Deletions linger on mobile until re-setup** — overlay pull never deletes
   local files; manifest drives the UI, so deleted recordings vanish from the
   list, orphan files stay on disk. Acceptable v1; noted for a future
   `--prune` pass.
7. **Sim/testing credentials** = local `gh auth token` + derived cf_token pair,
   entered through the real setup UI only — never committed, never bundled.
8. **Android ndk-context bootstrap**: Tauri v2 does NOT initialize `ndk-context`
   (only tao/android-activity do, and Tauri uses neither on Android), so
   `android-native-keyring-store` panicked at startup. Fix = its own JNI export
   is statically linked into `libdesktop_lib.so`; a hand-added
   `io/crates/keyring/Keyring.kt` + a `Keyring.initializeNdkContext()` call in
   `MainActivity.onCreate` **before** `super.onCreate()` hands over the context.
   Rust side wraps `Store::new` in `catch_unwind` so a regression degrades to a
   setup-page error instead of a crash loop.
9. **TLS = bundled webpki roots via `use_preconfigured_tls`** (shared
   `r2::http()` client). reqwest 0.13's `rustls` feature routes cert checks
   through rustls-platform-verifier, which on Android needs JNI init + a
   Gradle Kotlin component (`rustls-platform-verifier-android`) and panicked
   mid-request. Two known hosts -> Mozilla roots everywhere, zero platform
   glue. Every HTTP call site must use `r2::http()` — a stray
   `reqwest::Client::new()` reintroduces the Android panic.

## Retro

### MOBILE-1 (2026-07-28, in progress)

**iOS: PASS.** `tauri ios init` + dev build worked with zero project changes
(scaffold already had `mobile_entry_point`). On iPhone 17 Pro simulator:
bootstrap page rendered → demo data injected into sandbox
(`$HOME/projects/side-projects/watch-transcriber/data` — HOME maps to the app
container) → live-sync auto-entered the viewer (sidebar, categories, speaker
colors, day groups all working). **Serving decision: axum loopback works
unchanged on iOS** — `GET /index.html` 200, `Range: bytes=0-99` → **206**, so
audio seeking holds; no asset-protocol migration needed. Viewer renders the
desktop 3-pane layout crammed (expected — MOBILE-3), and followed the
simulator's light appearance (template has a light fallback; decide dark-first
vs follow-system in MOBILE-3).

**Android: PASS after one patch.** `data_dir()`'s `$HOME` fallback would panic
(no HOME in Android app processes) — fixed with a `#[cfg(mobile)]` block in
setup that resolves `app.path().app_data_dir()` and routes it through the
existing `WATCH_TRANSCRIBER_DATA` env check. Verified on the mio_api36_pixel8
emulator: viewer fully renders (narrow viewport stacks the desktop panes
vertically — usable but MOBILE-3 replaces it), `Range: bytes=0-99` → 206.
Android facts: `app_data_dir()` = the **package root** `/data/data/<pkg>`
(not `files/`); package name is `ai.ax.watch_transcriber` (dashes →
underscores); demo-data injection = `tar | adb shell run-as <pkg> tar -x`.

**⚠️ Release landmine:** `gen/android/app/build.gradle.kts` sets manifest
placeholder `usesCleartextTraffic` **false for release** (true only in debug)
— a release APK will refuse the loopback HTTP URL and white-screen. MOBILE-4
must flip it (or ship a network-security-config allowing 127.0.0.1 only).

### MOBILE-3 (2026-07-28, DONE — verified iPhone 17 Pro sim + Pixel 8 emulator)

Implemented 时间流 (variant A, AX's taste-fork pick) as a mobile layer in the
shared `viewer_template.html`: ≤719px breakpoint — full-screen list (brand +
search + horizontal filter chips), detail as a pushed screen (返回 button,
left-edge swipe, system back via pushState/popstate), bottom-docked player
(44pt), mini-player bar over the list while audio plays; manual light/dark
toggle on both desktop and mobile (`data-theme` on `<html>` +
localStorage(`echowall-theme`), beats the system media query both ways —
speaker hues re-render on change). Desktop 1440px verified pixel-unchanged;
22 tests + ruff green.

**Root cause of the day — `WebviewWindowBuilder.inner_size(1360, 900)` leaks
into the MOBILE webview's CSS viewport** (WKWebView renders ~1360px wide,
media queries never match, phone shows the desktop grid even though the
viewport meta is correct). Fix: `#[cfg(not(mobile))]` on
`inner_size`/`min_inner_size`. Also: WKWebView caches served pages hard —
after template changes, uninstall/reinstall (or bump) before judging; the
iOS app container UUID rotates on reinstall (re-resolve via
`simctl get_app_container` every time); with the mobile data-dir patch the
iOS data path is `<container>/Library/Application Support/<bundle-id>/data`.

**Emulator quirks:** this AVD needs `-gpu swiftshader_indirect` (hardware GPU
run showed chromium "tile memory limits exceeded" and a pure-white webview);
default snapshot restore can resurface another project's session — cold-boot
and reinstall before judging anything. The display pipeline can also die
mid-session (screencap turns pure black even on the LAUNCHER) — check a
known-good screen's brightness before blaming the app; cold restart fixes it. Tauri injects its init scripts twice on
Android ("Cannot redefine property" console errors) — benign for the viewer,
keep an eye on it.

**Sim quirks:** `tauri ios dev` does NOT boot the target simulator (fails
`simctl install` with SimError 405 on a Shutdown device) — boot it first.
Piping tauri dev output through `tail` buffers everything invisibly — always
redirect straight to a log file.

### MOBILE-2 (2026-07-29, DONE — verified end-to-end on both simulators with the real archive)

Sync core shipped: `secrets.rs` (keyring v4: iOS protected Keychain / Android
Keystore-encrypted SharedPreferences / macOS keychain), `r2.rs` (SigV4 signing
+ streaming Range proxy + background cache fill + 500MB LRU eviction + shared
webpki-roots reqwest client), `sync.rs` (tarball overlay pull, /api/sync/*
surface, data→cache→R2 fallback serving), `setup.html` token page; viewer
gained the `?m=1` mobile sync layer (pill states, pin button, offline player
states, toast) with `CAN_EDIT` off. Unit tests: SigV4 AWS vector + signed-GET
shape + tarball extract (prefix strip, `.git` skip, pin survival).

**Verified with real tokens through the real setup APIs** (nothing bundled):
- iOS sim: first-run setup → tokens validated + saved to Keychain → full
  93-recording archive synced in ~3s → viewer entered; Range `bytes=0-99` →
  206 via R2 proxy; second request served from warmed cache; pin → moves into
  data/; app relaunch reloads tokens from Keychain and launch-syncs.
- Android emu: same flow through Keystore; **airplane mode**: refresh →
  `offline` state with stale archive intact, notes 200 from local, pinned
  audio 206, unpinned 502 (viewer shows 离线未缓存); airplane off → recovers.
- Desktop 1440: pixel-identical 3-pane, editing intact, no sync UI.

**Two Android-only crashes found and fixed** (details in Autonomous decisions
#8/#9): Tauri never initializes ndk-context → Keystore store panicked (fixed
with the hand-added Kotlin bootstrap + catch_unwind); reqwest's
rustls-platform-verifier panics without its Gradle component (fixed by
switching every HTTP call to the shared `r2::http()` webpki-roots client).
**Watcher gotcha:** `tauri android dev`'s file watcher deploys can leave the
OLD process running (same port!) — `adb shell pidof` + `am force-stop` before
judging a redeploy, and confirm the fix behavior changed.

Not covered (deliberate): mid-flight token-expiry (401 after a valid save)
only exercised at the classify() unit level — fabricating it would mean
revoking the live test token; UI path exists (pill → 重新配置 → /setup).
