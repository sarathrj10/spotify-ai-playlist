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
import random
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


def fetch_tracks(sp: spotipy.Spotify) -> tuple[list[dict], dict[str, int]]:
    """Merge recently played + liked songs + top tracks, deduplicated.

    Returns:
      tracks          : deduplicated list of track dicts
      source_priority : {track_id: priority} — lower = stronger mood signal
                        1=recently played, 2=liked, 3=top short, 4=top medium
    """
    seen: set[str] = set()
    tracks: list[dict] = []
    source_priority: dict[str, int] = {}

    def add(track: dict, priority: int) -> None:
        tid = track.get("id")
        if tid and tid not in seen:
            seen.add(tid)
            tracks.append(_track_to_dict(track))
            source_priority[tid] = priority

    # 1. Recently played — strongest signal (priority 1)
    recent = sp.current_user_recently_played(limit=50)
    for item in recent["items"]:
        add(item["track"], 1)

    # 2. Liked songs — explicit saves (priority 2)
    liked = sp.current_user_saved_tracks(limit=50)
    for item in liked["items"]:
        add(item["track"], 2)

    # 3. Top tracks — short term (~4 weeks) (priority 3)
    top_short = sp.current_user_top_tracks(limit=50, time_range="short_term")
    for item in top_short["items"]:
        add(item, 3)

    # 4. Top tracks — medium term (~6 months) (priority 4)
    top_medium = sp.current_user_top_tracks(limit=50, time_range="medium_term")
    for item in top_medium["items"]:
        add(item, 4)

    return tracks, source_priority


def pick_seeds(
    tracks_by_id: dict[str, dict],
    classified_ids: list[str],
    source_priority: dict[str, int],
    n: int = 10,
) -> list[dict]:
    """Pick up to n seed tracks from classified_ids for a mood.

    Sorts by source priority so recently played come first (strongest signal),
    then liked songs, then top tracks.
    """
    candidates = [tid for tid in classified_ids if tid in tracks_by_id]
    candidates.sort(key=lambda tid: source_priority.get(tid, 99))
    return [tracks_by_id[tid] for tid in candidates[:n]]


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


# ── Discovery: find similar artists via Gemini, search tracks on Spotify ────────

DISCOVERY_PROMPT = """You are a music expert.
Given a list of seed songs that fit a specific mood, suggest 6 artists the listener would enjoy but has NOT heard yet.
The artists must closely match the mood and style of the seed songs.
Respond ONLY with a valid JSON array of artist name strings, no extra text:
["Artist 1", "Artist 2", "Artist 3", "Artist 4", "Artist 5", "Artist 6"]"""


def discover_similar_tracks(
    sp: spotipy.Spotify,
    seed_tracks: list[dict],
    known_ids: set[str],
    mood_name: str,
    target_count: int = 30,
) -> list[str]:
    """Ask Gemini for similar artists, search Spotify, return fresh track IDs."""
    api_key = os.environ["GEMINI_API_KEY"]
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=DISCOVERY_PROMPT,
    )

    payload = json.dumps(seed_tracks, ensure_ascii=False, indent=2)
    response = model.generate_content(
        f"Mood: {mood_name}\n\nSeed songs:\n{payload}"
    )

    raw = response.text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    suggested_artists: list[str] = json.loads(raw)
    print(f"    Similar artists: {', '.join(suggested_artists)}")

    discovered: list[str] = []
    seen_discovered: set[str] = set()

    for artist in suggested_artists:
        if len(discovered) >= target_count:
            break
        results = sp.search(q=f"artist:{artist}", type="track", limit=10)
        for item in results["tracks"]["items"]:
            tid = item.get("id")
            if tid and tid not in known_ids and tid not in seen_discovered:
                seen_discovered.add(tid)
                discovered.append(tid)

    return discovered[:target_count]


# ── Blend helpers ────────────────────────────────────────────────────


def blend_playlist(existing_ids: list[str], discovered_ids: list[str], total: int = 50) -> list[str]:
    """Return a shuffled blend: 40% familiar tracks + 60% fresh discoveries."""
    want_existing = round(total * 0.4)
    want_discovered = total - want_existing
    blended = existing_ids[:want_existing] + discovered_ids[:want_discovered]
    random.shuffle(blended)
    return blended


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

    print("Step 1/5  Connecting to Spotify...")
    sp, _ = get_spotify_client()
    user = sp.current_user()
    user_id = user["id"]
    print(f"  Logged in as: {user['display_name']} ({user_id})\n")

    print("Step 2/5  Fetching tracks (recently played + liked + top tracks)...")
    tracks, source_priority = fetch_tracks(sp)
    print(f"  Fetched {len(tracks)} unique tracks across all sources\n")

    if not tracks:
        print("No tracks found. Exiting.")
        return

    tracks_by_id = {t["id"]: t for t in tracks}
    known_ids = set(tracks_by_id.keys())

    print("Step 3/5  Classifying tracks with Gemini 2.5 Flash...")
    classification = classify_tracks(tracks)
    for mood, ids in classification.items():
        print(f"  {mood}: {len(ids)} tracks")
    print()

    print("Step 4/5  Discovering similar tracks via Gemini + Spotify Search...")
    discovered: dict[str, list[str]] = {}
    for mood_name, classified_ids in classification.items():
        print(f"  [{mood_name}]")
        seeds = pick_seeds(tracks_by_id, classified_ids, source_priority, n=10)
        if not seeds:
            print("    No seeds available, skipping discovery.")
            discovered[mood_name] = []
            continue
        new_tracks = discover_similar_tracks(sp, seeds, known_ids, mood_name, target_count=30)
        discovered[mood_name] = new_tracks
        print(f"    Found {len(new_tracks)} fresh tracks")
    print()

    print("Step 5/5  Blending and updating Spotify playlists...")
    for mood_name in PLAYLISTS:
        existing_ids = classification.get(mood_name, [])
        new_ids = discovered.get(mood_name, [])
        final_ids = blend_playlist(existing_ids, new_ids, total=50)
        playlist_id = get_or_create_playlist(sp, user_id, mood_name)
        replace_playlist_tracks(sp, playlist_id, final_ids)
        existing_count = min(len(existing_ids), round(50 * 0.4))
        new_count = len(final_ids) - existing_count
        print(f"  '{mood_name}': {existing_count} familiar + {new_count} fresh = {len(final_ids)} tracks")

    print("\nDone! Your playlists have been refreshed.")


if __name__ == "__main__":
    main()
