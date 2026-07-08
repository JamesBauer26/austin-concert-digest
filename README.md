# 🎸 Austin Concert Digest

Every Monday at 8am, this emails each subscribed person a **personalized**
digest of the next 2 weeks of Austin shows from
[Showlist Austin](https://austin.showlists.net/) — the source that lists ALL
the shows, not just the big ones — matched against their own Spotify listening:

1. **Your artists in town** — direct matches with date, venue, time, ticket link
2. **Discover** — every show relevant to your taste, grouped by genre, each
   with artist photo, genre tags, similar artists *you* listen to, and top
   songs to try

Each email links to your **personal interactive web page** (GitHub Pages):
cards with artist photos, filters by genre/date/venue, search, and 30-second
song previews where Spotify provides them.

Runs free on GitHub Actions. No server, no subscriptions. Supports multiple
people — each connects their own Spotify once.

## One-time setup for the admin (~15 minutes)

### 1. Spotify API credentials (~5 min)

1. Go to https://developer.spotify.com/dashboard → log in → **Create app**
   - Name/description: anything (e.g. "Concert Digest")
   - **Redirect URI**: `http://127.0.0.1:8888/callback` (must be exact)
   - Check "Web API", save
2. On the app page, copy the **Client ID** and **Client Secret**
3. Get your own refresh token (needs Python 3):
   ```
   pip install requests
   python get_refresh_token.py
   ```
   Paste your ID + Secret, approve in the browser, copy the **refresh token**.

### 2. Gmail app password (~3 min)

Digests are sent from your Gmail via SMTP.

1. Go to https://myaccount.google.com/apppasswords (needs 2-step verification on)
2. Create one named "concert digest" and copy the 16-character password

### 3. GitHub repo + secrets (~5 min)

1. Create a **private** repo at https://github.com/new (e.g. `austin-concert-digest`)
2. Upload everything in this folder, keeping the `.github/workflows/` path.
   The `.github` folder may be hidden in your file browser — if drag-drop
   misses it, create it manually: repo → Add file → Create new file → name it
   `.github/workflows/digest.yml` → paste the contents.
3. Repo → **Settings → Secrets and variables → Actions → New repository secret**,
   add all five:

   | Secret | Value |
   |---|---|
   | `SPOTIFY_CLIENT_ID` | from step 1 |
   | `SPOTIFY_CLIENT_SECRET` | from step 1 |
   | `GMAIL_ADDRESS` | the Gmail you send from |
   | `GMAIL_APP_PASSWORD` | from step 2 |
   | `USERS_JSON` | see below |

   `USERS_JSON` is a JSON list — one entry per person:
   ```json
   [
     {"name": "James", "email": "jabauer2426@gmail.com", "refresh_token": "AQD...yours"}
   ]
   ```

### 4. Enable the web page (~1 min)

Repo → **Settings → Pages** → under "Build and deployment" set **Source:
GitHub Actions**. That's it — the weekly run publishes the site.

Privacy: each person's page lives at an unguessable URL
(`https://<you>.github.io/<repo>/u/<16-char-token>/`) that is only sent in
their email. The landing page lists nobody. Pages are marked `noindex` so
search engines skip them. Technically anyone WITH the exact URL can view it —
don't forward the email if that matters.

### 5. Test it

Repo → **Actions** tab → "Weekly Austin Concert Digest" → **Run workflow**.
Digests land in ~2-5 minutes; the web page link is at the top of the email.
After that it runs every Monday automatically.

## 👥 Inviting someone (~5 min each)

Spotify apps in development mode allow up to 25 users, and each must be
allowlisted first:

1. **Allowlist them**: Spotify dashboard → your app → **User Management** →
   add their name + the email on their Spotify account
2. **They authorize**: send them `get_refresh_token.py` plus your Client ID
   and Secret (privately!). They run:
   ```
   pip install requests
   python get_refresh_token.py
   ```
   approve in the browser, and send you back the refresh token it prints.
3. **Add them**: edit the `USERS_JSON` secret, append
   `{"name": "Sam", "email": "sam@x.com", "refresh_token": "AQC..."}`

They're in the next Monday run. To remove someone, delete their entry.

Notes:
- Each person's digest is matched to *their* listening; artist lookups are
  cached so extra users barely add runtime
- If one person's token breaks, everyone else still gets their digest; the
  broken user gets an error email
- Beyond 25 people you'd need Spotify extended-quota approval (a review
  process) — at that point this becomes a real hosted app; different project

## 🎧 Weekly Top Songs group chat (GroupMe)

Every Wednesday evening (~7pm Austin) a bot posts each member's
most-played song of the past week — with play count and Spotify link —
into a GroupMe group. Friends can be in the group via plain SMS without
installing the app.

One-time setup:

1. Create a free GroupMe account (groupme.com or the app), make a group,
   and add your friends by phone number.
2. Go to https://dev.groupme.com/bots → Create Bot → pick your group,
   name it (e.g. "Song of the Week"), create, and copy the **Bot ID**.
3. Add it as a repo secret: `GROUPME_BOT_ID`.
4. Test: Actions → "Weekly Top Songs" → Run workflow.

Accuracy note: play-history access requires the `user-read-recently-played`
scope. Tokens authorized before this feature fall back to the short-term
top track; re-run the authorize + exchange flow to upgrade.

## Tuning

- `LOOKAHEAD_DAYS` (env var or default in `digest.py`): window size, default 14
- `MIN_SCORE`: raise to make discovery pickier, lower for a wider net
- `GENRE_FAMILIES` in `digest.py`: the genre buckets and keywords
- `SKIP_KEYWORDS`: things never recommended (bingo, trivia, watch parties...)
- Schedule: the cron line in `.github/workflows/digest.yml`

## How it works

- Scrapes every show on austin.showlists.net once per run
- For each user: pulls top artists (short/medium/long term), followed
  artists, and liked-song artists from the Spotify API — recommendations
  track your listening as it changes
- Direct matches by fuzzy artist-name comparison
- Discovery: unknown artists are looked up on Spotify; genres scored against
  your genre profile; "similar artists" = your artists with overlapping
  genres; top songs from Spotify's top-tracks endpoint
- Failures email the affected user instead of dying silently
- `digest.py` computes the data; `site_builder.py` renders the web pages;
  the email renderer is in `digest.py` — all share one engine

## Notes

- GitHub Actions cron is UTC and can drift 5–15 min; 8am is approximate
- Refresh tokens rarely expire; if one does, rerun `get_refresh_token.py`
- A run costs ~3-5 min of the 2,000 free private-repo Actions minutes/month
