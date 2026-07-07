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
  const meta = [ (e.genres||[]).join(", "), fmtDay(e.date), e.venue, e.time ]
      .filter(Boolean).map(esc).join(" · ");
  let detail = "";
  if (isMatch) {
    detail = '<div class="detail" style="color:#77778a">'+esc(e.title)+'</div>';
  } else {
    const bits = [];
    if ((e.similar||[]).length)
      bits.push("similar to " + e.similar.map(esc).join(", "));
    if ((e.songs||[]).length){
      const songs = e.songs.map(function(s){
        if (s.preview)
          return '<button data-src="'+esc(s.preview)+'">▶ '+esc(s.name)+'</button>';
        if (s.url)
          return '<a href="'+esc(s.url)+'" target="_blank"><i>'+esc(s.name)+'</i></a>';
        return '<i>'+esc(s.name)+'</i>';
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
    btn.addEventListener("click", function(){
      if (playingBtn === btn){ audio.pause(); reset(); return; }
      if (audio) audio.pause();
      reset();
      audio = new Audio(btn.dataset.src);
      audio.play();
      btn.classList.add("playing");
      playingBtn = btn;
      audio.onended = reset;
    });
  });
}
function reset(){
  if (playingBtn) playingBtn.classList.remove("playing");
  playingBtn = null;
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


def build_site(users_data, out_dir, today, end):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, ".nojekyll"), "w") as f:
        f.write("")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(LANDING)
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
