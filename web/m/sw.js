"use strict";
/* ===================================================================== Orbit for iPhone: the service worker
   The app shell (web/m/ and the desktop's shared files it loads) comes from the server when it answers within 2 s
   (network first: an update shows at the next start, not the one after; the server is the user's own machine), and
   from the cache when it does not (offline, or the tailnet away). /api/* is never cached (live data, the event
   stream, uploads), nor the manifest (it carries the token). */
const CACHE = "orbit-m-6";
const WAIT = 2000;
const SHELL = [
  "/m/", "/m/m.css", "/m/m-core.js", "/m/m-cards.js", "/m/m-list.js", "/m/m-conv.js", "/m/m-sheets.js",
  "/m/m-tabs.js", "/m/m-files.js", "/m/m-notes.js", "/m/m-misc.js", "/m/m-pviews.js", "/m/m.js",
  "/m/icon-192.png", "/m/apple-touch-icon.png",
  "/styles/base.css", "/src/markdown-base.js", "/src/markdown.js", "/src/brands.js", "/src/fileicons.js",
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
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
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

/* Next phase: push notifications ("#12 finished"). The server sends {title, body, sid}; a tap opens that session.
   Nothing subscribes yet, so these never fire. */
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch {}
  e.waitUntil(self.registration.showNotification(d.title || "Orbit", { body: d.body || "", tag: d.sid || "orbit", data: { sid: d.sid || "" }, icon: "/m/icon-192.png" }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const sid = (e.notification.data || {}).sid, url = "/m/" + (sid ? "#s/" + encodeURIComponent(sid) : "");
  e.waitUntil(self.clients.matchAll({ type: "window" }).then(ws => {
    const w = ws.find(x => new URL(x.url).pathname.startsWith("/m"));
    return w ? w.navigate(url).then(x => (x || w).focus()) : self.clients.openWindow(url);
  }));
});
