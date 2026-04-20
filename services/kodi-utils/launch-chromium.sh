#!/usr/bin/env bash
# launch-chromium.sh — kiosk browser launcher for use from Kodi favourites.
#
# cage only hosts a single Wayland application at a time. To launch Chromium
# from within a Kodi session running under cage, this script:
#   1. Stops kodi-session.service (releases cage + TTY1)
#   2. Runs Chromium in kiosk mode directly on TTY1 via a new cage instance
#   3. Restarts kodi-session.service when Chromium exits
#
# Usage: launch-chromium.sh <URL>
# Example (from Kodi favourites.xml):
#   System.Exec(/usr/local/bin/launch-chromium.sh https://www.max.com)

set -euo pipefail

URL="${1:-}"
if [[ -z "$URL" ]]; then
    echo "Usage: $0 <URL>" >&2
    exit 1
fi

echo "==> Stopping kodi-session to free TTY1..."
systemctl stop kodi-session.service || true

# Give logind a moment to release the session seat
sleep 1

echo "==> Launching Chromium kiosk: $URL"
# Run cage on TTY1 with Chromium in kiosk mode.
# --no-first-run: suppress first-run welcome screen
# --disable-features=Translate: no translation bar popups
# --autoplay-policy=no-user-gesture-required: allow video autoplay
cage -- /usr/bin/chromium \
    --kiosk \
    --no-first-run \
    --disable-features=Translate \
    --autoplay-policy=no-user-gesture-required \
    --no-default-browser-check \
    "$URL" \
    || true

echo "==> Chromium exited, restarting kodi-session..."
systemctl start kodi-session.service
