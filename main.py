"""
Spotify AI Playlist
===================
Fetches your recently played tracks, classifies them by mood using
Gemini 2.5 Flash, and auto-creates / refreshes three playlists:
  - Calm Waves
  - Pulse Energy
  - Midnight Flow

Required environment variables (set in .env locally or GitHub Secrets):
  SPOTIFY_CLIENT_ID
  SPOTIFY_CLIENT_SECRET
  SPOTIFY_REDIRECT_URI   (e.g. http://localhost:8888/callback)
  SPOTIFY_REFRESH_TOKEN  (obtained once via auth_helper.py)
  GEMINI_API_KEY
"""

import os
import json
import re
import base64
import requests
import spotipy
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# ── Playlist definitions ──────────────────────────────────────────────────────
PLAYLISTS = {
    "Calm Waves": "Relaxing, chill, and atmospheric tracks. "
                  "Perfect for unwinding, reading, or light background music.",
    "Pulse Energy": "High-energy songs for workouts, runs, or motivation. "
                    "Think upbeat, fast-tempo, or hype tracks.",
    "Midnight Flow": "Late-night, introspective, or moody tracks. "
                     "Great for night drives or midnight sessions.",
}

# ── Spotify auth using refresh token ─────────────────────────────────────────

def get_spotify_client() -> tuple[spotipy.Spotify, str]:
    """Return an authenticated Spotify client and raw access token.

    Uses a direct HTTP call to refresh the token — no SpotifyOAuth interactive
    flow, so it works safely in CI / GitHub Actions without hanging.
    """
    client_id = os.environ["SPOTIFY_CLIENT_ID"]
    client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
    refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    access_token = resp.json()["access_token"]
    return spotipy.Spotify(auth=access_token), access_token


# ── Fetch tracks from multiple sources ───────────────────────────────────────

def _track_to_dict(track: dict) -> dict:
    return {
        "id": track["id"],
        "name": track["name"],
        "artist": ", ".join(a["name"] for a in track["artists"]),
        "album": track["album"]["name"],
    }


def fetch_tracks(sp: spotipy.Spotify) -> list[dict]:
    """Merge recently played + liked songs + top tracks, deduplicated.

    Sources:
      - Recently played       : up to 50 plays  → fresh mood signal
      - Liked songs           : up to 50 tracks → your curated favourites
      - Top tracks short term : up to 50 tracks → last ~4 weeks favourites
      - Top tracks medium term: up to 50 tracks → last ~6 months favourites
    """
    seen: set[str] = set()
    tracks: list[dict] = []

    def add(track: dict) -> None:
        tid = track.get("id")
        if tid and tid not in seen:
            seen.add(tid)
            tracks.append(_track_to_dict(track))

    # 1. Recently played
    recent = sp.current_user_recently_played(limit=50)
    for item in recent["items"]:
        add(item["track"])

    # 2. Liked songs
    liked = sp.current_user_saved_tracks(limit=50)
    for item in liked["items"]:
        add(item["track"])

    # 3. Top tracks — short term (~4 weeks)
    top_short = sp.current_user_top_tracks(limit=50, time_range="short_term")
    for item in top_short["items"]:
        add(item)

    # 4. Top tracks — medium term (~6 months)
    top_medium = sp.current_user_top_tracks(limit=50, time_range="medium_term")
    for item in top_medium["items"]:
        add(item)

    return tracks


# ── Classify songs with Gemini ────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert AI musician.
Given a list of songs, classify EACH song into exactly one of these three mood categories:
- Calm Waves
- Pulse Energy
- Midnight Flow

Respond ONLY with a valid JSON object in this exact format (no markdown, no extra text):
{
  "Calm Waves": ["<track_id>", ...],
  "Pulse Energy": ["<track_id>", ...],
  "Midnight Flow": ["<track_id>", ...]
}

Every track_id from the input must appear in exactly one category.
Use only the track_id values provided — do not invent new ones."""


def classify_tracks(tracks: list[dict]) -> dict[str, list[str]]:
    """Send tracks to Gemini and return mood -> [track_id] mapping."""
    api_key = os.environ["GEMINI_API_KEY"]
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=SYSTEM_PROMPT,
    )

    songs_payload = json.dumps(tracks, ensure_ascii=False, indent=2)
    user_message = (
        f"Here are my recently played songs. Classify each one:\n\n{songs_payload}"
    )

    response = model.generate_content(user_message)
    raw = response.text.strip()

    # Strip markdown code fences if the model adds them
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    classification: dict[str, list[str]] = json.loads(raw)

    # Validate all expected keys are present
    for key in PLAYLISTS:
        classification.setdefault(key, [])

    # Drop any IDs Gemini hallucinated — keep only real input track IDs
    valid_ids = {t["id"] for t in tracks}
    classification = {
        mood: [tid for tid in ids if tid in valid_ids]
        for mood, ids in classification.items()
    }

    return classification


# ── Playlist helpers ──────────────────────────────────────────────────────────

def get_or_create_playlist(
    sp: spotipy.Spotify, user_id: str, name: str
) -> str:
    """Return the playlist ID for `name`, creating it if it doesn't exist."""
    offset = 0
    while True:
        page = sp.current_user_playlists(limit=50, offset=offset)
        for pl in page["items"]:
            if pl["name"] == name and pl["owner"]["id"] == user_id:
                url = pl.get("external_urls", {}).get("spotify", "")
                print(f"  Found existing playlist: {name} ({pl['id']})")
                print(f"  Open: {url}")
                return pl["id"]
        if page["next"] is None:
            break
        offset += 50

    new_pl = sp._post(
        "me/playlists",
        payload={
            "name": name,
            "public": True,
            "description": PLAYLISTS[name],
        },
    )
    url = new_pl.get("external_urls", {}).get("spotify", "")
    print(f"  Created new playlist: {name} ({new_pl['id']})")
    print(f"  Open: {url}")
    return new_pl["id"]


def replace_playlist_tracks(
    sp: spotipy.Spotify, playlist_id: str, track_ids: list[str]
) -> None:
    """Replace all tracks in a playlist with `track_ids`.
    """
    uris = [f"spotify:track:{tid}" for tid in track_ids]

    if not uris:
        # Clear the playlist by replacing with an empty list
        sp._put(f"playlists/{playlist_id}/items", payload={"uris": []})
        return

    for i in range(0, len(uris), 100):
        batch = uris[i : i + 100]
        if i == 0:
            # First batch: PUT replaces all existing tracks
            result = sp._put(
                f"playlists/{playlist_id}/items",
                payload={"uris": batch},
            )
        else:
            # Subsequent batches: POST appends
            result = sp._post(
                f"playlists/{playlist_id}/items",
                payload={"uris": batch},
            )



# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=== Spotify AI Playlist ===\n")

    print("Step 1/4  Connecting to Spotify...")
    sp, access_token = get_spotify_client()
    user = sp.current_user()
    user_id = user["id"]
    print(f"  Logged in as: {user['display_name']} ({user_id})\n")

    print("Step 2/4  Fetching tracks (recently played + top tracks)...")
    tracks = fetch_tracks(sp)
    print(f"  Fetched {len(tracks)} unique tracks across all sources\n")

    if not tracks:
        print("No tracks found. Exiting.")
        return

    print("Step 3/4  Classifying tracks with Gemini 2.5 Flash...")
    classification = classify_tracks(tracks)
    for mood, ids in classification.items():
        print(f"  {mood}: {len(ids)} tracks")
    print()

    print("Step 4/4  Updating Spotify playlists...")
    for mood_name in PLAYLISTS:
        track_ids = classification.get(mood_name, [])
        playlist_id = get_or_create_playlist(sp, user_id, mood_name)
        replace_playlist_tracks(sp, playlist_id, track_ids)
        print(f"  Updated '{mood_name}' with {len(track_ids)} tracks")

    print("\nDone! Your playlists have been refreshed.")


if __name__ == "__main__":
    main()
