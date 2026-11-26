"use strict";
/* ===================================================================== boot */
$("#lefttog").innerHTML = I("menu", 14);
$("#lefttog").addEventListener("click", () => { S.left.shown = !S.left.shown; S.filter = ""; applyLeft(); saveUi(); if (S.left.shown) focusPanel(); else focusMain(); });
$("#morebtn").innerHTML = I("more", 14);
$("#morebtn").addEventListener("click", moreMenu);
$("#bellbtn").innerHTML = I("bell", 14) + '<span class="badge"></span>';
$("#bellbtn").addEventListener("click", notifications);
$("#sbmeet").addEventListener("click", () => meetingsMenu());
$("#send").innerHTML = I("up", 16);
$("#findbar [data-f='-1']").innerHTML = I("up", 13);
$("#findbar [data-f='1']").innerHTML = I("down", 13);
$("#findbar [data-f='close']").innerHTML = I("x", 12);
setZoom(S.zoom, false);
(async () => {
  try { const d = await api("GET", "/api/sessions"); S.machines = d.machines || []; setSessions(d.sessions, d.groups); } catch {}
  try { const sc = await api("GET", "/api/schedules"); S.schedules = sc.schedules; S.schedArchived = sc.archived || []; S.settings = await api("GET", "/api/settings"); } catch {}
  api("GET", "/api/harnesses").then(d => { if (d.harnesses && d.harnesses.length) { S.harnesses = d.harnesses; renderStatus(); } }).catch(() => {});
  if (!EMBED && S.settings.calendar) loadCalToday();
  await loadVault();
  if (EMBED) {
    if (EMBED.kind === "pview") await EMBED_PUI;  // a plugin's view beside: what the plugins declare first
    S.file = EMBED.kind === "file" || EMBED.kind === "pview" ? EMBED.path : null;
    if (S.file) S.ftabs = { [S.group]: [{ path: EMBED.path, pinned: true }] };
    if (EMBED.kind === "file" && EMBED.line) S.reveal = { path: EMBED.path, line: EMBED.line };
    if (EMBED_REVIEW) loadReview();
    if (EMBED_X) loadX();
    showMain(); renderStatus();
    focusMain();
    connect();
    return;
  }
  applyLeft();
  const key = tabKey();
  if (key) { S.hist = [{ k: key, g: S.group }]; S.histPos = 0; }
  showCurrent(false);
  const f = LS.get("focus", "input");
  if (f === "left" && S.left.shown) focusPanel();
  else if (f === "side" && S.side) focusSide();
  else if (f === "conv") conv.focus({ preventScroll: true });
  else focusMain();
  connect();
})();
