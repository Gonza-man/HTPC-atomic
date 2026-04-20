"""
plugin.audio.nowplaying — Kodi service addon

Runs as a background service (xbmc.service, start="startup").
Polls the spotify-sidecar API every 2 seconds. When Spotify is playing,
shows a fullscreen overlay with:
  - Album art
  - Track name and artist
  - Scrolling synced lyrics (advancing in real time between polls)
  - Progress bar

No external pip dependencies — uses only xbmcgui, xbmc, xbmcaddon,
urllib.request, urllib.parse, json, time, threading.
"""

import sys
import os
import json
import time
import threading
import urllib.request
import urllib.parse

import xbmc
import xbmcgui
import xbmcaddon

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "resources", "lib"))
from lrclib import get_current_line  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADDON = xbmcaddon.Addon()
SIDECAR_BASE = "http://127.0.0.1:9999"
POLL_INTERVAL = 2  # seconds

# Screen layout (assumes 1920×1080; Kodi scales by DPI automatically)
SCREEN_W = 1920
SCREEN_H = 1080

# Left panel: album art
ART_X, ART_Y = 80, 140
ART_W, ART_H = 500, 500

# Right panel: text
TEXT_X = 640
TRACK_Y = 160
ARTIST_Y = 260
LYRIC_Y = 380

# Progress bar
PROG_X, PROG_Y = 80, 720
PROG_W, PROG_H = 1760, 12

# Colours (ARGB hex strings for Kodi)
COL_WHITE = "0xFFFFFFFF"
COL_GREY = "0xFFAAAAAA"
COL_ACCENT = "0xFF1DB954"  # Spotify green
COL_BG = "0xCC000000"     # semi-transparent black


# ---------------------------------------------------------------------------
# Overlay window
# ---------------------------------------------------------------------------

class NowPlayingWindow(xbmcgui.WindowDialog):
    """Fullscreen overlay window for the Now Playing display."""

    def __init__(self):
        super().__init__()
        self._controls_added = False
        self._last_art_url = ""
        self._lyrics: list = []
        self._poll_timestamp: float = 0.0
        self._progress_ms: int = 0
        self._duration_ms: int = 1

    def _build_controls(self):
        """Create all GUI controls. Called once on first show."""
        # Fullscreen semi-transparent background
        self.bg = xbmcgui.ControlImage(0, 0, SCREEN_W, SCREEN_H, "")
        self.bg.setColorDiffuse(COL_BG)
        self.addControl(self.bg)

        # Album art
        self.art = xbmcgui.ControlImage(ART_X, ART_Y, ART_W, ART_H, "")
        self.addControl(self.art)

        # Track name
        self.lbl_track = xbmcgui.ControlLabel(
            TEXT_X, TRACK_Y, SCREEN_W - TEXT_X - 80, 80,
            "", font="font20", textColor=COL_WHITE,
        )
        self.addControl(self.lbl_track)

        # Artist name
        self.lbl_artist = xbmcgui.ControlLabel(
            TEXT_X, ARTIST_Y, SCREEN_W - TEXT_X - 80, 50,
            "", font="font16", textColor=COL_GREY,
        )
        self.addControl(self.lbl_artist)

        # Current lyric line
        self.lbl_lyric = xbmcgui.ControlLabel(
            TEXT_X, LYRIC_Y, SCREEN_W - TEXT_X - 80, 60,
            "", font="font16", textColor=COL_WHITE,
        )
        self.addControl(self.lbl_lyric)

        # Progress bar background
        self.prog_bg = xbmcgui.ControlImage(PROG_X, PROG_Y, PROG_W, PROG_H, "")
        self.prog_bg.setColorDiffuse("0xFF444444")
        self.addControl(self.prog_bg)

        # Progress bar fill (width updated dynamically)
        self.prog_fill = xbmcgui.ControlImage(PROG_X, PROG_Y, 0, PROG_H, "")
        self.prog_fill.setColorDiffuse(COL_ACCENT)
        self.addControl(self.prog_fill)

        self._controls_added = True

    def update(self, data: dict, lyrics: list):
        """Update all controls with current playback state."""
        if not self._controls_added:
            self._build_controls()

        self._lyrics = lyrics or []
        self._poll_timestamp = data.get("timestamp", time.time())
        self._progress_ms = data.get("progress_ms", 0)
        self._duration_ms = max(data.get("duration_ms", 1), 1)

        # Album art (avoid reloading the same image)
        art_url = data.get("album_art_url", "")
        if art_url and art_url != self._last_art_url:
            self.art.setImage(art_url)
            self._last_art_url = art_url

        self.lbl_track.setLabel(data.get("track", ""))
        self.lbl_artist.setLabel(data.get("artist", ""))

        self._update_progress_and_lyrics()

    def _update_progress_and_lyrics(self):
        """Update the progress bar and current lyric line."""
        elapsed_ms = int((time.time() - self._poll_timestamp) * 1000)
        current_ms = self._progress_ms + elapsed_ms

        # Progress bar fill width
        ratio = min(current_ms / self._duration_ms, 1.0)
        fill_w = int(PROG_W * ratio)
        self.prog_fill.setWidth(fill_w)

        # Current lyric
        if self._lyrics:
            line = get_current_line(self._lyrics, current_ms)
            self.lbl_lyric.setLabel(line)

    def tick(self):
        """Called every ~500ms to update the progress bar and lyric position."""
        if self._controls_added:
            self._update_progress_and_lyrics()


# ---------------------------------------------------------------------------
# Sidecar API helpers
# ---------------------------------------------------------------------------

def _get_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Kodi-NowPlaying/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _fetch_now_playing() -> dict | None:
    return _get_json(f"{SIDECAR_BASE}/now-playing")


def _fetch_lyrics(track: str, artist: str) -> list:
    params = urllib.parse.urlencode({"track": track, "artist": artist})
    data = _get_json(f"{SIDECAR_BASE}/lyrics?{params}")
    if data and data.get("lines"):
        return data["lines"]
    return []


# ---------------------------------------------------------------------------
# Background ticker thread
# ---------------------------------------------------------------------------

class _TickerThread(threading.Thread):
    """Updates the overlay's progress bar every 500ms between polls."""

    def __init__(self, window: NowPlayingWindow, stop_event: threading.Event):
        super().__init__(daemon=True)
        self._window = window
        self._stop = stop_event

    def run(self):
        while not self._stop.wait(0.5):
            self._window.tick()


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class NowPlayingService(xbmc.Monitor):

    def __init__(self):
        super().__init__()
        self._window: NowPlayingWindow | None = None
        self._overlay_visible = False
        self._last_track = ""
        self._cached_lyrics: list = []
        self._ticker_stop = threading.Event()
        self._ticker: _TickerThread | None = None

    def _show_overlay(self, data: dict, lyrics: list):
        if self._window is None:
            self._window = NowPlayingWindow()
        self._window.update(data, lyrics)
        if not self._overlay_visible:
            self._window.show()
            self._overlay_visible = True
            # Start the ticker thread for smooth progress updates
            self._ticker_stop.clear()
            self._ticker = _TickerThread(self._window, self._ticker_stop)
            self._ticker.start()

    def _hide_overlay(self):
        if self._overlay_visible and self._window is not None:
            self._ticker_stop.set()
            self._window.close()
            self._overlay_visible = False
            self._last_track = ""
            self._cached_lyrics = []

    def run(self):
        xbmc.log("NowPlaying: service started", xbmc.LOGINFO)

        while not self.abortRequested():
            data = _fetch_now_playing()

            if data and data.get("is_playing"):
                track = data.get("track", "")
                artist = data.get("artist", "")

                # Fetch lyrics only when the track changes
                if track != self._last_track:
                    xbmc.log(f"NowPlaying: track changed to '{track}' by '{artist}'", xbmc.LOGDEBUG)
                    self._cached_lyrics = _fetch_lyrics(track, artist)
                    self._last_track = track

                self._show_overlay(data, self._cached_lyrics)
            else:
                self._hide_overlay()

            self.waitForAbort(POLL_INTERVAL)

        # Kodi is shutting down
        self._hide_overlay()
        xbmc.log("NowPlaying: service stopped", xbmc.LOGINFO)

    def onAbortRequested(self):
        self._hide_overlay()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    service = NowPlayingService()
    service.run()
