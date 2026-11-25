"""Orbit's own folder (state, data, logs): $ORBIT_HOME, else ~/.orbit.

Earlier versions used another folder name (legacy_home(), spelled apart so renames skip it). It is moved only when
asked (`orbit migrate-home`), never on its own: a second instance (another $ORBIT_HOME) must not touch the first one's
folders. The move leaves a link at the old place so paths written into old conversations still open.

Also: which port the server of a state folder listens on (`server_port`), for `orbit-agent` and `orbit` started
outside the server's environment (another terminal, tmux): its <state>/server.json, believed while it holds
<state>/server.lock.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PORT = 8770


def home() -> Path:
    v = os.environ.get("ORBIT_HOME", "").strip()
    return Path(v).expanduser() if v else Path.home() / ".orbit"


def legacy_home() -> Path:
    return Path.home() / (".a" + "ide")


def needs_migration() -> bool:
    """The old folder is a folder (not the link a move leaves) and ~/.orbit is not there yet (ORBIT_HOME unset)."""
    old = legacy_home()
    return not os.environ.get("ORBIT_HOME") and not home().exists() and old.is_dir() and not old.is_symlink()


def migrate() -> str:
    """`orbit migrate-home`: the old folder → ~/.orbit, a link left behind. What happened, as a sentence."""
    if os.environ.get("ORBIT_HOME"):
        return f"ORBIT_HOME is set ({home()}): nothing to move"
    new, old = home(), legacy_home()
    if new.exists():
        return f"{new} is there already: nothing to move"
    if not old.is_dir() or old.is_symlink():
        return f"no {old} folder: nothing to move"
    old.rename(new)
    old.symlink_to(new)
    return f"moved {old} to {new} (a link is left at {old})"


def lock_held(state: Path) -> bool:
    """Whether an Orbit Server holds <state>/server.lock (a non-blocking probe; released at once if not)."""
    import fcntl
    try:
        fd = os.open(state / "server.lock", os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)  # (closing drops the lock if the probe took it)
    return False


def state_dir() -> Path:
    """The state folder as the server picks it without --state-dir: $ORBIT_STATE_DIR, else home()."""
    return Path(os.environ.get("ORBIT_STATE_DIR") or home()).expanduser()


def server_port(state: Path | None = None) -> int:
    """The port to reach this machine's Orbit Server on: $ORBIT_PORT; else the port in <state>/server.json while a
    server holds <state>/server.lock; else 8770."""
    v = os.environ.get("ORBIT_PORT", "").strip()
    if v:
        return int(v)
    state = state or state_dir()
    try:
        port = json.loads((state / "server.json").read_text()).get("port")
        if isinstance(port, int) and 0 < port < 65536 and lock_held(state):
            return port
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_PORT
