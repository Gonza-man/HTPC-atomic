#!/usr/bin/env bash
# update-playlist.sh — download Chilean IPTV channels from iptv-org and merge local overrides.
#
# Usage:
#   update-playlist.sh              # normal mode: download and write to /var/lib/iptv/
#   update-playlist.sh --dry-run   # only count channels and report; exit 1 if zero found
#
# Run automatically by iptv-update.timer (daily).
# The CI pipeline runs this with --dry-run to detect broken stream lists early.

set -euo pipefail

IPTV_ORG_URL="https://iptv-org.github.io/iptv/countries/cl.m3u"
OUTPUT_DIR="/var/lib/iptv"
OUTPUT_FILE="$OUTPUT_DIR/channels.m3u"
LOCAL_OVERRIDES="$OUTPUT_DIR/local-overrides.m3u"
KODI_JSONRPC="http://localhost:8080/jsonrpc"
TMP_FILE="/tmp/cl-iptv-$$.m3u"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

cleanup() {
    rm -f "$TMP_FILE"
}
trap cleanup EXIT

echo "==> Downloading Chilean IPTV playlist from iptv-org..."
curl -fsSL \
    --max-time 30 \
    --retry 3 \
    --retry-delay 5 \
    -o "$TMP_FILE" \
    "$IPTV_ORG_URL"

COUNT=$(grep -c '#EXTINF' "$TMP_FILE" || true)
echo "Found $COUNT channels in iptv-org Chilean playlist."

if [[ "$COUNT" -eq 0 ]]; then
    echo "ERROR: Zero channels found — iptv-org playlist may be empty or the URL has changed." >&2
    exit 1
fi

# Merge local overrides (entries appended after the #EXTM3U header line)
if [[ -f "$LOCAL_OVERRIDES" ]]; then
    OVERRIDE_COUNT=$(grep -c '#EXTINF' "$LOCAL_OVERRIDES" || true)
    echo "Merging $OVERRIDE_COUNT local override channel(s) from $LOCAL_OVERRIDES..."
    # Skip the #EXTM3U header line from the overrides file before appending
    tail -n +2 "$LOCAL_OVERRIDES" >> "$TMP_FILE"
fi

if $DRY_RUN; then
    echo "Dry-run mode: not writing output. Total channels: $COUNT."
    exit 0
fi

echo "==> Writing merged playlist to $OUTPUT_FILE..."
mkdir -p "$OUTPUT_DIR"
mv "$TMP_FILE" "$OUTPUT_FILE"
echo "Playlist updated: $(grep -c '#EXTINF' "$OUTPUT_FILE") total channels."

# Signal Kodi to refresh the video library via JSON-RPC.
# This is best-effort; Kodi may not be running (e.g., during CI).
echo "==> Notifying Kodi to refresh library..."
curl -s \
    --max-time 5 \
    -X POST "$KODI_JSONRPC" \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","method":"VideoLibrary.Scan","id":1}' \
    -o /dev/null \
    || echo "(Kodi not responding — skipping library refresh)"

echo "==> Done."
