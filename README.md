# watch-transcriber

[中文版](README.zh.md)

Apple Watch voice transcription pipeline (~¥2/hour of audio via 妙记). Record on your wrist, get structured notes automatically.

```
Apple Watch (Voice Memos) → iCloud Sync → Mac detects new .m4a
  → 妙记 (Volcano Lark Minutes) STT — server-side diarization (Gemini/OpenAI fallback)
    → Pluggable delivery (Apple Notes, Feishu, Obsidian, custom)
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

### Why Voice Memos + launchd?

- **Voice Memos already solves all the hard problems.** Background recording, interruption recovery, unlimited duration, iCloud sync — all handled by Apple's own system-level entitlements that third-party apps can't access.
- **Action Button works.** You can map Voice Memos to the Ultra's Action Button for one-press recording.
- **Recordings sync instantly.** Files appear at a known path on your Mac within seconds.
- **launchd `WatchPaths`** is the native macOS way to react to filesystem changes — zero polling, zero battery waste, zero dependencies.

### STT: Why 妙记 (Volcano Lark Minutes)?

**妙记 (`volc.lark.minutes`) is the default** (`STT_PROVIDER=lark`). It does speaker diarization **server-side in a single call** — no chunking, no cross-chunk speaker stitching. Verified across 5 real recordings (2026-06): 妙记 returned the exact speaker count on every two-person conversation (2/2/2/2), where chunk-stitched Gemini/OpenAI and the raw Doubao auc models all over-counted (3–5 speakers); it also swallowed a 3.45-hour file in one pass. Diarization, not transcription, was the real hard half — and 妙记 treats it as a first-class server-side job instead of a stitching afterthought.

妙记 needs a publicly-fetchable FileURL, so the pipeline converts audio to a small 16kHz-mono mp3, uploads it to Volcano TOS, hands 妙记 a presigned URL, then deletes the object. Use a **Hong Kong** TOS region — it uploads far faster from outside mainland China (~700KB/s single-stream vs ~10–30KB/s to Shanghai) and 妙记 still fetches it fine. Requires `VOLC_API_KEY` + `VOLC_TOS_*` (see `.env.example`).

To reduce duration-based 妙记 usage, installing Senko enables conservative local compaction of non-speech gaps longer than 10 seconds in the temporary upload mp3 only. Every gap keeps at least 3 seconds on both sides, the original m4a is never modified, provider timestamps are mapped back to original recording time, and any Senko/ffmpeg/duration-validation failure falls back to uploading the complete recording. Set `LARK_TRIM_LONG_SILENCE=0` to disable it; see `.env.example` for the safety-floor settings.

**Gemini 3.5 Flash** and **OpenAI gpt-4o-transcribe-diarize** remain as fallbacks (`STT_PROVIDER=gemini|openai`); they auto-chunk long audio and stitch speaker labels across chunks (details below). We originally benchmarked these for mixed Chinese-English audio:

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

### Long-audio handling (silence-aware chunking + parallel)

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
- `ffmpeg` — required for audio conversion + silence-aware chunking. `brew install ffmpeg`

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

### Where the data lives (this repo is public ⚠️)

`data/` (notes, transcripts, audio, manifest) is gitignored here and must never be committed to this repo. Backup legs:

| What | Where | How |
|---|---|---|
| Notes + manifest, versioned | **private** `github.com/xingfanxia/watch-transcriber-data` | nested git repo inside `data/`; `archive_git` auto-commits + pushes per recording |
| Audio (AI-titled copies) | **private** Cloudflare R2 bucket `watch-transcriber-audio` | `r2_backup` per recording; catch-up via `scripts/backfill/backfill_r2_audio.py` (ledger: `state/r2_uploaded.json`) |
| Originals | Voice Memos + iCloud | never touched by the pipeline |

### Desktop app (Tauri)

`desktop/` is a thin Rust shell: a loopback axum server serves `data/` (with HTTP Range, so audio seeking works) and a webview opens the same generated `index.html` — no second viewer implementation. `cd desktop && npm run tauri dev` to run, `npm run tauri build` to bundle; `WATCH_TRANSCRIBER_DATA` overrides the archive location.

- **Speaker tagging** (app-only — needs the loopback API): click a speaker chip in the detail pane to name `SPEAKER_N`, optionally batch-apply to every recording in the current filter; tags land in `manifest.json` (`speakers` field), are preserved across reprocesses, auto-commit + push to the private notes repo, and power the 说话人 sidebar filter + transcript display. Tags are also written back into the note files' transcript labels (`scripts/ops/apply_speakers.py`, reversible via the manifest's `speakers_applied`). The page auto-refreshes when the pipeline rebuilds the archive.
- **Markdown attachments**: paste or pick a `.md`/`.txt` in the detail pane's 附注 tab — stored as `data/<date>/<HHMMSS>-attachments/*.md`, listed in the manifest, rendered in-app (vendored `marked`), and pushed to the private repo like everything else. The detail pane is tabbed 摘要/附注/转写 (keys 1/2/3).
- **Delete**: the detail pane's two-step 删除 button (or `scripts/ops/delete_recording.py`) removes the note, audio copy, attachments, manifest entry, by-topic links, R2 backup object, and refreshes the daily rollup — then commits/pushes. Voice Memos originals and Apple Notes/飞书 copies are deliberately untouched; git history keeps the note recoverable.
- **GPT thread import**: `scripts/ops/import_gpt_thread.py <export.json>` scans a ChatGPT export (all branches), maps uploaded transcript files back to recordings (three historical filename formats), attaches each recording's GPT analysis as a markdown note, and extracts "who is SPEAKER_N" from the analysis into speaker tags (never overwriting manual ones). Idempotent.
- **Fresh machine**: clone this repo, open the app — it shows a bootstrap page until `python3 scripts/ops/restore_archive.py` restores `data/` (clones the private notes repo, pulls all audio back from R2, seeds the upload ledger, rebuilds the viewer), then continues automatically.

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
│   ├── viewer.py              # data/index.html generator (viewer_template.html)
│   ├── apple_notes.py         # Apple Notes via AppleScript
│   ├── feishu.py              # Feishu/Lark doc via lark-cli
│   ├── feishu_notify.py       # Feishu bot DM with link to created doc
│   ├── obsidian_git.py        # GitHub commit to Obsidian vault
│   └── agent.py               # claude -p delegation (Feishu, Slack, etc.)
├── scripts/backfill/          # One-off ops: AI-title backfill / retitle / Feishu doc cleanup
├── setup.sh                   # One-command install
├── com.watch-transcriber.plist # launchd template
├── .env.example               # Configuration template
└── state/                     # Processed files tracking (gitignored)
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
