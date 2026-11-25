"use strict";
/* ===================================================================== a group's home page (its first tab) */
/* Every group has a home: its first tab (the group pill, left of the tab row), shown by a click on the pill, on the
   group in the left panel, or by the group's ⌘ digit pressed again. On it, widgets on a grid (orbit/desk/groups.py):
   its sessions as cards, its subgroups (a click goes into one), its other tabs, the schedules that wake its
   sessions, what happened lately, what was pinned (by you, or an agent: `orbit-agent pin`), and Markdown notes of your
   own (files in the vault: Groups/<its path>/…; ⏎ or Edit writes in place, Esc is done; their checkboxes tick here).
   A widget moves by its handle (drag), takes a third, a half, two thirds or the row (its ⋯ menu), or hides. A group
   has a look of its own: an icon, a colour (its pill, its row in the panel, the head of its home) and a cover picture
   over its home; none of it is drawn behind what you read or write. */
const HOME = "orbit:home";
VIEWS[HOME] = { kind: "home", title: "Home", icon: "panel" };
const H = { md: new Map(), editing: null, soon: 0, pins: new Map() };  // md: path → {text, hash, at}
const HOME_EVENTS = new Set(["session", "closed", "groups", "schedules", "pin", "card", "layout", "note_append"]);
const SPAN_LABEL = { 2: "A third", 3: "A half", 4: "Two thirds", 6: "The whole row" };
const WIDGET_TITLE = { sessions: "Sessions", groups: "Subgroups", tabs: "Files and views", schedules: "Schedules", activity: "Lately", pins: "Pinned" };
const DEFAULT_HOME = [{ id: "sessions", kind: "sessions", span: 6 }, { id: "groups", kind: "groups", span: 6 },
                      { id: "pins", kind: "pins", span: 6 },
                      { id: "tabs", kind: "tabs", span: 3 }, { id: "schedules", kind: "schedules", span: 3 },
                      { id: "activity", kind: "activity", span: 6 }];
const homeOn = () => xKind() === "home";

/* ---------- opening it */
function openHome(gid = S.group, { focus = true, rec = true } = {}) {
  if (EMBED) return;
  saveDraft();
  if (gid && gid !== S.group) S.group = gid;
  if (XN && XN.editing) leaveCalNote(false);
  S.file = HOME;
  S.lastTab[S.group] = "f:" + HOME;
  if (rec) record("f:" + HOME);
  showCurrent(focus);
}
function homeSoon() {
  if (!homeOn() || H.soon) return;
  H.soon = setTimeout(() => { H.soon = 0; if (homeOn() && !H.editing) renderHome(); }, 350);
}

/* ---------- a group's look */
const groupStyleOf = gid => gid === FAV ? { icon: "", color: "", cover: "" } : (groupOf(gid) || {});
/* Its icon: an emoji (or a few letters), or a picture; "" when it has none. */
function groupIconHtml(gid, size = 16) {
  const g = groupStyleOf(gid);
  if (!g.icon) return "";
  if (g.icon.startsWith("img:")) return `<img class="gicon" src="${esc(rawUrl(g.icon.slice(4)))}" alt="" style="width:${size}px;height:${size}px">`;
  return `<span class="gicon" style="font-size:${Math.round(size * 0.9)}px">${esc(g.icon)}</span>`;
}

/* ---------- the layout */
function homeWidgets(gid) {
  const g = groupOf(gid);
  if (gid === FAV) return [{ id: "sessions", kind: "sessions", span: 6 }, { id: "activity", kind: "activity", span: 6 }];
  return (g && g.home && Array.isArray(g.home.widgets)) ? g.home.widgets : DEFAULT_HOME;
}
async function saveHome(gid, widgets) {
  const g = groupOf(gid); if (!g) return;
  g.home = { widgets };  // (at once; the server's "groups" event follows)
  renderHome();
  try { await api("POST", `/api/groups/${gid}/home`, { widgets }); } catch (e) { toast(e.message); }
}
const newWidgetId = () => "w" + Math.random().toString(36).slice(2, 9);

/* ---------- what it shows */
function groupSessions(gid) { return [...S.sessions.values()].filter(s => inView(s, gid) && !s.fleet); }
function subtreeSessions(gid) {
  const ids = new Set(gid === FAV ? [] : [gid, ...groupDescendants(gid)]);
  return [...S.sessions.values()].filter(s => !s.fleet && (gid === FAV ? s.favorite : ids.has(s.group)));
}
function homeSummary(gid) {
  const ss = subtreeSessions(gid), w = ss.filter(s => s.state === "working" || s.agents > 0).length, attn = ss.filter(s => s.attention).length;
  const sids = new Set(ss.map(s => s.id));
  const next = (S.schedules || []).filter(x => x.enabled && x.next && sids.has(x.sid)).sort((a, b) => a.next - b.next)[0];
  return [ss.length ? `${ss.length} session${ss.length === 1 ? "" : "s"}` : "no session yet", w ? `${w} working` : "",
          attn ? `${attn} need${attn === 1 ? "s" : ""} you` : "", next ? `next: ${next.name} ${fmtNext(next.next)}` : ""].filter(Boolean).join(" · ");
}
function sessionCard(s) {
  const pv = sessionPreview(s), st = sessionStatus(s);
  return `<button class="hcard ss ${st.cls}" data-hopen="${esc(s.id)}" title="${esc(st.title)}">` +
    `<span class="h1">${stateMark(s)}${harnessIcon(s.harness)}<span class="nm">${esc(sessionTitle(s))}</span><span class="tm">${esc(lastMoved(s))}</span></span>` +
    `<span class="h2${pv.dim ? " dim" : ""}">${esc(pv.text)}</span></button>`;
}
function groupCard(g) {
  const ss = subtreeSessions(g.id), w = ss.filter(s => s.state === "working" || s.agents > 0).length;
  const kids = groupKids(g.id).length;
  return `<button class="hcard grp" data-hgroup="${esc(g.id)}" style="${g.color ? `--gc:${esc(g.color)}` : ""}">` +
    `<span class="h1">${groupIconHtml(g.id, 18) || `<span class="gicon dflt">${I("folder", 15)}</span>`}<span class="nm">${esc(g.name)}</span><span class="tm">${w}/${ss.length}</span></span>` +
    `<span class="h2 dim">${[ss.length ? `${ss.length} session${ss.length === 1 ? "" : "s"}` : "empty", kids ? `${kids} subgroup${kids === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ")}</span></button>`;
}
/* - [ ] items: ticked here (the note is written back, from the version read). */
function withTasks(html) {
  let n = 0;
  return html.replace(/<li>(\s*<p>)?\[( |x|X)\]\s/g, (_, p, x) => `<li class="task">${p || ""}<input type="checkbox" data-task="${n++}"${x !== " " ? " checked" : ""}> `);
}
async function loadMd(path, fresh = false) {
  const c = H.md.get(path);
  if (c && !fresh && Date.now() - c.at < 10000) return c;
  try {
    const f = await api("GET", "/api/file?path=" + encodeURIComponent(path));
    const x = { text: f.text || "", hash: f.hash, at: Date.now() };
    H.md.set(path, x);
    return x;
  } catch (e) { const x = { error: e.message, at: Date.now() }; H.md.set(path, x); return x; }
}
/* The pins: blocks of the group's Pins.md, newest first ({id, head, body}). */
function parsePins(text) {
  const out = [];
  const re = /<!-- pin:([0-9a-f]{10}) -->\n## ([^\n]*)\n([\s\S]*?)(?=<!-- pin:[0-9a-f]{10} -->\n|$)/g;
  let m;
  while ((m = re.exec(text || ""))) out.push({ id: m[1], head: m[2], body: m[3].trim() });
  return out;
}
function pinHead(h) {  // "2026-11-24 22:10 · Name · [source](orbit://session/…)" → its parts
  const src = /\[source\]\((orbit:\/\/session\/[^)]+)\)/.exec(h);
  return { text: h.replace(/\s*·\s*\[source\]\([^)]*\)/, ""), src: src ? src[1] : "" };
}

function widgetBody(gid, w) {
  if (w.kind === "sessions") {
    const ss = groupSessions(gid);
    return ss.length ? `<div class="hcards">${ss.map(sessionCard).join("")}</div>`
      : `<div class="hempty">No session here yet: ⌘N starts one in this group.</div>`;
  }
  if (w.kind === "groups") {
    const kids = gid === FAV ? [] : groupKids(gid);
    return kids.length ? `<div class="hcards">${kids.map(groupCard).join("")}</div>`
      : `<div class="hempty">No subgroup. ${groupDepth(gid) < GROUP_DEPTH ? `<button class="btn sm" data-hact="subgroup">New subgroup…</button>` : ""}</div>`;
  }
  if (w.kind === "tabs") {
    const ts = gid === FAV ? [] : groupFileTabs(gid);
    return ts.length ? `<div class="hlist">${ts.map(t => `<button class="hrow" data-hfile="${esc(t.path)}">` +
        `<span class="ic">${isView(t.path) ? I(viewInfo(t.path).icon, 13) : fileIcon(t.path)}</span><span class="nm">${esc(ftabTitle(t.path))}</span>` +
        `<span class="wh">${isView(t.path) ? "" : esc(tilde(dirname(t.path)))}</span></button>`).join("")}</div>`
      : `<div class="hempty">No file open in this group.</div>`;
  }
  if (w.kind === "schedules") {
    const sids = new Set(subtreeSessions(gid).map(s => s.id));
    const xs = (S.schedules || []).filter(x => sids.has(x.sid));
    return xs.length ? `<div class="hlist">${xs.map(x => `<button class="hrow" data-hsched="${esc(x.id)}"><span class="ic"><span class="dot ${schedState(x)}"></span></span>` +
        `<span class="nm">${esc(x.name)}</span><span class="wh">${x.enabled ? "next " + esc(fmtNext(x.next)) : "off"} · ${esc(x.target_name || "")}</span></button>`).join("")}</div>`
      : `<div class="hempty">No schedule wakes a session of this group.</div>`;
  }
  if (w.kind === "activity") {
    const ss = subtreeSessions(gid).filter(s => s.attention || (s.last && s.last.time)).sort((a, b) => !!b.attention - !!a.attention || ((b.last || {}).time || 0) - ((a.last || {}).time || 0)).slice(0, 8);
    return ss.length ? `<div class="hlist">${ss.map(s => `<button class="hrow act" data-hopen="${esc(s.id)}">${stateMark(s)}<span class="nm">${esc(sessionTitle(s))}</span>` +
        `<span class="pv">${esc(s.attention ? "needs you: " + s.attention : sessionPreview(s).text)}</span><span class="tm">${esc(lastMoved(s))}</span></button>`).join("")}</div>`
      : `<div class="hempty">Nothing done here yet.</div>`;
  }
  if (w.kind === "pins") {
    const g = groupOf(gid), path = g && g.pins, c = path && H.md.get(path);
    if (path && !c) loadMd(path).then(homeSoon);
    const ps = c && !c.error ? parsePins(c.text) : [];
    return ps.length ? `<div class="hpins">${ps.map(p => { const h = pinHead(p.head); return `<div class="hpin" data-pin="${p.id}">` +
        `<div class="ph"><span>${esc(h.text)}</span>${h.src ? `<a class="src" data-hsrc="${esc(h.src)}">source</a>` : ""}<button class="ibtn sm" data-unpin="${p.id}" title="Unpin">${I("x", 11)}</button></div>` +
        `<div class="pb md">${renderMd(p.body)}</div></div>`; }).join("")}</div>`
      : `<div class="hempty">Nothing pinned. Pin an answer from its menu, or an agent runs <code>orbit-agent pin "…"</code>: decisions, conclusions, what to remember.</div>`;
  }
  if (w.kind === "md") {
    const c = H.md.get(w.path);
    if (!c) { loadMd(w.path).then(homeSoon); return `<div class="hempty">Loading…</div>`; }
    if (c.error) return `<div class="hempty">${esc(c.error)}</div>`;
    return c.text.trim() ? `<div class="hmd md">${withTasks(renderMd(c.text))}</div>` : `<div class="hempty">Empty: ⏎ or Edit writes here.</div>`;
  }
  return "";
}
function widgetTitle(w) { return w.kind === "md" ? (w.title || basename(w.path).replace(/\.md$/i, "")) : WIDGET_TITLE[w.kind] || w.kind; }
function widgetCount(gid, w) {
  if (w.kind === "sessions") return groupSessions(gid).length || "";
  if (w.kind === "groups") return gid === FAV ? "" : groupKids(gid).length || "";
  if (w.kind === "tabs") return gid === FAV ? "" : groupFileTabs(gid).length || "";
  return "";
}

/* ---------- drawing it */
function renderHome() {
  if (!homeOn()) return;
  const gid = S.group, g = groupStyleOf(gid), name = gid === FAV ? "Favorites" : (groupOf(gid) || {}).name || "?";
  $("#xview").classList.add("home");
  const crumbs = [];
  for (let p = gid !== FAV && groupOf(gid) && groupOf(gid).parent; p; p = (groupOf(p) || {}).parent) crumbs.unshift(p);
  const head = `<div class="hhead${g.cover ? " cover" : ""}" style="${g.color ? `--gc:${esc(g.color)};` : ""}${g.cover ? `--cover:url('${esc(rawUrl(g.cover))}')` : ""}">` +
    `<div class="hhi">${crumbs.length ? `<div class="hcrumbs">${crumbs.map(c => `<a data-hgroup="${esc(c)}">${esc(groupOf(c).name)}</a>`).join(" › ")} ›</div>` : ""}` +
    `<div class="htitle">${groupIconHtml(gid, 30) || (gid === FAV ? `<span class="gicon">${I("star", 24)}</span>` : "")}<h1>${esc(name)}</h1></div>` +
    `<div class="hsum">${esc(homeSummary(gid))}</div></div>` +
    (gid === FAV ? "" : `<div class="hacts"><button class="btn sm" data-hact="style">${I("pencil", 12)} Look</button><button class="btn sm" data-hact="add">${I("plus", 12)} Widget</button></div>`) + `</div>`;
  const ws = homeWidgets(gid).filter(w => !w.hidden && !(w.kind === "groups" && !groupKids(gid).length && gid === FAV));
  const grid = ws.map(w => `<section class="hw${w.kind === "md" ? " mdw" : ""}${H.editing && H.editing.id === w.id ? " editing" : ""}" data-w="${esc(w.id)}" style="--span:${w.span || 6}">` +
    `<header title="Drag to move"><span class="grip">⋮⋮</span><span class="t">${esc(widgetTitle(w))}</span><span class="cnt">${widgetCount(gid, w)}</span>` +
    (w.kind === "md" ? `<button class="btn sm quiet" data-hedit="${esc(w.id)}">${H.editing && H.editing.id === w.id ? "Done" : "Edit"}</button>` : "") +
    (gid === FAV ? "" : `<button class="ibtn sm" data-wmenu="${esc(w.id)}" title="Width, hide, remove">${I("more", 13)}</button>`) + `</header>` +
    `<div class="wb">${widgetBody(gid, w)}</div></section>`).join("");
  $("#xhead").innerHTML = ""; $("#xfoot").textContent = "";
  $("#xbody").innerHTML = head + `<div class="hgrid">${grid}</div>`;
  if (H.editing) mountHomeEditor();
}
function homeUnshown() { $("#xview").classList.remove("home"); if (H.editing) leaveHomeEditor(false); }

/* ---------- Markdown widgets: edit in place */
async function editHomeMd(wid) {
  const w = homeWidgets(S.group).find(x => x.id === wid); if (!w || w.kind !== "md") return;
  if (H.editing && H.editing.id !== wid) await leaveHomeEditor(false);
  H.editing = { id: wid, path: w.path };
  renderHome();
}
async function mountHomeEditor() {
  const e = H.editing, box = $(`#xbody .hw[data-w="${CSS.escape(e.id)}"] .wb`); if (!box) return;
  let ed = EDITORS.get(e.path);
  if (!ed) {
    ed = { kind: "loading", host: document.createElement("div"), dirty: false };
    ed.host.className = "host md";
    EDITORS.set(e.path, ed);
    try { mountFile(e.path, ed, await api("GET", "/api/file?path=" + encodeURIComponent(e.path))); }
    catch (err) { EDITORS.delete(e.path); H.editing = null; toast(err.message); renderHome(); return; }
  }
  box.innerHTML = ""; box.classList.add("hedit"); box.appendChild(ed.host); ed.host.hidden = false;
  if (ed.ed) ed.ed.focus();
}
async function leaveHomeEditor(redraw = true) {
  const e = H.editing; if (!e) return;
  H.editing = null;
  const ed = EDITORS.get(e.path);
  if (ed) {
    if (ed.dirty) await saveFile(e.path);
    if (ed.ed) H.md.set(e.path, { text: ed.ed.getDoc(), hash: ed.hash, at: Date.now() });
    disposeEditor(e.path);
  }
  if (redraw && homeOn()) { renderHome(); $("#xbody").focus({ preventScroll: true }); }
}
async function toggleTask(path, n, on) {
  const c = await loadMd(path, true); if (c.error) return toast(c.error);
  let i = -1;
  const text = c.text.replace(/^(\s*[-*+]\s+)\[( |x|X)\]/gm, (all, pre) => (++i === n ? `${pre}[${on ? "x" : " "}]` : all));
  try { const r = await api("POST", "/api/file", { path, text, base_hash: c.hash }); H.md.set(path, { text, hash: r.hash, at: Date.now() }); }
  catch (e) { toast(e.message); H.md.delete(path); }
  renderHome();
}

/* ---------- adding, arranging */
const fileSafe = n => String(n).replace(/[\\/:*?"<>|#^[\]]+/g, " ").replace(/\s+/g, " ").trim();
const TEMPLATES = {
  About: "# About\n\n**Goal** \n\n**Status** \n\n**Links**\n- Repository: \n- Docs: \n\n**People** \n",
  "Next steps": "# Next steps\n\n- [ ] \n",
  Decisions: "# Decisions\n\n| When | What | Why |\n|---|---|---|\n|  |  |  |\n",
};
async function addMdWidget(title, text) {
  const gid = S.group, name = `Groups/${groupPath(gid, "/").split("/").map(fileSafe).join("/")}/${fileSafe(title) || "Note"}`;
  try {
    const r = await api("POST", "/api/notes/create", { name });
    if (r.created && text) await api("POST", "/api/file", { path: r.path, text, force: true });
    const ws = [...homeWidgets(gid), { id: newWidgetId(), kind: "md", path: r.path, title: fileSafe(title) || "Note", span: 3 }];
    H.md.delete(r.path);
    await saveHome(gid, ws);
  } catch (e) { toast(e.message); }
}
function addWidgetMenu(where) {
  const ws = homeWidgets(S.group), hidden = ws.filter(w => w.hidden), has = k => ws.some(w => w.kind === k);
  menu([
    { label: "A Markdown note…", run: async () => { const v = await dialog({ title: "New note on the home page", fields: [{ key: "title", label: "Title" }], ok: "Add", validate: v => v.title.trim() ? null : "Give it a title." }); if (v) addMdWidget(v.title, `# ${v.title.trim()}\n\n`); } },
    { label: "From a template", sub: () => Object.entries(TEMPLATES).map(([t, text]) => ({ label: t, run: () => addMdWidget(t, text) })) },
    { sep: true },
    ...Object.keys(WIDGET_TITLE).filter(k => !has(k)).map(k => ({ label: WIDGET_TITLE[k], run: () => saveHome(S.group, [...ws, { id: k, kind: k, span: 6 }]) })),
    ...hidden.map(w => ({ label: `Show “${widgetTitle(w)}” again`, run: () => saveHome(S.group, ws.map(x => x.id === w.id ? { ...x, hidden: false } : x)) })),
    { sep: true },
    { label: "Back to the default layout", run: async () => { const g = groupOf(S.group); if (g) delete g.home; renderHome(); try { await api("POST", `/api/groups/${S.group}/home`, { widgets: null }); } catch (e) { toast(e.message); } } },
  ], where);
}
function widgetMenu(wid, where) {
  const ws = homeWidgets(S.group), w = ws.find(x => x.id === wid); if (!w) return;
  const set = patch => saveHome(S.group, ws.map(x => x.id === wid ? { ...x, ...patch } : x));
  const i = ws.indexOf(w), move = d => { const j = i + d; if (j < 0 || j >= ws.length) return; const a = [...ws]; [a[i], a[j]] = [a[j], a[i]]; saveHome(S.group, a); };
  menu([
    { label: "Width", sub: () => [2, 3, 4, 6].map(n => ({ label: SPAN_LABEL[n], key: (w.span || 6) === n ? "now" : "", run: () => set({ span: n }) })) },
    { label: "Move earlier", disabled: i <= 0, run: () => move(-1) }, { label: "Move later", disabled: i >= ws.length - 1, run: () => move(1) },
    w.kind === "md" ? { label: "Open the note as a file", run: () => openFile(w.path, { pinned: true }) } : null,
    w.kind === "md" ? { label: "Rename…", run: async () => { const v = await dialog({ title: "Widget title", fields: [{ key: "t", label: "Title", value: widgetTitle(w) }] }); if (v && v.t.trim()) set({ title: v.t.trim() }); } } : null,
    { sep: true },
    w.kind === "md" ? { label: "Remove from the home page (the note stays)", run: () => saveHome(S.group, ws.filter(x => x.id !== wid)) }
                    : { label: "Hide", run: () => set({ hidden: true }) },
  ].filter(Boolean), where);
}

/* ---------- a group's look: icon, colour, cover */
const GROUP_COLORS = ["#2a78d6", "#1baf7a", "#eb6834", "#eda100", "#e87ba4", "#6250d6", "#008300", "#52514e"];
const ICON_IDEAS = ["🚀", "🧪", "📈", "🛠️", "📚", "🎯", "💡", "🧭", "🌱", "🏠", "💼", "🎨"];
async function uploadPicture() {
  const f = await new Promise(res => { const i = document.createElement("input"); i.type = "file"; i.accept = "image/*"; i.onchange = () => res(i.files[0] || null); i.click(); });
  if (!f) return null;
  const data = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1]); r.onerror = rej; r.readAsDataURL(f); });
  return (await api("POST", "/api/upload", { name: f.name, data })).path;
}
async function groupLookDialog(gid = S.group) {
  const g = groupOf(gid); if (!g) return;
  const st = { icon: g.icon || "", color: g.color || "", cover: g.cover || "" };
  const draw = box => {
    box.innerHTML = `<div class="field"><label>Icon</label><div class="lk-icons">${ICON_IDEAS.map(e => `<button class="lk-ic${st.icon === e ? " on" : ""}" data-ic="${e}">${e}</button>`).join("")}` +
      `<input class="lk-own" placeholder="Your own: an emoji or 1–2 letters" maxlength="8" value="${esc(st.icon.startsWith("img:") ? "" : st.icon)}"></div>` +
      `<div class="lk-row"><button class="btn sm" data-lk="iconpic">A picture…</button>${st.icon ? `<button class="btn sm quiet" data-lk="noicon">No icon</button>` : ""}${st.icon.startsWith("img:") ? `<img class="lk-prev" src="${esc(rawUrl(st.icon.slice(4)))}">` : ""}</div></div>` +
      `<div class="field"><label>Colour</label><div class="lk-colors"><button class="lk-c none${!st.color ? " on" : ""}" data-c="" title="None"></button>${GROUP_COLORS.map(c => `<button class="lk-c${st.color === c ? " on" : ""}" data-c="${c}" style="background:${c}"></button>`).join("")}` +
      `<input type="color" class="lk-pick" value="${esc(st.color || "#2a78d6")}" title="Any colour"></div></div>` +
      `<div class="field"><label>Cover (over the home page)</label><div class="lk-row"><button class="btn sm" data-lk="cover">A picture…</button>${st.cover ? `<button class="btn sm quiet" data-lk="nocover">No cover</button><span class="lk-cover" style="background-image:url('${esc(rawUrl(st.cover))}')"></span>` : ""}</div></div>`;
  };
  const v = await dialog({ title: `The look of ${groupPath(gid)}`, ok: "Save", wide: true,
    render: box => {
      draw(box);
      box.addEventListener("click", async e => {
        const ic = e.target.closest("[data-ic]"), c = e.target.closest("[data-c]"), lk = e.target.closest("[data-lk]");
        if (ic) { st.icon = ic.dataset.ic; draw(box); }
        else if (c) { st.color = c.dataset.c; draw(box); }
        else if (lk) {
          const a = lk.dataset.lk;
          try {
            if (a === "iconpic") { const p = await uploadPicture(); if (p) st.icon = "img:" + p; }
            else if (a === "cover") { const p = await uploadPicture(); if (p) st.cover = p; }
            else if (a === "noicon") st.icon = "";
            else if (a === "nocover") st.cover = "";
          } catch (err) { toast(err.message); }
          draw(box);
        }
      });
      box.addEventListener("input", e => {
        if (e.target.matches(".lk-own")) st.icon = e.target.value.trim();
        if (e.target.matches(".lk-pick")) { st.color = e.target.value; box.querySelectorAll(".lk-c").forEach(x => x.classList.remove("on")); }
      });
    } });
  if (!v) return;
  try { await api("POST", `/api/groups/${gid}/style`, st); } catch (e) { toast(e.message); }
}

/* ---------- pins */
async function pinToGroup(text, { gid = S.group, sid = "", cid = "", by = "" } = {}) {
  if (!gid || gid === FAV) { const s = S.sessions.get(sid); gid = s ? s.group : (topGroups()[0] || {}).id; }
  try { await api("POST", `/api/groups/${gid}/pin`, { text, sid, cid, by }); toast(`Pinned to ${groupPath(gid)}'s home`); }
  catch (e) { toast(e.message); }
}

/* ---------- events */
$("#xview").addEventListener("click", e => {
  if (!homeOn()) return;
  const t = e.target;
  const done = () => { e.preventDefault(); e.stopPropagation(); };
  const cb = t.closest("input[data-task]");
  if (cb) { const w = homeWidgets(S.group).find(x => x.id === cb.closest(".hw").dataset.w); if (w) toggleTask(w.path, +cb.dataset.task, cb.checked); return; }
  const o = t.closest("[data-hopen]"); if (o) { done(); switchTo(o.dataset.hopen); return; }
  const g = t.closest("[data-hgroup]"); if (g) { done(); openHome(g.dataset.hgroup); return; }
  const f = t.closest("[data-hfile]"); if (f) { done(); openFile(f.dataset.hfile); return; }
  const sc = t.closest("[data-hsched]"); if (sc) { done(); const it = (S.schedules || []).find(x => x.id === sc.dataset.hsched); if (it) scheduleDialog(it); return; }
  const src = t.closest("[data-hsrc]"); if (src) { done(); openUrl(src.dataset.hsrc); return; }
  const up = t.closest("[data-unpin]"); if (up) { done(); api("POST", `/api/groups/${S.group}/unpin`, { id: up.dataset.unpin }).then(() => { const g = groupOf(S.group); if (g && g.pins) H.md.delete(g.pins); renderHome(); }).catch(err => toast(err.message)); return; }
  const ed = t.closest("[data-hedit]"); if (ed) { done(); if (H.editing && H.editing.id === ed.dataset.hedit) leaveHomeEditor(); else editHomeMd(ed.dataset.hedit); return; }
  const wm = t.closest("[data-wmenu]"); if (wm) { done(); widgetMenu(wm.dataset.wmenu, { anchor: wm }); return; }
  const a = t.closest("[data-hact]");
  if (a) {
    done();
    if (a.dataset.hact === "style") groupLookDialog();
    else if (a.dataset.hact === "add") addWidgetMenu({ anchor: a });
    else if (a.dataset.hact === "subgroup") newGroup(S.group);
  }
}, true);
$("#xview").addEventListener("dblclick", e => {
  if (!homeOn() || H.editing) return;
  const w = e.target.closest(".hw.mdw"); if (w && !e.target.closest("input, a, button")) editHomeMd(w.dataset.w);
});
/* Keys on the home page: ⏎ edits the first note (or ⌘E: the add menu); Esc leaves a note being edited. */
function homeKey(e, combo) {
  if (combo === "Escape" && H.editing) { leaveHomeEditor(); return true; }
  if (H.editing) return false;
  if (combo === "M:KeyE") { addWidgetMenu({ anchor: $("#xbody [data-hact=add]") }); return true; }
  if (combo === "Enter") { const w = homeWidgets(S.group).find(x => x.kind === "md" && !x.hidden); if (w) editHomeMd(w.id); return true; }
  return false;
}
/* Dragging a widget by its head (its title bar, not its buttons): the others slide aside; the new order is saved. */
$("#xbody").addEventListener("mousedown", e => {
  H.press = homeOn() && !!e.target.closest(".hw > header") && !e.target.closest("button, a, input");
}, true);
dragSort({
  box: $("#xbody"), item: ".hw", axis: "x",
  keyOf: el => el.dataset.w, byKey: k => $(`#xbody .hw[data-w="${CSS.escape(k)}"]`),
  canDrag: el => homeOn() && S.group !== FAV && !H.editing && !!H.press,
  unit: el => [el],
  peers: el => [...$("#xbody").querySelectorAll(".hw")].filter(x => x !== el).map(x => [x]),
  redraw: () => renderHome(),
  drop: (key, prev, next) => {
    const ws = homeWidgets(S.group), w = ws.find(x => x.id === key); if (!w) return renderHome();
    const rest = ws.filter(x => x !== w);
    let at = next ? rest.findIndex(x => x.id === next.dataset.w) : prev ? rest.findIndex(x => x.id === prev.dataset.w) + 1 : rest.length;
    if (at < 0) at = rest.length;
    rest.splice(at, 0, w);
    saveHome(S.group, rest);
  },
});
