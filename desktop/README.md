# AX语音Vault (desktop shell)

Thin Tauri v2 shell over the archive the pipeline generates. A loopback axum
server serves `../data/` (HTTP Range → audio seeking) plus the manager APIs
(`/api/speakers`, `/api/attachments`, `/api/delete`, `/api/speaker-colors`);
the webview loads the same `data/index.html` the `viewer` delivery builds —
there is no second viewer implementation here.

```bash
npm install
npm run tauri dev            # run against ../data
npm run tauri build          # bundle AX语音Vault.app / .dmg
```

`WATCH_TRANSCRIBER_DATA` overrides the archive location; otherwise the app
walks up from its executable to find the enclosing clone's `data/`. On a
machine with no archive yet it shows a bootstrap page — see the repo README's
"Desktop app" section for the restore/first-run flows.
