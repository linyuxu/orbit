"""Starting the server: `make_server` (tests, the app) and `main` (`orbit serve`, `python -m orbit`)."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from orbit import ROOT
from orbit.desk import Desk
from orbit.desk.session import DRIVERS
from orbit.machines import federation, remote
from orbit.server.handler import DEFAULT_PORT, Handler
from orbit.server.sharing import Sharing
from orbit.server.support import Server
from orbit.services import schedule
from orbit.workspace import notes


def state_dir(arg: str | None) -> Path:
    from orbit.home import home
    return Path(arg or os.environ.get("ORBIT_STATE_DIR") or home()).expanduser()


def take_server_lock(state: Path) -> int:
    """One Orbit Server per state dir: an exclusive flock on <state>/server.lock, held for its life, taken before
    the Desk is built. A second one (the app starting one while another loads, a manual `orbit serve`) exits at once
    and touches nothing: not the sessions, not the agents."""
    state.mkdir(parents=True, exist_ok=True)
    fd = os.open(state / "server.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"orbit: another Orbit Server runs on {state} (it holds server.lock): exiting", flush=True)
        sys.exit(1)
    return fd


def server_info(state: Path, port: int | None, started: float) -> Path:
    """<state>/server.json {pid, port, started}: how `orbit stop` finds this server (port: once bound). It is
    believed only while <state>/server.lock is held."""
    f = state / "server.json"
    tmp = state / f"server.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({"pid": os.getpid(), "port": port, "started": started}))
    os.replace(tmp, f)
    return f


def make_server(port: int, desk: Desk, schedules: Path | None = None, binds: list[str] | None = None) -> ThreadingHTTPServer:
    """The HTTP server for desk; also attaches and starts the scheduler (<data root>/schedules.json)."""
    if desk.scheduler is None:
        desk.scheduler = schedule.Scheduler(desk, schedules or notes.data_root() / "schedules.json")
        desk.scheduler.start()
    if desk.federation is None:
        desk.federation = federation.Federation(desk)
        desk.federation.start()
    if desk.live is None:
        from orbit.services.live import Live
        desk.live = Live(desk)
    from orbit.services import plugin_items
    plugin_items.BROADCAST = desk.broadcast  # (1.3) a source that changed: an open ⌘O redraws
    from orbit.services import plugin_views
    plugin_views.BROADCAST = desk.broadcast  # (2.0) a thread or doc that changed: its open views redraw
    handler = type("BoundHandler", (Handler,), {"desk": desk, "port": port})
    binds = list(binds or ["127.0.0.1"])
    if "0.0.0.0" in binds:
        binds = ["0.0.0.0"]  # every address, this machine's included (one socket: the same port twice would clash)
    elif "127.0.0.1" not in binds:
        binds.insert(0, "127.0.0.1")  # the app and this machine's agents always reach it here
    srv = Server((binds[0], port), handler)
    handler.port = srv.server_address[1]  # port 0 picks a free one (tests)
    handler.sharing = Sharing(handler.port, handler)
    if binds[0] == "0.0.0.0":  # --remote: one socket for every address
        handler.sharing.servers["0.0.0.0"] = srv
    for b in binds[1:]:  # --bind: served in threads by main()
        handler.sharing.add(b, start=False)
    srv.extra = [s for a, s in handler.sharing.servers.items() if a != "0.0.0.0"]
    srv.daemon_threads = True
    handler.stop_server = staticmethod(lambda: threading.Thread(target=srv.shutdown, daemon=True).start())
    return srv


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="orbit", description="Orbit server")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ORBIT_PORT", DEFAULT_PORT)))
    ap.add_argument("--state-dir", help="UI state folder (default $ORBIT_STATE_DIR, else $ORBIT_HOME, else ~/.orbit)")
    ap.add_argument("--data-root", help="personal data folder (default $ORBIT_DATA_ROOT)")
    ap.add_argument("--remote", action="store_true",
                    help="also serve other machines (every address; their requests need the token: orbit url)")
    ap.add_argument("--bind", action="append", default=[], metavar="ADDR",
                    help="serve other machines on this address only (e.g. the Tailscale IP); repeatable")
    a = ap.parse_args(argv)
    # Agents this server starts can run `orbit-agent` (installed beside this Python) and reach this port.
    # The agents it starts find `orbit-agent` (and `orbit`) first in <repo>/cli, the same launchers a terminal uses.
    cli = str(ROOT / "cli")
    os.environ["PATH"] = cli + os.pathsep + os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
    os.environ["ORBIT_PORT"] = str(a.port)
    from orbit import home as orbit_home
    if orbit_home.needs_migration():  # (moved only when asked: a second instance must not move the first one's)
        print(f"orbit: {orbit_home.legacy_home()} holds an older Orbit's folder: `orbit migrate-home` moves it to "
              f"{orbit_home.home()}", flush=True)
    lock = take_server_lock(state_dir(a.state_dir))  # noqa: F841 - held until this process exits
    started = time.time()
    # Signals from here on: one during start-up (taking the hand-offs must not be cut in half) is acted on once the
    # server is up: a restart (hand-off) again, or with SIGUSR1 a stop with the agents.
    ctl: dict = {"up": None, "want": None}

    def on_signal(signum, _frame):
        ctl["want"] = "agents" if signum == signal.SIGUSR1 or ctl["want"] == "agents" else "handoff"
        if ctl["up"]:
            ctl["up"]()

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
        signal.signal(sig, on_signal)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)  # a closed terminal or app does not end the backend
    info = server_info(state_dir(a.state_dir), None, started)  # (after the handlers: `orbit stop` may signal it now)
    try:
        _serve(a, started, info, ctl)
    finally:
        try:
            if json.loads(info.read_text()).get("pid") == os.getpid():
                info.unlink()
        except (OSError, ValueError):
            pass


def _serve(a: argparse.Namespace, started: float, info: Path, ctl: dict) -> None:
    from orbit import plugins
    for p in plugins.load(state_dir(a.state_dir)):  # before the Desk: its sessions may run a plugin's harness
        print(f"orbit: plugin {p['name']} {p['version']}: " + ("loaded" if p["ok"] else p["error"]), flush=True)
    desk = Desk(state_dir(a.state_dir))
    # The agents run in the Harness Server (they outlive this server); the hand-off of the last one is taken, and
    # every process nobody took over ends, before any session can start one (and before the port opens).
    desk.host()
    if desk.hosting["mode"] == "hosted":
        print(f"orbit: agents run in the Harness Server (pid {desk.hosting['pid']})", flush=True)
    vault = notes.configure(a.data_root)
    binds = ["0.0.0.0"] if a.remote and not a.bind else ["127.0.0.1", *a.bind]
    srv = make_server(a.port, desk, binds=binds)  # after notes.configure: schedules live in the data root
    server_info(desk.state_dir, srv.server_address[1], started)
    print(f"orbit: http://127.0.0.1:{a.port}/  (state {desk.state_dir}, notes {vault})", flush=True)
    if a.remote or a.bind:
        hosts = a.bind or remote.addresses() or ["HOST"]
        print("orbit: other machines connect with (keep it private; orbit token --rotate replaces it):", flush=True)
        for h in hosts:
            print(f"  {remote.url(desk.state_dir, h, a.port)}", flush=True)
    for extra in getattr(srv, "extra", []):
        threading.Thread(target=extra.serve_forever, daemon=True).start()
    sharing = srv.RequestHandlerClass.sharing
    if not (a.remote or a.bind) and desk.settings.get("serve_remote"):  # Settings → Machines: on
        try:
            st = sharing.set(True)
            print(f"orbit: other machines reach it at {', '.join(st['addresses'])}:{a.port} (Settings → Machines)", flush=True)
        except (OSError, ValueError) as e:
            print(f"orbit: could not serve other machines: {e}", flush=True)
    from orbit.services import plugin_items, plugin_services
    plugin_services.start_all()  # the plugins' services: the server listens, they can call it
    plugin_items.start()  # (1.3) item sources refreshed by a command: in the background, every `every`
    # Requests the last server cut off when it stopped go on (their agents resume their conversations).
    # Agents that ran on outside Orbit (a driver's link_alive) are followed first: those requests go to them.
    threading.Timer(1.0, lambda: (desk.follow_living(), desk.resume_interrupted())).start()
    # The index of conversations to resume (Resume another session): the file stores read once, in the background.
    warm = threading.Timer(5.0, lambda: desk.external_index.refresh(dict(DRIVERS), files_only=True))
    warm.daemon = True
    warm.start()
    desk.start_reminders()  # meetings: a reminder 10 minutes before (Calendar on)
    pid_file = desk.state_dir / "server.pid"  # the app finds a server an earlier launch left running
    pid_file.write_text(str(os.getpid()))

    def stop(*_):
        for addr, extra in list(sharing.servers.items()):
            if extra is not srv:
                threading.Thread(target=extra.shutdown, daemon=True).start()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    def signalled() -> None:  # SIGTERM, SIGINT: a restart (the agents are handed off); SIGUSR1: as POST
        if ctl["want"] == "agents":  # /api/shutdown {"agents": "stop"}, for scripts
            desk.stop_agents = True
        stop()

    srv.RequestHandlerClass.stop_server = staticmethod(stop)
    ctl["up"] = signalled
    if ctl["want"]:  # a signal while it started: acted on now
        signalled()
    try:
        srv.serve_forever()
    finally:
        plugin_services.stop_all()
        plugin_items.flush()
        from orbit.services import plugin_views
        plugin_views.flush()
        desk.shutdown()
        try:
            if pid_file.read_text().strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
