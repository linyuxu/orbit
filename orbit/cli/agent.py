"""`orbit-agent`: the command line agents (and people) use to work with a running Orbit.

    orbit-agent ctx [--json]                 what the user is looking at: tab, file, cursor, selection, open tabs
    orbit-agent open PATH[:LINE[-END]] [--side]
                                      open a file for the user at a line (a tab, or the side tab; never takes the keyboard)
    orbit-agent show FILE.html|-|URL [--title T]
                                      show an HTML page beside the conversation (a URL opens in the browser;
                                      other files open as a tab in the side tab)
    orbit-agent sessions [--json]            the open sessions
    orbit-agent groups [--json]              the groups (left panel order)
    orbit-agent new FOLDER [--name N] [--model M] [--mode MODE] [--group G] [--harness H] [TEXT…]
                                      start a session (and send TEXT as its first request)
    orbit-agent wait SESSION [--timeout S]   wait until SESSION's current request ends; print its answer
    orbit-agent history SESSION [--last N]   a session's turns, also of a closed one (default the last 30)
    orbit-agent search TERMS… [--limit N]    every session's requests and answers, and the notes
    orbit-agent continue SESSION             resume a paused request (an agent that went away mid-request)
    orbit-agent send SESSION TEXT|- [--wait] send a request to another session (name or id); --wait prints its answer

  SESSION may be NAME/session: a session on another machine's server (NAME from `orbit remote add`).
    orbit-agent steer SESSION TEXT|- [--wait]
                                      a message for the request SESSION is running now: it joins that turn
                                      (Claude Code); idle, or a harness that cannot, it becomes a request
    orbit-agent read SESSION [--last N]      another session's recent turns (request and answer)
    orbit-agent mention SESSION TEXT|-       a note to another agent (not a task); it will not mention you back unless asked
    orbit-agent ask TEXT|- [--folder DIR] [--no-wait]
                                      a one-off question to a Fleet helper (a background agent in DIR, default
                                      the current folder); prints its answer
    orbit-agent note TEXT... | - | --new NAME | --week
                                      write to the notes vault (the journal: this week's note; or a new note)
    orbit-agent pin TEXT... | - | --file F [--group G]
                                      pin something worth keeping to your group's home page (a decision, a
                                      conclusion, a key fact or link; not progress): the user sees it there
    orbit-agent wake WHEN TEXT               wake this session later with TEXT (WHEN: 30m, 2h, tomorrow 9am)
    orbit-agent notify TITLE [BODY…] [--session S] [--key K]
                                      a notification on the user's phone (lock screen), pointing at this session (or
                                      S); a tap opens it. For a finished report or a needed decision, not progress.
                                      --key K: sent once per K (a retry sends nothing twice)
    orbit-agent remind WHEN TEXT [--title T] [--session S | --no-session]
                                      a reminder to the phone at WHEN (in 30m, tomorrow 9am, daily 08:00, weekdays
                                      9:00, cron …): sent by the server, no agent wakes; a tap opens this session
    orbit-agent schedule [list]              the schedules
    orbit-agent schedule add WHEN TEXT [--name N] [--session S | --folder DIR | --fleet] [--max N] [--until HH:MM]
                                      send TEXT at WHEN (every 2h, daily 09:00, mon,wed 18:00, in 30m, cron 0 9 * * 1-5)
                                      to this session, another session, a new session in DIR each run, or a Fleet
                                      helper; at most N runs a day (default 24), none after HH:MM
    orbit-agent schedule watch --every 30s --check CMD TEXT [--include-existing] [same target / guards]
                                      run CMD every 30s with no agent (free); each line it prints is an event (JSON
                                      with "id", or text); only new ones wake the agent with TEXT and the events
    orbit-agent schedule check ID|NAME        run a watch's CMD now: its events, which are new (nothing is sent)
    orbit-agent schedule journal ID|NAME      the job's journal (every run reads it first and appends to it)
    orbit-agent schedule rm|run|pause|resume ID|NAME
    orbit-agent calendar [--days N] [--json] the user's meetings from today (when Calendar is on in Orbit's Settings)
    orbit-agent p [--stdin] [NAME ARGS…]     a plugin's command (its [cli]), with its secrets; no NAME: which plugins have one

Agents started by Orbit have $ORBIT_SESSION set, so what they open, show or note names them. $ORBIT_PORT picks the
server; without it, the one running on $ORBIT_STATE_DIR (else $ORBIT_HOME, else ~/.orbit), else 8770.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


FINAL = ("completed", "error", "cancelled", "paused")


def call(method: str, path: str, body: dict | None = None, timeout: float = 15) -> dict:
    from orbit.home import server_port
    port = server_port()  # $ORBIT_PORT, else the server of $ORBIT_STATE_DIR / $ORBIT_HOME / ~/.orbit, else 8770
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    # An agent outlives a restart of the server: it may call in the gap, so a refused connection is tried again
    # for 30 s ($ORBIT_AGENT_WAIT).
    end = time.time() + float(os.environ.get("ORBIT_AGENT_WAIT", "30"))
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", str(e))
            except ValueError:
                msg = str(e)
            sys.exit(f"orbit-agent: {msg}")
        except OSError as e:
            if isinstance(getattr(e, "reason", e), ConnectionRefusedError) and time.time() < end:
                time.sleep(0.5)
                continue
            sys.exit(f"orbit-agent: Orbit is not running on port {port} ({e.__class__.__name__})")


def me() -> str:
    return os.environ.get("ORBIT_SESSION", "")


def read_text(args: list[str]) -> str:
    text = " ".join(args)
    if not args or text == "-":
        text = sys.stdin.read()
    return text


# ------------------------------------------------------------------ note
def cmd_note(a: argparse.Namespace) -> None:
    if a.new:
        print(call("POST", "/api/notes/create", {"name": a.new})["path"])
        return
    if a.week:
        print(call("POST", "/api/notes/week", {})["path"])
        return
    text = read_text(a.text)
    if not text.strip():
        sys.exit("orbit-agent: nothing to add (give TEXT or pipe it in)")
    source = f"[session](orbit://session/{me()})" if me() else ""
    print(call("POST", "/api/notes/today", {"text": text, "source": source})["path"])


def cmd_pin(a: argparse.Namespace) -> None:
    text = open(a.file, encoding="utf-8").read() if a.file else read_text(a.text)
    if not text.strip():
        sys.exit("orbit-agent: nothing to pin (give TEXT, --file, or pipe it in)")
    d = call("GET", "/api/sessions")
    mine = next((s for s in d.get("sessions", []) if s["id"] == me()), None)
    if a.group:
        g = find_group(d.get("groups", []), a.group)
        if not g:
            sys.exit(f"orbit-agent: no group {a.group!r} (orbit-agent groups)")
        gid = g["id"]
    elif mine and mine.get("group") and mine["group"] != "fleet":
        gid = mine["group"]
    else:
        sys.exit("orbit-agent: which group? (--group G; a session outside Orbit or a Fleet helper has none of its own)")
    r = call("POST", f"/api/groups/{gid}/pin", {"text": text, "by": mine["name"] if mine else "", "sid": me()})
    print(f"pinned to the home of {next((p for g, p in group_paths(d.get('groups', [])) if g['id'] == gid), gid)} ({r['path']})")


# ------------------------------------------------------------------ ctx
def cmd_ctx(a: argparse.Namespace) -> None:
    c = call("GET", "/api/ctx")
    if a.json:
        print(json.dumps(c, indent=2, ensure_ascii=False))
        return
    if not c:
        print("Orbit has not reported what the user sees yet (no window open?).")
        return
    age = time.time() - c.get("at", time.time())
    out = []
    s = c.get("session") or {}
    tab = c.get("tab") or {}
    if tab.get("kind") == "file":
        where = tab.get("path", "")
        if tab.get("line"):
            where += f":{tab['line']}"
        out.append(f"tab: file {where}" + (" (unsaved changes)" if tab.get("dirty") else ""))
    elif s:
        out.append(f"tab: session {s.get('name')} ({s.get('id')})")
    if s:
        out.append(f"session: {s.get('name')} · {s.get('cwd')}")
    if c.get("group"):
        out.append(f"group: {c['group']}")
    if c.get("side"):
        out.append(f"side tab: {c['side']}")
    if c.get("files"):
        out.append("open files: " + ", ".join(c["files"]))
    sel = c.get("selection") or ""
    if sel:
        out.append("selection:\n" + "\n".join("    " + l for l in sel.split("\n")))
    out.append(f"(reported {int(age)} s ago)")
    print("\n".join(out))


# ------------------------------------------------------------------ open / show
def cmd_open(a: argparse.Namespace) -> None:
    path, line = a.path, None
    m = re.fullmatch(r"(.+?):(\d+)(?:-(\d+))?", path)
    if m and not os.path.exists(path):
        path, line = m.group(1), int(m.group(2))  # PATH:A-B opens at A
    r = call("POST", "/api/open", {"path": os.path.abspath(os.path.expanduser(path)), "line": line,
                                   "side": a.side, "by": me()})
    print(f"opened {r['path']}" + (f":{line}" if line else "") + (" in the side tab" if a.side else ""))


def cmd_show(a: argparse.Namespace) -> None:
    target = a.target
    if re.match(r"(?i)^https?://", target):
        if a.browser:
            call("POST", "/api/open-url", {"url": target})
            print(f"opened {target} in the browser")
            return
        r = call("POST", "/api/show", {"url": target, "title": a.title, "by": me()})
        print(f"showing {target} in the side tab (a site that refuses to be framed shows blank: its bar opens it in the browser; --browser opens it there)")
        return
    if target == "-":
        html = sys.stdin.read()
        r = call("POST", "/api/show", {"html": html, "title": a.title or "Page", "by": me()})
    else:
        path = os.path.abspath(os.path.expanduser(target))
        if not os.path.isfile(path):
            sys.exit(f"orbit-agent: no such file: {path}")
        if not path.lower().endswith((".html", ".htm")):
            r = call("POST", "/api/open", {"path": path, "side": True, "by": me()})
            print(f"opened {path} in the side tab")
            return
        r = call("POST", "/api/show", {"path": path, "title": a.title or os.path.basename(path), "by": me()})
    print(f"showing {r['title']} in the side tab ({r['url']})")


# ------------------------------------------------------------------ sessions / send
# A session on another machine is NAME/session (NAME from `orbit remote add`): every call goes through this
# server's /api/remote/NAME/…, which adds that machine's token.
class Target:
    def __init__(self, prefix: str, remote: str, s: dict):
        self.prefix, self.remote, self.s = prefix, remote, s

    @property
    def label(self) -> str:
        return f"{self.remote}/{self.s['name']}" if self.remote else self.s["name"]

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        return call(method, self.prefix + path, body)

    def mine(self) -> bool:
        return not self.remote and self.s["id"] == me()


def resolve(ref: str, closed: bool = False) -> Target:
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}~[\w-]+", ref):  # NAME~ID, as the server lists other machines' sessions
        ref = ref.replace("~", "/", 1)
    name, sep, rest = ref.partition("/")
    if sep and rest and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name):
        prefix = f"/api/remote/{name}"
        return Target(prefix, name, call("POST", prefix + "/api/resolve-session", {"ref": rest, "closed": closed}))
    return Target("", "", call("POST", "/api/resolve-session", {"ref": ref, "closed": closed}))


def cmd_sessions(a: argparse.Namespace) -> None:
    # The server lists every machine's sessions: another machine's as NAME~ID, with "remote": NAME.
    ss = call("GET", "/api/sessions" + ("?local=1" if a.local else ""))["sessions"]
    if a.json:
        print(json.dumps(ss, indent=2, ensure_ascii=False))
        return
    for s in ss:
        mark = " (you)" if s["id"] == me() else ""
        name = f"{s['remote']}/{s['name']}" if s.get("remote") else s["name"]
        print(f"{name}{mark}\t{s['state']}\t{s['id']}\t{s['cwd']}")
    if not a.local:
        for r in call("GET", "/api/remotes").get("remotes", []):
            if not r.get("ok") and not r.get("sessions"):
                print(f"{r['name']}/…\tunreachable\t{r['url']}\t{r.get('error', '')}")


def wait_answer(t: "Target | str", name: str, cid: int, timeout: int) -> None:
    get = t.call if isinstance(t, Target) else (lambda m, path, body=None: call(m, path, body))
    sid = t.s["id"] if isinstance(t, Target) else t
    end = time.time() + timeout
    while time.time() < end:
        cards = get("GET", f"/api/sessions/{sid}/cards")["cards"]
        c = next((x for x in cards if x["id"] == cid), None)
        if c and c["state"] in FINAL:
            if c["state"] != "completed":
                sys.exit(f"orbit-agent: {name} #{c['id']} ended {c['state']}: {c.get('error', '')}")
            print(c["answer"])
            return
        time.sleep(1)
    sys.exit(f"orbit-agent: no answer from {name} within {timeout} s (#{cid} is still running)")


def cmd_ask(a: argparse.Namespace) -> None:
    text = read_text(a.text)
    if not text.strip():
        sys.exit("orbit-agent: nothing to ask (give TEXT or pipe it in)")
    r = call("POST", "/api/fleet/ask", {"text": text, "cwd": os.path.abspath(a.folder or os.getcwd())})
    s, card = r["session"], r["card"]
    if a.no_wait:
        print(f"asked {s['name']} as #{card['id']}")
        return
    wait_answer(s["id"], s["name"], card["id"], a.timeout)


def cmd_send(a: argparse.Namespace) -> None:
    t = resolve(a.session)
    if t.mine():
        sys.exit("orbit-agent: that is this session; ask another one")
    text = read_text(a.text)
    if not text.strip():
        sys.exit("orbit-agent: nothing to send (give TEXT or pipe it in)")
    card = t.call("POST", f"/api/sessions/{t.s['id']}/send", {"text": _sender_prefix(text, t), **_agent_origin(),
                                                              **({"silent": True} if a.silent else {})})
    if not a.wait:
        print(f"sent to {t.label} as #{card['id']}")
        return
    wait_answer(t, t.label, card["id"], a.timeout)


def _agent_origin() -> dict:
    """Who sent it, kept with the card ({"origin": {"kind": "agent", "from": {sid, name, machine}}}): the text's prefix
    says it to the agent, this says it to Orbit (the group home's collaboration line, desk/stats.py). From a terminal
    ($ORBIT_SESSION unset): nothing (the prefix names its folder)."""
    if not me():
        return {}
    try:
        who = next((x["name"] for x in call("GET", "/api/sessions")["sessions"] if x["id"] == me()), "")
    except SystemExit:
        who = ""
    return {"origin": {"kind": "agent", "from": {"sid": me(), "name": who, "machine": socket.gethostname().split(".")[0]}}}


def _sender_prefix(text: str, t: "Target | None" = None) -> str:
    """Who is writing: an Orbit session by its name; an agent in a terminal ($ORBIT_SESSION unset) by its folder
    (or $ORBIT_AGENT_NAME), so the receiver knows it cannot be answered with `orbit-agent mention`. To another
    machine, the name of this one too."""
    where = f" on {socket.gethostname().split('.')[0]}" if t is not None and t.remote else ""
    if me():
        sender = call("GET", "/api/sessions")["sessions"]
        who = next((x["name"] for x in sender if x["id"] == me()), "")
        return f"(from session \"{who}\"{where}) {text}" if who else text
    name = os.environ.get("ORBIT_AGENT_NAME") or os.getcwd().replace(os.path.expanduser("~"), "~", 1)
    return f"(from a terminal session in {name}{where}; it cannot be mentioned back) {text}"


def cmd_relay(a: argparse.Namespace) -> None:
    """The user's own words, word for word, to another session (when the user asks for it): the receiver gets the
    words alone, its card says they are the user's (via this session). Orbit sends them only if they are the user's:
    said in a voice call to this session or typed here (orbit/desk/relay.py); a message of yours is orbit-agent send."""
    if not me():
        sys.exit("orbit-agent: relay passes on the user's words from an Orbit session ($ORBIT_SESSION is not set)")
    t = resolve(a.session)
    if t.mine():
        sys.exit("orbit-agent: that is this session")
    text = read_text(a.text)
    if not text.strip():
        sys.exit("orbit-agent: nothing to relay (give the user's words, or pipe them in)")
    target = f"{t.remote}~{t.s['id']}" if t.remote else t.s["id"]
    card = call("POST", "/api/relay", {"from": me(), "target": target, "text": text})
    proof = (card.get("origin") or {}).get("proof") or {}
    again = " (sent before: once is enough)" if card.get("duplicate") else ""
    print(f"relayed to {t.label} as #{card['id']}: the user's own words ({proof.get('kind', '?')}), nothing added{again}")


def cmd_steer(a: argparse.Namespace) -> None:
    t = resolve(a.session)
    if t.mine():
        sys.exit("orbit-agent: that is this session")
    text = read_text(a.text)
    if not text.strip():
        sys.exit("orbit-agent: nothing to steer with (give TEXT or pipe it in)")
    card = t.call("POST", f"/api/sessions/{t.s['id']}/steer", {"text": _sender_prefix(text, t), **_agent_origin()})
    how = f"joined {t.label} #{card['id']} (running)" if card.get("steered") else \
        f"sent to {t.label} as #{card['id']} (nothing to steer: {'not running' if t.s['state'] != 'working' else 'its harness cannot'})"
    if not a.wait:
        print(how)
        return
    print(how, file=sys.stderr)
    wait_answer(t, t.label, card["id"], a.timeout)


def cmd_read(a: argparse.Namespace) -> None:
    t = resolve(a.session, closed=bool(getattr(a, "closed", False)))
    cards = [c for c in t.call("GET", f"/api/sessions/{t.s['id']}/cards")["cards"] if not c.get("hidden")]
    for c in cards[-max(1, a.last):]:
        text = re.sub(r"^\[orbit\] [^\n]*\n\n", "", c["text"])
        own = " · on its own" if c.get("kind") == "auto" else ""  # (a turn no request started)
        o = c.get("origin") or {}
        if o.get("kind") == "user-relay":  # (the user's own words, passed on: where from is here, not in the text)
            own += f" · the user's own words, via {(o.get('from') or {}).get('name') or 'another session'} ({(o.get('proof') or {}).get('kind', '?')})"
        print(f"## #{c['id']} ({c['state']}{own})\n\n{text.strip()}\n")
        if c.get("answer"):
            print(f"### answer\n\n{c['answer'].strip()}\n")
    if not cards:
        print(f"{t.label} has no requests yet")


def cmd_mention(a: argparse.Namespace) -> None:
    t = resolve(a.session)
    if t.mine():
        sys.exit("orbit-agent: that is this session")
    text = read_text(a.text)
    if not text.strip():
        sys.exit("orbit-agent: nothing to say (give TEXT or pipe it in)")
    if t.remote or not me():
        # Another machine (or a terminal agent): no shared session to name; a note as a plain request.
        body = (_sender_prefix("", t).strip() + f" Note, not a task: {text.strip()}\n\n"
                "Do not reply unless it asks you to.")
        card = t.call("POST", f"/api/sessions/{t.s['id']}/send", {"text": body})
    else:
        card = t.call("POST", f"/api/sessions/{t.s['id']}/mention", {"from": me(), "text": text, "agent": True})
    print(f"mentioned {t.label} as #{card['id']}")


def cmd_groups(a: argparse.Namespace) -> None:
    d = call("GET", "/api/sessions")
    if a.json:
        print(json.dumps(d.get("groups", []), indent=2, ensure_ascii=False))
        return
    for g, path in group_paths(d.get("groups", [])):
        n = sum(1 for s in d["sessions"] if s.get("group") == g["id"])
        print(f"{path}\t{n} session{'s' if n != 1 else ''}\t{g['id']}")


def group_paths(groups: list[dict]) -> list[tuple[dict, str]]:
    """The groups as a tree, a parent before its subgroups: [(group, "Work/Orbit/Release")]."""
    out: list[tuple[dict, str]] = []

    def walk(parent: str | None, prefix: str) -> None:
        for g in groups:
            if (g.get("parent") or None) == parent:
                out.append((g, prefix + g["name"]))
                walk(g["id"], prefix + g["name"] + "/")
    walk(None, "")
    return out


def find_group(groups: list[dict], ref: str) -> dict | None:
    """A group by id, by path ("Work/Orbit"), or by name when only one group has it (case ignored)."""
    ref = ref.strip().strip("/").lower()
    paths = group_paths(groups)
    hit = next((g for g, p in paths if g["id"].lower() == ref or p.lower() == ref), None)
    if hit:
        return hit
    named = [(g, p) for g, p in paths if g["name"].lower() == ref]
    if len(named) > 1:
        sys.exit(f"orbit-agent: several groups are named {ref!r}: " + ", ".join(p for _, p in named) + " (give its path)")
    return named[0][0] if named else None


def cmd_new(a: argparse.Namespace) -> None:
    pre = f"/api/remote/{a.on}" if a.on else ""
    folder = a.folder if a.on else os.path.abspath(os.path.expanduser(a.folder))  # a remote folder: as given
    if not a.on and not os.path.isdir(folder):
        sys.exit(f"orbit-agent: no such folder: {folder}")
    body = {"cwd": folder, "name": a.name, "model": a.model, "mode": a.mode, "harness": a.harness}
    if a.group:
        groups = call("GET", pre + "/api/sessions").get("groups", [])
        g = find_group(groups, a.group)
        if not g:
            sys.exit(f"orbit-agent: no group {a.group!r} (orbit-agent groups)")
        body["group"] = g["id"]
    s = call("POST", pre + "/api/sessions", body)
    print(f"started {(a.on + '/') if a.on else ''}{s['name']} ({s['id']}) in {s['cwd']}")
    text = " ".join(a.text).strip()
    if text:
        card = call("POST", pre + f"/api/sessions/{s['id']}/send", {"text": text})
        print(f"sent its first request as #{card['id']}")


def cmd_fork(a: argparse.Namespace) -> None:
    """A new session in the same group continuing this one's conversation (or SESSION's), with all its settings."""
    ref = me() if a.session in ("", ".") else a.session  # ("." : this session, so a first request can follow)
    if not ref:
        sys.exit("orbit-agent: name the session to fork (not run inside an Orbit session)")
    t = resolve(ref)
    s = t.call("POST", f"/api/sessions/{t.s['id']}/fork", {"name": a.name, "harness": a.harness, "model": a.model})
    print(f"forked {t.label} → {s['name']} ({s['id']}): {s['harness']}{' · ' + s['model'] if s.get('model') else ''}, in its group")
    if s.get("fork_harness"):
        print("another harness: it reads this session's turns, checks the working tree, reports and waits for you")
    text = " ".join(a.text).strip()
    if text:
        card = t.call("POST", f"/api/sessions/{s['id']}/send", {"text": text})
        print(f"sent its first request as #{card['id']}")


def cmd_wait(a: argparse.Namespace) -> None:
    t = resolve(a.session)
    cards = [c for c in t.call("GET", f"/api/sessions/{t.s['id']}/cards")["cards"] if not c.get("hidden")]
    live = [c for c in cards if c["state"] not in FINAL]
    if not live:
        if cards and cards[-1].get("answer"):
            print(cards[-1]["answer"])
        else:
            print(f"{t.label} is not working on anything")
        return
    wait_answer(t, t.label, live[-1]["id"], a.timeout)


def cmd_history(a: argparse.Namespace) -> None:
    a.closed = True  # history also reaches a closed session
    cmd_read(a)


def cmd_search(a: argparse.Namespace) -> None:
    q = " ".join(a.terms).strip()
    if not q:
        sys.exit("orbit-agent: search for what?")
    d = call("GET", f"/api/search?q={urllib.parse.quote(q)}&limit={a.limit}")
    for c in d.get("cards", []):
        when = time.strftime("%m-%d %H:%M", time.localtime(c["time"])) if c.get("time") else ""
        print(f"{c['name']}{' (closed)' if c.get('closed') else ''} #{c['cid']} {c['where']} {when}\n    {c['snippet']}\n    orbit://session/{c['sid']}#{c['cid']}")
    for n in d.get("notes", []):
        print(f"note {n.get('path', '')}" + (f":{n['line']}" if n.get("line") else "") + (f"\n    {n.get('snippet') or n.get('line_text', '')}" if n.get("snippet") or n.get("line_text") else ""))
    if not d.get("cards") and not d.get("notes"):
        print("Nothing found.")


def cmd_continue(a: argparse.Namespace) -> None:
    t = resolve(a.session)
    cards = t.call("GET", f"/api/sessions/{t.s['id']}/cards")["cards"]
    paused = [c for c in cards if c["state"] == "paused" and not c.get("continued")]
    if not paused:
        sys.exit(f"orbit-agent: {t.label} has no paused request")
    t.call("POST", f"/api/sessions/{t.s['id']}/continue", {"cid": paused[-1]["id"]})
    print(f"continued {t.label} #{paused[-1]['id']}")


# ------------------------------------------------------------------ schedules
def _when_line(x: dict) -> str:
    nxt = time.strftime("%a %m-%d %H:%M", time.localtime(x["next"])) if x.get("next") else "—"
    state = "" if x["enabled"] else " (off)"
    return f"{x['id']}  {x['name']}{state}\n    {x['describe']} → {x['target_name']}; next {nxt}"


def cmd_schedule(a: argparse.Namespace) -> None:
    act = a.action or "list"
    if act == "list":
        xs = call("GET", "/api/schedules")["schedules"]
        if a.json:
            print(json.dumps(xs, indent=2, ensure_ascii=False))
        elif not xs:
            print("No schedules. orbit-agent schedule add \"daily 09:00\" \"check the overnight build\"")
        else:
            print("\n".join(_when_line(x) for x in xs))
        return
    guards = {k: v for k, v in (("max", a.max), ("until", a.until)) if v}
    if act == "add":
        if len(a.rest) < 2:
            sys.exit("orbit-agent: usage: orbit-agent schedule add WHEN TEXT (quote WHEN if it has spaces: \"daily 09:00\")")
        add(a.rest[0], read_text(a.rest[1:]), a.name, a.session, a.folder, fleet=a.fleet, **guards)
        return
    if act == "watch":
        if not a.check or not a.rest:
            sys.exit("orbit-agent: usage: orbit-agent schedule watch --every 30s --check \"CMD\" TEXT")
        text = read_text(a.rest)
        add("every " + a.every.removeprefix("every ").strip(), text, a.name or "watch: " + text[:30], a.session,
            a.folder, fleet=a.fleet, check=a.check, include_existing=a.include_existing, **guards)
        return
    if not a.rest:
        sys.exit(f"orbit-agent: usage: orbit-agent schedule {act} ID|NAME")
    ref = " ".join(a.rest)
    if act == "rm":
        call("POST", f"/api/schedules/{_sched_id(ref)}/delete", {})
        print("removed")
    elif act == "run":
        x = call("POST", f"/api/schedules/{_sched_id(ref)}/run", {})
        print(f"ran {x['name']}" + (f": {x['last']['error']}" if x["last"].get("error") else f" → {x['target_name']}"))
    elif act in ("on", "off", "pause", "resume"):
        x = call("POST", f"/api/schedules/{_sched_id(ref)}/update", {"enabled": act in ("on", "resume")})
        print(_when_line(x))
    elif act == "check":
        r = call("POST", f"/api/schedules/{_sched_id(ref)}/check", {})
        if r.get("error"):
            sys.exit(f"orbit-agent: {r['error']}")
        new = {e["id"] for e in r["new"]}
        for e in r["events"]:
            print(("new  " if e["id"] in new else "     ") + e["line"])
        print(f"{len(r['events'])} events, {len(new)} new" + ("" if r["primed"] else " (not run yet: its first run only learns them)"))
    elif act == "journal":
        sid = _sched_id(ref)
        j = next(x for x in call("GET", "/api/schedules")["schedules"] if x["id"] == sid).get("journal")
        if not j:
            sys.exit("orbit-agent: a one-off job keeps no journal")
        print(f"{j}\n\n" + Path(j).read_text(errors="replace"))
    else:
        sys.exit(f"orbit-agent: no such schedule command: {act} (list, add, watch, check, journal, rm, run, pause, resume)")


def _sched_id(ref: str) -> str:
    xs = call("GET", "/api/schedules")["schedules"]
    hits = [x for x in xs if x["id"] == ref or (len(ref) >= 4 and x["id"].startswith(ref))] or \
           [x for x in xs if x["name"].lower() == ref.lower()]
    if len(hits) != 1:
        sys.exit(f"orbit-agent: {'no' if not hits else 'more than one'} schedule matches {ref!r}")
    return hits[0]["id"]


def add(when: str, text: str, name: str = "", session: str = "", folder: str = "", fleet: bool = False, **more) -> None:
    body: dict = {"when": when, "text": text, "name": name, "by": me(), **more}
    if fleet:
        body.update(fleet=True, cwd=os.path.abspath(os.path.expanduser(folder or os.getcwd())))
    elif folder:
        body["cwd"] = os.path.abspath(os.path.expanduser(folder))
    elif session:
        body["sid"] = call("POST", "/api/resolve-session", {"ref": session})["id"]
    elif me():
        body["sid"] = me()
    else:
        sys.exit("orbit-agent: say which session (--session) or folder (--folder); outside Orbit there is no 'this session'")
    print(_when_line(call("POST", "/api/schedules", body)))


# ------------------------------------------------------------------ the phone: notifications
def _notify_line(r: dict) -> str:
    if not r["devices"]:
        return "no phone has notifications on (Orbit on the phone: Misc → Notifications)"
    bad = [x for x in r["results"] if x["outcome"] not in ("accepted", "duplicate")]
    out = f"sent to {r['sent']} of {r['devices']} device(s)" + (f", {r['duplicate']} had it already" if r.get("duplicate") else "")
    return out + "".join(f"\n  {x['sub']}: {x['outcome']} {x.get('status') or ''} {x.get('reason') or ''}".rstrip() for x in bad)


def cmd_notify(a: argparse.Namespace) -> None:
    sid = call("POST", "/api/resolve-session", {"ref": a.session})["id"] if a.session else me()
    body = read_text(a.body) if a.body else ""
    r = call("POST", "/api/push/send", {"title": a.title, "body": body, "sid": sid, "key": a.key, "via": "orbit-agent"})
    line = _notify_line(r)
    if not r["sent"] and not r.get("duplicate"):
        sys.exit("orbit-agent: " + line)
    print(line)


def cmd_hangup(a: argparse.Namespace) -> None:
    r = call("POST", "/api/live/hangup", {"sid": me()})
    print("the voice call ended" if r.get("ended") else "asked the machine that holds the voice call to end it")


def cmd_voice(a: argparse.Namespace) -> None:
    """The voice of a call with this session: its instructions, silent material, the ack (services/live/agentvoice.py)."""
    if not me():
        sys.exit("orbit-agent: voice works from inside an Orbit session ($ORBIT_SESSION)")
    path = f"/api/sessions/{me()}/voice"

    def text_of(src: str) -> str:
        if src == "-":
            return sys.stdin.read()
        try:
            return Path(src).read_text(encoding="utf-8")
        except OSError as e:
            sys.exit(f"orbit-agent: cannot read {src}: {e.strerror}")

    if a.op == "transcript":
        cmd_voice_transcript(a, path)
        return
    if a.op == "show":
        r = call("POST", path, {"op": "get"})
        src = {"session": "this session's", "folder": "the folder's .orbit/voice.md", "": "none"}[r.get("source", "")]
        print(f"instructions: {src}, about {r.get('tokens', 0)} of {r.get('max', 0)} tokens"
              + (f"\nreminder: {r['remind']}" if r.get("remind") else "")
              + f"\nack: {'Settings' if r.get('ack') is None else ('on' if r['ack'] else 'off')}"
              + "\ncall: " + ("running here" if r.get("call") else "running on the machine holding it"
                               if r.get("call_elsewhere") else "none running"))
        if r.get("instructions"):
            print("\n" + r["instructions"])
        return
    if a.op == "instruct":
        if not a.clear and not a.src:
            sys.exit("orbit-agent: voice instruct FILE (- : stdin), or --clear")
        body: dict = {"op": "instruct", "text": "" if a.clear else text_of(a.src), "clear": a.clear}
        if a.remind is not None:
            body["remind"] = a.remind
    elif a.op == "context":
        if not a.src:
            sys.exit("orbit-agent: voice context FILE (- : stdin)")
        body = {"op": "context", "text": text_of(a.src)}
    elif a.op == "say":
        text = read_text(a.words) if a.src == "-" or not a.src else " ".join([a.src, *a.words])
        body = {"op": "say", "text": text}
    else:  # ack
        if a.src not in ("on", "off", "default"):
            sys.exit("orbit-agent: voice ack on | off | default")
        body = {"op": "ack", "ack": {"on": True, "off": False, "default": None}[a.src]}
    r = call("POST", path, body)
    if not r.get("applied") and a.op in ("say", "context"):
        sys.exit("orbit-agent: not delivered: no voice call with this session is running (no machine took it)")
    if r.get("call") == "ended" and a.op in ("say", "context"):
        sys.exit("orbit-agent: not delivered: the call was ending")
    where = ("into the call now" + (f" (held by {r['by']})" if r.get("by") else "")) if r.get("applied") else ""
    if a.op == "instruct":
        print(f"voice instructions {'cleared' if a.clear else 'set'} (about {r.get('tokens', 0)} tokens, "
              f"{r.get('left', 0)} left); at the next call" + (f", and {where}" if where else " (no call running now)"))
    elif a.op == "say":
        print(f"said through the voice, {where}" + ("; the request running now gets no spoken result" if r.get("quiet") else ""))
    elif a.op == "context":
        print(f"material sent {where} (about {r.get('tokens', 0)} tokens; the voice has it in about "
              f"{r.get('lands_s', 0)} s, after what was sent before it)")
    else:
        print(f"ack {a.src}" + (f"; {where}" if where else ""))


def cmd_voice_transcript(a: argparse.Namespace, path: str) -> None:
    """The call's transcript by turn; --follow: each turn as it completes, until the call ends."""
    def line(t: dict) -> str:
        s = int(t.get("start") or 0) // 1000
        return f"[{s // 60}:{s % 60:02d}] {'User' if t.get('who') == 'user' else 'Voice'}: {t.get('text', '')}"

    since = int(a.since or 0)
    r = call("POST", path, {"op": "transcript", "since": since})
    if not r.get("call") and not a.follow:
        sys.exit("orbit-agent: no voice call with this session yet")
    if not a.follow:
        for t in r.get("turns") or []:
            print(line(t) + ("" if t.get("done") else " …"))
        if r.get("call"):
            print(f"(call {'ended' if r.get('ended') else 'running'})")
        return
    printed: set = set()
    while True:  # (no call yet: waits for one)
        for t in r.get("turns") or []:
            k = (t.get("who"), t.get("start"))
            if t.get("done") and k not in printed:
                printed.add(k)
                print(line(t), flush=True)
        if r.get("call") and r.get("ended"):
            print("(call ended)", flush=True)
            return
        time.sleep(1)
        r = call("POST", path, {"op": "transcript", "since": since})


def cmd_remind(a: argparse.Namespace) -> None:
    from orbit.services import schedule as sched
    when = a.when
    try:
        sched.parse(when)
    except ValueError:
        when = "in " + when  # (30m, 2h: as wake takes them)
    text = read_text(a.text)
    if not text.strip():
        sys.exit("orbit-agent: say what the reminder says")
    body: dict = {"when": when, "text": text, "name": a.title or text[:40], "by": me(), "notify": True}
    if a.session:
        body["sid"] = call("POST", "/api/resolve-session", {"ref": a.session})["id"]
    elif me() and not a.no_session:
        body["sid"] = me()
    print(_when_line(call("POST", "/api/schedules", body)))


def cmd_wake(a: argparse.Namespace) -> None:
    if not me():
        sys.exit("orbit-agent: wake works inside an Orbit session; use orbit-agent schedule add --session")
    when = a.when if re.match(r"(?i)^(in|today|tomorrow|once|\d{4}-)", a.when) else "in " + a.when
    text = read_text(a.text)
    if not text.strip():
        sys.exit("orbit-agent: say what to wake up for")
    add(when, text, name=a.name or "wake: " + text[:30])


# ------------------------------------------------------------------ calendar
def cmd_calendar(a: argparse.Namespace) -> None:
    t = time.localtime()
    start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    end = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + max(1, a.days), 0, 0, 0, 0, 0, -1))
    d = call("GET", f"/api/calendar?from={start}&to={end}")
    if a.json:
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return
    if not d.get("enabled"):
        sys.exit("orbit-agent: Calendar is off (the user turns it on in Orbit: Settings, ⌘,)")
    if d.get("error"):
        sys.exit(f"orbit-agent: {d['error']}")
    day = None
    for e in d["events"]:
        lt = time.localtime(e["start"])
        label = time.strftime("%a %Y-%m-%d", lt)
        if label != day:
            print(("\n" if day else "") + label)
            day = label
        when = "all day" if e.get("all_day") else time.strftime("%H:%M", lt) + "–" + time.strftime("%H:%M", time.localtime(e["end"]))
        extra = " · ".join(x for x in (e.get("location"), e.get("link")) if x)
        who = ", ".join(e.get("people") or [])
        print(f"  {when}  {e['title']}" + (f"  ({extra})" if extra else "") + ("  [own block]" if e.get("noise") and not e.get("all_day") else ""))
        if who or e.get("organizer"):
            print(f"      {('by ' + e['organizer']) if e.get('organizer') else ''}{' · ' if who and e.get('organizer') else ''}{('with ' + who) if who else ''}")
    if not d["events"]:
        print("No events.")


# ------------------------------------------------------------------ plugins
def cmd_p(a: argparse.Namespace) -> None:
    """A plugin's command ([cli] in its plugin.toml), run by the server with the plugin's secrets."""
    if not a.name:
        d = call("GET", "/api/plugins")
        cmds = [p for p in d["plugins"] if p.get("cli")]
        print("\n".join(f"{p['name']}: {p.get('hint') or p.get('description') or ''}" for p in cmds)
              or "No plugin has a command (plugin.toml [cli]).")
        return
    stdin = sys.stdin.read() if a.stdin else ""  # only when asked: an agent's stdin may be a pipe that never ends
    r = call("POST", f"/api/plugins/{a.name}/run", {"args": a.args, "stdin": stdin, "session": me()}, timeout=180)
    sys.stdout.write(r.get("stdout", ""))
    sys.stderr.write(r.get("stderr", ""))
    sys.exit(int(r.get("code", 1)))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="orbit-agent", description="Work with a running Orbit.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("note", help="write to the notes vault")
    n.add_argument("text", nargs="*", help="text to add to the journal, under today (- or none: stdin)")
    n.add_argument("--new", metavar="NAME", help="create the note NAME and print its path")
    n.add_argument("--week", action="store_true", help="print the path of the journal: this week's note (Monday to Sunday)")
    n.set_defaults(func=cmd_note)

    pn = sub.add_parser("pin", help="pin something worth keeping to your group's home page")
    pn.add_argument("text", nargs="*", help="Markdown to pin (- or none: stdin)")
    pn.add_argument("--file", help="pin this file's text")
    pn.add_argument("--group", default="", help="another group: its path (Work/Orbit), its name, or its id")
    pn.set_defaults(func=cmd_pin)

    c = sub.add_parser("ctx", help="what the user is looking at")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_ctx)

    o = sub.add_parser("open", help="open a file for the user")
    o.add_argument("path", help="PATH or PATH:LINE")
    o.add_argument("--side", action="store_true", help="in the side tab")
    o.set_defaults(func=cmd_open)

    sh = sub.add_parser("show", help="show an HTML page beside the conversation")
    sh.add_argument("target", help="FILE.html, - (HTML on stdin), another file, or a URL")
    sh.add_argument("--title", default="")
    sh.add_argument("--browser", action="store_true", help="a URL: open it in the browser, not beside")
    sh.set_defaults(func=cmd_show)

    ss = sub.add_parser("sessions", help="list the open sessions (here and on every remote: NAME/session)")
    ss.add_argument("--json", action="store_true")
    ss.add_argument("--local", action="store_true", help="only this machine's")
    ss.set_defaults(func=cmd_sessions)

    rl = sub.add_parser("relay", help="pass the user's own words, word for word, to another session (when the user asks)")
    rl.add_argument("session", help="name or id (NAME/session: another machine's)")
    rl.add_argument("text", nargs="*", help="the user's words (- or none: stdin)")
    rl.set_defaults(func=cmd_relay)

    se = sub.add_parser("send", help="send a request to another session")
    se.add_argument("session", help="name or id")
    se.add_argument("text", nargs="*", help="the request (- or none: stdin)")
    se.add_argument("--wait", action="store_true", help="wait for the answer and print it")
    se.add_argument("--timeout", type=int, default=600)
    se.add_argument("--silent", action="store_true", help="a voice call with that session does not hear of it")
    se.set_defaults(func=cmd_send)

    st = sub.add_parser("steer", help="add a message to the request another session is running now")
    st.add_argument("session", help="name or id")
    st.add_argument("text", nargs="*", help="the message (- or none: stdin)")
    st.add_argument("--wait", action="store_true", help="wait for that request to end and print its answer")
    st.add_argument("--timeout", type=int, default=600)
    st.set_defaults(func=cmd_steer)
    gr = sub.add_parser("groups", help="the groups")
    gr.add_argument("--json", action="store_true")
    gr.set_defaults(func=cmd_groups)
    nw = sub.add_parser("new", help="start a session in a folder")
    nw.add_argument("folder")
    nw.add_argument("text", nargs="*", help="its first request (optional)")
    nw.add_argument("--name", default="")
    nw.add_argument("--model", default="")
    nw.add_argument("--mode", default="default", choices=["default", "acceptEdits", "plan", "bypassPermissions"])
    nw.add_argument("--group", default="", help="group: its path (Work/Orbit), its name, or its id")
    nw.add_argument("--harness", default="claude", help="claude, codex, agy or opencode")
    nw.add_argument("--on", default="", metavar="REMOTE", help="start it on another machine (orbit remote ls); FOLDER is there")
    nw.set_defaults(func=cmd_new)
    fk = sub.add_parser("fork", help="fork a session (default: this one): a new session in its group, continuing its conversation with all its settings")
    fk.add_argument("session", nargs="?", default="", help="the session to fork (default, or \".\": $ORBIT_SESSION, the one you run in)")
    fk.add_argument("text", nargs="*", help="the fork's first request (optional)")
    fk.add_argument("--name", default="", help="the fork's name (default: NAME (fork), or NAME (HARNESS))")
    fk.add_argument("--harness", default="", help="continue in another harness (claude, codex, agy, opencode), e.g. when "
                    "this one is out of quota: a hand-over, its first request reads this session's turns and waits")
    fk.add_argument("--model", default="", help="with --harness: its model (default: the harness's own)")
    fk.set_defaults(func=cmd_fork)
    wt = sub.add_parser("wait", help="wait for a session's current request; print its answer")
    wt.add_argument("session")
    wt.add_argument("--timeout", type=int, default=600)
    wt.set_defaults(func=cmd_wait)
    hi = sub.add_parser("history", help="a session's turns (also a closed one)")
    hi.add_argument("session")
    hi.add_argument("--last", type=int, default=30)
    hi.set_defaults(func=cmd_history)
    sr = sub.add_parser("search", help="search every session's history and the notes")
    sr.add_argument("terms", nargs="*")
    sr.add_argument("--limit", type=int, default=20)
    sr.set_defaults(func=cmd_search)
    co = sub.add_parser("continue", help="resume a paused request")
    co.add_argument("session")
    co.set_defaults(func=cmd_continue)
    rd = sub.add_parser("read", help="another session's recent turns")
    rd.add_argument("session")
    rd.add_argument("--last", type=int, default=3)
    rd.set_defaults(func=cmd_read)
    mn = sub.add_parser("mention", help="a note to another agent (not a task)")
    mn.add_argument("session")
    mn.add_argument("text", nargs="*")
    mn.set_defaults(func=cmd_mention)
    ak = sub.add_parser("ask", help="a one-off question to a Fleet helper")
    ak.add_argument("text", nargs="*", help="the question (- or none: stdin)")
    ak.add_argument("--folder", default="", help="where the helper works (default: the current folder)")
    ak.add_argument("--no-wait", action="store_true", help="print the request number instead of the answer")
    ak.add_argument("--timeout", type=int, default=600)
    ak.set_defaults(func=cmd_ask)

    w = sub.add_parser("wake", help="wake this session later")
    w.add_argument("when", help="30m, 2h, tomorrow 9am …")
    w.add_argument("text", nargs="*", help="what to do then (- or none: stdin)")
    w.add_argument("--name", default="")
    w.set_defaults(func=cmd_wake)

    nt = sub.add_parser("notify", help="a notification on the user's phone, pointing at this session")
    nt.add_argument("title")
    nt.add_argument("body", nargs="*", help="its text (- : stdin)")
    nt.add_argument("--session", default="", help="the session a tap opens (default: this one)")
    nt.add_argument("--key", default="", help="sent once per key (a retry with the same key sends nothing again)")
    nt.set_defaults(func=cmd_notify)

    hu = sub.add_parser("hangup", help="end the voice call with this session (when the user asks)")
    hu.set_defaults(func=cmd_hangup)

    vo = sub.add_parser("voice", help="shape the voice of a call with this session: instructions, silent material, ack")
    vo.add_argument("op", choices=["instruct", "context", "ack", "say", "transcript", "show"])
    vo.add_argument("src", nargs="?", default="", help="instruct/context: FILE (- : stdin); ack: on | off | default; "
                                                      "say: the words (- or none: stdin)")
    vo.add_argument("words", nargs="*", help=argparse.SUPPRESS)
    vo.add_argument("--since", type=int, default=0, help="transcript: turns from this many ms into the call")
    vo.add_argument("--follow", action="store_true", help="transcript: each turn as it completes, until the call ends")
    vo.add_argument("--remind", default=None, help="instruct: a one-line reminder sent with each context batch")
    vo.add_argument("--clear", action="store_true", help="instruct: drop this session's instructions")
    vo.set_defaults(func=cmd_voice)

    rm = sub.add_parser("remind", help="a reminder to the user's phone at a time (once or repeating); no agent wakes")
    rm.add_argument("when", help="in 30m, tomorrow 9am, daily 08:00, weekdays 9:00, every 2h, cron …")
    rm.add_argument("text", nargs="*", help="what it says (- or none: stdin)")
    rm.add_argument("--title", default="", help="its title (default: the text's start)")
    rm.add_argument("--session", default="", help="the session a tap opens (default: this one)")
    rm.add_argument("--no-session", action="store_true", help="a tap opens Orbit, no session")
    rm.set_defaults(func=cmd_remind)

    sc = sub.add_parser("schedule", help="list, add or change schedules")
    sc.add_argument("action", nargs="?", help="list (default), add, watch, check, journal, rm, run, pause, resume")
    sc.add_argument("rest", nargs="*", help="add: WHEN TEXT…; watch: TEXT…; others: ID or NAME")
    sc.add_argument("--name", default="")
    sc.add_argument("--session", default="", help="the session to send to (name or id; default: this one)")
    sc.add_argument("--folder", default="", help="start a new session in this folder for each run (with --fleet: its folder)")
    sc.add_argument("--fleet", action="store_true", help="send to an idle Fleet helper")
    sc.add_argument("--every", default="5m", help="watch: how often the check runs (10s at least)")
    sc.add_argument("--check", default="", help="watch: the shell command that prints events")
    sc.add_argument("--include-existing", action="store_true", help="watch: the first run's events wake it too")
    sc.add_argument("--max", type=int, default=0, help="agent runs a day at most (default 24)")
    sc.add_argument("--until", default="", help="no run after this time of day (HH:MM)")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(func=cmd_schedule)

    pp = sub.add_parser("p", help="run a plugin's command (no NAME: the plugins that have one)")
    pp.add_argument("--stdin", action="store_true", help="pass this command's stdin to it (before NAME)")
    pp.add_argument("name", nargs="?", default="")
    pp.add_argument("args", nargs=argparse.REMAINDER, help="its arguments")
    pp.set_defaults(func=cmd_p)

    ca = sub.add_parser("calendar", help="the user's meetings (read-only)")
    ca.add_argument("--days", type=int, default=1, help="how many days from today (default 1)")
    ca.add_argument("--json", action="store_true")
    ca.set_defaults(func=cmd_calendar)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
