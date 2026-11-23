"use strict";
/* ===================================================================== Sessions (home)
   The desktop's left panel for a phone: the user's groups in their order (Favorites first, the Fleet last), each
   session a two-line row (mark · agent · name · machine · when; then what it last said or is doing). A "Working now"
   strip on top while any session works; pull down to refresh; a dot for an answer not seen yet. */

/* The unread dot: a final answer newer than the last one seen here. A session seen for the first time is "seen". */
function seenInit() {
  if (S.seen) return;
  S.seen = {};
  for (const s of S.sessions.values()) S.seen[s.id] = (s.last && s.last.time) || 0;
  LS.set("seen", S.seen);
}
function isUnread(s) {
  if (!(s.id in S.seen)) { S.seen[s.id] = (s.last && s.last.time) || 0; LS.set("seen", S.seen); return false; }
  return !!(s.last && s.last.time && s.last.time > S.seen[s.id] + 0.001 && s.id !== S.current);
}
function markSeen(sid) {
  const s = S.sessions.get(sid); if (!s) return;
  const t = (s.last && s.last.time) || 0;
  if (S.seen[sid] !== t) { S.seen[sid] = t; LS.set("seen", S.seen); }
}
function lastMoved(s) { return isMoving(s) ? "now" : ago((s.last && s.last.time) || s.created); }
function sessionPreview(s) {
  if (s.state === "working") {
    const c = S.active.get(s.id);
    return { text: c ? "Working: " + plain(c.text) : "Working…" };
  }
  if (s.agents > 0) return { text: `${agentsWord(s.agents)} working` + (s.last && s.last.text ? ": " + plain(s.last.text) : "") };
  if (s.last && s.last.text) return { text: (s.last.state === "error" ? "Failed: " : "") + plain(s.last.text) };
  return { text: tilde(s.cwd), dim: true };
}
const elapsedOf = sid => { const c = S.active.get(sid); return c && c.started ? c.started : 0; };

function listViews() {
  const all = [...S.sessions.values()];
  const views = [];
  const favs = all.filter(s => s.favorite && !s.fleet);
  if (favs.length) views.push({ gid: "fav", name: "Favorites", star: true, ss: favs });
  for (const g of S.groups) {
    const ss = all.filter(s => s.group === g.id && !s.fleet);
    if (ss.length) views.push({ gid: g.id, name: g.name, ss });
  }
  const known = new Set(S.groups.map(g => g.id));
  const lost = all.filter(s => !s.fleet && !known.has(s.group));  // (a group this server does not list: still shown)
  if (lost.length) views.push({ gid: "other", name: "Other", ss: lost });
  const fleet = all.filter(s => s.fleet);
  if (fleet.length) views.push({ gid: "fleet", name: "Fleet", fleet: true, ss: fleet });
  return views;
}
function rowHtml(s) {
  const pv = sessionPreview(s), unread = isUnread(s), stopped = s.state === "stopped";
  return `<button class="srow${unread ? " unread" : ""}${stopped ? " stopped" : ""}" data-open="${esc(s.id)}">` +
    `<span class="c-mark">${stateMark(s)}</span><span class="c-icon">${harnessIcon(s.harness, 16)}</span>` +
    `<span class="l1"><span class="nm">${esc(sessionTitle(s))}</span>${s.favorite ? `<span class="star">${I("star", 12)}</span>` : ""}${machineTag(s)}` +
    `${unread ? '<span class="udot" aria-label="New answer"></span>' : ""}<span class="tm">${esc(lastMoved(s))}</span></span>` +
    `<span class="l2${pv.dim ? " dim" : ""}">${esc(pv.text)}</span></button>`;
}
function workingStripHtml() {
  const ws = [...S.sessions.values()].filter(s => isMoving(s) || s.attention);
  if (!ws.length) return "";
  return `<section class="wstrip"><div class="shead"><span class="gn">Working now</span><span class="cnt">${ws.length}</span></div>` +
    `<div class="wrow">${ws.map(s => {
      const t0 = s.state === "working" ? elapsedOf(s.id) : 0;  // (only subagents at work: how many, no request's time)
      return `<button class="wchip" data-open="${esc(s.id)}">${stateMark(s)}${harnessIcon(s.harness, 16)}<span class="nm">${esc(sessionTitle(s))}</span>` +
        `${t0 ? `<span class="el" data-t0="${t0}">${dur(Date.now() / 1000 - t0)}</span>` : s.state !== "working" && s.agents > 0 ? `<span class="el">${agentsWord(s.agents)}</span>` : ""}</button>`;
    }).join("")}</div></section>`;
}
function renderList() {
  if (!S.loaded) return;
  seenInit();
  const views = listViews();
  const box = $("#slist");
  if (!views.length) {
    box.innerHTML = `<div class="empty"><h2>No session yet</h2>Start one with <b>+</b> above: pick a folder, an agent and what to ask.</div>`;
    return;
  }
  box.innerHTML = workingStripHtml() + views.map(v => {
    const shut = S.folded.has(v.gid), working = v.ss.filter(isMoving).length;
    return `<section class="grp${shut ? " shut" : ""}"><button class="shead" data-fold="${esc(v.gid)}" aria-expanded="${!shut}">` +
      `${v.star ? `<span class="gstar">${I("star", 12)}</span>` : v.fleet ? `<span class="gstar">${I("zap", 12)}</span>` : ""}<span class="gn">${esc(v.name)}</span>` +
      `<span class="chev">${I("chevd", 12)}</span><span class="cnt">${working ? `${working}/` : ""}${v.ss.length}</span></button>` +
      (shut ? "" : `<div class="rows">${v.ss.map(rowHtml).join("")}</div>`) + `</section>`;
  }).join("");
  updateTitleBar();
  renderTabbar();  // (its count of answers not seen yet)
}
/* The big title gives way to the small one in the bar when it scrolls under it (iOS's large title). */
function updateTitleBar() {
  const sc = $("#lscroll"), big = $("#bigtitle");
  const under = big ? sc.scrollTop > big.offsetTop + big.offsetHeight - 8 : false;
  $("#vlist .nav").classList.toggle("scrolled", under);
}
$("#lscroll").addEventListener("scroll", updateTitleBar, { passive: true });
$("#slist").addEventListener("click", e => {
  const f = e.target.closest("[data-fold]");
  if (f) { const g = f.dataset.fold; S.folded.has(g) ? S.folded.delete(g) : S.folded.add(g); LS.set("folded", [...S.folded]); renderList(); return; }
  const o = e.target.closest("[data-open]");
  if (o) openSession(o.dataset.open);
});

/* ===================================================================== pull to refresh
   A list's own (the Sessions list's, a plugin's list): the native bounce is off (overscroll-behavior), so a pull from
   the top moves the list down after the finger (damped); past the line, letting go runs `refresh` (the spinner stays
   until it is done, half a second at least). */
const PULL_SVG = '<svg width="22" height="22" viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.25" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-dasharray="24 9"/></svg>';
function addPull(sc, refresh) {
  const P = { y0: null, d: 0, busy: false, LINE: 64, HOLD: 52 };
  if (!$(".pull", sc)) sc.insertAdjacentHTML("afterbegin", `<div class="pull" aria-hidden="true">${PULL_SVG}</div>`);
  sc.classList.add("haspull");
  const ind = $(".pull", sc), inner = $(".inner", sc);
  const set = (d, anim) => {
    P.d = d;
    inner.style.transition = ind.style.transition = anim ? "transform .25s ease" : "none";
    inner.style.transform = d ? `translateY(${d}px)` : "";
    ind.style.transform = `translateY(${Math.min(d, P.HOLD + 12) - 40}px)`;
    ind.style.opacity = Math.min(1, d / P.LINE);
    ind.querySelector("svg").style.transform = P.busy ? "" : `rotate(${Math.round(d / P.LINE * 270)}deg)`;
    ind.classList.toggle("armed", d >= P.LINE || P.busy);
  };
  sc.addEventListener("touchstart", e => {
    P.y0 = !P.busy && sc.scrollTop <= 0 && e.touches.length === 1 ? e.touches[0].clientY : null;
  }, { passive: true });
  sc.addEventListener("touchmove", e => {
    if (P.y0 == null) return;
    const dy = e.touches[0].clientY - P.y0;
    if (dy <= 0) { if (P.d) set(0); return; }
    if (sc.scrollTop > 0) { P.y0 = null; return; }
    e.preventDefault();
    set(Math.round(dy < 40 ? dy * .6 : 24 + (dy - 40) * .4));
  }, { passive: false });
  sc.addEventListener("touchend", async () => {
    if (P.y0 == null) return;
    P.y0 = null;
    if (P.d < P.LINE) { set(0, true); return; }
    P.busy = true;
    ind.classList.add("busy");
    set(P.HOLD, true);
    const t0 = Date.now();
    try { await refresh(); } catch {}
    await new Promise(r => setTimeout(r, Math.max(0, 500 - (Date.now() - t0))));  // (long enough to be seen)
    P.busy = false;
    ind.classList.remove("busy");
    set(0, true);
  });
}
addPull($("#lscroll"), async () => { await loadAll(); reconnect(); });
