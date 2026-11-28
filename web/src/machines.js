"use strict";
/* ===================================================================== other machines: remotes, this server, connect */
/* Other machines: servers this one reaches (`orbit remote add`, or Add a machine…). The server shows their sessions as
   its own (in your groups, tagged with their machine; ids NAME~ID; NAME/session for orbit-agent); "Open its page" opens that machine's own
   page in the browser, signed in with its token. */
const REMOTES_TAB = { name: "Other machines", empty: "No other machines. On one: orbit serve --remote, then orbit url; here: Add a machine…", items: async () => {
  const rows = (await api("GET", "/api/remotes")).remotes;
  const out = [];
  for (const r of rows) {
    const rm = where => menu([{ label: "Open its page", run: () => openRemote(r.name) },
      { label: `Remove ${r.name}…`, danger: true, run: () => removeRemote(r.name) }], where);
    out.push({ label: r.name, icon: "external", detail: r.url, tag: r.ok ? `${r.sessions.length} session${r.sessions.length === 1 ? "" : "s"}` : "unreachable",
               key: r.ok ? "" : (r.error || "").slice(0, 60), match: `${r.name} ${r.url}`, run: () => openRemote(r.name), menu: rm });
    for (const s of r.sessions)
      out.push({ label: `${r.name}/${s.name}`, icon: "terminal", detail: `${s.cwd} · ${harnessLabel(s.harness || "claude")}`, tag: s.state,
                 match: `${r.name} ${s.name} ${s.cwd}`, run: () => S.sessions.has(`${r.name}~${s.id}`) ? switchTo(`${r.name}~${s.id}`) : openRemote(r.name, s.id), menu: rm });
  }
  out.push({ label: "Add a machine…", icon: "plus", detail: "a URL from `orbit url` on it", match: "add machine remote", run: addRemote });
  return out;
} };
function remotesDialog() { finder({ title: "Other machines", tabs: [REMOTES_TAB] }); }
async function openRemote(name, session = "") {
  try { await api("POST", `/api/remotes/${name}/open`, { session }); } catch (e) { toast(e.message); }
}
async function addRemote() {
  const v = await dialog({ title: "Add a machine", ok: "Add", fields: [
      { key: "url", label: "URL (on that machine: orbit url)", placeholder: "http://100.x.y.z:8770/?token=…" },
      { key: "name", label: "Name (its sessions become name/session; empty: the URL's host)", placeholder: "gpu-box" }],
    validate: v => !/[?&]token=/.test(v.url) ? "The URL needs its ?token= (orbit url prints it)." :
                   v.name.trim() && !/^[a-z0-9][a-z0-9_-]{0,31}$/.test(v.name.trim().toLowerCase()) ? "A name: lower-case letters, digits, - and _." : null });
  if (!v) return;
  try { const r = await api("POST", "/api/remotes", { name: v.name, url: v.url.trim() }); toast(`Added ${r.name}: its sessions are ${r.name}/… for orbit-agent`); remotesDialog(); }
  catch (e) { toast(e.message); }
}
async function removeRemote(name) {
  if (!(await confirmDialog({ title: `Remove ${name}?`, body: "Its sessions keep running there; this machine just stops reaching it.", ok: "Remove" }))) return;
  try { await api("POST", `/api/remotes/${name}/delete`, {}); toast(`Removed ${name}`); } catch (e) { toast(e.message); }
}
/* Settings → Machines: whether other machines may reach this server, and the servers this one reaches (their
   sessions show here, in your groups, tagged with their machine). Both act at once: they are the server's, not this device's. */
/* A QR code of text, as SVG (vendor/qrcode.js). */
function qrSvg(text) {
  const q = qrcode(0, "M"); q.addData(text); q.make();
  return q.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
}
function machinesPane(pane, keys) {
  if (!["127.0.0.1", "localhost", "[::1]"].includes(location.hostname)) {  // the app shows another machine's server
    setRow(pane, "Machines", `This window shows ${esc(location.host)}'s server: set its machines up on that machine.`);
    return;
  }
  setHeading(pane, "This server");
  // HTTPS by Tailscale: what other machines and the phone use (a phone's browser asks HTTPS for its app, the
  // microphone, notifications). This Mac's own window stays on http://127.0.0.1, so it works with Tailscale down.
  const httpsCtl = setRow(pane, "HTTPS for other machines and your phone", "By Tailscale, at this Mac's Tailscale name, with a real certificate. This window stays on 127.0.0.1.");
  const httpsBox = document.createElement("div"); httpsBox.className = "set-block"; pane.appendChild(httpsBox);
  let hsw = null, qrOpen = "", lastHttps = null;  // (qrOpen: "phone" | "ipad", the code shown)
  const drawHttps = st => {
    lastHttps = st;
    if (!hsw) hsw = toggleSwitch(httpsCtl, { on: st.on, label: "HTTPS for other machines and your phone",
      onChange: async on => {
        httpsBox.innerHTML = `<div class="set-line"><span class="set-desc">${on ? "Asking Tailscale for a certificate…" : "Turning it off…"}</span></div>`;
        drawHttps(await api("POST", "/api/https", { on }));
        api("GET", "/api/sharing").then(drawShare).catch(() => {});  // (HTTPS on turns plain HTTP off)
      } });
    hsw.set(st.on); hsw.el.disabled = !st.available;
    if (!st.on) {
      qrOpen = "";
      httpsBox.innerHTML = `<div class="set-line"><span class="set-desc">${st.error ? esc(st.error)
        : `Off. Turning it on serves https://${esc(st.host || "this-mac.tailnet.ts.net")}:${st.port || 8443}.`}</span></div>`;
      return;
    }
    httpsBox.innerHTML = `<div class="set-line"><code class="murl">${esc(st.open_url.replace(/token=.*/, "token=…"))}</code>
        <button type="button" class="btn sm" data-copyurl="${esc(st.open_url)}">Copy URL</button>
        <button type="button" class="btn sm${qrOpen === "phone" ? " on" : ""}" data-qr="phone" aria-expanded="${qrOpen === "phone"}">iPhone…</button>
        <button type="button" class="btn sm${qrOpen === "ipad" ? " on" : ""}" data-qr="ipad" aria-expanded="${qrOpen === "ipad"}">iPad…</button></div>
      <div class="set-line"><span class="set-desc">On the other machine: Settings → Machines → Add, with this URL.</span></div>
      ${qrOpen ? `<div class="mqr"><div class="qr">${qrSvg(qrOpen === "ipad" ? st.ipad_url : st.phone_url)}</div><ol class="set-desc">
        <li>Scan this with the ${qrOpen === "ipad" ? "iPad" : "iPhone"}'s camera; open it in Safari.</li>
        <li>Share → Add to Home Screen.</li>
        <li>Orbit opens from its icon, logged in, full screen${qrOpen === "ipad" ? ": the whole of Orbit, made for touch (and a keyboard, if one is attached)" : ""}.</li></ol></div>` : ""}`;
  };
  httpsBox.addEventListener("click", e => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.copyurl) return copyText(t.dataset.copyurl);
    if ("qr" in t.dataset) { qrOpen = qrOpen === t.dataset.qr ? "" : t.dataset.qr; drawHttps(lastHttps); }
  });
  const shareCtl = setRow(pane, "Plain HTTP on the network", "Without HTTPS (a machine with no Tailscale certificate): it listens on this Mac's Tailscale address, else the LAN's. With HTTPS on, it is not needed.");
  const urlBox = document.createElement("div"); urlBox.className = "set-block"; pane.appendChild(urlBox);
  let sw = null;
  const drawShare = st => {
    if (!sw) sw = toggleSwitch(shareCtl, { on: st.on, label: "Plain HTTP on the network",
      onChange: async on => drawShare(await api("POST", "/api/sharing", { on })) });
    sw.set(st.on);
    urlBox.innerHTML = st.on ? st.urls.map(u => `<div class="set-line"><code class="murl">${esc(u.replace(/token=.*/, "token=…"))}</code>
        <button type="button" class="btn sm" data-copyurl="${esc(u)}">Copy URL</button></div>`).join("") +
      `<div class="set-line"><span class="set-desc">Listening on ${esc(st.addresses.join(", "))}:${st.port}. On the other machine: Settings → Machines → Add.</span>
        <button type="button" class="btn sm" data-rotate>New token…</button></div>`
      : `<div class="set-line"><span class="set-desc">${st.available.length ? `Off. Turning it on listens on ${esc(st.available.join(", "))}:${st.port}.` : "Off. This Mac has no network address."}</span></div>`;
  };
  urlBox.addEventListener("click", async e => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.copyurl) return copyText(t.dataset.copyurl);
    if ("rotate" in t.dataset && confirm("A new token: every machine that reaches this one must add it again with the new URL.")) {
      try { drawShare(await api("POST", "/api/sharing/rotate", {})); toast("New token: copy the URL again"); } catch (err) { toast(err.message); }
    }
  });
  api("GET", "/api/sharing?fresh=1").then(st => { drawHttps(st.https); drawShare(st); }).catch(e => { urlBox.innerHTML = `<div class="set-line"><span class="set-desc">${esc(e.message)}</span></div>`; });

  setHeading(pane, "Other machines");
  const list = document.createElement("div"); list.className = "set-block"; pane.appendChild(list);
  const form = document.createElement("div"); form.className = "set-block madd";
  form.innerHTML = `<div class="set-line"><input class="fin" data-k="url" placeholder="https://gpu-box.tailnet.ts.net:8443/?token=…" title="Copy URL on that machine (Settings → Machines), or run orbit url there" spellcheck="false" autocomplete="off" autocapitalize="off"></div>
      <div class="set-line"><input class="fin" data-k="name" placeholder="name" spellcheck="false" autocomplete="off" autocapitalize="off">
      <span class="set-desc mhint">its sessions show as <b>name</b>/…</span>
      <button type="button" class="btn primary" data-addremote disabled>Add</button></div><div class="set-desc merr"></div>`;
  pane.appendChild(form);
  const drawList = rows => {
    // "connecting": its first answer is still on the way (just added, or the server just started): not an error
    const st = r => r.ok ? "ready" : r.error === "connecting" ? "starting" : "notready";
    list.innerHTML = rows.map(r => `<div class="set-row machine"><div class="set-text">
        <div class="set-label"><span class="dot ${st(r)}"></span>${esc(r.name)}</div>
        <div class="set-desc">${esc(r.url)} · ${r.ok ? `${r.sessions.length} session${r.sessions.length === 1 ? "" : "s"}` : r.error === "connecting" ? "connecting…" : esc(r.error || "unreachable")}</div></div>
        <div class="set-ctl"><button type="button" class="btn sm" data-rmremote="${esc(r.name)}">Remove</button></div></div>`).join("")
      || `<div class="set-line"><span class="set-desc">None yet. Their sessions show in your groups, tagged with their machine.</span></div>`;
  };
  // The list follows the machines while it is shown: every 2 s while one is connecting or unreachable.
  let again = null;
  const loadList = () => api("GET", "/api/remotes").then(d => {
    if (!list.isConnected) return;
    known = d.remotes.map(r => r.name); drawList(d.remotes); refresh();
    clearTimeout(again);
    if (d.remotes.some(r => !r.ok)) again = setTimeout(() => { if (list.isConnected) loadList(); }, 2000);
  }).catch(() => { if (list.isConnected) drawList([]); });
  loadList();
  list.addEventListener("click", async e => {
    const t = e.target.closest("[data-rmremote]"); if (!t) return;
    try { await api("POST", `/api/remotes/${t.dataset.rmremote}/delete`, {}); loadList(); } catch (err) { toast(err.message); }
  });
  // The name follows the URL's host (http://build-box:8770 → build-box) until you type one; it is kept to what a
  // name may be (lower-case letters, digits, - and _), and what is wrong shows as you type.
  const urlIn = form.querySelector('[data-k="url"]'), nameIn = form.querySelector('[data-k="name"]');
  const err = form.querySelector(".merr"), hint = form.querySelector(".mhint"), addBtn = form.querySelector("[data-addremote]");
  let named = false;
  const fromUrl = u => { try { const h = new URL(u).hostname; return /^[\d.]+$|:/.test(h) ? "" : h.split(".")[0].toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 32); } catch { return ""; } };
  const problem = () => {
    const url = urlIn.value.trim(), name = nameIn.value.trim();
    if (!url) return "";
    if (!/^https?:\/\/[^/?#\s]+/.test(url)) return "A URL like https://gpu-box.tailnet.ts.net:8443/?token=…";
    if (!/[?&]token=[^&\s]+/.test(url)) return "The URL needs its ?token= (Copy URL on that machine: it has it).";
    if (!name) return "A name: the URL is an address, so give the machine a name.";
    if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(name)) return "A name: lower-case letters, digits, - and _ (up to 32).";
    if (machineNames().includes(name)) return `There is a machine called ${name} already.`;
    return "";
  };
  const refresh = () => {
    if (!named) nameIn.value = fromUrl(urlIn.value.trim());
    const p = problem();
    err.textContent = p; addBtn.disabled = !!p || !urlIn.value.trim();
    hint.innerHTML = `its sessions show as <b>${esc(nameIn.value.trim() || "name")}</b>/…`;
  };
  let known = [];
  const machineNames = () => known;
  urlIn.addEventListener("input", refresh);
  nameIn.addEventListener("input", () => {
    const at = nameIn.selectionStart, v = nameIn.value.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9_-]/g, "");
    if (v !== nameIn.value) { nameIn.value = v; nameIn.setSelectionRange(Math.min(at, v.length), Math.min(at, v.length)); }
    named = !!v; refresh();
  });
  const add = async () => {
    refresh();
    const url = urlIn.value.trim(), name = nameIn.value.trim();
    if (err.textContent || !url) return;
    err.textContent = "Connecting…";
    try {
      const r = await api("POST", "/api/remotes", { name, url });
      err.textContent = ""; form.querySelectorAll("input").forEach(i => { i.value = ""; }); named = false; refresh();
      toast(`Added ${r.name}: its sessions show in the “${r.name}” group`); loadList();
    } catch (e2) { err.textContent = e2.message; }
  };
  form.addEventListener("click", e => { if (e.target.closest("[data-addremote]")) add(); });
  // ⏎ in the add fields adds
  keys.push({ isOpen: () => !!(document.activeElement && form.contains(document.activeElement) && document.activeElement.tagName === "INPUT"),
    onKey: (e, combo) => { if (combo !== "Enter") return false; add(); return true; } });
}
/* App → Connect to Server…: the app shows whichever orbit server it is pointed at (this Mac's, or one on a Linux box). */
async function connectServer() {
  if (!inApp()) { toast("In a browser, open the other server's URL (orbit url there)"); return; }
  const here = ["127.0.0.1", "localhost"].includes(location.hostname) ? "this Mac's server" : location.host;
  const v = await dialog({ title: "Connect to an orbit server", ok: "Connect", body: `Now: ${here}. Its agents keep running whichever server the app shows.`,
    fields: [{ key: "url", label: "URL (on that machine: orbit url); empty for this Mac's server", placeholder: "http://100.x.y.z:8770/?token=…" }],
    validate: v => v.url.trim() && !/^https?:\/\/[^/]+/.test(v.url.trim()) ? "A URL like http://100.x.y.z:8770/?token=…" :
                   v.url.trim() && !/^https?:\/\/(127\.0\.0\.1|localhost)[:/]/.test(v.url.trim()) && !/[?&]token=/.test(v.url) ? "The URL needs its ?token= (orbit url prints it)." : null });
  if (v) { saveDraft && saveDraft(); shellAsk("connect", v.url.trim()); }
}
window.connectServer = connectServer;
