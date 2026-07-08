#!/usr/bin/env python3
"""Builds the static GitHub Pages site: one interactive page per user at
site/u/<slug>/index.html plus a bare landing page (no user links, so pages
stay findable only via the URL emailed to each person)."""

import json
import os

FAMILIES = [
    "Hip-Hop / R&B",
    "Indie / Synth / Electronic",
    "Punk / Metal / Hardcore",
    "Classic / Rock / Singer-Songwriter",
    "Pop",
    "Wildcards",
]

LANDING = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Austin Concert Digest</title>
<style>body{background:#101016;color:#9a9aa8;font-family:Helvetica,Arial,
sans-serif;display:flex;align-items:center;justify-content:center;
height:100vh;margin:0}div{text-align:center}</style></head>
<body><div><h1 style="color:#fff">🎸 Austin Concert Digest</h1>
<p>Personal pages are linked from each weekly email.</p></div></body></html>
"""

TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>__TITLE__</title>
<style>
:root{--bg:#101016;--card:#181820;--muted:#9a9aa8;--green:#1DB954}
*{box-sizing:border-box}
body{background:var(--bg);color:#eee;font-family:Helvetica,Arial,sans-serif;
margin:0;padding:16px}
.wrap{max-width:760px;margin:auto}
h1{font-size:22px;margin:8px 0 2px}
.sub{color:var(--muted);font-size:13px;margin:0 0 16px}
.controls{position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:5;
border-bottom:1px solid #22222c;margin-bottom:14px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.chip{background:var(--card);border:1px solid #2a2a35;color:#ccc;border-radius:16px;
padding:5px 12px;font-size:12px;cursor:pointer;user-select:none}
.chip.on{background:var(--green);color:#000;border-color:var(--green);font-weight:bold}
.row2{display:flex;gap:8px;flex-wrap:wrap}
select,input[type=search]{background:var(--card);color:#eee;border:1px solid #2a2a35;
border-radius:8px;padding:7px 10px;font-size:13px}
input[type=search]{flex:1;min-width:140px}
h2{font-size:14px;color:var(--green);letter-spacing:.5px;margin:22px 0 10px}
.card{display:flex;gap:12px;background:var(--card);border-radius:12px;
padding:12px;margin-bottom:8px}
.card img,.ph{width:64px;height:64px;border-radius:8px;object-fit:cover;
background:#2a2a35;flex-shrink:0}
.t a{color:#fff;text-decoration:none;font-weight:bold;font-size:15px}
.meta{color:var(--muted);font-size:12px;margin-top:2px}
.detail{color:#b8b8c4;font-size:12px;margin-top:6px;line-height:1.6}
.songs button{background:none;border:1px solid var(--green);color:var(--green);
border-radius:12px;font-size:11px;padding:2px 9px;cursor:pointer;margin:2px 4px 0 0}
.songs button.playing{background:var(--green);color:#000}
.songs a{color:var(--green);text-decoration:none}
.sp{color:var(--green);font-size:11px;text-decoration:none;margin-left:6px}
.empty{color:var(--muted);font-size:13px}
.count{color:#55556a;font-size:11px;margin:4px 0 0}
</style></head><body><div class="wrap">
<h1>🎸 Austin Concert Digest</h1>
<p class="sub">__SUBTITLE__</p>
<div class="controls">
  <div class="chips" id="famChips"></div>
  <div class="row2">
    <select id="dateSel">
      <option value="all">All dates</option>
      <option value="7">Next 7 days</option>
      <option value="weekend">This weekend</option>
    </select>
    <select id="venueSel"><option value="all">All venues</option></select>
    <input type="search" id="q" placeholder="Search artists, venues...">
  </div>
  <div class="count" id="count"></div>
</div>
<div id="matches"></div>
<div id="discover"></div>
<p class="count">Source: <a style="color:#77778a"
href="https://austin.showlists.net/">Showlist Austin</a> + your Spotify
listening. Updates every Monday.</p>
</div>
<script>
const DATA = __DATA__;
const FAMILIES = __FAMILIES__;
let famOn = new Set(FAMILIES);
let audio = null, playingBtn = null;

function fmtDay(iso){
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US",{weekday:"short",month:"short",day:"numeric"});
}
function esc(s){const d=document.createElement("div");d.textContent=s||"";return d.innerHTML;}

function card(e, isMatch){
  const img = e.image ? '<img src="'+esc(e.image)+'" alt="" loading="lazy">'
                      : '<div class="ph"></div>';
  let title = '<a href="'+esc(e.link)+'" target="_blank">'
            + esc(isMatch ? e.artist : e.name) + '</a>';
  if (e.spotify_url) title += '<a class="sp" href="'+esc(e.spotify_url)
            +'" target="_blank">Spotify</a>';
  const meta = [ (e.genres||[]).join(", "), fmtDay(e.date), e.venue, e.time, e.price ]
      .filter(Boolean).map(esc).join(" · ");
  let detail = "";
  if (isMatch) {
    detail = '<div class="detail" style="color:#77778a">'+esc(e.title)+'</div>';
  } else {
    const bits = [];
    if ((e.similar||[]).length)
      bits.push("similar to " + e.similar.map(esc).join(", "));
    if ((e.songs||[]).length){
      const artistName = e.name || "";
      const songs = e.songs.map(function(s){
        return '<button data-artist="'+esc(artistName)+'" data-track="'+esc(s.name)
          +'" data-src="'+esc(s.preview||"")+'" data-url="'+esc(s.url||"")
          +'">▶ '+esc(s.name)+'</button>';
      }).join(" ");
      bits.push('<span class="songs">try: '+songs+'</span>');
    }
    if (bits.length) detail = '<div class="detail">'+bits.join("<br>")+'</div>';
  }
  return '<div class="card">'+img+'<div style="min-width:0"><div class="t">'
       + title+'</div><div class="meta">'+meta+'</div>'+detail+'</div></div>';
}

function withinDate(iso, mode){
  if (mode === "all") return true;
  const d = new Date(iso + "T12:00:00"), now = new Date();
  now.setHours(0,0,0,0);
  if (mode === "7") return (d - now) / 864e5 < 7;
  if (mode === "weekend"){
    const day = d.getDay(); // Fri/Sat/Sun of the current week
    const diff = (d - now) / 864e5;
    return diff >= 0 && diff < 8 && (day === 5 || day === 6 || day === 0);
  }
  return true;
}

function matchesQuery(e, q){
  if (!q) return true;
  const hay = [(e.name||e.artist||""), e.venue, (e.genres||[]).join(" "),
    (e.similar||[]).join(" "), e.title||""].join(" ").toLowerCase();
  return hay.indexOf(q.toLowerCase()) !== -1;
}

function render(){
  const dateMode = document.getElementById("dateSel").value;
  const venue = document.getElementById("venueSel").value;
  const q = document.getElementById("q").value.trim();

  const mDiv = document.getElementById("matches");
  const shown = DATA.matches.filter(function(m){
    return withinDate(m.date, dateMode) && (venue==="all"||m.venue===venue)
        && matchesQuery(m, q);
  });
  mDiv.innerHTML = '<h2>🎤 YOUR ARTISTS IN TOWN</h2>' + (shown.length
    ? shown.map(function(m){return card(m,true);}).join("")
    : '<p class="empty">None match the current filters.</p>');

  const dDiv = document.getElementById("discover");
  let out = "", total = 0;
  FAMILIES.forEach(function(fam){
    if (!famOn.has(fam)) return;
    const entries = DATA.discover.filter(function(e){
      return e.family===fam && withinDate(e.date, dateMode)
          && (venue==="all"||e.venue===venue) && matchesQuery(e, q);
    });
    if (!entries.length) return;
    total += entries.length;
    out += '<h2>'+esc(fam.toUpperCase())+'</h2>'
         + entries.map(function(e){return card(e,false);}).join("");
  });
  dDiv.innerHTML = '<h2 style="color:#fff">🔍 DISCOVER</h2>'
    + (out || '<p class="empty">Nothing matches the current filters.</p>');
  document.getElementById("count").textContent =
    shown.length + " of your artists · " + total + " discovery picks shown";

  document.querySelectorAll(".songs button").forEach(function(btn){
    btn.addEventListener("click", function(){ resolveAndPlay(btn); });
  });
}
function reset(){
  if (playingBtn) playingBtn.classList.remove("playing");
  playingBtn = null;
}

function deezerJsonp(q){
  return new Promise(function(resolve){
    var cb = "dz" + Math.random().toString(36).slice(2);
    var s = document.createElement("script");
    var done = function(d){ resolve(d); delete window[cb]; s.remove(); };
    window[cb] = done;
    s.src = "https://api.deezer.com/search?limit=3&output=jsonp&callback="
          + cb + "&q=" + encodeURIComponent(q);
    s.onerror = function(){ done(null); };
    setTimeout(function(){ if (window[cb]) done(null); }, 6000);
    document.body.appendChild(s);
  });
}

async function freshPreview(btn){
  if (btn.dataset.res) return btn.dataset.res;
  var a = btn.dataset.artist || "", tr = btn.dataset.track || "";
  var d = await deezerJsonp('artist:"' + a + '" track:"' + tr + '"');
  var hit = d && d.data && d.data[0];
  if (!(hit && hit.preview)) {
    d = await deezerJsonp(a + " " + tr);
    hit = d && d.data && d.data[0];
  }
  var p = (hit && hit.preview) || btn.dataset.src || "";
  if (p) btn.dataset.res = p;
  return p;
}

async function resolveAndPlay(btn){
  if (playingBtn === btn){ if (audio) audio.pause(); reset(); return; }
  if (audio) audio.pause();
  reset();
  btn.classList.add("playing");
  playingBtn = btn;
  var src = await freshPreview(btn);
  if (playingBtn !== btn) return; // user clicked elsewhere meanwhile
  if (!src){
    reset();
    if (btn.dataset.url) window.open(btn.dataset.url, "_blank");
    return;
  }
  audio = new Audio(src);
  audio.onended = reset;
  audio.onerror = function(){
    reset();
    if (btn.dataset.url) window.open(btn.dataset.url, "_blank");
  };
  audio.play().catch(function(){
    reset();
    if (btn.dataset.url) window.open(btn.dataset.url, "_blank");
  });
}

(function init(){
  const chips = document.getElementById("famChips");
  FAMILIES.forEach(function(fam){
    const has = DATA.discover.some(function(e){return e.family===fam;});
    if (!has) { famOn.delete(fam); return; }
    const c = document.createElement("span");
    c.className = "chip on"; c.textContent = fam;
    c.onclick = function(){
      if (famOn.has(fam)) { famOn.delete(fam); c.classList.remove("on"); }
      else { famOn.add(fam); c.classList.add("on"); }
      render();
    };
    chips.appendChild(c);
  });
  const venues = Array.from(new Set(
    DATA.matches.concat(DATA.discover).map(function(e){return e.venue;})
      .filter(Boolean))).sort();
  const vs = document.getElementById("venueSel");
  venues.forEach(function(v){
    const o = document.createElement("option"); o.value = v; o.textContent = v;
    vs.appendChild(o);
  });
  ["dateSel","venueSel"].forEach(function(id){
    document.getElementById(id).addEventListener("change", render);
  });
  document.getElementById("q").addEventListener("input", render);
  render();
})();
</script></body></html>
"""


JOIN_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Join Song of the Week</title>
<style>
body{background:#101016;color:#eee;font-family:Helvetica,Arial,sans-serif;
margin:0;padding:24px}
.wrap{max-width:460px;margin:auto}
h1{font-size:22px}
p,li{color:#b8b8c4;font-size:14px;line-height:1.6}
input{width:100%;box-sizing:border-box;background:#181820;color:#eee;
border:1px solid #2a2a35;border-radius:8px;padding:10px;font-size:15px;
margin:8px 0}
button,.btn{display:inline-block;background:#1DB954;color:#000;font-weight:bold;
font-size:15px;padding:11px 22px;border-radius:22px;border:none;cursor:pointer;
text-decoration:none;margin-top:8px}
pre{background:#181820;border:1px solid #2a2a35;border-radius:8px;padding:12px;
white-space:pre-wrap;word-break:break-all;color:#1DB954;font-size:12px}
.err{color:#ff6b6b}
.hide{display:none}
</style></head><body><div class="wrap">
<h1>\U0001F3A7 Join Song of the Week</h1>
<div id="step1">
<p>Every Wednesday, everyone's most-played song of the week gets dropped in
the group chat. Connecting your Spotify takes ~30 seconds:</p>
<ol>
<li>Make sure James has already added your Spotify email (ask him!)</li>
<li>Type your first name below and hit Connect</li>
<li>Approve on Spotify, then send James the code that appears</li>
</ol>
<input id="name" placeholder="Your first name" maxlength="30">
<button id="go">Connect Spotify</button>
<p class="err hide" id="err1"></p>
</div>
<div id="step2" class="hide">
<p><b style="color:#fff">You're in \U0001F389</b> — copy this whole block and
text/DM it to James. He'll add you and your songs start appearing in the
group chat.</p>
<pre id="snippet"></pre>
<button id="copy">Copy to clipboard</button>
<p class="err hide" id="err2"></p>
</div>
<script>
var CLIENT_ID = "__CLIENT_ID__";
var REDIRECT = "__REDIRECT__";
var SCOPES = "user-top-read user-follow-read user-library-read " +
             "user-read-recently-played";

function b64url(buf){
  return btoa(String.fromCharCode.apply(null, new Uint8Array(buf)))
    .replace(/\\+/g,"-").replace(/\\//g,"_").replace(/=+$/,"");
}
async function sha256(s){
  return crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
}
function rand(n){
  var a = new Uint8Array(n); crypto.getRandomValues(a);
  return Array.from(a, function(b){return ("0"+b.toString(16)).slice(-2);})
    .join("");
}

document.getElementById("go").addEventListener("click", async function(){
  var name = document.getElementById("name").value.trim();
  if (!name){ show("err1","Type your name first"); return; }
  var verifier = rand(48);
  sessionStorage.setItem("pkce_v", verifier);
  sessionStorage.setItem("join_name", name);
  var challenge = b64url(await sha256(verifier));
  location.href = "https://accounts.spotify.com/authorize" +
    "?client_id=" + CLIENT_ID +
    "&response_type=code" +
    "&redirect_uri=" + encodeURIComponent(REDIRECT) +
    "&scope=" + encodeURIComponent(SCOPES) +
    "&code_challenge_method=S256&code_challenge=" + challenge;
});

function show(id, msg){
  var el = document.getElementById(id);
  el.textContent = msg; el.classList.remove("hide");
}

(async function init(){
  var code = new URLSearchParams(location.search).get("code");
  if (!code) return;
  document.getElementById("step1").classList.add("hide");
  document.getElementById("step2").classList.remove("hide");
  var verifier = sessionStorage.getItem("pkce_v") || "";
  var name = sessionStorage.getItem("join_name") || "";
  try {
    var r = await fetch("https://accounts.spotify.com/api/token", {
      method: "POST",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: new URLSearchParams({
        grant_type: "authorization_code", code: code,
        redirect_uri: REDIRECT, client_id: CLIENT_ID,
        code_verifier: verifier,
      }),
    });
    var d = await r.json();
    if (!d.refresh_token) throw new Error(d.error_description || "no token");
    var snippet = JSON.stringify({name: name || "FIRSTNAME",
      refresh_token: d.refresh_token, digest: false});
    document.getElementById("snippet").textContent = snippet;
    document.getElementById("copy").addEventListener("click", function(){
      navigator.clipboard.writeText(snippet);
      this.textContent = "Copied \u2713";
    });
    history.replaceState(null, "", REDIRECT);
  } catch (e) {
    show("err2", "Hmm, that didn't work (" + e.message + "). Most common " +
      "cause: James hasn't added your Spotify email yet, or the link was " +
      "reused - go back and hit Connect again.");
  }
})();
</script></div></body></html>
"""


def _base_url():
    if os.environ.get("PAGES_BASE_URL"):
        return os.environ["PAGES_BASE_URL"].rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}"
    return ""


def build_site(users_data, out_dir, today, end):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, ".nojekyll"), "w") as f:
        f.write("")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(LANDING)
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    base = _base_url()
    if client_id and base:
        join_dir = os.path.join(out_dir, "join")
        os.makedirs(join_dir, exist_ok=True)
        with open(os.path.join(join_dir, "index.html"), "w",
                  encoding="utf-8") as f:
            f.write(JOIN_TEMPLATE
                    .replace("__CLIENT_ID__", client_id)
                    .replace("__REDIRECT__", base + "/join/"))
    window = (
        f"{today.strftime('%b')} {today.day} – {end.strftime('%b')} {end.day}, "
        f"{end.year}"
    )
    for data in users_data:
        page_dir = os.path.join(out_dir, "u", data["slug"])
        os.makedirs(page_dir, exist_ok=True)
        payload = {"matches": data["matches"], "discover": data["discover"]}
        html_out = (
            TEMPLATE.replace("__TITLE__", "Austin Concert Digest")
            .replace(
                "__SUBTITLE__",
                f"{window} · matched to {data['name']}'s Spotify",
            )
            .replace("__FAMILIES__", json.dumps(FAMILIES))
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
        )
        with open(
            os.path.join(page_dir, "index.html"), "w", encoding="utf-8"
        ) as f:
            f.write(html_out)
