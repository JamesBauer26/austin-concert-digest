#!/usr/bin/env python3
"""
Weekly Top Songs — posts each person's most-played track of the past week
into a GroupMe group (members can participate via SMS without the app).

How the "top song" is computed (Spotify has no exact per-week chart):
  1. Count plays in the user's recently-played history (last 50 plays)
     within the past 7 days -> most-played track wins.
  2. Thin history or missing scope -> fall back to short-term top track.

Env: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, USERS_JSON, GROUPME_BOT_ID
"""

import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

from digest import Spotify, load_users, log


def top_song_of_week(sp):
    """{"name","artist","url","art","plays"} for the past week, best effort."""
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

    track, plays = None, 0
    if counts:
        tid, n = counts.most_common(1)[0]
        if n >= 2:
            track, plays = meta[tid], n

    if track is None:
        try:
            data = sp.get("/me/top/tracks",
                          {"time_range": "short_term", "limit": 1})
            items = data.get("items", [])
            if items:
                track = items[0]
        except Exception as e:
            log(f"  warn: top tracks failed: {e}")

    if track is None:
        return None
    images = (track.get("album") or {}).get("images") or []
    return {
        "name": track.get("name", "?"),
        "artist": ", ".join(a["name"] for a in track.get("artists", [])),
        "url": track.get("external_urls", {}).get("spotify", ""),
        "art": images[-1]["url"] if images else "",
        "plays": plays,
    }


def post_groupme(text):
    r = requests.post(
        "https://api.groupme.com/v3/bots/post",
        json={"bot_id": os.environ["GROUPME_BOT_ID"], "text": text},
        timeout=20,
    )
    r.raise_for_status()


def main():
    users = load_users()
    lines, failures = [], 0
    for user in users:
        log(f"=== {user['name']} ===")
        try:
            sp = Spotify(user["refresh_token"])
            song = top_song_of_week(sp)
            if song:
                plays = (f" ({song['plays']} plays)"
                         if song["plays"] >= 2 else "")
                line = (f"{user['name']} \u2014 \u201c{song['name']}\u201d "
                        f"by {song['artist']}{plays}")
                if song["url"]:
                    line += f"\n{song['url']}"
                lines.append(line)
                log(f"  {song['name']} by {song['artist']}")
            else:
                lines.append(f"{user['name']} \u2014 (no listening data "
                             "this week \U0001F634)")
        except Exception as e:
            log(f"  ERROR: {e}")
            failures += 1
            lines.append(f"{user['name']} \u2014 (Spotify hiccup, no pick "
                         "this week)")

    msg = "\U0001F3A7 Songs of the week:\n\n" + "\n\n".join(lines)
    post_groupme(msg)
    log(f"Posted to GroupMe for {len(users)} people.")
    if failures == len(users):
        raise RuntimeError("All users failed")


if __name__ == "__main__":
    main()
