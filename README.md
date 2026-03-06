# Spotify AI Playlist

An AI-powered, fully serverless playlist manager for Spotify.

Every Monday it automatically:
1. Fetches your **recently played + top tracks** (up to ~150 unique songs across 3 sources)
2. Sends them to **Gemini 2.5 Flash** for mood classification
3. Refreshes **3 playlists** on your Spotify account

| Playlist | Vibe |
|---|---|
| 🌊 Calm Waves | Chill, relaxing, atmospheric |
| ⚡ Pulse Energy | Workout, hype, high-energy |
| 🌙 Midnight Flow | Late-night, moody, introspective |

**Cost: ₹0 / $0 — runs entirely on GitHub Actions free tier.**

---

## Setup Guide

### Step 1 — Get Spotify API credentials

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click **Create App**
3. Fill in any name/description
4. Set **Redirect URI** to `http://127.0.0.1:8888/callback`
5. Copy your **Client ID** and **Client Secret**

---

### Step 2 — Get a Gemini API key

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Click **Create API Key**
3. Copy the key

---

### Step 3 — Set up locally & get your Spotify Refresh Token

```bash
# Clone the repo
git clone https://github.com/<your-username>/spotify-ai-playlist.git
cd spotify-ai-playlist

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy the example env file
cp .env.example .env
```

Open `.env` and fill in all five values:

```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIFY_REFRESH_TOKEN=          # leave blank for now
GEMINI_API_KEY=...
```

Now run the auth helper **once** to get your refresh token:

```bash
python auth_helper.py
```

A browser window will open. Log in with Spotify and approve the permissions.
The terminal will print your **REFRESH TOKEN** — copy it and paste it into `.env` as `SPOTIFY_REFRESH_TOKEN`.

> **Note:** The app requests these Spotify permissions: `user-read-recently-played`, `user-top-read`, `user-library-read`, `playlist-modify-public`, `playlist-modify-private`.

---

### Step 4 — Test locally

```bash
python main.py
```

You should see output like:

```
=== Spotify AI Playlist ===

Step 1/4  Connecting to Spotify...
  Logged in as: Your Name (your_username)

Step 2/4  Fetching tracks (recently played + top tracks)...
  Fetched 112 unique tracks across all sources

Step 3/4  Classifying tracks with Gemini 2.5 Flash...
  Calm Waves: 15 songs
  Pulse Energy: 14 songs
  Midnight Flow: 13 songs

Step 4/4  Updating Spotify playlists...
  Created new playlist: Calm Waves (...)
  Created new playlist: Pulse Energy (...)
  Created new playlist: Midnight Flow (...)

Done! Your playlists have been refreshed.
```

Check your Spotify app — the three playlists should now appear.

---

### Step 5 — Deploy to GitHub Actions (free automation)

1. Push this repo to GitHub
2. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
3. Add the following secrets one by one:

| Secret Name | Value |
|---|---|
| `SPOTIFY_CLIENT_ID` | From Step 1 |
| `SPOTIFY_CLIENT_SECRET` | From Step 1 |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8888/callback` |
| `SPOTIFY_REFRESH_TOKEN` | From Step 3 |
| `GEMINI_API_KEY` | From Step 2 |

4. Go to **Actions** tab → click **Spotify AI Playlist Refresh** → **Run workflow** to test it manually.

From now on, it runs automatically **every Monday at 9 AM UTC**.

---

## Running Locally Anytime

You can also run the script manually whenever you want:

```bash
source .venv/bin/activate
python main.py
```

This will immediately fetch your recent plays, classify them, and update the three playlists — no need to wait for Monday.

---

## Project Structure

```
spotify-ai-playlist/
├── .github/
│   └── workflows/
│       └── playlist-refresh.yml   # GitHub Actions schedule
├── main.py                   # Core script
├── auth_helper.py            # One-time Spotify token setup
├── requirements.txt
├── .env.example
└── README.md
```

## Tech Stack

| Layer | Tool |
|---|---|
| Music data | [spotipy](https://spotipy.readthedocs.io/) (Spotify Web API) |
| AI reasoning | Gemini 2.5 Flash |
| Automation | GitHub Actions |

## Data Sources

| Source | API | Coverage |
|---|---|---|
| Recently played | `current_user_recently_played` | Last few days — fresh signal |
| Liked songs | `current_user_saved_tracks` | Your curated favourites |
| Top tracks (short term) | `current_user_top_tracks(short_term)` | Last ~4 weeks |
| Top tracks (medium term) | `current_user_top_tracks(medium_term)` | Last ~6 months |

All three are merged and deduplicated before being sent to Gemini, giving a broader and more representative picture of your taste.
