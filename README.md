# watch-transcriber

[中文版](README.zh.md)

Cross-platform recording/import and Apple Watch voice transcription pipeline
(~¥2/hour of audio via 妙记), with the archive managed in **EchoWall** (回音壁).

> **Status:** the existing Mac Voice Memos watcher remains the proven default.
> App-owned capture/import for macOS, iOS, and Android is the current release
> target and remains in development until its physical-device matrix in
> [`docs/capture/PLAN.md`](docs/capture/PLAN.md) passes. Windows 11 parity is the
> following release; current Windows code and CI output are an unsupported
> technical preview, not a shipped product.

```
Apple Watch (Voice Memos) → iCloud Sync → Mac detects new .m4a
  → upload to Volcano TOS → 妙记 API — server-side transcription + diarization
    → Gemini title/summary → pluggable delivery (Apple Notes, Feishu, Obsidian, custom)
      → EchoWall desktop app (browse · play · tag speakers · manage)

EchoWall Record / Import → durable in-app Rust queue → direct Volcano TOS upload
  → 妙记 API → Gemini title/summary → in-app archive publisher

Apple Silicon macOS (explicit optional install) → MOSS/Metal or Whisper/Metal
  → local anonymous speakers → local Qwen3.8-27B summary
    → verified local archive; optional cloud backup syncs later
```

The new App path has no EchoWall processing server. Each user supplies their own
provider/archive credentials through explicit setup; values are stored in the
OS secure store and are never bundled with a release. The Python watcher remains
only as the proven Voice Memos compatibility path during rollout.

MOSS local processing is available on supported Apple Silicon Macs running
macOS 14 or later with at least 32 GiB unified memory. Open **MOSS 本地**, then
explicitly install its transcription, speaker and summary models. Imports offer
**MOSS 本地处理**, **Whisper 离线处理** and **云端处理** as separate choices.
You can save a default for future recordings; it survives App restarts and
does not change existing jobs. Without a default, recordings wait locally.
Unavailable local models or an unreadable preference never trigger an automatic
download or cloud fallback. Existing Whisper preferences are not converted to
MOSS; the App offers an explicit action to save an older page-local preference.
These settings apply only to recordings made in the App. The existing Apple
Watch → Voice Memos → automatic transcription service keeps running independently.

The practical quality target is useful English, Mandarin and mixed-language
meeting notes comparable to Feishu/Miaoji. Retained corpus and full local
source-engine evidence support this target; isolated difficult tails can still
fail with the original audio preserved for retry/export. These checks do not
claim that every historical sample or final installed release has passed.
See [current readiness and limitations](docs/capture/PLAN.md).
Current local macOS/Android/iOS build paths, hashes and installation limits are
in the [September 6 delivery notes](docs/capture/evidence/practical-closeout-2026-09-06.md).

**0.3.0 preview is available:** [Mac and Android downloads](https://github.com/xingfanxia/watch-transcriber/releases/tag/v0.3.0). Existing internal iOS testers can select **0.3.0 (3)** in TestFlight. Physical phone testing remains pending.

## EchoWall — the desktop client

The pipeline's output isn't a pile of markdown you never open again — the repo ships a desktop app (`desktop/`, Tauri v2) that turns the local archive into a browsable, playable, manageable voice vault:

| 摘要 — bilingual summary, key points, action items | 转写 — speaker-colored transcript, click-to-seek |
|---|---|
| ![Archive overview: summary tab with bilingual summary and speaker chips](docs/screenshots/01-overview.png) | ![Transcript tab with per-speaker colors and clickable timestamps](docs/screenshots/02-transcript.png) |
| **附注 — markdown attachments rendered in-app** | **说话人 — quick tagging + batch apply** |
| ![Attachments tab rendering a markdown analysis with tables and quotes](docs/screenshots/03-attachments.png) | ![Speaker picker with quick-select pills and batch apply](docs/screenshots/04-speaker-tagging.png) |

*Screenshots show fabricated demo data.*

- **Dark-mode archive UI** — AI titles, bilingual summaries, key points, and full diarized transcripts; search, topic + speaker filters, per-day rollups; tabbed detail pane (摘要/附注/转写, keys 1/2/3); click any transcript timestamp to seek the audio there. The page live-syncs when the pipeline delivers a new recording.
- **Speaker tagging** — click a chip to name `SPEAKER_N`, batch-apply across the current filter, pick per-person colors; stacked facepile avatars on every row. Tags live in `manifest.json` (`speakers`), survive reprocessing, auto commit+push to the private notes repo, and are written back into the note files' transcript labels (`scripts/ops/apply_speakers.py`, reversible via `speakers_applied`).
- **Markdown attachments** — paste or pick a `.md`/`.txt` per recording (an AI analysis of the conversation, meeting context, anything); stored under `data/<date>/<HHMMSS>-attachments/`, tracked in the manifest, rendered in-app. `scripts/ops/import_gpt_thread.py <export.json>` bulk-imports a ChatGPT export (all branches, three historical upload-filename formats): it attaches each recording's analysis and auto-extracts "who is SPEAKER_N" into tags (never overwriting manual ones). Idempotent.
- **Safe delete** — a two-step 删除 button uses the native Rust archive CAS to remove the note, App-owned audio, attachments, manifest entry, and R2 object, then publishes a recording tombstone so stale work cannot resurrect it. Historical R2 objects without App ownership metadata require a second explicit partial-delete confirmation and are reported as retained. Voice Memos originals and Apple Notes/飞书 copies are deliberately untouched; `scripts/ops/delete_recording.py` remains an explicit maintenance CLI.
- **Recoverable recording controls** — processing rows restore after relaunch and offer retry, cancel, Export Original, reprocess-from-transcript, and an explicitly confirmed local discard. Desktop recording stays alive when the window closes; the tray/menu bar shows source + timer with Pause/Continue and Stop, while Quit first closes and saves the active segment.
- **Optional end-to-end offline processing on Apple Silicon** — explicitly install the MOSS local model pack, or retain the separate Whisper fallback. Both provide anonymous speakers, Qwen3.8-27B `UD-Q4_K_XL` bilingual summaries and a verified local archive; the App shows download size and hardware requirements before installation. Rust owns durable processing and recovery, while bounded one-shot workers handle the models. Local completion makes no TOS, 妙记, Gemini, GitHub or R2 request. Optional cloud backup is a separate, explicit action.
- **Local-first, yours** — one Rust + Tauri app owns capture, import, durable processing, archive publication, and an authenticated in-process HTTP Range transport. Synced HTML/JavaScript is never executed. There is no EchoWall processing server, cloud account, or shared credential; data goes only to the private services you configure. `WATCH_TRANSCRIBER_DATA` overrides the Rust App archive location for source-checkout development; the legacy Python watcher uses `LOCAL_ARCHIVE_DIR`.
- **Fresh machine in minutes** — a standalone install stores its archive under the platform app-data directory. First launch can create a fully local archive without credentials, or accept scoped GitHub/R2 credentials plus explicit private destinations through a write-only native command. Empty archives are valid and continue into the App instead of trapping setup. No Python restore command or repository checkout is required by the App.

```bash
cd desktop
npm install
npm run tauri:dev:macos    # run against ../data with local-model sidecars
npm run tauri:build:macos  # local bundle; release signing uses CI credentials
```

Or skip the build: download the latest `EchoWall_*_universal.dmg` (Apple Silicon + Intel) from [**GitHub Releases**](https://github.com/xingfanxia/watch-transcriber/releases). Releases are built by CI on every `v*` tag (`.github/workflows/release.yml`).

> Release builds are **Developer ID signed and notarized by Apple**. CI verifies
> the universal App, then independently notarizes and staples the final DMG
> before upload.

## Mobile

EchoWall also runs on **iOS and Android** with the same Tauri shell and generated
viewer. Archive browsing/sync is the existing proven companion behavior.
App-owned, explicitly started microphone recording and native file/share import
are the new path: Android has a foreground-service recorder and SAF/share
intake; iOS has a background AVFAudio recorder and a Voice Memos/Files share
extension. They are distributed as preview features; physical phone lifecycle testing
remains pending. Editing (speakers,
attachments, delete) remains on desktop.

| 时间流 list + sync pill | Detail: tabs, player, offline pin | First-run token setup | Manual light/dark |
|---|---|---|---|
| ![Mobile list: day-grouped recordings with sync status pill](docs/screenshots/05-mobile-list.png) | ![Mobile detail: summary tab with bottom player and pin button](docs/screenshots/06-mobile-detail.png) | ![Token setup page with GitHub and R2 credential fields](docs/screenshots/07-mobile-setup.png) | ![Mobile list in light theme](docs/screenshots/08-mobile-light.png) |

*Screenshots show fabricated demo data.*

- **Direct-pull sync** — on launch (and on tap of the sync pill) the app downloads the notes repo tarball and overlays it into the app sandbox; the App renders validated archive data using its own compiled viewer template, rather than executing synced HTML or JavaScript. Sync states: 同步中 / ✓ 已同步 / 同步失败 / 离线 / token 已过期.
- **Audio: stream + cache + pin** — playback streams from R2 with HTTP Range (seek works), a 500MB LRU disk cache makes replays local, and the ↓ button on the player pins a recording's audio for offline. Offline: notes are always available, pinned audio plays, unpinned shows 离线未缓存.
- **Tokens live in the platform secure store** — iOS Keychain / Android Keystore, never in a file, never in this repo.
- **Recovery stays native** — iOS exports a verified non-empty document and
  Android writes the user-selected SAF `content://` destination; both re-open
  the result to verify size/hash and preserve the App copy on cancel/failure.

**Token setup** (first run asks for two credentials and the explicit private
repository/bucket destinations scoped to your own archive):

1. **GitHub fine-grained PAT** — scope it to only your private notes repo. Archive browsing needs Contents: Read-only; App-owned recording/import publication needs Contents: Read and write. Max expiry is 1 year — calendar the rotation.
2. **R2 API token** — scope it to only your audio bucket. Playback needs Object Read only; App-owned recording/import publication needs Object Read & Write. The dashboard shows the **Access Key ID** and **Secret Access Key**; your **Account ID** is on the R2 overview page.

Public builds contain no shared credentials. Read-only tokens remain valid for
viewer-only use; EchoWall must show processing as unconfigured until the
installation has the required user-owned write scopes.

**Install**: iOS via TestFlight (invite-based while the app record is pending) · Android via the signed `EchoWall_*_universal.apk` on [GitHub Releases](https://github.com/xingfanxia/watch-transcriber/releases) (sideload; built and signed by the same CI as the dmg).

Build from source (needs Xcode / Android SDK+NDK):

```bash
cd desktop
npm run tauri ios dev      # iOS simulator (boot it first)
npm run tauri android dev  # Android emulator
npm run tauri ios build -- --export-method app-store-connect   # App Store ipa
npm run tauri android build -- --target aarch64 x86_64 --apk # signed 64-bit APK
```

## Why This Approach

We researched and rejected several alternatives before landing on this design. Here's what we learned.

### Why not a custom Watch app?

A friend who built a custom watchOS recording app shared hard-won lessons:

- **watchOS networking is unreliable.** Battery management aggressively kills connections. Direct `URLSession` uploads from Watch to third-party APIs sound clean but fail in practice.
- **CloudKit as intermediary is painful.** The pipeline becomes: Watch → iPhone (proxy) → CloudKit → iPhone download → process. Four hops to get audio off the watch.
- **30-second chunking creates a different problem.** Short segments survive interruptions (phone calls mid-recording), but a day of recording generates hundreds of files that overwhelm CloudKit.
- **Significant development investment.** watchOS restrictions change subtly between versions. Each update requires re-testing on physical hardware — simulators don't reproduce real behavior.

> "If you just use the recorder and manually process afterwards, the watch experience is fine. If you want an automated workflow, I haven't found a good engineering approach yet."

### Why not Apple's built-in transcription?

Voice Memos in iOS 18+ has built-in transcription, but:

- **No code-switching support.** It's single-language — set your device to Chinese and English gets garbled, or vice versa. Useless for bilingual speakers.
- **No speaker diarization.** Single text block with no speaker labels.
- **~80-90% accuracy** vs Gemini 3 Pro's 7.2% MER on mixed Chinese-English benchmarks.

### Why keep Voice Memos + launchd?

- **It is the compatibility fallback, not the new processing architecture.**
  Voice Memos already handles background recording, interruptions, long files,
  and iCloud sync; the existing watcher stays available while app-owned capture
  proves the same reliability.
- **Action Button works.** You can map Voice Memos to the Ultra's Action Button for one-press recording.
- **Recordings sync instantly.** Files appear at a known path on your Mac within seconds.
- **launchd `WatchPaths`** remains the Mac-only detector for this legacy entry
  path. iPhone apps cannot scan the private Voice Memos container; on iPhone,
  use Share → EchoWall or record directly in EchoWall.

### STT: Why 妙记 (Volcano Lark Minutes)?

**妙记 (`volc.lark.minutes`) is the default** (`STT_PROVIDER=lark`). It does speaker diarization **server-side in a single call** — no chunking, no cross-chunk speaker stitching. Verified across 5 real recordings (2026-06): 妙记 returned the exact speaker count on every two-person conversation (2/2/2/2), where chunk-stitched Gemini/OpenAI and the raw Doubao auc models all over-counted (3–5 speakers); it also swallowed a 3.45-hour file in one pass. Diarization, not transcription, was the real hard half — and 妙记 treats it as a first-class server-side job instead of a stitching afterthought.

The proven legacy watcher route is intentionally small:

```text
detect a new .m4a → upload a compact copy to Volcano TOS → call the 妙记 API
```

妙记 needs a publicly-fetchable FileURL. The legacy Python watcher converts the complete recording to a small 16kHz-mono MP3, puts it in TOS, hands 妙记 a presigned URL, and deletes the temporary object after the job. The new App path does not use Python or ffmpeg: Rust/Symphonia fully validates `.m4a`, `.mp3`, or `.wav`, uploads it under the matching extension/MIME, then runs the same 妙记 → Gemini text-summary route. Neither default path runs Senko, pyannote, local diarization, or chunk stitching (`LARK_TRIM_LONG_SILENCE=0` on the watcher).

Use a **Hong Kong** TOS region — it uploads far faster from outside mainland China (~700KB/s single-stream vs ~10–30KB/s to Shanghai) and 妙记 still fetches it fine. Requires `VOLC_API_KEY` + `VOLC_TOS_*` (see `.env.example`).

The repository still retains the older **Gemini 3.5 Flash** and **OpenAI gpt-4o-transcribe-diarize** fallback providers (`STT_PROVIDER=gemini|openai`). Their chunking and local diarization code is not part of the default 妙记 route. We originally benchmarked these for mixed Chinese-English audio:

| Provider | Mixed zh+en MER | Price/hr | Diarization |
|----------|----------------|----------|-------------|
| **妙记 (Lark Minutes)** — default | Good (zh + mixed) | low | **Yes — server-side, best** |
| Gemini 3 Pro | **7.2%** (best) | ~$0.50-2 | No (prompt-based) |
| Gemini 3.5 Flash | Good | ~$0.10 | Chunk-stitched |
| OpenAI gpt-4o-transcribe-diarize | OK on en, weaker mixed | $0.45/hr | Yes (native) |
| Qwen3-ASR-Flash | 5.78% WER | ~$0.04 | No |
| OpenAI Whisper API | ~12% (single-lang) | $0.36 | No |
| Deepgram Nova-3 | Chinese not supported | $0.31 | Yes |

Between the two fallbacks: on a 2-hour Chinese+English voice note tested side-by-side, Gemini won on punctuation, code-switching (`ROI` stayed `ROI` vs OpenAI's `RY`), and didn't hallucinate English filler from Chinese particles — so Gemini is the preferred fallback; OpenAI (`STT_PROVIDER=openai`) catches more granular interjections.

### Long-audio handling (Gemini/OpenAI fallback only)

Gemini 3 Flash in a single call **silently drops/summarizes** on audio longer than ~15 minutes — verified on a 2hr file where the single-call output ended at 1h22m and collapsed 71 minutes into a one-line "turn". This pipeline auto-chunks long audio at silence boundaries (`ffmpeg silencedetect`) and transcribes chunks **in parallel** (8 concurrent by default).

For a 2-hour file: ~10 chunks of 8-15 min each, transcribed in parallel → ~60 sec wall time instead of ~10 min serial — and crucially **full coverage** with no fabricated content. Each chunk's timestamps are offset to absolute time, then a stitching layer:

- **drops malformed lines** (`[X -` no closing bracket — Gemini garbage)
- **clamps utterance length** (any single turn > 2 min is hallucination)
- **clamps timestamps past audio end** (post-EOF silence transcribed into fabricated dialogue)
- **drops Gemini compliance preamble + `（注：...）` meta-commentary**
- **filters out chunk-overlap duplicates** per-line + sorts chronologically (robust to Gemini emitting out-of-order chunks)

The summary step runs as a separate text-input call after transcription, so JSON-mode brittleness on long outputs is avoided. Transient Gemini errors (503/429/5xx) on individual chunks retry up to 3 times with exponential backoff instead of aborting the full job.

Tunable via `CHUNK_THRESHOLD_SEC` / `CHUNK_TARGET_SEC` / `CHUNK_MIN_SEC` / `CHUNK_MAX_SEC` / `CHUNK_PARALLELISM` env vars (see `.env.example`).

### Speaker label consistency across chunks (Gemini/OpenAI fallback only)

> This whole section applies only to the `gemini`/`openai` fallbacks. The default `lark` (妙记) provider does diarization server-side in one pass — no chunking, no stitching — which is exactly why it's the default.

When the audio gets chunked, each chunk's `SPEAKER_0`/`SPEAKER_1` labels are independent — chunk 1's SPEAKER_0 might be the same person as chunk 2's SPEAKER_1. This pipeline addresses that with a global diarization pass that runs **in parallel** with Gemini chunk transcription, then assigns each transcript line a consistent global speaker label.

Diarizer auto-select (via `DIARIZER` env var):

- **Senko** (recommended, default if installed) — `pip install senko`. CoreML-native on Apple Silicon, ~60 sec for a 2hr file on M4 Max. Uses CAM++ Mandarin embedder which handles Chinese-English mixed audio well. No HuggingFace token required.
- **pyannote.audio** (fallback) — slower (~15-25 min for 2hr on Apple Silicon MPS due to PyTorch fallback overhead). Requires HuggingFace token + accepting licenses for `pyannote/speaker-diarization-3.1` + `pyannote/segmentation-3.0` + `pyannote/speaker-diarization-community-1`.
- **none** (`DIARIZER=none`) — skip global diarization; rely on per-chunk text-matching overlap reconciliation (~85% reliable on 2-speaker conversations).

Neither approach is perfect on rapid Q+A where turn boundaries are sub-second (acoustic embedders can't always distinguish back-and-forth at that granularity) — but both keep macro-level speaker identity consistent across the whole transcript, which is what downstream summarization actually needs.

## Gotchas

### TCC / Full Disk Access

The Voice Memos `Group Container` directory is protected by macOS TCC (Transparency, Consent, and Control). Your terminal or the `launchd` agent needs **Full Disk Access** to read recordings.

- **Quick fix:** System Settings → Privacy & Security → Full Disk Access → add your Terminal app (Terminal.app, iTerm2, etc.)
- **Proper fix:** Wrap the script in a signed `.app` bundle and grant FDA to that bundle only — avoids giving `/bin/bash` blanket access. See [Apple's TCC docs](https://developer.apple.com/documentation/security/app-sandbox) for details.

If the watcher runs but never finds new files, this is almost certainly the cause.

### iCloud Optimized Storage

If your Mac is low on storage, macOS may keep recordings as **zero-byte stubs** (evicted to iCloud). The file appears in the directory but has no content until downloaded.

The script already skips files under 1KB and recordings shorter than `MIN_DURATION_SECONDS` (default 60s — see `.env.example`), but to force-download recordings:

```bash
# Force Voice Memos to download all recordings
open -g "/System/Applications/Voice Memos.app"
```

Or disable "Optimize Mac Storage" in System Settings → Apple ID → iCloud.

### lark-cli appsecret missing from keychain

If Feishu deliveries suddenly start failing with `keychain entry not found: lark-cli/appsecret:<YOUR_LARK_APP_ID>`, the macOS keychain entry for the lark-cli OAuth client got wiped (happens on keychain reset, login keychain rebuild, partial reinstall). The config file at `~/.lark-cli/config.json` still references the app, but the secret is gone, and `auth login` can't even start because device-flow OAuth needs the appsecret.

Recovery (need the original appsecret saved somewhere — 1Password etc.):

```bash
printf '%s' '<APPSECRET>' | lark-cli config init \
  --app-id <YOUR_LARK_APP_ID> --app-secret-stdin --brand feishu
lark-cli auth login --recommend --no-wait --json   # → use the verification_url
lark-cli auth login --device-code <code>           # → blocks until approved
lark-cli auth status                               # → should be tokenStatus: valid
```

For doc deletes specifically, you'll also need the `drive:drive` scope, which requires admin approval on the Lark app side: re-run `lark-cli auth login --scope "drive:drive offline_access" --no-wait --json` after approval.

## Setup

### Prerequisites

- macOS with iCloud signed in (same Apple ID as your Watch)
- Apple Watch with Voice Memos (any model)
- For the default **妙记** provider: a Volcano Engine `VOLC_API_KEY` + TOS bucket creds (`VOLC_TOS_*`, Hong Kong region recommended) — see `.env.example`. `pip install tos`.
- A [Gemini API key](https://aistudio.google.com/apikey) — always needed (the summary stage runs on Gemini; also the `gemini` fallback provider).
- Python **3.12+** (Apple's system `python3` is 3.9 — too old; install with `brew install python@3.12` or asdf)
- `ffmpeg` — required only by the legacy Python watcher and fallback providers; the standalone App path does not invoke it. `brew install ffmpeg`

### Install

```bash
git clone https://github.com/xingfanxia/watch-transcriber.git
cd watch-transcriber
cp .env.example .env
# Edit .env with your GEMINI_API_KEY and delivery preferences
./setup.sh
```

### Configure delivery targets

Edit `.env` to choose where transcripts go:

```bash
# Comma-separated list of targets
DELIVERY_TARGETS=file,apple_notes
```

Available deliveries:

| Target | Description | Config needed |
|--------|-------------|---------------|
| `file` | Save markdown to a folder | `OUTPUT_DIR` |
| `local_archive` | Structured `data/YYYY-MM-DD/` archive with per-recording `.md`, `daily.md`, `daily.html` rollup | `LOCAL_ARCHIVE_DIR` (default `./data`), `LOCAL_ARCHIVE_HTML=0` to skip HTML |
| `audio_archive` | AI-titled `.m4a` copy next to the archive note (`HHMMSS-<title>.m4a`) — Voice Memos has no rename API, so this is the browsable audio library. Original untouched; idempotent. Backfill: `scripts/backfill/backfill_audio_archive.py` | same `LOCAL_ARCHIVE_DIR` |
| `manifest` | `data/manifest.json` — 1:1 note↔audio↔original map + AI topic category (taxonomy in `deliveries/manifest.py:CATEGORIES`), plus `data/by-topic/<分类>/` symlink views. Backfill/classify: `scripts/backfill/backfill_manifest.py` | same `LOCAL_ARCHIVE_DIR` |
| `viewer` | Regenerates `data/index.html` — self-contained dark-mode archive UI (search, category filter, audio player with transcript-timestamp seek). Manual rebuild: `python3 -m deliveries.viewer` | same `LOCAL_ARCHIVE_DIR` |
| `archive_git` | Auto-commits the `data/` repo (notes + manifest; audio/generated files gitignored — the delivery bootstraps `data/.gitignore` itself) and pushes if a remote exists. `data/` is a nested repo — this project's GitHub repo is public, personal data never goes there; its own remote must be PRIVATE | `data/` must be `git init`-ed |
| `r2_backup` | Uploads the archive `.m4a` to a private Cloudflare R2 bucket (off-site audio backup; free ≤10GB/mo). Catch-up: `scripts/backfill/backfill_r2_audio.py` | local `wrangler` OAuth login; `R2_BUCKET` (default `watch-transcriber-audio`) |
| `apple_notes` | Create an Apple Note | `APPLE_NOTES_FOLDER` |
| `feishu` | Create a Feishu/Lark doc (optionally transfer ownership from the bot to you) | `FEISHU_FOLDER_TOKEN` or `FEISHU_WIKI_SPACE`; `FEISHU_DOC_OWNER_ID` for ownership transfer |
| `feishu_notify` | DM summary via Feishu bot | `FEISHU_NOTIFY_USER_ID` |
| `obsidian_git` | Commit to a GitHub repo | `OBSIDIAN_REPO`, `GITHUB_TOKEN` |
| `agent` | Delegate to `claude -p` | `AGENT_DELIVERY_PROMPT` |

**Order matters** within `DELIVERY_TARGETS`: `manifest` locates `local_archive`/`audio_archive` output on disk, and `viewer`/`archive_git` consume the manifest — keep `local_archive, audio_archive, manifest, viewer, archive_git, r2_backup` in that relative order.

### Summary languages

Notes carry an English + Chinese summary and key points by default. Set `SUMMARY_LANGUAGES` in `.env` to a comma-separated list of ISO 639-1 codes, in display order, to change that: `en` for English only, `en,es` for English + Spanish, and so on. Transcripts always stay in the spoken language. This applies to the Python watcher's deliveries and archive page; the EchoWall App's own recordings keep their built-in English + Chinese summary.

### Where the data lives (this repo is public ⚠️)

`data/` (notes, transcripts, audio, manifest) is gitignored here and must never be committed to this repo. Backup legs:

| What | Where | How |
|---|---|---|
| Notes + manifest, versioned | **private** `github.com/xingfanxia/watch-transcriber-data` | nested git repo inside `data/`; `archive_git` auto-commits + pushes per recording |
| Audio (AI-titled copies) | **private** Cloudflare R2 bucket `watch-transcriber-audio` | `r2_backup` per recording; catch-up via `scripts/backfill/backfill_r2_audio.py` (ledger: `state/r2_uploaded.json`) |
| Originals | Voice Memos + iCloud | never touched by the pipeline |

### Agent delivery examples

The `agent` delivery is the most flexible — it delegates to Claude Code which can use any installed skill:

```bash
# Send to Feishu doc
AGENT_DELIVERY_PROMPT=use lark-doc skill to create a feishu doc titled '{title}' with content: {content}

# Send to Google Docs
AGENT_DELIVERY_PROMPT=use gws-docs skill to create a google doc titled '{title}' with content: {content}

# Post to Slack
AGENT_DELIVERY_PROMPT=post to #voice-notes channel: {content}

# Email it
AGENT_DELIVERY_PROMPT=use gws-gmail-send to email me@example.com subject '{title}' body: {content}
```

### Test manually

```bash
# Process any new recordings right now
python3 transcribe.py

# Verify setup (env vars, FDA, delivery prerequisites, LaunchAgent state)
python3 transcribe.py --doctor

# Preview what would happen without calling Gemini or running deliveries
python3 transcribe.py --dry-run

# Reprocess all recordings from a specific date (ignores processed-state)
python3 transcribe.py --reprocess 2026-05-13
python3 transcribe.py --reprocess 2026-05-13 --dry-run   # preview only
```

### Map Action Button (Apple Watch Ultra)

Settings → Action Button → App → Voice Memos

Now one press starts recording, another press stops.

## Writing custom deliveries

Create `deliveries/your_target.py` with a single function:

```python
def deliver(note: dict) -> bool:
    """
    note contains:
      - title: str
      - transcript: str (raw with timestamps/speakers)
      - summary: str
      - todos: list[str]
      - audio_path: str
      - timestamp: str (ISO)
      - markdown: str (formatted)
    """
    # your logic here
    return True  # success
```

Then add `your_target` to `DELIVERY_TARGETS` in `.env`.

## How it works

1. **Record** on Apple Watch using Voice Memos (or any device)
2. **iCloud syncs** the `.m4a` to `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/`
3. **launchd detects** the new file via `WatchPaths`
4. **妙记 (Volcano Lark Minutes)** transcribes with server-side speaker diarization (or the Gemini/OpenAI fallback), then Gemini summarizes the transcript and names it
5. **Delivery layer** sends the structured note — titled `YYYY-MM-DD HH:MM <AI topic>` so name-sorted lists order chronologically — to your configured targets

## Project structure

```
watch-transcriber/
├── transcribe.py              # Main pipeline
├── deliveries/
│   ├── __init__.py            # Delivery router
│   ├── file.py                # Markdown file output
│   ├── local_archive.py       # Structured data/YYYY-MM-DD/ archive (per-recording + daily rollup + HTML)
│   ├── audio_archive.py       # AI-titled .m4a copy alongside the archive note
│   ├── manifest.py            # data/manifest.json map + category taxonomy + by-topic/ views
│   ├── viewer.py              # data/index.html generator (viewer_template.html + vendor/marked)
│   ├── archive_git.py         # Auto commit+push of the nested PRIVATE data/ repo
│   ├── r2_backup.py           # Per-recording audio upload to a private R2 bucket
│   ├── apple_notes.py         # Apple Notes via AppleScript
│   ├── feishu.py              # Feishu/Lark doc via lark-cli
│   ├── feishu_notify.py       # Feishu bot DM with link to created doc
│   ├── obsidian_git.py        # GitHub commit to Obsidian vault
│   └── agent.py               # claude -p delegation (Feishu, Slack, etc.)
├── desktop/                   # EchoWall 回音壁 — Tauri App (embedded Rust processing + authenticated media transport)
├── scripts/backfill/          # Idempotent backfills: audio copies / manifest+categories / R2 sync
├── scripts/ops/               # apply_speakers / delete_recording / import_gpt_thread / restore_archive
├── tests/                     # pytest suite (naming, deliveries, manifest, viewer, delete)
├── setup.sh                   # One-command install
├── com.watch-transcriber.plist # launchd template
├── .env.example               # Configuration template
└── state/                     # Processed files tracking + R2 ledger (gitignored)
```

## Contributing

This project is designed to be **modular and forkable**. Every layer is a simple, swappable component:

| Layer | Current | Want something different? |
|-------|---------|--------------------------|
| **Recording** | Apple Voice Memos | Any app that syncs audio files to a known directory |
| **File monitoring** | macOS `launchd WatchPaths` | `fswatch`, `inotifywait` (Linux), polling, or a cloud trigger |
| **Transcription** | 妙记 (Volcano Lark Minutes) default; Gemini 3.5 Flash / OpenAI fallback | Whisper, Qwen3-ASR, AssemblyAI, Deepgram — add a provider branch in `transcribe_and_summarize()` |
| **Delivery** | file, Apple Notes, Feishu, Obsidian, agent | Drop a new `.py` in `deliveries/` with a `deliver(note)` function |

PRs welcome for:
- **New transcription providers** — Whisper, Qwen3-ASR-Flash, etc. (OpenAI gpt-4o-transcribe-diarize already supported via `STT_PROVIDER=openai`)
- **New delivery targets** — Slack, Notion, WeChat, Telegram, email, etc.
- **Better file monitoring** — `fswatch`, cross-platform watchers, Linux `inotify` support
- **Smarter summarization** — custom prompts, topic extraction, meeting note templates
- **Chunking improvements** — adaptive chunk size based on speech density, VAD-based silence detection

## License

MIT
