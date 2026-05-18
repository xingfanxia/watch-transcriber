# watch-transcriber

[中文版](README.zh.md)

Zero-cost Apple Watch voice transcription pipeline. Record on your wrist, get structured notes automatically.

```
Apple Watch (Voice Memos) → iCloud Sync → Mac detects new .m4a
  → Gemini 3 Flash STT (multilingual + diarization)
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

### STT: Why Gemini 3 Flash?

We benchmarked STT options for mixed Chinese-English audio:

| Provider | Mixed zh+en MER | Price/hr | Diarization |
|----------|----------------|----------|-------------|
| Gemini 3 Pro | **7.2%** (best) | ~$0.50-2 | No (prompt-based) |
| Gemini 3 Flash Preview | Good | ~$0.10 | Yes |
| OpenAI gpt-4o-transcribe-diarize | OK on en, weaker mixed | $0.45/hr | Yes (native) |
| Qwen3-ASR-Flash | 5.78% WER | ~$0.04 | No |
| OpenAI Whisper API | ~12% (single-lang) | $0.36 | No |
| DouBao ASR | Good for zh, weaker mixed | Unknown | Unknown |
| Deepgram Nova-3 | Chinese not supported | $0.31 | Yes |

Gemini 3 Flash Preview is the default. On a 2-hour Chinese+English voice note we tested side-by-side against `gpt-4o-transcribe-diarize`, Gemini won on punctuation, code-switching (`ROI` stayed `ROI` vs OpenAI's `RY`), and didn't hallucinate English filler from Chinese particles. OpenAI is available via `STT_PROVIDER=openai` if you want more granular interjection capture (catches every "嗯/yeah").

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

### Speaker label consistency across chunks

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

If Feishu deliveries suddenly start failing with `keychain entry not found: lark-cli/appsecret:cli_a942426c0ab81cdd`, the macOS keychain entry for the lark-cli OAuth client got wiped (happens on keychain reset, login keychain rebuild, partial reinstall). The config file at `~/.lark-cli/config.json` still references the app, but the secret is gone, and `auth login` can't even start because device-flow OAuth needs the appsecret.

Recovery (need the original appsecret saved somewhere — 1Password etc.):

```bash
printf '%s' '<APPSECRET>' | lark-cli config init \
  --app-id cli_a942426c0ab81cdd --app-secret-stdin --brand feishu
lark-cli auth login --recommend --no-wait --json   # → use the verification_url
lark-cli auth login --device-code <code>           # → blocks until approved
lark-cli auth status                               # → should be tokenStatus: valid
```

For doc deletes specifically, you'll also need the `drive:drive` scope, which requires admin approval on the Lark app side: re-run `lark-cli auth login --scope "drive:drive offline_access" --no-wait --json` after approval.

## Setup

### Prerequisites

- macOS with iCloud signed in (same Apple ID as your Watch)
- Apple Watch with Voice Memos (any model)
- [Gemini API key](https://aistudio.google.com/apikey)
- Python **3.12+** (Apple's system `python3` is 3.9 — too old; install with `brew install python@3.12` or asdf)
- `ffmpeg` — required for silence-aware chunking of long audio. `brew install ffmpeg`

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
| `apple_notes` | Create an Apple Note | `APPLE_NOTES_FOLDER` |
| `feishu` | Create a Feishu/Lark doc | `FEISHU_FOLDER_TOKEN` or `FEISHU_WIKI_SPACE` |
| `feishu_notify` | DM summary via Feishu bot | `FEISHU_NOTIFY_USER_ID` |
| `obsidian_git` | Commit to a GitHub repo | `OBSIDIAN_REPO`, `GITHUB_TOKEN` |
| `agent` | Delegate to `claude -p` | `AGENT_DELIVERY_PROMPT` |

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
4. **Gemini 3 Flash** transcribes with speaker diarization and multilingual support
5. **Delivery layer** sends the structured note to your configured targets

## Project structure

```
watch-transcriber/
├── transcribe.py              # Main pipeline
├── deliveries/
│   ├── __init__.py            # Delivery router
│   ├── file.py                # Markdown file output
│   ├── local_archive.py       # Structured data/YYYY-MM-DD/ archive (per-recording + daily rollup + HTML)
│   ├── apple_notes.py         # Apple Notes via AppleScript
│   ├── feishu.py              # Feishu/Lark doc via lark-cli
│   ├── feishu_notify.py       # Feishu bot DM with link to created doc
│   ├── obsidian_git.py        # GitHub commit to Obsidian vault
│   └── agent.py               # claude -p delegation (Feishu, Slack, etc.)
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
| **Transcription** | Gemini 3 Flash (multimodal) | Whisper, Qwen3-ASR, DouBao, AssemblyAI, Deepgram — just replace `transcribe_and_summarize()` |
| **Delivery** | file, Apple Notes, Feishu, Obsidian, agent | Drop a new `.py` in `deliveries/` with a `deliver(note)` function |

PRs welcome for:
- **New transcription providers** — Whisper, Qwen3-ASR-Flash, etc. (OpenAI gpt-4o-transcribe-diarize already supported via `STT_PROVIDER=openai`)
- **New delivery targets** — Slack, Notion, WeChat, Telegram, email, etc.
- **Better file monitoring** — `fswatch`, cross-platform watchers, Linux `inotify` support
- **Smarter summarization** — custom prompts, topic extraction, meeting note templates
- **Chunking improvements** — adaptive chunk size based on speech density, VAD-based silence detection

## License

MIT
