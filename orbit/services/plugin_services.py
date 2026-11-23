"""Plugin services: processes a plugin declares (`[service.<id>]` in plugin.toml) that Orbit keeps running.

Orbit starts them once its server listens, in the plugin's folder, with the plugin's environment (plugins.env: the
server's URL, the plugin's token, data folder and secrets); their stdout and stderr go to
`<state>/logs/plugins/<plugin>.<service>.log` (1 MB, then one old file). One whose plugin's declared secrets are not
all set waits ("needs secrets") until they are. When one exits, `restart` decides:
`on-failure` (the default: again unless it exited 0), `always`, or `never`. Restarts wait 1, 2, 4 … 60 s; five
failures within five minutes stop it ("failed": a restart from Settings or `orbit plugin restart` tries again).
The server stops them when it stops (SIGTERM to the process group, SIGKILL after 5 s).

Each gets a port of its own (API 1.3: $ORBIT_SERVICE_PORT, a free one Orbit picks, kept while it stays free): the
server sends it a live search there (plugin_items.search: GET /orbit/search).
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

LOG_MAX = 1_000_000
CRASH_WINDOW, CRASH_MAX = 300.0, 5
BACKOFF_MAX = 60.0
STOP_WAIT = 5.0

_lock = threading.Lock()
SERVICES: dict[tuple[str, str], "Service"] = {}


class Service:
    def __init__(self, plugin: str, sid: str, spec: dict[str, Any], folder: Path):
        self.plugin, self.id, self.spec, self.folder = plugin, sid, spec, folder
        self.policy = str(spec.get("restart") or "on-failure")
        self.proc: subprocess.Popen | None = None
        self.state = "stopped"  # stopped · needs secrets · starting · running · restarting · failed · exited
        self.error = ""
        self.started: float | None = None
        self.restarts = 0
        self.last_exit: int | None = None
        self.crashes: deque[float] = deque(maxlen=CRASH_MAX)
        self.wake = threading.Event()
        self.stopping = False
        self.thread: threading.Thread | None = None
        self.port = 0  # $ORBIT_SERVICE_PORT: where it answers Orbit (a live search)

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        with _lock:
            if self.thread and self.thread.is_alive():
                return
            self.stopping = False
            self.crashes.clear()
            self.error = ""
            self.wake.clear()
            self.thread = threading.Thread(target=self._run, name=f"plugin {self.plugin}.{self.id}", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stopping = True
        self.wake.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                p.wait(STOP_WAIT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
        t = self.thread
        if t and t is not threading.current_thread():
            t.join(STOP_WAIT + 2)
        self.state = "stopped"

    def restart(self) -> None:
        self.stop()
        self.restarts = 0
        self.start()

    def log_path(self) -> Path:
        from orbit import plugins
        assert plugins.STATE is not None
        return plugins.STATE / "logs" / "plugins" / f"{self.plugin}.{self.id}.log"

    def status(self) -> dict[str, Any]:
        p = self.proc
        return {"id": self.id, "state": self.state, "pid": p.pid if p and p.poll() is None else None,
                "since": self.started, "restarts": self.restarts, "last_exit": self.last_exit, "error": self.error,
                "restart": self.policy, "port": self.port or None}

    def tail(self, lines: int = 200) -> str:
        try:
            with open(self.log_path(), "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 200_000))
                return b"\n".join(f.read().splitlines()[-lines:]).decode("utf-8", "replace")
        except OSError:
            return ""

    # ------------------------------------------------------------------ the loop
    def _open_log(self):
        f = self.log_path()
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            if f.stat().st_size > LOG_MAX:
                os.replace(f, f.with_suffix(".log.1"))
        except OSError:
            pass
        return open(f, "ab", buffering=0)

    def _run(self) -> None:
        from orbit import plugins
        m = plugins.LOADED.get(self.plugin) or {}
        missing = [k for k in m.get("secrets") or [] if k not in plugins.get_secrets(self.plugin)]
        if missing:  # it would only fail: it starts when they are set (set_secret restarts it)
            self.state, self.error = "needs secrets", "set " + ", ".join(missing) + " (Settings → Plugins, or orbit plugin secret)"
            return
        wait = 1.0
        while not self.stopping:
            self.state = "starting"
            log = self._open_log()
            log.write(f"\n--- orbit: starting {self.plugin}.{self.id} at {time.strftime('%Y-%m-%d %H:%M:%S')}\n".encode())
            try:
                argv = plugins.argv_of(self.spec["command"], self.folder)
                self.port = _free_port(self.port)
                env = {**os.environ, **plugins.env(self.plugin), **(self.spec.get("env") or {}),
                       "ORBIT_SERVICE_PORT": str(self.port)}
                self.proc = subprocess.Popen(argv, cwd=self.folder, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                             stderr=subprocess.STDOUT, start_new_session=True)
            except (OSError, ValueError) as e:
                self.proc, code = None, 127
                self.error = f"cannot start: {e}"
                log.write(f"--- orbit: {self.error}\n".encode())
            else:
                self.started, self.state = time.time(), "running"
                code = self.proc.wait()
                if self.stopping:
                    log.close()
                    break
                if code != 0:  # (a failure to start keeps its own error)
                    self.error = f"exited with {code}" if code > 0 else f"killed by signal {-code}"
                log.write(f"--- orbit: {self.plugin}.{self.id} exited ({code})\n".encode())
            log.close()
            self.last_exit = code
            if self.stopping:
                break
            if self.policy == "never" or (self.policy == "on-failure" and code == 0):
                self.state = "exited" if code == 0 else "failed"
                break
            now = time.time()
            if self.started and now - self.started > CRASH_WINDOW:
                wait = 1.0  # it ran a good while: start the backoff over
            self.crashes.append(now)
            if len(self.crashes) == CRASH_MAX and now - self.crashes[0] < CRASH_WINDOW:
                self.state = "failed"
                self.error = (self.error + "; " if self.error else "") + f"{CRASH_MAX} failures in {CRASH_WINDOW / 60:g} min: stopped"
                break
            self.state = "restarting"
            self.restarts += 1
            if self.wake.wait(wait):
                break
            wait = min(BACKOFF_MAX, wait * 2)
        self.proc = None if self.stopping else self.proc


def _free_port(want: int = 0) -> int:
    """want while it is free (a restart keeps its port), else one the system says is free."""
    for p in ([want] if want else []) + [0]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return s.getsockname()[1]
    raise OSError("no free port")


# ---------------------------------------------------------------------- the registry
def register(plugin: str, sid: str, spec: dict[str, Any], folder: Path) -> None:
    with _lock:
        SERVICES[(plugin, sid)] = Service(plugin, sid, spec, folder)


def forget() -> None:
    stop_all()
    with _lock:
        SERVICES.clear()


def start_all() -> None:
    for svc in list(SERVICES.values()):
        svc.start()


def stop_all() -> None:
    threads = [threading.Thread(target=s.stop, daemon=True) for s in list(SERVICES.values())]
    for t in threads:
        t.start()
    for t in threads:
        t.join(STOP_WAIT + 3)


def of(plugin: str) -> list[Service]:
    return [s for (p, _), s in sorted(SERVICES.items()) if p == plugin]


def status(plugin: str) -> list[dict[str, Any]]:
    return [s.status() for s in of(plugin)]


def restart(plugin: str, sid: str = "") -> list[dict[str, Any]]:
    """Restart one service of a plugin, or all of them (after its secrets changed)."""
    svcs = [s for s in of(plugin) if not sid or s.id == sid]
    if sid and not svcs:
        raise KeyError(f"{plugin}.{sid}")
    for s in svcs:
        s.restart()
    return status(plugin)
