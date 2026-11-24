"use strict";
/* ===================================================================== sheets: the ⋯ menu, New session, Settings
   iOS's own forms for the desktop's menu and dialogs: an action sheet (rows over a Cancel) and a page sheet that
   rises from the foot (Cancel · title · the one action). Content carries the colour; the chrome is hairlines. */
function closeSheets() { $$(".sheet-wrap").forEach(w => { w.classList.remove("show"); setTimeout(() => w.remove(), 260); }); }
function sheetWrap(html, cls) {
  closeSheets();
  const w = document.createElement("div");
  w.className = "sheet-wrap " + cls;
  w.innerHTML = `<div class="backdrop" data-dismiss></div>${html}`;
  document.body.appendChild(w);
  requestAnimationFrame(() => requestAnimationFrame(() => w.classList.add("show")));
  w.addEventListener("click", e => { if (e.target.closest("[data-dismiss]")) closeSheets(); });
  return w;
}
function actionSheet(title, items) {
  const w = sheetWrap(`<div class="asheet"><div class="agroup">${title ? `<div class="atitle">${esc(title)}</div>` : ""}` +
    items.map((it, i) => `<button class="arow${it.danger ? " danger" : ""}${it.sub ? " two" : ""}${it.on ? " on" : ""}" data-ai="${i}"${it.disabled ? " disabled" : ""}>${it.icon ? I(it.icon, 18) : ""}` +
      (it.sub ? `<span class="atx"><span>${esc(it.label)}</span><span class="asub">${esc(it.sub)}</span></span>` : `<span>${esc(it.label)}</span>`) +
      `${it.on !== undefined ? `<span class="acheck">${it.on ? I("check", 18) : ""}</span>` : ""}</button>`).join("") +
    `</div><button class="arow cancel" data-dismiss>Cancel</button></div>`, "as");
  w.addEventListener("click", e => {
    const b = e.target.closest("[data-ai]"); if (!b || b.disabled) return;
    closeSheets(); items[+b.dataset.ai].run();
  });
}
function pageSheet({ title, ok, body, onOk }) {
  const w = sheetWrap(`<div class="psheet" role="dialog" aria-label="${esc(title)}"><div class="phead">` +
    (ok ? `<button class="tbtn" data-dismiss>Cancel</button>` : `<span class="tbtn ghost" aria-hidden="true"></span>`) + `<div class="ptitle">${esc(title)}</div>` +
    `<button class="tbtn strong"${ok ? " data-ok" : " data-dismiss"}>${esc(ok || "Done")}</button></div>` +
    `<div class="pbody">${body}</div></div>`, "ps");
  if (onOk) w.querySelector("[data-ok]").addEventListener("click", () => onOk(w));
  return w;
}
const chipsHtml = (name, opts, value) => `<div class="chips" data-chips="${name}">` + opts.map(o =>
  `<button class="chip${o.value === value ? " on" : ""}" data-v="${esc(o.value)}">${o.icon || ""}<span>${esc(o.label)}</span></button>`).join("") + `</div>`;
function chipsValue(w, name) { const b = w.querySelector(`[data-chips="${name}"] .chip.on`); return b ? b.dataset.v : ""; }
function bindChips(w, onChange) {
  w.addEventListener("click", e => {
    const c = e.target.closest(".chip"); if (!c) return;
    const box = c.closest("[data-chips]");
    box.querySelectorAll(".chip").forEach(x => x.classList.toggle("on", x === c));
    if (onChange) onChange(box.dataset.chips, c.dataset.v);
  });
}

/* ===================================================================== New session: folder, agent, group, the first request */
const MODE_SHORT = { default: "default", acceptEdits: "accept edits", auto: "auto", plan: "plan", bypassPermissions: "bypass" };
/* The modes a harness maps (Claude Code has auto; Codex, agy, OpenCode do not). */
const modesForM = id => { const h = (S.harnesses || []).find(x => x.id === id); return Object.keys(MODE_SHORT).filter(m => !h || !h.modes || h.modes.includes(m)); };
const modeChipsHtml = (harness, mode) => chipsHtml("mode", modesForM(harness).map(value => ({ value, label: MODE_SHORT[value],
  icon: value === "bypassPermissions" ? `<span class="zap">${I("zap", 14)}</span>` : "" })), modesForM(harness).includes(mode) ? mode : "default");
async function newSession() {
  try { const d = await api("GET", "/api/harnesses"); if (d.harnesses && d.harnesses.length) S.harnesses = d.harnesses; } catch (e) { if (e instanceof NotPaired) return; }
  const last = LS.get("lastNew", { cwd: "~/", harness: "claude", mode: "acceptEdits", cwdOn: {} });
  const ups = (S.machines || []).filter(m => m.ok);
  let machine = "";
  const homeTilde = p => p.replace(/^\/(?:private\/)?(?:home\/[^/]+|Users\/[^/]+|root)(?=\/|$)/, "~");
  const onIt = (s, m) => !s.fleet && (m ? s.remote === m : !s.remote);
  const folderOf = s => homeTilde(s.remote ? s.cwd.replace(/^\/@[^/]+/, "") : s.cwd);
  const lastOn = m => m ? ((last.cwdOn || {})[m] || "") : (last.cwd || "");
  const recent = m => [...new Set([lastOn(m), ...[...S.sessions.values()].filter(x => onIt(x, m)).map(folderOf)].filter(Boolean))].slice(0, 8);
  const harnessOpts = m => (S.harnesses.length ? S.harnesses : [{ id: "claude", label: "Claude Code" }]).filter(h => !m || !h.local_only)
    .map(h => ({ value: h.id, label: h.label, icon: harnessIcon(h.id, 16) }));
  const firstHarness = m => { const o = harnessOpts(m); return (o.find(x => x.value === last.harness) || o[0]).value; };
  const gid = (S.groups.find(g => g.id === last.group) || S.groups[0] || {}).id || "";
  const w = pageSheet({
    title: "New session", ok: "Start",
    body: (ups.length ? `<div class="field"><div class="flabel">Machine</div>${chipsHtml("machine", [{ value: "", label: "This Mac" }, ...ups.map(m => ({ value: m.name, label: m.name }))], "")}</div>` : "") +
      `<div class="field"><div class="flabel">Folder</div><input id="nf-cwd" class="tinput" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="next" value="${esc(lastOn(""))}">` +
      `<div class="sugg" id="nf-sugg"></div></div>` +
      `<div class="field"><div class="flabel">Agent</div><div id="nf-h">${chipsHtml("harness", harnessOpts(""), firstHarness(""))}</div></div>` +
      `<div class="field"><div class="flabel">Group</div>${chipsHtml("group", S.groups.map(g => ({ value: g.id, label: g.name })), gid)}</div>` +
      `<div class="field"><div class="flabel">Mode</div><div id="nf-mode">${modeChipsHtml(firstHarness(""), last.mode || "acceptEdits")}</div></div>` +
      `<div class="field"><div class="flabel">First request <span class="opt">optional</span></div><textarea id="nf-text" class="tinput area" rows="3" placeholder="What should the agent do?"></textarea></div>` +
      `<div class="ferr" id="nf-err"></div>`,
    onOk: async w => {
      const cwd = $("#nf-cwd", w).value.trim(), text = $("#nf-text", w).value;
      if (!cwd) { $("#nf-err", w).textContent = "Pick a folder."; return; }
      const body = { cwd, harness: chipsValue(w, "harness"), group: chipsValue(w, "group"), mode: chipsValue(w, "mode") || "acceptEdits", ...(machine ? { machine } : {}) };
      const okb = w.querySelector("[data-ok]"); okb.disabled = true; $("#nf-err", w).textContent = "";
      let s;
      try { s = await api("POST", "/api/sessions", body); }
      catch (e) { okb.disabled = false; if (!(e instanceof NotPaired)) $("#nf-err", w).textContent = e.message; return; }
      LS.set("lastNew", { cwd: machine ? last.cwd : cwd, cwdOn: { ...(last.cwdOn || {}), ...(machine ? { [machine]: cwd } : {}) }, harness: body.harness, mode: body.mode, group: body.group });
      S.sessions.set(s.id, s); S.cards.set(s.id, []);
      if (S.seen) { S.seen[s.id] = 0; LS.set("seen", S.seen); }
      closeSheets();
      openSession(s.id);
      if (text.trim()) sendText(s.id, text).catch(() => { $("#input").value = text; autosize(); saveDraft(); renderComposer(); });
    },
  });
  const inp = $("#nf-cwd", w), sugg = $("#nf-sugg", w);
  let seq = 0;
  const showSugg = async () => {
    const q = inp.value, my = ++seq;
    let rows;
    if (document.activeElement !== inp) { sugg.innerHTML = ""; return; }
    try {
      const d = await api("GET", (machine ? `/api/remote/${machine}` : "") + "/api/complete-dir?q=" + encodeURIComponent(q || "~/"));
      rows = d.dirs || [];
    } catch { rows = []; }
    if (my !== seq) return;
    const rec = recent(machine).filter(p => !q || p.toLowerCase().includes(q.toLowerCase().replace(/\/$/, "")) && p !== q);
    const all = [...rec.map(p => ({ p, recent: true })), ...rows.filter(p => !rec.includes(p)).map(p => ({ p }))].slice(0, 12);
    sugg.innerHTML = all.map(x => `<button class="srow1" data-p="${esc(x.p)}">${I("folder", 16)}<span class="p">\u200e${esc(x.p)}\u200e</span>${x.recent ? `<span class="tag">recent</span>` : ""}</button>`).join("");
  };
  inp.addEventListener("input", showSugg);
  inp.addEventListener("focus", showSugg);
  inp.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== inp) sugg.innerHTML = ""; }, 150));
  sugg.addEventListener("pointerdown", e => e.preventDefault());  // (the field keeps the keyboard)
  sugg.addEventListener("click", e => { const b = e.target.closest("[data-p]"); if (!b) return; inp.value = b.dataset.p; inp.focus(); showSugg(); });
  const modesNow = () => { $("#nf-mode", w).innerHTML = modeChipsHtml(chipsValue(w, "harness"), chipsValue(w, "mode")); };  // (that agent's modes; the chosen one kept when it has it)
  bindChips(w, (name, v) => {
    if (name === "harness") { modesNow(); return; }
    if (name !== "machine") return;
    machine = v; inp.value = lastOn(v) || "~/";
    $("#nf-h", w).innerHTML = chipsHtml("harness", harnessOpts(v), firstHarness(v));
    modesNow();
  });
}
$("#newbtn").addEventListener("click", newSession);

/* ===================================================================== Settings: this server, the others, Desktop layout */
function serverName() {
  const h = location.hostname;
  return !h || /^[\d.]+$/.test(h) || h.includes(":") || h === "localhost" ? "This Mac" : h.split(".")[0];
}
async function settingsSheet() {
  const w = pageSheet({
    title: "Settings",
    body: `<div class="sec">This server</div><div class="list">` +
      `<div class="lrow"><span class="ic">${I("server", 18)}</span><span class="tx"><span class="t1">${esc(serverName())}</span><span class="t2">${esc(location.origin)}</span></span>` +
      `<span class="state ${S.online ? "ok" : "off"}">${S.online ? "connected" : "reconnecting"}</span></div></div>` +
      `<div class="sec">Other machines</div><div class="list" id="st-machines"><div class="lrow"><span class="tx"><span class="t2">Loading…</span></span></div></div>` +
      `<div class="note">Sessions on these machines come through this server. Add or remove them in Orbit on the Mac: Settings → Machines.</div>` +
      `<div class="sec">Layout</div><div class="list"><button class="lrow tap" id="st-desktop"><span class="ic">${I("monitor", 18)}</span>` +
      `<span class="tx"><span class="t1">Desktop layout</span><span class="t2">The Mac's page on this phone; open /m/ to come back</span></span><span class="go">${I("chev", 14)}</span></button></div>` +
      `<div class="sec">About</div><div class="list"><div class="lrow"><span class="ic">${I("info", 18)}</span><span class="tx"><span class="t1">Orbit for iPhone ${esc(M_VERSION)}</span><span class="t2" id="st-ver">Server …</span></span></div></div>`,
  });
  $("#st-desktop", w).addEventListener("click", () => { try { localStorage.setItem("orbit.forceDesktop", "1"); } catch {} location.href = "/"; });
  api("GET", "/api/version").then(v => { $("#st-ver", w).textContent = `Server ${v.version} · API ${v.api}`; }).catch(() => { $("#st-ver", w).textContent = "Server unreachable"; });
  let rs = null;
  try { rs = (await api("GET", "/api/remotes")).remotes; } catch {}
  const list = rs || (S.machines || []).map(m => ({ name: m.name, ok: m.ok, error: m.error }));
  const box = $("#st-machines", w); if (!box) return;
  box.innerHTML = list.length ? list.map(m => `<div class="lrow"><span class="ic">${I("server", 18)}</span><span class="tx"><span class="t1">${esc(m.name)}</span>` +
    `<span class="t2">${esc(m.url || "")}${m.sessions ? `${m.url ? " · " : ""}${m.sessions.length} session${m.sessions.length === 1 ? "" : "s"}` : ""}</span></span>` +
    `<span class="state ${m.ok ? "ok" : "off"}">${m.ok ? "reachable" : esc(m.error === "connecting" ? "connecting" : "unreachable")}</span></div>`).join("")
    : `<div class="lrow"><span class="tx"><span class="t2">None</span></span></div>`;
}
$("#setbtn").addEventListener("click", settingsSheet);

/* ===================================================================== images: full screen, zoomable
   The image fills the screen (fitted, never past its own size). Pinch zooms it about the fingers (1× to 4× its own
   pixels), one finger pans it while zoomed, a double tap zooms in at that point (or back out). At 1×: a tap closes, a
   drag down slides it away (the backdrop fades; let go past 90px: closed). Only the image moves: the viewer takes
   every touch (touch-action: none, and iOS's gesture events are cancelled), so the page under it never zooms. */
function openLightbox(src) {
  const w = document.createElement("div");
  w.className = "lightbox";
  w.innerHTML = `<img src="${esc(src)}" alt="" draggable="false"><button class="lbx" aria-label="Close">${I("x", 18)}</button>`;
  document.body.appendChild(w);
  const img = $("img", w);
  const V = { s: 1, x: 0, y: 0, w: 0, h: 0, max: 1, W: 0, H: 0 };
  const apply = (anim = false) => {
    img.style.transition = anim ? "transform .22s var(--ease)" : "none";
    img.style.transform = `translate(${V.x}px, ${V.y}px) scale(${V.s})`;
  };
  const clampV = () => {  // zoomed: no gap at an edge; smaller than the screen: centred
    const sw = V.w * V.s, sh = V.h * V.s;
    V.x = sw <= V.W ? (V.W - sw) / 2 : Math.min(0, Math.max(V.W - sw, V.x));
    V.y = sh <= V.H ? (V.H - sh) / 2 : Math.min(0, Math.max(V.H - sh, V.y));
  };
  const fit = () => {
    const nw = img.naturalWidth || 1, nh = img.naturalHeight || 1;
    V.W = w.clientWidth; V.H = w.clientHeight;
    const k = Math.min(V.W / nw, V.H / nh, 1);
    V.w = nw * k; V.h = nh * k; V.max = Math.max(2.5, 4 / k);
    img.style.width = V.w + "px"; img.style.height = V.h + "px";
    V.s = 1; clampV(); apply();
  };
  const zoomAt = (s, fx, fy) => {  // to scale s, the point under (fx, fy) staying there
    s = Math.min(V.max, Math.max(1, s));
    V.x = fx - (fx - V.x) * s / V.s; V.y = fy - (fy - V.y) * s / V.s; V.s = s;
  };
  if (img.complete && img.naturalWidth) fit(); else img.addEventListener("load", fit);
  const onResize = () => fit();
  addEventListener("resize", onResize);
  const close = () => {
    removeEventListener("resize", onResize);
    w.classList.add("out"); setTimeout(() => w.remove(), 180);
  };
  $(".lbx", w).addEventListener("click", e => { e.stopPropagation(); close(); });
  const stop = e => e.preventDefault();  // (iOS: its own pinch on the page)
  for (const t of ["gesturestart", "gesturechange", "gestureend"]) w.addEventListener(t, stop);
  const G = { mode: "", t0: 0, x0: 0, y0: 0, vx: 0, vy: 0, d0: 0, s0: 1, moved: false, lastTap: 0, tapTimer: 0 };
  const dist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  w.addEventListener("touchstart", e => {
    if (e.target.closest(".lbx")) return;
    e.preventDefault();
    const ts = e.touches;
    if (ts.length >= 2) {
      G.mode = "pinch"; G.d0 = dist(ts[0], ts[1]); G.s0 = V.s; G.vx = V.x; G.vy = V.y;
      G.fx = (ts[0].clientX + ts[1].clientX) / 2; G.fy = (ts[0].clientY + ts[1].clientY) / 2; G.moved = true;
    } else {
      G.mode = "pan"; G.x0 = ts[0].clientX; G.y0 = ts[0].clientY; G.vx = V.x; G.vy = V.y; G.moved = false; G.t0 = Date.now();
    }
  }, { passive: false });
  w.addEventListener("touchmove", e => {
    e.preventDefault();
    const ts = e.touches;
    if (G.mode === "pinch" && ts.length >= 2) {
      const fx = (ts[0].clientX + ts[1].clientX) / 2, fy = (ts[0].clientY + ts[1].clientY) / 2;
      V.s = G.s0; V.x = G.vx + fx - G.fx; V.y = G.vy + fy - G.fy;  // (the fingers' midpoint moves the image too)
      zoomAt(G.s0 * dist(ts[0], ts[1]) / G.d0, fx, fy);
      clampV(); apply();
    } else if (G.mode === "pan" && ts.length === 1) {
      const dx = ts[0].clientX - G.x0, dy = ts[0].clientY - G.y0;
      if (Math.abs(dx) + Math.abs(dy) > 6) G.moved = true;
      if (V.s > 1.01) { V.x = G.vx + dx; V.y = G.vy + dy; clampV(); apply(); }
      else if (G.moved) {  // at 1×: drag it away (down or up)
        V.y = G.vy + dy; V.x = G.vx + dx * .3; apply();
        w.style.backgroundColor = `rgba(0,0,0,${Math.max(.2, 1 - Math.abs(dy) / 400)})`;
      }
    }
  }, { passive: false });
  w.addEventListener("touchend", e => {
    e.preventDefault();
    if (e.touches.length === 1 && G.mode === "pinch") {  // one finger left: it pans on from here
      G.mode = "pan"; G.x0 = e.touches[0].clientX; G.y0 = e.touches[0].clientY; G.vx = V.x; G.vy = V.y; return;
    }
    if (e.touches.length) return;
    const was = G.mode; G.mode = "";
    if (was === "pan" && V.s <= 1.01 && G.moved) {
      if (Math.abs(V.y - G.vy) > 90) { close(); return; }
      w.style.backgroundColor = ""; clampV(); apply(true); return;
    }
    if (was === "pinch") { if (V.s < 1.05) { V.s = 1; } clampV(); apply(true); return; }
    if (was === "pan" && !G.moved) {  // a tap: double → zoom in there / out; single (at 1×) → close
      const t = e.changedTouches[0], now = Date.now();
      if (now - G.lastTap < 280) {
        clearTimeout(G.tapTimer); G.lastTap = 0;
        if (V.s > 1.01) V.s = 1; else zoomAt(Math.min(V.max, 2.5), t.clientX, t.clientY);
        clampV(); apply(true);
      } else {
        G.lastTap = now;
        G.tapTimer = setTimeout(() => { if (V.s <= 1.01) close(); }, 280);
      }
    }
  }, { passive: false });
  // (a mouse, e.g. the desktop's Safari on the phone page: a click closes, a double click zooms, the wheel zooms)
  w.addEventListener("click", e => { if (!e.target.closest(".lbx") && !("ontouchstart" in window)) { if (V.s <= 1.01) close(); } });
  w.addEventListener("dblclick", e => { if (V.s > 1.01) V.s = 1; else zoomAt(2.5, e.clientX, e.clientY); clampV(); apply(true); });
  w.addEventListener("wheel", e => { e.preventDefault(); zoomAt(V.s * Math.exp(-e.deltaY / 300), e.clientX, e.clientY); clampV(); apply(); }, { passive: false });
}

/* ===================================================================== not paired (401) */
function showUnpaired() {
  if (document.body.classList.contains("unpaired")) return;
  document.body.classList.add("unpaired");
  closeSheets();
  closeEvents();
}
$("#retry").addEventListener("click", () => location.reload());
