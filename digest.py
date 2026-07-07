#!/usr/bin/env python3
"""
Austin Concert Digest — multi-user, email + web page
Scrapes austin.showlists.net once, then for EACH user: matches shows against
their Spotify listening, emails a card-style personalized digest, and builds
an interactive web page (published via GitHub Pages by the workflow).

Config via environment variables:
  SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET   (one shared Spotify app)
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD          (sender account)
  USERS_JSON  — JSON list of users:
      [{"name": "James", "email": "you@x.com", "refresh_token": "..."}, ...]
      optional per-user "slug" to pin their page URL
  (Legacy single-user fallback: SPOTIFY_REFRESH_TOKEN + DIGEST_TO)
Optional:
  LOOKAHEAD_DAYS (default 14), MIN_SCORE (default 1)
  BUILD_SITE ("1" default — writes ./site for GitHub Pages)
  PAGES_BASE_URL (auto-derived from GITHUB_REPOSITORY when in Actions)
"""

import hashlib
import html
import json
import os
import re
import smtplib
import time
import unicodedata
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

SHOWLIST_URL = "https://austin.showlists.net/"
SPOTIFY_API = "https://api.spotify.com/v1"
LOOKAHEAD_DAYS = int(os.environ.get("LOOKAHEAD_DAYS", "14"))
MIN_SCORE = float(os.environ.get("MIN_SCORE", "1"))

# Shared across users so each artist is only looked up once per run.
ARTIST_CACHE = {}   # norm name -> artist info dict or None
TRACKS_CACHE = {}   # artist id -> [{"name","url","preview"}]

SKIP_KEYWORDS = [
    "world cup", "bingo", "trivia", "karaoke", "open mic", "comedy",
    "storytime", "gameshow", "poet", "reading", "taco tuesday", "market",
    "watch party", "drag brunch",
]
TRIBUTE_KEYWORDS = ["tribute", "covers", "plays the music of", "songs of"]

GENRE_FAMILIES = {
    "Hip-Hop / R&B": [
        "hip hop", "hip-hop", "rap", "trap", "drill", "r&b", "rnb",
        "neo soul", "neo-soul", "urban", "grime",
    ],
    "Indie / Synth / Electronic": [
        "indie", "synth", "dream pop", "shoegaze", "electronic", "electronica",
        "house", "techno", "edm", "dance", "indietronica", "chillwave",
        "new wave", "psych", "garage rock", "lo-fi", "bedroom", "art pop",
        "alternative", "post-punk", "new rave", "downtempo", "ambient pop",
    ],
    "Punk / Metal / Hardcore": [
        "punk", "metal", "hardcore", "emo", "screamo", "metalcore",
        "post-hardcore", "grunge", "thrash", "death metal", "doom", "sludge",
        "powerviolence", "ska",
    ],
    "Classic / Rock / Singer-Songwriter": [
        "classic rock", "soft rock", "rock", "singer-songwriter", "piano",
        "folk", "americana", "country", "blues", "soul", "funk", "pop rock",
        "mellow gold", "yacht rock", "adult standards",
    ],
    "Pop": [
        "pop", "dance pop", "electropop", "k-pop", "latin",
    ],
}


def log(msg):
    print(msg, flush=True)


def norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().strip()
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def similar(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def user_slug(user):
    if user.get("slug"):
        return user["slug"]
    raw = (user["email"] + user["refresh_token"]).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def pages_base_url():
    if os.environ.get("PAGES_BASE_URL"):
        return os.environ["PAGES_BASE_URL"].rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}"
    return ""


def load_users():
    raw = os.environ.get("USERS_JSON", "").strip()
    if raw:
        users = json.loads(raw)
        for u in users:
            for field in ("name", "email", "refresh_token"):
                if not u.get(field):
                    raise ValueError(f"USERS_JSON entry missing '{field}'")
        return users
    return [
        {
            "name": "you",
            "email": os.environ["DIGEST_TO"],
            "refresh_token": os.environ["SPOTIFY_REFRESH_TOKEN"],
        }
    ]


# ---------------------------------------------------------------- Spotify ---

def spotify_token(refresh_token):
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


class Spotify:
    def __init__(self, refresh_token):
        self.headers = {"Authorization": f"Bearer {spotify_token(refresh_token)}"}

    def get(self, path, params=None):
        for attempt in range(4):
            r = requests.get(
                SPOTIFY_API + path, headers=self.headers, params=params, timeout=30
            )
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "2")) + 1)
                continue
            if r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Spotify GET {path} kept failing")

    def my_artists(self):
        """{norm_name: {"name","genres","rank"}} from top/followed/saved."""
        artists = {}

        def add(name, genres, rank):
            key = norm(name)
            if not key:
                return
            if key not in artists or rank < artists[key]["rank"]:
                artists[key] = {"name": name, "genres": genres or [], "rank": rank}
            elif genres and not artists[key]["genres"]:
                artists[key]["genres"] = genres

        for i, rng in enumerate(["short_term", "medium_term", "long_term"]):
            try:
                data = self.get("/me/top/artists", {"time_range": rng, "limit": 50})
                for pos, a in enumerate(data.get("items", [])):
                    add(a["name"], a.get("genres"), i * 100 + pos)
            except Exception as e:
                log(f"  warn: top artists ({rng}) failed: {e}")

        try:
            after = None
            while True:
                params = {"type": "artist", "limit": 50}
                if after:
                    params["after"] = after
                data = self.get("/me/following", params).get("artists", {})
                for a in data.get("items", []):
                    add(a["name"], a.get("genres"), 500)
                after = data.get("cursors", {}).get("after")
                if not after:
                    break
        except Exception as e:
            log(f"  warn: followed artists failed: {e}")

        try:
            ids = {}
            for offset in range(0, 200, 50):
                data = self.get("/me/tracks", {"limit": 50, "offset": offset})
                for item in data.get("items", []):
                    for a in item["track"]["artists"]:
                        ids[a["id"]] = a["name"]
                if not data.get("next"):
                    break
            id_list = list(ids)
            for i in range(0, len(id_list), 50):
                data = self.get("/artists", {"ids": ",".join(id_list[i : i + 50])})
                for a in data.get("artists", []):
                    if a:
                        add(a["name"], a.get("genres"), 700)
        except Exception as e:
            log(f"  warn: saved tracks failed: {e}")

        noise = ("white noise", "sleep", "rain sounds", "brown noise", "asmr")
        return {
            k: v
            for k, v in artists.items()
            if not any(n in k or any(n in g for g in v["genres"]) for n in noise)
        }

    def lookup_artist(self, name):
        """Search Spotify for an artist (cached across users)."""
        key = norm(name)
        if key in ARTIST_CACHE:
            return ARTIST_CACHE[key]
        result = None
        try:
            data = self.get("/search", {"q": name, "type": "artist", "limit": 3})
            best, best_sim = None, 0.0
            for a in data.get("artists", {}).get("items", []):
                s = similar(a["name"], name)
                if s > best_sim:
                    best, best_sim = a, s
            if best and best_sim >= 0.85:
                images = best.get("images") or []
                result = {
                    "id": best["id"],
                    "name": best["name"],
                    "genres": best.get("genres", []),
                    "popularity": best.get("popularity", 0),
                    "url": best.get("external_urls", {}).get("spotify", ""),
                    "image": (images[-1]["url"] if images else ""),  # smallest
                    "image_lg": (images[0]["url"] if images else ""),
                }
        except Exception:
            pass
        ARTIST_CACHE[key] = result
        time.sleep(0.1)
        return result

    def top_tracks(self, artist_id, n=3):
        """[{"name","url","preview"}] for an artist (cached across users)."""
        if artist_id not in TRACKS_CACHE:
            tracks = []
            try:
                data = self.get(
                    f"/artists/{artist_id}/top-tracks", {"market": "US"}
                )
                for t in data.get("tracks", []):
                    tracks.append(
                        {
                            "name": t["name"],
                            "url": t.get("external_urls", {}).get("spotify", ""),
                            "preview": t.get("preview_url") or "",
                        }
                    )
            except Exception:
                pass
            TRACKS_CACHE[artist_id] = tracks
        return TRACKS_CACHE[artist_id][:n]


# --------------------------------------------------------------- Showlist ---

def fetch_shows(html_text=None):
    if html_text is None:
        r = requests.get(
            SHOWLIST_URL,
            headers={"User-Agent": "Mozilla/5.0 (concert-digest personal script)"},
            timeout=60,
        )
        r.raise_for_status()
        html_text = r.text
    soup = BeautifulSoup(html_text, "html.parser")

    shows = []
    current_date = None
    date_re = re.compile(
        r"(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})"
    )

    for el in soup.find_all(["h5", "li"]):
        if el.name == "h5":
            m = date_re.search(el.get_text(" ", strip=True))
            if m:
                current_date = datetime.strptime(
                    f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
                ).date()
            continue
        if current_date is None:
            continue
        anchors = el.find_all("a")
        show_a = next(
            (a for a in anchors if (a.get("title") or "") == "show link"), None
        )
        venue_a = next(
            (a for a in anchors if (a.get("title") or "") == "venue link"), None
        )
        if not show_a:
            continue
        title = show_a.get_text(" ", strip=True)
        if not title:
            continue
        venue = venue_a.get_text(" ", strip=True) if venue_a else ""
        text = el.get_text(" ", strip=True)
        tm = re.search(r"\[?(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\]?\s*$", text)
        shows.append(
            {
                "date": current_date,
                "title": title,
                "artists": split_artists(title),
                "venue": venue,
                "time": tm.group(1).lower() if tm else "",
                "link": show_a.get("href", SHOWLIST_URL),
            }
        )
    return shows


def split_artists(title):
    t = title
    t = re.sub(r"(?i)sold out\s*[–-]?\s*", "", t)
    t = re.sub(r"\(.*?\)", "", t)
    if ":" in t:
        head, tail = t.split(":", 1)
        if re.search(
            r"(?i)anniversary|presents|night|fest|hsn|rrcd|tour|release|"
            r"residency|showcase|celebration|party",
            head,
        ) and tail.strip():
            t = tail
    parts = re.split(
        r"\s+w\s*/\s*|\s+with\s+|\s*/\s*|,|\s+ft\.?\s+|\s+feat\.?\s+", t
    )
    out = []
    for p in parts:
        p = re.sub(r"(?i)\b(and more|& more|more|tba|special guests?)\b", "", p)
        p = re.sub(
            r"(?i)\s+(monday|tuesday|wednesday|thursday|friday|saturday|"
            r"sunday)?\s*(solo\s+)?residency\b.*$",
            "",
            p,
        )
        p = p.strip(" -–—&+!.")
        if len(p) >= 3 and not p.isdigit():
            out.append(p)
    return out


def is_skippable(title):
    tl = title.lower()
    return any(k in tl for k in SKIP_KEYWORDS)


def is_tribute(title):
    tl = title.lower()
    return any(k in tl for k in TRIBUTE_KEYWORDS)


# --------------------------------------------------------------- Matching ---

def genre_families(genres):
    fams = set()
    for g in genres:
        gl = g.lower()
        for fam, keywords in GENRE_FAMILIES.items():
            if any(k in gl for k in keywords):
                fams.add(fam)
    return fams


def build_profile(my_artists):
    weights = {f: 0.0 for f in GENRE_FAMILIES}
    for a in my_artists.values():
        w = 3.0 if a["rank"] < 300 else (2.0 if a["rank"] < 600 else 1.0)
        for fam in genre_families(a["genres"]):
            weights[fam] += w
    total = sum(weights.values()) or 1.0
    return {f: w / total for f, w in weights.items()}


def similar_listened(rec_genres, my_artists, n=3):
    rec_fams = genre_families(rec_genres)
    rec_tokens = set(t for g in rec_genres for t in g.lower().split())
    scored = []
    for a in my_artists.values():
        toks = set(t for g in a["genres"] for t in g.lower().split())
        overlap = len(rec_tokens & toks)
        fam_overlap = len(rec_fams & genre_families(a["genres"]))
        if overlap or fam_overlap:
            scored.append((-(overlap * 2 + fam_overlap), a["rank"], a["name"]))
    scored.sort()
    return [name for _, _, name in scored[:n]]


# ---------------------------------------------------------------- Compute ---

def compute_user_data(user, shows, sp=None):
    """Everything needed to render one user's email + page. Dates -> ISO."""
    sp = sp or Spotify(user["refresh_token"])
    mine = sp.my_artists()
    profile = build_profile(mine)
    log(f"  {len(mine)} artists; taste "
        f"{ {k: round(v, 2) for k, v in profile.items() if v} }")

    matches = []
    for show in shows:
        if is_skippable(show["title"]):
            continue
        for cand in show["artists"]:
            for a in mine.values():
                if similar(cand, a["name"]) >= 0.92:
                    info = sp.lookup_artist(a["name"]) or {}
                    matches.append(
                        {
                            "artist": a["name"],
                            "title": show["title"],
                            "date": show["date"].isoformat(),
                            "venue": show["venue"],
                            "time": show["time"],
                            "link": show["link"],
                            "image": info.get("image", ""),
                            "spotify_url": info.get("url", ""),
                        }
                    )
                    break

    matched_titles = {m["title"] for m in matches}

    discover = {}
    for show in shows:
        if show["title"] in matched_titles or is_skippable(show["title"]):
            continue
        if is_tribute(show["title"]):
            continue
        for cand in show["artists"]:
            key = norm(cand)
            if not key or key in discover or key in mine:
                continue
            info = sp.lookup_artist(cand)
            if not info or not info["genres"]:
                continue
            fams = genre_families(info["genres"])
            score = sum(profile.get(f, 0) * 10 for f in fams)
            if score < MIN_SCORE:
                continue
            family = max(fams, key=lambda f: profile.get(f, 0))
            discover[key] = {
                "name": info["name"],
                "genres": info["genres"][:3],
                "family": family,
                "score": round(score + info["popularity"] / 100.0, 3),
                "similar": similar_listened(info["genres"], mine),
                "songs": sp.top_tracks(info["id"]),
                "spotify_url": info["url"],
                "image": info["image"],
                "date": show["date"].isoformat(),
                "venue": show["venue"],
                "time": show["time"],
                "link": show["link"],
            }
    log(f"  {len(matches)} matches, {len(discover)} discovery picks")

    return {
        "name": user["name"],
        "email": user["email"],
        "slug": user_slug(user),
        "matches": sorted(matches, key=lambda m: m["date"]),
        "discover": sorted(
            discover.values(), key=lambda e: (-e["score"], e["date"])
        ),
    }


# ------------------------------------------------------------------ Email ---

def esc(s):
    return html.escape(s or "")


def fmt_iso(iso):
    d = date.fromisoformat(iso)
    return d.strftime("%a %b ") + str(d.day)


CARD_TD = (
    "padding:10px 12px;background:#181820;border-radius:10px;"
    "font-family:Helvetica,Arial,sans-serif"
)


def email_card(img, title_html, meta_html, detail_html=""):
    img_html = (
        f"<img src='{esc(img)}' width='56' height='56' alt='' "
        "style='border-radius:8px;display:block;object-fit:cover'>"
        if img
        else "<div style='width:56px;height:56px;border-radius:8px;"
        "background:#2a2a35'></div>"
    )
    return (
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0'"
        " style='margin:0 0 8px 0'><tr>"
        f"<td width='56' valign='top' style='padding:10px 0 10px 12px;"
        f"background:#181820;border-radius:10px 0 0 10px'>{img_html}</td>"
        f"<td valign='top' style='{CARD_TD};border-radius:0 10px 10px 0'>"
        f"<div style='font-size:15px;line-height:1.35'>{title_html}</div>"
        f"<div style='font-size:12px;color:#9a9aa8;margin-top:2px'>{meta_html}</div>"
        + (
            f"<div style='font-size:12px;color:#b8b8c4;margin-top:6px;"
            f"line-height:1.5'>{detail_html}</div>"
            if detail_html
            else ""
        )
        + "</td></tr></table>"
    )


def render_email(data, today, end, page_url=""):
    a_style = "color:#fff;text-decoration:none;font-weight:bold"
    g_style = "color:#1DB954;text-decoration:none"
    parts = [
        "<div style='background:#101016;padding:24px 8px'>"
        "<div style='max-width:600px;margin:auto;font-family:Helvetica,Arial,"
        "sans-serif;color:#eee'>",
        "<h1 style='font-size:22px;margin:0 0 4px 0;color:#fff'>"
        "🎸 Austin Concert Digest</h1>",
        f"<p style='color:#9a9aa8;font-size:13px;margin:0 0 18px 0'>"
        f"{fmt_iso(today.isoformat())} – {fmt_iso(end.isoformat())}, {end.year}"
        f" · matched to {esc(data['name'])}'s Spotify</p>",
    ]
    if page_url:
        parts.append(
            f"<p style='margin:0 0 20px 0'><a href='{esc(page_url)}' "
            "style='display:inline-block;background:#1DB954;color:#000;"
            "font-weight:bold;font-size:13px;padding:9px 18px;border-radius:20px;"
            "text-decoration:none'>Open interactive page →</a></p>"
        )

    parts.append(
        "<h2 style='font-size:15px;color:#fff;margin:18px 0 10px 0'>"
        "🎤 YOUR ARTISTS IN TOWN</h2>"
    )
    if data["matches"]:
        for m in data["matches"]:
            title = f"<a href='{esc(m['link'])}' style='{a_style}'>{esc(m['artist'])}</a>"
            if m.get("spotify_url"):
                title += (
                    f" &nbsp;<a href='{esc(m['spotify_url'])}' "
                    f"style='{g_style};font-size:11px'>Spotify</a>"
                )
            meta = " · ".join(
                x for x in [fmt_iso(m["date"]), esc(m["venue"]), esc(m["time"])] if x
            )
            parts.append(
                email_card(m.get("image", ""), title, meta,
                           f"<span style='color:#77778a'>{esc(m['title'])}</span>")
            )
    else:
        parts.append(
            "<p style='color:#9a9aa8;font-size:13px'>None this window.</p>"
        )

    parts.append(
        "<h2 style='font-size:15px;color:#fff;margin:22px 0 10px 0'>"
        "🔍 DISCOVER</h2>"
    )
    by_family = {}
    for e in data["discover"]:
        by_family.setdefault(e["family"], []).append(e)
    for fam in GENRE_FAMILIES:
        entries = by_family.get(fam, [])
        if not entries:
            continue
        parts.append(
            f"<h3 style='font-size:13px;color:#1DB954;margin:16px 0 8px 0;"
            f"letter-spacing:.5px'>{esc(fam).upper()}</h3>"
        )
        for e in entries:
            title = f"<a href='{esc(e['link'])}' style='{a_style}'>{esc(e['name'])}</a>"
            if e.get("spotify_url"):
                title += (
                    f" &nbsp;<a href='{esc(e['spotify_url'])}' "
                    f"style='{g_style};font-size:11px'>Spotify</a>"
                )
            meta = " · ".join(
                x
                for x in [
                    esc(", ".join(e["genres"])),
                    fmt_iso(e["date"]),
                    esc(e["venue"]),
                    esc(e["time"]),
                ]
                if x
            )
            detail = []
            if e["similar"]:
                detail.append(
                    "similar to " + ", ".join(esc(s) for s in e["similar"])
                )
            if e["songs"]:
                links = ", ".join(
                    f"<a href='{esc(s['url'])}' style='{g_style}'>"
                    f"<i>{esc(s['name'])}</i></a>"
                    if s.get("url")
                    else f"<i>{esc(s['name'])}</i>"
                    for s in e["songs"]
                )
                detail.append("try: " + links)
            parts.append(
                email_card(e.get("image", ""), title, meta, "<br>".join(detail))
            )

    parts.append(
        "<p style='color:#55556a;font-size:11px;margin-top:24px'>"
        f"Generated from <a href='{SHOWLIST_URL}' style='color:#77778a'>"
        "Showlist Austin</a> + your Spotify listening.</p></div></div>"
    )
    subject = (
        f"🎸 Austin shows {fmt_iso(today.isoformat())}–{fmt_iso(end.isoformat())}: "
        f"{len(data['matches'])} of your artists, "
        f"{len(data['discover'])} to discover"
    )
    return subject, "".join(parts)


def send_email(to, subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ["GMAIL_ADDRESS"]
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)


# ------------------------------------------------------------------- Main ---

def main():
    users = load_users()
    today = date.today()
    end = today + timedelta(days=LOOKAHEAD_DAYS - 1)
    base_url = pages_base_url()
    log(f"Window {today}..{end} | users: {len(users)} | pages: {base_url or '-'}")

    log("Scraping Showlist Austin...")
    shows = [s for s in fetch_shows() if today <= s["date"] <= end]
    log(f"  {len(shows)} shows in window")

    all_data, failures = [], []
    for user in users:
        log(f"=== {user['name']} <{user['email']}> ===")
        try:
            data = compute_user_data(user, shows)
            page_url = f"{base_url}/u/{data['slug']}/" if base_url else ""
            subject, body = render_email(data, today, end, page_url)
            send_email(user["email"], subject, body)
            log("  email sent.")
            all_data.append(data)
        except Exception as e:
            log(f"  ERROR for {user['name']}: {e}")
            failures.append((user, e))
            try:
                send_email(
                    user["email"],
                    "⚠️ Your Austin Concert Digest failed this week",
                    f"<p>This week's digest hit an error:</p>"
                    f"<pre>{esc(str(e))}</pre>"
                    "<p>Most common cause: your Spotify authorization expired "
                    "— re-run the connect step and send the new token to "
                    "whoever runs the digest.</p>",
                )
            except Exception:
                pass

    if os.environ.get("BUILD_SITE", "1") == "1" and all_data:
        from site_builder import build_site
        build_site(all_data, "site", today, end)
        log(f"Site built for {len(all_data)} user(s) in ./site")

    if failures:
        raise RuntimeError(
            f"{len(failures)} user(s) failed: "
            + ", ".join(u["name"] for u, _ in failures)
        )
    log("All done.")


if __name__ == "__main__":
    main()
