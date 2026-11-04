#!/usr/bin/env python3
"""Collect Spotify taste signals locally using OAuth Authorization Code + PKCE.

This does not download music or send Spotify credentials anywhere. It writes
only normalized listening signals and playlist metadata to the local output
file; the OAuth refresh token stays in the user's home directory.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


REDIRECT_URI = "http://127.0.0.1:8765/callback"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
SCOPES = " ".join(
    (
        "user-top-read",
        "user-read-recently-played",
        "playlist-read-private",
        "playlist-read-collaborative",
        "user-library-read",
        "user-follow-read",
    )
)
TOKEN_PATH = Path.home() / ".config" / "cineswarm" / "spotify_token.json"


def json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        body = urllib.parse.urlencode(payload).encode()
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(
        url, data=body, headers=request_headers, method=method
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def load_token() -> dict[str, Any] | None:
    try:
        return json.loads(TOKEN_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_token(token: dict[str, Any]) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(token, indent=2) + "\n")
    TOKEN_PATH.chmod(0o600)


def exchange_code(client_id: str, code: str, verifier: str) -> dict[str, Any]:
    token = json_request(
        TOKEN_URL,
        method="POST",
        payload={
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600))
    save_token(token)
    return token


def refresh_token(client_id: str, token: dict[str, Any]) -> dict[str, Any]:
    refreshed = json_request(
        TOKEN_URL,
        method="POST",
        payload={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        },
    )
    refreshed["refresh_token"] = refreshed.get(
        "refresh_token", token["refresh_token"]
    )
    refreshed["expires_at"] = int(time.time()) + int(
        refreshed.get("expires_in", 3600)
    )
    save_token(refreshed)
    return refreshed


def authorize(client_id: str) -> dict[str, Any]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    callback: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query
            )
            callback.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"Spotify authorization received. You can close this window.\n"
            )

        def log_message(self, *_args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 8765), CallbackHandler)
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    url = f"{AUTH_URL}?{query}"
    print("Opening Spotify authorization in your browser...")
    print(url)
    webbrowser.open(url)

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=300)
    server.server_close()
    if callback.get("state") != state:
        raise RuntimeError("Spotify authorization state did not match")
    if "error" in callback:
        raise RuntimeError(f"Spotify authorization failed: {callback['error']}")
    if not callback.get("code"):
        raise RuntimeError("Timed out waiting for Spotify authorization")
    return exchange_code(client_id, callback["code"], verifier)


def spotify_get(token: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{API_URL}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        return json_request(url, headers={"Authorization": f"Bearer {token}"})
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("Spotify token expired or was revoked") from exc
        raise


def collect(client_id: str) -> dict[str, Any]:
    token = load_token()
    if not token or token.get("expires_at", 0) <= time.time() + 60:
        if token and token.get("refresh_token"):
            token = refresh_token(client_id, token)
        else:
            token = authorize(client_id)
    access_token = token["access_token"]
    result: dict[str, Any] = {
        "collected_at": int(time.time()),
        "profile": spotify_get(access_token, "me"),
        "top_artists": {},
        "top_tracks": {},
        "recently_played": spotify_get(
            access_token, "me/player/recently-played", {"limit": 50}
        ).get("items", []),
        "playlists": spotify_get(
            access_token, "me/playlists", {"limit": 50}
        ).get("items", []),
        "saved_tracks": spotify_get(
            access_token, "me/tracks", {"limit": 50}
        ).get("items", []),
        "followed_artists": spotify_get(
            access_token, "me/following", {"type": "artist", "limit": 50}
        ).get("artists", {}).get("items", []),
    }
    for time_range in ("short_term", "medium_term", "long_term"):
        result["top_artists"][time_range] = spotify_get(
            access_token,
            "me/top/artists",
            {"time_range": time_range, "limit": 50},
        ).get("items", [])
        result["top_tracks"][time_range] = spotify_get(
            access_token,
            "me/top/tracks",
            {"time_range": time_range, "limit": 50},
        ).get("items", [])
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", default=os.environ.get("SPOTIFY_CLIENT_ID"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / ".local/share/cineswarm/spotify_seed.json",
    )
    args = parser.parse_args()
    if not args.client_id:
        parser.error("set SPOTIFY_CLIENT_ID or pass --client-id")
    result = collect(args.client_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    profile = result["profile"]
    print(f"Collected Spotify signals for {profile.get('display_name', 'user')}.")
    print(
        "Top artists: "
        + ", ".join(
            artist["name"]
            for artist in result["top_artists"]["medium_term"][:10]
        )
    )
    print(f"Saved local seed data to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
