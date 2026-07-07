#!/usr/bin/env python3
"""
One-time helper: get a Spotify refresh token for the digest.

1. Create an app at https://developer.spotify.com/dashboard
   - Redirect URI must be exactly: http://127.0.0.1:8888/callback
2. Run:  python get_refresh_token.py
   (paste your Client ID and Secret when prompted)
3. A browser opens; log in and approve. The refresh token prints here.
4. Save it as the SPOTIFY_REFRESH_TOKEN secret in your GitHub repo.
"""

import base64
import http.server
import threading
import urllib.parse
import webbrowser

import requests

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "user-top-read user-follow-read user-library-read"

code_holder = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code_holder["code"] = q.get("code", [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<h2>Done! You can close this tab and return to the terminal.</h2>"
        )

    def log_message(self, *args):
        pass


def main():
    client_id = input("Spotify Client ID: ").strip()
    client_secret = input("Spotify Client Secret: ").strip()

    server = http.server.HTTPServer(("127.0.0.1", 8888), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
        }
    )
    print("\nOpening browser for Spotify login...")
    print(f"(If it doesn't open, visit:\n{auth_url})\n")
    webbrowser.open(auth_url)

    while "code" not in code_holder:
        pass  # wait for the callback

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code_holder["code"],
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Authorization": f"Basic {basic}"},
        timeout=30,
    )
    r.raise_for_status()
    token = r.json()["refresh_token"]
    print("=" * 60)
    print("Your SPOTIFY_REFRESH_TOKEN (save as a GitHub secret):\n")
    print(token)
    print("=" * 60)


if __name__ == "__main__":
    main()
