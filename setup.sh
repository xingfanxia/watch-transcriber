#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VOICE_MEMOS_DIR="$HOME/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
PLIST_NAME="com.watch-transcriber.plist"
PLIST_SRC="$SCRIPT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== watch-transcriber setup ==="
echo ""

# Check Voice Memos directory
if [ ! -d "$VOICE_MEMOS_DIR" ]; then
    echo "ERROR: Voice Memos directory not found at:"
    echo "  $VOICE_MEMOS_DIR"
    echo ""
    echo "Make sure you have Voice Memos installed and have at least one recording."
    exit 1
fi
echo "[OK] Voice Memos directory found"

# Check transcribe plugin
TRANSCRIBE_SCRIPT="$HOME/.claude/plugins/transcribe/transcribe_any.py"
if [ ! -f "$TRANSCRIBE_SCRIPT" ]; then
    echo "WARNING: Transcribe plugin not found at $TRANSCRIBE_SCRIPT"
    echo "  Install it via Claude Code: the 'transcribe' plugin"
    echo "  Continuing setup anyway..."
else
    echo "[OK] Transcribe plugin found"
fi

# Install python-dotenv
pip3 install python-dotenv --quiet 2>/dev/null || true
echo "[OK] python-dotenv installed"

# Create .env if missing
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "[!!] Created .env from .env.example — edit it with your API keys"
fi

# Create state directory
mkdir -p "$SCRIPT_DIR/state"

# Generate launchd plist with actual paths
sed "s|__INSTALL_DIR__|$SCRIPT_DIR|g; s|__VOICE_MEMOS_DIR__|$VOICE_MEMOS_DIR|g" \
    "$PLIST_SRC" > "$PLIST_DST"
echo "[OK] LaunchAgent plist installed at $PLIST_DST"

# Unload if already loaded, then load
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
echo "[OK] LaunchAgent loaded"

echo ""
echo "=== Setup complete ==="
echo ""
echo "The watcher is now monitoring Voice Memos for new recordings."
echo "Record something on your Apple Watch and it will be auto-transcribed."
echo ""
echo "Logs: $SCRIPT_DIR/state/transcriber.log"
echo "Config: $SCRIPT_DIR/.env"
echo ""
echo "To test manually: python3 $SCRIPT_DIR/transcribe.py"
echo "To stop: launchctl unload $PLIST_DST"
