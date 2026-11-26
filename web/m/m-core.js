"use strict";
/* ===================================================================== Orbit on the phone: helpers and state
   The phone is one more client of the same server (its HTTP API and /api/events). This file loads first: the shared
   Markdown files (/src/markdown-base.js, /src/markdown.js) and the agents' marks (/src/brands.js) read a few of the
   desktop's globals when they draw; they are defined here (esc, S, mentionTag, MD_MACHINE, MD_CWD, machinePath). */
const M_VERSION = "1.0";  // the phone app's own version (Settings); the server's comes from /api/version
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
/* The phone's own keys (orbit.m.*): a home-screen app on iOS keeps its storage apart from Safari anyway. */
const LS = {
  get(k, d) { try { const v = localStorage.getItem("orbit.m." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("orbit.m." + k, JSON.stringify(v)); } catch {} },
};

/* ===================================================================== state */
const ACTIVE = new Set(["thinking", "running"]);
const FINAL = new Set(["completed", "error", "cancelled"]);
const S = {
  sessions: new Map(),          // every open session, in the server's order (also read by markdown.js for @@ links)
  groups: [],                   // [{id, name, parent?}]: a tree, in the user's order (orbit/desk/groups.py)
  machines: [],                 // other machines: [{name, ok, error}]
  harnesses: [],                // /api/harnesses (brands.js reads it for a plugin harness's icon)
  cards: new Map(),             // sid → [card] for the sessions opened here
  active: new Map(),            // sid → its running card (from the live events: the list's "Working: …")
  current: null,                // the conversation on screen (null: the Sessions list)
  seen: LS.get("seen", null),   // sid → the time of the last answer seen (the unread dot)
  folded: new Set(LS.get("folded", ["fleet"])),
  drafts: LS.get("drafts", {}),
  openSteps: new Set(), closedSteps: new Set(), openReqs: new Set(),
  online: false,
};
const groupName = gid => (S.groups.find(g => g.id === gid) || { name: "?" }).name;
const groupKids = parent => S.groups.filter(g => (g.parent || "") === (parent || ""));
function groupPath(gid, sep = " › ") {
  const names = []; let g = S.groups.find(x => x.id === gid);
  while (g && names.length < 8) { names.unshift(g.name); g = g.parent ? S.groups.find(x => x.id === g.parent) : null; }
  return names.length ? names.join(sep) : "?";
}
const groupSubtree = gid => [gid, ...groupKids(gid).flatMap(c => groupSubtree(c.id))];
const harnessLabel = id => (S.harnesses.find(h => h.id === id) || { label: { claude: "Claude Code", codex: "Codex", agy: "agy", opencode: "OpenCode" }[id] || id }).label;
const machineUp = name => !(S.machines || []).some(m => m.name === name && !m.ok);
const mentionTag = name => "@@" + String(name).trim().replace(/\s+/g, "-");  // (markdown.js: @@Name links)

/* What markdown.js reads while a card of another machine's session is drawn: that machine's paths. */
let MD_MACHINE = "", MD_CWD = "";
const machinePath = p => MD_MACHINE && p.startsWith("/") && !p.startsWith("/@") && !p.startsWith("/api/") ? `/@${MD_MACHINE}${p}` : p;
/* (markdown.js's file-reference menu calls these; the phone opens no files, so they only say so.) */
function openSide() { toast("Files open on the Mac"); }
function openFile() { toast("Files open on the Mac"); }
function openExternal() {}
function menu() {}
const openInLabel = () => "";

/* ===================================================================== formatting */
const basename = p => String(p || "").replace(/\/+$/, "").split("/").pop();
const dirname = p => String(p || "").replace(/\/[^/]*$/, "") || "/";
const relTo = (root, p) => root && p.startsWith(root + "/") ? p.slice(root.length + 1) : p;
const machineOf = p => (/^\/@([a-z0-9][a-z0-9_-]*)(?=\/|$)/.exec(p || "") || [])[1] || "";  // another machine's path: /@NAME/…
const bytes = n => n == null ? "" : n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB" : (n / 1048576).toFixed(1) + " MB";
const pad2 = n => String(n).padStart(2, "0");
const hm24 = t => { const d = new Date(t * 1000); return pad2(d.getHours()) + ":" + pad2(d.getMinutes()); };
function dayStart(n = 0) { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n).getTime() / 1000; }
function dayLabel(t) { const d = new Date(t * 1000); return d.toLocaleDateString("en-US", { weekday: "short" }) + " " + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()); }
const tilde = p => {
  const m = /^\/@([a-z0-9][a-z0-9_-]*)(\/.*)?$/.exec(p || "");
  if (m) return `${m[1]}:${(m[2] || "/").replace(/^\/(?:home\/[^/]+|Users\/[^/]+|root)(?=\/|$)/, "~")}`;
  return (p || "").replace(/^\/(?:private\/)?(?:Users\/[^/]+)/, "~");
};
const oneLine = s => String(s || "").replace(/\s+/g, " ").trim();
const plain = t => oneLine(String(t || "").replace(/```[\s\S]*?(```|$)/g, " ").replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[#>*_`|~]+/g, " "));
function ago(t) {
  if (!t) return "";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "now";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h";
  return Math.floor(s / 86400) + "d";
}
function dur(s) {
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}
const dayTime = t => t ? new Date(t * 1000).toLocaleString([], { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
function modelName(m) {  // "claude-opus-5-5-high" → "Claude Opus 5.5 High" (as the desktop says it)
  if (!m) return "";
  const k = m.replace(/\[1m\]$/i, "").replace(/-\d{8}$/, "").match(/^claude-([a-z]+)-(\d+)(?:-(\d+))?(?:-([a-z]+))?$/i);
  const cap = w => w[0].toUpperCase() + w.slice(1);
  if (k) return `Claude ${cap(k[1])} ${k[2]}${k[3] ? "." + k[3] : ""}${k[4] ? " " + cap(k[4]) : ""}`;
  return /^(opus|sonnet|haiku)$/i.test(m) ? cap(m) : m;
}

/* ===================================================================== icons (ui.md §5: one line set, 16×16, stroke 1.5) */
const ICONS = {
  plus: '<path d="M8 3v10M3 8h10"/>',
  quote: '<path d="M3 9.5h3v3.5H2.5V9c0-2.5 1-4 3-5M9.5 9.5h3v3.5H9V9c0-2.5 1-4 3-5"/>',
  x: '<path d="M4 4l8 8M12 4l-8 8"/>',
  up: '<path d="M8 13V3M3.5 7.5L8 3l4.5 4.5"/>',
  down: '<path d="M8 3v10M3.5 8.5L8 13l4.5-4.5"/>',
  back: '<path d="M10 3L5 8l5 5"/>',
  chev: '<path d="M6 4l4 4-4 4"/>',
  chevd: '<path d="M4 6l4 4 4-4"/>',
  caretr: '<path d="M6.5 5v6L10 8z" fill="currentColor" stroke="none"/>',
  more: '<circle cx="3.5" cy="8" r=".9" fill="currentColor"/><circle cx="8" cy="8" r=".9" fill="currentColor"/><circle cx="12.5" cy="8" r=".9" fill="currentColor"/>',
  check: '<path d="M3 8.5l3 3 7-7"/>',
  pause: '<path d="M6 4v8M10 4v8"/>',
  ban: '<circle cx="8" cy="8" r="5.5"/><path d="M4.2 11.8l7.6-7.6"/>',
  copy: '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5V3.5a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2"/>',
  link: '<path d="M6.5 9.5a3 3 0 0 0 4.2 0l2-2a3 3 0 0 0-4.2-4.2l-.8.8"/><path d="M9.5 6.5a3 3 0 0 0-4.2 0l-2 2a3 3 0 0 0 4.2 4.2l.8-.8"/>',
  gear: '<circle cx="8" cy="8" r="2.3"/><path d="M8 1.6v1.8M8 12.6v1.8M1.6 8h1.8M12.6 8h1.8M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3M3.5 12.5l1.3-1.3M11.2 4.8l1.3-1.3"/>',
  folder: '<path d="M1.5 4a1 1 0 0 1 1-1h3.5l1.5 1.5h6a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z"/>',
  server: '<rect x="2" y="2.5" width="12" height="4.5" rx="1.2"/><rect x="2" y="9" width="12" height="4.5" rx="1.2"/><path d="M4.6 4.75h.01M4.6 11.25h.01"/>',
  resend: '<path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9"/><path d="M2.5 2.5v3h3"/>',
  undo: '<path d="M5.5 3L2.5 6l3 3"/><path d="M2.5 6H10a3.5 3.5 0 0 1 0 7H7"/>',
  plug: '<path d="M6 2v3.5M10 2v3.5M4 5.5h8v2.5a4 4 0 0 1-8 0zM8 12v2.5"/>',
  zap: '<path d="M9 1.5L3.5 9H8l-1 5.5L12.5 7H8z"/>',
  star: '<path d="M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6z"/>',
  stop: '<rect x="4.5" y="4.5" width="7" height="7" rx="1.2" fill="currentColor" stroke="none"/>',
  image: '<rect x="2" y="3" width="12" height="10" rx="1.6"/><circle cx="5.8" cy="6.4" r="1.1"/><path d="M2.5 11.5l3.4-3.2 2.6 2.4 2-1.8 3 2.6"/>',
  fork: '<circle cx="4.5" cy="3.5" r="1.5"/><circle cx="11.5" cy="3.5" r="1.5"/><circle cx="8" cy="12.5" r="1.5"/><path d="M4.5 5v1a2 2 0 0 0 2 2h3a2 2 0 0 0 2-2V5M8 8v3"/>',
  play: '<path d="M5.5 3.5l6.5 4.5-6.5 4.5z"/>',
  refresh: '<path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9"/><path d="M13.5 2.5v3h-3"/>',
  monitor: '<rect x="1.5" y="2.5" width="13" height="8.5" rx="1.2"/><path d="M5.5 14h5M8 11v3"/>',
  info: '<circle cx="8" cy="8" r="6"/><path d="M8 7.3V11M8 5h.01"/>',
  file: '<path d="M4 1.5h5l3.5 3.5v9a.5.5 0 0 1-.5.5H4a.5.5 0 0 1-.5-.5V2a.5.5 0 0 1 .5-.5z"/><path d="M9 1.5V5h3.5"/>',
  calendar: '<rect x="2" y="3" width="12" height="11" rx="1.5"/><path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3"/>',
  clock: '<circle cx="8" cy="8" r="5.8"/><path d="M8 4.8V8l2.2 1.6"/>',
  bell: '<path d="M4 11.5V7a4 4 0 0 1 8 0v4.5l1 1H3z"/><path d="M6.5 14a1.5 1.5 0 0 0 3 0"/>',
  pencil: '<path d="M10.5 2.5l3 3L6 13H3v-3z"/><path d="M9 4l3 3"/>',
  external: '<path d="M9 2.5h4.5V7"/><path d="M13.5 2.5L7 9"/><path d="M12 9.5v3a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3"/>',
  search: '<circle cx="7" cy="7" r="4.5"/><path d="M10.4 10.4L14 14"/>',
  home: '<path d="M2.5 7.2L8 2.8l5.5 4.4V13a.5.5 0 0 1-.5.5H3a.5.5 0 0 1-.5-.5z"/><path d="M6.5 13.5V10h3v3.5"/>',
  list: '<path d="M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01"/>',
  /* the tab bar's: Sessions, Files (folder), Notes, Misc */
  chat: '<path d="M2.5 4a1.5 1.5 0 0 1 1.5-1.5h8A1.5 1.5 0 0 1 13.5 4v5.5A1.5 1.5 0 0 1 12 11H7.2L4.5 13.5V11H4a1.5 1.5 0 0 1-1.5-1.5z"/>',
  note: '<path d="M4 1.5h8a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1z"/><path d="M5.5 5h5M5.5 8h5M5.5 11h3"/>',
  /* a voice call (m-voice.js): the microphone (the desktop's), muted, the keyboard (type instead), the handset down */
  mic: '<rect x="6" y="1.75" width="4" height="7.75" rx="2"/><path d="M3.75 7.75a4.25 4.25 0 0 0 8.5 0M8 12v2.25"/>',
  micoff: '<rect x="6" y="1.75" width="4" height="7.75" rx="2"/><path d="M3.75 7.75a4.25 4.25 0 0 0 8.5 0M8 12v2.25M2.25 2.25l11.5 11.5"/>',
  keyboard: '<rect x="1.75" y="4" width="12.5" height="8" rx="1.5"/><path d="M4.5 6.6h.01M7 6.6h.01M9.5 6.6h.01M12 6.6h.01M5 9.4h6"/>',
  hangup: '<path d="M1.6 9.1c3.5-3.4 9.3-3.4 12.8 0 .4.4.4 1 0 1.4l-1.2 1.2c-.3.3-.8.4-1.2.1l-1.7-1.1c-.3-.2-.5-.5-.5-.9V8.6a8 8 0 0 0-3.6 0v1.2c0 .4-.2.7-.5.9l-1.7 1.1c-.4.3-.9.2-1.2-.1l-1.2-1.2c-.4-.4-.4-1 0-1.4z" fill="currentColor" stroke="none"/>',
  grid: '<rect x="2" y="2" width="5" height="5" rx="1.3"/><rect x="9" y="2" width="5" height="5" rx="1.3"/><rect x="2" y="9" width="5" height="5" rx="1.3"/><rect x="9" y="9" width="5" height="5" rx="1.3"/>',
};
function I(name, size = 16, cls = "") {
  return `<svg class="i${cls ? " " + cls : ""}" width="${size}" height="${size}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

/* Moving now: a request running, or a subagent still at work after its request ended (the server's `agents`). */
const isMoving = s => s.state === "working" || s.agents > 0;
const agentsWord = n => `${n} subagent${n === 1 ? "" : "s"}`;

/* ===================================================================== a session's status mark (ui.md "Status marks")
   The desktop's set, drawn: working a spinning --run ring; needs you a filled --run "!"; stopped a hollow ring; not
   ready ✕; else how its last request ended (✓ ✕ pause stopped-circle); ready with nothing asked a small dot. */
const MARK_SVG = {
  run: '<circle cx="8" cy="8" r="5.25" stroke="currentColor" stroke-width="1.75" fill="none" stroke-linecap="round" stroke-dasharray="24 9"/>',
  attn: '<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M8 4.4v4.5" stroke="var(--on-run)" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="11.5" r="1.05" fill="var(--on-run)"/>',
  ok: '<path d="M3 8.5l3 3 7-7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  err: '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>',
  warn: '<path d="M6 4.5v7M10 4.5v7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>',
  quiet: '<circle cx="8" cy="8" r="5.25" stroke="currentColor" stroke-width="1.6" fill="none"/><path d="M4.4 11.6l7.2-7.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  stopped: '<circle cx="8" cy="8" r="3.5" stroke="currentColor" stroke-width="1.5" fill="none"/>',
  idle: '<circle cx="8" cy="8" r="2.5" fill="currentColor"/>',
};
function sessionStatus(s) {  // {cls, word}: what the session is doing, or how its last request ended
  if (s.attention) return { cls: "attn", word: "Needs you" };
  if (s.state === "working") return { cls: "run", word: "Working" };
  if (s.state === "starting") return { cls: "run", word: "Starting" };
  if (s.state === "not ready") return { cls: "err", word: "Not ready" };
  if (s.state === "stopped" || s.closed) return { cls: "stopped", word: "Stopped" };
  if (s.agents > 0) return { cls: "run", word: "Subagents working" };  // (a background subagent runs on after its request)
  const l = s.last || {};
  return ({ completed: { cls: "ok", word: "Done" }, error: { cls: "err", word: "Failed" },
            paused: { cls: "warn", word: "Paused" }, cancelled: { cls: "quiet", word: "Cancelled" } })[l.state] || { cls: "idle", word: "Ready" };
}
const markSvg = (cls, size = 14) => `<svg class="mk-${cls}" width="${size}" height="${size}" viewBox="0 0 16 16" aria-hidden="true">${MARK_SVG[cls]}</svg>`;
const stateMark = (s, size = 14) => { const st = sessionStatus(s); return `<span class="si ${st.cls}" title="${esc(st.word)}">${markSvg(st.cls, size)}</span>`; };
const machineTag = s => s && s.remote ? `<span class="mtag${machineUp(s.remote) ? "" : " off"}">${esc(s.remote)}</span>` : "";
/* A session's title in lists: its name, and · a short key when another open session has the same name. */
function sessionTitle(s) {
  const twin = [...S.sessions.values()].some(x => x.id !== s.id && x.name === s.name);
  return twin ? `${s.name} · ${s.id.replace(/^.*~/, "").slice(0, 4)}` : s.name;
}

/* ===================================================================== the server */
class NotPaired extends Error {}
/* The token: from the pairing link (?token=, also the home-screen app's start_url), kept for the next start. Every
   request carries it (the header; the event stream and images in their URL), so nothing waits on the cookie, which
   the home-screen app, with its own storage, may not have yet. */
const TOKEN = (() => {
  const q = new URLSearchParams(location.search).get("token") || "";
  try { if (q) localStorage.setItem("orbit.m.token", q); return q || localStorage.getItem("orbit.m.token") || ""; } catch { return q; }
})();
// (this server's own addresses only, "/…": the token never goes to another site)
const withToken = url => TOKEN && /^\/(?!\/)/.test(url) ? url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN) : url;
async function api(method, path, body, opts = {}) {  // (opts.keepalive: a request that outlives the page, the voice's hang-up)
  const headers = { "Content-Type": "application/json", ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}) };
  const r = await fetch(path, { method, credentials: "same-origin", headers, body: body ? JSON.stringify(body) : undefined, keepalive: !!opts.keepalive });
  if (r.status === 401) { showUnpaired(); throw new NotPaired("not paired"); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.error || `HTTP ${r.status}`); e.status = r.status; e.data = d; throw e; }  // (a save's 409 carries the disk's version)
  return d;
}

/* ===================================================================== toast (ui.md: bottom-centre, --ink, white) */
function toast(msg, ms = 2400) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.tm);
  toast.tm = setTimeout(() => t.classList.remove("show"), ms);
}
/* Copy: the clipboard API needs a secure context (HTTPS, or this machine); plain HTTP on a tailnet falls back. */
async function copyText(s, what = "Copied") {
  try { await navigator.clipboard.writeText(s); toast(what); return; } catch {}
  const ta = document.createElement("textarea");
  ta.value = s; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;top:-100px;opacity:0;font-size:16px";
  document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, s.length);
  let ok = false; try { ok = document.execCommand("copy"); } catch {}
  ta.remove();
  toast(ok ? what : "Copy failed");
}
