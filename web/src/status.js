"use strict";
/* ===================================================================== rendering: title bar, tabs, status */
/* A session's mark (ui.md "Status marks"): the same drawing wherever a session is listed. */
const stateMark = (s, extra = "") => { const st = sessionStatus(s); return `<span class="si ${st.cls}${extra}" title="${esc(st.title)}">${st.icon}</span>`; };
const dotClass = st => ({ ready: "ready", working: "working", "not ready": "notready", stopped: "stopped", starting: "starting", offline: "notready" }[st] || "stopped");
function renderTabs() {
  if (DRAG.active) { DRAG.pending.add(renderTabs); return; }  // (a drag is moving the tabs: after it)
  if (EMBED) return;
  const gp = $("#grouppill");
  const gs = S.group && S.group !== FAV ? groupOf(S.group) || {} : {};
  gp.innerHTML = (S.group === FAV ? `<span class="star">${I("star", 13)}</span><span class="nm">Favorites</span>` : `${groupIconHtml(S.group, 14)}<span class="nm">${esc(S.group ? groupPath(S.group) : "Orbit")}</span>`) +
    `<span class="gcaret" title="Another group">${I("caret", 12)}</span>`;
  gp.classList.toggle("active", S.file === HOME);  // (the group's home is in front: the pill is the tab)
  gp.style.setProperty("--gc", gs.color || "");
  // every tab of a coloured group wears its home's tint at the top of the page (the home draws its own)
  const pane = $("#pane");
  pane.classList.toggle("gtint", !!gs.color && S.file !== HOME);
  pane.style.setProperty("--gc", gs.color || "");
  const html = key => {
    if (key.startsWith("f:")) return fileTabHtml(fileTabs().find(t => t.path === key.slice(2)));
    const id = key.slice(2);
    const s = S.sessions.get(id);
    const active = !S.file && id === S.current;
    return `<div class="tab${active ? " active" : ""}" role="tab" data-sid="${id}" title="${esc(s.name)} · ${esc(s.state)} · ${esc(tilde(s.cwd))}">
      ${stateMark(s)}<span class="name">${esc(s.name)}</span>${machineTag(s)}${s.attention ? `<span class="attn" title="Needs you in its terminal: ${esc(s.attention)} (⌘T)">!</span>` : ""}${S.unread.has(id) && !active ? '<span class="udot" title="New answer"></span>' : ""}
      <span class="x" data-close="${id}" title="${S.group === FAV && s.favorite ? "Remove from Favorites (⌘W): it stays open in its group" : "Close (⌘W)"}">${I("x", 12)}</span></div>`;
  };
  const fileTabHtml = t => {
    const ed = EDITORS.get(t.path);
    const cls = "tab" + (S.file === t.path ? " active" : "") + (t.pinned ? "" : " preview") + (ed && ed.dirty ? " dirty" : "");
    return `<div class="${cls}" role="tab" data-file="${esc(t.path)}" title="${isView(t.path) ? esc(viewInfo(t.path).title) : esc(tilde(t.path)) + (t.pinned ? "" : " · preview (editing pins it)")}">
      <span class="fi">${isView(t.path) ? I(viewInfo(t.path).icon, 13) : fileIcon(t.path)}</span><span class="name">${esc(isView(t.path) ? viewInfo(t.path).title : isNote(t.path) ? noteTitle(t.path) : basename(t.path))}</span>${t.unread && S.file !== t.path ? '<span class="udot" title="Unread"></span>' : ""}
      <span class="x" data-closefile="${esc(t.path)}" title="Close (⌘W)">${I("x", 12)}</span></div>`;
  };
  $("#tabs").innerHTML = allTabKeys().map(html).join("") + `<button class="ibtn addtab" id="addtab" title="New session (⌘N)">${I("plus")}</button>`;
  tabsWrapped();
}
function renderStatus() {
  if (typeof USAGE !== "undefined" && USAGE.harnesses) renderUsage();  // (the usage follows the session in front)
  const s = cur();
  const ed = S.file ? EDITORS.get(S.file) : null;
  if (S.file) {
    const st = !ed || ed.kind === "view" ? "" : ed.conflict ? "conflict" : ed.dirty ? "modified" : ed.saving ? "saving…" : "saved";
    $("#sb-state").innerHTML = st ? `<span class="dot ${ed && ed.conflict ? "notready" : ed && ed.dirty ? "working" : "ready"}"></span><span class="sw">${esc(st)}</span>` : "";
    $("#sb-state").title = "";
    $("#sb-harness").textContent = ed && ed.ed ? ed.ed.language : isView(S.file) ? viewInfo(S.file).title : (ed ? ed.kind : "");
    $("#sb-harness").style.display = "";
    $("#sb-extra").textContent = s ? s.name : "";
    $("#sb-extra").style.display = s ? "" : "none";
  } else {
    $("#sb-state").innerHTML = s ? `${stateMark(s)}<span class="sw">${esc(s.state)}${s.queued ? ` · ${s.queued} queued` : ""}</span>` : esc(S.group ? groupPath(S.group) : "no session");
    $("#sb-state").title = s && s.error ? s.error : "";
    $("#sb-harness").textContent = s ? harnessLabel(s.harness) : "";
    $("#sb-harness").style.display = s ? "" : "none";
    $("#sb-extra").style.display = "none";
  }
  const root = S.file && !isView(S.file) ? dirname(S.file) : s ? s.cwd : "";
  $("#sb-folder").innerHTML = root ? `${I("folder", 13)}<span>${esc(tilde(root))}</span>` : "";
  $("#modechip").innerHTML = s ? (s.mode === "bypassPermissions" ? `<span class="zap">${I("zap", 13)}</span>` : "") + esc(MODE_SHORT[s.mode] || s.mode) + I("caret", 10) : "";
  $("#modechip").title = s ? `Permission mode: ${modeLabel(s.harness, s.mode)}. Click or /mode to change it (the agent restarts on the same conversation)` : "";
  $("#modechip").style.display = s ? "" : "none";
  const bits = [];
  if (s) {
    bits.push((modelName(s.model_seen || s.model) || "default model") + (s.effort ? " " + s.effort[0].toUpperCase() + s.effort.slice(1) : ""));
    if (s.context) bits.push("ctx " + kfmt(s.context));
    if (s.compactions) bits.push("⟲" + s.compactions);
  }
  $("#runon").innerHTML = bits.map(b => `<span>${esc(b)}</span>`).join("");
  // The keys that send: while it works ⌘⏎ queues (and ⌘⇧⏎ steers, where the harness can); otherwise ⌘⏎ sends.
  $("#runhint").textContent = !s || s.remote && s.state === "offline" ? "" : s.state === "working" ? (s.steers ? "⌘⏎ queue · ⌘⇧⏎ steer" : "⌘⏎ queue") : "⌘⏎ send";
  $("#runon").title = "What the request runs on: model · context the last model call read · compactions";
  $("#composer").style.display = s ? "" : "none";
  const badge = $("#bellbtn .badge");
  if (badge) badge.textContent = S.notes.length ? String(Math.min(S.notes.length, 99)) : "";
  if (!EMBED && renderStatus.badge !== S.notes.length) {  // the app shell shows it on the Dock icon
    renderStatus.badge = S.notes.length;
    api("POST", "/api/badge", { count: S.notes.length }).catch(() => { renderStatus.badge = null; });
  }
  document.title = EMBED ? sideTitle(EMBED) : S.file ? `${isView(S.file) ? viewInfo(S.file).title : basename(S.file)} — Orbit` : s ? `${s.name} — Orbit` : "Orbit";
}
/* ===================================================================== plan usage (status bar) */
let USAGE = { harnesses: [] };
const inFmt = at => { if (!at) return ""; let m = Math.max(0, Math.round((at * 1000 - Date.now()) / 60000)); const d = Math.floor(m / 1440); m -= d * 1440; const h = Math.floor(m / 60); m -= h * 60;
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`; };
const heat = u => u >= 90 ? "hot" : u >= 75 ? "warm" : "";
const ubar = u => `<span class="ubar ${heat(u)}"><i style="width:${Math.min(100, Math.max(0, u))}%"></i></span>`;
function renderUsage() {  // every harness, always, in a steady order (the session's own one a shade darker); the popover has the details
  const b = $("#sb-usage"); if (!b) return;
  // (a plugin's agent may add a quota line, plugin API 1.3: "8 USD left", after its meter or alone)
  const s = cur(), hs = (USAGE.harnesses || []).filter(h => (h.limits || []).length || h.quota);
  const v = voiceUse();
  b.hidden = !hs.length && !v;
  b.innerHTML = `<span class="lbl">Used</span>` + hs.map(h => {
    const main = (h.limits || [])[0], q = h.quota ? `<span class="uq">${esc(h.quota)}</span>` : "";
    return `<span class="h${s && h.id === s.harness ? " mine" : ""}"><span class="hn">${esc(h.label)}</span>` + (main ? `${ubar(main.used)}<span class="${heat(main.used)}">${Math.round(main.used)}%</span>` +
      `${main.resets_at ? `<span class="rs">${inFmt(main.resets_at)}</span>` : ""}` : "") + `${q}</span>`;
  }).join("") + (v ? `<span class="h ov"><span class="hn">OpenAI</span>` + (v.b ? `${ubar(v.pct)}<span class="${heat(v.pct)}">${usd(v.b.left)} left</span>`
    : `<span>${v.m.estimated ? "≈ " : ""}${usd(v.m.cost)}</span>`) + `</span>` : "");
  b.title = (v ? [`OpenAI (voice): ${usd(v.m.cost)} this month, ${v.m.calls} call${v.m.calls === 1 ? "" : "s"}` + (v.b ? `; ${usd(v.b.left)} of ${usd(v.b.given)} left (≈ ${vmin(v.b.active_min)} of talking)` : "")] : []).concat(hs.map(h => `${h.label}${h.plan ? " " + h.plan : ""}: ` + [...(h.limits || []).map(l => `${Math.round(l.used)}% of ${l.label || "its plan"}${l.resets_at ? " (resets in " + inFmt(l.resets_at) + ")" : ""}`), ...(h.quota ? [h.quota] : [])].join(", "))).join("\n") + "\n(click: details)";
}
/* OpenAI (GPT Live voice): what Orbit's calls cost this month, by their own usage (an API key cannot read the
   account), and the balance left when the user gave one in Settings → Voice. Shown once there is a call or a balance. */
const usd = x => "$" + (x < 1000 ? x.toFixed(2) : Math.round(x));
const vmin = m => { m = Math.round(m); return m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}` : `${m} min`; };
function voiceUse() {
  const u = USAGE.voice; if (!u || !u.month) return null;
  const m = u.month, b = u.budget;
  if (!m.calls && !b) return null;
  return { m, b, pct: b && b.given ? Math.min(100, b.spent / b.given * 100) : 0 };
}
async function loadUsage() {
  try {
    const [u, v] = await Promise.all([api("GET", "/api/usage"), api("GET", "/api/live/usage").catch(() => null)]);
    USAGE = { ...u, voice: v };
    renderUsage(); if (document.getElementById("usagepop")) usagePop(true);
  } catch {}
}
function usagePop(keep = false) {
  let pop = document.getElementById("usagepop");
  if (pop && !keep) { pop.remove(); return; }
  if (!pop) { pop = document.createElement("div"); pop.id = "usagepop"; document.body.appendChild(pop); }
  const hs = USAGE.harnesses || [];
  pop.innerHTML = `<div class="uh"><b>Usage</b><span class="sub">· all harnesses</span><button class="btn sm" data-urefresh>Refresh</button></div>` +
    (hs.length ? hs.map(h => `<div class="ur"><div class="t"><b>${esc(h.label)}</b>${h.plan ? `<span class="plan">· ${esc(h.plan)}</span>` : ""}` +
      `<span class="rs">${h.limits && h.limits[0] && h.limits[0].resets_at ? "Resets in " + inFmt(h.limits[0].resets_at) : h.error ? esc(h.error) : ""}</span></div>` +
      limRows(h) +
      (h.quota ? `<span class="lim uq">${esc(h.quota)}</span>` : "") +
      `</div>`).join("") : `<div class="ur">No harness reported its usage yet.</div>`) + voicePopRow() +
    `<div class="foot">Claude: its /usage (no model call) · Codex: its last turn's rate limits${(USAGE.harnesses || []).some(h => h.id === "agy") ? " · Antigravity: its /usage" : ""}${voiceUse() ? " · OpenAI: Orbit's own calls, by what GPT Live reported (an API key cannot read the account)" : ""}${USAGE.at ? " · checked " + ago(USAGE.at) : ""}</div>`;
  const r = $("#sb-usage").getBoundingClientRect(), k = rectScale();
  pop.style.left = Math.max(8, Math.min(innerWidth / S.zoom - 390, r.left / k)) + "px";
  pop.style.bottom = (innerHeight / S.zoom - r.top / k + 6) + "px";
}
/* A harness's windows in the popover: inline; more than two (Antigravity: two model groups × week and 5 h) in a grid,
   each group's on its own row. */
function limRows(h) {
  const ls = h.limits || [];
  if (!ls.length) return h.quota ? "" : `<span class="lim">no figures reported</span>`;
  const one = x => `<span class="lim" title="${esc(x.window)}${x.resets_text ? " · resets " + esc(x.resets_text) : ""}${x.resets_at ? " · resets in " + inFmt(x.resets_at) : ""}"><span class="k">${esc(x.label)}</span>${ubar(x.used)}<span class="${heat(x.used)}">${Math.round(x.used)}%</span></span>`;
  return ls.length > 2 ? `<div class="lims">${[...ls].sort((a, b) => a.label.localeCompare(b.label)).map(one).join("")}</div>` : ls.map(one).join("");
}
function voicePopRow() {
  const v = voiceUse(); if (!v) return "";
  const m = v.m, b = v.b, dur = sec => sec >= 3600 ? vmin(sec / 60) : sec >= 60 ? `${Math.round(sec / 60)} min` : `${sec} s`;
  return `<div class="ur"><div class="t"><b>OpenAI</b><span class="plan">· voice (GPT Live)</span>` +
    `<span class="rs">${b ? `≈ ${vmin(b.active_min)} of talking left` : ""}</span></div>` +
    `<span class="lim"><span class="k">This month</span>${m.estimated ? "≈ " : ""}${usd(m.cost)} · ${m.calls} call${m.calls === 1 ? "" : "s"} · ${dur(m.active_s)} talking${m.standby_s ? ` · ${dur(m.standby_s)} standby` : ""}</span>` +
    (b ? `<div><span class="lim"><span class="k">Balance</span>${ubar(v.pct)}<span class="${heat(v.pct)}">${usd(b.left)} of ${usd(b.given)} left</span></span></div>`
       : `<div class="lim vhint">Give your balance in Settings → Voice to see what is left</div>`) +
    `</div>`;
}
$("#sb-usage").addEventListener("click", () => usagePop());
document.addEventListener("mousedown", e => {
  const pop = document.getElementById("usagepop");
  if (!pop || pop.contains(e.target) || e.target.closest("#sb-usage")) return;
  pop.remove();
});
document.addEventListener("click", e => {
  if (e.target.closest && e.target.closest("[data-urefresh]")) {
    api("POST", "/api/usage/refresh", {}).then(() => { toast("Checking usage…"); setTimeout(loadUsage, 4000); setTimeout(loadUsage, 12000); }).catch(err => toast(err.message));
  }
});
if (!EMBED) { loadUsage(); setTimeout(loadUsage, 6000); setTimeout(loadUsage, 20000); setInterval(loadUsage, 60000); setInterval(renderUsage, 30000); }

function renderAll() { renderTabs(); renderStatus(); renderLeft(); renderReviewBadge(); }

/* The active tab's underline sits on the title bar's foot (it joins the content below); when the tabs wrap to a
   second row, it stays under the tab itself. */
function tabsWrapped() {
  const tabs = $("#tabs"), ts = tabs.querySelectorAll(".tab");
  const first = ts[0], last = ts[ts.length - 1];
  const mid = tab => tab.offsetTop + tab.offsetHeight / 2;
  tabs.classList.toggle("wrapped", !!first && mid(last) - mid(first) > first.offsetHeight / 2);
}
// Applying the wrapped style changes height, so update outside the observer's layout pass.
if (!EMBED) new ResizeObserver(() => requestAnimationFrame(tabsWrapped)).observe($("#tabs"));
