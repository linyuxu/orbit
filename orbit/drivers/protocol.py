"""A harness that speaks the Orbit Harness Protocol (builddoc/harness-protocol.md): JSON lines on stdin / stdout.

`protocol_driver(id, spec, folder)` makes a Driver class for one declared harness (a plugin's `[harness.<id>]`):
its command, label and machine-locality from the declaration; models, efforts, modes and capabilities from the
agent's own `--describe` (asked once, cached). One process serves the session's requests; it may end between them
(the next request starts it again with `--conversation`); ending during a request pauses that request, which the
Desk continues by itself.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from orbit.drivers.base import DriverEvent, HistoryCard, strip_orbit_line, tool_summary
from orbit.drivers.process import ProcessDriver

PROTOCOL = 1
QUERY_TIMEOUT = 20


def _command(spec_cmd: list[str] | str, folder: Path | None) -> list[str]:
    """The declared argv: a relative program is the plugin folder's (when it exists there), else found on PATH."""
    argv = shlex.split(spec_cmd) if isinstance(spec_cmd, str) else [str(x) for x in spec_cmd]
    if not argv:
        raise ValueError("an empty command")
    prog = argv[0]
    if folder and not os.path.isabs(prog) and (folder / prog).exists():
        argv[0] = str((folder / prog).resolve())
    return argv


class ProtocolDriver(ProcessDriver):
    """One session of a harness that speaks the protocol (subclassed per declared harness: see protocol_driver).
    Every one is reattachable: a hand-off is the same process on the same pipes, invisible to the agent."""

    reattachable = True

    command_argv: list[str] = []
    folder: Path | None = None
    plugin = ""  # the plugin that declared it: its secrets and data folder go to the agent's environment
    described: dict[str, Any] | None = None
    has_terminal = False  # --describe: capabilities.terminal (protocol 1.1)
    usage_fraction = True  # --usage reports `used` as 0–1 (the usage service turns it into percent)

    # ---------------------------------------------------------------- what the agent says about itself
    @classmethod
    def plugin_env(cls) -> dict[str, str]:
        if not cls.plugin:
            return {}
        from orbit import plugins
        try:
            return plugins.env(cls.plugin)
        except ValueError:
            return {}

    @classmethod
    def query(cls, *args: str, timeout: float = QUERY_TIMEOUT) -> str | None:
        """Run `command ARGS` (a question, not a session): its stdout, or None when it fails or is not supported."""
        try:
            r = subprocess.run([*cls.command_argv, *args], capture_output=True, text=True, timeout=timeout,
                               cwd=str(cls.folder) if cls.folder else None, stdin=subprocess.DEVNULL,
                               env={**os.environ, **cls.plugin_env()})
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout if r.returncode == 0 else None

    @classmethod
    def describe(cls) -> dict[str, Any]:
        if cls.described is None:
            out = cls.query("--describe", timeout=10)
            try:
                d = json.loads(out) if out else {}
            except ValueError:
                d = {}
            cls._take_described(d if isinstance(d, dict) else {})
        return cls.described

    @classmethod
    def primed(cls, cp: dict[str, Any]) -> None:
        """A hand-off carries what the agent said of itself: taking it never runs `--describe`."""
        if cls.described is None and isinstance(cp.get("described"), dict) and cp["described"]:
            cls._take_described(cp["described"])

    @classmethod
    def _take_described(cls, d: dict[str, Any]) -> None:
        cls.described = d
        caps = d.get("capabilities") or {}
        cls.steers = bool(caps.get("steer"))
        cls.efforts = tuple(str(x) for x in d.get("efforts") or ())
        if d.get("modes"):
            cls.modes = tuple(str(x) for x in d["modes"])
        if d.get("label") and cls.label == cls.harness:
            cls.label = str(d["label"])
        cls.has_terminal = bool(caps.get("terminal"))
        cls.native_fork = caps.get("fork", True) is not False  # (1.2) `fork: false`: Orbit carries the conversation over

    @classmethod
    def models(cls) -> list[dict[str, str]]:
        ms = cls.describe().get("models") or []
        return [{"value": str(m.get("value", "")), "label": str(m.get("label") or m.get("value", ""))}
                for m in ms if isinstance(m, dict) and m.get("value")]

    @classmethod
    def external_sessions(cls, limit: int, skip: set[str]) -> list[dict[str, Any]]:
        return [x for x in cls.list_conversations(limit) or [] if x["id"] not in skip][:limit]

    @classmethod
    def list_conversations(cls, limit: int) -> list[dict[str, Any]] | None:
        """`--list-conversations --limit N`: its rows, or None when it failed (the server's index runs it in the
        background, desk/external_index.py: the dialog never waits for it)."""
        out = cls.query("--list-conversations", "--limit", str(limit))
        if out is None:
            return None
        found = []
        for line in (out or "").splitlines():
            try:
                x = json.loads(line)
            except ValueError:
                continue
            if isinstance(x, dict) and x.get("id"):
                try:
                    t = float(x.get("time") or 0)
                except (TypeError, ValueError):
                    t = 0.0
                found.append({"harness": cls.harness, "id": str(x["id"]), "cwd": str(x.get("cwd") or ""),
                              "first": str(x.get("first") or "")[:300], "time": t, "name": str(x.get("name") or "")[:200],
                              "title": str(x.get("title") or "")[:200], "live": x.get("live")})
        return found[:limit]

    @classmethod
    def skill_dirs(cls, folder: Path) -> list[tuple[Path, str, str]]:
        """`skill_dirs` from --describe (protocol 1.1): relative to the session's folder, or absolute."""
        out = []
        for d in cls.describe().get("skill_dirs") or []:
            p = Path(os.path.expanduser(str(d)))
            out.append((p if p.is_absolute() else Path(folder) / p, "skills", "folder" if not p.is_absolute() else "user"))
        return out

    @classmethod
    def usage(cls) -> dict[str, Any] | None:
        out = cls.query("--usage", timeout=10)
        try:
            d = json.loads(out) if out else None
        except ValueError:
            return None
        if not isinstance(d, dict):
            return None
        # (1.3) "quota": a line of its own ("8 USD left"), shown after its meter (or alone): a string, 40 characters
        q = " ".join(d["quota"].split())[:40] if isinstance(d.get("quota"), str) else ""
        d = {k: v for k, v in d.items() if k != "quota"}
        if not isinstance(d.get("limits"), list):
            d["limits"] = []
        return {**d, "quota": q} if q else d

    # ---------------------------------------------------------------- a session (the engine: process.py)
    def __init__(self, **kw):
        super().__init__(**kw)
        self.seq = 0
        self.req_id = ""
        self.terminal = []
        self.conversation = self.linked() if self.resume else ""
        self.describe()

    def program(self) -> list[str]:
        return list(self.command_argv)

    def env(self) -> dict[str, str]:
        return {**super().env(), **self.plugin_env()}

    def command(self) -> list[str]:
        cmd = list(self.command_argv)
        own = self.conversation or (self.linked() if self.resume else "")
        src = self.linked(self.fork_from) if self.fork_from and not own else ""
        if own:
            cmd += ["--conversation", own]
        elif src:
            cmd += ["--fork", src]
        if self.model:
            cmd += ["--model", self.model]
        if self.mode and self.mode != "default":
            cmd += ["--mode", self.mode]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    def begin(self) -> None:
        self.seq += 1
        self.req_id = f"r{self.seq}"

    def checkpoint(self) -> dict[str, Any]:
        return {**super().checkpoint(), "seq": self.seq, "req_id": self.req_id, "conversation": self.conversation,
                "terminal": list(self.terminal), "described": type(self).described or {}}

    def restore(self, cp: dict[str, Any]) -> None:
        super().restore(cp)
        self.seq = int(cp.get("seq") or 0)
        self.req_id = str(cp.get("req_id") or "")
        self.conversation = str(cp.get("conversation") or "")
        self.terminal = [str(x) for x in cp.get("terminal") or []]

    def request_line(self, text: str) -> str:
        return json.dumps({"type": "request", "id": self.req_id, "text": text})

    def steer_line(self, text: str) -> str:
        return json.dumps({"type": "steer", "request": self.req_id, "text": text})

    def interrupt_line(self) -> str:
        return json.dumps({"type": "interrupt"})

    # ---------------------------------------------------------------- what it says
    def handle(self, m: dict) -> None:
        t = m.get("type")
        if t == "hello":
            conv = str(m.get("conversation") or "")
            if conv:
                self.conversation = conv
                self.link(conv)  # the next process (after any restart) continues this conversation
            term = (m.get("terminal") or {}).get("command") if isinstance(m.get("terminal"), dict) else None
            if self.has_terminal and isinstance(term, list) and term and all(isinstance(x, str) for x in term):
                self.terminal = list(term)  # (1.1) what ⌘T runs to attach to the agent's terminal
            self.emit(DriverEvent("info", {"model": str(m.get("model") or ""),
                                           "commands": [str(c) for c in m.get("commands") or []],
                                           "terminal": bool(self.terminal)}))
        elif t == "attention":  # (1.1) the agent waits for the user (in its terminal); "" clears it
            self.emit(DriverEvent("attention", {"detail": str(m.get("detail") or "")[:200]}))
        elif t in ("delta", "thinking"):
            d = {"text": str(m.get("text") or "")}
            if t == "thinking" and m.get("start"):
                d["start"] = True
            self.emit(DriverEvent(t, d))
        elif t == "text":
            self.said(str(m.get("text") or ""))
        elif t in ("tool_start", "sub_tool_start"):
            name = str(m.get("name") or "tool")
            st = {"id": str(m.get("id") or ""), "name": name,
                  "summary": str(m.get("summary") or tool_summary(m.get("input")) or "")[:300]}
            if t == "tool_start" and m.get("agent"):
                st.update(agent=str(m["agent"]), sub=[], result="")
            if t == "sub_tool_start":
                st.update(parent=str(m.get("parent") or ""), kind="tool", done=False, error=False)
            self.emit(DriverEvent(t, st))
        elif t in ("tool_end", "sub_tool_end"):
            d = {"id": str(m.get("id") or ""), "error": bool(m.get("error"))}
            if t == "sub_tool_end":
                d["parent"] = str(m.get("parent") or "")
            if "result" in m:
                d["result"] = str(m.get("result") or "")
            if m.get("background"):
                d["background"] = True
            self.emit(DriverEvent(t, d))
        elif t == "sub_text":
            self.emit(DriverEvent("sub_text", {"parent": str(m.get("parent") or ""), "text": str(m.get("text") or "")}))
        elif t == "agent_done":
            self.emit(DriverEvent("agent_done", {"id": str(m.get("id") or ""), "status": str(m.get("status") or ""),
                                                 "result": str(m.get("result") or "")}))
        elif t == "activity":
            self.emit(DriverEvent("activity", {"detail": str(m.get("detail") or "")[:200]}))
        elif t == "usage":
            try:
                ctx = int(m.get("context") or 0)
            except (TypeError, ValueError):
                ctx = 0
            self.emit(DriverEvent("usage", {"context": ctx, "model": str(m.get("model") or "")}))
        elif t == "compaction":
            self.emit(DriverEvent("compaction", {}))
        elif t == "result":
            ok = bool(m.get("ok"))
            self.finish(ok, str(m.get("text") or "") or None, cancelled=bool(m.get("cancelled")) or self.stopping,
                        error=str(m.get("error") or "the agent reported an error"))
        elif t == "error":
            self.emit(DriverEvent("state", {"state": "ready", "error": str(m.get("text") or "")}))
        # anything else: a newer protocol's message, ignored

    # ---------------------------------------------------------------- the conversation so far
    def history(self) -> list[HistoryCard]:
        conv = self.conversation or self.linked()
        if not conv:
            return []
        out = self.query("--history", conv)
        cards = []
        for line in (out or "").splitlines():
            try:
                x = json.loads(line)
            except ValueError:
                continue
            if not isinstance(x, dict) or not x.get("text"):
                continue
            steps = []
            for st in x.get("steps") or []:
                if not isinstance(st, dict):
                    continue
                kind = "note" if st.get("kind") == "note" else "tool"
                steps.append({"id": str(st.get("id") or ""), "kind": kind, "name": str(st.get("name") or ""),
                              "summary": str(st.get("summary") or st.get("text") or "")[:300],
                              **({"text": str(st["text"])} if st.get("text") else {}),
                              "done": True, "error": bool(st.get("error"))})
            cards.append(HistoryCard(
                text=strip_orbit_line(str(x["text"])), time=x.get("time"), steps=steps, answer=str(x.get("answer") or ""),
                followups=[str(f) for f in x.get("followups") or []], finished=x.get("finished"),
                state=str(x.get("state") or "completed"),
                steers=[s if isinstance(s, dict) else {"text": str(s)} for s in x.get("steers") or []],
                **({"kind": "auto", "auto": {k: str(v) for k, v in x["auto"].items() if k in ("why", "what")}}
                   if isinstance(x.get("auto"), dict) else {})))  # (1.3) a turn the agent began itself
        return cards


def protocol_driver(harness: str, spec: dict[str, Any], folder: Path | None = None, plugin: str = "") -> type[ProtocolDriver]:
    """The Driver class for one declared harness ([harness.<id>] in a plugin.toml)."""
    argv = _command(spec.get("command") or [], folder)
    return type(f"Protocol_{harness}", (ProtocolDriver,), {
        "harness": harness, "label": str(spec.get("label") or harness), "command_argv": argv, "folder": folder,
        "plugin": plugin,
        "icon_url": f"/plugins/{plugin}/{str(spec['icon']).lstrip('/')}" if plugin and spec.get("icon") else "",
        "local_only": bool(spec.get("local_only", False)), "described": None,
        "efforts": (), "steers": False, "modes": ProtocolDriver.modes,
    })
