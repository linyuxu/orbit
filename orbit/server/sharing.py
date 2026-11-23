"""Listening beyond 127.0.0.1 for other machines (Settings → Machines, `orbit serve --remote`)."""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

from orbit.machines import remote
from orbit.server.support import Server


class Sharing:
    """Listening beyond 127.0.0.1 (other machines, with the token): fixed by `--remote` / `--bind`, or switched at run
    time from Settings → Machines (remembered as the serve_remote setting and applied at the next start)."""

    def __init__(self, port: int, handler):
        self.port, self.handler = port, handler
        self.servers: dict[str, ThreadingHTTPServer] = {}
        self.lock = threading.Lock()

    @staticmethod
    def default_addresses() -> list[str]:
        """Tailscale's (100.64/10) when there is one, else the LAN's."""
        found = remote.addresses()
        ts = [a for a in found if a.startswith("100.")]
        return ts or found

    def add(self, addr: str, start: bool = True) -> None:
        with self.lock:
            if addr in self.servers:
                return
            srv = Server((addr, self.port), self.handler)
            srv.daemon_threads = True
            self.servers[addr] = srv
        if start:
            threading.Thread(target=srv.serve_forever, daemon=True, name=f"serve-{addr}").start()

    def set(self, on: bool) -> dict:
        if on:
            addrs = [a for a in self.servers] or self.default_addresses()
            if not addrs:
                raise ValueError("this machine has no address but 127.0.0.1 (no network)")
            for a in addrs:
                self.add(a)
        else:
            self.stop()
        return self.status()

    def stop(self) -> None:
        with self.lock:
            servers, self.servers = list(self.servers.values()), {}
        for srv in servers:
            threading.Thread(target=lambda s=srv: (s.shutdown(), s.server_close()), daemon=True).start()

    def status(self) -> dict:
        with self.lock:
            addrs = sorted(a for a in self.servers if a != "0.0.0.0") or (remote.addresses() if "0.0.0.0" in self.servers else [])
        return {"on": bool(self.servers), "port": self.port, "addresses": addrs, "available": self.default_addresses()}
