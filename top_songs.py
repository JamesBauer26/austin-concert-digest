#!/usr/bin/env python3
"""
Weekly Top Songs — posts each person's most-played track of the past week
to a GroupMe group.

How the "top song" is computed (Spotify has no exact per-week chart):
  1. Pull the user's recently-played history (last 50 plays) and count
     plays within the past 7 days -> most-played track wins.
  2. Tie or thin history -> fall back to their short-term top track.

Env: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, USERS_JSON, GROUPME_BOT_ID
Reuses the Spotify auth from digest.py.
"""

import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

from digest import Spotify, load_users, log


def top_song_of_week(sp):
    """(track_name, artist, url, plays) for the past week, best effort."""
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    counts, meta = Counter(), {}
    try:
        data = sp.get("/me/player/recently-played", {"limit": 50})
        for item in data.get("items", []):
            played = item.get("played_at", "")
            try:
                ts = datetime.fromisoformat(played.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < week_ago:
                continue
            t = item.get("track") or {}
            tid = t.get("id")
            if not tid:
                continue
            counts[tid] += 1
            meta[tid] = t
    except Exception as e:
        log(f"  warn: recently-played failed: {e}")

    if counts:
        tid, plays = counts.most_common(1)[0]
        if plays >= 2:
            t = meta[tid]
            return (
                t.get("name", "?"),
                ", ".join(a["name"] for a in t.get("artists", [])),
                t.get("external_urls", {}).get("spotify", ""),
                plays,
            )

    # Fallback: short-term top track (~last 4 weeks)
    try:
        data = sp.get("/me/top/tracks", {"time_range": "short_term", "limit": 1})
        items = data.get("items", [])
        if items:
            t = items[0]
            return (
                t.get("name", "?"),
                ", ".join(a["name"] for a in t.get("artists", [])),
                t.get("external_urls", {}).get("spotify", ""),
                0,
            )
    except Exception as e:
        log(f"  warn: top tracks failed: {e}")
    return None


def post_groupme(text):
    r = requests.post(
        "https://api.groupme.com/v3/bots/post",
        json={"bot_id": os.environ["GROUPME_BOT_ID"], "text": text},
        timeout=20,
    )
    r.raise_for_status()


def main():
    users = load_users()
    lines, failures = [], []
    for user in users:
        log(f"=== {user['name']} ===")
        try:
            sp = Spotify(user["refresh_token"])
            song = top_song_of_week(sp)
            if song:
                name, artist, url, plays = song
                extra = f" ({plays} plays)" if plays >= 2 else ""
                line = f"{user['name']} — “{name}” by {artist}{extra}"
                if url:
                    line += f"\n{url}"
                lines.append(line)
                log(f"  {name} by {artist}")
            else:
                lines.append(f"{user['name']} — (no listening data this week)")
        except Exception as e:
            log(f"  ERROR: {e}")
            failures.append(user["name"])
            lines.append(f"{user['name']} — (Spotify hiccup, no pick this week)")

    msg = "\U0001F3A7 Songs of the week:\n\n" + "\n\n".join(lines)
    post_groupme(msg)
    log("Posted to GroupMe.")
    if failures and len(failures) == len(users):
        raise RuntimeError("All users failed")


if __name__ == "__main__":
    main()
