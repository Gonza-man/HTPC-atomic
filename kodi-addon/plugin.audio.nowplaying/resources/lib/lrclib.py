"""
lrclib.py — LRCLIB API client for the Kodi Now Playing addon.

Uses only Python standard library (urllib) — no pip dependencies.
"""

import re
import urllib.request
import urllib.parse
import json

LRCLIB_BASE = "http://127.0.0.1:9999/lyrics"


def fetch_lyrics(track: str, artist: str) -> tuple:
    """
    Fetch lyrics for a track via the spotify-sidecar /lyrics endpoint.

    Returns:
        (is_synced: bool, lines: list | None)

        lines is a list of dicts: [{"time_ms": int, "text": str}, ...]
        For plain (unsynced) lyrics, a single entry with time_ms=0 is returned.
        None is returned when no lyrics are available.
    """
    params = urllib.parse.urlencode({"track": track, "artist": artist})
    url = f"{LRCLIB_BASE}?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Kodi-NowPlaying/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False, None

    synced = data.get("synced", False)
    lines = data.get("lines")
    return synced, lines


def parse_synced_lyrics(synced_str: str) -> list:
    """
    Parse LRC format '[MM:SS.xx] text' string into a sorted list of
    {"time_ms": int, "text": str} dicts.

    Used when you have raw LRC text rather than the pre-parsed sidecar response.
    """
    pattern = re.compile(r"^\[(\d+):(\d{2})\.(\d{2,3})\]\s*(.*)$")
    lines = []
    for raw in synced_str.splitlines():
        m = pattern.match(raw.strip())
        if not m:
            continue
        minutes, seconds, centis, text = m.groups()
        frac_ms = int(centis) * (10 if len(centis) == 2 else 1)
        time_ms = int(minutes) * 60_000 + int(seconds) * 1_000 + frac_ms
        lines.append({"time_ms": time_ms, "text": text})
    return sorted(lines, key=lambda x: x["time_ms"])


def get_current_line(lines: list, current_ms: int) -> str:
    """
    Given a list of synced lyric dicts and the current playback position in ms,
    return the text of the line that should currently be displayed.
    """
    if not lines:
        return ""

    current_text = ""
    for line in lines:
        if line["time_ms"] <= current_ms:
            current_text = line["text"]
        else:
            break
    return current_text
