"""The orbit server: stdlib HTTP + Server-Sent Events, on 127.0.0.1 (and, when shared, beyond it, with the token).

    handler.py   guards and the routes (GET, POST)
    streams.py   SSE: live updates, shell output
    forward.py   requests about another machine's sessions, files and shells, done by that server
    sharing.py   listening for other machines
    support.py   uploads, the page log, fonts, folder completion, the server class
    run.py       make_server, main
"""

from orbit.server.handler import DEFAULT_PORT, WEB, Handler
from orbit.server.run import main, make_server, state_dir
from orbit.server.sharing import Sharing
from orbit.server.support import complete_dir, Server


__all__ = ["DEFAULT_PORT", "WEB", "Handler", "Server", "Sharing", "complete_dir", "main", "make_server", "state_dir"]
