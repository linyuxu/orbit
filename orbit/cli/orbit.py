"""`orbit`: run and manage Orbit itself (people, not agents; agents use `orbit-agent`).

    orbit serve [--port N] [--state-dir D] [--data-root D]
                              the server: the backend the app and every agent talk to (the app starts it
                              when nothing listens on the port; on a machine without the app, run it yourself)
    orbit tools install       make Orbit's tools available to every harness, in any terminal:
                              ~/.local/bin/orbit-agent and ~/.local/bin/orbit (links to <repo>/cli), and the
                              orbit-agents skill in ~/.claude/skills, ~/.codex/skills and ~/.gemini/skills (agy)
    orbit tools uninstall     remove exactly those links (only links that point into this repository)
    orbit tools status        what is installed where
    orbit plugin list         the plugins (<state dir>/plugins/*, plugin_paths in config.toml): loaded or why not
    orbit plugin check [DIR]  load them (and DIR) and ask each harness to describe itself: what would go wrong
    orbit plugin secret NAME KEY [--clear]   keep one of a plugin's secrets (asked for; never echoed, never sent out)
    orbit plugin restart NAME [SERVICE]      restart a plugin's services   ·   orbit plugin logs NAME [SERVICE] [-n N]
    orbit stop [--agents]     stop this machine's server of the state dir (--state-dir, as serve). Plain: a restart-
                              style stop, its agents run on in the Harness Server until the next `orbit serve`
                              takes them over (direct mode: they end too, and it says so). --agents: every agent
                              ends, and the Harness Server with them
    orbit harness-server status   the Harness Server (it runs the agents, so they outlive a restart of the server):
                              its pid, its processes and their memory, and whether the server uses it
    orbit harness-server restart  a new one (the code on disk): every agent process ends; their sessions stay and
                              a request at work goes on by itself
    orbit harness-server stop     stop it: every agent ends (their sessions resume on the next request)
    orbit migrate-home        move the folder of an older Orbit (its old name) to ~/.orbit, a link left behind (never done on
                              its own: $ORBIT_HOME, another instance's folder, is never touched)

  Another instance on the same machine: its own $ORBIT_HOME (state and data under it, unless $ORBIT_STATE_DIR /
  $ORBIT_DATA_ROOT say otherwise) and $ORBIT_PORT. `orbit` and `orbit-agent` reach the server of that state folder
  (its server.json) when $ORBIT_PORT is not set.

  Remote (other machines on a private network: Tailscale, a LAN)
    orbit serve --remote [--bind ADDR]
                              also serve other machines (all addresses, or only ADDR); they need the token
    orbit url [--host H]      the URL another machine connects with (address + token)
    orbit token [--rotate]    print the token; --rotate replaces it (every remote must be added again)
    orbit remote add [NAME] URL  reach the server at URL (from `orbit url` there); its sessions are NAME/session (NAME: the host)
    orbit remote ls           the remotes, whether they answer, their sessions
    orbit remote rm NAME

  On a machine without the app (a Linux box), keep the server running in tmux:
    tmux new -d -s orbit 'cd ~/orbit && cli/orbit serve --remote'

Launched through <repo>/cli/orbit (the repository's own Python; standard library only).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from orbit import ROOT


CLI = ROOT / "cli"
SKILL = ROOT / "plugin" / "skills" / "orbit-agents"


def _links() -> list[tuple[Path, Path, str]]:
    """(where the link goes, what it points to, what it is for)."""
    home = Path.home()
    out = [(home / ".local" / "bin" / "orbit-agent", CLI / "orbit-agent", "orbit-agent on PATH (agents in any terminal)"),
           (home / ".local" / "bin" / "orbit", CLI / "orbit", "orbit on PATH")]
    for base, who in ((home / ".claude", "Claude Code"), (home / ".codex", "Codex"), (home / ".gemini", "agy")):
        out.append((base / "skills" / "orbit-agents", SKILL, f"the orbit-agents skill for {who}"))
    return out


def _ours(link: Path) -> bool:
    try:
        return link.is_symlink() and Path(os.path.realpath(link)).is_relative_to(ROOT)
    except OSError:
        return False


LEGACY = ["aide"]  # earlier names of the agents' command: their links (ours, now dangling) are cleaned up


def _clean_legacy() -> None:
    for name in LEGACY:
        old = Path.home() / ".local" / "bin" / name
        if _ours(old):
            old.unlink()
            print(f"removed  {old}  (the old name of orbit-agent)")


def tools_install(dry: bool = False) -> int:
    home, problems = Path.home(), 0
    if not dry:
        _clean_legacy()
    for link, target, what in _links():
        harness_home = link.parent.parent if link.parent.name == "skills" else None
        if harness_home is not None and not harness_home.is_dir():
            print(f"skip     {link}  ({what}: {harness_home} does not exist, the harness is not set up here)")
            continue
        if _ours(link) and Path(os.path.realpath(link)) == target.resolve():
            print(f"ok       {link}")
            continue
        if link.exists() or link.is_symlink():
            print(f"KEEP     {link}  (something else is there; not replaced; remove it and run again)")
            problems += 1
            continue
        print(f"link     {link} → {target}  ({what})")
        if not dry:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
    bindir = str(home / ".local" / "bin")
    if bindir not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"\nnote: {bindir} is not on PATH; add it to your shell's startup file, e.g.\n"
              f"      export PATH=\"$HOME/.local/bin:$PATH\"")
    print("\nAgents started in a terminal now have `orbit-agent` and the orbit-agents skill; they reach the server on "
          f"127.0.0.1:{_port()} (the server of {_state()}; $ORBIT_PORT overrides).")
    return 1 if problems else 0


def tools_uninstall() -> int:
    _clean_legacy()
    for link, _, _ in _links():
        if _ours(link):
            link.unlink()
            print(f"removed  {link}")
        elif link.exists() or link.is_symlink():
            print(f"left     {link}  (not ours)")
    return 0


def tools_status() -> int:
    for link, target, what in _links():
        state = "installed" if _ours(link) else ("other" if link.exists() or link.is_symlink() else "missing")
        print(f"{state:9} {link}  ({what})")
    on_path = shutil.which("orbit-agent")
    print(f"\naide on PATH: {on_path or 'no'}")
    return 0


# ------------------------------------------------------------------ remote
STATE: str | None = None  # --state-dir (else $ORBIT_STATE_DIR, else ~/.orbit)


def _state() -> Path:
    from orbit.server import state_dir
    return state_dir(STATE)


def _port() -> int:
    from orbit.home import server_port
    return server_port(_state())


def cmd_url(host: str) -> int:
    from orbit.machines import remote
    hosts = [host] if host else (remote.addresses() or ["HOST"])
    for h in hosts:
        print(remote.url(_state(), h, _port()))
    return 0


def cmd_https(action: str, port: int) -> int:
    """HTTPS for other machines and the phone, by Tailscale (machines/https.py)."""
    from orbit.machines import https, remote
    try:
        st = https.enable(_state(), _port(), port) if action == "on" else https.disable(_state(), _port()) if action == "off" \
            else https.status(_state(), _port(), fresh=True)
    except RuntimeError as e:
        print(f"orbit https: {e}", file=sys.stderr)
        return 1
    if st["on"]:
        tok = remote.token(_state())
        print(f"on: {st['url']}  (Tailscale's certificate for {st['host']})")
        print(f"  another machine:  orbit remote add {st['host'].split('.')[0]} '{st['url']}/?token={tok}'")
        print(f"  the iPhone:       {st['url']}/m/?token={tok}   (or Settings → Machines → iPhone…: a QR code)")
        print("  plain HTTP from the network now goes to that address; this machine's own page stays on http://127.0.0.1")
    else:
        print("off" + (f" ({st['error']})" if st["error"] else "") + (f" · this machine: {st['host']}" if st["host"] else ""))
    return 0


def cmd_token(rotate: bool) -> int:
    from orbit.machines import remote
    print(remote.rotate(_state()) if rotate else remote.token(_state()))
    if rotate:
        print("the old token no longer works: every machine that reached this one must add it again", file=sys.stderr)
    return 0


def cmd_push_relay(action: str, a: str, b: str) -> int:
    """Phone notifications from another machine's agents (orbit/services/push.py): where the phone subscribed,
    `add NAME` makes a relay token for that machine; there, `upstream URL TOKEN` forwards its notifications here."""
    from orbit.services.push import Push
    p = Push(_state())
    try:
        if action == "add":
            if not a:
                sys.exit("orbit: usage: orbit push-relay add NAME   (NAME: what this server calls that machine)")
            tok = p.relay_add(a)
            from orbit.machines import https
            url = https.saved_url(_state()) or "https://THIS-MACHINE:8443"
            print(f"relay token for {a} (it opens POST /api/push/relay only). On {a}:")
            print(f"  orbit push-relay upstream {url} {tok}")
        elif action == "rm":
            print("removed" if p.relay_remove(a) else f"no relay token for {a}")
        elif action == "upstream":
            p.set_upstream(a, b)
            print(f"notifications with no phone here go to {a}" if a else "no upstream: notifications stay here")
        else:  # ls
            import json
            rel = json.loads((p.dir / "relays.json").read_text()) if (p.dir / "relays.json").exists() else {}
            print("relay tokens here: " + (", ".join(sorted(v["name"] for v in rel.values())) or "none"))
            up = p.upstream()
            print("upstream: " + (up["url"] if up else "none"))
    except ValueError as e:
        sys.exit(f"orbit: {e}")
    return 0


def cmd_remote(action: str, name: str, url: str) -> int:
    from orbit.machines import remote
    st = _state()
    if action == "add":
        if name and not url and "://" in name:  # orbit remote add URL: its host names it
            name, url = "", name
        if not url:
            sys.exit("orbit: usage: orbit remote add [NAME] URL   (URL from `orbit url` on that machine)")
        try:
            r = remote.add(st, name, url)
        except (ValueError, remote.RemoteError) as e:
            sys.exit(f"orbit: {e}")
        print(f"added {r['name']} ({r['url']}): its sessions are {r['name']}/<session> for orbit-agent")
        return 0
    if action == "rm":
        try:
            remote.remove(st, name)
        except KeyError:
            sys.exit(f"orbit: no remote called {name}")
        print(f"removed {name}")
        return 0
    rows = remote.status(st)
    if not rows:
        print("No remotes. On the other machine: orbit serve --remote; orbit url. Here: orbit remote add URL")
    for r in rows:
        state = f"{len(r['sessions'])} sessions" if r["ok"] else f"unreachable: {r.get('error', '')}"
        print(f"{r['name']}\t{r['url']}\t{state}")
        for s in r["sessions"]:
            print(f"    {r['name']}/{s['name']}\t{s['state']}\t{s['cwd']}")
    return 0


def _server(method: str, path: str, body: dict | None = None) -> dict | None:
    """The running server's answer (None: no server on ORBIT_PORT / the state dir's server.json / 8770)."""
    import json
    import urllib.error
    import urllib.request
    port = _port()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", str(e))
        except ValueError:
            msg = str(e)
        sys.exit(f"orbit: {msg}")
    except OSError:
        return None


def cmd_plugin_manage(action: str, args: list[str], clear: bool, lines: int) -> int:
    """secret NAME KEY · restart NAME [SERVICE] · logs NAME [SERVICE]"""
    if not args:
        sys.exit(f"orbit plugin {action}: which plugin?")
    name = args[0]
    if action == "secret":
        if len(args) < 2:
            sys.exit("orbit plugin secret NAME KEY [--clear]")
        key, value = args[1], None
        if not clear:
            import getpass
            value = getpass.getpass(f"{name} {key}: ") if sys.stdin.isatty() else sys.stdin.read().strip()
            if not value:
                sys.exit("orbit: nothing given (--clear forgets a secret)")
        if _server("POST", f"/api/plugins/{name}/secret", {"key": key, "value": value}) is None:
            from orbit import plugins
            from orbit.server.run import state_dir
            plugins.load(state_dir(STATE))  # no server running: write it here (its services get it when it starts)
            try:
                plugins.set_secret(name, key, value)
            except (KeyError, ValueError) as e:
                sys.exit(f"orbit: {e}")
        print(f"{name}: {key} " + ("forgotten" if clear else "kept (its services restarted)"))
        return 0
    svc = args[1] if len(args) > 1 else ""
    if action == "restart":
        d = _server("POST", f"/api/plugins/{name}/restart", {"service": svc})
        if d is None:
            sys.exit("orbit: the server is not running (its services start with it)")
        for s in d["services"]:
            print(f"{name}.{s['id']}: {s['state']}")
        return 0
    from urllib.parse import quote
    d = _server("GET", f"/api/plugins/{name}/log?lines={lines}" + (f"&service={quote(svc)}" if svc else ""))
    if d is None:
        sys.exit("orbit: the server is not running")
    print(d["log"])
    return 0


def cmd_plugin(action: str, dirs: list[str]) -> int:
    from orbit import API_VERSION, plugins
    from orbit.server.run import state_dir
    state = state_dir(STATE)
    dirs = [str(Path(d).expanduser().resolve()) for d in dirs]  # (relative to here, not to the state dir)
    found = plugins.check(state, dirs) if action == "check" else plugins.load(state, dirs)
    print(f"Orbit plugin API {API_VERSION[0]}.{API_VERSION[1]} · {state / 'plugins'}")
    if not found:
        print("No plugins. Put one in that folder (a git clone), or list its folder in plugin_paths (config.toml).")
        return 0
    bad = 0
    for p in found:
        mark = "ok " if p["ok"] else ("off" if not p["enabled"] else "ERR")
        bad += not p["ok"] and p["enabled"]
        print(f"{mark} {p['name']} {p['version']}  {p['path']}")
        if p["harnesses"]:
            print(f"    harnesses: {', '.join(p['harnesses'])}")
        if p.get("people"):  # (API 1.2) [people]: check asks it about one name
            said = p.get("people_check")
            print("    people: says who a name is" + (f" · {said}" if said else ""))
        srv = _server("GET", "/api/plugins") if action == "list" else None
        live = {x["name"]: x for x in (srv or {}).get("plugins") or []}
        why = "(not loaded by the running server: restart it)" if srv else "(the server is not running)"
        for s in ((live.get(p["name"]) or {}).get("services") or [{"id": x, "state": why} for x in p.get("services") or []]
                  if action == "list" else []):
            print(f"    service {s['id']}: {s['state']}" + (f" · {s['error']}" if s.get("error") else ""))
        m = plugins.LOADED.get(p["name"])
        if m and m["secrets"]:
            have = plugins.get_secrets(p["name"])
            print("    secrets: " + ", ".join(f"{k} ({'set' if k in have else 'NOT SET'})" for k in m["secrets"]))
        for hid, d in (p.get("described") or {}).items():
            print(f"    {hid}: {d['label']} · models {', '.join(d['models']) or '(its default)'} · steer {'yes' if d['steer'] else 'no'}"
                  + (" · this machine only" if d["local_only"] else ""))
        if p["error"]:
            print(f"    {p['error']}")
    return 1 if bad else 0


def _harness_restart(state: Path) -> int | None:
    """`orbit harness-server restart` through this state dir's running Orbit Server (it closes its agents, then
    starts a new Harness Server). None: no Orbit Server runs for it."""
    import json
    import urllib.error
    import urllib.request
    try:
        info = json.loads((state / "server.json").read_text())
        port = int(info["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not _lock_held(state):
        return None
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/harness-server/restart", method="POST", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", str(e))
        except ValueError:
            msg = str(e)
        sys.exit(f"orbit: {msg}")
    except OSError as e:
        sys.exit(f"orbit: the Orbit Server (port {port}) did not answer: {e}")
    print(f"Restarted the Harness Server (pid {d.get('old_pid')} → {d.get('pid')}): {d.get('ended', 0)} agent "
          "process(es) ended; their sessions stay, and a request at work goes on by itself.")
    return 0


def cmd_harness_server(action: str) -> int:
    import time
    from orbit.harness_server.client import Client
    from orbit.server.run import state_dir
    state = state_dir(STATE)
    if action == "restart":
        done = _harness_restart(state)
        if done is not None:
            return done
        action = "stop"  # no Orbit Server: the next `orbit serve` starts a new one
    c = Client(state, "orbit harness-server")
    try:
        hello = c.connect(start=False)
    except OSError:
        print(f"No Harness Server runs for {state}.")
        if action == "status":
            srv = _server("GET", "/api/harness-server")
            if srv:
                print(f"The server runs its agents {srv.get('mode')}" + (f": {srv['why']}" if srv.get("why") else "."))
        return 0
    try:
        if action == "stop":
            c.request("shutdown")
            print(f"Stopped the Harness Server (pid {hello['pid']}): its agents end; their sessions resume on the next request.")
            return 0
        procs = c.request("list")["procs"]
    finally:
        c.close()
    up = int(time.time() - float(hello.get("started") or time.time()))
    print(f"Harness Server {hello.get('version')} (protocol {hello.get('protocol')}) · pid {hello['pid']} · "
          f"up {up // 3600}h{up % 3600 // 60:02d}m · {state / 'harness'}")
    for p in procs:
        rss = f"{p['rss'] / 1e6:.0f} MB" if p.get("rss") else "?"
        print(f"  {p['proc']}  pid {p['pid']}  " + ("running" if p["alive"] else f"exited ({p['code']})") +
              f"  {rss}  {p.get('tag') or ''}")
    if not procs:
        print("  (no processes)")
    srv = _server("GET", "/api/harness-server")
    if srv:
        print(f"The server runs its agents {srv.get('mode')}" + (f": {srv['why']}" if srv.get("why") else "."))
    return 0


def _lock_held(state: Path) -> bool:
    from orbit.home import lock_held
    return lock_held(state)


def cmd_stop(agents: bool, wait: float = 30.0) -> int:
    """Stop the Orbit Server of this state dir, found by <state>/server.json (never by a default port): POST
    /api/shutdown with its pid; unreachable, a signal to that pid while it holds server.lock. Waits until the lock
    is released."""
    import json
    import signal
    import time
    import urllib.error
    import urllib.request
    state = _state()
    try:
        info = json.loads((state / "server.json").read_text())
        pid = int(info["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        info, pid = {}, 0
    if not _lock_held(state):
        print(f"No Orbit Server runs for {state}.")
        return 0
    if not pid:
        sys.exit(f"orbit: a server holds {state / 'server.lock'} but left no server.json (an older version?): "
                 "stop it from the app or by its pid")
    said: dict = {}
    if info.get("port"):
        req = urllib.request.Request(f"http://127.0.0.1:{int(info['port'])}/api/shutdown", method="POST",
                                     data=json.dumps({"pid": pid, **({"agents": "stop"} if agents else {})}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                said = json.loads(r.read() or b"{}")
        except (OSError, ValueError):  # (refused, a 409: another server on that port) → by signal
            said = {}
    how = "asked"
    if not said.get("ok"):
        if not _lock_held(state):
            print(f"No Orbit Server runs for {state}.")
            return 0
        try:
            os.kill(pid, signal.SIGUSR1 if agents else signal.SIGTERM)
        except OSError as e:
            sys.exit(f"orbit: could not signal the server (pid {pid}): {e}")
        how = "signalled"
    end = time.time() + wait
    while _lock_held(state):
        if time.time() > end:
            sys.exit(f"orbit: the server (pid {pid}) did not stop within {wait:g} s")
        time.sleep(0.1)
    mode = said.get("mode")
    if agents:
        print(f"Stopped the Orbit Server (pid {pid}, {how}) with its agents; the Harness Server ended too.")
    elif mode == "hosted":
        print(f"Stopped the Orbit Server (pid {pid}). Its agents run on in the Harness Server: the next "
              "`orbit serve` takes them over (`orbit stop --agents` ends them).")
    elif mode == "direct":
        print(f"Stopped the Orbit Server (pid {pid}). Its agents ran in the server ({said.get('why') or 'direct'}), "
              "so they ended too; their requests go on at the next start.")
    else:
        print(f"Stopped the Orbit Server (pid {pid}, signalled: a restart-style stop). Hosted, its agents run on in "
              "the Harness Server; direct, they ended with it.")
    return 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="orbit", description="Run and manage Orbit (agents use `orbit-agent`).")
    from orbit import API_VERSION, __version__
    ap.add_argument("--version", action="version", version=f"orbit {__version__} (plugin API {API_VERSION[0]}.{API_VERSION[1]})")
    ap.add_argument("--state-dir", dest="state_top", default=None, help="the server's state folder (default $ORBIT_STATE_DIR, else $ORBIT_HOME, else ~/.orbit)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="run the server (the backend)")
    sv.add_argument("--port", type=int, default=None)
    sv.add_argument("--state-dir", default=None)
    sv.add_argument("--data-root", default=None)
    sv.add_argument("--remote", action="store_true", help="also serve other machines (they need the token)")
    sv.add_argument("--bind", action="append", default=[], metavar="ADDR", help="serve other machines on ADDR only")
    ur = sub.add_parser("url", help="the URL another machine connects with")
    ur.add_argument("--host", default="")
    hs = sub.add_parser("https", help="HTTPS for other machines and the phone, by Tailscale (tailscale serve)")
    hs.add_argument("action", nargs="?", default="status", choices=["on", "off", "status"])
    hs.add_argument("--port", type=int, default=8443, help="the HTTPS port on the tailnet (default 8443)")
    tk = sub.add_parser("token", help="print (or --rotate) the token other machines need")
    tk.add_argument("--rotate", action="store_true")
    pr = sub.add_parser("push-relay", help="phone notifications from another machine's agents: add NAME (here, where the phone "
                                           "subscribed) · upstream URL TOKEN (there) · rm NAME · ls")
    pr.add_argument("action", choices=["add", "rm", "upstream", "ls"])
    pr.add_argument("a", nargs="?", default="")
    pr.add_argument("b", nargs="?", default="")
    rm = sub.add_parser("remote", help="servers on other machines")
    rm.add_argument("action", choices=["add", "ls", "rm"])
    rm.add_argument("name", nargs="?", default="")
    rm.add_argument("url", nargs="?", default="")
    pl = sub.add_parser("plugin", help="plugins: list, check, secrets, restart their services, their logs")
    pl.add_argument("action", choices=["list", "check", "secret", "restart", "logs"])
    pl.add_argument("dirs", nargs="*", help="check: plugin folders besides the installed ones; secret: NAME KEY; "
                                             "restart, logs: NAME [SERVICE]")
    pl.add_argument("--clear", action="store_true", help="secret: forget it")
    pl.add_argument("-n", "--lines", type=int, default=200, help="logs: how many lines")
    st = sub.add_parser("stop", help="stop the server of the state dir: its agents run on in the Harness Server "
                                     "(--agents: they end too)")
    st.add_argument("--state-dir", default=None)
    st.add_argument("--agents", action="store_true",
                    help="also end every agent and the Harness Server (without: they run on, the next serve takes them)")
    hv = sub.add_parser("harness-server", help="the Harness Server, which runs the agents: status, restart (every agent "
                        "process ends; their sessions stay), stop")
    hv.add_argument("action", choices=["status", "restart", "stop"])
    sub.add_parser("migrate-home", help="move the folder of an older Orbit (its old name) to ~/.orbit, leaving a link")
    tl = sub.add_parser("tools", help="make Orbit's tools available to every harness")
    tl.add_argument("action", choices=["install", "uninstall", "status"])
    tl.add_argument("--dry-run", action="store_true", help="install: say what would change, change nothing")
    a = ap.parse_args(argv)
    global STATE
    STATE = getattr(a, "state_dir", None) or a.state_top
    if a.cmd == "serve" and STATE:
        a.state_dir = STATE
    if a.cmd == "serve":
        from orbit import server
        args = []
        for k in ("port", "state_dir", "data_root"):
            v = getattr(a, k)
            if v is not None:
                args += ["--" + k.replace("_", "-"), str(v)]
        args += (["--remote"] if a.remote else []) + sum((["--bind", b] for b in a.bind), [])
        server.main(args)
        return
    if a.cmd == "url":
        sys.exit(cmd_url(a.host))
    if a.cmd == "plugin":
        if a.action in ("secret", "restart", "logs"):
            sys.exit(cmd_plugin_manage(a.action, a.dirs, a.clear, a.lines))
        sys.exit(cmd_plugin(a.action, a.dirs))
    if a.cmd == "token":
        sys.exit(cmd_token(a.rotate))
    if a.cmd == "https":
        sys.exit(cmd_https(a.action, a.port))
    if a.cmd == "remote":
        sys.exit(cmd_remote(a.action, a.name, a.url))
    if a.cmd == "push-relay":
        sys.exit(cmd_push_relay(a.action, a.a, a.b))
    if a.cmd == "harness-server":
        sys.exit(cmd_harness_server(a.action))
    if a.cmd == "stop":
        sys.exit(cmd_stop(a.agents))
    if a.cmd == "migrate-home":
        from orbit import home
        print(home.migrate())
        sys.exit(0)
    if a.action == "install":
        sys.exit(tools_install(a.dry_run))
    sys.exit(tools_uninstall() if a.action == "uninstall" else tools_status())


if __name__ == "__main__":
    main()
