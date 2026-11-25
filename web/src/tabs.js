"use strict";
/* ===================================================================== tabs and groups */
function recordVisit(key) {
  if (!key || EMBED) return;
  S.recent = [{ id: key, t: Date.now() / 1000 }, ...S.recent.filter(r => r.id !== key)].slice(0, 80);
  LS.set("recent", S.recent);
}
/* Focus belongs to the tab (interaction.md principle 8): leaving a tab remembers whether the keyboard was in its main
   pane or its side pane; coming back puts it there again, whatever switched the tab. */
const mainFocused = () => { const a = document.activeElement; return !!a && a !== document.body && $("#pane").contains(a); };
let fromList = false;  // a list row opened this tab on purpose (⏎ in the left panel): the keyboard follows it
function showCurrent(focus) {
  const key = tabKey();
  const was = sideFocused() ? "side" : mainFocused() ? "main" : null;
  if (S.shownKey && S.shownKey !== key && was) S.tabFocus[S.shownKey] = was;
  S.shownKey = key;
  closeFind(false);
  S.atEnd = !(typeof SCROLL !== "undefined" && S.current && SCROLL[S.current]);  // each session where it was left (the end, or a place)
  if (S.current && !S.file) clearNotes(S.current);
  recordVisit(tabKey());
  showMain();
  renderAll();
  followPanel();
  saveUi();
  pollReview();
  shellFollow();
  renderSide();
  if (focus) placeFocus(key, was);
  else if (was === "side" && !sideFocused()) focusMain();  // the side tab we were in is gone with its main tab
}
function placeFocus(key, was) {
  if (modal || (panelFocused() && !fromList)) return;  // lists and dialogs that have the keyboard keep it
  const want = S.tabFocus[key] || (was === "side" ? "side" : "main");  // a tab not visited yet: where the keyboard is now
  if (want === "side" && S.side) focusSide(); else focusMain();
}
/* Show the session view, the file view or ± Review in the main pane. */
function showMain() {
  const isX = xOn(), isP = isPluginTab(S.file), isFile = !!S.file && !isX && !isP;
  if (xKind() !== "home" && typeof homeUnshown === "function") homeUnshown();
  $("#sessview").hidden = !!S.file || EMBED_REVIEW || isX;
  $("#fileview").hidden = !isFile;
  $("#pview").hidden = !isP;
  $("#reviewview").hidden = !EMBED_REVIEW;
  $("#xview").hidden = !isX;
  if (!isX && XN.editing) leaveCalNote(false);  // left the Calendar tab while writing its note: saved
  if (isX) { if (EMBED_X) renderX(); else { renderStatus(); loadX(); } return; }
  if (isP) { showPluginTab(S.file); return; }
  if (isFile) { showFile(S.file); return; }
  if (EMBED_REVIEW) return;
  input.value = S.drafts[S.current] || ""; autosize(); updateSend(); renderEditing(); closeMention();
  if (!S.current) renderConv();
  else if (!S.cards.has(S.current)) { $("#cards").innerHTML = ""; loadCards(S.current); }
  else renderConv();
}
/* ⌘- ⌘=: back and forward through visited tabs. An entry is a tab in its group ({k: "s:<id>" | "f:<path>", g}), so
   back from another group goes to that group's tab (a file tab is one group's: the same path in the group in front
   would be another tab). A tab is kept once, at its last visit; HIST_MAX entries. */
const HIST_MAX = 200;
function record(key, gid = S.group) {
  if (!key) return;
  S.hist = S.hist.slice(0, S.histPos + 1).filter(e => !(e.k === key && e.g === gid));
  S.hist.push({ k: key, g: gid });
  if (S.hist.length > HIST_MAX) S.hist = S.hist.slice(-HIST_MAX);
  S.histPos = S.hist.length - 1;
}
function switchTo(sid, { focus = true, rec = true, group } = {}) {
  const s = S.sessions.get(sid);
  if (!s) return;
  if (s.fleet && !EMBED) { openSide({ kind: "session", sid }, { focus }); return; }  // a Fleet helper has no tab
  saveDraft();
  if (group) S.group = group;
  else if (!inView(s)) S.group = s.group;
  S.current = sid;
  S.file = null;
  S.lastTab[S.group] = "s:" + sid;
  if (rec) record("s:" + sid);
  showCurrent(focus);
}
function switchGroup(gid, { focus = true, quiet = false } = {}) {
  if (gid !== FAV && !S.groups.some(g => g.id === gid)) return;
  saveDraft();
  S.group = gid;
  const want = String(S.lastTab[gid] || "");
  const ids = tabIds();
  S.file = null;
  if (want.startsWith("f:") && (want === "f:" + HOME || fileTabs().some(t => t.path === want.slice(2)))) S.file = want.slice(2);
  const sid = want.startsWith("s:") ? want.slice(2) : want.startsWith("f:") ? null : want;
  if (sid && ids.includes(sid)) S.current = sid;
  else if (!ids.includes(S.current)) S.current = ids[0] || null;
  if (!quiet && !S.left.shown) toast(groupPath(gid));
  record(tabKey());  // (the tab it lands on: ⌘- comes back to it)
  showCurrent(focus);
}
function groupByIndex(n) {
  if (n === 1) return switchGroup(FAV);
  const g = topGroups()[n - 2];  // (the top groups: a subgroup is reached in its parent)
  if (g && g.id === S.group && S.file !== HOME) { openHome(g.id); return; }  // (its key again: its home)
  if (g) switchGroup(g.id); else toast(`No group ${n - 1}: ⌘\` → Sessions to make one`);
}
/* The tab row: sessions and files are tabs alike, in one order per group (S.tabOrder; ⌘⌃H ⌘⌃L move any tab past
   any other). A tab never arranged yet goes after those that were: sessions in the server's order, then files. */
function allTabKeys(gid = S.group) {
  const keys = [...tabIds(gid).map(id => "s:" + id), ...fileTabs(gid).map(t => "f:" + t.path)];
  const ord = S.tabOrder[gid] || [];
  const pos = new Map(ord.map((k, i) => [k, i]));
  return keys.map((k, i) => [k, pos.has(k) ? pos.get(k) : ord.length + i]).sort((a, b) => a[1] - b[1]).map(x => x[0]);
}
function goKey(key, opts = {}) {
  if (key.startsWith("s:")) switchTo(key.slice(2), opts);
  else if (key.startsWith("f:")) openFile(key.slice(2), { ...opts, keepPreview: true });
}
function cycleTab(d) {
  const keys = allTabKeys();
  if (keys.length < 2) return;
  const i = keys.indexOf(tabKey());
  goKey(keys[(i + d + keys.length) % keys.length]);
}
/* Whether a history entry's tab is still there: a session not closed (shown in its group, or anywhere), a file tab
   still open in its group. Closed ones are skipped. */
function histOk(e) {
  if (!e || typeof e !== "object") return false;
  if (e.k.startsWith("s:")) { const s = S.sessions.get(e.k.slice(2)); return !!s && !s.closed && !s.fleet; }
  return e.k.startsWith("f:") && (e.g === FAV || S.groups.some(g => g.id === e.g)) && (e.k === "f:" + HOME || fileTabs(e.g).some(t => t.path === e.k.slice(2)));
}
function histGo(d) {
  let p = S.histPos + d;
  while (p >= 0 && p < S.hist.length && !histOk(S.hist[p])) p += d;
  if (p < 0 || p >= S.hist.length) return;
  S.histPos = p;
  const e = S.hist[p];
  if (e.k.startsWith("s:")) {
    const sid = e.k.slice(2);
    switchTo(sid, { rec: false, group: tabIds(e.g).includes(sid) ? e.g : undefined });  // (in its group, if it is still there)
  } else if (e.k === "f:" + HOME) openHome(e.g, { rec: false });
  else {
    if (e.g !== S.group) { saveDraft(); S.group = e.g; }  // that group's file tab, not one opened in the group in front
    openFile(e.k.slice(2), { rec: false, keepPreview: true });
  }
}
async function moveSession(sid, d) {
  const ids = tabIds(), i = ids.indexOf(sid), j = i + d;
  if (i < 0 || j < 0 || j >= ids.length) return;
  const all = [...S.sessions.keys()];
  let before;
  if (d < 0) before = ids[j];
  else { const k = all.indexOf(ids[j]); before = all[k + 1] || null; }
  const ord = S.tabOrder[S.group];  // the tab row, if arranged, swaps the same two tabs
  if (ord) {
    const a = ord.indexOf("s:" + sid), b = ord.indexOf("s:" + ids[j]);
    if (a >= 0 && b >= 0) { [ord[a], ord[b]] = [ord[b], ord[a]]; saveUi(); renderTabs(); }
  }
  try { await api("POST", `/api/sessions/${sid}/move`, { before }); } catch (e) { toast(e.message); }
}
function moveCurrentTab(d) {
  const keys = allTabKeys(), k = tabKey(), i = keys.indexOf(k), j = i + d;
  if (i < 0 || j < 0 || j >= keys.length) return;
  [keys[i], keys[j]] = [keys[j], keys[i]];
  S.tabOrder[S.group] = keys;
  saveUi(); renderTabs();
  if (k.startsWith("s:")) {  // the sessions' own order (the left panel, other windows) follows: before the next session tab
    const ss = keys.filter(x => x.startsWith("s:")).map(x => x.slice(2));
    const next = ss[ss.indexOf(k.slice(2)) + 1] || null;
    api("POST", `/api/sessions/${k.slice(2)}/move`, { before: next }).catch(e => toast(e.message));
  }
}
async function closeSession(sid = S.current) {
  const s = S.sessions.get(sid);
  if (!s) return;
  // Always asked (⌘W, ×, the menu): a slip of the key must not close a session. ⏎ closes, Esc keeps it.
  const busy = s.state === "working" || s.queued, bg = !busy && s.agents > 0;
  const ok = await confirmDialog({ title: `Close “${s.name}”?`, ok: "Close", danger: busy || bg,
    body: (busy ? "It is working: closing it stops the running request. " : bg ? `${s.agents === 1 ? "A subagent is" : `${s.agents} subagents are`} still working in the background: closing it stops ${s.agents === 1 ? "it" : "them"}. ` : "") + "Its conversation is kept: ⌘⇧T or ⌘O reopens it." });
  if (!ok) return;
  try { await api("POST", `/api/sessions/${sid}/close`); } catch (e) { toast("Close failed: " + e.message); }
}
function removeSession(sid) {
  closeAllSides("s:" + sid);
  const ids = tabIds(), i = ids.indexOf(sid);
  S.sessions.delete(sid); S.cards.delete(sid); clearNotes(sid);
  if (S.current === sid) {
    const rest = tabIds();
    S.current = rest[Math.min(Math.max(i, 0), rest.length - 1)] || null;
    if (!S.file) S.lastTab[S.group] = S.current ? "s:" + S.current : null;
    showCurrent(document.activeElement === input || document.activeElement === document.body);
  } else renderAll();
}
async function closedSessions() { return (await api("GET", "/api/sessions?closed=1")).sessions.filter(s => s.closed); }
async function reopen(sid) {
  try {
    const s = await api("POST", `/api/sessions/${sid}/reopen`);
    S.sessions.set(s.id, s);
    switchTo(s.id);
  } catch (e) { toast("Reopen failed: " + e.message); }
}
async function reopenLast() {
  const closed = (await closedSessions()).sort((a, b) => (b.closed_at || 0) - (a.closed_at || 0));
  const f = S.closedFiles[0];
  if (f && (!closed.length || f.t >= (closed[0].closed_at || 0))) {
    S.closedFiles.shift();
    if (f.gid && f.gid !== S.group && (f.gid === FAV || S.groups.some(g => g.id === f.gid))) { saveDraft(); S.group = f.gid; }  // (back in its own group)
    openFile(f.path, { pinned: true });
    return;
  }
  if (!closed.length) { toast("No closed tab to reopen"); return; }
  reopen(closed[0].id);
}
async function toggleFavorite(sid = S.current) {
  const s = S.sessions.get(sid); if (!s) return;
  try { await api("POST", `/api/sessions/${sid}/update`, { favorite: !s.favorite }); toast(s.favorite ? "Removed from Favorites" : "Added to Favorites"); }
  catch (e) { toast(e.message); }
}
async function moveToGroup(sid, gid) {
  const s = S.sessions.get(sid); if (!s) return;
  if (gid === "__new") {
    const v = await dialog({ title: "New group", fields: [{ key: "name", label: "Name" }], ok: "Create",
      validate: v => v.name.trim() ? null : "Give the group a name." });
    if (!v) return;
    try { gid = (await api("POST", "/api/groups", { name: v.name })).id; } catch (e) { toast(e.message); return; }
  }
  try { await api("POST", `/api/sessions/${sid}/update`, { group: gid }); toast(`Moved to ${groupPath(gid)}`); } catch (e) { toast(e.message); }
}
/* Fork: a new session in the same group continuing this conversation, with all its settings (server-side: every
   harness; also `orbit-agent fork` and /fork-session). It opens in front; the original is untouched. */
async function forkSession(sid = S.current, name = "") {
  const s = S.sessions.get(sid); if (!s) return;
  try {
    const r = await api("POST", `/api/sessions/${sid}/fork`, { name });
    S.sessions.set(r.id, r); switchTo(r.id);
    toast(`Forked “${s.name}” → “${r.name}”: it continues this conversation (${harnessLabel(r.harness)}${r.model ? " · " + modelName(r.model) : ""}${r.effort ? " · " + r.effort : ""})`);
  } catch (e) { toast("Fork failed: " + e.message); }
}
async function sessionAction(sid, action) {
  try {
    const r = await api("POST", `/api/sessions/${sid}/${action}`);
    if (action === "clone") { S.sessions.set(r.id, r); switchTo(r.id); toast("Forked: the copy continues from this conversation"); }
    else if (action === "restart") toast("Restarted");
  } catch (e) { toast(`${action} failed: ${e.message}`); }
}
/* ⌘W: the side tab, else the file tab, else the session tab (in Favorites: off the favorites, nothing closed). */
function closeWhatHasKeyboard() {
  if (sideFocused()) return closeSide();
  if (S.file) return closeFile(S.file);
  return closeOrUnfavorite();
}
/* In the Favorites view a session's ⌘W and × only take it off the favorites: nothing is closed (it stays open in its
   own group), so nothing is asked. Elsewhere they close it, asked first. */
function closeOrUnfavorite(sid = S.current, gid = S.group) {
  const s = S.sessions.get(sid); if (!s) return;
  if (gid === FAV && s.favorite) return unfavorite(sid);
  return closeSession(sid);
}
async function unfavorite(sid) {
  const s = S.sessions.get(sid); if (!s) return;
  try { await api("POST", `/api/sessions/${sid}/update`, { favorite: false }); toast(`Removed “${s.name}” from Favorites: still open in ${groupPath(s.group)} (⌘⌃P stars it again)`); }
  catch (e) { toast(e.message); }
}
$("#tabs").addEventListener("mousedown", e => {
  if (e.button !== 0) return;
  const x = e.target.closest("[data-close]");
  if (x) { e.preventDefault(); closeOrUnfavorite(x.dataset.close); return; }
  const xf = e.target.closest("[data-closefile]");
  if (xf) { e.preventDefault(); closeFile(xf.dataset.closefile); return; }
  if (e.target.closest("#addtab")) { e.preventDefault(); newSession(); return; }
  const t = e.target.closest(".tab");
  if (!t) return;
  e.preventDefault();
  if (t.dataset.sid) switchTo(t.dataset.sid);
  else openFile(t.dataset.file, { keepPreview: true });
});
$("#tabs").addEventListener("dblclick", e => { const t = e.target.closest(".tab[data-file]"); if (t) pinFile(t.dataset.file); });
$("#tabs").addEventListener("contextmenu", e => {
  const t = e.target.closest(".tab");
  if (!t) return;
  e.preventDefault();
  const at = { at: { x: e.clientX, y: e.clientY } };
  if (t.dataset.sid) tabMenu(t.dataset.sid, at); else fileMenu(t.dataset.file, at);
});
$("#grouppill").addEventListener("click", e => {
  if (!e.target.closest(".gcaret")) { openHome(); return; }  // the pill is the group's home tab; its ▾: another group
  menu([{ label: "Favorites", key: "⌘1", run: () => switchGroup(FAV) },
        ...groupMenuItems(gid => switchGroup(gid), { current: S.group }),
        { sep: true }, { label: "New group…", run: () => newGroup() }], { anchor: $("#grouppill") });
});

/* card actions (delegated) */
$("#cards").addEventListener("click", e => {
  const s = cur(); if (!s) return;
  const t = e.target;
  const stepsBtn = t.closest("[data-steps]");
  if (stepsBtn) {  // one group of tool calls: the click is remembered either way (it outlives re-renders)
    const key = stepsBtn.dataset.steps, el = stepsBtn.parentElement, opening = !el.classList.contains("open");
    (opening ? S.closedSteps : S.openSteps).delete(key);
    (opening ? S.openSteps : S.closedSteps).add(key);
    el.classList.toggle("open", opening);
    return;
  }
  if (mdClick(e)) return;  // a code block's Copy, a picture (msglist.js)
  const cr = t.closest("[data-copyreq]");
  if (cr) { const c = (S.cards.get(s.id) || []).find(c => c.id == cr.dataset.copyreq); if (c) copyText(c.text); return; }
  const lr = t.closest("[data-linkreq]");
  if (lr) { copyText(`orbit://session/${s.id}#${lr.dataset.linkreq}`); return; }
  if (t.closest("[data-stop]")) { sessionAction(s.id, "stop"); return; }
  const card = attr => { const b = t.closest(`[${attr}]`); return b && (S.cards.get(s.id) || []).find(c => c.id == b.getAttribute(attr)); };
  const ec = card("data-editreq");
  if (ec) { startEdit(s.id, ec); return; }
  const rc = card("data-resend");
  if (rc) { sendText(s.id, rc.text).then(c => { setAtEnd(true); upsertCard(c); }).catch(e => toast("Resend failed: " + e.message)); return; }
  const wc = card("data-withdraw");
  if (wc) {  // out of the queue and back into the input, to edit (and send again)
    api("POST", `/api/sessions/${s.id}/withdraw`, { cid: wc.id }).then(() => {
      if (s.id === S.current) {
        const v = input.value;
        input.value = v.trim() ? v.replace(/\s+$/, "") + "\n\n" + wc.text : wc.text;
        input.focus(); input.setSelectionRange(input.value.length, input.value.length); updateSend(); saveDraft();
      } else { S.drafts[s.id] = wc.text; LS.set("drafts", S.drafts); }
      toast(`#${wc.id} is back in the input`);
    }).catch(e => toast(e.message));
    return;
  }
  const sn = card("data-steernow");
  if (sn) {
    api("POST", `/api/sessions/${s.id}/withdraw`, { cid: sn.id })
      .then(() => api("POST", `/api/sessions/${s.id}/steer`, { text: sn.text }))
      .then(c => { upsertCard(c); toast(c.steered ? `Steered #${c.id}` : `Sent as #${c.id}`); }).catch(e => toast(e.message));
    return;
  }
  const cont = t.closest("[data-continue]");
  if (cont) { api("POST", `/api/sessions/${s.id}/continue`, { cid: +cont.dataset.continue }).catch(e => toast(e.message)); return; }
  const fr = t.closest("code.fref");
  // A click follows "Open files in" (⌘,): JetBrains or VS Code at the line, else Orbit's side tab (Default app: a click
  // stays in Orbit, ↗ uses the app); ⌘-click: an Orbit tab. Another machine's file always opens in Orbit.
  if (fr && !getSelection().toString()) {
    const oi = (S.settings && S.settings.open_in) || "default";
    openFileRef(fr.dataset.fref, s.cwd, e.metaKey ? "tab" : oi === "jetbrains" || oi === "vscode" ? "editor" : "side");
  }
});
$("#cards").addEventListener("contextmenu", e => {
  const fr = e.target.closest("code.fref"), s = cur();
  if (!fr && s && e.target.closest(".ans") && !getSelection().toString()) {  // an answer: the plugins' actions on it
    const id = +((e.target.closest(".card") || {}).id || "").replace("card-", "");
    const c = (S.cards.get(s.id) || []).find(x => x.id === id);
    if (answerMenu(c, { at: { x: e.clientX, y: e.clientY } })) e.preventDefault();
    return;
  }
  if (!fr || !s) return;
  e.preventDefault();
  fileRefMenu(fr.dataset.fref, s.cwd, { at: { x: e.clientX, y: e.clientY } });
});
/* Links open in the system browser. In the app shell a WebView ignores target=_blank, so the server opens them. */
/* The app shell's bridge: a hidden frame navigates to http://orbit-shell.invalid/<what>?url=…, which the shell cancels
   and handles (open: a link on this Mac; connect: show another orbit server). */
function shellAsk(what, url) {
  const top = window.parent !== window ? window.parent : window;
  const f = top.document.createElement("iframe");
  f.style.display = "none";
  f.src = `http://orbit-shell.invalid/${what}?url=${encodeURIComponent(url)}`;
  top.document.body.append(f);
  setTimeout(() => f.remove(), 2000);
}
function inApp() { try { return !!(window.__ORBIT_APP__ || (window.parent !== window && window.parent.__ORBIT_APP__)); } catch { return false; } }
if (window.__ORBIT_APP__) document.body.classList.add("app");
function openUrl(url) {
  // orbit://session/<id>#<request>: a card in this app (week-note links, copied links)
  const m = /^orbit:\/\/session\/([\w~-]+)(?:#(\d+))?/.exec(url);
  if (m) {
    if (EMBED) { window.parent.postMessage({ orbit: "open-card", sid: m[1], cid: m[2] ? +m[2] : null }, location.origin); return; }
    if (!S.sessions.has(m[1])) { toast("That session is closed: ⌘O lists closed named sessions"); return; }
    if (m[2]) openCard(m[1], +m[2]); else switchTo(m[1]);
    return;
  }
  if (inApp()) shellAsk("open", url);
  else window.open(url, "_blank", "noopener");
}
document.addEventListener("click", e => {
  const a = e.target.closest("a[href]");
  if (!a || !/^(https?|mailto|orbit):/i.test(a.href)) return;
  e.preventDefault();
  openUrl(a.href);
});
$("#jump").innerHTML = I("down", 14) + "Latest";
$("#jump").addEventListener("click", () => { scrollLatest(); });
function openLightbox(src) { const lb = $("#lightbox"); lb.querySelector("img").src = src; lb.classList.add("show"); }
function closeLightbox() { $("#lightbox").classList.remove("show"); }
$("#lightbox").addEventListener("click", closeLightbox);

/* The tab row by mouse: drag a tab along the row (its neighbours slide aside); the same order as ⌘⌃H ⌘⌃L, and a
   session's order follows on the server. */
const tabElKey = el => el.dataset.sid ? "s:" + el.dataset.sid : "f:" + el.dataset.file;
dragSort({
  box: $("#tabs"), item: ".tab", axis: "x", keyOf: tabElKey,
  byKey: k => [...$("#tabs").querySelectorAll(".tab")].find(t => tabElKey(t) === k),
  unit: el => [el],
  peers: el => [...$("#tabs").querySelectorAll(".tab")].filter(t => t !== el).map(t => [t]),
  redraw: () => renderTabs(),
  drop: (key, prev, next) => {
    const keys = allTabKeys().filter(k => k !== key);
    let i = next ? keys.indexOf(tabElKey(next)) : prev ? keys.indexOf(tabElKey(prev)) + 1 : 0;
    if (i < 0) return renderTabs();
    keys.splice(i, 0, key);
    S.tabOrder[S.group] = keys; saveUi(); renderTabs();
    if (key.startsWith("s:")) {
      const ss = keys.filter(x => x.startsWith("s:")), nx = ss[ss.indexOf(key) + 1];
      api("POST", `/api/sessions/${key.slice(2)}/move`, { before: nx ? nx.slice(2) : null }).catch(e => toast(e.message));
    }
  },
});
