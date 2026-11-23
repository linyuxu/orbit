"""The ⌘J shell: one login shell per session, in the session's folder, on a pty the server owns.

The page shows it with xterm.js. Output streams over SSE as base64 (a read can split a UTF-8 character); the last
SCROLLBACK bytes are kept, so a reload (⌘⇧R) or a second window shows the screen again. The shell outlives the
page; it ends when the user kills it, the shell exits, its session closes or the server stops.
"""

from __future__ import annotations

import base64
import fcntl
import os
import pwd
import queue
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time
import uuid
from pathlib import Path


SCROLLBACK = 256 * 1024


def login_shell() -> str:
    for cand in (os.environ.get("ORBIT_SHELL"), _passwd_shell(), os.environ.get("SHELL"), "/bin/zsh", "/bin/sh"):
        if cand and shutil.which(cand):
            return cand
    return "/bin/sh"


def _passwd_shell() -> str | None:
    try:
        return pwd.getpwuid(os.getuid()).pw_shell
    except KeyError:
        return None


class Shell:
    def __init__(self, key: str, cwd: str, title: str = "", env: dict | None = None, argv: list[str] | None = None):
        self.id = uuid.uuid4().hex[:10]
        self.argv = argv  # a program instead of a login shell (an agent's terminal: its attach command)
        self.key, self.cwd, self.title = key, cwd, title
        self.env = env or {}
        self.buf = bytearray()
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.alive = False
        self.code: int | None = None
        self.started = 0.0
        self.proc: subprocess.Popen | None = None
        self.fd = -1
        self.cols, self.rows = 100, 30

    # ------------------------------------------------------------------ life
    def start(self) -> None:
        master, slave = os.openpty()
        self._winsize(slave)
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "TERM_PROGRAM": "Orbit", **self.env}
        env.pop("TERM_PROGRAM_VERSION", None)
        sh = login_shell()

        def child():  # a session of its own with the pty as its controlling terminal: job control works
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self.proc = subprocess.Popen(self.argv or [sh, "-l"], cwd=self.cwd if os.path.isdir(self.cwd) else str(Path.home()),
                                     stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True,
                                     preexec_fn=child, close_fds=True)
        os.close(slave)
        self.fd, self.alive, self.code, self.started = master, True, None, time.time()
        threading.Thread(target=self._read, name=f"shell-{self.id}", daemon=True).start()

    def _read(self) -> None:
        fd = self.fd
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if not data:
                break
            with self.lock:
                self.buf += data
                if len(self.buf) > SCROLLBACK:
                    del self.buf[: len(self.buf) - SCROLLBACK]
            self._emit({"t": "data", "d": base64.b64encode(data).decode()})
        try:
            code = self.proc.wait(timeout=5) if self.proc else None
        except subprocess.TimeoutExpired:
            code = None
        try:
            os.close(fd)
        except OSError:
            pass
        self.alive, self.code = False, code
        self._emit({"t": "exit", "code": code})

    def kill(self) -> None:
        if not self.proc or not self.alive:
            return
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            for _ in range(20):
                if self.proc.poll() is not None:
                    return
                time.sleep(0.05)

    def restart(self) -> None:
        self.kill()
        with self.lock:
            self.buf.clear()
        self._emit({"t": "reset"})
        self.start()

    # ------------------------------------------------------------------ io
    def write(self, text: str) -> None:
        if not self.alive:
            raise ValueError("the shell has exited")
        data = text.encode()
        while data:
            n = os.write(self.fd, data)
            data = data[n:]

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = max(2, min(1000, cols)), max(2, min(500, rows))
        if self.alive:
            self._winsize(self.fd)

    def _winsize(self, fd: int) -> None:
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))
        except OSError:
            pass

    # ------------------------------------------------------------------ stream
    def subscribe(self) -> tuple[queue.Queue, bytes]:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.subs.append(q)
            return q, bytes(self.buf)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def _emit(self, ev: dict) -> None:
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            q.put(ev)

    def public(self) -> dict:
        return {"id": self.id, "key": self.key, "cwd": self.cwd, "title": self.title, "alive": self.alive,
                "code": self.code, "started": self.started,
                "shell": os.path.basename(self.argv[0] if self.argv else login_shell()), "terminal": bool(self.argv)}


class Shells:
    def __init__(self):
        self.by_id: dict[str, Shell] = {}
        self.lock = threading.Lock()

    def open(self, key: str, cwd: str, title: str = "", env: dict | None = None, argv: list[str] | None = None) -> Shell:
        """The shell for key (a session id, or a folder), started if it is not there or has exited."""
        cwd = os.path.expanduser(cwd or "~")
        with self.lock:
            sh = next((s for s in self.by_id.values() if s.key == key), None)
            if sh and sh.alive:
                sh.title = title or sh.title
                return sh
            if sh:
                sh.cwd, sh.title = cwd, title or sh.title
                sh.argv = argv or sh.argv
                sh.restart()
                return sh
            sh = Shell(key, cwd, title, env, argv)
            sh.start()
            self.by_id[sh.id] = sh
            return sh

    def get(self, sid: str) -> Shell:
        return self.by_id[sid]

    def for_key(self, key: str) -> Shell | None:
        return next((s for s in self.by_id.values() if s.key == key), None)

    def close_key(self, key: str) -> None:
        with self.lock:
            sh = next((s for s in self.by_id.values() if s.key == key), None)
            if sh:
                self.by_id.pop(sh.id, None)
        if sh:
            sh.kill()

    def list(self) -> list[dict]:
        return [s.public() for s in self.by_id.values()]

    def shutdown(self) -> None:
        for s in list(self.by_id.values()):
            s.kill()


def open_terminal_app(cwd: str) -> str:
    """Open the folder in iTerm (or Terminal when iTerm is not installed); returns the app's name."""
    cwd = os.path.expanduser(cwd or "~")
    for app in ("iTerm", "Terminal"):
        if subprocess.run(["open", "-Ra", app], capture_output=True).returncode == 0:
            subprocess.Popen(["open", "-a", app, cwd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return app
    raise RuntimeError("neither iTerm nor Terminal is installed")
