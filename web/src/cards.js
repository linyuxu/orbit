"use strict";
/* ===================================================================== rendering: cards */
const STATE_WORD = { queued: "Queued", thinking: "Thinking", running: "Running", paused: "Paused", completed: "Done", error: "Error", cancelled: "Cancelled" };
/* "claude-opus-5-5-high" → "Claude Opus 5.5 High"; other harnesses' model names stay as they are. */
function modelName(m) {
  if (!m) return "";
  const big = /\[1m\]$/i.test(m);
  const k = m.replace(/\[1m\]$/i, "").replace(/-\d{8}$/, "").match(/^claude-([a-z]+)-(\d+)(?:-(\d+))?(?:-([a-z]+))?$/i);
  const cap = w => w[0].toUpperCase() + w.slice(1);
  if (k) return `Claude ${cap(k[1])} ${k[2]}${k[3] ? "." + k[3] : ""}${k[4] ? " " + cap(k[4]) : ""}${big ? " 1M" : ""}`;
  return /^(opus|sonnet|haiku)$/i.test(m) ? cap(m) : m;
}
/* What a card's tool steps did, by kind: "ran 10 commands, read 3 files". */
const STEP_KINDS = [
  [/web|fetch|browse|url/i, n => `${n} web lookup${n === 1 ? "" : "s"}`],
  [/bash|shell|command|exec|terminal/i, n => `ran ${n} command${n === 1 ? "" : "s"}`],
  [/edit|write|patch|notebook|create_file|replace/i, n => `edited ${n} file${n === 1 ? "" : "s"}`],
  [/read|view|open|cat/i, n => `read ${n} file${n === 1 ? "" : "s"}`],
  [/grep|glob|search|find|list|ls/i, n => `searched ${n} time${n === 1 ? "" : "s"}`],
  [/task|agent/i, n => `${n} agent${n === 1 ? "" : "s"}`],
];
function stepsDid(tools) {
  const counts = new Map();
  for (const t of tools) {
    const k = STEP_KINDS.findIndex(([re]) => re.test(t.name || ""));
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  return [...counts].sort((a, b) => b[1] - a[1]).slice(0, 3)
    .map(([k, n]) => k < 0 ? `${n} other${n === 1 ? "" : "s"}` : STEP_KINDS[k][1](n)).join(", ");
}
function currentTool(c) {
  for (let i = c.steps.length - 1; i >= 0; i--) {
    const st = c.steps[i];
    if (st.kind === "tool" && !st.done) return `${st.name}: ${st.summary}`;
  }
  return c.activity || "";
}
/* The turn as it happened, like Claude Code's own view: everything the agent said between tool calls, in full
   (Markdown), and each run of tool calls as one foldable group ("ran 2 commands, read 1 file"). While the request
   runs the group running now is open; the rest fold. A click on a group wins (per group). */
function timelineHtml(c) {
  if (!c.steps.length) return "";
  const parts = [];
  for (const st of c.steps) {
    if (st.kind === "note") { parts.push({ note: st }); continue; }
    if (st.agent) { parts.push({ agent: st }); continue; }  // a subagent: its own block
    const last = parts[parts.length - 1];
    if (last && last.tools) last.tools.push(st); else parts.push({ tools: [st] });
  }
  const running = ACTIVE.has(c.state) && !c.live && !c.partial;  // nothing said since the last group: it is the current one
  return parts.map((p, i) => p.note ? `<div class="md said">${renderMd(p.note.text || p.note.summary)}</div>`
    : p.agent ? agentHtml(c, p.agent) : toolsHtml(c, p.tools, i, running && i === parts.length - 1)).join("");
}
const stepMark = st => st.error ? `<span class="mk bad">${I("x", 11)}</span>` : st.done ? `<span class="mk done">${I("check", 11)}</span>` : `<span class="mk cur"><span class="cdot"></span></span>`;
/* A subagent (Claude Code's Agent / Task tool), like Claude Code shows it: its type and task, what it says and runs
   as it works (open while it runs), then its report to the main agent (folded once it is done). */
function agentHtml(c, st) {
  const key = `${c.sid}:${c.id}:a:${st.id}`;
  const open = S.openSteps.has(key) || (!st.done && !S.closedSteps.has(key));
  const sub = st.sub || [], tools = sub.filter(x => x.kind === "tool");
  const did = stepsDid(tools);
  const now = (tools.find(x => !x.done) || {}).name;
  const state = st.lost ? "ended with its agent" : st.error ? "failed" : st.done ? "done" : st.background ? (now ? `in the background · ${now}` : "running in the background") : now || "working";
  const rows = sub.map(x => x.kind === "note" ? `<li class="said">${esc((x.text || x.summary).replace(/\s+/g, " ").slice(0, 400))}</li>`
    : `<li>${stepMark(x)}<span class="nm">${esc(x.name)}</span><span class="sm" title="${esc(x.summary)}">${esc(x.summary)}</span></li>`).join("");
  return `<div class="steps agent${open ? " open" : ""}${st.done ? "" : " live"}">
    <button class="sum" data-steps="${key}" title="A subagent (${esc(st.agent)}): it works in its own context and reports back">${I("caretr", 13)}${stepMark(st)}<span class="agtype">${esc(st.agent)}</span><span class="agtask">${esc(st.summary || "")}</span><span class="agmeta">${tools.length ? `${tools.length} step${tools.length === 1 ? "" : "s"}${did ? " · " + esc(did) : ""} · ` : ""}${esc(state)}</span></button>
    <div class="agbody">${rows ? `<ol>${rows}</ol>` : ""}${st.result ? `<div class="agres"><div class="lbl">Report</div><div class="md">${renderMd(st.result)}</div></div>` : ""}</div>
  </div>`;
}
function toolsHtml(c, tools, i, current) {
  const key = `${c.sid}:${c.id}:${i}`;
  const open = S.openSteps.has(key) || (current && !S.closedSteps.has(key));
  const did = stepsDid(tools);
  const sum = `${tools.length} step${tools.length === 1 ? "" : "s"}${did ? " · " + esc(did) : ""}`;
  const rows = tools.map(st => {
    const mk = stepMark(st);
    return `<li>${mk}<span class="nm">${esc(st.name)}</span><span class="sm" title="${esc(st.summary)}">${esc(st.summary)}</span></li>`;
  }).join("");
  return `<div class="steps${open ? " open" : ""}"><button class="sum" data-steps="${key}" title="${esc([...new Set(tools.map(s => s.name))].join(", "))}">${I("caretr", 13)}${sum}</button><ol>${rows}</ol></div>`;
}
/* The work before the answer (what the agent said between tool calls, the tool groups, the subagents) as one fold:
   open while the request runs (a click folds it), folded once the answer is there, so a card reads as the request and
   its answer; "Worked for 3m 12s · 6 messages · 18 steps" opens it. A card that ended without an answer keeps it
   open (it is all there is). A click on it is remembered (S.openSteps / S.closedSteps, key …:p). */
function processHtml(c, timeline, body) {
  if (!timeline) return "";
  const key = `${c.sid}:${c.id}:p`, active = ACTIVE.has(c.state);
  const open = S.openSteps.has(key) || ((active || !body) && !S.closedSteps.has(key));
  const said = c.steps.filter(s => s.kind === "note").length, tools = c.steps.filter(s => s.kind === "tool" && !s.agent).length;
  const agents = c.steps.filter(s => s.agent).length;
  const parts = [active ? "Working" : c.started && c.finished ? `Worked for ${dur(c.finished - c.started)}` : "The work"];
  if (said) parts.push(`${said} message${said === 1 ? "" : "s"}`);
  if (tools) parts.push(`${tools} step${tools === 1 ? "" : "s"}`);
  if (agents) parts.push(`${agents} subagent${agents === 1 ? "" : "s"}`);
  return `<div class="proc${open ? " open" : ""}"><button class="psum" data-steps="${key}" title="${open ? "Fold" : "Show"} what it did before the answer">${I("caretr", 13)}<span>${esc(parts.join(" · "))}</span></button>` +
    `<div class="pbody">${timeline}</div></div>`;
}
function footHtml(c) {
  if (!c.started) return "";
  const end = c.finished || Date.now() / 1000;
  const n = c.steps.filter(s => s.kind === "tool").length;
  const parts = [dur(end - c.started)];
  if (n) parts.push(`${n} step${n === 1 ? "" : "s"}`);
  if (c.compactions) parts.push(`⟲${c.compactions}`);
  return parts.join(" · ");
}
function cardHtml(c) {
  const sess = S.sessions.get(c.sid) || {};
  MD_MACHINE = sess.remote || ""; MD_CWD = sess.cwd || "";
  try { return cardHtml0(c); } finally { MD_MACHINE = ""; MD_CWD = ""; }
}
const VOICE_SENT_TIP = "Asked in a voice call (GPT Live). The agent got the words below as they were transcribed; what you said while it waited to start is joined into this one request. It also got one line not shown here: “This request came by voice (the user's transcribed speech: it may mishear words); the start of your answer is spoken back to them, so lead with the takeaway in a sentence or two; code, tables and details stay in Orbit.”";
/* Where a request came from, when a plugin sent it (Slack, a webhook …): "via <label>", a link if it gave one. */
function originHtml(o) {
  if (!o || !(o.label || o.plugin)) return "";
  if (o.label === "Voice" && !o.plugin) return `<div class="origin" title="${esc(VOICE_SENT_TIP)}">${I("mic", 11)}via Voice</div>`;
  const what = esc(o.label || o.plugin), tip = esc(`Sent by the ${o.plugin || "a"} plugin`);
  const url = o.url && /^(https?|slack):\/\//.test(o.url) ? o.url : "";
  return `<div class="origin" title="${tip}">${I("plug", 11)}via ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${what}</a>` : what}</div>`;
}
/* A note card (a voice call's transcript): kept in the conversation, not a request. */
/* A note card (a voice call's transcript): folded to its head and what it asked (the requests are cards of their
   own above it, so the words would show twice); a click on the head opens it (kept while the page lives). */
const NOTE_OPEN = new Set();
function noteHtml(c) {
  const key = `${c.sid}:${c.id}`, open = NOTE_OPEN.has(key);
  const m = /(Requests from this call: [^\n]*|No requests from this call\.)/.exec(c.answer || "");
  const turns = ((c.answer || "").match(/^\*\*(You|Voice)\*\* · /gm) || []).length;
  return `<div class="vnote${open ? " open" : ""}"><button type="button" class="vhead" data-note="${esc(key)}" aria-expanded="${open}" title="${open ? "Fold the transcript" : "Show the transcript"}">` +
    `${I("chev", 11)}${I("mic", 13)}<span class="vt">${esc(c.text)}</span>` +
    `<span class="vsum">${turns ? `${turns} turn${turns > 1 ? "s" : ""}` : ""}${m ? ` · ${esc(m[1].replace("Requests from this call: ", "requests "))}` : ""}</span>` +
    `<span class="when">${dayTime(c.time)}</span></button>` +
    (open ? `<div class="md">${renderMd(c.answer || "")}</div>` : "") + `</div>`;
}
document.addEventListener("click", e => {
  const h = e.target.closest(".vnote .vhead[data-note]");
  if (!h) return;
  const key = h.dataset.note;
  if (NOTE_OPEN.has(key)) NOTE_OPEN.delete(key); else NOTE_OPEN.add(key);
  const [sid, id] = [key.slice(0, key.lastIndexOf(":")), +key.slice(key.lastIndexOf(":") + 1)];
  const c = (S.cards.get(sid) || []).find(x => x.id === id);
  if (c) renderCard(c);
});
/* A turn the agent took by itself (kind "auto": a background task finished and woke it; no request started it):
   not the user's words, so no bubble and nothing to edit, resend or link to; a quiet line on the left says so and what
   woke it ("On its own · after a background agent finished", then the task). Its work and answer show as a request's. */
function autoReqHtml(c) {
  const a = c.auto || {}, why = a.why || "", what = a.what || (c.text !== "On its own" && !why ? c.text : "");
  const tip = `The agent took this turn by itself: no request started it${why ? ` (${why})` : ""}. Its steps, subagents and answer are below.`;
  return `<div class="req auto"><div class="autoreq" title="${esc(tip)}">` +
    `<div class="ahead">${I("bell", 12)}<span class="lbl">On its own</span>${why ? `<span class="why">· ${esc(why)}</span>` : ""}` +
    `<span class="when">${dayTime(c.time)}</span></div>${what ? `<div class="what">${esc(what)}</div>` : ""}</div>` +
    (c.steers || []).map(x => `<div class="steer" title="Sent while this turn ran: it joined it${x.time ? " · " + dayTime(x.time) : ""}"><span class="lbl">steer</span>${reqHtml(x.text)}</div>`).join("") +
    `</div>`;
}
function cardHtml0(c) {
  if (c.kind === "note") return noteHtml(c);
  const active = ACTIVE.has(c.state);
  const tool = active ? currentTool(c) : "";
  const body = c.answer || [c.live, c.partial].filter(Boolean).join("\n\n");
  return `
    ${c.kind === "auto" ? autoReqHtml(c) : `<div class="req">
      <div class="bubble">${originHtml(c.origin)}${reqBody(c)}
      <div class="meta">
        <button data-linkreq="${c.id}" title="Copy a link to this request">${I("link", 11)}link</button>
        <button data-copyreq="${c.id}" title="Copy the request's text">${I("copy", 11)}copy</button>
        ${active || c.state === "queued" ? "" : `<button data-editreq="${c.id}" title="Edit: the text goes back into the input, and this card is replaced on send">${I("pencil", 11)}edit</button>`}
        ${c.state === "queued" ? `${canSteer(c.sid) ? `<button data-steernow="${c.id}" title="Steer now: send it into the request that is running (it joins that turn)">${I("zap", 11)}steer now</button>` : ""}<button data-withdraw="${c.id}" title="Withdraw: out of the queue and back into the input, to edit">${I("undo", 11)}withdraw</button>`
          : active ? "" : `<button data-resend="${c.id}" title="Resend as a new request">${I("resend", 11)}resend</button>`}
        <span class="when">#${c.id} · ${dayTime(c.time)}</span></div></div>
      ${(c.steers || []).map(x => `<div class="steer" title="Sent while the request ran: it joined this turn${x.time ? " · " + dayTime(x.time) : ""}"><span class="lbl">steer</span>${reqHtml(x.text)}</div>`).join("")}
    </div>`}
    <div class="ans s-${c.state}">
      <div class="head"><span class="cdot"></span><span class="st">${STATE_WORD[c.state] || c.state}</span>${tool ? `<span class="cur">${esc(tool)}</span>` : ""}
        ${active ? `<button class="btn sm stop" data-stop title="Stop this ${c.kind === "auto" ? "turn" : "request"} (⌘E → Session → Stop)">Stop</button>` : ""}
        ${c.state === "paused" && !c.continued ? `<button class="btn sm stop" data-continue="${c.id}" title="Resume the agent and let it go on">Continue</button>` : ""}</div>
      ${processHtml(c, timelineHtml(c), body)}
      ${active && c.thinking && !c.partial ? `<div class="thinking" title="What the model is thinking (streams while it runs)">${esc(c.thinking.slice(-400))}</div>` : ""}
      ${body ? `<div class="md">${renderMd(body)}</div>` : ""}
      ${c.error && (c.state === "error" || c.state === "paused") ? `<div class="err-line">${esc(c.error)}</div>` : ""}
      ${(c.followups || []).map(f => `<div class="followup"><div class="lbl">↳ Follow-up</div><div class="md">${renderMd(f)}</div></div>`).join("")}
      <div class="foot" data-foot>${footHtml(c)}</div>
    </div>`;
}
function emptyHtml() {
  if (EMBED) return `<div class="empty"><h2>This session is closed</h2>Close the side tab with <kbd>⌘W</kbd>.</div>`;
  if (!S.sessions.size) return `<div class="empty"><h2>No session yet</h2>Press <kbd>⌘N</kbd> to start an agent session in a folder, or <kbd>⌘O</kbd> to reopen one.</div>`;
  if (!S.current && S.group === FAV) return `<div class="empty"><h2>No favorites yet</h2><kbd>⌘⌃P</kbd> stars the current session. <kbd>⌘2</kbd> goes to your first group.</div>`;
  if (!S.current) return `<div class="empty"><h2>No session in ${esc(groupPath(S.group))}</h2>Press <kbd>⌘N</kbd> to start one here, or <kbd>⌘\`</kbd> for every group.</div>`;
  const s = cur(), model = s && (s.model_seen || s.model);
  return `<div class="empty"><h2>Nothing asked yet</h2>Type a request below and press <kbd>⌘⏎</kbd> to send it.` +  // U14: where it runs, and what to try
    (s ? `<div class="ectx">${esc(tilde(s.cwd))} · ${esc(harnessLabel(s.harness))}${model ? " · " + esc(model) : ""}</div>` : "") +
    `<div class="enext"><button class="btn sm" data-empty="slash"><kbd>/</kbd> a skill or command</button><button class="btn sm" data-empty="at"><kbd>@</kbd> a file</button>` +
    `<button class="btn sm" data-empty="shell"><kbd>⌘J</kbd> a shell here</button></div></div>`;
}
document.addEventListener("mousedown", e => { if (e.target.closest && e.target.closest("[data-empty]")) e.preventDefault(); });  // (the input keeps the keyboard: its list stays open)
document.addEventListener("click", e => {
  const b = e.target.closest && e.target.closest("[data-empty]"); if (!b) return;
  if (b.dataset.empty === "shell") { toggleShell(); return; }
  const ch = b.dataset.empty === "slash" ? "/" : "@";
  setTimeout(() => { focusMain(); input.insertAt(ch); updateSend(); updateMention(); }, 0);  // (after this click: its list opens, as when typed)
});
/* A long conversation draws only its latest requests (CONV_WINDOW); earlier ones come in above as you scroll up (or
   with the button), and all at once for ⌘F or a jump to an early card. Hundreds of cards in the page made every
   resize, side pane and switch slow. CONV.from: how many of the current session's cards are not drawn. */
const CONV_WINDOW = 40;
const CONV = { sid: null, from: 0 };
const cardDiv = c => `<div class="card" id="card-${c.id}" data-rev="${c.rev}">${cardHtml(c)}</div>`;
const earlierHtml = n => n > 0 ? `<div class="earlier" id="earlier"><button class="btn sm" data-earlier>Show ${n} earlier request${n === 1 ? "" : "s"}</button></div>` : "";
/* Subagents at work in this session (foreground or background), in a bar above the input: one row each, so they are
   not lost among the cards. A click opens the row right there (one at a time): what it says and runs, as in its card,
   following the latest unless scrolled up; "Show in conversation" goes to its card. Gone when none is running. */
const AGBAR_MAX = 3;
function runningAgents(sid) {
  const out = [];
  for (const c of S.cards.get(sid) || []) for (const st of c.steps) if (st.agent && !st.done) out.push({ c, st });
  return out;
}
function renderAgentBar() {
  const bar = $("#agentbar"); if (!bar) return;
  const list = S.current ? runningAgents(S.current) : [];
  bar.hidden = !list.length;
  if (!list.length) { bar.innerHTML = ""; return; }
  let all = S.agentbarAll || list.length <= AGBAR_MAX + 1, shown = all ? list : list.slice(0, AGBAR_MAX);
  const openKey = S.agentbarOpen && list.some(({ c, st }) => `${c.id}:${st.id}` === S.agentbarOpen) ? S.agentbarOpen : null;
  if (openKey && !shown.some(({ c, st }) => `${c.id}:${st.id}` === openKey)) { all = true; shown = list; }  // (the open one stays in view)
  const old = bar.querySelector(".agdetail"), keep = old && { top: old.scrollTop, end: old.scrollHeight - old.scrollTop - old.clientHeight < 4 };
  bar.innerHTML = shown.map(({ c, st }) => {
    const open = openKey === `${c.id}:${st.id}`;
    const sub = st.sub || [], cur = [...sub].reverse().find(x => x.kind === "tool" && !x.done);
    const now = cur ? `${cur.name} ${cur.summary || ""}`.trim() : sub.length ? "thinking" : "starting";
    const n = sub.filter(x => x.kind === "tool").length;
    const rows = sub.map(x => x.kind === "note" ? `<li class="said">${esc((x.text || x.summary).replace(/\s+/g, " ").slice(0, 600))}</li>`
      : `<li>${stepMark(x)}<span class="nm">${esc(x.name)}</span><span class="sm" title="${esc(x.summary)}">${esc(x.summary)}</span></li>`).join("");
    return `<button class="agrow${open ? " open" : ""}" data-agcard="${c.id}" data-agstep="${esc(st.id)}" aria-expanded="${open}" title="${esc(st.agent)}: ${esc(st.summary || "")}${st.background ? " (in the background)" : ""}\nClick: ${open ? "fold" : "what it is doing"}">` +
      `${I("caretr", 12)}<span class="cdot"></span><span class="agtype">${esc(st.agent)}</span><span class="agtask">${esc(st.summary || "")}</span>` +
      `<span class="agnow">${esc(now)}</span><span class="agn">${n ? `${n} step${n === 1 ? "" : "s"} · ` : ""}<span class="el" data-t0="${st.t0 || ""}">${st.t0 ? dur(Date.now() / 1000 - st.t0) : ""}</span></span></button>` +
      (open ? `<div class="agdetail">${rows ? `<ol>${rows}</ol>` : `<div class="agempty">Starting…</div>`}</div>` +
        `<div class="agfoot"><button class="aglink" data-agshow="${c.id}" data-agstep="${esc(st.id)}">Show in conversation</button></div>` : "");
  }).join("") + (list.length > shown.length ? `<button class="agmore" data-agmore="1">${list.length - shown.length} more running · show all</button>`
    : S.agentbarAll && list.length > AGBAR_MAX + 1 ? `<button class="agmore" data-agmore="0">Show fewer</button>` : "");
  const det = bar.querySelector(".agdetail");  // where it was read: at its end it follows what comes in
  if (det) det.scrollTop = !keep || keep.end ? det.scrollHeight : keep.top;
}
setInterval(() => {  // the elapsed times tick
  for (const el of document.querySelectorAll("#agentbar .el[data-t0]")) if (el.dataset.t0) el.textContent = dur(Date.now() / 1000 - +el.dataset.t0);
}, 1000);
function revealAgent(cid, stid) {
  const key = `${S.current}:${cid}:a:${stid}`, proc = `${S.current}:${cid}:p`;
  S.openSteps.add(key); S.closedSteps.delete(key);
  S.openSteps.add(proc); S.closedSteps.delete(proc);  // (its card's work, folded once answered: opened to show it)
  const el = () => [...document.querySelectorAll("#cards [data-steps]")].find(b => b.dataset.steps === key);
  if (!el()) { S.pendingScroll = +cid; renderConv(); }
  else { const c = (S.cards.get(S.current) || []).find(x => x.id === +cid); if (c) renderCard(c); }
  const b = el(); if (!b) return;
  const box = b.closest(".steps");
  S.atEnd = false;  // (going there is the user's choice: held there, not pulled back to the end)
  box.scrollIntoView({ block: "center" });
  setAtEnd(convAtBottom());
  box.classList.remove("flash"); void box.offsetWidth; box.classList.add("flash");
}
$("#agentbar").addEventListener("mousedown", e => { if (e.target.closest("button")) e.preventDefault(); });  // the input keeps the keyboard (the details stay selectable)
$("#agentbar").addEventListener("click", e => {
  const m = e.target.closest("[data-agmore]");
  if (m) { S.agentbarAll = m.dataset.agmore === "1"; renderAgentBar(); return; }
  const sh = e.target.closest("[data-agshow]");
  if (sh) { revealAgent(sh.dataset.agshow, sh.dataset.agstep); return; }
  const r = e.target.closest(".agrow"); if (!r) return;
  const k = `${r.dataset.agcard}:${r.dataset.agstep}`;
  S.agentbarOpen = S.agentbarOpen === k ? null : k;
  renderAgentBar();
});
function renderConv() {
  renderAgentBar();
  if (typeof renderCaps === "function") renderCaps();  // (the call button, and a voice call's captions: this session's or none)
  const box = $("#cards");
  const list = S.cards.get(S.current);
  if (!S.current || !list || !list.length) {
    box.innerHTML = S.current && !list ? "" : emptyHtml();
    CONV.sid = S.current; CONV.from = 0;
    updateJump();
    return;
  }
  const shown = list.filter(c => !c.hidden);
  let from = CONV.sid === S.current ? Math.min(CONV.from, Math.max(0, shown.length - CONV_WINDOW)) : Math.max(0, shown.length - CONV_WINDOW);
  if (S.pendingScroll != null) { const k = shown.findIndex(c => c.id === S.pendingScroll); if (k >= 0) from = Math.min(from, Math.max(0, k - 2)); }
  const anc = !S.atEnd && S.pendingScroll == null && SCROLL[S.current];  // held where the user left it: that card is drawn
  if (anc) { const k = shown.findIndex(c => String(c.id) === String(anc.card)); if (k >= 0) from = Math.min(from, Math.max(0, k - 2)); }
  if (FIND.open && FIND.target && FIND.target.root === box) from = 0;
  CONV.sid = S.current; CONV.from = from;
  box.innerHTML = shown.length ? earlierHtml(from) + shown.slice(from).map(cardDiv).join("") : emptyHtml();
  if (S.pendingFindQ) { const q = S.pendingFindQ; S.pendingFindQ = null; setTimeout(() => openFind(q), 0); }
  else refreshFind();
  if (S.pendingScroll != null) {
    const el = document.getElementById("card-" + S.pendingScroll);
    S.pendingScroll = null;
    if (el) { el.scrollIntoView({ block: "start" }); setAtEnd(convAtBottom()); return; }
  }
  if (anc && restoreAnchor()) { updateJump(); return; }
  if (anc) setAtEnd(true);  // (its card is gone: the end)
  stick();
}
/* Draw n more of the earlier cards above (all: every one), keeping what is on screen where it is. */
function showEarlier(n = CONV_WINDOW) {
  const list = S.cards.get(S.current);
  if (!list || CONV.sid !== S.current || CONV.from <= 0 || S.file) return;
  const shown = list.filter(c => !c.hidden), to = CONV.from, from = Math.max(0, to - n);
  const anchor = $("#cards .card"), y = anchor ? anchor.getBoundingClientRect().top : 0;
  const btn = $("#earlier"); if (btn) btn.remove();
  $("#cards").insertAdjacentHTML("afterbegin", earlierHtml(from) + shown.slice(from, to).map(cardDiv).join(""));
  CONV.from = from;
  if (anchor) conv.scrollTop += (anchor.getBoundingClientRect().top - y) / rectScale();  // whatever the browser's own anchoring did
}
const showAllCards = () => showEarlier(Infinity);
function renderCard(c) {
  if (c.sid !== S.current || S.file) return;
  const box = $("#cards");
  let el = document.getElementById("card-" + c.id);
  if (c.hidden) { if (el) el.remove(); return; }
  if (!el && CONV.sid === c.sid && CONV.from > 0) {  // one of the earlier cards not drawn: it stays undrawn
    const first = box.querySelector(".card");
    if (first && c.id < +first.id.slice(5)) return;
  }
  if (!el) {
    if (box.querySelector(".empty")) box.innerHTML = "";
    el = document.createElement("div");
    el.className = "card";
    el.id = "card-" + c.id;
    box.appendChild(el);
  }
  el.dataset.rev = c.rev;
  const stepsList = el.querySelector(".steps ol");
  const scroll = stepsList ? stepsList.scrollTop : 0;
  el.innerHTML = cardHtml(c);
  const ns = el.querySelector(".steps ol");
  if (ns) ns.scrollTop = ACTIVE.has(c.state) ? ns.scrollHeight : scroll;
  stick();
  refreshFind();
}
function upsertCard(c) {
  noteCard(c);
  const list = S.cards.get(c.sid);
  if (!list) return;
  const i = list.findIndex(x => x.id === c.id);
  if (i >= 0) { if ((list[i].rev || 0) > (c.rev || 0)) return; list[i] = c; }
  else { list.push(c); list.sort((a, b) => a.id - b.id); }
  renderCard(c);
  if (c.sid === S.current) renderAgentBar();
}
async function loadCards(sid) {
  try {
    const d = await api("GET", `/api/sessions/${sid}/cards`);
    const have = S.cards.get(sid) || [];
    const byId = new Map(have.map(c => [c.id, c]));
    const list = d.cards.map(c => { const h = byId.get(c.id); return h && (h.rev || 0) > (c.rev || 0) ? h : c; });
    S.cards.set(sid, list);
    for (const c of list) S.cardState.set(c.sid + ":" + c.id, c.state);
    if (sid === S.current && !S.file) { S.atEnd = !SCROLL[sid]; renderConv(); }
  } catch (e) { toast("Could not load the conversation: " + e.message); }
}

/* ===================================================================== notifications (⌘I) */
const FINAL = new Set(["completed", "error"]);
function noteCard(c) {
  const key = c.sid + ":" + c.id;
  const prev = S.cardState.get(key);
  S.cardState.set(key, c.state);
  if (!FINAL.has(c.state) || prev === c.state) return;
  const fresh = prev ? !FINAL.has(prev) && prev !== "cancelled" : c.finished && Date.now() / 1000 - c.finished < 10;
  if (!fresh) return;
  const s = S.sessions.get(c.sid);
  if (s) { refreshTree(s.cwd, true); if (s.id === S.current) pollReview(); }  // the agent may have changed files
  if (S.vault) { loadNoteNames(); if (S.left.shown && S.left.tab === "notes") refreshTree(S.vault, true); }  // or notes (orbit-agent note)
  if (EMBED_REVIEW && s && s.id === EMBED.sid) loadReview();
  if (EMBED || !s || (c.sid === S.current && !S.file && !document.hidden)) return;
  S.notes = [{ sid: c.sid, cid: c.id, name: s.name, state: c.state, time: c.finished || Date.now() / 1000, ...(c.kind === "auto" ? { auto: true } : {}),
               text: oneLine(c.state === "error" ? c.error : c.answer).slice(0, 160) },
             ...S.notes.filter(n => !(n.sid === c.sid && n.cid === c.id))].slice(0, 50);
  S.unread.add(c.sid);
  LS.set("notes", S.notes); LS.set("unread", [...S.unread]);
  renderTabs(); renderStatus(); renderLeft();
  toast(`${s.name}: ${c.kind === "auto" ? "its turn on its own" : "#" + c.id} ${c.state === "error" ? "failed" : "finished"}`, () => openCard(c.sid, c.id));
}
/* The agent needs the user in its terminal (Harness Protocol 1.1 attention): a mark on its tab, a toast, the bell. */
function attentionChanged(s) {
  S.notes = S.notes.filter(n => !(n.kind === "attention" && n.sid === s.id));
  if (s.attention) {
    S.notes = [{ kind: "attention", sid: s.id, name: s.name, text: s.attention, time: Date.now() / 1000 }, ...S.notes].slice(0, 50);
    toast(`${s.name} needs you in its terminal (⌘T): ${s.attention}`, () => { switchTo(s.id); toggleTerminal(); });
  }
  LS.set("notes", S.notes);
  renderStatus();
}
function clearNotes(sid) {
  const before = S.notes.length;
  S.notes = S.notes.filter(n => n.sid !== sid || n.kind === "attention");  // (attention lasts until the agent clears it)
  const changed = S.unread.delete(sid) || before !== S.notes.length;
  if (changed) { LS.set("notes", S.notes); LS.set("unread", [...S.unread]); }
}
function openCard(sid, cid) {
  if (!S.sessions.has(sid)) { toast("That session is closed"); return; }
  S.pendingScroll = cid;
  if (sid === S.current && !S.file) { renderConv(); focusMain(); } else switchTo(sid);
}
function notifications() {
  if (modal && modal.kind === "bell") { closeModal(); return; }
  const items = S.notes.length ? S.notes.map(n => n.kind === "plugin" ? {  // a plugin's notification
    label: `${n.name}${n.text ? " — " + n.text : ""}`, key: ago(n.time),
    run: () => n.open ? openPluginTab(n.plugin, n.open.kind, n.open.key, { title: n.open.title }) : null } : n.kind === "attention" ? {
    label: `${n.name} needs you in its terminal — ${n.text}`, key: ago(n.time), run: () => { switchTo(n.sid); toggleTerminal(); } } : {
    label: `${n.name} ${n.auto ? "· on its own" : "#" + n.cid}${n.state === "error" ? " · failed" : ""} — ${n.text || "(no text)"}`, key: ago(n.time),
    run: () => openCard(n.sid, n.cid) }) : [{ label: "Nothing new", disabled: true }];
  if (S.notes.length) items.push({ sep: true }, { label: "Clear all", run: () => { S.notes = []; S.unread.clear(); LS.set("notes", []); LS.set("unread", []); renderAll(); } });
  menu(items, { anchor: $("#bellbtn"), maxWidth: 520 }, "bell");
}

/* ===================================================================== scrolling */
const conv = $("#conv");
$("#cards").addEventListener("click", e => { if (e.target.closest("[data-earlier]")) showEarlier(); });
/* Near the top (two screens or less): the earlier ones come in, once the user's scrolling has stopped. Cards put in
   above move what is on screen, and showEarlier puts it back with scrollTop; WebKit (the app's WKWebView) scrolls on
   its own thread during a trackpad's momentum or a wheel's animation and drops that write, so the view landed on the
   cards just put in, near the top again, which pulled in the next ones: up to the first request. */
const earlierNear = () => conv.scrollTop < Math.max(1200, 2 * conv.clientHeight);
function earlierSoon() {
  clearTimeout(earlierSoon.t);
  if (CONV.from <= 0 || !earlierNear()) return;
  earlierSoon.t = setTimeout(() => { if (userScrolling()) earlierSoon(); else if (earlierNear()) showEarlier(); }, 150);
}
conv.addEventListener("scroll", earlierSoon, { passive: true });
/* Where the conversation is, per session, kept across resizes, new answers and reloads: either stuck to its end (it
   follows every new line), or held where the user scrolled to (the card at the top of the view and its offset). Only
   the user changes which: their own scrolling (wheel, trackpad, the scroll bar, page keys), ⌘L or Latest (the end
   again), sending a request (the end). A resize, a new answer or a picture loading never does. The message list view
   (msglist.js) does it, as for a plugin's thread. */
const SCROLL = LS.get("convScroll", {});  // sid -> {card, off}: held there; absent: at the end
const CONVL = new MsgList({
  box: conv, items: $("#cards"), jump: $("#jump"), itemSel: ".card",
  idOf: el => el.id.slice(5), elOf: id => document.getElementById("card-" + id),
  keyOf: () => S.current, anchors: SCROLL, persist: () => LS.set("convScroll", SCROLL),
  active: () => !S.file, hasItems: () => !!(S.cards.get(S.current) || []).length,
});
Object.defineProperty(S, "atEnd", { get: () => CONVL.atEnd, set: v => { CONVL.atEnd = v; }, configurable: true });
const userScrolling = () => CONVL.userScrolling();
const convAtBottom = () => CONVL.atBottom();
function setAtEnd(v) { CONVL.setAtEnd(v); }
function saveAnchor() { CONVL.saveAnchor(); }
function restoreAnchor() { return CONVL.restoreAnchor(); }
function stick() { CONVL.stick(); }
function updateJump() { CONVL.updateJump(); }
function scrollLatest() { if (pviewKey("latest")) return; CONVL.latest(); }
function scrollConv(how) { CONVL.scroll(how); }

/* A long request folds to its first 12 lines (U4): "Show all" opens it (kept per card). */
const REQ_FOLD = 12;
S.openReqs = S.openReqs || new Set();
function reqBody(c) {
  const n = (c.text || "").split("\n").length, key = c.sid + ":" + c.id;
  const fold = n > REQ_FOLD && !S.openReqs.has(key);
  return `<div class="rtext${fold ? " fold" : ""}">${reqHtml(c.text)}</div>` +
    (n > REQ_FOLD ? `<button class="rmore" data-reqmore="${esc(key)}">${fold ? `Show all ${n} lines` : "Show less"}</button>` : "");
}
document.addEventListener("click", e => {
  const b = e.target.closest && e.target.closest("[data-reqmore]"); if (!b) return;
  const key = b.dataset.reqmore;
  S.openReqs.has(key) ? S.openReqs.delete(key) : S.openReqs.add(key);
  const [sid, id] = [key.slice(0, key.lastIndexOf(":")), +key.slice(key.lastIndexOf(":") + 1)];
  const c = (S.cards.get(sid) || []).find(x => x.id === id); if (c) renderCard(c);
});
