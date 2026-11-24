"use strict";
/* ===================================================================== the Side view (a session's side panel)
   The title bar's toggle switches the conversation for the session's side panel: the same tabs every screen shows,
   kept by the server like the conversation (orbit/desk/sides.py; GET/POST /api/sessions/<id>/side, "side" events).
   Chips pick the one shown (on every screen), × closes one. Drawn here: an agent's page (sandboxed, live), a file
   (Markdown drawn, code, an image); ± Review, another session, a plugin's view and the like say what they are, the
   session one opens it. Another machine's session's panel comes through this server (its pages too). Which of the
   two this screen shows, conversation or Side, is this screen's own. */
const SIDE = { mode: new Map(), st: new Map(), shown: "" };
const sideMode = (sid = S.current) => SIDE.mode.get(sid) || "chat";
const sideUrl = sid => `/api/sessions/${encodeURIComponent(sid)}/side`;

function applySide(sid, st) {
  if (!st || !Array.isArray(st.items)) return;
  const had = SIDE.st.get(sid);
  if (had && (st.rev || 0) < (had.rev || 0)) return;
  SIDE.st.set(sid, st);
  if (sid === S.current) { renderSideToggle(); if (sideMode() === "side") renderSide(); }
}
async function loadSide(sid = S.current) {
  if (!sid) return;
  try { applySide(sid, await api("GET", sideUrl(sid))); }
  catch (e) { if (!(e instanceof NotPaired) && !SIDE.st.has(sid)) applySide(sid, { items: [], i: 0, rev: 0 }); }
}
function sideOp(body) {
  const sid = S.current; if (!sid) return;
  api("POST", sideUrl(sid), body).then(st => applySide(sid, st)).catch(err => { if (!(err instanceof NotPaired)) toast(err.message); });
}
function renderSideToggle() {
  const st = SIDE.st.get(S.current), n = st ? st.items.length : 0, side = sideMode() === "side";
  for (const b of $$("#cseg button")) { const on = (b.dataset.v === "side") === side; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); }
  $("#cseg .n").textContent = n ? String(n) : "";
  $("#vconv").classList.toggle("sidemode", side);
}
function setSideMode(v) {
  if (!S.current) return;
  SIDE.mode.set(S.current, v);
  renderSideToggle();
  if (v === "side") { renderSide(); loadSide(); }
  else if (typeof stick === "function") stick();
}
/* Opened a session: the view it was left in, its panel fetched again. */
function sideOpened(sid) {
  SIDE.shown = "";
  renderSideToggle();
  if (sideMode(sid) === "side") renderSide();
  loadSide(sid);
}

const SIDE_WHAT = { review: "± Review", session: "Session", schedules: "Schedules", calendar: "Calendar", pview: "View" };
function sideLabel(x) {
  if (x.kind === "web") return x.title || "Page";
  if (x.kind === "file") return basename(x.path || "") || "File";
  if (x.kind === "session") { const s = S.sessions.get(x.sid); return s ? sessionTitle(s) : "Session"; }
  if (x.kind === "review") return "± Review · " + basename(x.root || "");
  return x.title || SIDE_WHAT[x.kind] || "Side tab";
}
function renderSide() {
  const box = $("#sideview"), st = SIDE.st.get(S.current);
  const chips = box.querySelector(".schips"), body = box.querySelector(".sbody");
  const blank = html => { chips.innerHTML = ""; body.innerHTML = html; SIDE.shown = ""; };
  if (!st) return blank(loadingHtml());
  if (!st.items.length) return blank(emptyBox("Nothing beside this session", "What is open beside it on any screen comes here: the pages its agent shows, the files it opens beside the conversation, ± Review."));
  const i = Math.max(0, Math.min(st.i | 0, st.items.length - 1)), it = st.items[i];
  chips.innerHTML = st.items.map((x, k) => `<button class="schip${k === i ? " on" : ""}" data-item="${esc(x.id)}">` +
    `${I(x.kind === "web" ? "external" : x.kind === "file" ? "file" : x.kind === "session" ? "chat" : "folder", 13)}<span class="t">${esc(sideLabel(x))}</span>` +
    `<span class="x" data-drop="${esc(x.id)}" aria-label="Close it, on every screen">${I("x", 11)}</span></button>`).join("");
  const on = chips.querySelector(".schip.on"); if (on) on.scrollIntoView({ block: "nearest", inline: "nearest" });
  const key = `${S.current}:${it.id}:${it.url || ""}:${it.path || ""}:${it.line || ""}`;
  if (SIDE.shown === key) return;  // (the same page: its frame stays, with its state)
  SIDE.shown = key;
  if (it.kind === "web") {
    body.innerHTML = `<iframe class="sframe" title="${esc(it.title || "Page")}" sandbox="allow-scripts allow-popups allow-forms" src="${esc(withToken(it.url))}"></iframe>`;
    return;
  }
  if (it.kind !== "file") {
    const s = it.kind === "session" && S.sessions.get(it.sid);
    body.innerHTML = emptyBox(sideLabel(it), (it.kind === "review" ? esc(tilde(it.root || "")) + "<br>Its changes show on the Mac." :
      it.kind === "session" ? (s ? `<div class="eacts"><button class="pill" data-open-sid="${esc(it.sid)}">Open the session</button></div>` : "That session is closed.")
      : "It shows on the Mac."));
    return;
  }
  body.innerHTML = loadingHtml();
  api("GET", "/api/file?path=" + encodeURIComponent(it.path)).then(f => {
    if (SIDE.shown !== key) return;
    const path = it.path;
    body.innerHTML = `<div class="sfile"><div class="sfpath">${esc(tilde(path))}${it.line ? `:${it.line}` : ""}</div>` + (
      f.kind === "text" ? (isMdPath(path) ? `<div class="doc md">${renderDoc(f.text, path)}</div>` : codeHtml(f.text, path, LS.get("wrap", true)))
      : f.kind === "image" ? `<div class="imgview"><img src="${esc(rawUrl(path) + "&v=" + f.mtime)}" alt="${esc(basename(path))}"></div>`
      : emptyBox(f.kind === "too large" ? "Too large to show here" : "Binary file", esc(bytes(f.size)))) + `</div>`;
    if (f.kind === "text" && it.line && !isMdPath(path)) {  // (at its line, as on the Mac)
      const ln = body.querySelectorAll(".cl")[it.line - 1];
      if (ln) ln.scrollIntoView({ block: "center" });
    }
  }).catch(e => { if (SIDE.shown === key && !(e instanceof NotPaired)) body.innerHTML = emptyBox(`Cannot open ${basename(it.path)}`, esc(e.message)); });
}

/* The live stream: a session's side panel changed (side); an agent put something beside (show / open with side): a
   toast while the conversation is shown. */
function sideEvent(m) {
  if (m.t === "side") { if (m.sid && (m.sid === S.current || SIDE.st.has(m.sid))) applySide(m.sid, m.side); return; }
  if (m.t === "open" && !m.side) return;
  if (m.sid && m.sid === S.current && sideMode() !== "side")
    toast(`${m.by || "The agent"} put “${m.t === "show" ? m.title : basename(m.path)}” beside: tap ◫ above`);
}

$("#cseg").addEventListener("click", e => { const b = e.target.closest("button[data-v]"); if (b) setSideMode(b.dataset.v); });
$("#sideview").addEventListener("click", e => {
  const x = e.target.closest("[data-drop]");
  if (x) { e.stopPropagation(); sideOp({ op: "close", id: x.dataset.drop }); return; }
  const c = e.target.closest("[data-item]");
  if (c) {
    const st = SIDE.st.get(S.current), k = st ? st.items.findIndex(y => y.id === c.dataset.item) : -1;
    if (k >= 0 && k !== st.i) { applySide(S.current, { ...st, i: k }); sideOp({ op: "select", id: c.dataset.item }); }
    return;
  }
  const o = e.target.closest("[data-open-sid]");
  if (o) { SIDE.mode.set(o.dataset.openSid, "chat"); openSession(o.dataset.openSid); }
});
