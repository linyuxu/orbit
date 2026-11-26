"use strict";
/* ===================================================================== Notifications: Web Push to this phone
   Misc → Notifications. "Turn on" (a tap: iOS asks only from one) asks for permission, subscribes this app with the
   server's VAPID public key and posts the subscription (POST /api/push/subscribe; the server sends: orbit/services/
   push.py). iOS has Web Push only in a Home Screen app opened from an HTTPS address: elsewhere the page says what is
   missing. Then: Send a test (to this phone only), a reminder (a schedule with notify: the server sends it at its
   time, no agent wakes), the last sends (the push service's answer), Turn off. A notification's tap opens its session
   (sw.js: a running app gets {t:"open", sid}; a closed one starts at /m/#s/<sid>). */
const PUSH = { st: null, sub: null, busy: false, err: "" };
const pushIos = () => /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const pushStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
function pushBlocker() {  // why this page cannot subscribe here, or ""
  if (!window.isSecureContext) return "Notifications need this server's HTTPS address: open Orbit from it (Settings → Phone on the Mac), then add it to the Home Screen.";
  if (pushIos() && !pushStandalone()) return "On iPhone, notifications come to Orbit on the Home Screen: Share → Add to Home Screen, open Orbit from there, and turn them on here.";
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return "This browser has no Web Push.";
  return "";
}
const b64uBytes = s => { const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)); return Uint8Array.from(b, c => c.charCodeAt(0)); };
const bytesB64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
async function pushLoad() {
  try {
    const reg = pushBlocker() ? null : await navigator.serviceWorker.getRegistration("/m/");
    PUSH.sub = reg ? await reg.pushManager.getSubscription() : null;
    PUSH.st = await api("POST", "/api/push", { endpoint: PUSH.sub ? PUSH.sub.endpoint : "" });
    PUSH.err = "";
  } catch (e) { if (e instanceof NotPaired) return; PUSH.err = e.message; }
  miscChanged();
}
const pushOn = () => !!(PUSH.sub && PUSH.st && PUSH.st.subscribed && Notification.permission === "granted");
const pushHint = () => pushBlocker() ? "not here" : !PUSH.st ? "" : pushOn() ? "on" : "off";
const deviceLabel = () => /iPad/.test(navigator.userAgent) ? "iPad" : pushIos() ? "iPhone" : /Android/.test(navigator.userAgent) ? "Android" : "browser";
async function pushTurnOn() {
  // First thing in the tap: iOS asks for permission only from a user gesture, before any other await.
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error(perm === "denied" ? "Notifications are blocked for Orbit: iPhone Settings → Notifications → Orbit" : "Not allowed");
  const st = PUSH.st || await api("GET", "/api/push");
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (sub && sub.options && sub.options.applicationServerKey && bytesB64u(sub.options.applicationServerKey) !== st.publicKey) {
    await sub.unsubscribe(); sub = null;  // (made with another server key: a fresh one)
  }
  sub = sub || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uBytes(st.publicKey) });
  PUSH.st = await api("POST", "/api/push/subscribe", { subscription: sub.toJSON(), label: deviceLabel() });
  PUSH.sub = sub;
}
async function pushTurnOff() {
  const sub = PUSH.sub;
  if (sub) { await api("POST", "/api/push/unsubscribe", { endpoint: sub.endpoint }); await sub.unsubscribe().catch(() => {}); }
  PUSH.sub = null;
  await pushLoad();
}
const outcomeHtml = o => `<span class="po ${o === "accepted" ? "ok" : o === "duplicate" ? "" : "err"}">${esc(o)}</span>`;
function notifPage() {
  const page = makePage({ title: "Notifications" });
  page.misc = true;
  page.refresh = () => {
    const block = pushBlocker(), st = PUSH.st, on = pushOn();
    const denied = !block && Notification.permission === "denied";
    const reminders = (S.schedules || []).filter(x => x.notify);
    let h = `<div class="rows"><div class="mrow awake"><span class="aw-t"><span class="nm">Notifications on this ${esc(deviceLabel())}</span>` +
      `<span class="sub">${block ? esc(block) : denied ? "Blocked: iPhone Settings → Notifications → Orbit" : on ? "On: reminders and agents' reports come to the lock screen; a tap opens the session" : "Reminders and agents' reports on the lock screen, even with Orbit closed"}</span></span>` +
      (block ? "" : `<button class="switch${on ? " on" : ""}" data-push-toggle role="switch" aria-checked="${on}" aria-label="Notifications"${PUSH.busy || denied ? " disabled" : ""}><span></span></button>`) + `</div>` +
      (on ? `<button class="mrow prow" data-push-test>${I("bell", 18)}<span class="nm">Send a test notification</span></button>` : "") + `</div>`;
    if (PUSH.err) h += `<div class="note err psec">${esc(PUSH.err)}</div>`;
    if (on || reminders.length) {
      h += `<div class="sec psec">Remind me</div><div class="rows pform"><input id="pr-text" class="tinput" placeholder="What (e.g. Call the dentist)" enterkeyhint="next">` +
        `<div class="prline"><input id="pr-when" class="tinput" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="When: 30m · 9am · daily 8:00" enterkeyhint="done">` +
        `<button class="mjoin" data-push-remind>Add</button></div></div>`;
      if (reminders.length) h += `<div class="rows">` + reminders.map(it =>
        `<div class="mrow prem${it.enabled ? "" : " off"}"><span class="tx"><span class="nm">${esc(it.name)}</span><span class="t2">${esc(it.describe)} · ${it.enabled ? "next " + esc(fmtNext(it.next) || "—") : "done"}${it.last && it.last.error ? ` · <span class="err">${esc(it.last.error)}</span>` : ""}</span></span>` +
        `<button class="mjoin" data-push-del="${esc(it.id)}" aria-label="Delete ${esc(it.name)}">${I("x", 14)}</button></div>`).join("") + `</div>`;
    }
    const log = (st && st.log || []).slice(-6).reverse();
    if (log.length) h += `<div class="sec psec">Last sent</div><div class="rows">` + log.map(l =>
      `<div class="mrow plog"><span class="tx"><span class="nm">${esc(l.title || "")}</span><span class="t2">${esc(ago(l.at))} · ${outcomeHtml(l.outcome)}${l.status ? " " + l.status : ""}${l.reason ? " · " + esc(l.reason) : ""}${l.via ? " · " + esc(l.via) : ""}</span></span></div>`).join("") + `</div>`;
    if (st && st.subscriptions && st.subscriptions.length) h += `<div class="note psec">${st.subscriptions.length} device${st.subscriptions.length === 1 ? "" : "s"} subscribed on this server. Agents send with <code>orbit-agent notify</code>, reminders with <code>orbit-agent remind</code>.</div>`;
    const keep = [$("#pr-text", page.body), $("#pr-when", page.body)].map(x => x && x.value);  // (a redraw keeps what is typed)
    page.body.innerHTML = h;
    [$("#pr-text", page.body), $("#pr-when", page.body)].forEach((x, i) => { if (x && keep[i]) x.value = keep[i]; });
  };
  const run = async (what, f) => {
    PUSH.busy = true; PUSH.err = ""; page.refresh();
    try { await f(); } catch (e) { if (!(e instanceof NotPaired)) { PUSH.err = e.message || String(e); toast(PUSH.err); } }
    PUSH.busy = false; miscChanged();
  };
  page.body.addEventListener("click", e => {
    if (e.target.closest("[data-push-toggle]")) {
      if (PUSH.busy) return;
      if (pushOn()) run("off", pushTurnOff);
      else run("on", async () => { await pushTurnOn(); toast("Notifications are on"); });
      return;
    }
    if (e.target.closest("[data-push-test]")) {
      run("test", async () => {
        const r = await api("POST", "/api/push/send", { endpoint: PUSH.sub.endpoint, title: "Orbit", body: "Notifications work on this phone.", via: "phone test" });
        toast(r.sent ? "Sent: it shows in a moment (lock the phone to see it there)" : "Not sent: " + ((r.results[0] || {}).reason || (r.results[0] || {}).outcome || "no device"));
        await pushLoad();
      });
      return;
    }
    if (e.target.closest("[data-push-remind]")) {
      const text = $("#pr-text", page.body).value.trim(), w = $("#pr-when", page.body).value.trim();
      if (!text || !w) { toast("Say what, and when"); return; }
      run("remind", async () => {
        let when = w;
        try { await api("POST", "/api/schedules/parse", { when }); } catch { when = "in " + w; }  // (30m: as orbit-agent wake)
        const x = await api("POST", "/api/schedules", { when, text, name: text.slice(0, 40), notify: true });
        S.schedules = [...(S.schedules || []).filter(y => y.id !== x.id), x];
        $("#pr-text", page.body).value = ""; $("#pr-when", page.body).value = "";
        toast(`Reminder: ${fmtNext(x.next)}`);
      });
      return;
    }
    const del = e.target.closest("[data-push-del]");
    if (del) run("del", async () => {
      await api("POST", `/api/schedules/${encodeURIComponent(del.dataset.pushDel)}/delete`);
      S.schedules = (S.schedules || []).filter(y => y.id !== del.dataset.pushDel);
    });
  });
  page.onShow = () => { pushLoad(); loadMisc(); };
  page.refresh();
  pushLoad();
  return page;
}
/* A tap on a notification while the app runs (sw.js posts it): open its session. */
if ("serviceWorker" in navigator) navigator.serviceWorker.addEventListener("message", e => {
  const d = e.data || {};
  if (d.t !== "open") return;
  const sid = String(d.sid || "");
  if (sid && S.sessions) openSession(sid);  // (closed since: it says so)
});
