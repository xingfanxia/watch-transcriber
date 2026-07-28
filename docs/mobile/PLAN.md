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

## MOBILE-3: Mobile UX adaptation (same template, responsive)

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

iOS: bundle id `ai.ax.echowall`, ASC app record via API, Apple Distribution
cert + App Store provisioning profile (API-first, web fallback), build +
upload to TestFlight via ASC API key; internal testing group. Android:
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

## Retro

(appended per phase)
