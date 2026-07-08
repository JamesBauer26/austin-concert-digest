#!/usr/bin/env python3
"""
Weekly Top Songs — one group email to everyone in USERS_JSON with each
person's most-played track of the past week. Everyone is on the To: line,
so reply-all works like a group thread.

How the "top song" is computed (Spotify has no exact per-week chart):
  1. Count plays in the user's recently-played history (last 50 plays)
     within the past 7 days -> most-played track wins.
  2. Thin history or missing scope -> fall back to short-term top track.

Env: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, USERS_JSON,
     GMAIL_ADDRESS, GMAIL_APP_PASSWORD
"""

import os
import smtplib
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from digest import Spotify, esc, load_users, log


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


def render(picks, today):
    rows = []
    for user_name, song in picks:
        if song is None:
            rows.append(
                "<tr><td colspan='2' style='padding:10px 12px;color:#9a9aa8;"
                "font-family:Helvetica,Arial,sans-serif;font-size:14px'>"
                f"{esc(user_name)} — no listening data this week 😴</td></tr>"
            )
            continue
        art = (
            f"<img src='{esc(song['art'])}' width='56' height='56' alt='' "
            "style='border-radius:8px;display:block'>"
            if song["art"] else
            "<div style='width:56px;height:56px;border-radius:8px;"
            "background:#2a2a35'></div>"
        )
        plays = (f" <span style='color:#9a9aa8;font-weight:normal'>"
                 f"({song['plays']} plays)</span>" if song["plays"] >= 2 else "")
        title = esc(song["name"])
        if song["url"]:
            title = (f"<a href='{esc(song['url'])}' style='color:#1DB954;"
                     f"text-decoration:none'>{title}</a>")
        rows.append(
            "<tr>"
            f"<td width='56' style='padding:10px 0 10px 12px'>{art}</td>"
            "<td style='padding:10px 12px;font-family:Helvetica,Arial,"
            "sans-serif'>"
            f"<div style='color:#9a9aa8;font-size:12px'>{esc(user_name)}'s "
            "song of the week</div>"
            f"<div style='color:#fff;font-size:16px;font-weight:bold'>"
            f"{title}{plays}</div>"
            f"<div style='color:#b8b8c4;font-size:13px'>{esc(song['artist'])}"
            "</div></td></tr>"
        )
    return (
        "<div style='background:#101016;padding:24px 8px'>"
        "<div style='max-width:520px;margin:auto'>"
        "<h1 style='font-family:Helvetica,Arial,sans-serif;font-size:20px;"
        "color:#fff;margin:0 0 14px 0'>🎧 Songs of the week</h1>"
        "<table role='presentation' width='100%' cellpadding='0' "
        "cellspacing='0' style='background:#181820;border-radius:12px'>"
        + "".join(rows) +
        "</table>"
        "<p style='font-family:Helvetica,Arial,sans-serif;color:#55556a;"
        "font-size:11px;margin-top:14px'>Reply-all with your hot takes. "
        "Sent every Monday from your Spotify listening.</p>"
        "</div></div>"
    )


def main():
    users = load_users()
    picks, failures = [], 0
    for user in users:
        log(f"=== {user['name']} ===")
        try:
            sp = Spotify(user["refresh_token"])
            song = top_song_of_week(sp)
            picks.append((user["name"], song))
            if song:
                log(f"  {song['name']} by {song['artist']}")
        except Exception as e:
            log(f"  ERROR: {e}")
            picks.append((user["name"], None))
            failures += 1

    today = datetime.now().strftime("%b %-d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎧 Songs of the week — {today}"
    msg["From"] = os.environ["GMAIL_ADDRESS"]
    msg["To"] = ", ".join(u["email"] for u in users)
    msg.attach(MIMEText(render(picks, today), "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)
    log(f"Sent to {len(users)} people.")
    if failures == len(users):
        raise RuntimeError("All users failed")


if __name__ == "__main__":
    main()
