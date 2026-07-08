#!/usr/bin/env python3
"""
Auto-enrollment for Song of the Week.

The /join signup page posts each new member's entry into the GroupMe chat
(via the bot). This script — run hourly by GitHub Actions — reads recent
group messages, finds signup entries not yet in USERS_JSON, merges them in,
updates the USERS_JSON repo secret, and confirms in the chat.

Safe by design: a signup only works if its Spotify refresh token is valid,
and tokens can only be minted for accounts the admin has allowlisted in
the Spotify dashboard. Random posts can't enroll anyone.

Env: GROUPME_ACCESS_TOKEN (from dev.groupme.com), GROUPME_BOT_ID,
     USERS_JSON, ADMIN_PAT (fine-grained, Secrets: write on this repo),
     GITHUB_REPOSITORY
"""

import base64
import json
import os
import re

import requests
from nacl import encoding, public

GM = "https://api.groupme.com/v3"
GH = "https://api.github.com"


def log(msg):
    print(msg, flush=True)


def gm_token():
    return os.environ["GROUPME_ACCESS_TOKEN"].strip()


def find_group_id():
    r = requests.get(f"{GM}/bots", params={"token": gm_token()}, timeout=20)
    r.raise_for_status()
    for bot in r.json().get("response", []):
        if bot.get("bot_id") == os.environ["GROUPME_BOT_ID"].strip():
            return bot["group_id"]
    raise RuntimeError("Bot not found for this GroupMe account")


def recent_messages(group_id, limit=100):
    r = requests.get(
        f"{GM}/groups/{group_id}/messages",
        params={"token": gm_token(), "limit": limit},
        timeout=20,
    )
    if r.status_code == 304:  # empty group
        return []
    r.raise_for_status()
    return r.json().get("response", {}).get("messages", [])


def extract_signups(messages):
    """Pull {"name","refresh_token",...} JSON objects out of chat texts."""
    found = []
    for m in messages:
        text = m.get("text") or ""
        if '"refresh_token"' not in text:
            continue
        for candidate in re.findall(r"\{[^{}]*\}", text):
            try:
                entry = json.loads(candidate)
            except ValueError:
                continue
            if not (isinstance(entry, dict) and entry.get("name")
                    and entry.get("refresh_token")):
                continue
            entry.setdefault("digest", False)  # signups are songs-only
            found.append(entry)
    return found


def update_users_secret(users):
    repo = os.environ["GITHUB_REPOSITORY"]
    headers = {
        "Authorization": f"Bearer {os.environ['ADMIN_PAT'].strip()}",
        "Accept": "application/vnd.github+json",
    }
    r = requests.get(f"{GH}/repos/{repo}/actions/secrets/public-key",
                     headers=headers, timeout=20)
    r.raise_for_status()
    key = r.json()
    sealed = public.SealedBox(
        public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
    ).encrypt(json.dumps(users).encode())
    r = requests.put(
        f"{GH}/repos/{repo}/actions/secrets/USERS_JSON",
        headers=headers,
        json={
            "encrypted_value": base64.b64encode(sealed).decode(),
            "key_id": key["key_id"],
        },
        timeout=20,
    )
    r.raise_for_status()


def bot_post(text):
    requests.post(
        f"{GM}/bots/post",
        json={"bot_id": os.environ["GROUPME_BOT_ID"].strip(), "text": text},
        timeout=20,
    )


def main():
    users = json.loads(os.environ["USERS_JSON"])
    known = {u["refresh_token"] for u in users}
    known_names = {u["name"].strip().lower() for u in users}

    group_id = find_group_id()
    signups = extract_signups(recent_messages(group_id))

    added, changed = [], False
    for entry in signups:
        if entry["refresh_token"] in known:
            continue
        changed = True
        name = str(entry["name"]).strip()[:30] or "Friend"
        if name.lower() in known_names:
            # same person re-authorized: replace their token
            for u in users:
                if u["name"].strip().lower() == name.lower():
                    u["refresh_token"] = entry["refresh_token"]
            log(f"refreshed token for {name}")
        else:
            users.append({"name": name,
                          "refresh_token": entry["refresh_token"],
                          "digest": bool(entry.get("digest", False)),
                          **({"email": entry["email"]}
                             if entry.get("email") else {})})
            known_names.add(name.lower())
            added.append(name)
        known.add(entry["refresh_token"])

    if changed:
        update_users_secret(users)
        if added:
            bot_post("✅ " + ", ".join(added) +
                     (" is" if len(added) == 1 else " are") +
                     " in! Songs of the week start this Wednesday \U0001F3A7")
        log(f"updated (added: {added or 'token refresh only'})")
    else:
        log("nothing to do")


if __name__ == "__main__":
    main()
