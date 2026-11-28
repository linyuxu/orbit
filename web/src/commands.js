"use strict";
/* ===================================================================== commands */
/* "Default" says what it ran last (an alias or the harness's own default, as its start-up reported it). */
const defaultModelLabel = h => h.default_seen ? `Default · ${modelName(h.default_seen)} (${h.default_seen}, last run)` : "Default (the harness's own setting)";
const modelsFor = id => { const h = S.harnesses.find(x => x.id === id) || { models: [] }; return [{ value: "", label: defaultModelLabel(h) }, ...(h.models || [])]; };
const effortsFor = id => { const h = S.harnesses.find(x => x.id === id) || {}; return [{ value: "", label: "Default (the harness's own setting)" }, ...((h.efforts || []).map(e => ({ value: e, label: e })))]; };
const MODE_SHORT = { default: "default", acceptEdits: "accept edits", auto: "auto", plan: "plan", bypassPermissions: "bypass" };
const MODE_LABELS = { default: "default · the agent's own setting (Claude Code: your permissions.defaultMode)",
  acceptEdits: "accept edits · file edits approved, other tools refused", auto: "auto · Claude Code's classifier approves what is safe, refuses the rest",
  plan: "plan · read only, proposes a plan", bypassPermissions: "bypass · auto-approve every tool" };
/* The modes a harness maps (/api/harnesses: Claude Code has auto, Codex, agy and OpenCode do not): the menus offer only those. */
const modesFor = id => { const h = (S.harnesses || []).find(x => x.id === id); return Object.keys(MODE_LABELS).filter(m => !h || !h.modes || h.modes.includes(m)); };
/* Where a harness's modes mean something else, its own words. */
const MODE_LABELS_BY = { opencode: {
  default: "default · OpenCode's own config; what it asks waits in its terminal (⌘T)",
  acceptEdits: "accept edits · file edits approved; anything else OpenCode asks waits in ⌘T",
  bypassPermissions: "bypass · every ask approved" } };
const modeLabel = (harness, m) => (MODE_LABELS_BY[harness] || {})[m] || MODE_LABELS[m] || m;
const modeOptions = id => modesFor(id).map(value => ({ value, label: modeLabel(id, value) }));
async function newSession(gidArg, preset = {}) {  // preset: {cwd, machine} (a folder's menu: "New session here…"); {fork: sid}: "Fork to…"
  try { const d = await api("GET", "/api/harnesses"); if (d.harnesses && d.harnesses.length) S.harnesses = d.harnesses; } catch {}  // what "Default" ran last
  const last = LS.get("lastNew", { cwd: "~/", model: "", mode: "acceptEdits", harness: "claude" });
  // Folders are written as the chosen machine writes them (~/proj there), and listed per machine: the recent ones
  // on it (its sessions' folders, the last one used there), and completion asks that machine.
  const onIt = (s, m) => !s.fleet && (m ? s.remote === m : !s.remote);
  const homeTilde = p => p.replace(/^\/(?:home\/[^/]+|Users\/[^/]+|root)(?=\/|$)/, "~");
  const folderOf = s => homeTilde(s.remote ? s.cwd.replace(/^\/@[^/]+/, "") : s.cwd);
  const lastOn = m => m ? ((last.cwdOn || {})[m] || "") : tilde(last.cwd || "");
  // Fork to…: the dialog starts from the session forked (its machine, folder, harness, model, group); another machine
  // or folder makes a hand-over there (orbit/desk/forkto.py), the same ones the session's own fork
  const src = preset.fork ? S.sessions.get(preset.fork) : null;
  if (src) { preset = { ...preset, machine: src.remote || "", cwd: folderOf(src) }; gidArg = gidArg || src.group; }
  const harness = src && S.harnesses.some(h => h.id === src.harness) ? src.harness : S.harnesses.some(h => h.id === last.harness) ? last.harness : S.harnesses[0].id;
  let ctls = null;
  const gid = typeof gidArg === "string" ? gidArg : (S.group || (cur() ? cur().group : (S.groups[0] || {}).id));
  // Which machine: this one, or another (its folders, its harnesses); the group is this server's either way.
  const ups = (S.machines || []).filter(m => m.ok);
  let machine = cur() && cur().remote && ups.some(m => m.name === cur().remote) ? cur().remote : (S.machine && S.machine !== "local" && ups.some(m => m.name === S.machine) ? S.machine : "");
  if (preset.cwd) machine = preset.machine && ups.some(m => m.name === preset.machine) ? preset.machine : "";
  const completeDir = q => api("GET", (machine ? `/api/remote/${machine}` : "") + "/api/complete-dir?q=" +
    encodeURIComponent(q.replace(/^\/@[^/]+/, ""))).then(d => d.dirs);
  const hereOn = m => cur() && onIt(cur(), m) ? folderOf(cur()) : "";
  const recent = () => [...new Set([hereOn(machine), lastOn(machine),
    ...[...S.sessions.values()].filter(x => onIt(x, machine)).map(folderOf)].filter(Boolean))];
  const defaultFolder = m => hereOn(m) || lastOn(m) || "~/";
  // A harness that runs on this machine only (a local plugin's) is not offered for another machine.
  const harnessOpts = m => S.harnesses.filter(h => !m || !h.local_only).map(h => ({ value: h.id, label: h.label }));
  let harnessNow = harness;
  const v = await dialog({
    title: src ? `Fork “${src.name}” to…` : "New session", ok: src ? "Fork ⌘⏎" : "Start ⌘⏎",
    ...(src ? { body: "Same machine and folder: it continues the conversation (another harness: a hand-over). Another machine or folder: a hand-over there: its latest turns go with it as a file, it reads them, checks the working tree, reports and waits." } : {}),
    fields: [
      { key: "name", label: "Name", value: src ? `${src.name} (fork)` : "", placeholder: "optional, kept when the tab closes; e.g. softcensor review" },
      ...(ups.length ? [{ key: "machine", label: "Machine", type: "dropdown", value: machine,
        options: [{ value: "", label: "This Mac" }, ...ups.map(m => ({ value: m.name, label: m.name }))],
        onChange: m => {
          machine = m; const f = ctls.cwd.focusEl; f.value = defaultFolder(m); f.focus(); f.select();
          const opts = harnessOpts(m);
          if (!opts.some(o => o.value === harnessNow)) { harnessNow = opts[0].value; ctls.model.setOptions(modelsFor(harnessNow), ""); ctls.effort.setOptions(effortsFor(harnessNow), ""); }
          ctls.harness.setOptions(opts, harnessNow);
        } }] : []),
      { key: "cwd", label: "Folder", value: preset.cwd || defaultFolder(machine), recent, complete: completeDir },
      { key: "harness", label: "Harness", type: "dropdown", value: harness, options: harnessOpts(machine),
        onChange: h => { harnessNow = h; ctls.model.setOptions(modelsFor(h), ""); ctls.effort.setOptions(effortsFor(h), ""); ctls.mode.setOptions(modeOptions(h), ctls.mode.get()); } },
      { key: "model", label: "Model", type: "dropdown", value: src ? (src.model || "") : harness === last.harness ? last.model : "", options: modelsFor(harness) },
      { key: "effort", label: "Effort", type: "dropdown", value: src ? (src.effort || "") : harness === last.harness ? (last.effort || "") : "", options: effortsFor(harness) },
      { key: "mode", label: "Mode", type: "dropdown", value: src && modesFor(harness).includes(src.mode) ? src.mode : modesFor(harness).includes(last.mode) ? last.mode : "default", options: modeOptions(harness) },
      { key: "group", label: "Group", type: "dropdown", value: gid, options: groupTree().map(({ g, depth }) => ({ value: g.id, label: "\u2003".repeat(depth) + g.name })) },
    ],
    render: (_, ctl) => { ctls = ctl; },
    validate: async v => {
      if (!v.cwd.trim()) return "Pick a folder.";
      try { newSession.created = await api("POST", src ? `/api/sessions/${src.id}/fork` : "/api/sessions", src ? { ...v, machine: v.machine || "" } : v); }
      catch (e) { return e.message; }
      return null;
    },
  });
  if (!v) return;
  if (src) {  // (a fork: the last New session choices stay as they were)
    const r = newSession.created;
    S.sessions.set(r.id, r); if (!S.cards.has(r.id)) S.cards.set(r.id, []);
    switchTo(r.id);
    toast(r.fork_across ? `Forked “${src.name}” → “${r.name}”${v.machine ? " on " + v.machine : ""}: a hand-over (it reads ${r.handover ? "the turns written for it" : "this session's turns"}, checks the working tree, reports and waits)`
                        : `Forked “${src.name}” → “${r.name}”`);
    return;
  }
  const cwdOn = { ...(last.cwdOn || {}), ...(v.machine ? { [v.machine]: v.cwd } : {}) };  // the last folder, per machine
  LS.set("lastNew", { cwd: v.machine ? last.cwd : v.cwd, cwdOn, model: v.model, mode: v.mode, harness: v.harness, effort: v.effort });
  const s = newSession.created;
  S.sessions.set(s.id, s);
  S.cards.set(s.id, []);
  switchTo(s.id);
}
async function renameSession(sid = S.current) {
  const s = S.sessions.get(sid); if (!s) return;
  const v = await dialog({ title: "Rename session", fields: [{ key: "name", label: "Name (empty: the folder name)", value: s.name }] });
  if (v) try { await api("POST", `/api/sessions/${sid}/rename`, v); } catch (e) { toast(e.message); }
}
function tabMenu(sid = S.current, where) {
  const s = S.sessions.get(sid); if (!s) return;
  const working = s.state === "working";
  const groups = () => [...groupMenuItems(gid => moveToGroup(sid, gid), { current: s.group }),
                        { sep: true }, { label: "New group…", run: () => moveToGroup(sid, "__new") }];
  menu([
    { label: "Rename", run: () => renameSession(sid) },
    EMBED ? null : { label: "Fork", key: "/fork-session", run: () => forkSession(sid) },
    EMBED ? null : { label: "Fork to…", run: () => newSession(undefined, { fork: sid }) },  // another harness, machine or folder (a dialog)
    EMBED ? null : { label: "Move to", sub: groups },
    { label: "Copy link", run: () => copyText(`orbit://session/${sid}`) },
    { label: "Copy resume command", run: () => copyResumeCommand(sid) },
    { sep: true },
    EMBED ? null : { label: "Shell here", key: "⌘J", run: () => shellHere(sid) },
    { label: "Open folder in a terminal", run: () => api("POST", "/api/open-terminal", { sid }).then(r => toast(`Opened ${tilde(s.cwd)} in ${r.app}`)).catch(e => toast(e.message)) },
    { sep: true },
    EMBED ? null : { label: "Side tab", sub: () => [
      { label: "Open this session in the side tab", run: () => openSide({ kind: "session", sid }) },
      { label: "± Review of its folder", key: "⌘⇧I", run: () => openSide({ kind: "review", root: s.cwd, sid }) },
      { label: S.side || !sideEntry() ? "Hide the side panel" : "Show the side panel", key: "⌘\\", disabled: !sideEntry(), run: toggleSidePanel },
      { label: "Close the side tab", disabled: !S.side, run: () => closeSide() },
    ] },
    { label: "Session", sub: () => [
      { label: "Stop", disabled: !working && !s.queued, run: () => sessionAction(sid, "stop") },
      { label: "Resume", disabled: s.state === "ready" || s.state === "working", run: () => sessionAction(sid, "resume") },
      { label: "Restart", run: () => sessionAction(sid, "restart") },
    ] },
    EMBED ? null : { label: "More", sub: moreItems },
    { sep: true },
    EMBED ? null : { label: "Close", key: "⌘W", run: () => closeSession(sid) },
  ].filter(Boolean), where || { anchor: document.querySelector(`.tab[data-sid="${sid}"]`) || $("#tabs") });
}
/* Copy resume command: a shell line that continues this conversation in a terminal, in the agent's own UI
   (cd <folder> && claude --resume <id> · codex resume <thread> · agy --conversation <id> · opencode --session <id>). */
async function copyResumeCommand(sid) {
  const s = S.sessions.get(sid); if (!s) return;
  // WebKit lets a page write the clipboard only within the click: the write starts now, with the text to come.
  const asked = api("GET", `/api/sessions/${encodeURIComponent(sid)}/resume-command`), got = asked.then(d => d.command);
  try {
    if (window.ClipboardItem && navigator.clipboard.write) {
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": got.then(t => new Blob([t], { type: "text/plain" })) })]);
    } else await navigator.clipboard.writeText(await got);
    const busy = s.state === "working" || s.agents > 0, alive = (await asked).alive;  // alive: its agent's process runs here
    toast(s.remote ? `Copied: run it on ${s.remote}`
      : busy ? "Copied. It is working here: stop it before you go on in the terminal"
      : alive ? "Copied. Its agent is still open here: after the terminal, use Session → Restart so it continues from what you did there (those turns won't show as cards)"
      : "Copied: paste it in a terminal");
  } catch (e) { toast(await got.then(() => "Copy failed", x => x.message)); }  // (why the server said no, else the clipboard)
}
function contextMenu() {  // ⌘E: the menu of whatever has the keyboard (a selection first: quote it, copy it)
  const q = quoteSource();
  if (q) { quoteMenu(q); return; }
  if (xOn() && inXView()) { xMenu(); return; }
  if (EMBED) { if (S.file) fileMenu(S.file, { anchor: $("#fhead") }); else if (S.current && !EMBED_REVIEW) tabMenu(S.current, { anchor: $("#composer") }); return; }
  if (S.file) fileMenu(S.file); else tabMenu();
}
/* How this machine's agents run (GET /api/harness-server, asked at each hello): one line in ⋯ when they run in the
   server itself, so a restart ends them; nothing when the Harness Server runs them (the normal case). */
let HOSTING = null;
function loadHosting() {
  api("GET", "/api/harness-server").then(h => { HOSTING = h; }, () => { HOSTING = null; });
}
/* Restart Harness Server (here and View → Restart Harness Server in the app): every agent process ends, so it asks
   first, saying how many run. Their sessions stay: a request at work goes on by itself once the new one is up. */
async function restartHarnessServer() {
  let h;
  try { h = await api("GET", "/api/harness-server"); } catch (e) { toast(e.message); return; }
  if (h.mode !== "hosted") { toast(`The agents run in the server itself${h.why ? ` (${h.why})` : ""}: there is no Harness Server to restart`); return; }
  const n = (h.procs || []).filter(p => p.alive).length;
  const busy = [...S.sessions.values()].filter(s => !s.remote && (s.state === "working" || s.agents > 0)).length;
  const ok = await confirmDialog({
    title: "Restart the Harness Server?",
    body: (n ? `${n} agent process${n === 1 ? "" : "es"} will end` : "No agent process runs now") +
      (busy ? `; ${busy >= n ? (n === 1 ? "it is" : "all are") : busy === 1 ? "one is" : `${busy} are`} at work: its request is cut off and goes on by itself once the new one is up` : "") +
      ". The sessions stay; each agent starts again on its next request. Use this only to run a new version of the Harness Server.",
    ok: "Restart", danger: true, focus: "cancel",
  });
  if (!ok) return;
  toast("Restarting the Harness Server…");
  try {
    const r = await api("POST", "/api/harness-server/restart", {});
    HOSTING = r;
    toast(`The Harness Server restarted${r.ended ? `: ${r.ended} agent process${r.ended === 1 ? "" : "es"} ended` : ""}`);
  } catch (e) { toast(e.message); }
}
window.restartHarnessServer = restartHarnessServer;
function moreItems() {
  const direct = HOSTING && HOSTING.mode === "direct"
    ? [{ label: `Agents run in the server: a restart ends them${HOSTING.why ? ` (${HOSTING.why})` : ""}`, disabled: true }, { sep: true }] : [];
  const reload = { label: "Reload UI", key: "⌘⇧R", run: reloadUi };
  const touch = document.body.classList.contains("touch");  // (an iPad: no ⌘⇧R to hand, the reload comes first)
  return [
    ...(EMBED ? [] : direct),
    touch ? reload : null,
    { label: "Keyboard shortcuts", key: "⌘/", run: shortcutSheet },
    { label: "Settings…", key: "⌘,", run: settingsDialog },
    { label: "Schedules", key: "⌘⌃S", run: () => toggleView("orbit:schedules") },
    { label: "Schedules in the side pane", run: () => toggleSideKind("schedules") },
    { label: "Calendar", key: "⌘⌃C", run: () => toggleView("orbit:calendar") },
    { label: "Calendar in the side pane", run: () => toggleSideKind("calendar") },
    EMBED ? null : { label: S.side || !sideEntry() ? "Hide the side panel" : "Show the side panel", key: "⌘\\", disabled: !sideEntry(), run: toggleSidePanel },
    touch ? null : reload,
    HOSTING && HOSTING.mode === "hosted" ? { label: "Restart Harness Server…", run: restartHarnessServer } : null,
    { label: "Reset panel sizes", run: resetPanels },
    { label: "Reset zoom", key: "⌘0", run: () => setZoom(ZOOM_DEFAULT) },
    ...(EMBED || !pluginMenuItems().length ? [] : [{ sep: true }, ...pluginMenuItems()]),
    EMBED ? null : { label: S.follow ? "Follow: on (the left panel reveals the current tab)" : "Follow: off", run: () => { S.follow = !S.follow; saveUi(); followPanel(); renderLeft(); } },
  ].filter(Boolean);
}
function moreMenu() { menu(moreItems(), { anchor: $("#morebtn") }); }
async function reloadUi() {
  if (EMBED) { window.parent.postMessage({ orbit: "key", combo: "MS:KeyR" }, location.origin); return; }
  saveDraft(); saveUi(); LS.set("focus", focusPlace());
  await Promise.race([saveAll(), new Promise(r => setTimeout(r, 1500))]);  // drafts and edits survive ⌘⇧R
  location.reload();
}
window.reloadUi = reloadUi;
window.saveDraft = saveDraft;
let _rectScale = null;
function rectScale() {  // how getBoundingClientRect relates to CSS px under the page zoom: 1 or the zoom
  if (_rectScale == null) {
    const p = document.createElement("div");
    p.style.cssText = "position:fixed;left:100px;top:0;width:1px;height:1px;visibility:hidden";
    document.body.appendChild(p);
    _rectScale = p.getBoundingClientRect().left / 100 || 1;
    p.remove();
  }
  return _rectScale;
}
function setZoom(z, tell = true) {
  _rectScale = null;
  S.zoom = Math.round(Math.max(0.5, Math.min(3, z)) * 10) / 10;
  if (!EMBED) LS.set("zoom", S.zoom);
  document.documentElement.style.zoom = S.zoom;
  document.documentElement.style.setProperty("--zoom", S.zoom);
  if (tell && !EMBED) eachFrame(w => w.postMessage({ orbit: "zoom", z: S.zoom }, location.origin));
  fitEmbed();
}
/* A side tab's page fits its frame: WebKit takes vw/vh in a zoomed iframe as already zoomed (the page came out 1.2×
   too wide, cut off at the right), Chromium does not; the frame's own size divided by the zoom is right in both. */
function fitEmbed() {
  if (!EMBED) return;
  const app = document.getElementById("app"); if (!app) return;
  app.style.width = innerWidth / S.zoom + "px"; app.style.height = innerHeight / S.zoom + "px";
}
if (EMBED) addEventListener("resize", fitEmbed);

/* ===================================================================== shortcut table (interaction.md §8): the one list */
const SHORTCUTS = [
  ["Sessions and tabs", [
    ["⌘N", "New session (folder, model, permissions, group, name)", "M:KeyN", () => newSession()],
    ["⌘O", "Open: finder with Sessions · Files & Folders (a folder: the Files panel on it) · All Files (every session's folder and the home folders) · Notes · History (starts on Files in a file tab, on Notes in a note; ⌘⏎ opens pinned)", "M:KeyO", () => openFinder()],
    ["⌘⌃O", "Open into the side tab", "MC:KeyO", () => openFinder(null, { side: true })],
    ["⌘R", "Recent: sessions (with their last answer) and files, open or closed (in the Groups panel: rename the row: a group, a session, a file tab's file)", "M:KeyR", openRecent],
    ["⌘W", "Close what has the keyboard: the side tab, else the file tab, else the session tab", "M:KeyW", closeWhatHasKeyboard],
    ["⌘⇧T", "Reopen the last closed tab, in its group (again: the one before)", "MS:KeyT", reopenLast],
    ["⌃D", "In the Groups panel: close the row's session or tab, or delete its group; in Files and Notes: the row (or the selection) to the Trash (asked first)"],
    ["Space · ← →", "In the Groups panel: fold / unfold the group (on a row in it: its group folds)"],
    ["Tab · ⇧Tab", "In the Groups panel on a group: into the group above it (a subgroup) · out a level. Groups nest 3 deep"],
    ["⌘⇧N", "New window, on this group (each window looks at a group of its own; the tabs are shared); ⌘` goes to the next window", "MS:KeyN", () => newWindow()],
    ["⌘⇧W", "Close this window (the app's first window hides: the server and its agents go on)"],
    ["⌘⇧[ ⌘⇧]", "Previous / next tab, the group's home first (in the left panel or a finder: its tabs)", ["MS:BracketLeft", "MS:BracketRight"], [() => cycleTab(-1), () => cycleTab(1)]],
    ["⌘⌃H ⌘⌃L", "Move the tab left / right, past any tab, session or file (in the Groups panel: move the group up / down)", ["MC:KeyH", "MC:KeyL"], [() => moveCurrentTab(-1), () => moveCurrentTab(1)]],
    ["⌘- ⌘=", "Back / forward through visited tabs", ["M:Minus", "M:Equal"], [() => histGo(-1), () => histGo(1)]],
    ["⌘E", "Context menu of what has the keyboard (tab menu; file menu in a file tab)", "M:KeyE", contextMenu],
    ["⌘K", "Quick ask: a one-off request to a Fleet agent or any session", "M:KeyK", () => quickAsk()],
    ["⌘I", "Notifications: finished requests of other tabs", "M:KeyI", notifications],
  ]],
  ["Input and conversation", [
    ["⌘⏎", "Send (while the agent works: queued as the next request; in a plugin's thread: the reply)", "M:Enter", () => { if (pviewKey("send")) return; if (!S.file && !EMBED_REVIEW) send(); }],
    ["⌘⌥⏎", "Send the input to another session, as you typed it (your message there; this tab stays; also the send button's right-click)", "MA:Enter", () => { if (!S.file && !EMBED_REVIEW) sendInputTo(); }],
    ["⌘⇧⏎", "Steer: while the agent works, the message joins the running request", "MS:Enter", () => { if (!S.file && !EMBED_REVIEW) send({ steer: true }); }],
    ["⏎", "New line"],
    ["Esc", "Input ⇄ conversation"],
    ["↑↓ PgUp PgDn ⌃V ⌥V", "Scroll the focused conversation"],
    ["Home End ⌥⇧, ⌥⇧.", "Top / end of the conversation (End sticks to the end again)"],
    ["⌘L", "Scroll to the latest answer", "M:KeyL", scrollLatest],
    ["⌘⇧V", "Voice call with the current session (GPT Live): talk, and it does the work; in a call, from anywhere: hang up (its settings and the OpenAI key: Settings → Voice)", "MS:KeyV", () => toggleCall()],
    ["⌘⇧A", "In a voice call, from anywhere: mute / unmute the microphone", "MS:KeyA", () => voiceMute()],
  ]],
  ["Panels", [
    ["⌘1", "Left panel (three-state toggle)", "M:Digit1", toggleLeft],
    ["⌘J", "Shell in the session's folder (three-state toggle); Esc Esc back to the input", "M:KeyJ", toggleShell],
    ["⌃X 1", "Close every panel beside the main one"],
    ["⌘⇧O", "Keyboard: main panel ⇄ side tab", "MS:KeyO", toggleSideFocus],
    ["⌘\\", "Hide the side panel (its tabs are kept), or show it again", "M:Backslash", toggleSidePanel],
    ["⌥⇧[ ⌥⇧]", "Previous / next side tab of this tab (from the main page or the side pane); in the left panel's Groups tab: the machine filter (All · This Mac · each machine)", ["AS:BracketLeft", "AS:BracketRight"], [() => cycleSide(-1), () => cycleSide(1)]],
    ["⌃⌥H ⌃⌥L · ⌃⌥K ⌃⌥J", "Move the focused panel's edge", ["CA:KeyH", "CA:KeyL", "CA:KeyK", "CA:KeyJ"], [() => nudgeSplit(-24), () => nudgeSplit(24), () => nudgeShell(24), () => nudgeShell(-24)]],
    ["⌘T", "The agent's own terminal (a harness that has one: Harness Protocol 1.1); three-state like ⌘J", "M:KeyT", toggleTerminal],
    ["⌘⇧I", "± Review: the session's uncommitted changes in the side tab (three-state); comment on lines, revert a hunk", "MS:KeyI", toggleReview],
    ["⌘⌃C · ⌘⇧C", "Reserved for Calendar (D4)"],
  ]],
  ["Find, files, notes", [
    ["⌘F", "Find in what has the keyboard; ⏎ / ⇧⏎ or ⌘G / ⌘⇧G next / previous; Esc closes", ["M:KeyF", "M:KeyG", "MS:KeyG"],
      [() => openFind(), () => findNext(1), () => findNext(-1)]],
    ["⌘⇧F", "Search every session's history and the notes (finder → History)", "MS:KeyF", () => openFinder("History", { opener: "MS:KeyF" })],
    ["⌘⇧E", "Browse the open file's folders (breadcrumb): ↑↓ ⏎, `..` goes up", "MS:KeyE", () => browseFolder()],
    ["⌘⇧D", "Today's (or this week's) note: the journal (selected answer text goes under today, linked to its card)", "MS:KeyD", weekNote],
    ["⌘S · ⌃X ⌃S", "Save (also auto-save 1.5 s after the last edit and on window blur)", "M:KeyS", saveCurrent],
  ]],
  ["Text fields (the left panel's filter, ⌘O, dialogs, the find bar)", [
    ["⌃A ⌃E · ⌃F ⌃B · ⌥F ⌥B", "Start / end of the line · a character · a word (as in the editors; ↑↓ ⌃N ⌃P still move in the list)"],
    ["⌃D ⌥D · ⌫ ⌃H · ⌥⌫ ⌃W", "Delete forward a character / a word · back a character · back a word"],
    ["⌃K · ⌃U · ⌃Y", "Kill to the end / to the start · yank what ⌃K ⌃U ⌃W ⌥D ⌥⌫ killed"],
  ]],
  ["Editors (code and Markdown)", [
    ["⌃Space · ⌃G", "Set mark · cancel (⌃A ⌃E ⌃F ⌃B ⌃N ⌃P ⌥F ⌥B extend the region while the mark is set)"], ["⌃W · ⌥W · ⌃Y", "Cut region · copy region · yank"], ["⌥D · ⌥⌫ · ⌃K", "Kill a word forward / back (presses in a row make one kill) · to the end of the line"], ["⌃V · ⌥V", "Page down / up"],
    ["⌃S · ⌃R", "Incremental search: open the find bar, then next / previous match; Esc closes"],
    ["⌃L", "Recenter (middle, top, bottom)"], ["⌥⇧, · ⌥⇧.", "Top / end of the document"], ["Tab · ⇧Tab", "Indent / outdent (Tab never leaves the editor)"],
    ["⌃/ · ⌘Z ⌘⇧Z", "Undo · undo / redo"], ["⌃X H", "Select all"], ["⌥-click · ⌘-click", "Edit a link's text · open it (a plain click opens it in Markdown)"],
  ]],
  ["Schedules and Calendar", [
    ["⌘⌃S", "Schedules (a main tab): ⏎ edit · N new · Space pause/resume · R run now · O journal · L last run · ⌫ remove", "MC:KeyS", () => toggleView("orbit:schedules")],
    ["⌘⌃C", "Calendar (a main tab: today and tomorrow, read-only; off until turned on in Settings): ↑↓ move · → open · ← close · ⏎ write its note · P prep · J join · W week", "MC:KeyC", () => toggleView("orbit:calendar")],
    ["⌘⇧C", "Today's and tomorrow's meetings (⏎ opens it in the Calendar, ⌘⏎ joins)", "MS:KeyC", () => meetingsMenu()],
  ]],
  ["App", [
    ["⌘,", "Settings: General, Appearance, Machines, About", "M:Comma", () => settingsDialog()],
    ["⌘/", "Shortcut sheet", "M:Slash", () => shortcutSheet()],
    ["⌘⇧R", "Reload the UI (drafts and edits kept)", "MS:KeyR", reloadUi],
    ["⌘⇧= ⌘⇧- ⌘0", `Zoom in / out / reset (default ${ZOOM_DEFAULT})`, ["MS:Equal", "MS:Minus", "M:Digit0"],
      [() => setZoom(S.zoom + 0.1), () => setZoom(S.zoom - 0.1), () => setZoom(ZOOM_DEFAULT)]],
  ]],
];
const BINDINGS = new Map();
for (const [, rows] of SHORTCUTS) for (const [, , combo, fn] of rows) {
  if (!combo) continue;
  if (Array.isArray(combo)) combo.forEach((c, k) => BINDINGS.set(c, fn[k])); else BINDINGS.set(combo, fn);
}
const WORKS_WITHOUT_COMBO = new Set(["⏎", "Esc", "↑↓ PgUp PgDn ⌃V ⌥V", "Home End ⌥⇧, ⌥⇧.", "⌃X 1", "⌃Space · ⌃G", "⌃W · ⌥W · ⌃Y", "⌃S · ⌃R",
  "⌃V · ⌥V", "⌃L", "⌥⇧, · ⌥⇧.", "Tab · ⇧Tab", "⌃/ · ⌘Z ⌘⇧Z", "⌃X H", "⌥-click · ⌘-click"]);
function shortcutSheet() {
  if (modal && modal.kind === "sheet") { closeModal(); return; }
  let rows = [], cur = 0, list = null;
  const runRow = r => {
    const fn = Array.isArray(r[3]) ? r[3][0] : r[3];
    if (typeof fn !== "function") { toast("That one works in place (no action to run from here)"); return; }
    closeModal(); fn();
  };
  dialog({ title: "Keyboard shortcuts", ok: "", cancel: "Close", wide: true, kind: "sheet",
    fields: [{ key: "q", label: "", placeholder: "Search by key or action (fuzzy) · ↑↓ move · ⏎ runs it" }],
    render(fbox, ctl) {
      list = document.createElement("div"); list.className = "sheet";
      fbox.appendChild(list);
      const inp = ctl.q.focusEl;
      const draw = () => {
        const q = inp.value.trim();
        rows = [];
        list.innerHTML = [...SHORTCUTS, ...pluginShortcutGroups()].map(([g, rs]) => {
          const hit = rs.filter(([k, a]) => !q || fuzzy(q, `${k} ${a}`) >= 0);
          if (!hit.length) return "";
          return `<h4>${esc(g)}</h4>` + hit.map(r => { rows.push(r); const [k, a, c] = r, n = rows.length - 1;
            return `<div class="r${n === cur ? " sel" : ""}" data-n="${n}"><span class="kk">${esc(k)}</span><span class="aa">${esc(a)}${c || WORKS_WITHOUT_COMBO.has(k) ? "" : " <span style='color:var(--ink-3)'>(later)</span>"}</span></div>`; }).join("");
        }).join("");
        cur = Math.max(0, Math.min(cur, rows.length - 1));
        const el = list.querySelector(".r.sel"); if (el) el.scrollIntoView({ block: "nearest" });
      };
      inp.addEventListener("input", () => { cur = 0; draw(); });
      list.addEventListener("mousedown", e => e.preventDefault());
      list.addEventListener("click", e => { const r = e.target.closest(".r[data-n]"); if (r) { cur = +r.dataset.n; draw(); } });
      list.addEventListener("dblclick", e => { const r = e.target.closest(".r[data-n]"); if (r) runRow(rows[+r.dataset.n]); });
      const box = () => list.closest(".dialog");
      ctl.q = { ...ctl.q, isOpen: () => true, onKey: (e, c) => {
        const pg = Math.max(1, Math.floor((box() ? box().clientHeight : 400) / 26) - 2);
        const mv = { ArrowDown: 1, ArrowUp: -1, "C:KeyN": 1, "C:KeyP": -1, PageDown: pg, PageUp: -pg, "C:KeyV": pg, "A:KeyV": -pg }[c];
        if (mv) { cur = Math.max(0, Math.min(rows.length - 1, cur + mv)); draw(); return true; }
        if (c === "AS:Comma" || c === "AS:Period") { cur = c === "AS:Comma" ? 0 : rows.length - 1; draw(); return true; }
        if (c === "C:KeyL") { recenter(list, list.querySelector(".r.sel")); return true; }
        if (c === "Enter") { if (rows[cur]) runRow(rows[cur]); return true; }
        return false;
      } };
      draw();
    } });
}
