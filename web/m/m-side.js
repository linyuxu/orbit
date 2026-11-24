"use strict";
/* ===================================================================== the Side view (a session's side panel)
   The title bar's toggle switches the conversation for what the session's agent put beside it: the pages it showed
   (orbit-agent show, sandboxed as on the Mac) and the files it opened beside (orbit-agent open --side), newest first,
   kept by the server (GET /api/sessions/<id>/side; the Mac's own side tabs live in its browser). Chips pick one;
   × takes it off the list. Another machine's session: not here yet. */
const SIDE = { mode: new Map(), items: new Map(), sel: new Map(), shown: "" };
const sideMode = (sid = S.current) => SIDE.mode.get(sid) || "chat";
const remoteSid = sid => String(sid || "").includes("~");

async function loadSide(sid = S.current) {
  if (!sid) return;
  if (remoteSid(sid)) SIDE.items.set(sid, null);
  else {
    try { SIDE.items.set(sid, (await api("GET", `/api/sessions/${encodeURIComponent(sid)}/side`)).items || []); }
    catch (e) { if (e instanceof NotPaired) return; if (!SIDE.items.has(sid)) SIDE.items.set(sid, []); }
  }
  if (sid === S.current) { renderSideToggle(); if (sideMode() === "side") renderSide(); }
}
function renderSideToggle() {
  const items = SIDE.items.get(S.current), n = items ? items.length : 0, side = sideMode() === "side";
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
/* Opened a session: the view it was left in, its list fetched again. */
function sideOpened(sid) {
  SIDE.shown = "";
  renderSideToggle();
  if (sideMode(sid) === "side") renderSide();
  loadSide(sid);
}

function renderSide() {
  const box = $("#sideview"), items = SIDE.items.get(S.current);
  const chips = box.querySelector(".schips"), body = box.querySelector(".sbody");
  const blank = html => { chips.innerHTML = ""; body.innerHTML = html; SIDE.shown = ""; };
  if (items === null) return blank(emptyBox("On another machine", "The pages and files that machine's agent shows open there, in Orbit on the Mac."));
  if (!items) return blank(loadingHtml());
  if (!items.length) return blank(emptyBox("Nothing beside this session", "The pages its agent shows (orbit-agent show) and the files it opens beside the conversation come here."));
  let sel = SIDE.sel.get(S.current);
  if (!items.some(x => x.id === sel)) sel = items[0].id;
  SIDE.sel.set(S.current, sel);
  chips.innerHTML = items.map(x => `<button class="schip${x.id === sel ? " on" : ""}" data-item="${esc(x.id)}">` +
    `${I(x.kind === "web" ? "external" : "file", 13)}<span class="t">${esc(x.title || "Page")}</span>` +
    `<span class="x" data-drop="${esc(x.id)}" aria-label="Take it off the list">${I("x", 11)}</span></button>`).join("");
  const on = chips.querySelector(".schip.on"); if (on) on.scrollIntoView({ block: "nearest", inline: "nearest" });
  const it = items.find(x => x.id === sel), key = S.current + ":" + sel + ":" + (it.time || 0);
  if (SIDE.shown === key) return;  // (the same page: its frame stays, with its state)
  SIDE.shown = key;
  if (it.kind === "web") {
    body.innerHTML = `<iframe class="sframe" title="${esc(it.title || "Page")}" sandbox="allow-scripts allow-popups allow-forms" src="${esc(withToken(it.url))}"></iframe>`;
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

/* The live stream: a page shown or a file opened beside a session (show / open with side), or its list changed. */
function sideEvent(m) {
  const sid = m.sid; if (!sid) return;
  if (m.t === "open" && !m.side) return;
  if (m.t === "show" || m.t === "open") {
    SIDE.sel.set(sid, m.t === "show" ? m.id : null);  // (the new one is the one shown)
    if (sid === S.current && sideMode() !== "side") toast(`${m.by || "The agent"} put “${m.t === "show" ? m.title : basename(m.path)}” beside: tap ◫ above`);
  }
  if (sid === S.current || SIDE.items.has(sid)) loadSide(sid);
}

$("#cseg").addEventListener("click", e => { const b = e.target.closest("button[data-v]"); if (b) setSideMode(b.dataset.v); });
$("#sideview").addEventListener("click", e => {
  const x = e.target.closest("[data-drop]");
  if (x) {
    e.stopPropagation();
    api("POST", `/api/sessions/${encodeURIComponent(S.current)}/side/remove`, { id: x.dataset.drop })
      .then(d => { SIDE.items.set(S.current, d.items || []); SIDE.shown = ""; renderSideToggle(); renderSide(); })
      .catch(err => { if (!(err instanceof NotPaired)) toast(err.message); });
    return;
  }
  const c = e.target.closest("[data-item]");
  if (c) { SIDE.sel.set(S.current, c.dataset.item); renderSide(); }
});
