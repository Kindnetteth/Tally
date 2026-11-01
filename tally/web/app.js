"use strict";

// ---- tiny helpers ----------------------------------------------------------

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") {
      for (const [prop, val] of Object.entries(v)) {
        if (prop.startsWith("--")) el.style.setProperty(prop, val);
        else el.style[prop] = val;
      }
    }
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function svg(markup, cls = "icon") {
  const wrap = document.createElement("span");
  wrap.innerHTML = `<svg viewBox="0 0 24 24" class="${cls}" aria-hidden="true">${markup}</svg>`;
  return wrap.firstChild;
}
const ICON_DOTS = '<circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/>';
const ICON_CHECK = '<path d="M5 12.5l4.5 4.5L19 7.5"/>';

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 2600);
}

const pad = (n) => String(n).padStart(2, "0");

function epCode(s, e, end) {
  if (e == null) return "";
  const base = s != null ? `S${pad(s)}E${pad(e)}` : `Ep ${e}`;
  return end ? `${base}–E${pad(end)}` : base;
}

function duration(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  const hrs = Math.floor(secs / 3600), mins = Math.floor((secs % 3600) / 60);
  if (hrs) return `${hrs}h ${mins}m`;
  if (mins) return `${mins}m`;
  return `${secs}s`;
}

function clock(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  const hrs = Math.floor(secs / 3600), mins = Math.floor((secs % 3600) / 60), s = secs % 60;
  return hrs ? `${hrs}:${pad(mins)}:${pad(s)}` : `${mins}:${pad(s)}`;
}

function startOfDay(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; }

function dayLabel(ts) {
  const d = new Date(ts * 1000);
  const diff = Math.round((startOfDay(new Date()) - startOfDay(d)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return d.toLocaleDateString(undefined, { weekday: "long" });
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: sameYear ? undefined : "numeric" });
}

function timeOfDay(ts) {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function relative(ts) {
  if (!ts) return "";
  const secs = Date.now() / 1000 - ts;
  if (secs < 90) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
  if (secs < 86400 && new Date(ts * 1000).getDate() === new Date().getDate()) return `today, ${timeOfDay(ts)}`;
  const label = dayLabel(ts);
  return label === "Yesterday" ? "yesterday" : label;
}

function airLabel(date) {
  if (!date) return "";
  return new Date(date + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function hue(text) {
  let x = 0;
  for (const ch of text || "") x = (x * 31 + ch.charCodeAt(0)) % 360;
  return x;
}

function poster(title, extra) {
  const name = title ? title.name : "Unknown";
  if (title && title.poster) {
    return h("div", { class: "poster" }, h("img", { src: title.poster, alt: "", loading: "lazy" }), extra);
  }
  const hh = hue(name);
  return h("div", {
    class: "poster placeholder",
    style: { background: `linear-gradient(160deg, hsl(${hh} 45% 46%), hsl(${(hh + 40) % 360} 50% 26%))` },
  }, h("span", {}, name), extra);
}

function bar(progress, done) {
  if (progress == null) return null;
  return h("div", { class: "bar" + (done ? " done" : "") }, h("i", { style: { width: `${Math.round(progress * 100)}%` } }));
}

function watchLabel(w) {
  if (!w.title) return w.filename;
  return w.title.name;
}

function watchSub(w) {
  const bits = [];
  if (w.episode != null) bits.push(epCode(w.season, w.episode, w.episode_end));
  if (w.episode_name) bits.push(w.episode_name);
  if (!w.title || !w.title.matched) bits.push(w.filename);
  if (w.title && w.title.kind === "movie" && w.title.year) bits.push(String(w.title.year));
  return bits.join(" · ");
}

function progressText(w) {
  if (w.completed) return "Finished";
  if (w.progress == null) return duration(w.watched_seconds);
  if (w.duration && w.position != null) {
    const left = w.duration - w.position;
    return `${w.estimated ? "~" : ""}${duration(left)} left`;
  }
  return `${Math.round(w.progress * 100)}%`;
}

function closeMenus() { document.querySelectorAll(".menu").forEach((m) => m.remove()); }
document.addEventListener("click", (e) => { if (!e.target.closest(".menu, .menu-btn")) closeMenus(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeMenus(); closeModal(); } });

function menuButton(items) {
  const btn = h("button", { class: "menu-btn", "aria-label": "More actions", type: "button" }, svg(ICON_DOTS));
  btn.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    const open = document.querySelector(".menu");
    closeMenus();
    if (open && open._owner === btn) return;
    const menu = h("div", { class: "menu", role: "menu" }, items.filter(Boolean).map((it) =>
      h("button", { class: it.danger ? "danger" : "", role: "menuitem", type: "button", onclick: async () => {
        closeMenus();
        try { await it.run(); } catch (err) { toast(err.message); }
      } }, it.label)));
    menu._owner = btn;
    document.body.append(menu);
    const r = btn.getBoundingClientRect();
    const left = Math.min(window.scrollX + r.right - menu.offsetWidth, window.scrollX + document.documentElement.clientWidth - menu.offsetWidth - 8);
    menu.style.left = `${Math.max(8, left)}px`;
    menu.style.top = `${window.scrollY + r.bottom + 4}px`;
  });
  return btn;
}

function watchMenu(w, reload) {
  return menuButton([
    w.title && { label: "Open " + (w.title.kind === "show" ? "show" : "movie"), run: () => (location.hash = `#/title/${w.title.id}`) },
    { label: w.completed ? "Mark as not finished" : "Mark as finished", run: async () => {
      await api(`/api/watches/${w.id}/completed`, { completed: !w.completed }); reload();
    } },
    w.path && { label: "Copy file path", run: async () => { await navigator.clipboard.writeText(w.path); toast("Path copied"); } },
    { label: "Remove from history", danger: true, run: async () => {
      if (!confirm("Remove this entry from your history?")) return;
      await api(`/api/watches/${w.id}/delete`, {}); toast("Removed"); reload();
    } },
  ]);
}

function watchRow(w, reload) {
  const link = w.title ? `#/title/${w.title.id}` : null;
  const meta = h("div", { class: "row-meta" },
    h("div", {}, timeOfDay(w.started_at)),
    h("div", {}, w.manual ? h("span", { class: "pill" }, "Marked") : w.completed ? h("span", { class: "pill good" }, "Finished") : progressText(w)),
    w.completed ? null : bar(w.progress));
  return h("div", { class: "row" },
    h("a", { href: link || "#/history", tabindex: -1 }, poster(w.title)),
    h("div", { class: "row-main" },
      h("div", { class: "row-title" }, link ? h("a", { href: link }, watchLabel(w)) : watchLabel(w)),
      h("div", { class: "row-sub", title: w.path || "" }, watchSub(w) || " ",
        w.player ? h("span", { class: "faint" }, `  ·  ${w.player}`) : null)),
    meta, watchMenu(w, reload));
}

function groupByDay(watches) {
  const groups = [];
  for (const w of watches) {
    const label = dayLabel(w.started_at);
    let g = groups[groups.length - 1];
    if (!g || g.label !== label) { g = { label, items: [], secs: 0 }; groups.push(g); }
    g.items.push(w);
    g.secs += w.watched_seconds || 0;
  }
  return groups;
}

function dayGroups(watches, reload) {
  return groupByDay(watches).map((g) => h("div", { class: "day" },
    h("div", { class: "day-head" }, h("span", {}, g.label), h("span", {}, duration(g.secs))),
    h("div", { class: "rows" }, g.items.map((w) => watchRow(w, reload)))));
}

function empty(title, text) {
  return h("div", { class: "empty" }, h("strong", {}, title), text);
}

// ---- modal -----------------------------------------------------------------

function closeModal() { document.getElementById("modal-root").replaceChildren(); }

function openModal(content) {
  const root = document.getElementById("modal-root");
  const backdrop = h("div", { class: "backdrop", onclick: (e) => { if (e.target === backdrop) closeModal(); } },
    h("div", { class: "modal", role: "dialog", "aria-modal": "true" }, content));
  root.replaceChildren(backdrop);
  const input = backdrop.querySelector("input");
  if (input) input.focus();
}

function fixMatch(title, done) {
  let kind = title.kind;
  const input = h("input", { class: "input", value: title.name, placeholder: "Search by name", "aria-label": "Search" });
  const results = h("div", { class: "results" });
  const seg = h("div", { class: "seg" });
  const kinds = [["show", "TV show"], ["movie", "Movie"]];
  function renderSeg() {
    seg.replaceChildren(...kinds.map(([k, label]) => h("button", { type: "button", class: k === kind ? "on" : "", onclick: () => { kind = k; renderSeg(); search(); } }, label)));
  }
  let seq = 0;
  async function search() {
    const q = input.value.trim();
    if (!q) return;
    const mine = ++seq;
    results.replaceChildren(h("div", { class: "muted small" }, "Searching…"));
    try {
      const found = await api(`/api/search?kind=${kind}&q=${encodeURIComponent(q)}`);
      if (mine !== seq) return;
      if (!found.length) { results.replaceChildren(h("div", { class: "muted small" }, "No results. Try a different spelling.")); return; }
      results.replaceChildren(...found.map((c) => h("button", { class: "result", type: "button", onclick: async () => {
        results.replaceChildren(h("div", { class: "muted small" }, `Matching ${c.name}…`));
        try {
          const r = await api(`/api/titles/${title.id}/match`, { ...c });
          closeModal(); toast(`Matched to ${c.name}`); done(r.title_id);
        } catch (err) { toast(err.message); search(); }
      } },
        poster({ name: c.name, poster: c.poster_url }),
        h("div", { class: "row-main" },
          h("div", { class: "row-title" }, c.name, c.year ? h("span", { class: "muted" }, ` (${c.year})`) : null),
          h("div", { class: "row-sub" }, c.overview || ""),
          h("div", { class: "faint small" }, { tvmaze: "TVmaze", tmdb: "TMDB", imdb: "IMDb" }[c.source] || c.source)))));
    } catch (err) { results.replaceChildren(h("div", { class: "muted small" }, err.message)); }
  }
  let timer;
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 400); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(timer); search(); } });
  renderSeg();
  openModal([h("h2", {}, "Fix match"),
    h("p", { class: "muted small", style: { marginTop: "-6px" } }, "Pick the right title. Every file already logged under this one moves with it."),
    h("div", { class: "form-row" }, input, seg), results]);
  search();
}

// ---- views -----------------------------------------------------------------

const view = () => document.getElementById("view");
let refreshTimer = null;
let currentRender = null;
let refreshEvery = 30000; // live pages; 5s while something is playing

// Settings from /api/prefs; the defaults here only matter until it answers
let PREFS = { theme: "system", accent: "orchid", app_icon: "match", paused: false, week_start: "monday", hide_spoilers: false };

function applyLook(p) {
  const root = document.documentElement;
  if (p.theme && p.theme !== "system") root.dataset.theme = p.theme; else delete root.dataset.theme;
  root.dataset.accent = p.accent || "orchid";
  try { localStorage.setItem("tally-look", JSON.stringify({ theme: p.theme, accent: p.accent })); } catch (e) { /* private window */ }
}

async function loadPrefs() {
  try { PREFS = await api("/api/prefs"); applyLook(PREFS); } catch (e) { /* keep what we have */ }
  return PREFS;
}

async function savePrefs(changes) {
  const res = await api("/api/settings", changes);
  if (res.prefs) { PREFS = res.prefs; applyLook(PREFS); }
  // Inside the app window, the Dock icon is the window's to change
  if (("app_icon" in changes || "accent" in changes) && window.pywebview && window.pywebview.api) {
    window.pywebview.api.set_icon(PREFS.app_icon === "match" ? PREFS.accent : PREFS.app_icon);
  }
  return PREFS;
}

function weekStart() {
  const d = startOfDay(new Date());
  const day = PREFS.week_start === "sunday" ? d.getDay() : (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - day);
  return d.getTime() / 1000;
}

const ICON_PLAY = '<path d="M8 5.5v13l11-6.5z" fill="currentColor" stroke="none"/>';

async function playFile(path, label) {
  try { await api("/api/play", { path }); toast(`Playing ${label}`); }
  catch (err) { toast(err.message); }
}

// Round play button over a poster; clicking it plays instead of opening the title
function posterPlay(path, label) {
  return h("button", { class: "poster-play", type: "button", title: `Play ${label}`, "aria-label": `Play ${label}`,
    onclick: (e) => { e.preventDefault(); e.stopPropagation(); playFile(path, label); } }, svg(ICON_PLAY, "icon"));
}

function untilLabel(date) {
  const days = Math.round((new Date(date + "T12:00:00") - startOfDay(new Date())) / 86400000 - 0.5);
  if (days <= 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 14) return `in ${days} days`;
  if (days < 60) return `in ${Math.round(days / 7)} weeks`;
  return `in ${Math.round(days / 30)} months`;
}

function shortDate(date) {
  const d = new Date(date + "T12:00:00");
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

function updateCard(item) {
  const t = item.title;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const kinds = {
    new_season: ["New season", `Season ${item.season} is out`, plural(item.count, "episode") + (item.count < item.season_size ? ` of ${item.season_size} so far` : "")],
    new_episodes: ["New", plural(item.count, "new episode"), `From ${epCode(item.season, item.episode)}`],
    premiere: ["Soon", `Season ${item.season} · ${item.airdate ? shortDate(item.airdate) : ""}`, item.airdate ? `Premieres ${untilLabel(item.airdate)}` : ""],
    next_episode: ["Soon", `${epCode(item.season, item.episode)} · ${item.airdate ? shortDate(item.airdate) : ""}`, item.airdate ? `Airs ${untilLabel(item.airdate)}` : ""],
    announced: ["Announced", `Season ${item.season} announced`, "No release date yet"],
  };
  const [badge, line, sub] = kinds[item.kind];
  const out = item.kind === "new_season" || item.kind === "new_episodes";
  const label = `${t.name} ${epCode(item.season, item.episode)}`;
  return h("a", { class: "card", href: `#/title/${t.id}` },
    poster(t, [h("span", { class: "badge" + (out ? " fresh" : "") }, badge), item.file ? posterPlay(item.file, label) : null]),
    h("div", { class: "card-title" }, t.name),
    h("div", { class: "card-sub" + (out ? " accent" : ""), title: item.episode_name || "" }, line),
    h("div", { class: "card-sub faint" }, sub));
}

function continueCard(item) {
  const t = item.title;
  let sub, cls = "card-sub", badge = null, prog = null;
  if (item.state === "resume") {
    const w = item.watch;
    sub = t.kind === "show" ? `${epCode(item.season, item.episode)} · ${progressText(w)}` : progressText(w);
    prog = bar(item.progress);
  } else if (item.state === "upcoming") {
    sub = `${epCode(item.season, item.episode)} · airs ${airLabel(item.airdate)}`;
    badge = h("span", { class: "badge" }, "Soon");
  } else {
    sub = `Up next · ${epCode(item.season, item.episode)}`;
    cls += " accent";
    badge = h("span", { class: "badge" }, "Next");
  }
  const label = t.kind === "show" ? `${t.name} ${epCode(item.season, item.episode)}` : t.name;
  return h("a", { class: "card", href: `#/title/${t.id}` },
    poster(t, [badge, prog, item.file ? posterPlay(item.file, label) : null]),
    h("div", { class: "card-title" }, t.name),
    h("div", { class: cls, title: item.episode_name || "" }, sub));
}

function nowPlaying(items) {
  return items.map((w) => {
    const paused = w.paused === true;
    return h("a", { class: "now", href: w.title ? `#/title/${w.title.id}` : "#/" },
      poster(w.title),
      h("div", { class: "now-body" },
        h("div", { class: "now-label" + (paused ? " paused" : "") }, h("span", { class: "dot" }), paused ? "Paused" : "Now playing", h("span", { class: "faint" }, ` · ${w.player}`)),
        h("h3", {}, watchLabel(w)),
        h("div", { class: "muted small" }, watchSub(w)),
        bar(w.progress),
        h("div", { class: "faint small", style: { marginTop: "4px" } },
          w.position != null && w.duration ? `${w.estimated ? "about " : ""}${clock(w.position)} of ${clock(w.duration)}` : `${duration(w.watched_seconds)} so far`)));
  });
}

async function renderHome() {
  await loadPrefs();
  const [data, updates] = await Promise.all([api(`/api/home?week_start=${weekStart()}`), api("/api/updates").catch(() => [])]);
  refreshEvery = data.now_playing.length ? 5000 : 30000;
  const s = data.stats;
  const reload = () => route();
  const parts = [];
  if (PREFS.paused) {
    parts.push(h("div", { class: "paused-banner" }, "Logging is paused. Nothing you play is being recorded.",
      h("button", { class: "btn primary", type: "button", onclick: async () => { await savePrefs({ paused: false }); toast("Logging again"); reload(); } }, "Resume")));
  }
  if (data.now_playing.length) parts.push(h("section", { class: "section" }, nowPlaying(data.now_playing)));
  parts.push(h("section", { class: "section" },
    h("div", { class: "section-head" }, h("h1", {}, "This week")),
    h("div", { class: "stats" },
      h("div", { class: "stat" }, h("div", { class: "num" }, duration(s.week_seconds)), h("div", { class: "lbl" }, "watched")),
      h("div", { class: "stat" }, h("div", { class: "num" }, s.week_episodes), h("div", { class: "lbl" }, s.week_episodes === 1 ? "episode" : "episodes")),
      h("div", { class: "stat" }, h("div", { class: "num" }, s.week_movies), h("div", { class: "lbl" }, s.week_movies === 1 ? "movie" : "movies")),
      h("div", { class: "stat" }, h("div", { class: "num" }, duration(s.total_seconds)), h("div", { class: "lbl" }, "all time")))));
  parts.push(h("section", { class: "section" },
    h("div", { class: "section-head" }, h("h2", {}, "Continue watching"), data.continue.length ? h("a", { class: "link small", href: "#/shows" }, "All shows") : null),
    data.continue.length
      ? h("div", { class: "rail" }, data.continue.map(continueCard))
      : empty("Nothing in progress", "Open a video in IINA, VLC, mpv or your usual player and it will show up here.")));
  if (updates.length) {
    parts.push(h("section", { class: "section" },
      h("div", { class: "section-head" }, h("h2", {}, "New & upcoming"), h("span", { class: "muted small" }, "From shows you've watched")),
      h("div", { class: "rail" }, updates.map(updateCard))));
  }
  parts.push(h("section", { class: "section" },
    h("div", { class: "section-head" }, h("h2", {}, "Watched this week"), h("a", { class: "link small", href: "#/history" }, "Full history")),
    data.week.length ? dayGroups(data.week, reload) : empty("Nothing yet this week", "Everything you play is logged automatically.")));
  return parts;
}

function showSub(item) {
  const c = item.current;
  if (!c) return ["", "card-sub"];
  if (c.state === "resume") return [`${epCode(c.season, c.episode)} · ${Math.round((c.progress || 0) * 100)}%`, "card-sub"];
  if (c.state === "next") return [`Up next · ${epCode(c.season, c.episode)}`, "card-sub accent"];
  if (c.state === "upcoming") return [`${epCode(c.season, c.episode)} · ${airLabel(c.airdate)}`, "card-sub"];
  return ["Caught up", "card-sub"];
}

let showsFilter = "all";
async function renderShows() {
  const shows = await api("/api/shows");
  if (!shows.length) return [h("h1", {}, "Shows"), h("div", { style: { marginTop: "16px" } }, empty("No shows yet", "Episodes you watch are grouped into shows here."))];
  const filters = [["all", "All"], ["watching", "In progress"], ["caught_up", "Caught up"]];
  const matches = (it) => showsFilter === "all" || (showsFilter === "caught_up" ? it.current && it.current.state === "caught_up" : !(it.current && it.current.state === "caught_up"));
  const grid = h("div", { class: "grid" });
  const seg = h("div", { class: "seg" });
  function draw() {
    seg.replaceChildren(...filters.map(([k, l]) => h("button", { type: "button", class: k === showsFilter ? "on" : "", onclick: () => { showsFilter = k; draw(); } }, l)));
    grid.replaceChildren(...shows.filter(matches).map((it) => {
      const [sub, cls] = showSub(it);
      const total = it.total_episodes;
      const done = it.watched_episodes;
      const badge = total ? h("span", { class: "badge" + (done >= total ? " done" : "") }, `${done}/${total}`) : null;
      return h("a", { class: "card", href: `#/title/${it.title.id}` },
        poster(it.title, [badge, total ? bar(done / total, done >= total) : null]),
        h("div", { class: "card-title" }, it.title.name),
        h("div", { class: cls }, sub),
        h("div", { class: "card-sub faint" }, relative(it.last_watched)));
    }));
  }
  draw();
  return [h("div", { class: "section-head" }, h("h1", {}, "Shows"), seg), grid];
}

async function renderMovies() {
  const movies = await api("/api/movies");
  if (!movies.length) return [h("h1", {}, "Movies"), h("div", { style: { marginTop: "16px" } }, empty("No movies yet", "Movies you play show up here with their poster and year."))];
  return [h("div", { class: "section-head" }, h("h1", {}, "Movies"), h("span", { class: "muted" }, `${movies.length} watched`)),
    h("div", { class: "grid" }, movies.map((m) => {
      const done = m.times_finished > 0;
      return h("a", { class: "card", href: `#/title/${m.title.id}` },
        poster(m.title, [done ? h("span", { class: "badge done" }, m.times_finished > 1 ? `Watched ${m.times_finished}×` : "Watched") : null,
          !done && m.progress != null ? bar(m.progress) : null]),
        h("div", { class: "card-title" }, m.title.name),
        h("div", { class: "card-sub" }, [m.title.year, relative(m.last_watched)].filter(Boolean).join(" · ")));
    }))];
}

let openSeason = {};
async function renderTitle(id) {
  const data = await api(`/api/titles/${id}`);
  const t = data.title;
  const reload = () => route();
  const metaBits = [
    h("span", { class: "pill" }, t.kind === "show" ? "TV show" : "Movie"),
    t.year ? h("span", {}, t.year) : null,
    t.runtime ? h("span", {}, `${t.runtime} min`) : null,
    t.status ? h("span", {}, t.status) : null,
    !t.matched ? h("span", { class: "pill accent" }, "Not matched yet") : null,
  ];
  const links = [];
  if (t.source === "tvmaze") links.push(h("a", { class: "link small", href: `https://www.tvmaze.com/shows/${t.source_id}`, target: "_blank", rel: "noopener" }, "TVmaze"));
  if (t.source === "tmdb") links.push(h("a", { class: "link small", href: `https://www.themoviedb.org/movie/${t.source_id}`, target: "_blank", rel: "noopener" }, "TMDB"));
  if (t.imdb_id) links.push(h("a", { class: "link small", href: `https://www.imdb.com/title/${t.imdb_id}/`, target: "_blank", rel: "noopener" }, "IMDb"));

  const actions = h("div", { class: "btn-row" },
    h("button", { class: "btn", type: "button", onclick: () => fixMatch(t, (newId) => { location.hash = `#/title/${newId}`; route(); }) }, t.matched ? "Fix match" : "Find match"),
    h("button", { class: "btn", type: "button", onclick: async (e) => {
      e.target.disabled = true;
      try { await api(`/api/titles/${t.id}/refresh`, {}); toast("Updated"); reload(); } catch (err) { toast(err.message); e.target.disabled = false; }
    } }, "Refresh info"),
    menuButton([{ label: "Delete this title and its history", danger: true, run: async () => {
      if (!confirm(`Delete ${t.name} and all ${data.history.length} history entries?`)) return;
      await api(`/api/titles/${t.id}/delete`, {}); location.hash = t.kind === "show" ? "#/shows" : "#/movies";
    } }]));

  const back = h("button", { class: "back", type: "button", onclick: () => {
    if (history.length > 1) history.back();
    else location.hash = t.kind === "show" ? "#/shows" : "#/movies";
  } }, svg('<path d="M15 18l-6-6 6-6"/>', "icon"), "Back");
  const parts = [back, h("div", { class: "hero" }, poster(t), h("div", {},
    h("h1", {}, t.name),
    h("div", { class: "hero-meta" }, metaBits, links),
    t.overview ? h("p", { class: "overview" }, t.overview) : h("p", { class: "muted" }, t.matched ? "No description available." : "Tally couldn't find this title automatically. Use Find match to pick it."),
    data.current ? currentCallout(data.current, t) : null,
    t.kind === "movie" && data.file ? h("div", { class: "btn-row", style: { margin: "16px 0" } },
      h("button", { class: "btn primary", type: "button", onclick: () => playFile(data.file, t.name) }, svg(ICON_PLAY, "icon"), "Play")) : null,
    actions))];

  if (t.kind === "show" && data.seasons && data.seasons.length) {
    const seasons = data.seasons;
    const cur = data.current;
    const defaultSeason = cur && cur.season != null && seasons.some((s) => s.season === cur.season) ? cur.season : seasons[0].season;
    let selected = openSeason[t.id] != null && seasons.some((s) => s.season === openSeason[t.id]) ? openSeason[t.id] : defaultSeason;
    const seg = h("div", { class: "seg", style: { flexWrap: "wrap" } });
    const list = h("div", { class: "rows" });
    const drawSeason = () => {
      openSeason[t.id] = selected;
      seg.replaceChildren(...seasons.map((s) => {
        const watched = s.episodes.filter((e) => e.watched).length;
        const label = s.season === 0 ? "Specials" : s.season === -1 ? "Other" : `Season ${s.season}`;
        return h("button", { type: "button", class: s.season === selected ? "on" : "", onclick: () => { selected = s.season; drawSeason(); } },
          label, watched ? h("span", { class: "faint" }, ` ${watched}/${s.episodes.length}`) : null);
      }));
      const season = seasons.find((s) => s.season === selected);
      list.replaceChildren(...season.episodes.map((e) => episodeRow(t, e, cur, reload)));
    };
    drawSeason();
    parts.push(h("section", { class: "section" }, h("div", { class: "section-head" }, h("h2", {}, "Episodes")), h("div", { style: { marginBottom: "12px" } }, seg), list));
  }

  parts.push(moreLikeThis(t));
  parts.push(h("section", { class: "section" },
    h("div", { class: "section-head" }, h("h2", {}, "History"), h("span", { class: "muted small" }, `${data.history.length} ${data.history.length === 1 ? "entry" : "entries"}`)),
    data.history.length ? dayGroups(data.history, reload) : empty("No history", "")));
  return parts;
}

// Recommendations from TMDB; filled in after the page shows so it never waits on them
const similarCache = {};
function moreLikeThis(t) {
  const section = h("section", { class: "section" });
  const draw = (res) => {
    if (res.needs_key) {
      section.replaceChildren(h("div", { class: "section-head" }, h("h2", {}, "More like this")),
        h("p", { class: "muted small" }, "Add a free TMDB key in ", h("a", { class: "link", href: "#/settings" }, "Settings"), " to see shows and movies like this one."));
    } else if (res.items && res.items.length) {
      section.replaceChildren(h("div", { class: "section-head" }, h("h2", {}, "More like this")),
        h("div", { class: "rail" }, res.items.map((it) => {
          const art = it.poster
            ? h("div", { class: "poster" }, h("img", { src: it.poster, alt: "", loading: "lazy" }), it.title_id ? h("span", { class: "badge done" }, "Watched") : null)
            : poster({ name: it.name });
          const attrs = it.title_id ? { href: `#/title/${it.title_id}` } : { href: it.url, target: "_blank", rel: "noopener" };
          return h("a", { class: "card", title: it.overview || "", ...attrs }, art,
            h("div", { class: "card-title" }, it.name), h("div", { class: "card-sub" }, [it.year, it.kind === "show" ? "TV show" : "Movie"].filter(Boolean).join(" · ")));
        })));
    } else {
      section.replaceChildren();
    }
  };
  if (similarCache[t.id]) draw(similarCache[t.id]);
  else api(`/api/titles/${t.id}/similar`).then((res) => { similarCache[t.id] = res; draw(res); }).catch(() => section.replaceChildren());
  return section;
}

function currentCallout(c, t) {
  let big, sub = c.episode_name || "";
  if (c.state === "resume") big = `Partway through ${epCode(c.season, c.episode)}`;
  else if (c.state === "next") big = `Up next: ${epCode(c.season, c.episode)}`;
  else if (c.state === "upcoming") { big = `Next: ${epCode(c.season, c.episode)}`; sub = `${sub ? sub + " · " : ""}airs ${airLabel(c.airdate)}`; }
  else { big = "You're caught up"; sub = `Last watched ${epCode(c.season, c.episode)}`; }
  const play = c.file ? h("button", { class: "btn primary", type: "button", onclick: () => playFile(c.file, `${t.name} ${epCode(c.season, c.episode)}`) },
    svg(ICON_PLAY, "icon"), c.state === "resume" ? "Resume" : `Play ${epCode(c.season, c.episode)}`) : null;
  return h("div", { class: "callout" },
    h("div", {}, h("div", { class: "big" }, big), h("div", { class: "muted small" }, sub || " ")),
    c.state === "resume" && c.watch ? h("div", { class: "muted small", style: { marginLeft: "auto" } }, progressText(c.watch)) : null,
    play,
    c.state === "resume" ? bar(c.progress) : null);
}

function episodeRow(t, e, cur, reload) {
  const isCurrent = cur && cur.state !== "caught_up" && cur.season === e.season && cur.episode === e.episode;
  const partial = !e.watched && e.progress > 0.01;
  const check = h("button", {
    class: "check" + (e.watched ? " on" : "") + (partial ? " partial" : ""), type: "button",
    style: partial ? { "--p": `${Math.round(e.progress * 100)}%` } : null,
    title: e.watched ? "Mark as not watched" : "Mark as watched",
    "aria-label": e.watched ? "Mark as not watched" : "Mark as watched",
    onclick: async () => {
      try { await api(`/api/titles/${t.id}/episode`, { season: e.season, episode: e.episode, watched: !e.watched }); reload(); }
      catch (err) { toast(err.message); }
    },
  }, svg(ICON_CHECK, ""));
  const future = e.airdate && e.airdate > new Date().toISOString().slice(0, 10);
  const sub = [e.airdate ? (future ? `Airs ${airLabel(e.airdate)}` : airLabel(e.airdate)) : null,
    e.last_watched ? `watched ${relative(e.last_watched)}` : null,
    e.times > 1 ? `${e.times} times` : null].filter(Boolean).join(" · ");
  return h("div", { class: "ep" + (isCurrent ? " current" : "") },
    h("div", { class: "ep-num" }, e.episode),
    h("div", { class: "row-main" },
      h("div", { class: "row-title" + (future ? " faint" : "") },
        h("span", { class: PREFS.hide_spoilers && e.name && !e.watched ? "spoiler" : null, title: PREFS.hide_spoilers && !e.watched ? "Hover to reveal" : null }, e.name || `Episode ${e.episode}`), isCurrent ? h("span", { class: "pill accent", style: { marginLeft: "8px" } }, cur.state === "resume" ? "Watching" : "Next") : null),
      h("div", { class: "row-sub" }, sub || " ")),
    partial ? h("span", { class: "muted small" }, `${Math.round(e.progress * 100)}%`) : null,
    check,
    e.season > 0 ? menuButton([{ label: "Mark all up to here as watched", run: async () => {
      const r = await api(`/api/titles/${t.id}/through`, { season: e.season, episode: e.episode });
      toast(`Marked ${r.marked} episode${r.marked === 1 ? "" : "s"}`); reload();
    } }]) : null);
}

let historyQuery = "";
async function renderHistory() {
  const input = h("input", { class: "input search", type: "search", placeholder: "Search titles, episodes, files", value: historyQuery, "aria-label": "Search history" });
  const body = h("div", {});
  const more = h("button", { class: "btn", type: "button", style: { marginTop: "16px" } }, "Load more");
  let items = [];
  let seq = 0;
  async function load(reset) {
    const mine = ++seq;
    const before = !reset && items.length ? `&before=${items[items.length - 1].started_at}` : "";
    const page = await api(`/api/history?limit=100&q=${encodeURIComponent(historyQuery)}${before}`);
    if (mine !== seq) return;
    items = reset ? page : items.concat(page);
    body.replaceChildren(...(items.length ? dayGroups(items, () => load(true)) : [empty(historyQuery ? "No matches" : "No history yet", historyQuery ? "Try another search." : "Play something and it will be logged here.")]));
    more.hidden = page.length < 100;
  }
  let timer;
  input.addEventListener("input", () => { historyQuery = input.value; clearTimeout(timer); timer = setTimeout(() => load(true), 250); });
  more.addEventListener("click", () => load(false));
  await load(true);
  return [h("div", { class: "section-head" }, h("h1", {}, "History"), h("a", { class: "link small", href: "/api/export" }, "Export JSON")), h("div", { style: { margin: "0 0 18px" } }, input), body, more];
}

const SOURCE_HELP = {
  mpv: "Reads exact position from IINA and mpv. Set up players turns this on; reopen IINA once afterwards.",
  vlc: "Uses VLC's web interface on this computer only. Set up players turns it on; restart VLC afterwards.",
  mpc: "Uses MPC-HC/MPC-BE's local web interface (port 13579). Set up players turns it on; restart the player afterwards.",
  openfiles: "Notices video files open in other players (QuickTime, Infuse, PotPlayer, Movies & TV…). Progress is estimated from time spent.",
};

const THEMES = [
  ["system", "Automatic", ["#f6f6f8", "#1b1b21"]],
  ["light", "Light", ["#f6f6f8", "#ffffff"]],
  ["dark", "Dark", ["#111115", "#1b1b21"]],
  ["midnight", "Midnight", ["#000000", "#0c0c0f"]],
  ["glow", "Glow", ["#0e0e13", "#18181f"]],
];
const ACCENTS = [
  ["orchid", "Orchid", "#ff5fa2", "#8b5cf6"], ["violet", "Violet", "#b06bff", "#4f6bff"],
  ["ocean", "Ocean", "#38bdf8", "#2563eb"], ["mint", "Mint", "#34d399", "#0ea5a4"],
  ["sunset", "Sunset", "#ffb347", "#ff4f6d"], ["graphite", "Graphite", "#a1a1ad", "#52525e"],
];

function settingRow(title, sub, control) {
  return h("div", { class: "setting" },
    h("div", { class: "setting-text" }, h("div", { class: "row-title" }, title), sub ? h("div", { class: "row-sub" }, sub) : null),
    control);
}

function toggle(checked, onchange, label) {
  const input = h("input", { type: "checkbox", role: "switch", checked, "aria-label": label, onchange: async () => {
    try { await onchange(input.checked); } catch (err) { toast(err.message); input.checked = !input.checked; }
  } });
  return h("label", { class: "switch" }, input, h("span"));
}

function choice(options, value, onchange, label) {
  const sel = h("select", { class: "input", "aria-label": label, onchange: async () => {
    try { await onchange(isNaN(+sel.value) ? sel.value : +sel.value); } catch (err) { toast(err.message); }
  } }, options.map(([v, text]) => h("option", { value: v, selected: v === value }, text)));
  return sel;
}

function appearancePanel(st) {
  const redraw = () => route({ quiet: true });
  const themes = h("div", { class: "themes" }, THEMES.map(([id, label, [bg, surface]]) => {
    const a = ACCENTS.find((x) => x[0] === PREFS.accent) || ACCENTS[0];
    const preview = id === "system"
      ? { background: `linear-gradient(135deg, ${bg} 50%, ${surface} 50%)` }
      : id === "glow" ? { background: `radial-gradient(120% 90% at 20% 0%, ${a[2]}55, transparent 60%), radial-gradient(120% 90% at 90% 0%, ${a[3]}66, transparent 60%), ${bg}` }
      : { background: bg };
    return h("button", { class: "theme-card" + (PREFS.theme === id ? " on" : ""), type: "button", "aria-pressed": PREFS.theme === id,
      onclick: async () => { await savePrefs({ theme: id }); redraw(); } },
      h("span", { class: "preview", style: preview },
        h("i", { style: { height: "60%", background: surface, boxShadow: "0 0 0 1px rgba(127,127,127,.25)" } }),
        h("i", { style: { height: "85%", background: `linear-gradient(${a[2]}, ${a[3]})` } }),
        h("i", { style: { height: "40%", background: surface, boxShadow: "0 0 0 1px rgba(127,127,127,.25)" } })),
      h("span", { class: "label" }, label));
  }));
  const swatches = h("div", { class: "swatches" }, ACCENTS.map(([id, label, a1, a2]) =>
    h("button", { class: "swatch" + (PREFS.accent === id ? " on" : ""), type: "button", title: label, "aria-label": label,
      "aria-pressed": PREFS.accent === id, style: { background: `linear-gradient(135deg, ${a1}, ${a2})` },
      onclick: async () => { await savePrefs({ accent: id }); redraw(); } })));
  const iconChoices = [["match", "Match colour"], ...ACCENTS.map(([id, label]) => [id, label]), ["midnight", "Midnight"]];
  const current = PREFS.app_icon === "match" ? PREFS.accent : PREFS.app_icon;
  const icons = h("div", { class: "icons" }, iconChoices.map(([id, label]) =>
    h("button", { class: "icon-choice" + (PREFS.app_icon === id ? " on" : ""), type: "button", "aria-pressed": PREFS.app_icon === id,
      onclick: async () => { await savePrefs({ app_icon: id }); redraw(); } },
      h("img", { src: `/assets/icon-${id === "match" ? current : id}.png`, alt: "" }), label)));
  return h("div", { class: "panel" }, h("h2", {}, "Appearance"),
    h("p", { class: "muted small" }, "Theme"), themes,
    h("p", { class: "muted small", style: { marginTop: "16px" } }, "Colour"), swatches,
    st.desktop ? [h("p", { class: "muted small", style: { marginTop: "16px" } }, "App icon"), icons,
      h("p", { class: "faint small", style: { marginTop: "6px" } }, "Shown in the Dock while Tally is open.")] : null);
}

function loggingPanel(st) {
  const folders = h("textarea", { class: "input", placeholder: "One folder per line, e.g. ~/Movies/Home videos", "aria-label": "Folders to ignore" },
    (PREFS.ignored_folders || []).join("\n"));
  const saveFolders = h("button", { class: "btn", type: "button", onclick: async () => {
    try { await savePrefs({ ignored_folders: folders.value.split("\n") }); toast("Saved"); } catch (err) { toast(err.message); }
  } }, "Save folders");
  return h("div", { class: "panel" }, h("h2", {}, "Logging"),
    settingRow("Pause logging", "Stop recording what you play until you turn this off. Also in the menu bar.",
      toggle(PREFS.paused, (v) => savePrefs({ paused: v }), "Pause logging")),
    st.desktop ? settingRow("Open at login", "Start Tally quietly when you log in, so it's always logging.",
      toggle(st.login_item, async (v) => { await api("/api/settings", { login_item: v }); }, "Open at login")) : null,
    settingRow("Only shows and movies", "Skip music, phone videos, screen recordings and your own edits. Downloaded shows and movies, and libraries named like \"Movie (2010)\", are still logged.",
      toggle(PREFS.only_releases, (v) => savePrefs({ only_releases: v }), "Only shows and movies")),
    settingRow("Ignore quick peeks", "Don't log videos you close again within",
      choice([[20, "20 seconds"], [60, "1 minute"], [120, "2 minutes"], [300, "5 minutes"]], PREFS.min_play_seconds,
        (v) => savePrefs({ min_play_seconds: v }), "Ignore quick peeks")),
    settingRow("Counts as finished", "When an episode or movie is marked watched. Earlier entries are rechecked when you change this.",
      choice([[80, "80% or last 5 min"], [85, "85% or last 4 min (credits)"], [90, "90% or last 2 min"], [95, "95% or last minute"], [98, "The very end"]],
        PREFS.finish_at, async (v) => { await savePrefs({ finish_at: v }); toast("Updated"); }, "Counts as finished")),
    h("div", { class: "setting", style: { display: "block" } },
      h("div", { class: "row-title" }, "Folders to ignore"),
      h("div", { class: "row-sub", style: { whiteSpace: "normal", marginBottom: "8px" } }, "Nothing inside these folders is ever logged."),
      folders, h("div", { class: "btn-row", style: { marginTop: "8px" } }, saveFolders)));
}

function displayPanel() {
  const seg = (options, value, onpick) => h("div", { class: "seg" }, options.map(([v, text]) =>
    h("button", { class: v === value ? "on" : null, type: "button", onclick: onpick.bind(null, v) }, text)));
  return h("div", { class: "panel" }, h("h2", {}, "Display"),
    settingRow("Week starts on", "For This week on the home page.",
      seg([["monday", "Monday"], ["sunday", "Sunday"]], PREFS.week_start, async (v) => { await savePrefs({ week_start: v }); route({ quiet: true }); })),
    settingRow("Play with", "Which app the Play buttons open episodes and movies in.",
      seg([["iina", "IINA"], ["default", "Default player"]], PREFS.play_with || "iina", async (v) => { await savePrefs({ play_with: v }); route({ quiet: true }); })),
    settingRow("Hide spoilers", "Blur the names of episodes you haven't watched yet. Hover to reveal.",
      toggle(PREFS.hide_spoilers, (v) => savePrefs({ hide_spoilers: v }), "Hide spoilers")));
}

async function renderSettings() {
  const [st] = await Promise.all([api("/api/status"), loadPrefs()]);
  const keyInput = h("input", { class: "input", type: "password", placeholder: st.tmdb_key_set ? "Key saved — paste a new one to replace" : "Paste a TMDB API key (optional)", autocomplete: "off", "aria-label": "TMDB API key" });
  const save = h("button", { class: "btn primary", type: "button", onclick: async () => {
    save.disabled = true;
    try { await api("/api/settings", { tmdb_key: keyInput.value }); toast(keyInput.value ? "Key saved" : "Key removed"); route(); }
    catch (err) { toast(err.message); save.disabled = false; }
  } }, "Save");
  const setup = h("button", { class: "btn", type: "button", onclick: async () => {
    setup.disabled = true;
    try { const res = await api("/api/setup-players", {}); toast(res.lines.join(" · ") || "Done"); }
    catch (err) { toast(err.message); }
    setup.disabled = false;
  } }, "Set up players");
  const importIina = h("button", { class: "btn", type: "button", onclick: async () => {
    importIina.disabled = true;
    try { const res = await api("/api/import-iina", {}); toast(res.added ? `Added ${res.added} from IINA's history` : "Nothing new in IINA's history"); }
    catch (err) { toast(err.message); }
    importIina.disabled = false;
  } }, "Import IINA history");
  return [
    h("h1", { style: { marginBottom: "18px" } }, "Settings"),
    appearancePanel(st),
    loggingPanel(st),
    displayPanel(),
    h("div", { class: "panel" }, h("h2", {}, "Players"),
      h("p", { class: "muted small" }, "Tally checks these every few seconds. Green means it's talking to the player right now."),
      h("div", { style: { marginTop: "10px" } }, st.sources.map((s) => h("div", { class: "source" },
        h("span", { class: "status-dot " + s.status }),
        h("div", { class: "row-main" }, h("div", { class: "row-title" }, s.label), h("div", { class: "row-sub", style: { whiteSpace: "normal" } }, s.detail || "", h("div", { class: "faint" }, SOURCE_HELP[s.name] || "")))))),
      h("div", { class: "btn-row", style: { marginTop: "12px" } }, setup, st.iina_history ? importIina : null),
    ),
    h("div", { class: "panel" }, h("h2", {}, "Movie database"),
      h("p", { class: "muted small" }, "Shows use TVmaze and movies use IMDb + Wikipedia, no setup needed. For better movie matches, add a free TMDB key from themoviedb.org → Settings → API."),
      h("div", { class: "form-row" }, keyInput, save),
      st.unmatched ? h("p", { class: "small", style: { marginTop: "12px" } }, `${st.unmatched} title${st.unmatched === 1 ? "" : "s"} not matched yet. `,
        h("a", { class: "link", href: "#", onclick: async (e) => { e.preventDefault(); await api("/api/rematch-unmatched", {}); toast("Retrying in the background"); } }, "Retry now")) : null),
    h("div", { class: "panel" }, h("h2", {}, "Your data"),
      h("p", { class: "muted small" }, "Everything (history, posters, settings) is in one file on this computer:"),
      h("p", {}, h("code", {}, st.data_file)),
      h("div", { class: "btn-row", style: { marginTop: "12px" } },
        h("a", { class: "btn", href: "/api/export" }, "Export as JSON")),
      st.desktop ? null : h("p", { class: "muted small", style: { marginTop: "12px" } }, "To open Tally from your phone on the same Wi-Fi, run it with ", h("code", {}, "python3 -m tally run --lan"), ".")),
    h("div", { class: "panel" }, h("h2", {}, "About"),
      h("p", { class: "muted small" }, `Tally ${st.version} · listening on 127.0.0.1:${st.port} · log: `, h("code", {}, st.log_file)),
      h("div", { class: "btn-row", style: { marginTop: "12px" } },
        h("button", { class: "btn", type: "button", onclick: async () => {
          const later = st.login_item ? "It starts again at your next login." : "Nothing is logged until you open it again.";
          if (!confirm(`Quit Tally? ${later}`)) return;
          await api("/api/quit", {}); document.body.replaceChildren(h("div", { class: "view" }, empty("Tally has stopped", later)));
        } }, "Quit Tally"))),
  ];
}

// ---- router ----------------------------------------------------------------

const routes = [
  [/^#?\/?$/, "home", renderHome, true],
  [/^#\/shows$/, "shows", renderShows, false],
  [/^#\/movies$/, "movies", renderMovies, false],
  [/^#\/history$/, "history", renderHistory, false],
  [/^#\/settings$/, "settings", renderSettings, true],
  [/^#\/title\/(\d+)$/, null, renderTitle, true],
];

let routeSeq = 0;
async function route(opts = {}) {
  clearTimeout(refreshTimer);
  closeMenus();
  const hash = location.hash || "#/";
  const match = routes.find(([re]) => re.test(hash)) || routes[0];
  const [re, tab, render, live] = match;
  const args = (hash.match(re) || []).slice(1);
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  const mine = ++routeSeq;
  const el = view();
  if (!opts.quiet) el.replaceChildren(h("div", { class: "skeleton" }));
  try {
    const parts = await render(...args);
    if (mine !== routeSeq) return;
    const y = window.scrollY;
    el.replaceChildren(...[parts].flat());
    if (opts.quiet) window.scrollTo(0, y);
    currentRender = { hash, live };
  } catch (err) {
    if (mine !== routeSeq) return;
    el.replaceChildren(empty("Couldn't load this page", err.message === "Failed to fetch" ? "Tally isn't running. Open the Tally app to start it." : err.message));
  }
  // Live pages refresh while open so "now playing" stays current
  if (live) {
    const tick = () => {
      if (location.hash !== hash && !(hash === "#/" && !location.hash)) return;
      const busy = document.querySelector(".menu, .backdrop") || (document.activeElement && document.activeElement.tagName === "INPUT");
      if (document.hidden || busy) { refreshTimer = setTimeout(tick, refreshEvery); return; }
      route({ quiet: true });
    };
    refreshTimer = setTimeout(tick, refreshEvery);
  }
}

window.addEventListener("hashchange", () => {
  try { localStorage.setItem("tally-last", location.hash); } catch (e) { /* private window */ }
  route(); window.scrollTo(0, 0);
});
// The app unloads the page while its window is closed and reopens it at #restore
if (location.hash === "#restore") {
  let last = "#/";
  try { last = localStorage.getItem("tally-last") || "#/"; } catch (e) { /* private window */ }
  history.replaceState(null, "", last === "#restore" ? "#/" : last);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && currentRender && currentRender.live) route({ quiet: true }); });
loadPrefs().finally(route);
