"""
spotify-sidecar — FastAPI service for the Kodi Now Playing addon.

Endpoints:
  GET /now-playing   — current Spotify track (polls /me/player/currently-playing)
  GET /lyrics        — synced lyrics from LRCLIB (?track=X&artist=Y)

Auth: set SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN
in /etc/htpc/secrets.env (passed via Quadlet EnvironmentFile).

The Spotify Web API requires user-scoped OAuth2 for /me/player endpoints.
This service uses the Authorization Code flow: obtain a refresh token once
(see README §Spotify OAuth2 setup), then this service auto-refreshes on expiry.
"""

import os
import re
import time
import urllib.parse
import base64

import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="spotify-sidecar", version="1.0.0")

# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

_token_cache: dict = {"access_token": None, "expires_at": 0.0}

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"


async def _get_access_token(client: httpx.AsyncClient) -> str:
    """Return a valid Spotify access token, refreshing if necessary."""
    now = time.time()
    # Refresh if the token expires within 60 seconds
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["access_token"]

    client_id = os.environ["SPOTIFY_CLIENT_ID"]
    client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
    refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    resp = await client.post(
        SPOTIFY_TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        content=urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }).encode(),
    )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Spotify token refresh failed: {resp.status_code} {resp.text}",
        )

    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return _token_cache["access_token"]


# ---------------------------------------------------------------------------
# /now-playing
# ---------------------------------------------------------------------------

@app.get("/now-playing")
async def now_playing():
    """
    Poll Spotify for the currently playing track.

    Returns:
      {"is_playing": false}                  — nothing playing / 204 from Spotify
      {"is_playing": true, "track": ..., ...} — full track metadata
    """
    async with httpx.AsyncClient(timeout=10) as client:
        token = await _get_access_token(client)

        resp = await client.get(
            f"{SPOTIFY_API_BASE}/me/player/currently-playing",
            headers={"Authorization": f"Bearer {token}"},
        )

        # 204: player is active but nothing is playing (or private session)
        if resp.status_code == 204:
            return {"is_playing": False}

        # 401: token was rejected — clear cache and retry once
        if resp.status_code == 401:
            _token_cache["access_token"] = None
            _token_cache["expires_at"] = 0.0
            token = await _get_access_token(client)
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/me/player/currently-playing",
                headers={"Authorization": f"Bearer {token}"},
            )

        if resp.status_code == 204:
            return {"is_playing": False}

        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Spotify API error: {resp.status_code} {resp.text}",
            )

        data = resp.json()
        item = data.get("item")

        if not item or not data.get("is_playing"):
            return {"is_playing": False}

        artists = item.get("artists", [{}])
        images = item.get("album", {}).get("images", [{}])

        return {
            "is_playing": True,
            "track": item.get("name", ""),
            "artist": artists[0].get("name", "") if artists else "",
            "album": item.get("album", {}).get("name", ""),
            "album_art_url": images[0].get("url", "") if images else "",
            "progress_ms": data.get("progress_ms", 0),
            "duration_ms": item.get("duration_ms", 0),
            # Timestamp lets the Kodi addon interpolate progress between polls
            "timestamp": time.time(),
        }


# ---------------------------------------------------------------------------
# /lyrics
# ---------------------------------------------------------------------------

LRCLIB_URL = "https://lrclib.net/api/get"


def _parse_synced_lyrics(synced_str: str) -> list[dict]:
    """Parse LRC format '[MM:SS.xx] text' into a list of {time_ms, text} dicts."""
    pattern = re.compile(r"^\[(\d+):(\d{2})\.(\d{2,3})\]\s*(.*)$")
    lines = []
    for raw in synced_str.splitlines():
        m = pattern.match(raw.strip())
        if not m:
            continue
        minutes, seconds, centis, text = m.groups()
        # Support both 2-digit (cs) and 3-digit (ms) fractional seconds
        frac_ms = int(centis) * (10 if len(centis) == 2 else 1)
        time_ms = int(minutes) * 60_000 + int(seconds) * 1_000 + frac_ms
        lines.append({"time_ms": time_ms, "text": text})
    return lines


@app.get("/lyrics")
async def lyrics(
    track: str = Query(..., description="Track name"),
    artist: str = Query(..., description="Artist name"),
):
    """
    Fetch synced lyrics from LRCLIB for the given track and artist.

    Returns:
      {"synced": true,  "lines": [{"time_ms": int, "text": str}, ...]}
      {"synced": false, "lines": [{"time_ms": 0,   "text": "<plain text>"}]}
      {"synced": false, "lines": null}   — no lyrics found
    """
    params = urllib.parse.urlencode({
        "track_name": track,
        "artist_name": artist,
    })

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{LRCLIB_URL}?{params}")

    if resp.status_code == 404:
        return {"synced": False, "lines": None}

    if resp.status_code != 200:
        # Best-effort: return empty rather than erroring
        return {"synced": False, "lines": None}

    data = resp.json()
    synced_str = data.get("syncedLyrics")

    if synced_str:
        return {"synced": True, "lines": _parse_synced_lyrics(synced_str)}

    plain = data.get("plainLyrics")
    if plain:
        # Return plain lyrics as a single entry at time 0 for compatibility
        return {"synced": False, "lines": [{"time_ms": 0, "text": plain}]}

    return {"synced": False, "lines": None}


# ---------------------------------------------------------------------------
# Entry point (for local dev / direct execution)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9999)
