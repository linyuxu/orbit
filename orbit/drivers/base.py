"""The harness-neutral driver contract (builddoc/harness-protocol.md §6).

A driver adapts one harness (Claude Code, Codex, agy, OpenCode, or any agent speaking the Orbit Harness Protocol
through `ProtocolDriver`) to the Desk. The Desk never knows which harness it talks to: it calls the methods below and
receives `DriverEvent`s through the `emit` callback given at construction. All of them run on one engine (process.py:
ProcessDriver), which owns the process and the request's life; a harness module says only what differs.

Events (`DriverEvent.kind`, data):

| kind | data | meaning |
|---|---|---|
| state | state: starting · ready · not ready · stopped, error? | the process's state changed |
| info | model, commands | at start: the model in use, the harness's slash commands |
| delta | text | answer text as it is written |
| thinking | text, start? | thinking as it is written (start: a new block) |
| text | text | a whole block of text (narration or answer) |
| tool_start | id, name, summary, [agent, sub, result] | a tool call began (agent: it starts a subagent) |
| tool_end | id, error, result?, background? | it finished (background: it runs on; agent_done later) |
| sub_text · sub_tool_start · sub_tool_end | parent + as above | a subagent at work |
| agent_done | id, status, result | a background subagent finished |
| activity | detail | a short progress line |
| usage | context, model? | context the last model call read |
| compaction | | the conversation was compacted |
| auto | why?, what? | the harness began a turn on its own (no request; e.g. a background task finished): what woke it |
| result | ok, text, cancelled, paused?, died?, auto?, error? | the request finished (died: its process went away; auto: the turn of its own did) |
| attention | detail ("" clears it) | the agent waits for the user (in its terminal: ⌘T), not for Orbit |

A turn the harness began itself (`auto`, from the engine) that runs a tool gets a card of its own (kind "auto"); one
that only talks stays follow-ups on the last card. The Desk decides that, not the driver.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


ORBIT_PREFIX = "[orbit] "
_ids_lock = threading.Lock()


FORK_OPEN, FORK_CLOSE = "[orbit-fork]", "[/orbit-fork]"  # the conversation a fork carries over (a harness that cannot fork)


def strip_orbit_line(text: str) -> str:
    """The request as the user wrote it: without the [orbit] context line Orbit puts before a process's first one, nor
    the conversation a fork carries over to a harness that cannot fork natively."""
    if text.startswith(ORBIT_PREFIX):
        text = text.split("\n", 1)[1].lstrip("\n") if "\n" in text else ""
    if text.startswith(FORK_OPEN) and FORK_CLOSE in text:
        text = text.split(FORK_CLOSE, 1)[1].lstrip("\n")
    return text


def auto_title(why: str, what: str) -> str:
    """A turn of its own as one line (its card's text, for read-outs and search): "After a background agent finished:
    Find the parser", or "On its own" when the harness did not say what woke it."""
    why = why[:1].upper() + why[1:]
    return f"{why}: {what}" if why and what else why or what or "On its own"


# Argument names that say what a tool call works on, in the spellings harnesses use (command, CommandLine, file_path,
# AbsolutePath, ...): compared lower-case without underscores.
_SUMMARY_KEYS = ("command", "commandline", "cmd", "filepath", "absolutepath", "targetfile", "path", "pattern",
                 "query", "url", "searchpath", "directorypath", "description", "prompt", "toolsummary")


def tool_summary(params: Any) -> str:
    """One short line naming what a tool call works on (any harness's argument spelling)."""
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            params = {"command": params}
    if not isinstance(params, dict):
        return ""
    norm = {str(k).lower().replace("_", ""): v for k, v in params.items()}
    for key in _SUMMARY_KEYS:
        v = norm.get(key)
        if isinstance(v, list):
            v = " ".join(str(x) for x in v)
        if isinstance(v, str) and v.strip():
            line = v.strip().splitlines()[0]
            home = str(Path.home())
            if line.startswith(home):
                line = "~" + line[len(home):]
            return line[:200]
    return ""


@dataclass
class DriverEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


Emit = Callable[[DriverEvent], None]


@dataclass
class HistoryCard:
    """One past request rebuilt from the harness's own store."""

    text: str
    time: float | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    followups: list[str] = field(default_factory=list)
    compactions: int = 0
    finished: float | None = None
    state: str = "completed"
    steers: list[dict[str, Any]] = field(default_factory=list)  # messages that joined the request mid-turn
    # "auto": a turn the harness began itself (no request), with auto = {why, what} (what woke it); text is its title
    kind: str = ""
    auto: dict[str, str] | None = None


class Driver:
    """Base class: one instance per session."""

    harness = "?"
    label = "?"  # the harness's name as the page shows it
    efforts: tuple[str, ...] = ()  # reasoning effort levels the harness takes (empty: none)
    modes: tuple[str, ...] = ("default", "acceptEdits", "plan", "bypassPermissions")  # permission modes it maps
    steers = False  # True: a message can join the request that is running (sent mid-turn, no new request)
    local_only = False  # True: this machine only (never offered for, or run by, another machine's server)
    identity = "harness-id"  # "orbit-id": its conversation id is the Orbit session's; "harness-id": linked (link())
    native_fork = True  # the harness forks a conversation itself; else a fork's first request carries the conversation over
    agent_hint = ""  # appended to the [orbit] line for this harness's agents (what differs when it runs in Orbit)
    icon_url = ""  # its mark for the page (a plugin harness's ui/ icon; the built-ins' are the page's own)
    terminal: list[str] = []  # an instance's: the argv that attaches to the agent's terminal (⌘T), [] when it has none
    # Its process can be handed off at a restart of the server and read on by the next one (builddoc/harness-server.md
    # §4): checkpoint() is what the driver needs to read on, restore() its reverse on a new instance, attach() takes
    # the process over (ProcessDriver). Not reattachable: closed at a restart, and resumed as before.
    reattachable = False

    def __init__(self, *, session_id: str, cwd: str, model: str, mode: str, emit: Emit, resume: bool,
                 fork_from: str = "", state_dir: str = "", effort: str = ""):
        self.effort = effort
        self.session_id = session_id
        self.fork_from = fork_from  # clone: start from this conversation (the harness forks it)
        self.cwd = cwd
        self.model = model
        self.mode = mode
        self.emit = emit
        self.resume = resume
        self.state_dir = state_dir

    @classmethod
    def models(cls) -> list[dict[str, str]]:
        """Models the ⌘N dialog offers besides the harness's default: [{"value", "label"}]."""
        return []

    @classmethod
    def external_sessions(cls, limit: int, skip: set[str]) -> list[dict[str, Any]]:
        """Conversations started outside Orbit that it can resume (⌘O → Other sessions), newest first:
        [{"harness", "id", "cwd", "first", "time", "name"?, "title"?, "live"?}]; skip: ids Orbit already has."""
        return []

    # A harness whose conversations are files (Claude Code, Codex) says so: the server's index of conversations to
    # resume (desk/external_index.py) reads each file once, and again only when it changed. Without these, the index
    # calls external_sessions in the background.
    @classmethod
    def external_files(cls) -> list[tuple[str, float, int]] | None:
        """Its store's files, (path, mtime, size), newest first; None: not a store of files."""
        return None

    @classmethod
    def external_row(cls, path: str) -> dict[str, Any] | None:
        """One file of external_files as an external_sessions row, or None (nothing to resume in it)."""
        return None

    @classmethod
    def external_extra(cls) -> dict[str, dict[str, Any]]:
        """What changes without its file changing, by conversation id (a process that runs it, a name kept elsewhere)."""
        return {}

    @classmethod
    def usage(cls) -> dict[str, Any] | None:
        """The status bar's "Used": {"id", "label", "plan"?, "limits": [{"label", "used", "resets_at"}]} or None."""
        return None

    @classmethod
    def skill_dirs(cls, folder: Path) -> list[tuple[Path, str, str]]:
        """The harness's own skills and commands for `/` completion: [(dir, "skills" | "commands", source)]."""
        return []

    def terminal_command(self) -> list[str] | None:
        """The argv that continues this session's conversation in a terminal, with the harness's own interactive
        UI (Copy resume command); None when it has none (yet)."""
        return None

    def can_hand_off(self) -> bool:
        return False

    @classmethod
    def primed(cls, cp: dict[str, Any]) -> None:
        """Before a hand-off's driver is made: class-wide state its checkpoint carries (a protocol agent's
        `--describe`, so taking a hand-off never runs it)."""

    def checkpoint(self) -> dict[str, Any]:
        return {}

    def restore(self, cp: dict[str, Any]) -> None:
        pass

    def steer(self, text: str) -> None:
        """Add text to the request that is running (only when `steers`)."""
        raise RuntimeError(f"{self.label} cannot take a message in the middle of a request")

    # ------------------------------------------------------------------ conversation ids
    # Harnesses that name their own conversations (Codex threads, agy conversations) are found again through this
    # map: Orbit session id -> the harness's conversation id. Only ids live here, never the conversation.
    def _ids_path(self) -> Path | None:
        return Path(self.state_dir) / "harness-ids.json" if self.state_dir else None

    def linked(self, sid: str | None = None) -> str:
        return linked_id(self.state_dir, self.harness, sid or self.session_id)

    def link(self, conversation: str) -> None:
        p = self._ids_path()
        if not p or not conversation or self.linked() == conversation:
            return
        with _ids_lock:
            try:
                ids = json.loads(p.read_text())
            except (OSError, ValueError):
                ids = {}
            ids.setdefault(self.harness, {})[self.session_id] = conversation
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(ids, indent=1))
            os.replace(tmp, p)

    def start(self) -> None:
        raise NotImplementedError

    def send(self, text: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        """Interrupt the running request; the process stays."""
        raise NotImplementedError

    def close(self) -> None:
        """End the process: the server stops or hands off, the session's settings changed (a new process resumes the
        conversation), or end() below."""
        raise NotImplementedError

    def end(self) -> None:
        """The user closed the session (Desk.close): its agent is done with, not restarted. Default: close(). A driver
        whose agent outlives it (one running in a terminal multiplexer, which close() leaves running for the next
        server to take over) ends the agent here."""
        self.close()

    def alive(self) -> bool:
        raise NotImplementedError

    # ------------------------------------------------------------------ agents that outlive the server
    # For an agent running outside Orbit's processes (in a terminal multiplexer), which close() leaves running and a
    # later server should take on. Asked with no instance: `link` is the session's conversation id as the driver
    # linked it (link()), else the Orbit session's id (session_link()). (Not `alive`: that is the instance's process.)
    @classmethod
    def link_alive(cls, link: str, session_id: str = "", state_dir: str = "") -> bool:
        """At the server's start: does this session's agent still run? True: the session is followed at once (its
        driver made and started with resume=True, which takes the running agent on: no new one), so its state and
        ⌘T work without a Resume. False (the default): it waits, stopped, for a request or Resume, as before."""
        return False

    @classmethod
    def end_link(cls, link: str, session_id: str = "", state_dir: str = "") -> None:
        """The user closed a session Orbit has no driver for (not followed since the server started): end its agent
        all the same (as end() would). Default: nothing (no agent of it runs without its driver)."""

    def history(self) -> list[HistoryCard]:
        return []


def linked_id(state_dir: str, harness: str, sid: str) -> str:
    """The harness's conversation id linked to an Orbit session (harness-ids.json), or ""."""
    if not state_dir:
        return ""
    with _ids_lock:
        try:
            ids = json.loads((Path(state_dir) / "harness-ids.json").read_text())
        except (OSError, ValueError):
            return ""
    v = ids.get(harness, {}).get(sid, "") if isinstance(ids, dict) else ""
    return v if isinstance(v, str) else ""


def session_link(cls: type[Driver], sid: str, state_dir: str) -> str:
    """What link_alive / end_link are asked about: the conversation id linked, else the session's own id."""
    return linked_id(state_dir, cls.harness, sid) or sid


def unavailable(harness: str) -> type[Driver]:
    """The driver of a session whose harness is gone (its plugin removed or disabled): not ready, and says why."""
    class Unavailable(Driver):
        label = harness

        def start(self) -> None:
            self.emit(DriverEvent("state", {"state": "not ready", "error": f"the harness {harness!r} is not available: "
                                            "its plugin is not loaded (orbit plugin list)"}))

        def send(self, text: str) -> None:
            raise RuntimeError(f"the harness {harness!r} is not available")

        def stop(self) -> None:
            pass

        def close(self) -> None:
            self.emit(DriverEvent("state", {"state": "stopped"}))

        def alive(self) -> bool:
            return False
    Unavailable.harness = harness
    return Unavailable
