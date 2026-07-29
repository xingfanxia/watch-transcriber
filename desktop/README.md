# 回音壁 EchoWall (desktop + mobile shell)

Thin Tauri v2 shell over the archive the pipeline generates. A loopback axum
server serves `../data/` (HTTP Range → audio seeking) plus the manager APIs
(`/api/speakers`, `/api/attachments`, `/api/delete`, `/api/speaker-colors`);
the webview loads the same `data/index.html` the `viewer` delivery builds —
there is no second viewer implementation here.

The same crate builds the **iOS / Android** read-only companion
(`#[cfg(mobile)]`): `src/sync.rs` pulls the private notes repo as a GitHub
tarball into the app sandbox, `src/r2.rs` streams audio from R2 (SigV4,
Range, 500MB LRU cache, offline pin), `src/secrets.rs` keeps the two
read-only tokens in Keychain/Keystore, and the viewer runs in read-only+sync
mode via the `/index.html?m=1` landing URL. Token setup guide + install paths:
repo README `## Mobile`. Agent landmines: repo `CLAUDE.md` `## Mobile`.

```bash
npm install
npm run tauri dev            # run against ../data
npm run tauri build          # bundle EchoWall.app / .dmg
```

Prebuilt universal dmg on [GitHub Releases](https://github.com/xingfanxia/watch-transcriber/releases)
(`v*` tags auto-build via `.github/workflows/release.yml`; Developer ID
signed + notarized).

`WATCH_TRANSCRIBER_DATA` overrides the archive location; otherwise the app
walks up from its executable to find the enclosing clone's `data/`. On a
machine with no archive yet it shows a bootstrap page — see the repo README's
"EchoWall — the desktop client" section for the restore/first-run flows.
