"""The Harness Server: the agent processes, in a small daemon that outlives the Orbit Server (builddoc/harness-server.md).

    server.py    the daemon (`python -m orbit.harness_server --state DIR`): spawn, stdin, an output journal, signals
    client.py    the Orbit Server's side: a connection, HostedLauncher, HostedProcess (what the drivers use of Popen)

Standard library only, and nothing from orbit outside this package: a running Harness Server must not change when
the code on disk does, and it must start even when the rest of Orbit is broken.
"""

import os

PROTOCOL = 1  # major: a client and a server of different majors do not talk (the old one is asked to shut down)
VERSION = "1.0"


def harness_dir(state: str | os.PathLike) -> str:
    """<state>/harness: the socket, the lock, the pid, the log and the processes' journals."""
    return os.path.join(os.fspath(state), "harness")
