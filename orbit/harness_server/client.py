"""The Orbit Server's side of the Harness Server (builddoc/harness-server.md §3).

- `Client`: one connection; requests are JSON lines, answered at once, one at a time.
- `HostedLauncher`: what `ProcessDriver._spawn` uses when the Harness Server runs the agents. Writes, signals,
  trims and forgets are posted in order from one thread: nothing a driver does (often under the Desk's lock) ever
  waits on the Harness Server, except starting a process.
- `HostedProcess`: what the drivers use of `subprocess.Popen` (stdin, stdout / stderr by line, poll, wait, signals,
  pid), fed by one `read` stream. `offset` is where to read on after a hand-off; `detach()` stops its readers at a
  line boundary without reporting an exit.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from orbit.harness_server import PROTOCOL, harness_dir

START_WAIT = 5.0  # seconds a starting Harness Server has to answer `hello`
TIMEOUT = 10.0  # a request's answer (every request is answered at once: longer means it is wedged)
SIG_NAMES = {signal.SIGINT: "INT", signal.SIGTERM: "TERM", signal.SIGKILL: "KILL"}


class HarnessError(OSError):
    """The Harness Server refused a request (`ok: false`)."""


class Mismatch(OSError):
    """A Harness Server of another major protocol."""

    def __init__(self, hello: dict):
        super().__init__(f"the Harness Server speaks protocol {hello.get('protocol')}, this Orbit {PROTOCOL}")
        self.hello = hello


def sock_path(state: str | os.PathLike) -> str:
    """The socket's path. A deep state dir would exceed sun_path's 104 bytes: then it is reached through a short
    link (ours, in this user's temp dir) to its folder."""
    hdir = harness_dir(os.path.abspath(state))
    p = os.path.join(hdir, "server.sock")
    if len(p.encode()) < 100:
        return p
    link = os.path.join(tempfile.gettempdir(), "orbit-hs-" + hashlib.sha1(hdir.encode()).hexdigest()[:16])
    try:
        st = os.lstat(link)
        if st.st_uid != os.getuid():
            raise OSError(f"{link} belongs to another user")
        if os.readlink(link) != hdir:
            os.unlink(link)
            os.symlink(hdir, link)
    except FileNotFoundError:
        os.symlink(hdir, link)
    return os.path.join(link, "server.sock")


def start_server(state: str | os.PathLike, args: list[str] | tuple = ()) -> subprocess.Popen:
    """Start a Harness Server for `state`, detached (its own session; it outlives this process). Two starters at once
    are fine: the second one exits at once (the flock), and both talk to the first."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the folder holding orbit/
    env = {**os.environ, "PYTHONPATH": root + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    p = subprocess.Popen([sys.executable, "-m", "orbit.harness_server", "--state", os.path.abspath(state), *args],
                         cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    threading.Thread(target=p.wait, daemon=True).start()  # reaped if it exits while this process runs
    return p


class Client:
    """One connection to the Harness Server of a state dir."""

    def __init__(self, state: str | os.PathLike, name: str = "orbit"):
        self.state = os.path.abspath(state)
        self.name = name
        self.sock: socket.socket | None = None
        self.f = None
        self.lock = threading.Lock()
        self.n = 0
        self.hello: dict = {}

    def connect(self, start: bool = False, args: list[str] | tuple = ()) -> dict:
        """Connect (starting one when none answers and `start`), then `hello`. Raises OSError."""
        try:
            self._open()
        except OSError:
            if not start:
                raise
            start_server(self.state, args)
            end = time.time() + START_WAIT
            while True:
                try:
                    self._open()
                    break
                except OSError:
                    if time.time() > end:
                        raise
                    time.sleep(0.05)
        self.hello = self.request("hello", client=self.name, protocol=PROTOCOL)
        return self.hello

    def _open(self) -> None:
        if self.sock is not None:
            self._broken()  # (connecting again)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT)
        try:
            s.connect(sock_path(self.state))
        except OSError:
            s.close()
            raise
        self.sock, self.f = s, s.makefile("rb")

    @property
    def alive(self) -> bool:
        return self.sock is not None

    def request(self, op: str, **args) -> dict:
        with self.lock:
            if self.sock is None:
                raise ConnectionError("not connected to the Harness Server")
            self.n += 1
            try:
                self.sock.sendall((json.dumps({"id": self.n, "op": op, **args}) + "\n").encode())
                line = self.f.readline()
            except OSError as e:
                self._broken()
                raise ConnectionError(f"the Harness Server connection broke: {e}") from e
            if not line:
                self._broken()
                raise ConnectionError("the Harness Server closed the connection")
        r = json.loads(line)
        if not r.get("ok"):
            raise HarnessError(r.get("error") or f"{op}: refused")
        return r

    def _broken(self) -> None:
        if self.sock:
            _close(self.sock, self.f)
        self.sock = None

    def close(self) -> None:
        with self.lock:
            self._broken()


def _close(sock: socket.socket, f=None) -> None:
    """Close a connection for good (a makefile() still open would keep its descriptor, and the server would not
    see the client go)."""
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    for x in (f, sock):
        try:
            if x is not None:
                x.close()
        except OSError:
            pass


class HostedLauncher:
    """Agents as processes of the Harness Server (one Orbit Server: one owner id)."""

    def __init__(self, state: str | os.PathLike, args: list[str] | tuple = (), fallback=None):
        self.state = os.path.abspath(state)
        self.args = list(args)  # for a Harness Server it starts (tests: --idle)
        self.fallback = fallback  # a launcher for when none can be reached (the Orbit Server's DirectLauncher)
        self.owner = uuid.uuid4().hex
        self.client: Client | None = None
        self.closed = False
        self.lock = threading.RLock()  # (re)connecting
        self.live_lock = threading.Lock()
        self.live: dict[str, HostedProcess] = {}  # processes with a read stream (attached)
        self.posts: queue.Queue = queue.Queue()
        self.cv = threading.Condition()
        self.pending = 0
        threading.Thread(target=self._send_posts, daemon=True).start()

    # ------------------------------------------------------------------ the connection
    def connect(self, takeover: bool = False) -> dict:
        """Connect (start one if none answers), check the protocol, claim. Raises Mismatch, HarnessError (refused),
        OSError (none could start)."""
        c = Client(self.state)
        hello = c.connect(start=True, args=self.args)
        if hello.get("protocol") != PROTOCOL:
            c.close()
            raise Mismatch(hello)
        c.request("claim", owner=self.owner, takeover=takeover)
        self.client = c
        return hello

    def _client(self) -> Client:
        with self.lock:
            if self.client is not None and self.client.alive:
                return self.client
            if self.closed:
                raise ConnectionError("this Orbit Server let go of the Harness Server")
            # The connection broke (the Harness Server restarted, or went away): its streams ended too, and their
            # drivers took the processes for dead. A process still listed is ended before anything new starts on
            # the same conversation (never two writers). Another owner took over: refused (HarnessError).
            self.connect(takeover=False)
            try:
                self.end([p for p in self.list() if p["proc"] not in self.live])
            except OSError:
                pass
            return self.client  # type: ignore[return-value]

    def request(self, op: str, **args) -> dict:
        return self._client().request(op, **args)

    def post(self, op: str, **args) -> None:
        """A request nobody waits for, sent in order (a failure shows later as the process's exit)."""
        with self.cv:
            self.pending += 1
        self.posts.put((op, args))

    def _send_posts(self) -> None:
        while True:
            op, args = self.posts.get()
            try:
                c = self.client
                if c is not None and c.alive:
                    c.request(op, **args)
            except (OSError, ValueError):
                pass
            with self.cv:
                self.pending -= 1
                self.cv.notify_all()

    def flush(self, timeout: float = 5.0) -> None:
        """Wait until every posted request was sent."""
        with self.cv:
            self.cv.wait_for(lambda: self.pending == 0, timeout)

    def close(self) -> None:
        """Let go (a restart: the processes run on)."""
        self.flush()
        self.closed = True
        if self.client:
            self.client.close()

    def shutdown_server(self) -> None:
        """End every process and the Harness Server itself (Quit Orbit and Stop Agents)."""
        self.flush()
        self.closed = True
        try:
            if self.client and self.client.alive:
                self.client.request("shutdown")
        except OSError:
            pass
        if self.client:
            self.client.close()

    # ------------------------------------------------------------------ processes
    def spawn(self, argv: list[str], cwd: str, env: dict[str, str], tag: str = "") -> HostedProcess | subprocess.Popen:
        try:
            c = self._client()
        except HarnessError as e:  # another Orbit Server took the Harness Server over: start nothing
            raise OSError(f"the Harness Server: {e}") from e
        except OSError as e:  # none could be reached or started: as before it existed
            if self.fallback is None:
                raise
            print(f"orbit: the Harness Server cannot be used ({e}): this agent runs directly", flush=True)
            return self.fallback.spawn(argv, cwd, env, tag)
        r = c.request("spawn", argv=list(argv), cwd=cwd, env=dict(env), tag=tag)
        try:
            return HostedProcess(self, r["proc"], int(r["pid"]), 0, argv)
        except OSError:
            self.post("signal", proc=r["proc"], sig="KILL")  # nobody could read it: it must not run on unattached
            raise

    def attach(self, proc: str, offset: int, pid: int) -> HostedProcess:
        """A process started before (a hand-off): its output from `offset` on. Raises OSError (refused, e.g. the
        journal was truncated)."""
        return HostedProcess(self, proc, pid, offset, [])

    def list(self, tag: str | None = None) -> list[dict]:
        return self.request("list", **({"tag": tag} if tag is not None else {}))["procs"]

    def stream(self, proc: str, offset: int) -> tuple[socket.socket, object]:
        """A connection turned into the `read` stream of one process."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT)
        try:
            s.connect(sock_path(self.state))
            f = s.makefile("rb")
            for n, req in enumerate(({"op": "claim", "owner": self.owner}, {"op": "read", "proc": proc, "from": offset})):
                s.sendall((json.dumps({"id": n, **req}) + "\n").encode())
                r = json.loads(f.readline() or b"{}")
                if not r.get("ok"):
                    raise HarnessError(r.get("error") or "read: refused")
        except (OSError, ValueError):
            _close(s)
            raise
        s.settimeout(None)
        return s, f

    def end(self, procs: list[dict], grace: float = 3.0) -> None:
        """End these processes (SIGTERM, SIGKILL after `grace` s, to the pid), wait for them, then forget them."""
        alive = {p["proc"] for p in procs if p.get("alive")}
        for sig in ("TERM", "KILL"):
            for proc in alive:
                try:
                    self.request("signal", proc=proc, sig=sig)
                except HarnessError:
                    pass
            end = time.time() + grace
            while alive and time.time() < end:
                time.sleep(0.05)
                alive &= {p["proc"] for p in self.list() if p["alive"]}
            if not alive:
                break
        for p in procs:
            try:
                self.request("forget", proc=p["proc"])
            except HarnessError:
                pass


EOF, LIVE, WAKE = object(), object(), object()


class _Stdin:
    """Writes go out in order and never wait (fire-and-forget)."""

    def __init__(self, hp: "HostedProcess"):
        self.hp, self.closed = hp, False

    def write(self, s: str) -> int:
        if self.closed:
            raise ValueError("write to a closed stdin")
        self.hp.launcher.post("write", proc=self.hp.proc, data=base64.b64encode(s.encode()).decode())
        return len(s)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.hp.launcher.post("close_stdin", proc=self.hp.proc)


class _Lines:
    """stdout or stderr, by line (str), as iterating a text-mode pipe gives them."""

    def __init__(self, hp: "HostedProcess", out: bool):
        self.hp, self.out = hp, out
        self.q: queue.Queue = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self) -> str:
        hp = self.hp
        if self.out:
            hp._handled()  # the line returned last is handled now (the driver asks for the next one)
        while True:
            if hp.detached:
                break
            item = self.q.get()
            if item is WAKE:
                continue
            if item is EOF:
                if self.out:
                    with hp.lock:
                        if not hp.detached:
                            hp.ended = True
                    if hp.ended:
                        hp.launcher.post("forget", proc=hp.proc)  # its exit was read: its files can go
                break
            if item is LIVE:
                if hp.on_live:
                    hp.on_live()
                continue
            if self.out:
                line, hp._after, hp.line_ts = item
                return line.decode("utf-8", "replace")
            return item.decode("utf-8", "replace")
        if self.out:
            hp._parked.set()
        raise StopIteration

    def close(self) -> None:
        pass


class HostedProcess:
    """A process of the Harness Server, as the drivers use a Popen."""

    def __init__(self, launcher: HostedLauncher, proc: str, pid: int, offset: int, argv: list[str]):
        self.launcher, self.proc, self.pid, self.args = launcher, proc, pid, argv
        self.returncode: int | None = None
        self.offset = offset  # the journal offset just after the last complete stdout line the driver handled
        self._after: int | None = None  # ... after the line it is handling now
        self.line_ts: float | None = None  # when the line being handled arrived at the Harness Server
        self.on_live = None  # called (on the reader's thread) once the output read is what arrives now
        self.lock = threading.Lock()
        self.detached = self.ended = False
        self._exited = threading.Event()
        self._parked = threading.Event()
        self.stdin = _Stdin(self)
        self.stdout = _Lines(self, out=True)
        self.stderr = _Lines(self, out=False)
        self._sock, self._f = launcher.stream(proc, offset)
        with launcher.live_lock:
            launcher.live[proc] = self
        threading.Thread(target=self._demux, args=(self._f,), daemon=True).start()

    def _handled(self) -> None:
        if self._after is not None:
            self.offset, self._after = self._after, None

    def _demux(self, f) -> None:
        """The stream's frames into lines: bytes first, then lines, then text (a frame can split either)."""
        bufs = {"out": bytearray(), "err": bytearray()}
        code = end = None
        try:
            for raw in f:
                fr = json.loads(raw)
                t = fr.get("t")
                if t in ("out", "err"):
                    data, off, buf = base64.b64decode(fr["b64"]), int(fr["off"]), bufs[t]
                    i = 0
                    while True:
                        nl = data.find(b"\n", i)
                        if nl < 0:
                            buf += data[i:]
                            break
                        line = bytes(buf) + data[i:nl + 1]
                        buf.clear()
                        if t == "out":
                            self.stdout.q.put((line, off + nl + 1, fr.get("ts")))
                        else:
                            self.stderr.q.put(line)
                        i = nl + 1
                elif t == "live":
                    self.stdout.q.put(LIVE)
                elif t == "exit":
                    code, end = int(fr.get("code") or 0), int(fr.get("off") or 0)
                    break
        except (OSError, ValueError):
            pass
        if self.detached:
            return
        if bufs["out"]:  # a last line without a newline
            self.stdout.q.put((bytes(bufs["out"]), end, None))
        if bufs["err"]:
            self.stderr.q.put(bytes(bufs["err"]))
        # The connection broke without an exit: as if it had died (today's paths take over).
        self.returncode = code if code is not None else -1
        self._exited.set()
        with self.launcher.live_lock:
            self.launcher.live.pop(self.proc, None)
        self.stdout.q.put(EOF)
        self.stderr.q.put(EOF)
        _close(self._sock, self._f)

    def begin_detach(self) -> bool:
        """detach()'s first half: its reader stops at the next line boundary (False: its exit was read already)."""
        with self.lock:
            if self.ended:
                return False
            self.detached = True
        self.stdout.q.put(WAKE)
        self.stderr.q.put(WAKE)
        return True

    @property
    def handed_off(self) -> bool:
        """Detached and its reader stopped: what it says now is the next server's (nothing more is written here)."""
        return self.detached and self._parked.is_set()

    def detach(self, timeout: float = 5.0) -> int | None:
        """Stop reading at the next line boundary, without reporting an exit (a hand-off). The offset to read on
        from, or None when its exit was read already (then the driver handles it as always) or its reader did not
        stop in time (then it is closed as before)."""
        if not self.begin_detach():
            return None
        parked = self._parked.wait(timeout)
        _close(self._sock, self._f)
        with self.launcher.live_lock:
            self.launcher.live.pop(self.proc, None)
        return self.offset if parked else None  # (its reader stuck in a line: the offset may not be right)

    def trim(self) -> None:
        """The journal before what was read can go."""
        self.launcher.post("trim", proc=self.proc, before=self.offset)

    # ------------------------------------------------------------------ Popen
    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self._exited.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode  # type: ignore[return-value]

    def send_signal(self, sig: int) -> None:
        if self.returncode is None and sig in SIG_NAMES:
            self.launcher.post("signal", proc=self.proc, sig=SIG_NAMES[sig])

    def terminate(self) -> None:
        self.send_signal(signal.SIGTERM)

    def kill(self) -> None:
        self.send_signal(signal.SIGKILL)
