"""The engine every harness runs on: a child process that takes requests and answers in JSON lines.

`ProcessDriver` owns the process and the request's life, the same for every harness:

- **start** checks the program and (for a long-lived harness) starts it; **send** writes the request, or starts a
  process for it (a harness with a process per request, or one whose process ended between requests);
- **one result per request**: the harness's own end-of-turn message (`finish`), or, if the process goes away
  first, a cancelled result (after Stop) or a paused one marked `died` (the Desk continues it by itself);
- **stop**: the harness's interrupt message, or SIGINT; no result within `STOP_GRACE` s: SIGINT, then kill. A
  process that got SIGINT is expected to exit: the next request starts a new one on the same conversation;
- **a process that ends between requests**: after Stop, or with exit code 0, quietly replaced by the next request;
  otherwise the session is "not ready" with the error (the Desk starts a new driver on the next request);
- **close** ends the process (stdin closed, then terminate, then kill) and reports "stopped".
- **a turn of its own**: a harness may begin a turn no request started (Claude Code wakes when a background task
  finishes). Its first event while idle (`TURN_EVENTS`) is preceded by an `auto` event (what woke it, when the harness
  said: `woke`), and its end is a `result` marked `auto` (from `finish`, its process's exit, or a Stop). `busy` stays
  the requests' alone (a harness's own bookkeeping keys on it); a request written meanwhile takes the turn over.

A harness says only what differs: `program()`/`command()`, `request_line`, `steer_line`, `interrupt_line`,
`handle(msg)` (its events → DriverEvents, through `said`, `tool_started`, `finish`), `plain_line`, and `history()`.
Events are emitted outside the driver's lock (the Desk takes its own lock in `emit`).

The process comes from a **launcher** (builddoc/harness-server.md §3): `DirectLauncher` (a child of this server,
the default) or the Harness Server's `HostedLauncher` (a process that outlives this server; the Desk sets it). A
`reattachable` harness's hosted process can be handed off at a restart: `checkpoint()` here, `restore()` and
`attach()` in the next server, which reads on from the byte offset this one stopped at.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
from collections import deque
from typing import Any

from orbit.drivers.base import Driver, DriverEvent

class DirectLauncher:
    """Agents as children of this server, joined to it by pipes (they end when it does)."""

    def spawn(self, argv: list[str], cwd: str, env: dict[str, str], tag: str = "") -> subprocess.Popen:
        return subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1, env=env)


DIRECT = DirectLauncher()

STOP_GRACE = 5.0  # seconds an interrupted request has to finish before SIGINT (then kill)
EXIT_WAIT = 15.0  # seconds the next request waits for an interrupted process to exit


TURN_EVENTS = ("delta", "thinking", "text", "tool_start")  # what only a turn says: while idle, one of the harness's own


class ProcessDriver(Driver):
    per_request = False  # True: a process per request, the request on its stdin (then closed): Codex's exec mode
    launcher = DIRECT  # where its processes start (the Desk sets the Harness Server's when it runs the agents)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None  # the current process's stdout reader
        self.req_proc: subprocess.Popen | None = None  # the process the running request went to (None: starting)
        self.doomed: subprocess.Popen | None = None  # a process sent SIGINT: it is expected to exit
        self.lock = threading.RLock()
        self.wlock = threading.Lock()
        self.stderr_tail: deque[str] = deque(maxlen=40)
        self.turn_text: list[str] = []  # text since the last tool call: the answer when the harness gives none
        self.ready = self.failed = self.busy = self.stopping = self.closing = False
        self.acked = True  # the harness said something since the request was written (it took it)
        self._line_ts: float | None = None  # when the stdout line being handled arrived (a hosted process says)
        self.auto = False  # a turn the harness began itself (no request) is running: its end is a result marked auto
        self._autos = 0  # how many such turns began (a Stop's give-up must not end the next one)
        self.woke: dict[str, str] | None = None  # what will have woken it ({why, what}: a harness sets it, the turn takes it)
        self._out, self.emit = self.emit, self._emit  # (every event passes _emit: a turn of its own is noticed there)

    # ------------------------------------------------------------------ what a harness says
    def program(self) -> list[str]:
        """The command's argv prefix (the program, maybe an interpreter and a script)."""
        raise NotImplementedError

    def command(self) -> list[str]:
        raise NotImplementedError

    def request_line(self, text: str) -> str:
        """The stdin line that sends a request to a long-lived process ("" from this or the other *_line hooks:
        nothing to write now, the harness module sends it itself)."""
        raise NotImplementedError

    def steer_line(self, text: str) -> str:
        raise RuntimeError(f"{self.label} cannot take a message in the middle of a request")

    def interrupt_line(self) -> str | None:
        """The stdin line that interrupts the running request (None: SIGINT)."""
        return None

    def begin(self) -> None:
        """A request starts: reset what the harness tracks per request."""

    def handle(self, msg: dict) -> None:
        raise NotImplementedError

    def keep_err(self, line: str) -> bool:
        """Whether a stderr line belongs in the error shown when something goes wrong."""
        return True

    def plain_line(self, line: str) -> None:
        """A stdout line that is not JSON: not an event, kept for the error message (a harness whose events come
        another way reads its own start-up line here: OpenCode's address)."""
        self.stderr_tail.append(line)

    def env(self) -> dict[str, str]:
        """The process's environment."""
        return {**os.environ, "ORBIT_SESSION": self.session_id}

    def spawned(self, proc: subprocess.Popen) -> None:
        """A process started (e.g. later ones continue the conversation)."""

    # ------------------------------------------------------------------ what a harness calls
    def said(self, text: str) -> None:
        """A whole block of text (narration, or the answer if nothing follows)."""
        self.turn_text.append(text)
        self.emit(DriverEvent("text", {"text": text}))

    def tool_started(self, step: dict[str, Any]) -> None:
        self.turn_text = []  # text before a tool call is narration (the Desk makes it a note)
        if self._line_ts:  # when it arrived, not when it was read (after a restart, the gap is read later)
            step = {**step, "t0": self._line_ts}
        self.emit(DriverEvent("tool_start", step))

    def answer_so_far(self) -> str:
        return "\n\n".join(self.turn_text)

    def _emit(self, ev: DriverEvent) -> None:
        """Every event goes out here: the first one only a turn says, while no request runs, begins a turn of the
        harness's own (announced first, with what woke it), so the Desk can give it a card."""
        if ev.kind in TURN_EVENTS and not self.busy and not self.auto and not self.closing:
            with self.lock:
                began = not self.busy and not self.auto
                if began:
                    self.auto, woke, self.woke = True, self.woke, None
                    self._autos += 1
                    if ev.kind != "text":  # (said() put its text in already: what came before is the last turn's)
                        self.turn_text = []
            if began:
                self._out(DriverEvent("auto", dict(woke or {})))
        self._out(ev)

    def finish(self, ok: bool, text: str | None = None, *, cancelled: bool = False, error: str = "",
               paused: bool = False) -> bool:
        """The running request ended, or the turn the harness began itself (once: later calls, and calls while
        idle, are ignored)."""
        with self.lock:
            if self.busy:
                self.busy, self.req_proc, auto = False, None, False
            elif self.auto:
                self.auto, auto = False, True
            else:
                return False
        data: dict[str, Any] = {"ok": ok, "text": self.answer_so_far() if text is None else text,
                                "cancelled": cancelled and not ok}
        self.turn_text = []  # (a turn of its own that follows starts from nothing)
        if paused:
            data["paused"] = True
        if auto:
            data["auto"] = True
        if not ok and not cancelled:
            data["error"] = error or "error"
        self.emit(DriverEvent("result", data))
        return True

    def stderr_text(self, n: int = 6) -> str:
        return "\n".join(list(self.stderr_tail)[-n:])

    # ------------------------------------------------------------------ the contract
    def start(self) -> None:
        self.emit(DriverEvent("state", {"state": "starting"}))
        prog = (self.program() or [""])[0]
        err = "" if prog and (os.path.isfile(prog) or shutil.which(prog)) else f"{prog or self.harness}: not found"
        if not err and not self.per_request:
            with self.lock:
                err = self._spawn()
        if err:
            self.failed = True
            self.emit(DriverEvent("state", {"state": "not ready", "error": err}))
            return
        self.ready, self.failed = True, False
        self.emit(DriverEvent("state", {"state": "ready"}))

    def alive(self) -> bool:
        return self.ready and not self.failed and not self.closing

    def send(self, text: str) -> None:
        if not self.alive():
            raise RuntimeError("session process is not running")
        with self.lock:
            if self.busy:
                raise RuntimeError("a request is already running")
            self.busy, self.stopping = True, False
            # A turn the harness began itself is running: the request is written all the same (as before: the harness
            # folds it into that turn, whose one result is then the request's), and the turn is the request's now.
            self.auto, self.woke = False, None
            self.turn_text = []
            self.begin()
            p = self.proc
            if not self.per_request and p is not None and p.poll() is None and p is not self.doomed:
                self.req_proc = p
                try:
                    self._write(p, self.request_line(text))
                except RuntimeError:
                    self.busy, self.req_proc = False, None
                    raise
                self.acked = False
                if hasattr(p, "trim"):  # a hosted process: its journal before what was read can go
                    p.trim()
                return
            self.req_proc, reader = None, self.reader
        # A new process: wait for the last one to be gone (its exit settled), then start it, off the caller's thread.
        threading.Thread(target=self._launch, args=(p, reader, text), daemon=True).start()

    def steer(self, text: str) -> None:
        if not self.steers:
            raise RuntimeError(f"{self.label} cannot take a message in the middle of a request")
        with self.lock:
            p = self.req_proc if self.busy else self.proc if self.auto else None  # (a turn of its own takes one too)
            if p is None:
                raise RuntimeError("no request is running")
            self._write(p, self.steer_line(text))

    def stop(self) -> None:
        with self.lock:
            if not self.busy:
                if self.auto:
                    self._stop_auto()
                return
            self.stopping = True
            p = self.req_proc
            if p is None or p.poll() is not None:
                return  # still starting: _launch cancels it; already gone: its reader reports it
            line = self.interrupt_line()
            try:
                if line is None:
                    raise RuntimeError("signal")
                self._write(p, line)
            except RuntimeError:
                self._sigint(p)

        def escalate() -> None:  # no result in time: SIGINT, then the process goes
            if not (self.busy and self.req_proc is p and p.poll() is None) or getattr(p, "detached", False):
                return  # (a process handed off to the next server is its business now)
            if p is not self.doomed:
                self._sigint(p)
                try:
                    p.wait(3)
                    return
                except subprocess.TimeoutExpired:
                    pass
            try:
                p.kill()
            except OSError:
                pass
        t = threading.Timer(STOP_GRACE, escalate)
        t.daemon = True
        t.start()

    def _stop_auto(self) -> None:
        """Stop a turn the harness began itself: its interrupt message, never a signal (the process, and the background
        subagents it runs, stay); no end within STOP_GRACE s: the turn is taken as ended, so nothing waits behind it."""
        p = self.proc
        try:
            line = self.interrupt_line() if p is not None else None
            if line:
                self._write(p, line)
        except RuntimeError:
            pass

        n = self._autos

        def give_up() -> None:
            if self.auto and not self.busy and self._autos == n:
                self.finish(False, cancelled=True)
        t = threading.Timer(STOP_GRACE, give_up)
        t.daemon = True
        t.start()

    def close(self) -> None:
        self.closing = True
        p = self.proc
        if p is None or p.poll() is not None:
            self.emit(DriverEvent("state", {"state": "stopped"}))
            return
        if self.busy:
            self.stopping = True
            self._sigint(p)
        try:
            if p.stdin and not p.stdin.closed:
                p.stdin.close()  # a long-lived harness ends when its input does
        except OSError:
            pass
        try:
            p.wait(3)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()

    # ------------------------------------------------------------------ a restart of the server (hand-off)
    def can_hand_off(self) -> bool:
        """Its process can be carried over to the next server: hosted, alive, and in no state that cannot be (a Stop
        in progress, a process starting, a request the harness has not taken yet, closing)."""
        with self.lock:
            p = self.proc
            return (self.reattachable and p is not None and hasattr(p, "detach") and p.poll() is None
                    and self.ready and not self.failed and not self.closing and p is not self.doomed
                    and not (self.busy and (self.stopping or self.req_proc is None or not self.acked)))

    def detach_for_hand_off(self) -> tuple[Any, int] | None:
        """The hand-off's first step: (its process, the offset read) once its reader has stopped at a line boundary,
        or None (closed as before). The check and the start of the detach run under the lock, so no Stop, steer or
        request slips in between; the wait for the reader does not (it may need the lock to finish its line)."""
        with self.lock:
            if not self.can_hand_off():
                return None
            p = self.proc
            if not p.begin_detach():
                return None  # it exited just now: its driver reports that as always
        off = p.detach()
        return None if off is None else (p, off)

    def checkpoint(self) -> dict[str, Any]:
        """What the next server needs to read on (plain JSON): a harness adds its own to this."""
        return {"turn_text": list(self.turn_text), "resume": self.resume, "busy": self.busy, "auto": self.auto}

    def restore(self, cp: dict[str, Any]) -> None:
        self.turn_text = [str(x) for x in cp.get("turn_text") or []]
        self.resume = bool(cp.get("resume", True))
        self.auto = bool(cp.get("auto")) and not cp.get("busy")  # a turn of its own read on (its result ends its card)

    def running(self) -> bool:
        """Its process is running (hosted or direct)."""
        p = self.proc
        return p is not None and p.poll() is None

    def attach(self, proc, checkpoint: dict[str, Any], busy: bool) -> None:
        """Take over a running process (a hand-off) instead of start(): its output is read from where the last
        server stopped; once caught up (its stream says `live`) the session is ready (never "starting": that would
        end its background subagents)."""
        self.restore(checkpoint)
        with self.lock:
            self.proc = proc
            self.ready, self.failed = True, False
            self.busy = busy
            self.req_proc = proc if busy else None
            # Caught up: `live` says so (the Desk ends catching up on it, not on any other "ready").
            proc.on_live = lambda: self.emit(DriverEvent("state", {"state": "ready", "live": True}))
            self.stderr_tail.clear()
            self.reader = threading.Thread(target=self._read_out, args=(proc,), daemon=True)
            self.reader.start()
            threading.Thread(target=self._read_err, args=(proc,), daemon=True).start()

    # ------------------------------------------------------------------ the process
    def _spawn(self) -> str:
        """Start a process (with the lock held): '' or why it could not start."""
        try:
            proc = self.launcher.spawn(self.command(), self.cwd, self.env(), tag=self.session_id)
        except (OSError, ValueError) as e:
            return str(e)
        self.proc = proc
        self.stderr_tail.clear()
        self.reader = threading.Thread(target=self._read_out, args=(proc,), daemon=True)
        self.reader.start()
        threading.Thread(target=self._read_err, args=(proc,), daemon=True).start()
        self.spawned(proc)
        return ""

    def _launch(self, old: subprocess.Popen | None, reader: threading.Thread | None, text: str) -> None:
        if old is not None and old.poll() is None:
            try:
                old.wait(EXIT_WAIT)
            except subprocess.TimeoutExpired:
                old.kill()
        if reader is not None and reader is not threading.current_thread():
            reader.join(EXIT_WAIT)
        with self.lock:
            if self.stopping or self.closing:  # Stop (or close) came while it was starting
                err, cancelled = "", True
            else:
                err, cancelled = self._spawn(), False
            if not err and not cancelled:
                p = self.req_proc = self.proc
                assert p is not None
                try:
                    if self.per_request:
                        assert p.stdin
                        p.stdin.write(text)
                        p.stdin.close()
                    else:
                        self._write(p, self.request_line(text))
                    self.acked = False
                except (OSError, RuntimeError):
                    pass  # its reader reports how the process ended
                return
        self.finish(False, "", cancelled=cancelled, error=err)

    def _write(self, p: subprocess.Popen, line: str) -> None:
        """Write one stdin line. A hook that returns "" has nothing to write now (it sends its message itself later,
        e.g. once the harness has answered a handshake)."""
        if p.poll() is not None:
            raise RuntimeError("the agent's process is not running")
        if getattr(p, "handed_off", False):
            raise RuntimeError("the agent's process was handed off to the next server")
        if not line:
            return
        try:
            with self.wlock:
                assert p.stdin
                p.stdin.write(line + "\n")
                p.stdin.flush()
        except (OSError, ValueError, AssertionError) as e:
            raise RuntimeError(f"could not write to the agent: {e}") from e

    def _sigint(self, p: subprocess.Popen) -> None:
        self.doomed = p
        try:
            p.send_signal(signal.SIGINT)
        except OSError:
            pass

    def _read_err(self, proc: subprocess.Popen) -> None:
        assert proc.stderr
        for line in proc.stderr:
            line = line.rstrip()
            if line and self.keep_err(line):
                self.stderr_tail.append(line)
        proc.stderr.close()

    def _read_out(self, proc: subprocess.Popen) -> None:
        assert proc.stdout
        for line in proc.stdout:
            self.acked = True
            self._line_ts = getattr(proc, "line_ts", None)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.plain_line(line)
                continue
            if not isinstance(msg, dict):
                continue
            try:
                self.handle(msg)
            except Exception as e:  # a bad message must not end the reader
                self.stderr_tail.append(f"orbit: {type(e).__name__}: {e}")
        if getattr(proc, "detached", False):
            return  # handed off: the next server reads on, and reports its exit
        code = proc.wait()
        for pipe in (proc.stdin, proc.stdout):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass
        self._exited(proc, code)

    def _exited(self, proc: subprocess.Popen, code: int) -> None:
        with self.lock:
            if proc is not self.proc:
                return  # an older process (replaced)
            self.proc = None
            mine = self.busy and self.req_proc is proc
            auto, self.auto = self.auto, False  # a turn of its own ends with its process
            expected = self.per_request or proc is self.doomed or self.stopping or self.closing or code == 0
            if not expected:
                self.failed = True
        err = self.stderr_text()
        if mine:
            if self.stopping:
                self.finish(False, cancelled=True)
            else:  # the agent went away in the middle of a request: paused, continued by the Desk
                with self.lock:
                    self.busy, self.req_proc = False, None
                self.emit(DriverEvent("result", {"ok": False, "text": self.answer_so_far(), "cancelled": False,
                                                 "paused": True, "died": True,
                                                 "error": err or f"the agent stopped before it finished (exit {code})"}))
        if auto and not mine:
            self.emit(DriverEvent("result", {"ok": False, "text": self.answer_so_far(), "auto": True,
                                             "cancelled": bool(self.stopping or self.closing or proc is self.doomed),
                                             "error": err or f"the agent's process ended (exit {code})"}))
        if self.closing:
            self.emit(DriverEvent("state", {"state": "stopped"}))
        elif not expected:
            self.emit(DriverEvent("state", {"state": "not ready", "error": err or f"process exited ({code})"}))
