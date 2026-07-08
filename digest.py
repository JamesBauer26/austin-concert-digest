#!/usr/bin/env python3
"""
Austin Concert Digest — multi-user, email + web page
Scrapes austin.showlists.net once, then for EACH user: matches shows against
their Spotify listening, emails a card-style personalized digest, and builds
an interactive web page (published via GitHub Pages by the workflow).

Data sources (Spotify removed genre data for new API apps, so):
  Spotify     — the user's listening profile + direct artist matching
  MusicBrainz — genre tags for taste-matching (free, keyless)
  Deezer      — artist photos + top songs with 30s previews (free, keyless)
  Last.fm     — OPTIONAL, better tags + true similar-artists if
                LASTFM_API_KEY secret is set

Config via environment variables:
  SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET   (one shared Spotify app)
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD          (sender account)
  USERS_JSON  — JSON list: [{"name","email","refresh_token"[,"slug"]}, ...]
Optional:
  LASTFM_API_KEY, LOOKAHEAD_DAYS (14), MIN_SCORE (0.01), BUILD_SITE ("1")
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
MB_API = "https://musicbrainz.org/ws/2"
DEEZER_API = "https://api.deezer.com"
LASTFM_API = "https://ws.audioscrobbler.com/2.0/"
LASTFM_KEY = os.environ.get("LASTFM_API_KEY", "").strip()
UA = {"User-Agent": "AustinConcertDigest/2.0 (personal concert digest)"}

LOOKAHEAD_DAYS = int(os.environ.get("LOOKAHEAD_DAYS", "14"))
MIN_SCORE = float(os.environ.get("MIN_SCORE", "0.01"))

# Caches shared across users so each artist is only looked up once per run.
TAG_CACHE = {}      # norm name -> [tags]
SIMILAR_CACHE = {}  # norm name -> [similar artist names] (last.fm only)
DEEZER_CACHE = {}   # norm name -> {"image","url","songs"}
SP_CACHE = {}       # norm name -> spotify info dict or None
TRACKS_CACHE = {}   # spotify artist id -> [{"name","url","preview"}]
PRICE_CACHE = {}    # url -> price string ("$15", "$10\u2013$20", "Free", "")

SKIP_KEYWORDS = [
    "world cup", "bingo", "trivia", "karaoke", "open mic", "comedy",
    "storytime", "gameshow", "poet", "reading", "taco tuesday", "market",
    "watch party", "drag brunch",
]
TRIBUTE_KEYWORDS = ["tribute", "covers", "plays the music of", "songs of"]

JUNK_TAGS = {
    "seen live", "favorites", "favourites", "favourite", "spotify", "all",
    "usa", "american", "america", "british", "uk", "english", "canadian",
    "australian", "german", "french", "swedish", "norwegian", "japanese",
    "austin", "texas", "male vocalists", "female vocalists", "male vocalist",
    "female vocalist", "under 2000 listeners", "60s", "70s", "80s", "90s",
    "00s", "10s", "20s", "2020s", "oldies", "check out",
}

GENRE_FAMILIES = {
    "Hip-Hop / R&B": [
        "hip hop", "hip-hop", "hip hop rnb and dance hall", "rap", "trap",
        "drill", "r&b", "rnb", "rhythm and blues", "neo soul", "neo-soul",
        "grime", "boom bap",
    ],
    "Indie / Synth / Electronic": [
        "indie", "synth", "dream pop", "shoegaze", "electronic", "electronica",
        "house", "techno", "edm", "dance", "indietronica", "chillwave",
        "new wave", "psych", "garage rock", "lo-fi", "bedroom", "art pop",
        "alternative", "post-punk", "post punk", "new rave", "downtempo",
        "ambient", "idm", "trance", "drum and bass", "dubstep", "surf",
    ],
    "Punk / Metal / Hardcore": [
        "punk", "metal", "hardcore", "emo", "screamo", "metalcore",
        "post-hardcore", "post hardcore", "grunge", "thrash", "death metal",
        "doom", "sludge", "powerviolence", "ska", "grindcore", "noise rock",
    ],
    "Classic / Rock / Singer-Songwriter": [
        "classic rock", "soft rock", "rock", "singer-songwriter",
        "singer/songwriter", "piano", "folk", "americana", "country",
        "blues", "soul", "funk", "pop rock", "rock and roll", "jam band",
        "psychedelic rock", "roots",
    ],
    "Pop": [
        "pop", "dance pop", "electropop", "k-pop", "latin", "reggaeton",
    ],
}
WILDCARD = "Wildcards"
FAMILY_ORDER = list(GENRE_FAMILIES) + [WILDCARD]

_last_mb = [0.0]


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
    """USERS_JSON entries support optional feature flags:
      "digest": true/false (default true)  — Monday concert digest email/page
      "songs":  true/false (default true)  — Wednesday top-songs GroupMe drop
    "email" is only required for digest members."""
    raw = os.environ.get("USERS_JSON", "").strip()
    if raw:
        users = json.loads(raw)
        for u in users:
            for field in ("name", "refresh_token"):
                if not u.get(field):
                    raise ValueError(f"USERS_JSON entry missing '{field}'")
            if u.get("digest", True) and not u.get("email"):
                raise ValueError(
                    f"USERS_JSON entry for {u['name']} needs 'email' "
                    "(or set \"digest\": false)")
        return users
    return [
        {
            "name": "you",
            "email": os.environ["DIGEST_TO"],
            "refresh_token": os.environ["SPOTIFY_REFRESH_TOKEN"],
        }
    ]


# --------------------------------------------------------------- Tag data ---

def clean_tags(raw_tags):
    out = []
    for t in raw_tags:
        tn = t.strip().lower()
        if tn and tn not in JUNK_TAGS and len(tn) < 40 and tn not in out:
            out.append(tn)
    return out[:8]


def lastfm_get(method, **params):
    try:
        r = requests.get(
            LASTFM_API,
            params={"method": method, "api_key": LASTFM_KEY, "format": "json",
                    "autocorrect": 1, **params},
            headers=UA, timeout=20,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def mb_tags(name):
    """MusicBrainz artist search -> tags/genres. Keyless; 1 req/sec."""
    wait = 1.1 - (time.time() - _last_mb[0])
    if wait > 0:
        time.sleep(wait)
    _last_mb[0] = time.time()
    try:
        r = requests.get(
            f"{MB_API}/artist",
            params={"query": f'artist:"{name}"', "fmt": "json", "limit": 3},
            headers=UA, timeout=20,
        )
        if r.status_code != 200:
            return []
        best, best_sim = None, 0.0
        for a in r.json().get("artists", []):
            s = similar(a.get("name", ""), name)
            if s > best_sim:
                best, best_sim = a, s
        if not best or best_sim < 0.87:
            return []
        tags = [t.get("name", "") for t in
                sorted(best.get("tags", []),
                       key=lambda t: -int(t.get("count", 0) or 0))]
        return clean_tags(tags)
    except Exception:
        return []


def artist_tags(name, mb_fallback=None):
    """Genre tags: Last.fm when key present, MusicBrainz otherwise.
    MB is slow (1 req/s), so with a Last.fm key it is only used as a
    fallback where mb_fallback=True (the user's own profile artists)."""
    key = norm(name)
    if key in TAG_CACHE:
        return TAG_CACHE[key]
    if mb_fallback is None:
        mb_fallback = not LASTFM_KEY
    tags = []
    if LASTFM_KEY:
        data = lastfm_get("artist.gettoptags", artist=name)
        raw = [
            t.get("name", "")
            for t in data.get("toptags", {}).get("tag", [])
            if int(t.get("count", 0) or 0) >= 10
        ]
        tags = clean_tags(raw)
        time.sleep(0.08)
    if not tags and mb_fallback:
        tags = mb_tags(name)
    TAG_CACHE[key] = tags
    return tags


def similar_artists(name):
    """True similar-artist list (Last.fm only; empty without a key)."""
    if not LASTFM_KEY:
        return []
    key = norm(name)
    if key in SIMILAR_CACHE:
        return SIMILAR_CACHE[key]
    data = lastfm_get("artist.getsimilar", artist=name, limit=80)
    sims = [a.get("name", "")
            for a in data.get("similarartists", {}).get("artist", [])]
    SIMILAR_CACHE[key] = sims
    time.sleep(0.08)
    return sims


def deezer_info(name):
    """Artist photo + top songs with 30s previews. Keyless."""
    key = norm(name)
    if key in DEEZER_CACHE:
        return DEEZER_CACHE[key]
    info = {"image": "", "url": "", "songs": []}
    try:
        r = requests.get(f"{DEEZER_API}/search/artist",
                         params={"q": name, "limit": 3}, headers=UA, timeout=20)
        best, best_sim = None, 0.0
        for a in r.json().get("data", []):
            s = similar(a.get("name", ""), name)
            if s > best_sim:
                best, best_sim = a, s
        if best and best_sim >= 0.85:
            info["image"] = best.get("picture_medium") or best.get("picture") or ""
            info["url"] = best.get("link", "")
            rt = requests.get(f"{DEEZER_API}/artist/{best['id']}/top",
                              params={"limit": 3}, headers=UA, timeout=20)
            for t in rt.json().get("data", []):
                info["songs"].append({
                    "name": t.get("title", ""),
                    "url": t.get("link", ""),
                    "preview": t.get("preview", "") or "",
                })
    except Exception:
        pass
    DEEZER_CACHE[key] = info
    time.sleep(0.05)
    return info


# ------------------------------------------------------------------ Price ---

def _fmt_price(lo, hi=None):
    def f(v):
        v = float(v)
        return f"${v:g}"
    if hi is not None and float(hi) > float(lo):
        return f"{f(lo)}\u2013{f(hi)}"
    return f(lo)


def show_price(url):
    """Best-effort ticket price from a show's ticket page.
    Reads schema.org JSON-LD offers first, then falls back to $ patterns.
    Returns '$15', '$10\u2013$20', 'Free', or ''."""
    if not url or url == SHOWLIST_URL:
        return ""
    if url in PRICE_CACHE:
        return PRICE_CACHE[url]
    price = ""
    try:
        r = requests.get(url, headers=UA, timeout=15, allow_redirects=True)
        if r.status_code == 200 and "text/html" in r.headers.get(
                "Content-Type", "text/html"):
            html_text = r.text[:600000]
            # 1) JSON-LD offers
            for m in re.finditer(
                r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                html_text, re.S | re.I,
            ):
                try:
                    data = json.loads(m.group(1).strip())
                except Exception:
                    continue
                stack = data if isinstance(data, list) else [data]
                while stack and not price:
                    node = stack.pop()
                    if not isinstance(node, dict):
                        continue
                    offers = node.get("offers")
                    if offers:
                        offers = offers if isinstance(offers, list) else [offers]
                        los, his = [], []
                        for o in offers:
                            if not isinstance(o, dict):
                                continue
                            for k in ("price", "lowPrice"):
                                v = o.get(k)
                                if v not in (None, ""):
                                    try:
                                        los.append(float(v))
                                    except (TypeError, ValueError):
                                        pass
                            v = o.get("highPrice")
                            if v not in (None, ""):
                                try:
                                    his.append(float(v))
                                except (TypeError, ValueError):
                                    pass
                        if los:
                            lo = min(los)
                            hi = max(his) if his else max(los)
                            if lo == 0 and hi == 0:
                                price = "Free"
                            elif 0 < lo <= 500:
                                price = _fmt_price(lo, hi if hi != lo else None)
                    for v in node.values():
                        if isinstance(v, (dict, list)):
                            stack.append(v)
                if price:
                    break
            # 2) regex fallback near ticket-ish words
            if not price:
                text = re.sub(r"<[^>]+>", " ", html_text)
                if re.search(r"(?i)\b(free show|free entry|no cover|"
                             r"free admission)\b", text):
                    price = "Free"
                else:
                    amounts = [
                        float(a) for a in re.findall(
                            r"\$\s?(\d{1,3}(?:\.\d{2})?)", text)
                        if 3 <= float(a) <= 500
                    ]
                    if amounts:
                        lo, hi = min(amounts), max(amounts)
                        price = _fmt_price(lo, hi if hi > lo else None)
    except Exception:
        pass
    PRICE_CACHE[url] = price
    time.sleep(0.05)
    return price


# ---------------------------------------------------------------- Spotify ---

def spotify_token(refresh_token):
    """Refresh an access token. Handles both token flavors: ones issued
    with the client secret, and ones issued via the browser signup page
    (PKCE — refreshed with client_id in the body, no Basic auth)."""
    url = "https://accounts.spotify.com/api/token"
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    r = requests.post(
        url, data=data,
        auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
        timeout=30,
    )
    if r.status_code == 400:
        r = requests.post(
            url,
            data={**data, "client_id": os.environ["SPOTIFY_CLIENT_ID"]},
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
        """{norm_name: {"name","rank"}} from top/followed/saved-track artists.
        Lower rank = stronger signal. (No genres — Spotify no longer provides
        them to new apps; tags come from MusicBrainz/Last.fm instead.)"""
        artists = {}

        def add(name, rank):
            key = norm(name)
            if key and (key not in artists or rank < artists[key]["rank"]):
                artists[key] = {"name": name, "rank": rank}

        for i, rng in enumerate(["short_term", "medium_term", "long_term"]):
            try:
                data = self.get("/me/top/artists", {"time_range": rng, "limit": 50})
                for pos, a in enumerate(data.get("items", [])):
                    add(a["name"], i * 100 + pos)
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
                    add(a["name"], 500)
                after = data.get("cursors", {}).get("after")
                if not after:
                    break
        except Exception as e:
            log(f"  warn: followed artists failed: {e}")

        try:
            for offset in range(0, 200, 50):
                data = self.get("/me/tracks", {"limit": 50, "offset": offset})
                for item in data.get("items", []):
                    for a in item["track"]["artists"]:
                        add(a["name"], 700)
                if not data.get("next"):
                    break
        except Exception as e:
            log(f"  warn: saved tracks failed: {e}")

        noise = ("white noise", "sleep", "rain sounds", "brown noise", "asmr")
        return {
            k: v for k, v in artists.items()
            if not any(n in k for n in noise)
        }

    def lookup_artist(self, name):
        """Spotify search (for link/image/top-tracks id). Cached."""
        key = norm(name)
        if key in SP_CACHE:
            return SP_CACHE[key]
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
                    "url": best.get("external_urls", {}).get("spotify", ""),
                    "image": (images[-1]["url"] if images else ""),
                }
        except Exception:
            pass
        SP_CACHE[key] = result
        time.sleep(0.05)
        return result

    def top_tracks(self, artist_id, n=3):
        if artist_id not in TRACKS_CACHE:
            tracks = []
            try:
                data = self.get(f"/artists/{artist_id}/top-tracks",
                                {"market": "US"})
                for t in data.get("tracks", []):
                    tracks.append({
                        "name": t["name"],
                        "url": t.get("external_urls", {}).get("spotify", ""),
                        "preview": t.get("preview_url") or "",
                    })
            except Exception:
                pass
            TRACKS_CACHE[artist_id] = tracks
        return TRACKS_CACHE[artist_id][:n]


# --------------------------------------------------------------- Showlist ---

def fetch_shows(html_text=None):
    if html_text is None:
        r = requests.get(SHOWLIST_URL, headers=UA, timeout=60)
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

def genre_families(tags):
    fams = set()
    for g in tags:
        gl = g.lower()
        for fam, keywords in GENRE_FAMILIES.items():
            if any(k in gl for k in keywords):
                fams.add(fam)
    return fams


def build_profile(mine, top_n=60):
    """Tag + family weight profiles from the user's top artists."""
    tag_w, fam_w, artist_fams = {}, {}, {}
    ranked = sorted(mine.values(), key=lambda a: a["rank"])[:top_n]
    for a in ranked:
        w = 3.0 if a["rank"] < 300 else (2.0 if a["rank"] < 600 else 1.0)
        tags = artist_tags(a["name"], mb_fallback=True)
        fams = genre_families(tags)
        artist_fams[a["name"]] = fams
        for t in tags:
            tag_w[t] = tag_w.get(t, 0.0) + w
        for f in fams:
            fam_w[f] = fam_w.get(f, 0.0) + w
    ts = sum(tag_w.values()) or 1.0
    fs = sum(fam_w.values()) or 1.0
    return (
        {t: w / ts for t, w in tag_w.items()},
        {f: w / fs for f, w in fam_w.items()},
        artist_fams,
    )


def known_similar(cand_name, cand_fams, mine, artist_fams):
    """Artists the user listens to that relate to the candidate.
    Prefers Last.fm true-similarity, falls back to shared genre family."""
    user_by_norm = {k: v["name"] for k, v in mine.items()}
    sims = similar_artists(cand_name)
    hits = [user_by_norm[norm(s)] for s in sims if norm(s) in user_by_norm]
    if hits:
        return hits[:3]
    ranked = sorted(
        (a for a in mine.values() if artist_fams.get(a["name"]) and
         cand_fams & artist_fams[a["name"]]),
        key=lambda a: a["rank"],
    )
    return [a["name"] for a in ranked[:3]]


# ---------------------------------------------------------------- Compute ---

def compute_user_data(user, shows, sp=None):
    sp = sp or Spotify(user["refresh_token"])
    mine = sp.my_artists()
    log(f"  {len(mine)} artists in profile")
    tag_profile, fam_profile, artist_fams = build_profile(mine)
    top_tags = sorted(tag_profile, key=tag_profile.get, reverse=True)[:10]
    log(f"  top tags: {top_tags}")
    log(f"  families: { {f: round(w, 2) for f, w in fam_profile.items()} }")

    matches = []
    for show in shows:
        if is_skippable(show["title"]):
            continue
        for cand in show["artists"]:
            for a in mine.values():
                if similar(cand, a["name"]) >= 0.92:
                    info = sp.lookup_artist(a["name"]) or {}
                    dz = deezer_info(a["name"])
                    matches.append(
                        {
                            "artist": a["name"],
                            "title": show["title"],
                            "date": show["date"].isoformat(),
                            "venue": show["venue"],
                            "time": show["time"],
                            "link": show["link"],
                            "price": show_price(show["link"]),
                            "image": info.get("image") or dz["image"],
                            "spotify_url": info.get("url", ""),
                        }
                    )
                    break

    matched_titles = {m["title"] for m in matches}

    discover, checked = {}, 0
    for show in shows:
        if show["title"] in matched_titles or is_skippable(show["title"]):
            continue
        if is_tribute(show["title"]):
            continue
        for cand in show["artists"]:
            key = norm(cand)
            if not key or key in discover or key in mine:
                continue
            checked += 1
            tags = artist_tags(cand)
            if not tags:
                continue  # unidentifiable — skip rather than guess
            fams = genre_families(tags)
            score = 2.0 * sum(tag_profile.get(t, 0.0) for t in tags) + sum(
                fam_profile.get(f, 0.0) for f in fams
            )
            if score < MIN_SCORE:
                continue
            family = (
                max(fams, key=lambda f: fam_profile.get(f, 0.0))
                if fams else WILDCARD
            )
            sp_info = sp.lookup_artist(cand)
            dz = deezer_info(cand)
            songs = sp.top_tracks(sp_info["id"]) if sp_info else []
            if songs:
                for s in songs:  # borrow Deezer previews when Spotify has none
                    if not s["preview"]:
                        for d in dz["songs"]:
                            if similar(s["name"], d["name"]) > 0.75:
                                s["preview"] = d["preview"]
                                break
            else:
                songs = dz["songs"]
            discover[key] = {
                "name": (sp_info or {}).get("name") or cand,
                "genres": tags[:3],
                "family": family,
                "score": round(score, 4),
                "similar": known_similar(cand, fams, mine, artist_fams),
                "songs": songs[:3],
                "spotify_url": (sp_info or {}).get("url", ""),
                "image": (sp_info or {}).get("image") or dz["image"],
                "date": show["date"].isoformat(),
                "venue": show["venue"],
                "time": show["time"],
                "link": show["link"],
                "price": show_price(show["link"]),
            }
    log(f"  {len(matches)} matches; {checked} candidates checked, "
        f"{len(discover)} discovery picks")

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
                x for x in [fmt_iso(m["date"]), esc(m["venue"]),
                            esc(m["time"]), esc(m.get("price", ""))] if x
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
    for fam in FAMILY_ORDER:
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
                    esc(e.get("price", "")),
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
    users = [u for u in load_users() if u.get("digest", True)]
    today = date.today()
    end = today + timedelta(days=LOOKAHEAD_DAYS - 1)
    base_url = pages_base_url()
    log(f"Window {today}..{end} | users: {len(users)} | "
        f"tags via {'Last.fm' if LASTFM_KEY else 'MusicBrainz'} | "
        f"pages: {base_url or '-'}")

    log("Scraping Showlist Austin...")
    shows = [s for s in fetch_shows() if today <= s["date"] <= end]
    log(f"  {len(shows)} shows in window")

    all_data, failures = [], []
    for user in users:
        log(f"=== {user['name']} ===")
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
                    "<p>Most common cause: your Spotify authorization expired.</p>",
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
