"""The Harness Server daemon: `python -m orbit.harness_server --state <state dir>` (builddoc/harness-server.md §2).

It owns the agent processes: it starts them, feeds their stdin, keeps their output in a journal on disk, signals them
and reports how they ended. It knows nothing about harnesses: bytes in, bytes out, byte offsets. One per state dir
(an exclusive flock on <state>/harness/server.lock); clients talk JSON lines over <state>/harness/server.sock.

Every request is answered at once. Process readers only append to the journal, each `read` stream has its own
thread, and stdin is fed from a queue: a slow or dead client never blocks an agent's pipe or another client.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import queue
import resource
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

from orbit.harness_server import PROTOCOL, VERSION, harness_dir

SEGMENT = 64 << 20  # a journal segment rolls at this size (on a frame boundary)
UNREAD_MAX = 512 << 20  # more journal than this: the oldest segments go (a client gone for a long time)
IDLE_EXIT = 60.0  # seconds with no process and no client before it exits (tests and forgotten state dirs leak nothing)
LOG_MAX = 5 << 20
CHUNK = 65536
END_GRACE = 3.0  # SIGTERM, then SIGKILL after this long
SIGNALS = {"INT": signal.SIGINT, "TERM": signal.SIGTERM, "KILL": signal.SIGKILL}


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)
    try:
        if os.fstat(1).st_size > LOG_MAX:
            _open_log(_log_path[0])
    except OSError:
        pass


_log_path = [""]


def _open_log(path: str) -> None:
    """stdout and stderr to the log (a traceback lands there too), rotated at 5 MB, one old copy kept."""
    _log_path[0] = path
    try:
        if os.path.getsize(path) > LOG_MAX:
            os.replace(path, path + ".1")
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _ps() -> list[tuple[int, int, int, int]]:
    """Every process: (pid, ppid, pgid, rss in bytes), from one `ps`; [] when it cannot run."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,rss="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        try:
            pid, ppid, pgid, rss = (int(x) for x in line.split())
        except ValueError:
            continue
        rows.append((pid, ppid, pgid, rss * 1024))
    return rows


class Segment:
    def __init__(self, path: str, start: int):
        self.path, self.start, self.end, self.size = path, start, start, 0  # offsets [start, end); size: file bytes


class Proc:
    """One process: its pipes, its journal (segments of JSON-line frames), how it ended."""

    def __init__(self, srv: "Server", argv: list[str], cwd: str, env: dict[str, str], tag: str):
        self.srv = srv
        # Its own process group (a session of its own): a group signal reaches what it started. No controlling tty.
        self.popen = subprocess.Popen(argv, cwd=cwd or None, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
        self.id = uuid.uuid4().hex[:12]
        self.pid = self.popen.pid
        self.argv, self.cwd, self.tag = argv, cwd, tag
        self.started = time.time()
        self.dir = os.path.join(srv.procs_dir, self.id)
        os.makedirs(self.dir, mode=0o700)
        self.cond = threading.Condition()
        self.segments: list[Segment] = []
        self.nseg = 0  # journal.<n>: the next segment's number
        self.file = None
        self.end = 0  # the journal's length: offset of the next byte
        self.code: int | None = None  # exit code, once it exited and its output was all read
        self.exit_ts: float | None = None
        self.truncated_before = 0  # bytes before this were dropped unread (UNREAD_MAX)
        self.stdin_q: queue.Queue = queue.Queue()
        self._roll()
        self.write_meta()
        out = threading.Thread(target=self._pump, args=(self.popen.stdout, "out"), daemon=True)
        err = threading.Thread(target=self._pump, args=(self.popen.stderr, "err"), daemon=True)
        out.start()
        err.start()
        threading.Thread(target=self._feed, daemon=True).start()
        threading.Thread(target=self._wait, args=(out, err), daemon=True).start()

    # ------------------------------------------------------------------ the journal
    def _roll(self) -> Segment:
        if self.file:
            self.file.close()
        seg = Segment(os.path.join(self.dir, f"journal.{self.nseg}"), self.end)
        self.nseg += 1
        self.file = open(seg.path, "ab", buffering=0)
        self.segments.append(seg)
        return seg

    def append(self, kind: str, data: bytes) -> None:
        with self.cond:
            if self.code is not None:
                return  # (stderr after the exit was reported: nothing may follow an exit frame)
            line = (json.dumps({"t": kind, "off": self.end, "ts": round(time.time(), 3), "b64": _b64(data)}) + "\n").encode()
            seg = self.segments[-1]
            if seg.size and seg.size + len(line) > self.srv.segment:
                seg = self._roll()
            self.file.write(line)
            seg.size += len(line)
            self.end += len(data)
            seg.end = self.end
            if len(self.segments) > 1 and sum(s.size for s in self.segments) > self.srv.unread_max:
                while len(self.segments) > 1 and sum(s.size for s in self.segments) > self.srv.unread_max:
                    self._drop(self.segments.pop(0))
                self.truncated_before = self.segments[0].start
                log(f"{self.id}: journal over {self.srv.unread_max} bytes unread: dropped before {self.truncated_before}")
                self.write_meta()
            self.cond.notify_all()

    def _drop(self, seg: Segment) -> None:
        try:
            os.unlink(seg.path)
        except OSError:
            pass

    def trim(self, before: int) -> None:
        """Whole segments that end before `before` go (never the one being written)."""
        with self.cond:
            while len(self.segments) > 1 and self.segments[0].end <= before:
                self._drop(self.segments.pop(0))

    def first(self) -> int:
        with self.cond:
            return self.segments[0].start if self.segments else self.end

    # ------------------------------------------------------------------ the process
    def _pump(self, pipe, kind: str) -> None:
        fd = pipe.fileno()
        while True:
            try:
                data = os.read(fd, CHUNK)
            except OSError:
                data = b""
            if not data:
                break
            self.append(kind, data)
        try:
            pipe.close()
        except OSError:
            pass

    def _feed(self) -> None:
        """stdin, in order, off the request threads (a full pipe blocks only this thread)."""
        fd, broken = self.popen.stdin.fileno(), False
        while True:
            data = self.stdin_q.get()
            if data is None:
                break
            while data and not broken:
                try:
                    data = data[os.write(fd, data):]
                except OSError:
                    broken = True  # it stopped reading (or exited): the exit says how it ended
        try:
            self.popen.stdin.close()
        except OSError:
            pass

    def _wait(self, out: threading.Thread, err: threading.Thread) -> None:
        code = self.popen.wait()
        # Its output to the end first: the exit frame comes after the last byte. A process it left behind (a dev
        # server) may hold the pipes open: what that writes after the exit is not journaled.
        out.join(2)
        err.join(1)
        with self.cond:
            self.code, self.exit_ts = code, time.time()
            if self.file:
                self.file.close()
                self.file = None
            self.cond.notify_all()
        self.stdin_q.put(None)
        self.write_meta()
        log(f"{self.id}: pid {self.pid} exited ({code})")

    def alive(self) -> bool:
        return self.code is None and self.popen.returncode is None

    def signal(self, sig: int, group: bool = False) -> None:
        if not self.alive():
            return  # exited (reaped): its pid may belong to another process now
        try:
            (os.killpg if group else os.kill)(self.pid, sig)
        except OSError:
            pass

    def write_meta(self) -> None:
        """argv, cwd, tag, times, pid, the exit code. Never the environment (it can hold a plugin's secrets)."""
        m = {"proc": self.id, "argv": self.argv, "cwd": self.cwd, "tag": self.tag, "started": self.started,
             "pid": self.pid, "code": self.code, "ended": self.exit_ts, "truncated_before": self.truncated_before}
        try:
            tmp = os.path.join(self.dir, "meta.tmp")
            with open(tmp, "w") as f:
                json.dump(m, f)
            os.replace(tmp, os.path.join(self.dir, "meta.json"))
        except OSError:
            pass

    def info(self, rss: int | None) -> dict:
        return {"proc": self.id, "tag": self.tag, "pid": self.pid, "alive": self.alive(), "code": self.code,
                "end": self.end, "started": self.started, "rss": rss}


class Refused(Exception):
    pass


class Server:
    def __init__(self, state: str, idle: float = IDLE_EXIT, segment: int = SEGMENT, unread_max: int = UNREAD_MAX):
        self.state = os.path.abspath(state)
        self.dir = harness_dir(self.state)
        self.procs_dir = os.path.join(self.dir, "procs")
        self.idle, self.segment, self.unread_max = idle, segment, unread_max
        self.procs: dict[str, Proc] = {}
        self.lock = threading.Lock()
        self.conns: set[socket.socket] = set()
        self.owner = ""  # the Orbit Server whose connections may spawn, write, signal and read
        self.owner_conns: set[socket.socket] = set()
        self.started = time.time()
        self.stopping = threading.Event()
        self.sock: socket.socket | None = None

    # ------------------------------------------------------------------ life
    def bind(self) -> None:
        # Bound by a path relative to its folder: a deep state dir never exceeds sun_path's 104 bytes (macOS).
        os.chdir(self.dir)
        try:
            os.unlink("server.sock")  # stale (we hold the lock: no other instance listens on it)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind("server.sock")
        os.chmod("server.sock", 0o600)
        self.sock.listen(64)
        os.chdir("/")
        with open(os.path.join(self.dir, "server.pid"), "w") as f:
            f.write(str(os.getpid()))

    def serve(self) -> None:
        assert self.sock
        self.sock.settimeout(1.0)
        quiet_since = time.time()
        while not self.stopping.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                conn = None
            except OSError:
                if self.stopping.is_set():
                    break
                continue
            if conn is not None:
                conn.setblocking(True)
                with self.lock:
                    self.conns.add(conn)
                threading.Thread(target=self._conn, args=(conn,), daemon=True).start()
            if not os.path.isdir(self.dir):
                log("its state dir is gone: stopping")
                break
            with self.lock:
                busy = bool(self.conns) or any(p.alive() for p in self.procs.values())
            if busy:
                quiet_since = time.time()
            elif time.time() - quiet_since > self.idle:
                log(f"no process and no client for {self.idle:g} s: stopping")
                break
        self.end_all()
        for f in ("server.sock", "server.pid"):
            try:
                os.unlink(os.path.join(self.dir, f))
            except OSError:
                pass
        log("stopped")

    def end_all(self) -> None:
        """SIGTERM to each process group, SIGKILL after END_GRACE s. A group whose leader exited is still reached
        (what it left behind), through its members as `ps` lists them, never by a pid that may be reused."""
        with self.lock:
            procs = list(self.procs.values())
        if not procs:
            return
        groups = {p.pid for p in procs}

        def left() -> bool:
            return any(p.alive() for p in procs) or any(pgid in groups for _, _, pgid, _ in _ps())

        for sig in (signal.SIGTERM, signal.SIGKILL):
            for p in procs:
                p.signal(sig, group=True)
            for pid, _, pgid, _ in _ps():
                if pgid in groups:
                    try:
                        os.kill(pid, sig)
                    except OSError:
                        pass
            end = time.time() + END_GRACE
            while time.time() < end and left():
                time.sleep(0.1)
            if not left():
                return

    # ------------------------------------------------------------------ connections
    def _send(self, conn: socket.socket, obj: dict) -> None:
        conn.sendall((json.dumps(obj) + "\n").encode())

    def _drop_conn(self, conn: socket.socket) -> None:
        with self.lock:
            self.conns.discard(conn)
            if conn in self.owner_conns:
                self.owner_conns.discard(conn)
                if not self.owner_conns:
                    self.owner = ""
        try:
            conn.close()
        except OSError:
            pass

    def _conn(self, conn: socket.socket) -> None:
        f = conn.makefile("rb")
        try:
            for raw in f:
                try:
                    req = json.loads(raw)
                    rid, op = req.get("id"), req.get("op")
                except (ValueError, AttributeError):
                    self._send(conn, {"id": None, "ok": False, "error": "not a JSON object"})
                    continue
                try:
                    if op == "read":
                        p, pos = self._read_args(conn, req)
                        self._send(conn, {"id": rid, "ok": True})
                        return self._stream(conn, p, pos)  # the connection is the stream now
                    res = self._op(conn, op, req)
                    self._send(conn, {"id": rid, "ok": True, **res})
                except Refused as e:
                    self._send(conn, {"id": rid, "ok": False, "error": str(e)})
                except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as e:
                    if isinstance(e, (BrokenPipeError, ConnectionResetError)):
                        return
                    self._send(conn, {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"})
                if op == "shutdown":
                    self.stopping.set()
        except OSError:
            pass
        finally:
            self._drop_conn(conn)

    def _proc(self, req: dict) -> Proc:
        p = self.procs.get(str(req.get("proc", "")))
        if p is None:
            raise Refused(f"no such process: {req.get('proc')}")
        return p

    def _owner_only(self, conn: socket.socket) -> None:
        if conn not in self.owner_conns:
            raise Refused("claim the Harness Server first")

    def _op(self, conn: socket.socket, op: str, req: dict) -> dict:
        if op == "hello":
            return {"version": VERSION, "protocol": PROTOCOL, "pid": os.getpid(), "started": self.started}
        if op == "claim":
            owner = str(req.get("owner") or "")
            if not owner:
                raise Refused("claim: owner is required")
            with self.lock:
                if self.owner and self.owner != owner and self.owner_conns:
                    if not req.get("takeover"):
                        raise Refused(f"another Orbit Server owns this Harness Server ({self.owner})")
                    log(f"{owner} takes over from {self.owner}")
                    for c in list(self.owner_conns):
                        try:
                            c.shutdown(socket.SHUT_RDWR)  # its threads end on their own
                        except OSError:
                            pass
                    self.owner_conns = set()
                self.owner = owner
                self.owner_conns.add(conn)
            return {}
        if op == "list":
            tag = req.get("tag")
            with self.lock:
                procs = [p for p in self.procs.values() if tag is None or p.tag == tag]
            return {"procs": [p.info(r) for p, r in zip(procs, _rss([p.pid for p in procs]))]}
        if op == "shutdown":
            return {}
        self._owner_only(conn)
        if op == "spawn":
            argv = req.get("argv")
            if not (isinstance(argv, list) and argv and all(isinstance(x, str) for x in argv)):
                raise Refused("spawn: argv is a list of strings")
            env = req.get("env")
            env = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else None
            p = Proc(self, argv, str(req.get("cwd") or ""), env, str(req.get("tag") or ""))
            with self.lock:
                self.procs[p.id] = p
            log(f"{p.id}: pid {p.pid} started ({p.tag}): {' '.join(argv)[:200].replace(chr(10), ' ')}")
            return {"proc": p.id, "pid": p.pid}
        p = self._proc(req)
        if op == "write":
            p.stdin_q.put(base64.b64decode(str(req.get("data", ""))))
        elif op == "close_stdin":
            p.stdin_q.put(None)
        elif op == "signal":
            sig = SIGNALS.get(str(req.get("sig", "")))
            if sig is None:
                raise Refused(f"signal: one of {', '.join(SIGNALS)}")
            p.signal(sig, bool(req.get("group")))
        elif op == "trim":
            p.trim(int(req.get("before", 0)))
        elif op == "forget":
            if p.code is None:
                raise Refused(f"{p.id} is still running")
            with self.lock:
                self.procs.pop(p.id, None)
            shutil.rmtree(p.dir, ignore_errors=True)
        else:
            raise Refused(f"unknown op: {op}")
        return {}

    # ------------------------------------------------------------------ read streams
    def _read_args(self, conn: socket.socket, req: dict) -> tuple[Proc, int]:
        self._owner_only(conn)
        p = self._proc(req)
        pos = int(req.get("from", 0))
        if pos < p.first():
            if pos < p.truncated_before:
                raise Refused(f"truncated_before {p.truncated_before}: the journal before it was dropped unread")
            raise Refused(f"trimmed before {p.first()}")
        if pos > p.end:
            raise Refused(f"from {pos} is past the end ({p.end})")
        return p, pos

    def _gone(self, conn: socket.socket) -> bool:
        """A stream's client sends nothing: readable means it closed."""
        try:
            r, _, _ = select.select([conn], [], [], 0)
            return bool(r) and conn.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def _stream(self, conn: socket.socket, p: Proc, pos: int) -> None:
        live = False
        cur, fpos = "", 0  # the segment being read and where in it
        while True:
            with p.cond:
                while pos >= p.end and live and p.code is None:
                    p.cond.wait(0.5)
                    if self._gone(conn):
                        return
                todo = [(s.path, s.size) for s in p.segments if s.end > pos]
                end, code, ets = p.end, p.code, p.exit_ts
            for path, size in todo:
                try:
                    with open(path, "rb") as fh:
                        if path == cur:
                            fh.seek(fpos)
                        while fh.tell() < size:
                            line = fh.readline()
                            if not line.endswith(b"\n"):
                                break
                            fr = json.loads(line)
                            data = base64.b64decode(fr["b64"])
                            o = fr["off"]
                            if o + len(data) > pos:
                                if o < pos:  # the frame straddles the offset asked for: from there on
                                    line = (json.dumps({**fr, "off": pos, "b64": _b64(data[pos - o:])}) + "\n").encode()
                                conn.sendall(line)
                                pos = o + len(data)
                        cur, fpos = path, fh.tell()
                except FileNotFoundError:
                    return  # trimmed or forgotten under it
                except OSError:
                    return
            if pos >= end:
                try:
                    if not live:
                        self._send(conn, {"t": "live", "off": pos})
                        live = True
                    if code is not None:
                        self._send(conn, {"t": "exit", "off": pos, "code": code, "ts": ets})
                        return
                except OSError:
                    return


def _rss(pids: list[int]) -> list[int | None]:
    """Each process tree's resident memory (bytes), from one `ps`."""
    rows = _ps() if pids else []
    if not rows:
        return [None] * len(pids)
    kids: dict[int, list[int]] = {}
    rss = {}
    for pid, ppid, _, r in rows:
        kids.setdefault(ppid, []).append(pid)
        rss[pid] = r
    out = []
    for root in pids:
        if root not in rss:
            out.append(None)
            continue
        total, todo, seen = 0, [root], set()
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            total += rss.get(x, 0)
            todo += kids.get(x, [])
        out.append(total)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m orbit.harness_server", description="Orbit's Harness Server")
    ap.add_argument("--state", required=True, help="the Orbit state dir (it uses <state>/harness)")
    ap.add_argument("--idle", type=float, default=IDLE_EXIT, help="exit after this many seconds with no process and no client")
    ap.add_argument("--segment", type=int, default=SEGMENT, help=argparse.SUPPRESS)  # (tests: small segments)
    ap.add_argument("--unread-max", type=int, default=UNREAD_MAX, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    srv = Server(a.state, a.idle, a.segment, a.unread_max)
    os.makedirs(srv.dir, mode=0o700, exist_ok=True)
    os.chmod(srv.dir, 0o700)  # the only auth: same user
    lock = os.open(os.path.join(srv.dir, "server.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another instance runs: it is the one
    try:
        os.setsid()  # (started by hand from a terminal: still its own session)
    except OSError:
        pass
    _open_log(os.path.join(srv.dir, "server.log"))
    try:  # a GUI-started process gets 256 files; each agent holds four
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = 10240 if hard == resource.RLIM_INFINITY else min(hard, 10240)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except (OSError, ValueError):
        pass
    # An earlier instance's processes are never adopted: pids are reused, and it could not wait() for them.
    shutil.rmtree(srv.procs_dir, ignore_errors=True)
    os.makedirs(srv.procs_dir, mode=0o700, exist_ok=True)
    srv.bind()
    signal.signal(signal.SIGTERM, lambda *_: srv.stopping.set())
    log(f"started: pid {os.getpid()}, protocol {PROTOCOL}, version {VERSION}, state {srv.state}")
    srv.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
