#!/usr/bin/env bash
# widevine-install.sh — first-boot oneshot: extract Widevine CDM from Chrome .deb
# and install it to a writable path that Chromium is pointed at via managed policy.
#
# Widevine is written to /var/lib/chromium-widevine/ (writable across bootc upgrades).
# Chromium is directed there via WidevineCdmPath in /etc/chromium/policies/managed/policies.json.
#
# Run by widevine-install.service; guarded by ConditionPathExists=!/var/lib/widevine-installed
# so this is a no-op on subsequent boots.

set -euo pipefail

FLAG=/var/lib/widevine-installed
WVDIR=/var/lib/chromium-widevine/_platform_specific/linux_x64
CHROME_DEB=/tmp/chrome-widevine-$$.deb
EXTRACT_DIR=/tmp/chrome-extract-$$

# Idempotency: exit immediately if already installed
if [[ -f "$FLAG" ]]; then
    echo "Widevine already installed, skipping."
    exit 0
fi

echo "==> Downloading Google Chrome stable..."
curl -fL \
    --max-time 120 \
    --retry 3 \
    --retry-delay 5 \
    -o "$CHROME_DEB" \
    https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb

echo "==> Extracting .deb archive..."
mkdir -p "$EXTRACT_DIR"
cd "$EXTRACT_DIR"

# .deb files are ar archives; extract to get data.tar.*
ar x "$CHROME_DEB"

# The data archive may be data.tar.xz or data.tar.zst depending on Chrome version
DATA_TAR=$(ls data.tar.* 2>/dev/null | head -1)
if [[ -z "$DATA_TAR" ]]; then
    echo "ERROR: Could not find data.tar.* in Chrome .deb" >&2
    exit 1
fi

echo "==> Extracting WidevineCdm from $DATA_TAR..."
mkdir -p widevine
# Use --wildcards to only extract Widevine-related files (much faster than full extraction)
tar -xf "$DATA_TAR" \
    --wildcards \
    --no-anchored \
    '*/WidevineCdm/*' \
    -C widevine/

# Locate the shared library
WVLIB=$(find widevine -name 'libwidevinecdm.so' | head -1)
WVMANIFEST=$(find widevine -name 'manifest.json' -path '*/WidevineCdm/*' | head -1)

if [[ -z "$WVLIB" ]]; then
    echo "ERROR: libwidevinecdm.so not found in extracted archive" >&2
    exit 1
fi

echo "==> Installing Widevine to $WVDIR..."
mkdir -p "$WVDIR"
install -m 755 "$WVLIB"     "$WVDIR/libwidevinecdm.so"

if [[ -n "$WVMANIFEST" ]]; then
    install -m 644 "$WVMANIFEST" "$WVDIR/../manifest.json"
fi

echo "==> Cleaning up temporary files..."
cd /
rm -rf "$EXTRACT_DIR" "$CHROME_DEB"

echo "==> Widevine installed successfully."
touch "$FLAG"
