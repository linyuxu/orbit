"""Plugins: folders that add to Orbit without changing it (builddoc/plugins.md).

A plugin is a folder with a `plugin.toml`, in `<state dir>/plugins/<name>/` (usually a git clone) or in a folder
listed by `plugin_paths` in `<state dir>/config.toml` (for working on a plugin in place):

    [plugin]
    name = "acme"
    version = "0.1.0"
    requires_api = ">=1,<2"

    secrets = ["ACME_TOKEN"]      # set with `orbit plugin secret acme ACME_TOKEN`; given to its processes as env

    [harness.devin]               # a harness speaking the Orbit Harness Protocol
    label = "Devin"
    command = ["devin-orbit"]
    local_only = true

    [service.bridge]              # a process Orbit keeps running (services.py)
    command = ["bridge"]

    [cli]                         # what `orbit-agent p acme ARGS…` runs (for agents, and people)
    command = ["acme-cli"]

    [agent]                       # one sentence added to the [orbit] line agents get
    hint = "Acme: `orbit-agent p acme --help`."

    [people]                      # (API 1.2) who a name is: [cli] + args, stdin {"names": [...]} (people(), below)
    args = ["people"]

    [meeting]                     # (API 2.1) the Calendar's meetings: [cli] + args, stdin {"event": {...}}
    related = ["related"]         # what relates to a meeting (meeting_related(), below)
    prep = ["prep"]               # context added to a meeting's Prep (meeting_context(), below)

    [[ui.sidebar]]                # (API 1.1) a left-panel tab; [[ui.panel]] the side pane; [[ui.tab]] a tab kind
    id = "inbox"
    title = "Inbox"
    view = "list"                 # (API 2.0) drawn by Orbit: a list, a doc or a thread (no iframes: `path` is gone)
    source = "slack"
    shortcut = "Mod-Shift-S"      # (API 1.3) or "Ctrl-Alt-S"; Orbit's own or taken: ignored with an error

    [[ui.palette]]                # (API 1.3) a ⌘O tab of items it keeps in Orbit (services/plugin_items.py)
    id = "slack"
    title = "Slack"
    search = true                 # its service answers live searches ($ORBIT_SERVICE_PORT)

`config.toml` may also list `disabled = ["name", …]`. A plugin that cannot load (bad manifest, an API it does not fit,
a harness id taken) is skipped and reported (`/api/plugins`, `orbit plugin list`); Orbit starts anyway.
"""

from __future__ import annotations

import json
import os
import re
import secrets as _secrets
import shlex
import subprocess
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from orbit import API_VERSION

PLUGINS: list[dict[str, Any]] = []  # what was found, loaded or not: the Settings and `orbit plugin list` show it
_REGISTERED: dict[str, str] = {}  # harness id → the plugin that added it (to undo a load)
LOADED: dict[str, dict[str, Any]] = {}  # name → the manifest of a plugin that loaded (+ "folder")
TOKENS: dict[str, str] = {}  # a plugin's token (ORBIT_PLUGIN_TOKEN, kept across starts) → its name: who is calling
STATE: Path | None = None  # the state dir of the last load (secrets, data, logs)
ID_RE = r"[a-z0-9][a-z0-9_-]*"
ENV_RE = r"[A-Z_][A-Z0-9_]*"
CLI_TIMEOUT = 120
LAST_1X = (1, 3)  # the last 1.x API: a plugin that needs it still loads (2.0 only took out the iframe pages)


def config(state: Path) -> dict[str, Any]:
    try:
        with open(state / "config.toml", "rb") as f:
            d = tomllib.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def folders(state: Path, extra: list[str] | None = None) -> list[Path]:
    """Plugin folders: <state>/plugins/*, then plugin_paths from config.toml, then extra (tests); each once."""
    out: list[Path] = []
    base = state / "plugins"
    if base.is_dir():
        out += sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))
    for x in list(config(state).get("plugin_paths") or []) + list(extra or []):
        p = Path(str(x)).expanduser()
        if not p.is_absolute():
            p = state / p
        out.append(p)
    seen, uniq = set(), []
    for p in out:
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            uniq.append(p)
    return uniq


def fits(spec: str, version: tuple[int, int] = API_VERSION) -> bool:
    """Whether an API version fits a requirement like ">=1,<2" or ">=1.2" (major.minor; all parts must hold)."""
    ops = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b,
           "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
    for part in [x.strip() for x in str(spec or "").split(",") if x.strip()]:
        m = re.fullmatch(r"(>=|<=|==|!=|>|<)\s*(\d+)(?:\.(\d+))?", part)
        if not m:
            raise ValueError(f"cannot read requires_api {spec!r}")
        want = (int(m.group(2)), int(m.group(3) or 0))
        have = version if m.group(3) else (version[0], 0)  # ">=1" compares majors
        if not ops[m.group(1)](have, want):
            return False
    return True


def read(folder: Path) -> dict[str, Any]:
    """The manifest of one plugin folder (ValueError says what is wrong)."""
    f = folder / "plugin.toml"
    if not f.is_file():
        raise ValueError(f"no plugin.toml in {folder}")
    try:
        with open(f, "rb") as fh:
            d = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"plugin.toml: {e}") from None
    meta = d.get("plugin") or {}
    name = str(meta.get("name") or folder.name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise ValueError(f"plugin name {name!r}: lower-case letters, digits, - and _")
    sec = meta.get("secrets") or []
    if not isinstance(sec, list) or not all(isinstance(k, str) and re.fullmatch(ENV_RE, k) for k in sec):
        raise ValueError("secrets: a list of environment variable names (A_Z, digits, _)")
    for kind in ("harness", "service"):
        for xid, spec in (d.get(kind) or {}).items():
            if not re.fullmatch(ID_RE, xid):
                raise ValueError(f"{kind} id {xid!r}: lower-case letters, digits, - and _")
            if not isinstance(spec, dict) or not spec.get("command"):
                raise ValueError(f"[{kind}.{xid}] needs a command")
    for xid, spec in (d.get("harness") or {}).items():
        icon = str(spec.get("icon") or "").lstrip("/")
        if icon and (not icon.startswith("ui/") or ".." in icon.split("/")):
            raise ValueError(f"[harness.{xid}] icon: a file in the plugin's ui/ folder (ui/icon.svg)")
    for xid, spec in (d.get("service") or {}).items():
        if spec.get("restart", "on-failure") not in ("on-failure", "always", "never"):
            raise ValueError(f"[service.{xid}] restart: on-failure, always or never")
        if not all(isinstance(v, str) and re.fullmatch(ENV_RE, k) for k, v in (spec.get("env") or {}).items()):
            raise ValueError(f"[service.{xid}] env: NAME = \"text\" pairs")
    cli = d.get("cli") or {}
    if cli and not cli.get("command"):
        raise ValueError("[cli] needs a command")
    ui = d.get("ui") or {}
    warnings: list[str] = []  # what is ignored but does not stop the plugin (a bad shortcut, a bad view, an iframe page)
    services = d.get("service") or {}
    for xid, spec in services.items():  # (2.0) `files`: the prefix of its paths the svc: proxy may reach
        if "files" in spec:
            try:
                spec["files"] = files_prefix(spec["files"])
            except ValueError as e:
                warnings.append(f"[service.{xid}] files {spec['files']!r} ignored: {e}")
                spec.pop("files")
    schemes = []  # (2.0) link schemes its Markdown may use besides https, http and mailto (slack: …)
    for sc in meta.get("schemes", d.get("schemes")) or []:
        if isinstance(sc, str) and re.fullmatch(r"[a-z][a-z0-9+.-]{0,31}", sc) and sc not in UNSAFE_SCHEMES:
            schemes.append(sc)
        else:
            warnings.append(f"schemes: {sc!r} ignored: a scheme's name (slack, zoommtg), not one of "
                            + ", ".join(sorted(UNSAFE_SCHEMES)))
    panels, actions = [], []
    for x in ui.get("action") or []:  # [[ui.action]]: a menu item that runs the plugin's command
        if not (isinstance(x, dict) and re.fullmatch(ID_RE, str(x.get("id", ""))) and x.get("title")):
            raise ValueError("[[ui.action]] needs id and title")
        if x.get("where", "menu") not in ("menu", "answer"):
            raise ValueError(f"[[ui.action]] {x['id']}: where is menu or answer")
        if not cli.get("command"):
            raise ValueError(f"[[ui.action]] {x['id']}: runs the plugin's [cli] command, which is missing")
        actions.append({"id": x["id"], "title": str(x["title"])[:60], "where": x.get("where", "menu"),
                        "args": [str(a) for a in x.get("args") or []]})

    def menu_of(items, what):  # [[ui.tab.menu]], [[ui.message.menu]]: items that run [cli]; a bad one is ignored
        out = []
        for it in items or []:
            if not (isinstance(it, dict) and re.fullmatch(ID_RE, str(it.get("id", ""))) and it.get("title")):
                warnings.append(f"{what}: an item needs id and title: ignored")
            elif not cli.get("command"):
                warnings.append(f"{what} {it['id']}: runs the plugin's [cli] command, which is missing: ignored")
            elif it["id"] in {m["id"] for m in out}:
                warnings.append(f"{what} {it['id']}: a second one with that id: ignored")
            else:
                out.append({"id": it["id"], "title": str(it["title"])[:60], "args": [str(a) for a in it.get("args") or []]})
        return out

    def view_of(x, what, allowed):  # (2.0) `view`, checked; `path` (an iframe page of 1.x) is gone
        v, say = x.get("view"), " or ".join(f'"{a}"' for a in allowed)
        if v is None and x.get("path"):
            raise ValueError(f"{what}: path {x['path']!r}: iframe pages are gone in plugin API 2.0: use view = {say}")
        if v not in allowed:
            raise ValueError(f"{what}: view is {say}")
        if x.get("path"):
            warnings.append(f"{what}: path ignored: iframe pages are gone in plugin API 2.0 (its view = \"{v}\" is used)")
        return v

    def service_of(x, what):  # the service a view asks: the one named, else the plugin's only one, else none ([cli])
        sv = x.get("service")
        if sv in (None, ""):
            if len(services) > 1:
                warnings.append(f"{what}: the plugin has several services, say which one it asks: "
                                f"service = \"{next(iter(services))}\" (until then: [cli])")
            return next(iter(services)) if len(services) == 1 else None
        if isinstance(sv, str) and sv in services:
            return sv
        warnings.append(f"{what}: service {sv!r} ignored: no [service.{sv}]")
        return None

    def doc_of(x, what):  # the doc a panel or a left tab shows: `doc`, else its id
        doc = x.get("doc", x.get("id"))
        if not isinstance(doc, str) or not 0 < len(doc) <= 300:
            raise ValueError(f"{what}: doc: the id of a doc it pushes, 1–300 characters")
        return doc

    tabs = []
    for x in ui.get("tab") or []:  # [[ui.tab]]: a main-tab kind (1.1); 2.0: a thread or a doc, drawn by Orbit
        what = f"[[ui.tab]] {x.get('kind', '') if isinstance(x, dict) else ''}"
        try:
            if not (isinstance(x, dict) and re.fullmatch(ID_RE, str(x.get("kind", ""))) and x.get("title")):
                raise ValueError(f"{what}: needs kind and title")
            if x["kind"] in {t["kind"] for t in tabs}:
                raise ValueError(f"{what}: a second one with that kind")
            view = view_of(x, what, ("thread", "doc"))
            tabs.append({"kind": x["kind"], "title": str(x["title"])[:40], "icon": str(x.get("icon") or ""), "view": view,
                         "service": service_of(x, what), "menu": menu_of(x.get("menu"), f"[[ui.tab.menu]] of {x['kind']}")})
        except ValueError as e:
            warnings.append(f"{e}: ignored")
    msg = ui.get("message") if isinstance(ui.get("message"), dict) else {}
    messages = menu_of(msg.get("menu"), "[[ui.message.menu]]")  # (2.0) items of a thread message's context menu
    palette = []  # [[ui.palette]]: item sources, a tab each in ⌘O (API 1.3); a bad one is ignored with an error
    for x in ui.get("palette") or []:
        try:
            pal = palette_of(x, cli, tabs, services, warnings)
        except ValueError as e:
            warnings.append(f"{e}: ignored")
            continue
        if pal["id"] in {q["id"] for q in palette}:
            warnings.append(f"[[ui.palette]] {pal['id']}: a second one with that id: ignored")
            continue
        palette.append(pal)

    def source_of(x, what):  # a list view's item source: one of its [[ui.palette]] ids
        src = x.get("source")
        if src not in {p["id"] for p in palette}:
            raise ValueError(f"{what}: source: one of its [[ui.palette]] ids ({', '.join(p['id'] for p in palette) or 'none'})")
        return src

    sidebars = []
    for x in ui.get("sidebar") or []:  # [[ui.sidebar]]: a left-panel tab (1.1); 2.0: a list (an item source) or a doc
        what = f"[[ui.sidebar]] {x.get('id', '') if isinstance(x, dict) else ''}"
        try:
            if not (isinstance(x, dict) and re.fullmatch(ID_RE, str(x.get("id", ""))) and x.get("title")):
                raise ValueError(f"{what}: needs id and title")
            if x["id"] in {s["id"] for s in sidebars}:
                raise ValueError(f"{what}: a second one with that id")
            view = view_of(x, what, ("list", "doc"))
            sb = {"id": x["id"], "title": str(x["title"])[:24], "icon": str(x.get("icon") or ""), "view": view,
                  "order": int(x.get("order") or 50)}
            if view == "list":
                sb["source"] = source_of(x, what)
            else:
                sb["doc"], sb["service"] = doc_of(x, what), service_of(x, what)
            sb["shortcut"] = shortcut_of(x, what, warnings)
            sidebars.append(sb)
        except ValueError as e:
            warnings.append(f"{e}: ignored")
    for x in ui.get("panel") or []:  # [[ui.panel]]: in the side pane; 2.0: a doc, a list or a thread
        what = f"[[ui.panel]] {x.get('id', '') if isinstance(x, dict) else ''}"
        try:
            if not (isinstance(x, dict) and re.fullmatch(ID_RE, str(x.get("id", ""))) and x.get("title")):
                raise ValueError(f"{what}: needs id and title")
            if x["id"] in {p["id"] for p in panels}:
                raise ValueError(f"{what}: a second one with that id")
            view = view_of(x, what, ("doc", "list", "thread"))
            pn = {"id": x["id"], "title": str(x["title"])[:40], "view": view}
            if view == "list":
                pn["source"] = source_of(x, what)
            elif view == "doc":
                pn["doc"], pn["service"] = doc_of(x, what), service_of(x, what)
            else:
                kinds = [t["kind"] for t in tabs if t["view"] == "thread"]
                if x.get("kind") not in kinds or not isinstance(x.get("key"), str) or not 0 < len(x["key"]) <= 300:
                    raise ValueError(f"{what}: a thread panel names kind (one of its thread [[ui.tab]] kinds: "
                                     f"{', '.join(kinds) or 'none'}) and key")
                pn["kind"], pn["key"] = x["kind"], x["key"]
            pn["shortcut"] = shortcut_of(x, what, warnings)
            panels.append(pn)
        except ValueError as e:
            warnings.append(f"{e}: ignored")
    people = d.get("people")  # [people]: who a name is (API 1.2), answered by the plugin's [cli] command
    if people is not None:
        if not isinstance(people, dict):
            raise ValueError("[people]: a table, args = [\"people\"]")
        pargs = people.get("args", [])
        if not isinstance(pargs, list) or not all(isinstance(a, str) for a in pargs):
            raise ValueError("[people] args: a list of strings (the arguments of the plugin's [cli] command)")
        if not cli.get("command"):
            raise ValueError("[people] runs the plugin's [cli] command, which is missing")
        people = {"args": list(pargs)}
    meeting = d.get("meeting")  # [meeting]: hooks on the Calendar's meetings (API 2.1), answered by [cli]
    if meeting is not None:
        if not isinstance(meeting, dict) or not set(meeting) <= {"related", "prep"} or not meeting:
            raise ValueError("[meeting]: a table, related = [\"…\"] and/or prep = [\"…\"]")
        for k, v in meeting.items():
            if not isinstance(v, list) or not all(isinstance(a, str) for a in v):
                raise ValueError(f"[meeting] {k}: a list of strings (the arguments of the plugin's [cli] command)")
        if not cli.get("command"):
            raise ValueError("[meeting] runs the plugin's [cli] command, which is missing")
        meeting = {k: list(v) for k, v in meeting.items()}
    hint = " ".join(str((d.get("agent") or {}).get("hint") or "").split())
    if len(hint) > 300:
        raise ValueError("[agent] hint: one sentence (300 characters at most)")
    return {"name": name, "version": str(meta.get("version") or ""), "requires_api": str(meta.get("requires_api") or ""),
            "description": str(meta.get("description") or ""), "harness": d.get("harness") or {},
            "service": d.get("service") or {}, "secrets": list(sec), "cli": cli.get("command") or None, "hint": hint,
            "panels": panels, "actions": actions, "sidebars": sidebars, "tabs": tabs, "people": people,
            "meeting": meeting or {}, "palette": palette, "messages": messages, "schemes": schemes, "warnings": warnings}


# ---------------------------------------------------------------------- native views (API 2.0)
UNSAFE_SCHEMES = {"javascript", "vbscript", "data", "file", "blob", "about", "filesystem", "http", "https", "mailto",
                  "svc", "orbit", "ws", "wss"}  # (http, https, mailto are always allowed; svc and orbit are Orbit's)


def files_prefix(v: Any) -> str:
    """A service's `files` ("/files/"): the paths under it the svc: proxy may reach. ValueError says why not."""
    if not isinstance(v, str) or not re.fullmatch(r"/(?:[A-Za-z0-9._~-]+/)+", v) or len(v) > 200:
        raise ValueError("a path prefix like \"/files/\" (letters, digits, . _ ~ -, a / at each end)")
    if any(seg in (".", "..") for seg in v.split("/")):
        raise ValueError("no . or .. segments")
    if v.lower().startswith("/orbit/"):
        raise ValueError("/orbit/ is where Orbit asks the service, never a proxy")
    return v


def view_kind(name: str, kind: str, view: str = "") -> dict[str, Any]:
    """A plugin's [[ui.tab]] kind (of this view, when given); ValueError when it has none."""
    t = tab_kind(name, kind)
    if view and t.get("view") != view:
        raise ValueError(f"{name}'s tab kind {kind} is a {t.get('view')}, not a {view}")
    return t


# ---------------------------------------------------------------------- item sources (API 1.3)
def every_of(v: Any) -> float:
    """`every`: seconds, or "90s", "5m", "1h"; ValueError when it is neither."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(s|m|min|h)?\s*", v) if isinstance(v, str) else None
    if not m:
        raise ValueError(f"every {v!r}: seconds, or \"90s\", \"5m\", \"1h\"")
    return float(m.group(1)) * {"s": 1, None: 1, "m": 60, "min": 60, "h": 3600}[m.group(2)]


def palette_of(x: Any, cli: dict, tabs: list[dict], services: dict, warnings: list[str]) -> dict[str, Any]:
    """One [[ui.palette]] source, checked (plugin_items keeps its items). ValueError: the source is ignored; a bad
    optional part (a refresh too often) is said in warnings and mended."""
    from orbit.services import plugin_items
    if not isinstance(x, dict) or not re.fullmatch(ID_RE, str(x.get("id", ""))) or not x.get("title"):
        raise ValueError(f"[[ui.palette]] {x.get('id', '') if isinstance(x, dict) else ''}: needs id and title")
    what = f"[[ui.palette]] {x['id']}"
    if x["id"] == plugin_items.CONVERSATIONS:
        raise ValueError(f"{what}: `conversations` is the source a harness plugin pushes its conversations to "
                         "(Resume another session), not a ⌘O tab")
    out: dict[str, Any] = {"id": x["id"], "title": str(x["title"])[:40], "open": x.get("open") or "tab"}
    if out["open"] not in ("tab", "action", "url"):
        raise ValueError(f"{what}: open is tab, action or url")
    if x.get("icon"):
        out["icon"] = str(x["icon"])[:40]
    if out["open"] == "tab":  # the item's kind, else the source's, else the plugin's only [[ui.tab]] kind
        kinds = [t["kind"] for t in tabs]
        kind = str(x.get("kind") or (kinds[0] if len(kinds) == 1 else ""))
        if not kinds:
            raise ValueError(f"{what}: open = \"tab\" opens one of the plugin's [[ui.tab]] kinds; it has none")
        if kind and kind not in kinds:
            raise ValueError(f"{what}: kind {kind!r} is none of its [[ui.tab]] kinds ({', '.join(kinds)})")
        if kind:
            out["kind"] = kind
    if out["open"] == "action":
        if not cli.get("command"):
            raise ValueError(f"{what}: open = \"action\" runs the plugin's [cli] command, which is missing")
        args = x.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError(f"{what}: args: a list of strings")
        out["args"] = list(args)
    sv = x.get("search", False)  # true: its only service answers; with several, the one named
    out["search"] = None
    if sv is True:
        if len(services) == 1:
            out["search"] = next(iter(services))
        else:
            warnings.append(f"{what}: search ignored: " + ("the plugin has no [service.*] to answer it" if not services
                            else f"it has several services, say which: search = \"{next(iter(services))}\""))
    elif isinstance(sv, str) and sv:
        if sv in services:
            out["search"] = sv
        else:
            warnings.append(f"{what}: search ignored: no [service.{sv}]")
    elif sv not in (False, None, ""):
        warnings.append(f"{what}: search ignored: true, or a service id")
    r = x.get("refresh")
    out["refresh"] = None
    if r is not None:
        try:
            if not isinstance(r, dict):
                raise ValueError("refresh: {args = [...], every = \"5m\"}")
            args = r.get("args", [])
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise ValueError("refresh args: a list of strings")
            if not cli.get("command"):
                raise ValueError("refresh runs the plugin's [cli] command, which is missing")
            every = every_of(r.get("every", "5m"))
            if every < plugin_items.MIN_EVERY:
                warnings.append(f"{what}: refresh every {r.get('every')!r}: at least "
                                f"{plugin_items.MIN_EVERY:g} s, so every {plugin_items.MIN_EVERY:g} s")
                every = plugin_items.MIN_EVERY
            out["refresh"] = {"args": list(args), "every": every}
        except ValueError as e:
            warnings.append(f"{what}: {e}: refresh ignored")
    return out


# ---------------------------------------------------------------------- shortcuts (API 1.3)
# A [[ui.sidebar]] or [[ui.panel]] may say `shortcut = "Mod-Shift-S"` (Mod: ⌘ on macOS, Ctrl elsewhere) or
# "Ctrl-Alt-S": one of these two modifier sets and one letter or digit. One that is malformed, one of Orbit's (below)
# or one an earlier plugin took is ignored with an error; the plugin loads anyway.
#
# RESERVED_SHORTCUTS is the one list of Orbit's own keys in those two forms: the page's (web/src: SHORTCUTS in
# commands.js, keys.js, the finder, the left panel …), the app's menu (app/src/main.rs), the editors' (CodeMirror,
# web/cm/entry.js) and the system's or a browser's that the page never sees. tests/test_plugin_api_13.py fails when
# web/src or main.rs binds such a key that is missing here, so a new Orbit shortcut is checked against plugins too.
# (The page also never lets a plugin's key replace one of its own bindings.)
RESERVED_SHORTCUTS: dict[str, str] = {
    "Mod-Shift-C": "Orbit: today's meetings",
    "Mod-Shift-D": "Orbit: this week's note",
    "Mod-Shift-E": "Orbit: browse the open file's folders",
    "Mod-Shift-F": "Orbit: search history and notes",
    "Mod-Shift-G": "Orbit: previous match",
    "Mod-Shift-I": "Orbit: ± Review (and a browser's developer tools)",
    "Mod-Shift-O": "Orbit: main panel ⇄ side tab",
    "Mod-Shift-R": "Orbit: reload the UI (also the app's menu)",
    "Mod-Shift-T": "Orbit: reopen the last closed tab",
    "Mod-Shift-V": "Orbit: a voice call (GPT Live)",
    "Mod-Shift-A": "Orbit: mute a voice call",
    "Mod-Shift-Z": "Orbit: redo (also the app's menu)",
    "Mod-Shift-K": "the editor: delete the line",
    "Mod-Shift-U": "the editor: redo the selection",
    "Mod-Shift-J": "a browser's developer tools / downloads",
    "Mod-Shift-N": "a browser: new window",
    "Mod-Shift-W": "a browser: close the window",
    "Mod-Shift-Q": "macOS: log out",
    "Mod-Shift-3": "macOS: screenshot",
    "Mod-Shift-4": "macOS: screenshot",
    "Mod-Shift-5": "macOS: screenshot",
    "Mod-Shift-6": "macOS: screenshot",
    "Ctrl-Alt-H": "Orbit: move the focused panel's edge",
    "Ctrl-Alt-J": "Orbit: move the focused panel's edge",
    "Ctrl-Alt-K": "Orbit: move the focused panel's edge",
    "Ctrl-Alt-L": "Orbit: move the focused panel's edge",
}


def shortcut_form(v: Any) -> str:
    """'mod-shift-s' → 'Mod-Shift-S', 'Ctrl-Alt-7' → 'Ctrl-Alt-7'; '' if it is not one a plugin may use."""
    m = re.fullmatch(r"(mod-shift|ctrl-alt)-([a-z0-9])", v.strip(), re.I) if isinstance(v, str) else None
    if not m:
        return ""
    return ("Mod-Shift-" if m.group(1).lower() == "mod-shift" else "Ctrl-Alt-") + m.group(2).upper()


def shortcut_of(x: dict[str, Any], what: str, warnings: list[str]) -> str | None:
    """A declared shortcut in its canonical form, or None (absent, or malformed: said in warnings)."""
    if "shortcut" not in x or x["shortcut"] in (None, ""):
        return None
    sc = shortcut_form(x["shortcut"])
    if not sc:
        warnings.append(f"{what}: shortcut {x['shortcut']!r} ignored: \"Mod-Shift-<letter or digit>\" or "
                        f"\"Ctrl-Alt-<letter or digit>\"")
        return None
    if sc in RESERVED_SHORTCUTS:
        warnings.append(f"{what}: shortcut {sc} ignored: it is reserved ({RESERVED_SHORTCUTS[sc]})")
        return None
    return sc


def _claim_shortcuts(name: str, m: dict[str, Any], taken: dict[str, str]) -> None:
    """Give a loaded plugin its shortcuts unless an earlier plugin (or an earlier item of its own) has them."""
    for kind, items in (("ui.sidebar", m["sidebars"]), ("ui.panel", m["panels"])):
        for x in items:
            sc = x.get("shortcut")
            if not sc:
                continue
            what = f"[[{kind}]] {x['id']}"
            if sc in taken:
                m["warnings"].append(f"{what}: shortcut {sc} ignored: {taken[sc]} has it")
                x["shortcut"] = None
            else:
                taken[sc] = f"{name}'s [[{kind}]] {x['id']}"


def argv_of(cmd: list[str] | str, folder: Path) -> list[str]:
    """A declared command: a relative program is the plugin folder's (when it is there), else found on PATH."""
    argv = shlex.split(cmd) if isinstance(cmd, str) else [str(x) for x in cmd]
    if not argv:
        raise ValueError("an empty command")
    if not os.path.isabs(argv[0]) and (folder / argv[0]).exists():
        argv[0] = str((folder / argv[0]).resolve())
    return argv


# ---------------------------------------------------------------------- secrets, data, environment
def _secrets_file(name: str) -> Path:
    if STATE is None:
        raise ValueError("no plugins loaded")
    return STATE / "secrets" / f"{name}.json"


def get_secrets(name: str) -> dict[str, str]:
    try:
        d = json.loads(_secrets_file(name).read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, str)} if isinstance(d, dict) else {}


def set_secret(name: str, key: str, value: str | None) -> None:
    """Keep (or with None forget) one of a plugin's declared secrets: a 0600 file in a 0700 folder, never sent out."""
    m = LOADED.get(name)
    if not m:
        raise KeyError(name)
    if key not in m["secrets"]:
        raise ValueError(f"{name} declares no secret {key} (it has: {', '.join(m['secrets']) or 'none'})")
    f = _secrets_file(name)
    f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(f.parent, 0o700)
    d = get_secrets(name)
    if value is None:
        d.pop(key, None)
    else:
        d[key] = value
    tmp = f.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(d, fh)
    os.replace(tmp, f)


def data_dir(name: str) -> Path:
    """The plugin's own folder for what it keeps (<state>/plugin-data/<name>)."""
    if STATE is None:
        raise ValueError("no plugins loaded")
    d = STATE / "plugin-data" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def env(name: str) -> dict[str, str]:
    """What a plugin's processes (harnesses, services, its cli) get besides Orbit's environment."""
    port = os.environ.get("ORBIT_PORT", "8770")
    token = next((t for t, n in TOKENS.items() if n == name), "")
    return {**get_secrets(name), "ORBIT_PLUGIN": name, "ORBIT_PLUGIN_TOKEN": token, "ORBIT_PORT": port,
            "ORBIT_URL": f"http://127.0.0.1:{port}", "ORBIT_PLUGIN_DATA": str(data_dir(name))}


def hints() -> list[str]:
    """The plugins' sentences for the [orbit] line."""
    return [f"{n}: {m['hint']}" for n, m in sorted(LOADED.items()) if m.get("hint")]


def run_cli(name: str, args: list[str], stdin: str = "", session: str = "", timeout: float = CLI_TIMEOUT) -> dict[str, Any]:
    """`orbit-agent p NAME ARGS…`: the plugin's [cli] command, in its folder, with its environment and secrets."""
    m = LOADED.get(name)
    if not m:
        raise KeyError(name)
    if not m.get("cli"):
        raise ValueError(f"{name} has no [cli] command")
    argv = argv_of(m["cli"], m["folder"]) + [str(a) for a in args]
    e = {**os.environ, **env(name), **({"ORBIT_SESSION": session} if session else {})}
    try:
        r = subprocess.run(argv, cwd=m["folder"], input=stdin, capture_output=True, text=True, timeout=timeout, env=e)
    except subprocess.TimeoutExpired:
        return {"code": 124, "stdout": "", "stderr": f"{name}: no answer within {timeout:g} s"}
    except OSError as ex:
        return {"code": 127, "stdout": "", "stderr": f"{name}: {ex}"}
    return {"code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}


# ---------------------------------------------------------------------- what plugins add to the page
STATUS: dict[str, dict[str, dict[str, Any]]] = {}  # plugin → status-bar items it set (until it clears them or Orbit stops)


def set_status(name: str, item: dict[str, Any]) -> list[dict[str, Any]]:
    """A status-bar item: {"id", "text" ("" removes it), "title"?, "level"? (info · warn · error), "url"? or "panel"?}."""
    if name not in LOADED:
        raise KeyError(name)
    sid = str(item.get("id") or "main")
    if not re.fullmatch(ID_RE, sid):
        raise ValueError("status id: lower-case letters, digits, - and _")
    mine = STATUS.setdefault(name, {})
    text = " ".join(str(item.get("text") or "").split())[:60]
    if not text:
        mine.pop(sid, None)
    else:
        url = str(item.get("url") or "")
        panel = str(item.get("panel") or "")
        if panel and panel not in {p["id"] for p in LOADED[name]["panels"]}:
            raise ValueError(f"{name} declares no panel {panel}")
        level = item.get("level") if item.get("level") in ("info", "warn", "error") else "info"
        mine[sid] = {"plugin": name, "id": sid, "text": text, "title": str(item.get("title") or "")[:300], "level": level,
                     **({"url": url} if url.startswith(("https://", "http://")) else {}), **({"panel": panel} if panel else {})}
    return statuses()


def statuses() -> list[dict[str, Any]]:
    return [x for n in sorted(STATUS) for x in STATUS[n].values()]


BADGES: dict[str, dict[str, dict[str, Any]]] = {}  # plugin → sidebar id → {"count", "level"}


def set_badge(name: str, sid: str, count: int, level: str = "info") -> dict[str, Any]:
    """A count on one of the plugin's left-panel tabs (0 hides it)."""
    m = LOADED.get(name)
    if not m:
        raise KeyError(name)
    if sid not in {s["id"] for s in m["sidebars"]}:
        raise ValueError(f"{name} declares no sidebar {sid}")
    mine = BADGES.setdefault(name, {})
    if count > 0:
        mine[sid] = {"count": int(count), "level": level if level in ("info", "warn", "error") else "info"}
    else:
        mine.pop(sid, None)
    return badges()


def badges() -> dict[str, dict[str, Any]]:
    return {f"{n}:{i}": b for n in sorted(BADGES) for i, b in BADGES[n].items()}


def tab_kind(name: str, kind: str) -> dict[str, Any]:
    m = LOADED.get(name)
    t = next((t for t in (m or {}).get("tabs") or [] if t["kind"] == kind), None)
    if not t:
        raise ValueError(f"{name} declares no tab kind {kind}")
    return t


def ui() -> dict[str, Any]:
    """Everything the plugins add to the page: status items, panels, actions; left-panel tabs and main-tab kinds (2.0:
    each a view Orbit draws), message menus and link schemes. A panel or left tab that has a key carries its
    `shortcut` (API 1.3: "Mod-Shift-S" / "Ctrl-Alt-S")."""
    panels, actions, sidebars, tabs, palette, messages, schemes = [], [], [], [], [], [], {}
    for n, m in sorted(LOADED.items()):
        key = lambda x: {"shortcut": x["shortcut"]} if x.get("shortcut") else {}  # (1.3: only when it has one)
        what = lambda x: {k: x[k] for k in ("doc", "source", "kind", "key") if x.get(k) is not None}
        panels += [{"plugin": n, "id": p["id"], "title": p["title"], "view": p["view"], **what(p), **key(p)}
                   for p in m["panels"]]
        actions += [{"plugin": n, **a} for a in m["actions"]]
        sidebars += [{"plugin": n, "id": s["id"], "title": s["title"], "icon": s["icon"], "order": s["order"],
                      "view": s["view"], **what(s), **key(s)} for s in m["sidebars"]]
        tabs += [{"plugin": n, "kind": t["kind"], "title": t["title"], "icon": t["icon"], "view": t["view"],
                  "menu": t["menu"]} for t in m["tabs"]]
        palette += [{"plugin": n, "id": x["id"], "title": x["title"], "open": x["open"], "search": bool(x["search"]),
                     **{k: x[k] for k in ("kind", "icon", "args") if k in x}} for x in m.get("palette") or []]
        messages += [{"plugin": n, **x} for x in m.get("messages") or []]
        if m.get("schemes"):
            schemes[n] = list(m["schemes"])
    sidebars.sort(key=lambda s: (s["order"], s["plugin"], s["id"]))
    return {"status": statuses(), "panels": panels, "actions": actions, "sidebars": sidebars, "tabs": tabs,
            "badges": badges(), "palette": palette, "messages": messages, "schemes": schemes}


ICON_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp"}


def harness_icon(name: str, rel: str) -> Path | None:
    """A harness's declared `icon` (a picture in the plugin's ui/ folder), or None: the only file of a plugin Orbit
    serves (2.0: plugin pages are gone)."""
    m = LOADED.get(name)
    if not m or Path(rel).suffix.lower() not in ICON_TYPES:
        return None
    if rel not in {str(h.get("icon") or "").lstrip("/") for h in m["harness"].values()}:
        return None
    base = (m["folder"] / "ui").resolve()
    f = (m["folder"] / rel).resolve()
    return f if f.is_file() and base in f.parents else None


def public() -> list[dict[str, Any]]:
    """/api/plugins: every plugin found, with its services' state and which secrets are set (never their values)."""
    from orbit.services import plugin_items, plugin_services
    out = []
    for e in PLUGINS:
        m = LOADED.get(e["name"]) if e["ok"] else None
        x = {k: v for k, v in e.items() if k not in ("described", "people_check")}
        if m:
            have = get_secrets(e["name"])
            x.update(secrets=[{"key": k, "set": k in have} for k in m["secrets"]], cli=bool(m.get("cli")),
                     hint=m.get("hint", ""), services=plugin_services.status(e["name"]), people=bool(m.get("people")),
                     meeting=sorted(m.get("meeting") or {}))
            if m.get("people") and e["name"] in PEOPLE_ERRORS:
                x["people_error"] = PEOPLE_ERRORS[e["name"]]["error"]
            x["palette"] = [p["id"] for p in m.get("palette") or []]
            pe = plugin_items.errors(e["name"])
            if pe:  # (1.3) a refresh command's last failure, per source, until it runs without one
                x["palette_errors"] = pe
        out.append(x)
    return out


def unload() -> None:
    """Forget every plugin (tests; a reload)."""
    from orbit.desk.session import DRIVERS
    from orbit.services import plugin_items, plugin_services
    from orbit.services import plugin_views
    plugin_services.forget()
    plugin_items.unload()  # (what changed is written first)
    plugin_views.unload()
    for hid in list(_REGISTERED):
        DRIVERS.pop(hid, None)
    _REGISTERED.clear()
    PLUGINS.clear()
    LOADED.clear()
    TOKENS.clear()
    STATUS.clear()
    BADGES.clear()
    _people_forget()


def load(state: Path, extra: list[str] | None = None) -> list[dict[str, Any]]:
    """Find and load the plugins: their harnesses join DRIVERS, their services are registered (started by
    plugin_services.start_all once the server listens). Returns (and keeps in PLUGINS) one entry per folder."""
    global STATE
    from orbit.desk.session import DRIVERS
    from orbit.drivers.protocol import protocol_driver
    from orbit.services import plugin_services
    unload()
    STATE = Path(state)
    kept = _kept_tokens()
    disabled = set(config(state).get("disabled") or [])
    taken: dict[str, str] = {}  # shortcut → who has it (the first plugin to claim one wins)
    for folder in folders(state, extra):
        entry: dict[str, Any] = {"name": folder.name, "path": str(folder), "version": "", "ok": False, "error": "",
                                 "enabled": True, "harnesses": [], "services": [], "people": False, "meeting": []}
        try:
            m = read(folder)
            entry.update(name=m["name"], version=m["version"], description=m["description"])
            if m["name"] in disabled:
                entry.update(enabled=False, error="disabled in config.toml")
                PLUGINS.append(entry)
                continue
            if any(p["name"] == m["name"] and p["ok"] for p in PLUGINS):
                raise ValueError(f"another plugin is already called {m['name']}")
            if m["requires_api"] and not fits(m["requires_api"]):
                if not fits(m["requires_api"], LAST_1X):
                    raise ValueError(f"needs Orbit's plugin API {m['requires_api']}; this Orbit has "
                                     f"{API_VERSION[0]}.{API_VERSION[1]}")
                # (2.0 took out only the iframe pages: a plugin written for 1.x loads; a page it declares is ignored with
                # its own error, read())
            made = {}
            for hid, spec in m["harness"].items():
                if hid in DRIVERS or hid in made:
                    raise ValueError(f"harness {hid!r} exists already")
                made[hid] = protocol_driver(hid, spec, folder, plugin=m["name"])
            for sid, spec in m["service"].items():
                argv_of(spec["command"], folder)  # (an empty command fails here)
            for hid, cls in made.items():
                DRIVERS[hid] = cls
                _REGISTERED[hid] = m["name"]
            LOADED[m["name"]] = {**m, "folder": folder}
            _claim_shortcuts(m["name"], LOADED[m["name"]], taken)
            TOKENS[kept.get(m["name"]) or "orbitp_" + _secrets.token_urlsafe(24)] = m["name"]
            for sid, spec in m["service"].items():
                plugin_services.register(m["name"], sid, spec, folder)
            entry.update(ok=True, harnesses=sorted(made), services=sorted(m["service"]), people=bool(m["people"]),
                         meeting=sorted(m["meeting"]))
            if m["warnings"]:  # (ignored, reported where a plugin's problems show; the plugin is loaded)
                entry.update(error="; ".join(m["warnings"]), warnings=list(m["warnings"]))
        except (ValueError, OSError) as e:
            entry["error"] = str(e)
        PLUGINS.append(entry)
    _keep_tokens({**kept, **{n: t for t, n in TOKENS.items()}})
    from orbit.services import plugin_items
    plugin_items.load()  # the item sources, as they were (API 1.3)
    from orbit.services import plugin_views
    plugin_views.load()  # the threads and docs they keep (API 2.0)
    return PLUGINS


def _kept_tokens() -> dict[str, str]:
    """<state>/plugin-tokens.json {plugin: token}: a plugin keeps its ORBIT_PLUGIN_TOKEN across server starts, so a
    harness process handed off at a restart is still recognised when it calls back."""
    try:
        d = json.loads((STATE / "plugin-tokens.json").read_text()) if STATE is not None else {}
    except (OSError, ValueError):
        return {}
    return {n: t for n, t in d.items() if isinstance(t, str) and t.startswith("orbitp_")} if isinstance(d, dict) else {}


def _keep_tokens(tokens: dict[str, str]) -> None:
    if STATE is None or not tokens:
        return
    f = STATE / "plugin-tokens.json"
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(tokens, fh)
        os.chmod(tmp, 0o600)  # (an older file's mode does not carry over: os.open's mode is for a new one)
        os.replace(tmp, f)
    except OSError as e:
        print(f"orbit: plugin tokens not kept ({e}): they change at the next start", flush=True)


def check(state: Path, extra: list[str] | None = None) -> list[dict[str, Any]]:
    """load(), and ask every plugin harness to describe itself: what `orbit plugin check` reports."""
    from orbit.desk.session import DRIVERS
    out = load(state, extra)
    for e in out:
        e["described"] = {}
        for hid in e.get("harnesses") or []:
            cls = DRIVERS[hid]
            d = cls.describe()
            if not d:
                e["ok"] = False
                e["error"] = (e["error"] + "; " if e["error"] else "") + f"{hid}: `--describe` gave nothing (is the command right?)"
            elif d.get("protocol") not in (None, 1):
                e["ok"] = False
                e["error"] = (e["error"] + "; " if e["error"] else "") + f"{hid}: speaks protocol {d.get('protocol')}, Orbit 1"
            e["described"][hid] = {"label": cls.label, "models": [m["value"] for m in cls.models()],
                                   "steer": bool(cls.steers), "local_only": bool(cls.local_only)}
        if e.get("people") and e["name"] in LOADED:  # [people]: ask it about one name (nothing is cached)
            probe = "Ada Lovelace"
            got, err = _ask(e["name"], [probe], log=False)
            if err:
                e["ok"] = False
                e["error"] = (e["error"] + "; " if e["error"] else "") + f"[people]: {err}"
                e["people_check"] = f"asked about {probe!r}: {err}"
            else:
                e["people_check"] = (f"asked about {probe!r}: " + (", ".join(
                    f"{k} {'a data: image' if k == 'avatar' and v.startswith('data:') else repr(v)}"
                    for k, v in got[probe].items()) if probe in got else "not known (fine)"))
    return out


# ---------------------------------------------------------------------- people (API 1.2): who a name is
# A plugin with [people] answers, through its [cli] command, which avatar (and handle) a name has: the Calendar shows
# them for a meeting's people. Orbit asks only for names it shows, at most PEOPLE_BATCH per call, the names on stdin;
# every answer (a "not known" too) is cached in memory and in <state>/cache/people.json.
PEOPLE_TIMEOUT = 8.0  # s, per call
PEOPLE_BATCH = 50  # names per call
PEOPLE_MAX = 200  # names per request (/api/people)
PEOPLE_TTL = 24 * 3600  # a known name
PEOPLE_UNKNOWN_TTL = 6 * 3600  # a name no provider knows (a new hire shows up the same day)
PEOPLE_FAIL_TTL = 600  # a name a provider failed on (crashed, timed out, printed junk)
PEOPLE_CACHE_MAX = 5000  # names kept (the soonest to expire go first)
AVATAR_MAX = 200_000  # characters of a data: URI
HANDLE_MAX = 64
PEOPLE_ERRORS: dict[str, dict[str, Any]] = {}  # plugin → its last failure {"error", "at"} (until it answers again)
_people: dict[str, dict[str, Any]] = {}  # name → {"v": {"avatar"?, "handle"?} or None, "until": epoch s}
_people_meta: dict[str, Any] = {"loaded": False, "providers": None}
_people_lock = threading.Lock()
_people_busy: dict[str, threading.Event] = {}  # names being asked for right now (a second caller waits)


def _now() -> float:
    return time.time()


def providers() -> list[str]:
    """The loaded plugins that can say who a name is, in load order (the first that knows a name wins)."""
    return [n for n, m in LOADED.items() if m.get("people")]


def person_name(x: Any) -> str:
    """A name as asked about: its spaces collapsed; '' if it is not a usable name."""
    n = " ".join(str(x).split()) if isinstance(x, str) else ""
    return n if len(n) <= 200 else ""


def _avatar(v: Any) -> str:
    """An https: URL (no spaces, quotes or <>) or a data:image/… URI (no control characters), else ''."""
    v = v.strip() if isinstance(v, str) else ""
    if v.startswith("https://"):
        u = urlparse(v)
        ok = len(v) <= 4096 and u.netloc and not any(c.isspace() or c in '"\'<>' for c in v)
        return v if ok else ""
    if (re.match(r"data:image/[a-z0-9.+-]+(;[a-z0-9=.+-]+)*,", v, re.I) and len(v) <= AVATAR_MAX
            and all(c.isprintable() for c in v)):
        return v
    return ""


def person_value(v: Any) -> dict[str, str] | None:
    """One answer, checked: {"avatar"?: https URL or data:image URI, "handle"?: a short string}, or None."""
    if not isinstance(v, dict):
        return None
    out = {}
    a = _avatar(v.get("avatar"))
    if a:
        out["avatar"] = a
    h = v.get("handle")
    if isinstance(h, str):
        h = "".join(c for c in h.strip() if c.isprintable())
        if h and len(h) <= HANDLE_MAX:
            out["handle"] = h
    return out or None


def _people_file() -> Path | None:
    return STATE / "cache" / "people.json" if STATE is not None else None


def _people_forget() -> None:
    with _people_lock:
        _people.clear()
        _people_meta.update(loaded=False, providers=None)
        PEOPLE_ERRORS.clear()


def _people_load() -> None:
    """(under the lock) The cache from disk, once per load; dropped when the providers changed."""
    want = providers()
    if _people_meta["loaded"] and _people_meta["providers"] == want:
        return
    _people.clear()
    _people_meta.update(loaded=True, providers=want)
    f = _people_file()
    try:
        d = json.loads(f.read_text()) if f else {}
    except (OSError, ValueError):
        return
    if not isinstance(d, dict) or d.get("providers") != want or not isinstance(d.get("people"), dict):
        return
    now = _now()
    for n, x in d["people"].items():
        if isinstance(x, dict) and isinstance(x.get("until"), (int, float)) and x["until"] > now:
            _people[n] = {"v": person_value(x.get("v")), "until": float(x["until"])}


def _people_save() -> None:
    """(under the lock) The cache to disk: what has not expired, at most PEOPLE_CACHE_MAX names."""
    f = _people_file()
    if f is None:
        return
    now = _now()
    keep = sorted(((n, x) for n, x in _people.items() if x["until"] > now), key=lambda nx: -nx[1]["until"])
    _people.clear()
    _people.update(keep[:PEOPLE_CACHE_MAX])
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"providers": _people_meta["providers"], "people": _people}, ensure_ascii=False))
        os.replace(tmp, f)
    except OSError:
        pass


def _people_log(name: str, text: str) -> None:
    """A provider's failure: kept for /api/plugins (people_error) and in <state>/logs/plugins/<name>.people.log."""
    PEOPLE_ERRORS[name] = {"error": text, "at": _now()}
    if STATE is None:
        return
    try:
        f = STATE / "logs" / "plugins" / f"{name}.people.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists() and f.stat().st_size > 1_000_000:
            os.replace(f, f.with_suffix(".log.1"))
        with open(f, "a") as fh:
            fh.write(f"--- orbit: {time.strftime('%Y-%m-%d %H:%M:%S')} [people] {text}\n")
    except OSError:
        pass


def _ask(name: str, names: list[str], log: bool = True) -> tuple[dict[str, dict[str, str]], str]:
    """One call to one provider: (its checked answers for these names, '' or what went wrong). Never raises."""
    try:
        r = run_cli(name, LOADED[name]["people"]["args"], json.dumps({"names": names}),
                    timeout=PEOPLE_TIMEOUT)
    except (KeyError, ValueError, OSError) as e:
        r = {"code": 127, "stdout": "", "stderr": str(e)}
    err = ""
    if r["code"] != 0:
        tail = (r["stderr"] or r["stdout"]).strip().splitlines()[-1:] or [""]
        err = f"exited {r['code']}" + (f": {tail[0][:300]}" if tail[0] else "")
    else:
        try:
            d = json.loads(r["stdout"] or "")
        except ValueError:
            d = None
        if not isinstance(d, dict):
            err = "its output is not a JSON object {name: {avatar, handle}}"
    if err:
        if log:
            _people_log(name, err)
        return {}, err
    if log:
        PEOPLE_ERRORS.pop(name, None)
    asked = set(names)
    return {n: v for n, v in ((k, person_value(x)) for k, x in d.items()) if n in asked and v}, ""


def _fetch(names: list[str]) -> dict[str, dict[str, Any]]:
    """Ask the providers about one batch: each in load order, about the names none before it knew."""
    found: dict[str, dict[str, str]] = {}
    failed: set[str] = set()  # names some provider before the one that knew it failed on: kept only briefly
    for p in providers():
        left = [n for n in names if n not in found]
        if not left:
            break
        got, err = _ask(p, left)
        if err:
            failed.update(left)
        found.update(got)
    now = _now()
    return {n: {"v": found.get(n),
                "until": now + (PEOPLE_FAIL_TTL if n in failed else PEOPLE_TTL if n in found else PEOPLE_UNKNOWN_TTL)}
            for n in names}


def people(names: list[Any]) -> dict[str, dict[str, str]]:
    """Who these names are, as far as the [people] providers know: {name: {"avatar"?, "handle"?}}, known names only.
    Each name is asked about once (cached for a day, a "not known" for 6 h, a failure for 10 min); a provider that
    fails never breaks the caller. At most PEOPLE_MAX names (the rest are left out)."""
    if not providers():
        return {}
    want = list(dict.fromkeys(n for n in map(person_name, names) if n))[:PEOPLE_MAX]
    mine: list[str] = []
    theirs: dict[str, threading.Event] = {}
    with _people_lock:
        _people_load()
        now = _now()
        for n in want:
            x = _people.get(n)
            if x and x["until"] > now:
                continue
            if n in _people_busy:
                theirs[n] = _people_busy[n]
            else:
                _people_busy[n] = threading.Event()
                mine.append(n)
    if mine:
        results: list[dict[str, dict[str, Any]]] = []
        try:
            batches = [mine[i:i + PEOPLE_BATCH] for i in range(0, len(mine), PEOPLE_BATCH)]
            with ThreadPoolExecutor(max_workers=min(4, len(batches))) as pool:
                results = list(pool.map(_fetch, batches))
        except Exception:  # (never the caller's problem: these names count as failed, briefly)
            results = [{n: {"v": None, "until": _now() + PEOPLE_FAIL_TTL} for n in mine}]
        finally:
            with _people_lock:
                for r in results:
                    _people.update(r)
                for n in mine:
                    _people_busy.pop(n).set()
                _people_save()
    for ev in theirs.values():
        ev.wait(PEOPLE_TIMEOUT * (len(LOADED) + 1) * 4)
    with _people_lock:
        return {n: dict(x["v"]) for n in want if (x := _people.get(n)) and x["v"]}


# ---------------------------------------------------------------------- meetings (API 2.1): hooks on the Calendar
# A plugin with [meeting] is asked, through its [cli] command (the event as JSON on stdin), what relates to a meeting
# (`related`: shown when the meeting is opened) and what its Prep should know (`prep`: added to the helper's prompt).
# Every such plugin is asked at once, MEETING_TIMEOUT s at most; one that fails is left out and logged.
MEETING_TIMEOUT = 8
MEETING_ITEMS = 8  # related items per plugin
MEETING_CONTEXT_MAX = 6000  # characters of context per plugin
EVENT_KEYS = ("id", "title", "start", "end", "all_day", "organizer", "people", "guests", "attendees", "location",
              "calendar", "notes", "link", "recurring")


def meeting_event(e: Any) -> dict[str, Any]:
    """What a plugin is told of a meeting: its plain fields (the invitation's text cut at 4000 characters)."""
    if not isinstance(e, dict):
        return {}
    out = {k: e[k] for k in EVENT_KEYS if k in e and isinstance(e[k], (str, int, float, bool, list))}
    if isinstance(out.get("notes"), str):
        out["notes"] = out["notes"][:4000]
    return out


def _hook_log(name: str, hook: str, text: str) -> None:
    if STATE is None:
        return
    try:
        f = STATE / "logs" / "plugins" / f"{name}.meeting.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists() and f.stat().st_size > 1_000_000:
            os.replace(f, f.with_suffix(".log.1"))
        with open(f, "a") as fh:
            fh.write(f"--- orbit: {time.strftime('%Y-%m-%d %H:%M:%S')} [meeting] {hook}: {text}\n")
    except OSError:
        pass


def _meeting_ask(name: str, hook: str, event: dict) -> dict | None:
    """One plugin's answer to one hook: its JSON object, or None (logged) when it failed."""
    try:
        r = run_cli(name, LOADED[name]["meeting"][hook], json.dumps({"event": event}), timeout=MEETING_TIMEOUT)
    except (KeyError, ValueError, OSError) as e:
        r = {"code": 127, "stdout": "", "stderr": str(e)}
    if r["code"] != 0:
        tail = (r["stderr"] or r["stdout"]).strip().splitlines()[-1:] or [""]
        _hook_log(name, hook, f"exited {r['code']}" + (f": {tail[0][:300]}" if tail[0] else ""))
        return None
    try:
        d = json.loads(r["stdout"] or "")
    except ValueError:
        d = None
    if not isinstance(d, dict):
        _hook_log(name, hook, "its output is not a JSON object")
        return None
    return d


def _meeting_all(hook: str, event: Any) -> list[tuple[str, dict]]:
    names = [n for n, m in LOADED.items() if hook in (m.get("meeting") or {})]
    if not names:
        return []
    ev = meeting_event(event)
    with ThreadPoolExecutor(max_workers=min(4, len(names))) as pool:
        got = list(pool.map(lambda n: _meeting_ask(n, hook, ev), names))
    return [(n, d) for n, d in zip(names, got) if d is not None]


def _text(v: Any, n: int) -> str:
    return " ".join(v.split())[:n] if isinstance(v, str) else ""


def meeting_related(event: Any) -> list[dict[str, str]]:
    """What the plugins say relates to a meeting: [{"plugin", "title", "detail"?, "url"? (http/https), "path"?
    (absolute)}], MEETING_ITEMS per plugin at most; an item without a title is left out."""
    out = []
    for name, d in _meeting_all("related", event):
        items = d.get("items") if isinstance(d.get("items"), list) else []
        for x in items[:MEETING_ITEMS]:
            if not isinstance(x, dict) or not _text(x.get("title"), 200):
                continue
            it = {"plugin": name, "title": _text(x["title"], 200)}
            if _text(x.get("detail"), 300):
                it["detail"] = _text(x["detail"], 300)
            u = x.get("url")
            if isinstance(u, str) and re.match(r"https?://[^\s\"'<>]+$", u) and len(u) <= 4096:
                it["url"] = u
            pth = x.get("path")
            if isinstance(pth, str) and pth.startswith("/") and len(pth) <= 1000 and "\n" not in pth:
                it["path"] = pth
            out.append(it)
    return out


def meeting_context(event: Any) -> list[dict[str, str]]:
    """What the plugins add to a meeting's Prep: [{"plugin", "text"}] (Markdown, MEETING_CONTEXT_MAX characters each)."""
    out = []
    for name, d in _meeting_all("prep", event):
        t = d.get("context")
        if isinstance(t, str) and t.strip():
            out.append({"plugin": name, "text": t.strip()[:MEETING_CONTEXT_MAX]})
    return out
