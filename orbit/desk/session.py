"""A session (one harness conversation) and the desk's constants: modes, groups, the Fleet, settings, the [orbit] line."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from orbit import ROOT
from orbit.drivers.agy import AgyDriver
from orbit.drivers.base import Driver
from orbit.drivers.claude import ClaudeDriver
from orbit.drivers.codex import CodexDriver
from orbit.drivers.opencode import OpenCodeDriver
from orbit.services.live.config import DEFAULTS as LIVE_DEFAULTS


DRIVERS: dict[str, type[Driver]] = {"claude": ClaudeDriver, "codex": CodexDriver, "agy": AgyDriver,
                                     "opencode": OpenCodeDriver}
ORBIT_PREFIX = "[orbit] "
ORBIT_SKILLS = ROOT / "plugin" / "skills"  # Orbit's own skills (also offered by `/` in the input)

SETTINGS: dict[str, Any] = {
    "calendar": False,     # D4: Calendar reads macOS Calendar only when turned on
    "fleet_harness": "",   # the harness Fleet helpers (⌘K asks, Prep, `orbit-agent ask`) run: "" = claude
    "fleet_model": "",     # and their model: "" = the harness's default
    "week_template": "",   # a vault note whose text starts a new week note ({days}: the Mon–Fri headings); "" = built in
    "meeting_notes": "{vault}/Meetings/{date} {title}.md",  # where a meeting's note is (desk/meetings.py)
    "open_in": "default",  # where ↗ / "Open in …" open a file: default | jetbrains | vscode | side
    "serve_remote": False,  # other machines may reach this server (its Tailscale / LAN address, with the token)
    **LIVE_DEFAULTS,  # GPT Live voice (Settings → Voice): live_model, live_voice, … (orbit/services/live/config.py)
}
OPEN_IN = ("default", "jetbrains", "vscode", "side")


def orbit_line(s: "Session") -> str:
    """The [orbit] line: the first request of each agent process learns where it runs and what it can call.

    It is sent to the agent only; the card shows the user's text, and history parsing strips it again.
    """
    return (f'{ORBIT_PREFIX}You run inside Orbit, an IDE whose user reads your answers as rendered Markdown cards '
            f'(session "{s.name}", id {s.id}, folder {s.cwd}). The cards render Markdown: tables, highlighted code, KaTeX '
            "math ($…$, $$…$$), and images: ![caption](path) with an absolute, ~/ or folder-relative path to a PNG, JPG, "
            "GIF or SVG shows the picture in the answer (to show a plot, save it to a file and embed it), as do https: "
            "image URLs; `file.py:42` in inline code is a link that opens the file there. No raw HTML or Mermaid in answers "
            "(for an interactive page use orbit-agent show). Orbit routes the user's requests and gives you the orbit-agent "
            "commands below; your other tools are your own. Say what you read or check in the first person (\"I'll read "
            "that session\"); when you hand work to another session, name it. Shell commands for working with the user: "
            "`orbit-agent ctx` (what they are looking at: tab, file, cursor, selection), "
            "`orbit-agent open PATH[:LINE]` (open a file for them), `orbit-agent show FILE.html|-|URL` (show an HTML page or a "
            "website beside the conversation), `orbit-agent note TEXT` (add to their journal note), `orbit-agent pin TEXT` (pin a decision, conclusion or key fact to your group's home page: only what is worth keeping, never progress), `orbit-agent sessions`, "
            "`orbit-agent send SESSION TEXT` (ask another session), `orbit-agent relay SESSION WORDS` (pass the user's own words, "
            "word for word, to another session when the user asks: they arrive as the user's), `orbit-agent steer SESSION TEXT` (add to the request it is running now), "
            "`orbit-agent read SESSION --last N` (another session's recent "
            "turns), `orbit-agent mention SESSION TEXT` (a note to another agent, not a task), `orbit-agent wake WHEN TEXT` "
            "(wake yourself later), `orbit-agent notify TITLE BODY` (a notification on the user's phone pointing at this "
            "session: a finished report or a decision needed, not progress), `orbit-agent schedule add WHEN TEXT` / `schedule watch --every 1m --check CMD TEXT` "
            "(recurring work; a watch runs CMD for free and wakes you only on new events). Be proactive: when work needs a "
            "later look, schedule it yourself and say so; never sleep or poll in a turn. Show files and pages with "
            "orbit-agent instead of pasting long content. Before working with another session, read Orbit's skill "
            f"{ORBIT_SKILLS / 'orbit-agents' / 'SKILL.md'}." + _harness_hint(s) + _plugin_hints())


def _harness_hint(s: "Session") -> str:
    cls = DRIVERS.get(s.harness)
    return getattr(cls, "agent_hint", "") if cls else ""


def _plugin_hints() -> str:
    """What the user's plugins add for agents (their [agent] hint), still on the one line."""
    from orbit import plugins
    h = plugins.hints()
    return (" Plugins (`orbit-agent p NAME ARGS…` runs a plugin's command): " + "; ".join(h) + ".") if h else ""


def orbit_skill_hint(text: str) -> str:
    """A request that starts with /name of one of Orbit's own skills (repo skills/): where it is, for any harness."""
    m = re.match(r"\s*/([\w:.-]+)", text)
    if not m:
        return ""
    f = ORBIT_SKILLS / m.group(1) / "SKILL.md"
    return f"/{m.group(1)} is Orbit's skill {f}: read it and follow it." if f.is_file() else ""


def mention_tag(name: str) -> str:
    """How a session is mentioned: @@ and its title with dashes for spaces."""
    return "@@" + re.sub(r"\s+", "-", name.strip())


def clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


MODES = ["default", "acceptEdits", "auto", "plan", "bypassPermissions"]  # every mode Orbit knows; a harness says which it maps (Driver.modes)
ORIGINS_KEPT = 500  # a session remembers where its last 500 plugin-sent requests came from (and their client ids)
NOTES_KEPT = 200  # and its last 200 note cards
DEFAULT_GROUPS = [{"id": "main", "name": "Main"}, {"id": "projects", "name": "Projects"}, {"id": "personal", "name": "Personal"}]
FLEET_GROUP = "fleet"  # helper sessions live outside the user's groups: no tab, no left-panel row
FLEET_MAX = 3  # helpers per folder; more asks queue on the least busy one
ACTIVE = ("thinking", "running")


class Stopping(RuntimeError):
    """The server is stopping: no new work (the page gets 503 and tries again on the next server)."""


class Session:
    def __init__(self, meta: dict[str, Any]):
        self.id: str = meta["id"]
        self.name: str = meta.get("name") or Path(meta["cwd"]).name
        self.cwd: str = meta["cwd"]
        self.model: str = meta.get("model", "")
        self.mode: str = meta.get("mode", "default")
        self.effort: str = meta.get("effort", "")  # reasoning effort ("" = the harness's own setting)
        self.harness: str = meta.get("harness", "claude")
        self.commands: list[str] = []  # the harness's own slash commands, as its process reported them
        self.attention = ""  # the agent waits for the user (its terminal): what for
        self.terminal = False  # its harness can show the agent's terminal (⌘T)
        self.created: float = meta.get("created", time.time())
        self.closed: bool = meta.get("closed", False)
        self.closed_at: float | None = meta.get("closed_at")
        self.named: bool = meta.get("named", bool(meta.get("name")))  # a name the user gave: survives close
        self.group: str = meta.get("group", "")
        self.favorite: bool = meta.get("favorite", False)
        self.fork_from: str = meta.get("fork_from", "")  # a fork starts from this session's conversation
        self.fork_carry: int = meta.get("fork_carry", 0)  # a harness that cannot fork: this many cards go with the first request
        self.fork_harness: str = meta.get("fork_harness", "")  # a fork into another harness: the source's (a hand-over, no harness fork)
        self.last: dict | None = meta.get("last")  # preview of the last finished request (⌘R, finder)
        self.pending: list[dict] = meta.get("pending", [])  # queued requests not yet sent: survive restarts
        self.fleet: bool = meta.get("fleet", False)  # a Fleet helper (⌘K quick asks, `orbit-agent ask`)
        self.hidden: list[int] = meta.get("hidden", [])  # cards replaced by an edited request (ids only)
        self.origins: dict[str, dict] = meta.get("origins", {})  # card id → where the request came from (a plugin)
        self.client_ids: dict[str, int] = meta.get("client_ids", {})  # a sender's id for a request → its card (sent once)
        self.notes: list[dict] = meta.get("notes", [])  # note cards (a voice call's transcript): not requests, kept here
        self.interrupted: bool = meta.get("interrupted", False)  # a request was running when the server stopped
        self.was_running: bool = meta.get("running", False)  # a request ran at the last save (after a crash: cut off)
        self.catching_up = False  # its handed-off process's output is being read (no new request goes to it yet)
        self.state = "stopped"
        self.error = ""
        self.context = 0
        self.model_seen = ""  # the model the harness reports using
        self.compactions = 0
        self.activity = ""
        self.cards: list[dict] = []
        self.driver: Driver | None = None
        self.loaded = False
        self.intro_pending = False  # the next request of this process carries the [orbit] line
        self.auto_turn: dict | None = None  # a turn the harness began itself, not yet a card: what woke it, follow-ups so far

    def meta(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "cwd": self.cwd, "model": self.model, "mode": self.mode,
                "effort": self.effort, "harness": self.harness, "created": self.created, "closed": self.closed,
                "closed_at": self.closed_at, "named": self.named, "group": self.group, "favorite": self.favorite,
                "fork_from": self.fork_from, "last": self.last, "fleet": self.fleet, "hidden": self.hidden,
                **({"origins": dict(list(self.origins.items())[-ORIGINS_KEPT:])} if self.origins else {}),
                **({"client_ids": dict(list(self.client_ids.items())[-ORIGINS_KEPT:])} if self.client_ids else {}),
                **({"notes": self.notes[-NOTES_KEPT:]} if self.notes else {}),
                **({"interrupted": True} if self.interrupted else {}),
                **({"running": True} if (self.current_request() is not None if self.loaded else self.was_running) else {}),
                **({"fork_carry": self.fork_carry} if self.fork_carry else {}),
                **({"fork_harness": self.fork_harness} if self.fork_harness else {}),
                "pending": [c for c in self.cards if c["state"] == "queued"] or self.pending}

    def running_agents(self) -> int:
        """Its subagents at work, of any request: a background one runs on after its request ended (the session is
        not "working" then: a new request runs at once, nothing queues)."""
        return sum(1 for c in self.cards for st in c["steps"] if st.get("agent") and not st["done"])

    def public(self) -> dict[str, Any]:
        working = any(c["state"] in ACTIVE for c in self.cards)
        return {"id": self.id, "name": self.name, "cwd": self.cwd, "model": self.model, "mode": self.mode,
                "harness": self.harness, "state": "working" if working and self.state == "ready" else self.state,
                "error": self.error, "context": self.context, "model_seen": self.model_seen, "compactions": self.compactions,
                "effort": self.effort, "commands": self.commands, "steers": bool(getattr(DRIVERS.get(self.harness), "steers", False)),
                "attention": self.attention, "terminal": self.terminal,
                "queued": sum(1 for c in self.cards if c["state"] == "queued"), "closed": self.closed,
                "closed_at": self.closed_at, "named": self.named, "group": self.group, "favorite": self.favorite,
                "last": self.last, "created": self.created, "fleet": self.fleet, "agents": self.running_agents(),
                **({"fork_from": self.fork_from, "fork_harness": self.fork_harness} if self.fork_harness else {})}

    def next_id(self) -> int:
        """The next request's card id (note cards and turns of the agent's own sit between requests with ids like 4.001
        and 4.501: never counted)."""
        return max((c["id"] for c in self.cards if c.get("kind") not in ("note", "auto")), default=0) + 1

    def current(self) -> dict | None:
        """The card at work: a request, or a turn the agent began itself (kind "auto": requests queue behind it too)."""
        for c in self.cards:
            if c["state"] in ACTIVE:
                return c
        return None

    def current_request(self) -> dict | None:
        """The request at work (a turn of the agent's own is not one: cut off, there is nothing to continue)."""
        c = self.current()
        return c if c is not None and c.get("kind") != "auto" else None
