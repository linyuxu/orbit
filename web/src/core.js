"use strict";
/* ===================================================================== embedding (side tab) */
const PARAMS = new URLSearchParams(location.search);
const ZOOM_DEFAULT = 1;  // the page zoom ⌘0 goes back to
const EMBED = (() => {  // {kind: "session"|"file"|"review", sid?, path?, root?} or null
  try {
    if (PARAMS.get("embedtab")) return JSON.parse(PARAMS.get("embedtab"));
    if (PARAMS.get("embed")) return { kind: "session", sid: PARAMS.get("embed") };
  } catch {}
  return null;
})();
const EMBED_REVIEW = !!(EMBED && EMBED.kind === "review");
const EMBED_X = !!(EMBED && (EMBED.kind === "schedules" || EMBED.kind === "calendar"));  // list side tabs
/* The Schedules / Calendar view: a side tab's whole page (EMBED_X), or a view tab in the main pane (orbit:calendar),
   drawn in the page itself (no frame: no zoom on zoom, the page's own keys). */
const xKind = () => EMBED_X ? EMBED.kind : (typeof S !== "undefined" && isView(S.file) && VIEWS[S.file] ? VIEWS[S.file].kind : null);
const xOn = () => !!xKind();
const inXView = () => EMBED_X || !!(document.activeElement && document.activeElement.closest && document.activeElement.closest("#xview"));
if (EMBED) document.body.classList.add("embed");

/* ===================================================================== helpers */
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
/* An embedded page reads the main page's state but never writes it (its own keys live under orbit.embed.). */
const LS = {
  key: k => (EMBED ? "orbit.embed." : "orbit.") + k,
  get(k, d) {
    try {
      let v = localStorage.getItem(LS.key(k));
      if (v == null && EMBED) v = localStorage.getItem("orbit." + k);
      return v == null ? d : JSON.parse(v);
    } catch { return d; }
  },
  set(k, v) { try { localStorage.setItem(LS.key(k), JSON.stringify(v)); } catch {} },
};

/* Page errors and slow operations go to the server's page.log (state folder), at most 30 a minute. */
const PLOG = { n: 0, since: 0, last: "" };
function logPage(kind, data = {}) {
  const now = Date.now();
  if (now - PLOG.since > 60000) { PLOG.since = now; PLOG.n = 0; }
  const key = kind + ":" + (data.message || data.op || "");
  if (++PLOG.n > 30 || (key === PLOG.last && kind === "error")) return;
  PLOG.last = key;
  try {
    fetch("/api/log", { method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
      body: JSON.stringify({ kind, page: EMBED ? "side tab" : "main", ...data }) }).catch(() => {});
  } catch {}
}
addEventListener("error", e => logPage("error", { message: String(e.message || e.error || "error"), where: `${e.filename || ""}:${e.lineno || 0}:${e.colno || 0}`,
  stack: String(e.error && e.error.stack || "") }));
addEventListener("unhandledrejection", e => logPage("error", { message: "unhandled rejection: " + String(e.reason && e.reason.message || e.reason),
  stack: String(e.reason && e.reason.stack || "") }));
if (window.CM && CM.hooks) CM.hooks.onError = d => logPage("decoration", d);  // a decoration builder threw: the text stays
const SLOW_MS = 300;

/* One icon set: Lucide-style, 16×16 viewBox, stroke 1.5 (ui.md §5). */
const ICONS = {
  plus: '<path d="M8 3v10M3 8h10"/>',
  x: '<path d="M4 4l8 8M12 4l-8 8"/>',
  up: '<path d="M8 13V3M3.5 7.5L8 3l4.5 4.5"/>',
  down: '<path d="M8 3v10M3.5 8.5L8 13l4.5-4.5"/>',
  more: '<circle cx="3.5" cy="8" r=".6"/><circle cx="8" cy="8" r=".6"/><circle cx="12.5" cy="8" r=".6"/>',
  copy: '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5V3.5a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2"/>',
  link: '<path d="M6.5 9.5a3 3 0 0 0 4.2 0l2-2a3 3 0 0 0-4.2-4.2l-.8.8"/><path d="M9.5 6.5a3 3 0 0 0-4.2 0l-2 2a3 3 0 0 0 4.2 4.2l.8-.8"/>',
  chev: '<path d="M6 4l4 4-4 4"/>',
  bell: '<path d="M4 11.5V7a4 4 0 0 1 8 0v4.5l1 1H3z"/><path d="M6.5 14a1.5 1.5 0 0 0 3 0"/>',
  crosshair: '<circle cx="8" cy="8" r="5"/><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3"/>',
  menu: '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>',
  file: '<path d="M4 1.5h5l3.5 3.5v9a.5.5 0 0 1-.5.5H4a.5.5 0 0 1-.5-.5V2a.5.5 0 0 1 .5-.5z"/><path d="M9 1.5V5h3.5"/>',
  folder: '<path d="M1.5 4a1 1 0 0 1 1-1h3.5l1.5 1.5h6a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z"/>',
  pin: '<path d="M10 2l4 4-2 1-2.5 2.5L9 13 3 7l3.5-.5L9 4z"/><path d="M5.5 10.5L2 14"/>',
  external: '<path d="M9 2.5h4.5V7"/><path d="M13.5 2.5L7 9"/><path d="M12 9.5v3a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3"/>',
  refresh: '<path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9"/><path d="M13.5 2.5v3h-3"/>',
  swap: '<path d="M3 5.5h10l-3-3M13 10.5H3l3 3"/>',
  star: '<path d="M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6z"/>',
  check: '<path d="M3 8.5l3 3 7-7"/>',
  pause: '<path d="M6 4v8M10 4v8"/>',
  play: '<path d="M5.5 3.5l6.5 4.5-6.5 4.5z"/>',
  ban: '<circle cx="8" cy="8" r="5.5"/><path d="M4.2 11.8l7.6-7.6"/>',
  calendar: '<rect x="2" y="3" width="12" height="11" rx="1.5"/><path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3"/>',
  clip: '<path d="M13 7.5l-5.3 5.3a3.2 3.2 0 0 1-4.5-4.5l5.6-5.6a2.1 2.1 0 0 1 3 3L6.3 11.2a1 1 0 0 1-1.5-1.5L10 4.5"/>',
  terminal: '<rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/><path d="M4.5 6l2 2-2 2M8.5 10.5h3"/>',
  power: '<path d="M8 2v5.5"/><path d="M4.6 4.2a5 5 0 1 0 6.8 0"/>',
  pencil: '<path d="M10.5 2.5l3 3L6 13H3v-3z"/><path d="M9 4l3 3"/>',
  resend: '<path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9"/><path d="M2.5 2.5v3h3"/>',
  undo: '<path d="M5.5 3L2.5 6l3 3"/><path d="M2.5 6H10a3.5 3.5 0 0 1 0 7H7"/>',
  caret: '<path d="M5 6.5h6L8 10z" fill="currentColor" stroke="none"/>',  // ▾ of a dropdown
  caretr: '<path d="M6.5 5v6L10 8z" fill="currentColor" stroke="none"/>',  // ▸ of a folded list
  zap: '<path d="M9 1.5L3.5 9H8l-1 5.5L12.5 7H8z"/>',
  gear: '<circle cx="8" cy="8" r="2.3"/><path d="M8 1.6v1.8M8 12.6v1.8M1.6 8h1.8M12.6 8h1.8M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3M3.5 12.5l1.3-1.3M11.2 4.8l1.3-1.3"/>',
  keyboard: '<rect x="1.75" y="4" width="12.5" height="8" rx="1.5"/><path d="M4.5 6.6h.01M7 6.6h.01M9.5 6.6h.01M12 6.6h.01M5 9.4h6"/>',
  type: '<path d="M2 13l3.3-9h.4L9 13M3.2 10h4.6"/><path d="M10.5 8.6c.5-.5 1.1-.8 1.9-.8 1.1 0 1.8.6 1.8 1.7V13M14.2 10.6h-1.9c-1.1 0-1.8.5-1.8 1.3s.6 1.2 1.4 1.2c.9 0 1.6-.4 2.3-1.1"/>',
  clock: '<circle cx="8" cy="8" r="5.8"/><path d="M8 4.8V8l2.2 1.6"/>',
  plug: '<path d="M6 2v3.5M10 2v3.5M4 5.5h8v2.5a4 4 0 0 1-8 0zM8 12v2.5"/>',
  server: '<rect x="2" y="2.5" width="12" height="4.5" rx="1.2"/><rect x="2" y="9" width="12" height="4.5" rx="1.2"/><path d="M4.6 4.75h.01M4.6 11.25h.01"/>',
  info: '<circle cx="8" cy="8" r="6"/><path d="M8 7.3V11M8 5h.01"/>',
  quote: '<path d="M3 9.5h3v3.5H2.5V9c0-2.5 1-4 3-5M9.5 9.5h3v3.5H9V9c0-2.5 1-4 3-5"/>',
  micoff: '<rect x="6" y="1.75" width="4" height="7.75" rx="2"/><path d="M3.75 7.75a4.25 4.25 0 0 0 8.5 0M8 12v2.25M2.5 2.5l11 11"/>',
  panel: '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5"/><path d="M9.75 2.75v10.5"/>',
  mic: '<rect x="6" y="1.75" width="4" height="7.75" rx="2"/><path d="M3.75 7.75a4.25 4.25 0 0 0 8.5 0M8 12v2.25"/>',
};
function I(name, size = 16) {
  return `<svg class="i" width="${size}" height="${size}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
}
function toast(msg, onClick) {
  const t = $("#toast");
  t.textContent = msg;
  t.style.textDecoration = onClick ? "underline" : "none";
  t.style.cursor = onClick ? "pointer" : "default";
  t.onclick = onClick ? () => { t.classList.remove("show"); onClick(); } : null;
  t.classList.add("show");
  clearTimeout(toast.tm);
  toast.tm = setTimeout(() => t.classList.remove("show"), onClick ? 4500 : 2600);
}
async function api(method, path, body, opts = {}) {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined, keepalive: !!opts.keepalive });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.error || `HTTP ${r.status}`); e.status = r.status; e.data = d; throw e; }
  return d;
}
/* Paths for display: ~ for the home folder; another machine's path (/@gpu-box/home/me/x) as gpu-box:~/x. */
const tilde = p => {
  const m = /^\/@([a-z0-9][a-z0-9_-]*)(\/.*)?$/.exec(p || "");
  if (m) return `${m[1]}:${(m[2] || "/").replace(/^\/(?:home\/[^/]+|Users\/[^/]+|root)(?=\/|$)/, "~")}`;
  return (p || "").replace(/^\/Users\/[^/]+/, "~");
};
/* A path written into a request: another machine's stays /@NAME/… (its server makes it that machine's path). */
const agentPath = p => (p || "").startsWith("/@") ? p : tilde(p);
/* While a card of another machine's session renders, the absolute paths it names are that machine's. */
let MD_MACHINE = "", MD_CWD = "";  // the session whose Markdown is being drawn: its machine, its folder
const machinePath = p => MD_MACHINE && p.startsWith("/") && !p.startsWith("/@") && !p.startsWith("/api/") ? `/@${MD_MACHINE}${p}` : p;
const basename = p => String(p || "").replace(/\/+$/, "").split("/").pop();
const dirname = p => String(p || "").replace(/\/[^/]*$/, "") || "/";
const relTo = (root, p) => root && p.startsWith(root + "/") ? p.slice(root.length + 1) : p;
const hhmm = t => t ? new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
const dayTime = t => t ? new Date(t * 1000).toLocaleString([], { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
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
const kfmt = n => n >= 1000 ? Math.round(n / 1000) + "k" : String(n);
const bytes = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
const oneLine = s => String(s || "").replace(/\s+/g, " ").trim();
async function copyText(s) { try { await navigator.clipboard.writeText(s); toast("Copied"); } catch { toast("Copy failed"); } }

/* Fuzzy match: every query character in order; bonus for runs and word starts. -1 = no match. */
function fuzzy(q, text) {
  if (!q) return 0;
  q = q.toLowerCase();
  const t = String(text || "").toLowerCase();
  let i = 0, score = 0, last = -2;
  for (let k = 0; k < t.length && i < q.length; k++) {
    if (t[k] !== q[i]) continue;
    score += 1 + (k === last + 1 ? 3 : 0) + (k === 0 || /[\s/_\-.·]/.test(t[k - 1]) ? 2 : 0);
    last = k; i++;
  }
  return i === q.length ? score - t.length * 0.01 : -1;
}
