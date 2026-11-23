"use strict";
/* ===================================================================== Conversation
   One session: its cards, live while it works; the desktop's scroll rule (stuck to the end it follows, scrolled up it
   stays put, "Latest" brings it back); the subagents strip and the input at the foot, above the keyboard. */
const conv = $("#conv");
const CONV_WINDOW = 30;  // the latest cards drawn; earlier ones on demand (a long session stays light)
const CONV = { from: 0 };

function openSession(sid, { push = true } = {}) {
  if (!S.sessions.has(sid)) { toast("That session is closed"); return; }
  if (AREA.cur !== "sessions") switchTab("sessions");  // (from another area: a meeting's related session, a schedule's last run)
  if (S.current && S.current !== sid) saveDraft();
  S.current = sid;
  markSeen(sid);
  // One history entry, its address naming the open session (a reload comes back to it). No entry is pushed: iOS's own
  // back and forward swipes (edges) would go through them, a forward one reopening a session closed long ago.
  if (push) history.replaceState({ sid }, "", "#s/" + encodeURIComponent(sid));
  CONV.from = 0;
  SCROLLS.atEnd = !SCROLLS.held[sid];
  $("#input").value = S.drafts[sid] || "";
  autosize(); renderNav(); renderComposer();
  showView("conv");
  if (S.cards.has(sid)) renderConv(); else { $("#cards").innerHTML = `<div class="loading">${markSvg("run", 18)}</div>`; renderAgentBar(); }
  loadCards(sid);
}
function closeSession() {  // back to the list
  if (!S.current) return;
  saveDraft(); markSeen(S.current);
  $("#input").blur();
  S.current = null;
  showView("list");
  renderList();
}
async function loadCards(sid) {
  try {
    const d = await api("GET", `/api/sessions/${encodeURIComponent(sid)}/cards`);
    const have = new Map((S.cards.get(sid) || []).map(c => [c.id, c]));
    S.cards.set(sid, d.cards.map(c => { const h = have.get(c.id); return h && (h.rev || 0) > (c.rev || 0) ? h : c; }));
    for (const c of S.cards.get(sid)) noteActive(c);
    if (sid === S.current) renderConv();
  } catch (e) { if (!(e instanceof NotPaired) && sid === S.current) toast("Could not load the conversation: " + e.message); }
}

/* ===================================================================== the nav bar */
function renderNav() {
  const s = S.sessions.get(S.current); if (!s) return;
  const st = sessionStatus(s);
  const bits = [st.word];
  if (s.queued) bits.push(`${s.queued} queued`);
  bits.push(harnessLabel(s.harness));
  bits.push(modeShort(s.mode));
  if (s.remote) bits.push("on " + s.remote);
  $("#cname").textContent = s.name;
  $("#csub").innerHTML = `${stateMark(s, 12)}<span class="w ${st.cls}">${esc(bits[0])}</span><span>${esc(bits.slice(1).join(" · "))}</span>`;
}

/* ===================================================================== drawing */
function emptyHtml(s) {
  const model = s.model_seen || s.model;
  return `<div class="empty"><h2>Nothing asked yet</h2>Write a request below and send it.` +
    `<div class="ectx">${esc(tilde(s.cwd))} · ${esc(harnessLabel(s.harness))}${model ? " · " + esc(modelName(model)) : ""}</div></div>`;
}
function renderConv() {
  const s = S.sessions.get(S.current); if (!s) return;
  renderAgentBar();
  const list = (S.cards.get(S.current) || []).filter(c => !c.hidden);
  if (!list.length) { $("#cards").innerHTML = emptyHtml(s); updateJump(); return; }
  let from = Math.max(0, list.length - CONV_WINDOW);
  const held = !SCROLLS.atEnd && SCROLLS.held[S.current];
  if (held) { const k = list.findIndex(c => c.id === held.card); if (k >= 0) from = Math.min(from, Math.max(0, k - 2)); }
  CONV.from = from;
  $("#cards").innerHTML = (from ? `<div class="earlier"><button class="pill quiet" data-earlier>Show ${from} earlier request${from === 1 ? "" : "s"}</button></div>` : "") +
    list.slice(from).map(cardDiv).join("");
  if (held && restoreAnchor()) { updateJump(); return; }
  SCROLLS.atEnd = true;
  stick();
}
function showEarlier() {
  const list = (S.cards.get(S.current) || []).filter(c => !c.hidden);
  const to = CONV.from, from = Math.max(0, to - CONV_WINDOW);
  const anchor = $("#cards .card"), y = anchor ? anchor.getBoundingClientRect().top : 0;
  const btn = $("#cards .earlier"); if (btn) btn.remove();
  $("#cards").insertAdjacentHTML("afterbegin", (from ? `<div class="earlier"><button class="pill quiet" data-earlier>Show ${from} earlier request${from === 1 ? "" : "s"}</button></div>` : "") +
    list.slice(from, to).map(cardDiv).join(""));
  CONV.from = from;
  if (anchor) progScroll(conv.scrollTop + anchor.getBoundingClientRect().top - y);
}
function renderCard(c) {
  if (c.sid !== S.current) return;
  const box = $("#cards");
  let el = document.getElementById("card-" + c.id);
  if (c.hidden) { if (el) el.remove(); return; }
  if (!el) {
    const first = box.querySelector(".card");
    if (CONV.from > 0 && first && c.id < +first.id.slice(5)) return;  // one of the earlier cards not drawn
    if (box.querySelector(".empty, .loading")) box.innerHTML = "";
    el = document.createElement("div"); el.className = "card"; el.id = "card-" + c.id;
    box.appendChild(el);
  }
  const lists = [...el.querySelectorAll(".steps ol")].map(o => o.scrollTop);
  el.innerHTML = cardHtml(c);
  el.querySelectorAll(".steps ol").forEach((o, i) => { o.scrollTop = ACTIVE.has(c.state) ? o.scrollHeight : lists[i] || 0; });
  stick();
}
/* A card from the live events: kept, drawn when its session is on screen; the list hears what a session runs. */
function noteActive(c) {
  if (ACTIVE.has(c.state)) S.active.set(c.sid, c);
  else if ((S.active.get(c.sid) || {}).id === c.id) S.active.delete(c.sid);
}
function upsertCard(c) {
  noteActive(c);
  const list = S.cards.get(c.sid);
  if (!list) return;
  const i = list.findIndex(x => x.id === c.id);
  if (i >= 0) { if ((list[i].rev || 0) > (c.rev || 0)) return; list[i] = c; }
  else { list.push(c); list.sort((a, b) => a.id - b.id); }
  if (c.sid === S.current) { renderCard(c); renderAgentBar(); }
}

/* ===================================================================== the scroll rule (ui.md "Conversation scroll")
   Stuck to the end, it follows every new line; scrolled up by the user, it stays where it is (the card at the top and
   its offset), whatever comes in; "Latest" or sending a request sticks it to the end again. A scroll the page made
   itself is told apart by its position (it lands where the page put it), so iOS's momentum counts as the user's. */
const SCROLLS = { atEnd: true, held: LS.get("held", {}), prog: -1 };
const atBottom = () => conv.scrollHeight - conv.scrollTop - conv.clientHeight < 4;
function progScroll(top) { conv.scrollTop = top; SCROLLS.prog = conv.scrollTop; }
function stick() { if (SCROLLS.atEnd) progScroll(conv.scrollHeight); updateJump(); }
function saveAnchor() {
  if (!S.current) return;
  const top = conv.getBoundingClientRect().top;
  const el = $$("#cards .card").find(c => c.getBoundingClientRect().bottom > top + 1);
  if (!el) return;
  SCROLLS.held[S.current] = { card: +el.id.slice(5), off: Math.round(el.getBoundingClientRect().top - top) };
  clearTimeout(saveAnchor.t); saveAnchor.t = setTimeout(() => LS.set("held", SCROLLS.held), 400);
}
function restoreAnchor() {
  const a = SCROLLS.held[S.current], el = a && document.getElementById("card-" + a.card);
  if (!el) return false;
  const d = el.getBoundingClientRect().top - conv.getBoundingClientRect().top - a.off;
  if (Math.abs(d) >= 1) progScroll(conv.scrollTop + d);
  return true;
}
function setAtEnd(v) {
  SCROLLS.atEnd = v;
  if (v) { if (S.current in SCROLLS.held) { delete SCROLLS.held[S.current]; LS.set("held", SCROLLS.held); } }
  else saveAnchor();
  updateJump();
}
conv.addEventListener("scroll", () => {
  $("#vconv .nav").classList.toggle("scrolled", conv.scrollTop > 2);
  if (!S.current) return;
  if (Math.abs(conv.scrollTop - SCROLLS.prog) < 2) return;  // where the page put it
  SCROLLS.prog = -1;
  const end = atBottom();
  if (end !== SCROLLS.atEnd) setAtEnd(end); else if (!end) saveAnchor();
  if (CONV.from > 0 && conv.scrollTop < 300 && !showEarlier.busy) { showEarlier.busy = true; requestAnimationFrame(() => { showEarlier.busy = false; if (conv.scrollTop < 300 && CONV.from > 0) showEarlier(); }); }
}, { passive: true });
const keepPlace = () => { if (!S.current) return; if (SCROLLS.atEnd) stick(); else restoreAnchor(); };
new ResizeObserver(keepPlace).observe($("#cards"));
new ResizeObserver(keepPlace).observe(conv);
function updateJump() { $("#jump").classList.toggle("show", !!S.current && !SCROLLS.atEnd && !!(S.cards.get(S.current) || []).length); }
$("#jump").addEventListener("click", () => { setAtEnd(true); progScroll(conv.scrollHeight); });

/* ===================================================================== card taps */
$("#cards").addEventListener("click", e => {
  const s = S.sessions.get(S.current); if (!s) return;
  const t = e.target;
  const sb = t.closest("[data-steps]");
  if (sb) {  // a group of steps or a subagent: the tap is remembered either way
    const key = sb.dataset.steps, box = sb.parentElement, opening = !box.classList.contains("open");
    (opening ? S.closedSteps : S.openSteps).delete(key); (opening ? S.openSteps : S.closedSteps).add(key);
    box.classList.toggle("open", opening);
    return;
  }
  if (t.closest("[data-earlier]")) { showEarlier(); return; }
  if (t.closest("[data-copy]")) { copyText(t.closest(".pre").querySelector("code").innerText); return; }
  const card = attr => { const b = t.closest(`[${attr}]`); return b && (S.cards.get(s.id) || []).find(c => c.id == b.getAttribute(attr)); };
  const rm = t.closest("[data-reqmore]");
  if (rm) {
    const key = rm.dataset.reqmore; S.openReqs.has(key) ? S.openReqs.delete(key) : S.openReqs.add(key);
    const c = (S.cards.get(s.id) || []).find(x => s.id + ":" + x.id === key); if (c) renderCard(c);
    return;
  }
  let c;
  if ((c = card("data-copyreq"))) { copyText(c.text); return; }
  if ((c = card("data-resend"))) { sendText(s.id, c.text); return; }
  if ((c = card("data-withdraw"))) {
    api("POST", `/api/sessions/${encodeURIComponent(s.id)}/withdraw`, { cid: c.id }).then(() => {
      const ta = $("#input"); ta.value = ta.value.trim() ? ta.value.replace(/\s+$/, "") + "\n\n" + c.text : c.text;
      autosize(); renderComposer(); saveDraft(); toast(`#${c.id} is back in the input`);
    }).catch(err => toast(err.message));
    return;
  }
  if ((c = card("data-steernow"))) {
    api("POST", `/api/sessions/${encodeURIComponent(s.id)}/withdraw`, { cid: c.id })
      .then(() => api("POST", `/api/sessions/${encodeURIComponent(s.id)}/steer`, { text: c.text }))
      .then(r => { upsertCard(r); toast(r.steered ? `Steered #${r.id}` : `Sent as #${r.id}`); }).catch(err => toast(err.message));
    return;
  }
  const cont = t.closest("[data-continue]");
  if (cont) { api("POST", `/api/sessions/${encodeURIComponent(s.id)}/continue`, { cid: +cont.dataset.continue }).catch(err => toast(err.message)); return; }
  const img = t.closest(".md img, .rimg");
  if (img) { openLightbox(img.src); return; }
  const a = t.closest("a.mention");
  if (a) { e.preventDefault(); const sid = (a.getAttribute("href") || "").replace("orbit://session/", ""); if (S.sessions.has(sid)) openSession(sid); return; }
});

/* ===================================================================== the subagents strip */
const AGBAR_MAX = 2;
function renderAgentBar() {
  const bar = $("#agentbar");
  const list = S.current ? runningAgents(S.current) : [];
  bar.hidden = !list.length;
  if (!list.length) { bar.innerHTML = ""; return; }
  const all = renderAgentBar.all || list.length <= AGBAR_MAX + 1, shown = all ? list : list.slice(0, AGBAR_MAX);
  bar.innerHTML = shown.map(({ c, st }) => {
    const sub = st.sub || [], cur = [...sub].reverse().find(x => x.kind === "tool" && !x.done);
    const n = sub.filter(x => x.kind === "tool").length;
    return `<button class="agrow" data-agcard="${c.id}" data-agstep="${esc(st.id)}"><span class="cdot"></span><span class="agtype">${esc(st.agent)}</span>` +
      `<span class="agtask">${esc(cur ? `${cur.name} ${cur.summary || ""}` : st.summary || "")}</span>` +
      `<span class="agn">${n ? `${n} · ` : ""}<span class="el" data-t0="${st.t0 || ""}">${st.t0 ? dur(Date.now() / 1000 - st.t0) : ""}</span></span></button>`;
  }).join("") + (list.length > shown.length ? `<button class="agmore" data-agmore="1">${list.length - shown.length} more running · show all</button>`
    : renderAgentBar.all && list.length > AGBAR_MAX + 1 ? `<button class="agmore" data-agmore="0">Show fewer</button>` : "");
}
$("#agentbar").addEventListener("pointerdown", e => e.preventDefault());  // (the input keeps the keyboard)
$("#agentbar").addEventListener("click", e => {
  const m = e.target.closest("[data-agmore]");
  if (m) { renderAgentBar.all = m.dataset.agmore === "1"; renderAgentBar(); return; }
  const r = e.target.closest(".agrow"); if (!r) return;
  const key = `${S.current}:${r.dataset.agcard}:a:${r.dataset.agstep}`;
  S.openSteps.add(key); S.closedSteps.delete(key);
  const c = (S.cards.get(S.current) || []).find(x => x.id === +r.dataset.agcard);
  if (c && !document.getElementById("card-" + c.id)) { CONV.from = 0; SCROLLS.atEnd = false; renderConv(); }
  else if (c) renderCard(c);
  const box = $$("#cards [data-agent]").find(b => b.dataset.agent === key); if (!box) return;
  const top = box.getBoundingClientRect().top - conv.getBoundingClientRect().top;
  progScroll(conv.scrollTop + top - (conv.clientHeight - box.offsetHeight) / 2);
  setAtEnd(atBottom());
  box.classList.remove("flash"); void box.offsetWidth; box.classList.add("flash");
});

/* ===================================================================== the composer */
/* The desktop's live-preview Markdown editor (web/cm): images, links, math and code render in place; a pasted or
   dropped image is uploaded and goes in as ![name](path). `ta` is its content element with the textarea calls the
   page uses (value, selectionStart, placeholder, insertAt); "input" listeners hear edits after the editor applied
   them. It grows up to a third of the visible height, then scrolls. */
const inputEd = CM.createEditor({
  parent: $("#inputhost"), doc: "", path: "input.md",
  composer: { placeholder: "Message the agent…",
    attrs: { id: "input", autocorrect: "on", autocapitalize: "sentences", spellcheck: "true", enterkeyhint: "enter" } },
  preview: { resolve: inputImageUrl, inline: s => md.renderInline(s) },
  zoom: url => openLightbox(url),
  onOpenLink: url => window.open(url, "_blank", "noopener"),
  onFiles: (files, pos) => attachPhotos(files, pos),
  onChange: () => { if (!inputSetting) inputListeners.forEach(fn => { try { fn(); } catch (e) { console.error(e); } }); },
});
let inputSetting = false;
const inputListeners = [];
const ta = inputEd.view.contentDOM;
{
  const view = inputEd.view, nativeAdd = ta.addEventListener.bind(ta);
  ta.addEventListener = (type, fn, o) => type === "input" ? inputListeners.push(fn) : nativeAdd(type, fn, o);
  Object.defineProperties(ta, {
    value: { get: () => view.state.doc.toString(), set: v => {
      inputSetting = true;
      try { view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: String(v ?? "") }, selection: { anchor: String(v ?? "").length } }); }
      finally { inputSetting = false; }
    } },
    selectionStart: { get: () => view.state.selection.main.from },
    placeholder: { get: () => ta._ph || "", set: v => { if (v !== ta._ph) { ta._ph = v; inputEd.setPlaceholder(v); } } },
  });
  ta.insertAt = (text, at = view.state.selection.main.head) => {  // typed in: undoable, listeners hear it
    view.dispatch({ changes: { from: at, insert: text }, selection: { anchor: at + text.length }, userEvent: "input", scrollIntoView: true });
  };
}
/* An image in the input: a URL as is; a path (the agent's machine's) through /api/raw, with the token. */
function inputImageUrl(src) {
  if (/^(https?:|data:|blob:)/i.test(src)) return src;
  const s = S.sessions.get(S.current);
  let p = src;
  try { p = decodeURIComponent(src); } catch {}
  if (p.startsWith("file://")) p = p.slice(7);
  if (!p.startsWith("/") && !p.startsWith("~/") && s) p = s.cwd.replace(/\/+$/, "") + "/" + p;
  if (s && s.remote && p.startsWith("/") && !p.startsWith("/@")) p = `/@${s.remote}${p}`;
  return withToken(`/api/raw?path=${encodeURIComponent(p)}`);
}
function autosize() {
  const max = Math.max(80, Math.round((window.visualViewport ? visualViewport.height : innerHeight) * .32));
  document.documentElement.style.setProperty("--input-max", max + "px");
}
function saveDraft() {
  if (!S.current) return;
  if (ta.value) S.drafts[S.current] = ta.value; else delete S.drafts[S.current];
  LS.set("drafts", S.drafts);
}
const SEND_SVG = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 13.5V2.5M3.5 7L8 2.5 12.5 7"/></svg>';
/* Idle: Send. While the agent works: Steer (a harness that steers) or Queue, and Stop. */
function renderComposer() {
  const s = S.sessions.get(S.current); if (!s) return;
  const working = s.state === "working", has = !!ta.value.trim();
  $("#stop").hidden = !(working || s.queued);
  const send = $("#send");
  send.classList.toggle("word", working);
  send.innerHTML = `<span class="disc">${working ? (s.steers ? "Steer" : "Queue") : SEND_SVG}</span>`;
  send.setAttribute("aria-label", working ? (s.steers ? "Steer: join the running request" : "Queue: send it after this one") : "Send");
  send.disabled = !has;
  ta.placeholder = working ? (s.steers ? "Steer the agent, or wait…" : "Queue the next request…") : "Message the agent…";
}
ta.addEventListener("input", () => { autosize(); renderComposer(); saveDraft(); });
async function sendText(sid, text) {
  try {
    const c = await api("POST", `/api/sessions/${encodeURIComponent(sid)}/send`, { text });
    if (sid === S.current) setAtEnd(true);
    upsertCard(c);
    return c;
  } catch (e) { if (!(e instanceof NotPaired)) toast("Send failed: " + e.message); throw e; }
}
async function send() {
  const s = S.sessions.get(S.current), text = ta.value;
  if (!s || !text.trim()) return;
  ta.value = ""; autosize(); saveDraft(); renderComposer();
  try {
    if (s.state === "working" && s.steers) {
      const c = await api("POST", `/api/sessions/${encodeURIComponent(s.id)}/steer`, { text });
      setAtEnd(true); upsertCard(c);
      toast(c.steered ? `Steered #${c.id}: it joins the running request` : `Sent as #${c.id}`);
    } else await sendText(s.id, text);
  } catch (e) {  // never lose input: back into the box (or that session's draft)
    if (S.current === s.id) { if (!ta.value) { ta.value = text; autosize(); saveDraft(); renderComposer(); } }
    else { S.drafts[s.id] = text + (S.drafts[s.id] ? "\n\n" + S.drafts[s.id] : ""); LS.set("drafts", S.drafts); }
    if (!(e instanceof NotPaired) && s.state === "working" && s.steers) toast("Steer failed: " + e.message);
  }
}
$("#send").addEventListener("pointerdown", e => { if (document.activeElement === ta) e.preventDefault(); });  // (the keyboard stays up)
$("#send").addEventListener("click", send);
$("#stop").addEventListener("pointerdown", e => { if (document.activeElement === ta) e.preventDefault(); });
$("#stop").addEventListener("click", () => sessionAction(S.current, "stop"));

/* A photo from the camera or the library, or an image (any file) pasted or dropped into the input: saved by the
   server (a photo as a smaller JPEG first), ![name](path) (another file: [name](path)) into the request at the cursor. */
$("#attach").addEventListener("click", () => $("#photo").click());
$("#photo").addEventListener("change", () => {
  const files = [...$("#photo").files]; $("#photo").value = "";
  attachPhotos(files);
});
async function attachPhotos(files, pos) {
  const s = S.sessions.get(S.current); files = [...files].filter(f => f && f.size);
  if (!s || !files.length) return;
  let at = pos ?? ta.selectionStart, n = 0;
  for (const f of files) {
    $("#attach").classList.add("busy");
    try {
      const img = /^image\//.test(f.type);
      const { name, data } = img ? await shrinkPhoto(f) : { name: f.name || "file", data: await fileData(f) };
      const r = await api("POST", (s.remote ? `/api/remote/${s.remote}` : "") + "/api/upload", { name, data });
      const path = s.remote && r.path.startsWith("/") ? `/@${s.remote}${r.path}` : r.path;
      const link = `${img ? "!" : ""}[${basename(path).replace(/[\[\]]/g, "")}](${/[\s()<>]/.test(path) ? `<${path}>` : path})`;
      const v = ta.value; at = Math.min(at, v.length);
      const text = (v.slice(0, at) && !/\s$/.test(v.slice(0, at)) ? " " : "") + link + (/^\s/.test(v.slice(at)) ? "" : " ");
      ta.insertAt(text, at); at += text.length; n++;
    } catch (e) { if (!(e instanceof NotPaired)) toast(`${f.name || "Photo"}: ${e.message}`); }
    finally { $("#attach").classList.remove("busy"); }
  }
  if (n) toast(n === 1 ? "Attached: the agent gets its path" : `${n} attached: the agent gets their paths`);
}
const fileData = f => new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",")[1] || ""); r.onerror = () => bad(r.error); r.readAsDataURL(f); });
async function shrinkPhoto(f, max = 2048) {
  const b64 = blob => new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",")[1] || ""); r.onerror = () => bad(r.error); r.readAsDataURL(blob); });
  const stem = (f.name || "photo").replace(/\.[^.]+$/, "") || "photo";
  try {
    const bmp = await createImageBitmap(f);
    const k = Math.min(1, max / Math.max(bmp.width, bmp.height));
    if (k === 1 && /jpe?g|png/i.test(f.type) && f.size < 3e6) return { name: f.name || "photo.jpg", data: await b64(f) };
    const cv = document.createElement("canvas");
    cv.width = Math.round(bmp.width * k); cv.height = Math.round(bmp.height * k);
    cv.getContext("2d").drawImage(bmp, 0, 0, cv.width, cv.height);
    const blob = await new Promise(r => cv.toBlob(r, "image/jpeg", .85));
    return { name: stem + ".jpg", data: await b64(blob) };
  } catch { return { name: f.name || "photo.jpg", data: await b64(f) }; }
}

/* ===================================================================== session actions (⋯) */
async function sessionAction(sid, action) {
  try {
    const r = await api("POST", `/api/sessions/${encodeURIComponent(sid)}/${action}`, {});
    if (action === "fork") { S.sessions.set(r.id, r); toast(`Forked: “${r.name}” continues this conversation`); openSession(r.id, { push: false }); history.replaceState({ sid: r.id }, "", "#s/" + encodeURIComponent(r.id)); }
    else if (action === "restart") toast("Restarted");
    else if (action === "resume") toast("Resumed");
    else if (action === "stop") toast("Stopped");
  } catch (e) { if (!(e instanceof NotPaired)) toast(`${action[0].toUpperCase() + action.slice(1)} failed: ${e.message}`); }
}
/* Permission mode (the desktop's mode chip, /mode): the agent restarts on the same conversation with it, so the server
   refuses while a request runs (said in a toast). */
const MODES_M = [
  { id: "default", short: "default", sub: "The agent's own setting (Claude Code: your defaultMode)" },
  { id: "acceptEdits", short: "accept edits", sub: "File edits approved, other tools refused" },
  { id: "auto", short: "auto", sub: "Claude Code's classifier approves what is safe" },
  { id: "plan", short: "plan", sub: "Read only: it proposes a plan" },
  { id: "bypassPermissions", short: "bypass", sub: "Every tool approved, no questions" },
];
const modeShort = m => (MODES_M.find(x => x.id === m) || { short: m || "default" }).short;
function modeSheet(s) {
  const mine = modesForM(s.harness);
  actionSheet("Permission mode", MODES_M.filter(m => mine.includes(m.id)).map(m => ({ label: m.short[0].toUpperCase() + m.short.slice(1), sub: m.sub, on: (s.mode || "default") === m.id,
    run: () => setModeM(s, m.id) })));
}
async function setModeM(s, mode) {
  if ((s.mode || "default") === mode) return;
  try {
    const r = await api("POST", `/api/sessions/${encodeURIComponent(s.id)}/mode`, { mode });
    S.sessions.set(s.id, { ...S.sessions.get(s.id), ...r }); renderNav();
    toast(`Mode: ${modeShort(mode)} (the agent restarts on this conversation)`);
  } catch (e) { if (!(e instanceof NotPaired)) toast(e.message); }
}
function sessionMenu() {
  const s = S.sessions.get(S.current); if (!s) return;
  const working = s.state === "working";
  actionSheet(s.name, [
    { label: `Mode: ${modeShort(s.mode)}`, icon: "gear", disabled: working, run: () => modeSheet(s) },
    { label: "Stop", icon: "stop", disabled: !working && !s.queued, run: () => sessionAction(s.id, "stop") },
    { label: "Fork", icon: "fork", run: () => sessionAction(s.id, "fork") },
    { label: "Resume", icon: "play", disabled: s.state === "ready" || working, run: () => sessionAction(s.id, "resume") },
    { label: "Restart", icon: "refresh", run: () => sessionAction(s.id, "restart") },
    { label: "Copy link", icon: "link", run: () => copyText(`orbit://session/${s.id}`, "Link copied: it opens this session in Orbit on the Mac") },
  ]);
}
$("#cmore").addEventListener("click", sessionMenu);
function goBack() { closeSession(); history.replaceState(null, "", location.pathname + location.search); }
$("#cback").addEventListener("click", goBack);

/* ===================================================================== Quote (the desktop's ❝ Quote, editor.md §5)
   Text selected in an answer, or in a plugin thread's message, shows a Quote pill under the selection (iOS puts its
   own menu above it); a tap puts "> where" and the quoted lines at the end of that input (the session's, or the
   thread's reply), with the cursor after them. The pill keeps the selection (pointerdown is not let through). */
const quoteBlock = q => `> ${q.ref}\n` + q.text.replace(/\s+$/, "").split("\n").map(l => "> " + l).join("\n") + "\n\n";
const QUOTE = { q: null, el: null };
function quoteSourceM() {
  const sel = getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const text = sel.toString().trim();
  const el = n => n && (n.nodeType === 1 ? n : n.parentElement);
  const a = el(sel.anchorNode), f = el(sel.focusNode);
  if (!text || !a || !f) return null;
  const range = sel.getRangeAt(0);
  const card = a.closest("#cards .card");
  if (card && card.contains(f) && S.current && document.body.classList.contains("in-conv")) {
    const s = S.sessions.get(S.current);
    return { text, ref: `${s ? sessionTitle(s) : "session"} #${card.id.replace("card-", "")}`, into: null, range };
  }
  const msg = a.closest(".tmsg"), page = msg && typeof PV !== "undefined" && PV.threads.find(p => p.el.contains(msg));
  if (page && page.el.contains(f) && page.t.ed) {
    let m = msg; while (m && !m.querySelector(".tauthor .st") && !m.classList.contains("req")) m = m.previousElementSibling;  // (grouped: the author line above)
    const who = msg.classList.contains("req") ? "me" : m && m.querySelector(".tauthor .st") ? m.querySelector(".tauthor .st").textContent.trim() : "";
    return { text, ref: `${(page.t.data && page.t.data.title) || "thread"}${who ? " · " + who : ""}`, into: page, range };
  }
  return null;
}
function placeQuote() {
  const q = quoteSourceM();
  if (!QUOTE.el) {
    QUOTE.el = document.createElement("button");
    QUOTE.el.className = "mquote"; QUOTE.el.type = "button";
    QUOTE.el.innerHTML = `${I("quote", 14)}<span>Quote</span>`;
    QUOTE.el.addEventListener("pointerdown", e => e.preventDefault());  // (the selection stays)
    QUOTE.el.addEventListener("click", () => { if (QUOTE.q) quoteInto(QUOTE.q); });
    document.body.appendChild(QUOTE.el);
  }
  QUOTE.q = q;
  if (!q) { QUOTE.el.classList.remove("show"); return; }
  const rs = q.range.getClientRects(), r = rs[rs.length - 1] || q.range.getBoundingClientRect();
  const vv = window.visualViewport, top0 = vv ? vv.offsetTop : 0, h = vv ? vv.height : innerHeight;
  const bw = 96, bh = 36;
  let y = r.bottom + 12;  // under the selection's end; above it when that is off the visible part
  if (y + bh > top0 + h - 8) y = Math.max(top0 + 8, r.top - bh - 52);
  const x = Math.max(8, Math.min(innerWidth - bw - 8, r.right - bw / 2));
  QUOTE.el.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  QUOTE.el.classList.add("show");
}
function quoteInto(q) {
  const block = quoteBlock(q);
  if (q.into) {  // a plugin thread: its reply
    const ed = q.into.t.ed, v = ed.getDoc(), add = (v && !v.endsWith("\n") ? "\n\n" : "") + block;
    ed.view.dispatch({ changes: { from: v.length, insert: add }, selection: { anchor: v.length + add.length }, userEvent: "input", scrollIntoView: true });
    ed.focus();
  } else {
    const v = ta.value, add = (v && !v.endsWith("\n") ? "\n\n" : "") + block;
    ta.insertAt(add, v.length);
    inputEd.focus();
  }
  getSelection().removeAllRanges();
  QUOTE.el.classList.remove("show"); QUOTE.q = null;
  toast("Quoted: write your reply under it");
}
let quoteTimer = 0;
document.addEventListener("selectionchange", () => { clearTimeout(quoteTimer); quoteTimer = setTimeout(placeQuote, 120); });
addEventListener("scroll", () => QUOTE.el && QUOTE.q && placeQuote(), true);
