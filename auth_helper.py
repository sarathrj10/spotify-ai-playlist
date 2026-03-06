"""
auth_helper.py — Run this ONCE locally to get your Spotify refresh token.
==========================================================================
Usage:
  1. Copy .env.example to .env and fill in your Spotify credentials.
  2. Run:  python auth_helper.py
  3. A browser window will open. Log in and approve the permissions.
  4. Copy the REFRESH TOKEN printed in the terminal.
  5. Add it as the SPOTIFY_REFRESH_TOKEN secret in your GitHub repository.
"""

import os
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

SCOPE = "user-read-recently-played user-top-read user-library-read playlist-read-private playlist-modify-public playlist-modify-private"

auth_manager = SpotifyOAuth(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=os.environ["SPOTIFY_REDIRECT_URI"],
    scope=SCOPE,
    open_browser=True,
)

print("\nOpening Spotify login in your browser...")
print("After approving, you will be redirected to your REDIRECT_URI.")
print("Paste the full redirected URL here if prompted.\n")

token_info = auth_manager.get_access_token(as_dict=True)

print("\n" + "=" * 60)
print("SUCCESS! Copy your refresh token below and add it as")
print("SPOTIFY_REFRESH_TOKEN in your GitHub repository secrets.")
print("=" * 60)
print(f"\nREFRESH TOKEN:\n{token_info['refresh_token']}\n")
