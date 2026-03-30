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
| Gemini 3 Flash | Good | ~$0.10 | Yes |
| Qwen3-ASR-Flash | 5.78% WER | ~$0.04 | No |
| OpenAI Whisper API | ~12% (single-lang) | $0.36 | No |
| DouBao ASR | Good for zh, weaker mixed | Unknown | Unknown |
| Deepgram Nova-3 | Chinese not supported | $0.31 | Yes |

Gemini 3 Flash offers the best balance of quality, multilingual support, diarization, and cost for this use case.

## Setup

### Prerequisites

- macOS with iCloud signed in (same Apple ID as your Watch)
- Apple Watch with Voice Memos (any model)
- [Gemini API key](https://aistudio.google.com/apikey) (free tier available)
- Claude Code with the [transcribe plugin](https://github.com/anthropics/plugins) installed

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
| `apple_notes` | Create an Apple Note | `APPLE_NOTES_FOLDER` |
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
│   ├── apple_notes.py         # Apple Notes via AppleScript
│   ├── obsidian_git.py        # GitHub commit to Obsidian vault
│   └── agent.py               # claude -p delegation (Feishu, Slack, etc.)
├── setup.sh                   # One-command install
├── com.watch-transcriber.plist # launchd template
├── .env.example               # Configuration template
└── state/                     # Processed files tracking (gitignored)
```

## License

MIT
