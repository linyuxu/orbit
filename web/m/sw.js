"use strict";
/* ===================================================================== Orbit for iPhone: the service worker
   The app shell (web/m/ and the desktop's shared files it loads) comes from the server when it answers within 2 s
   (network first: an update shows at the next start, not the one after; the server is the user's own machine), and
   from the cache when it does not (offline, or the tailnet away). /api/* is never cached (live data, the event
   stream, uploads), nor the manifest (it carries the token). */
const CACHE = "orbit-m-39";
const PUSH_BOX = "orbit-push";  // (a tapped notification's session, for the page to open: kept across versions)
const WAIT = 2000;
const SHELL = [
  "/m/", "/m/m.css", "/m/m-core.js", "/m/m-cards.js", "/m/m-list.js", "/m/m-conv.js", "/m/m-voice.js", "/m/m-sheets.js",
  "/m/m-tabs.js", "/m/m-files.js", "/m/m-side.js", "/m/m-notes.js", "/m/m-misc.js", "/m/m-push.js", "/m/m-pviews.js", "/m/m.js",
  "/m/icon-192.png", "/m/apple-touch-icon.png",
  "/styles/base.css", "/src/scriptguard.js", "/src/markdown-base.js", "/src/markdown.js", "/src/brands.js", "/src/fileicons.js", "/src/voice-core.js", "/src/cardpatch.js",
  "/vendor/markdown-it.min.js", "/vendor/highlight.min.js", "/vendor/katex.min.js", "/vendor/katex.min.css",
  "/vendor/hljs-light.min.css", "/vendor/hljs-dark.min.css", "/vendor/cm.js",
];
const SHARED = /^\/(m\/(?!manifest)|styles\/|src\/|vendor\/)/;  // what the shell may keep (KaTeX's fonts come in as used)

self.addEventListener("install", e => {
  // One by one: a file that fails (not paired yet: 401) must not stop the rest.
  e.waitUntil(caches.open(CACHE).then(c => Promise.all(SHELL.map(u => fetch(u, { credentials: "same-origin" })
    .then(r => r.ok && !r.redirected ? c.put(u, r) : null).catch(() => null)))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE && k !== PUSH_BOX).map(k => caches.delete(k)))).then(() => self.clients.claim()).then(() => pushNote("activated")));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // The page itself: keyed as /m/ (its #s/… and ?session= are the app's).
  const page = req.mode === "navigate" && (url.pathname === "/m/" || url.pathname === "/m");
  if (!page && !SHARED.test(url.pathname)) return;
  const key = page ? "/m/" : url.pathname + url.search;
  // ?token= (the home-screen app starts at its manifest's start_url, the pairing link too): the server answers it
  // with the cookie and a redirect. Unknown here, that redirect is the page's own navigation; known, the shell
  // shows at once and the cookie is renewed behind it (the page drops ?token from its address).
  e.respondWith(caches.open(CACHE).then(async c => {
    // (A navigation's own request: a redirect from the server is followed by the browser, never handed back here.
    // The page with ?token= also sets the cookie: the server's answer, when there is one, is the one to use.)
    const fresh = fetch(req).then(r => {
      if (r.ok && !r.redirected && r.type === "basic") c.put(key, r.clone());
      return r;
    });
    const late = new Promise(ok => setTimeout(ok, WAIT, null));
    const r = await Promise.race([fresh.catch(() => null), late]);
    if (r && (r.ok || r.status === 401)) return r;  // (401: not paired; the page says so)
    const hit = await c.match(key);
    if (hit) { e.waitUntil(fresh.catch(() => null)); return hit; }  // slow or away: the cached shell, refreshed behind it
    return fresh;
  }));
});

/* Push notifications (orbit/services/push.py sends; Misc → Notifications subscribes): {title, body, sid, url, tag}.
   A tap opens the notification's session. iOS may hand a tap no window (then openWindow only brings the app to the
   front, its address unchanged) and may hold a message back, so the session is written first to a cache entry
   (PUSH_BOX /m/__open {sid, at}) the page takes whenever it shows or starts (m-push.js); the message, focus and
   openWindow follow. Each tap is reported to the server's push log (how it went: the device's own record). */
const pushNote = (how, sid = "", windows = 0) => fetch("/api/push/tap", { method: "POST", credentials: "same-origin",
  headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sid, how, windows, v: CACHE }) }).catch(() => {});
const pushUrl = d => {
  const sid = typeof d.sid === "string" ? d.sid : "";
  return "/m/" + (sid ? "#s/" + encodeURIComponent(sid) : "");
};
/* A declarative message ({web_push: 8030, notification: {title, body, navigate}}, push.py): where the browser takes it
   itself (iOS 18.4+: the event carries its proposed notification), it shows it and opens navigate on a tap, so nothing
   is shown here (a notification shown here would replace it, and iOS gives its tap to no one). Elsewhere it is shown
   from the same message, its session read from navigate's ?session=. */
const fromDeclarative = d => {
  const n = d.notification && typeof d.notification === "object" ? d.notification : {};
  let sid = "";
  try { const u = new URL(String(n.navigate || ""), location.origin); if (u.origin === location.origin) sid = u.searchParams.get("session") || ""; } catch {}
  return { title: n.title, body: n.body, sid, tag: sid || "orbit" };
};
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data ? e.data.text() : "" }; }
  if (!d || typeof d !== "object") d = {};
  if (d.web_push === 8030) {
    if (e.notification && typeof e.notification === "object") { e.waitUntil(pushNote("received-declarative", fromDeclarative(d).sid)); return; }
    d = fromDeclarative(d);
  }
  const sid = typeof d.sid === "string" ? d.sid : "";
  e.waitUntil(Promise.all([self.registration.showNotification(String(d.title || "Orbit"), {
    body: String(d.body || ""), tag: String(d.tag || sid || "orbit"), data: { sid, url: pushUrl(d) }, icon: "/m/icon-192.png" }),
    pushNote("received", sid)]));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const d = e.notification.data || {}, sid = typeof d.sid === "string" ? d.sid : "", url = pushUrl(d);
  e.waitUntil((async () => {
    pushNote("tapped", sid);
    try { const c = await caches.open(PUSH_BOX); await c.put("/m/__open", new Response(JSON.stringify({ sid, at: Date.now() }))); } catch {}
    const ws = await self.clients.matchAll({ type: "window", includeUncontrolled: true }).catch(() => []);
    const w = ws.find(x => new URL(x.url).origin === location.origin && new URL(x.url).pathname.startsWith("/m"));
    let how = "open";
    if (w) {
      how = "focus";
      try { w.postMessage({ t: "open", sid }); } catch {}
      try { await w.focus(); } catch { how = "focus-failed"; }
    } else {
      try { await self.clients.openWindow(url); } catch { how = "open-failed"; }
    }
    await pushNote(how, sid, ws.length);
  })());
});
