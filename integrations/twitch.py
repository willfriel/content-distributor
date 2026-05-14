"""
Twitch clip integration.
Uses client credentials (no user login) to fetch top clips by streamer or game category.
Clip URLs are passed to yt-dlp in the pipeline for download.
"""

import os
import time
import requests

_BASE      = "https://api.twitch.tv/helix"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# Simple in-memory token cache
_token_cache = {"token": None, "expires_at": 0}


def _get_token() -> str | None:
    client_id     = os.environ.get("TWITCH_CLIENT_ID")
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 300:
        return _token_cache["token"]

    try:
        r = requests.post(_TOKEN_URL, params={
            "client_id":     client_id,
            "client_secret": client_secret,
            "grant_type":    "client_credentials",
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
        _token_cache["token"]      = data["access_token"]
        _token_cache["expires_at"] = time.time() + data.get("expires_in", 86400)
        return _token_cache["token"]
    except Exception as e:
        print(f"[twitch] Token fetch failed: {e}")
        return None


def _headers() -> dict | None:
    token     = _get_token()
    client_id = os.environ.get("TWITCH_CLIENT_ID")
    if not token or not client_id:
        return None
    return {"Authorization": f"Bearer {token}", "Client-Id": client_id}


def _get_user_id(login: str) -> str | None:
    headers = _headers()
    if not headers:
        return None
    try:
        r = requests.get(f"{_BASE}/users", params={"login": login},
                         headers=headers, timeout=10)
        data = r.json().get("data", [])
        return data[0]["id"] if data else None
    except Exception:
        return None


def _get_game_id(game_name: str) -> str | None:
    headers = _headers()
    if not headers:
        return None
    try:
        r = requests.get(f"{_BASE}/games", params={"name": game_name},
                         headers=headers, timeout=10)
        data = r.json().get("data", [])
        return data[0]["id"] if data else None
    except Exception:
        return None


def get_clips_by_streamer(login: str, niche: str, max_results: int = 5,
                          started_at: str = None) -> list[dict]:
    """Fetch top clips from a specific streamer."""
    headers = _headers()
    if not headers:
        return []

    broadcaster_id = _get_user_id(login)
    if not broadcaster_id:
        print(f"[twitch] Streamer not found: {login}")
        return []

    try:
        params = {"broadcaster_id": broadcaster_id, "first": max_results}
        if started_at:
            params["started_at"] = started_at  # RFC3339 e.g. "2025-01-01T00:00:00Z"

        r = requests.get(f"{_BASE}/clips", params=params,
                         headers=headers, timeout=10)
        r.raise_for_status()
        clips = r.json().get("data", [])

        return [
            {
                "url":         clip["url"],
                "title":       clip["title"],
                "views":       clip["view_count"],
                "duration":    clip["duration"],
                "source_type": "twitch",
                "niche":       niche,
                "streamer":    login,
                "clip_id":     clip["id"],
            }
            for clip in clips
            if clip.get("url") and clip.get("duration", 0) <= 60
        ]
    except Exception as e:
        print(f"[twitch] Clip fetch failed for {login}: {e}")
        return []


def get_clips_by_game(game_name: str, niche: str, max_results: int = 5) -> list[dict]:
    """Fetch top clips from a game/category."""
    headers = _headers()
    if not headers:
        return []

    game_id = _get_game_id(game_name)
    if not game_id:
        print(f"[twitch] Game not found: {game_name}")
        return []

    try:
        r = requests.get(f"{_BASE}/clips",
                         params={"game_id": game_id, "first": max_results},
                         headers=headers, timeout=10)
        r.raise_for_status()
        clips = r.json().get("data", [])

        return [
            {
                "url":         clip["url"],
                "title":       clip["title"],
                "views":       clip["view_count"],
                "duration":    clip["duration"],
                "source_type": "twitch",
                "niche":       niche,
                "game":        game_name,
                "clip_id":     clip["id"],
            }
            for clip in clips
            if clip.get("url") and clip.get("duration", 0) <= 60
        ]
    except Exception as e:
        print(f"[twitch] Game clip fetch failed for {game_name}: {e}")
        return []
