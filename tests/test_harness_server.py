"""The Harness Server (builddoc/harness-server.md §8): alone; through a Desk (hosted, two Desks on one state dir play a
restart); through `python -m orbit` (SIGTERM, a second server, Quit and Stop Agents, a crash); direct mode.

Every test has its own state dir and starts and stops its own Harness Server; nothing touches ~/.orbit or a
running Orbit Server. Processes are only ever signalled by pids the test recorded.

Run: uv run python -m unittest tests.test_harness_server -v   (from the repository; or discover -s tests)
"""

import base64
import contextlib
import io
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAKE = f"{shlex.quote(sys.executable)} {shlex.quote(str(HERE / 'fake_claude.py'))}"

from orbit.harness_server.client import Client, HarnessError, HostedLauncher, start_server  # noqa: E402


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def until(cond, timeout: float = 10.0, step: float = 0.05) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class Base(unittest.TestCase):
    """A temporary state dir; every Harness Server and pid recorded is gone at the end."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.daemons: list[subprocess.Popen] = []
        self.pids: list[int] = []  # processes the test learned of (killed at the end if a test failed to end them)
        self.clients: list[Client] = []

    def client(self) -> Client:
        c = Client(self.state, "test")
        self.clients.append(c)
        return c

    def tearDown(self):
        for x in self.clients:
            x.close()
        c = Client(self.state)
        try:
            c.connect(start=False)
            pid = c.hello["pid"]
            c.request("shutdown")
            self.assertTrue(until(lambda: not alive(pid), 10), "the Harness Server did not stop")
        except OSError:
            pass
        finally:
            c.close()
        for p in self.daemons:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        left = [pid for pid in self.pids if alive(pid)]
        for pid in left:
            os.kill(pid, signal.SIGKILL)
        self._tmp.cleanup()
        self.assertEqual(left, [], "processes left running")

    def start(self, *args) -> Client:
        self.daemons.append(start_server(self.state, ["--idle", "30", *args]))
        c = self.client()
        self.assertTrue(until(lambda: self._try(c), 5), "the Harness Server did not answer")
        return c

    @staticmethod
    def _try(c: Client) -> bool:
        try:
            c.connect(start=False)
            return True
        except OSError:
            return False

    def owner(self, *args, owner: str = "t") -> Client:
        c = self.start(*args)
        c.request("claim", owner=owner)
        return c

    def frames(self, proc: str, start: int = 0, owner: str = "t", until_t: str = "exit", timeout: float = 10.0) -> list[dict]:
        """A read stream's frames, up to the first `until_t` frame."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(self.state / "harness" / "server.sock"))
        f = s.makefile("rb")
        out = []
        try:
            for n, req in enumerate(({"op": "claim", "owner": owner}, {"op": "read", "proc": proc, "from": start})):
                s.sendall((json.dumps({"id": n, **req}) + "\n").encode())
                r = json.loads(f.readline())
                if not r["ok"]:
                    raise HarnessError(r["error"])
            for raw in f:
                fr = json.loads(raw)
                out.append(fr)
                if fr["t"] == until_t or fr["t"] == "exit":
                    break
        finally:
            with contextlib.suppress(OSError):
                s.shutdown(socket.SHUT_RDWR)
            f.close()
            s.close()
        return out

    @staticmethod
    def data(frames: list[dict], t: str = "out") -> bytes:
        return b"".join(base64.b64decode(fr["b64"]) for fr in frames if fr["t"] == t)

    def spawn(self, c: Client, argv: list[str], tag: str = "x") -> dict:
        r = c.request("spawn", argv=argv, cwd=str(self.tmp), env=dict(os.environ), tag=tag)
        self.pids.append(r["pid"])
        return r


class HarnessServerAlone(Base):
    def test_spawn_write_and_read(self):
        """stdin in order, both streams in one journal with offsets, `live` then `exit`; from an offset too."""
        c = self.owner()
        p = self.spawn(c, py("import sys\nfor l in sys.stdin:\n print('got', l.strip(), flush=True)\n"
                             " print('e', file=sys.stderr, flush=True)\nsys.exit(4)"))
        for x in ("a", "b", "c"):
            c.request("write", proc=p["proc"], data=base64.b64encode(f"{x}\n".encode()).decode())
        c.request("close_stdin", proc=p["proc"])
        self.assertTrue(until(lambda: not c.request("list")["procs"][0]["alive"]))
        fr = self.frames(p["proc"])  # (all of it was there: `live` just before `exit`)
        self.assertEqual(self.data(fr), b"got a\ngot b\ngot c\n")
        self.assertEqual(self.data(fr, "err"), b"e\ne\ne\n")
        self.assertEqual([x["t"] for x in fr[-2:]], ["live", "exit"])
        self.assertEqual(fr[-1]["code"], 4)
        pos = 0
        for x in fr:
            if x["t"] in ("out", "err"):
                self.assertEqual(x["off"], pos)
                self.assertIsInstance(x["ts"], float)
                pos += len(base64.b64decode(x["b64"]))
        self.assertEqual(fr[-1]["off"], pos)
        whole = b"".join(base64.b64decode(x["b64"]) for x in fr if x["t"] in ("out", "err"))
        later = self.frames(p["proc"], start=5)  # (inside a frame: it starts there)
        self.assertEqual(later[0]["off"], 5)
        self.assertEqual(b"".join(base64.b64decode(x["b64"]) for x in later if x["t"] in ("out", "err")), whole[5:])
        lst = c.request("list")["procs"]
        self.assertEqual([(x["proc"], x["alive"], x["code"], x["end"], x["tag"]) for x in lst],
                         [(p["proc"], False, 4, pos, "x")])

    def test_live_comes_between_the_past_and_what_is_new(self):
        c = self.owner()
        p = self.spawn(c, py("import time\nprint('before', flush=True)\ntime.sleep(1)\nprint('after', flush=True)"))
        until(lambda: c.request("list")["procs"][0]["end"] > 0, 5)
        fr = self.frames(p["proc"])
        kinds = [(x["t"], base64.b64decode(x.get("b64", "")).decode()) for x in fr]
        self.assertEqual(kinds, [("out", "before\n"), ("live", ""), ("out", "after\n"), ("exit", "")])

    def test_split_utf8_and_split_lines(self):
        """A UTF-8 character and a line split between frames; a line longer than a pipe read: the client gives
        whole lines, decoded."""
        c = self.owner()
        launcher = HostedLauncher(self.state)
        launcher.owner = "t"
        launcher.client = c
        code = ("import os, time\nb = 'é'.encode()\nos.write(1, b'x' + b[:1])\ntime.sleep(0.2)\n"
                "os.write(1, b[1:] + b'one\\npar')\ntime.sleep(0.2)\nos.write(1, b'tial\\n' + b'z' * 200000 + b'\\nend')")
        p = self.spawn(c, py(code))
        fr = self.frames(p["proc"])
        self.assertGreaterEqual(len([x for x in fr if x["t"] == "out"]), 4)
        hp = launcher.attach(p["proc"], 0, p["pid"])
        lines = list(hp.stdout)
        self.assertEqual(lines[:2], ["xéone\n", "partial\n"])
        self.assertEqual(lines[2], "z" * 200000 + "\n")
        self.assertEqual(lines[3], "end")
        self.assertEqual(hp.wait(5), 0)
        self.assertEqual(hp.offset, 7 + 8 + 200001 + 3)  # after the last line it handled (its end)
        launcher.flush()

    def test_signal_pid_and_group(self):
        """To the pid: what it started lives on; to the group: that too."""
        c = self.owner()
        code = ("import subprocess, sys, time\np = subprocess.Popen(['sleep', '60'])\nprint(p.pid, flush=True)\n"
                "time.sleep(60)")
        procs = []
        for _ in range(2):
            p = self.spawn(c, py(code))
            self.assertTrue(until(lambda: c.request("list", tag="x")["procs"][-1]["end"] > 0))
            fr = self.frames(p["proc"], until_t="live")
            kid = int(self.data(fr).split()[0])
            self.pids.append(kid)
            procs.append((p, kid))
        (p1, kid1), (p2, kid2) = procs
        c.request("signal", proc=p1["proc"], sig="TERM")
        self.assertTrue(until(lambda: not alive(p1["pid"])))
        time.sleep(0.2)
        self.assertTrue(alive(kid1))  # (a dev server an agent left behind keeps running)
        c.request("signal", proc=p2["proc"], sig="TERM", group=True)
        self.assertTrue(until(lambda: not alive(p2["pid"]) and not alive(kid2)))
        with self.assertRaises(HarnessError):
            c.request("signal", proc=p2["proc"], sig="HUP")
        c.request("shutdown")  # its own shutdown ends the groups, a leader's leftovers included
        self.assertTrue(until(lambda: not alive(kid1)))

    def test_list_rss_and_tag(self):
        c = self.owner()
        a = self.spawn(c, ["sleep", "60"], tag="a")
        self.spawn(c, ["sleep", "60"], tag="b")
        mine = c.request("list", tag="a")["procs"]
        self.assertEqual([x["proc"] for x in mine], [a["proc"]])
        self.assertTrue(mine[0]["alive"])
        self.assertIsNone(mine[0]["code"])
        self.assertGreater(mine[0]["rss"], 0)
        self.assertEqual(len(c.request("list")["procs"]), 2)

    def test_trim_and_forget(self):
        c = self.owner("--segment", "2000")
        p = self.spawn(c, py("import sys, time\nfor i in range(60):\n print('%04d' % i + 'x' * 200, flush=True)\n"
                             " time.sleep(0.005)"))
        self.assertTrue(until(lambda: not c.request("list")["procs"][0]["alive"]))
        d = self.state / "harness" / "procs" / p["proc"]
        segs = sorted(x.name for x in d.glob("journal.*"))
        self.assertGreater(len(segs), 3)
        end = c.request("list")["procs"][0]["end"]
        with self.assertRaises(HarnessError):
            c.request("forget", proc="nope")
        c.request("trim", proc=p["proc"], before=end - 100)
        self.assertLess(len(list(d.glob("journal.*"))), len(segs))
        with self.assertRaisesRegex(HarnessError, "trimmed"):
            self.frames(p["proc"], start=0)
        tail = self.frames(p["proc"], start=end - 100)
        self.assertEqual(len(self.data(tail)), 100)
        self.assertTrue(json.loads((d / "meta.json").read_text())["argv"])
        self.assertNotIn("env", json.loads((d / "meta.json").read_text()))
        q = self.spawn(c, ["sleep", "60"])
        with self.assertRaisesRegex(HarnessError, "still running"):
            c.request("forget", proc=q["proc"])
        c.request("forget", proc=p["proc"])
        self.assertFalse(d.exists())
        self.assertEqual([x["proc"] for x in c.request("list")["procs"]], [q["proc"]])

    def test_unread_journal_over_the_limit_is_truncated(self):
        c = self.owner("--segment", "2000", "--unread-max", "6000")
        p = self.spawn(c, py("import sys, time\nfor i in range(60):\n print('y' * 500, flush=True)\n time.sleep(0.002)"))
        self.assertTrue(until(lambda: not c.request("list")["procs"][0]["alive"]))
        with self.assertRaisesRegex(HarnessError, "truncated_before"):
            self.frames(p["proc"], start=0)
        meta = json.loads((self.state / "harness" / "procs" / p["proc"] / "meta.json").read_text())
        self.assertGreater(meta["truncated_before"], 0)

    def test_claim_and_takeover(self):
        c1 = self.start()
        c1.request("claim", owner="a")
        c2 = self.client()
        c2.connect()
        with self.assertRaisesRegex(HarnessError, "claim the Harness Server first"):
            c2.request("spawn", argv=["true"], cwd="/", env={}, tag="")
        with self.assertRaisesRegex(HarnessError, "another Orbit Server"):
            c2.request("claim", owner="b")
        c2.request("list")  # anyone may look
        c2.request("claim", owner="b", takeover=True)
        with self.assertRaises(ConnectionError):  # the previous owner's connection was closed
            c1.request("list")
        self.spawn(c2, ["true"])
        c2.close()
        c3 = self.client()
        c3.connect()
        self.assertTrue(until(lambda: self._claims(c3, "c")))  # its owner gone: anyone may claim it

    @staticmethod
    def _claims(c, owner):
        try:
            c.request("claim", owner=owner)
            return True
        except HarnessError:
            return False

    def test_one_instance_under_a_race_of_two_starters(self):
        a, b = start_server(self.state, ["--idle", "30"]), start_server(self.state, ["--idle", "30"])
        self.daemons += [a, b]
        c1, c2 = self.client(), self.client()
        self.assertTrue(until(lambda: self._try(c1) and self._try(c2), 5))
        self.assertEqual(c1.hello["pid"], c2.hello["pid"])
        self.assertIn(c1.hello["pid"], (a.pid, b.pid))
        loser = b if c1.hello["pid"] == a.pid else a
        self.assertEqual(loser.wait(5), 0)
        self.assertEqual(int((self.state / "harness" / "server.pid").read_text()), c1.hello["pid"])
        self.assertEqual(os.stat(self.state / "harness").st_mode & 0o777, 0o700)

    def test_a_slow_client_does_not_block_another(self):
        c = self.owner()
        p = self.spawn(c, py("import sys\nfor i in range(80):\n sys.stdout.write('%05d' % i + 'q' * 65531)\n"
                             "sys.stdout.flush()"))
        stuck = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # reads nothing after asking
        stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        stuck.connect(str(self.state / "harness" / "server.sock"))
        stuck.sendall(b'{"id":1,"op":"claim","owner":"t"}\n' + json.dumps({"id": 2, "op": "read", "proc": p["proc"], "from": 0}).encode() + b"\n")
        try:
            t = time.time()
            fr = self.frames(p["proc"], timeout=20)
            self.assertEqual(len(self.data(fr)), 80 * 65536)
            self.assertEqual(fr[-1]["t"], "exit")
            self.assertLess(time.time() - t, 15)
            self.assertFalse(c.request("list")["procs"][0]["alive"])  # its pipe never waited on the stuck reader
        finally:
            stuck.close()

    def test_idle_exit(self):
        c = self.start("--idle", "1")
        c.close()
        self.assertEqual(self.daemons[0].wait(8), 0)
        self.assertFalse((self.state / "harness" / "server.sock").exists())

    def test_exit_when_its_state_dir_goes(self):
        c = self.owner()
        p = self.spawn(c, ["sleep", "60"])
        c.close()
        shutil.rmtree(self.state)
        self.assertEqual(self.daemons[0].wait(10), 0)
        self.assertTrue(until(lambda: not alive(p["pid"])))

    def test_shutdown_ends_everything(self):
        c = self.owner()
        a = self.spawn(c, ["sleep", "60"])
        b = self.spawn(c, py("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\n"
                             "time.sleep(60)"))
        self.frames(b["proc"], until_t="live")
        c.request("shutdown")
        self.assertEqual(self.daemons[0].wait(10), 0)  # (SIGKILL after 3 s for the one that ignores SIGTERM)
        self.assertTrue(until(lambda: not alive(a["pid"]) and not alive(b["pid"])))

    def test_cli_status_and_stop(self):
        from orbit.cli import orbit as cli
        c = self.owner()
        self.spawn(c, ["sleep", "60"], tag="sess-1")
        old = os.environ.get("ORBIT_PORT")
        os.environ["ORBIT_PORT"] = str(free_port())  # (no Orbit Server there: never the user's)
        try:
            for action in ("status", "stop"):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
                    cli.main(["--state-dir", str(self.state), "harness-server", action])
                self.assertEqual(e.exception.code, 0)
                if action == "status":
                    self.assertIn(f"pid {c.hello['pid']}", out.getvalue())
                    self.assertIn("sess-1", out.getvalue())
                else:
                    self.assertEqual(self.daemons[0].wait(10), 0)
        finally:
            if old is None:
                os.environ.pop("ORBIT_PORT", None)
            else:
                os.environ["ORBIT_PORT"] = old


class HostedDeskTest(Base):
    """Two Desks on one state dir, one after the other, play a restart of the Orbit Server."""

    def setUp(self):
        super().setUp()
        self._env = {k: os.environ.get(k) for k in ("ORBIT_CLAUDE_BIN", "ORBIT_CLAUDE_PROJECTS", "ORBIT_NO_NOTIFY",
                                                    "FAKE_BG_SLOW", "FAKE_FLOOD", "ORBIT_HARNESS_SERVER")}
        os.environ.update(ORBIT_CLAUDE_BIN=FAKE, ORBIT_CLAUDE_PROJECTS=str(self.tmp / "projects"), ORBIT_NO_NOTIFY="1")
        os.environ.pop("ORBIT_HARNESS_SERVER", None)
        (self.tmp / "work").mkdir()
        self.work = str(self.tmp / "work")
        from orbit.desk import driving
        self._retry, driving.RETRY_WAIT = driving.RETRY_WAIT, 0.2
        self.desks = []

    def tearDown(self):
        for d in self.desks:  # (a Desk a test did not stop: its agents go with it)
            if not d.stopping_all:
                d.stop_agents = True
                d.shutdown()
        self.pids += [x["pid"] for x in self.inputs()]
        from orbit.desk import driving
        driving.RETRY_WAIT = self._retry
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def desk(self, hosted: bool = True, state: Path | None = None):
        from orbit.desk import Desk
        d = Desk(state or self.state)
        if hosted:
            d.host(["--idle", "30"])
        self.desks.append(d)
        return d

    def inputs(self, sid: str | None = None) -> list[dict]:
        try:
            lines = (self.tmp / "projects" / "inputs.log").read_text().splitlines()
        except OSError:
            return []
        return [x for x in map(json.loads, lines) if sid is None or x["sid"] == sid]

    def card(self, d, sid, cid, states, timeout=20):
        c = None

        def ok():
            nonlocal c
            c = next((x for x in d.cards(sid) if x["id"] == cid), None)
            return c is not None and c["state"] in states
        self.assertTrue(until(ok, timeout), f"card {cid} never reached {states}: {c and c['state']}")
        return c

    @staticmethod
    def same(card: dict) -> dict:
        """A card without what differs between two runs of the same request (times, its session, revisions)."""
        out = {k: v for k, v in card.items() if k not in ("sid", "time", "started", "finished", "rev")}
        out["steps"] = [{k: v for k, v in st.items() if k != "t0"} for st in card["steps"]]
        return out

    def test_a_hosted_card_is_the_same_as_a_direct_one(self):
        a = self.desk()
        self.assertEqual(a.hosting["mode"], "hosted")
        s = a.create(self.work, name="hosted")
        a.send(s["id"], "narrate please")
        hosted = self.card(a, s["id"], 1, ("completed",))
        self.assertTrue(a.sessions[s["id"]].driver.proc.proc)  # (a HostedProcess)
        b = self.desk(hosted=False, state=self.tmp / "direct")
        s2 = b.create(self.work, name="direct")
        b.send(s2["id"], "narrate please")
        direct = self.card(b, s2["id"], 1, ("completed",))
        self.assertEqual(self.same(hosted), self.same(direct))
        self.assertTrue(all(st.get("t0") for st in hosted["steps"] if st["kind"] == "tool"))

    def test_restart_mid_request(self):
        """The same card goes on and completes with the same id, on the same process; no "server restarted" request,
        no second [orbit] line; a request sent while it catches up waits, then runs there too."""
        a = self.desk()
        s = a.create(self.work, name="restart")
        sid = s["id"]
        a.send(sid, "slow")
        self.card(a, sid, 1, ("running",))
        pid = a.sessions[sid].driver.proc.pid
        a.shutdown()
        self.assertTrue((self.state / "handoff" / f"{sid}.json").exists())
        self.assertFalse(json.loads((self.state / "sessions.json").read_text())["sessions"][0].get("interrupted"))
        b = self.desk()
        self.assertFalse((self.state / "handoff" / f"{sid}.json").exists())
        sb = b.sessions[sid]
        self.assertEqual(sb.driver.proc.pid, pid)
        self.assertEqual(b.cards(sid)[0]["state"], "running")  # its card as it was, at once
        b.send(sid, "next")
        b.resume_interrupted()  # (nothing to resume: it was handed off)
        c1 = self.card(b, sid, 1, ("completed",))
        self.assertEqual(c1["answer"], "slept")
        self.assertEqual(c1["steps"][0]["kind"], "note")  # (the steps from before the restart, as they were)
        self.assertEqual(self.card(b, sid, 2, ("completed",))["answer"], "answer: next")
        got = self.inputs(sid)
        self.assertEqual([x["pid"] for x in got], [pid, pid])
        self.assertTrue(got[0]["text"].startswith("[orbit] "))
        self.assertEqual(got[1]["text"], "next")
        self.assertFalse(any("restarted" in x["text"] for x in got))
        self.assertEqual(len(b.cards(sid)), 2)

    def test_restart_the_harness_server(self):
        """Restart Harness Server: its agent processes end (the session stays), a new Harness Server runs; the request
        that was at work goes on by itself, and one sent meanwhile waits, then runs, both on a new process."""
        a = self.desk()
        sid = a.create(self.work, name="hs")["id"]
        a.send(sid, "slow")
        self.card(a, sid, 1, ("running",))
        old_hs, pid = a.hosting["pid"], a.sessions[sid].driver.proc.pid
        got: dict = {}
        t = threading.Thread(target=lambda: got.update(a.restart_harness_server()), daemon=True)
        t.start()
        self.assertTrue(until(lambda: a.hs_restarting, 5))
        a.send(sid, "meanwhile")  # (queued: no process starts while it restarts)
        t.join(30)
        self.assertFalse(t.is_alive())
        self.assertEqual((got["mode"], got["ended"], got["old_pid"]), ("hosted", 1, old_hs))
        self.assertNotEqual(got["pid"], old_hs)
        self.assertTrue(until(lambda: not alive(old_hs) and not alive(pid), 10))
        self.assertEqual(self.card(a, sid, 2, ("completed",))["answer"], "answer: meanwhile")
        self.assertIn(self.card(a, sid, 1, ("cancelled", "paused"))["state"], ("cancelled", "paused"))
        again = self.card(a, sid, 3, ("completed",))  # the one it cut off goes on (once: no second continue)
        self.assertIn("Orbit's Harness Server restarted", again["text"])
        time.sleep(1)
        self.assertEqual(len(a.cards(sid)), 3)
        later = [x["pid"] for x in self.inputs(sid)][1:]
        self.assertEqual(len(later), 2)
        self.assertNotIn(pid, later)
        self.assertEqual(a.harness_status()["pid"], got["pid"])
        with self.assertRaises(ValueError):  # direct: nothing to restart
            self.desk(hosted=False, state=self.tmp / "other").restart_harness_server()

    def test_a_background_subagent_survives_a_restart(self):
        os.environ["FAKE_BG_SLOW"] = "3"
        a = self.desk()
        sid = a.create(self.work)["id"]
        a.send(sid, "bgslow please")
        self.card(a, sid, 1, ("completed",))
        agent = lambda d: next(st for st in d.cards(sid)[0]["steps"] if st.get("agent"))
        self.assertTrue(until(lambda: any(x.get("name") == "Read" for x in agent(a).get("sub", []))))
        a.shutdown()
        b = self.desk()
        self.assertEqual(b.sessions[sid].public()["agents"], 1)  # still at work
        self.assertTrue(until(lambda: agent(b)["done"], 15))
        st = agent(b)
        self.assertFalse(st.get("lost"))
        self.assertFalse(st["error"])
        self.assertIn("returns early on empty input", st["result"])
        self.assertEqual(b.sessions[sid].public()["agents"], 0)

    def test_output_while_down(self):
        """What the agent wrote while no server read it: the card after the restart equals one run without one."""
        os.environ["FAKE_FLOOD"] = "40"
        a = self.desk()
        sid = a.create(self.work, name="flood")["id"]
        a.send(sid, "flood")
        self.assertTrue(until(lambda: len(a.cards(sid)[0]["steps"]) >= 6))
        a.shutdown()
        time.sleep(1.0)  # it writes on meanwhile
        b = self.desk()
        restarted = self.card(b, sid, 1, ("completed",), 30)
        sid2 = b.create(self.work, name="flood")["id"]
        b.send(sid2, "flood")
        plain = self.card(b, sid2, 1, ("completed",), 30)
        self.assertEqual(self.same(restarted), self.same(plain))
        self.assertEqual(len([st for st in plain["steps"] if st["kind"] == "tool"]), 40)
        self.assertEqual(len(plain["steps"][40]["text"]), len("round 20: ") + 3 * (200_000 // 3))
        self.assertEqual(plain["answer"], "flooded")

    def test_the_agent_exits_while_down(self):
        """The exit read after the restart runs as if live: the card is paused, then continued by itself."""
        a = self.desk()
        sid = a.create(self.work)["id"]
        a.send(sid, "die 1.5")
        self.card(a, sid, 1, ("running",))
        a.shutdown()
        old = a.sessions[sid]
        self.assertTrue(until(lambda: not any(alive(x["pid"]) for x in self.inputs(sid)), 10))
        del old
        b = self.desk()
        c1 = self.card(b, sid, 1, ("paused",))
        self.assertIn("exit 3", c1["error"] + json.dumps(c1))
        c2 = self.card(b, sid, 2, ("completed",))
        self.assertEqual(c2["text"], "Continue request #1 where you stopped.")
        self.assertEqual(self.card(b, sid, 1, ("paused",))["continued_by"], 2)

    def test_a_stop_in_progress_is_not_handed_off(self):
        a = self.desk()
        sid = a.create(self.work)["id"]
        a.send(sid, "deaf")
        self.card(a, sid, 1, ("running",))
        a.stop(sid)  # it ignores the interrupt: the Stop is still in progress at the restart
        a.shutdown()
        self.assertFalse((self.state / "handoff" / f"{sid}.json").exists())
        self.assertTrue(json.loads((self.state / "sessions.json").read_text())["sessions"][0]["interrupted"])
        first = self.inputs(sid)[0]["pid"]
        self.assertFalse(alive(first))
        b = self.desk()
        b.resume_interrupted()
        self.assertTrue(until(lambda: len(self.inputs(sid)) == 2, 15))
        again = self.inputs(sid)[1]
        self.assertIn("Orbit's server restarted", again["text"])
        self.assertNotEqual(again["pid"], first)

    def test_nobody_attached_is_ended_before_anything_starts(self):
        """No hand-off (a crash): the process left running is ended when the next Desk starts, before it starts
        any; its request is marked interrupted and resumed."""
        a = self.desk()
        sid = a.create(self.work)["id"]
        a.send(sid, "slow")
        self.card(a, sid, 1, ("running",))
        pid = a.sessions[sid].driver.proc.pid
        a.stopping_all = True  # a crash: nothing more from this Desk (no hand-off, no save)
        for s in a.sessions.values():
            s.driver = None
        a.launcher.closed = True
        a.launcher.client.close()
        for hp in list(a.launcher.live.values()):
            hp._sock.close()
        b = self.desk()
        self.assertFalse(alive(pid))  # ended before host() returned
        self.assertTrue(b.sessions[sid].interrupted)
        b.resume_interrupted()
        self.assertTrue(until(lambda: len(self.inputs(sid)) == 2, 15))
        self.assertIn("Orbit's server restarted", self.inputs(sid)[1]["text"])
        self.assertNotEqual(self.inputs(sid)[1]["pid"], pid)

    def test_a_harness_server_that_cannot_start_means_direct(self):
        (self.state / "harness").write_text("not a folder")
        d = self.desk()
        self.assertEqual(d.hosting["mode"], "direct")
        self.assertIn("could not", d.hosting["why"])
        sid = d.create(self.work)["id"]
        d.send(sid, "hello")
        self.assertEqual(self.card(d, sid, 1, ("completed",))["answer"], "answer: hello")
        (self.state / "harness").unlink()

    def test_forced_direct(self):
        os.environ["ORBIT_HARNESS_SERVER"] = "0"
        d = self.desk()
        self.assertEqual(d.hosting, {"mode": "direct", "why": "ORBIT_HARNESS_SERVER=0"})
        sid = d.create(self.work)["id"]
        d.send(sid, "hello")
        self.assertEqual(self.card(d, sid, 1, ("completed",))["answer"], "answer: hello")
        self.assertIsInstance(d.sessions[sid].driver.proc, subprocess.Popen)
        self.assertFalse((self.state / "harness").exists())


class OrbitServeTest(Base):
    """`python -m orbit` (what `orbit serve` and the app run): hosting is on there."""

    def setUp(self):
        super().setUp()
        (self.tmp / "work").mkdir()
        (self.tmp / "home").mkdir()
        self.work = str(self.tmp / "work")
        self.env = {k: v for k, v in os.environ.items() if k not in ("ORBIT_VAULT", "ORBIT_STATE_DIR", "ORBIT_DATA_ROOT",
                                                                      "ORBIT_PORT", "ORBIT_HARNESS_SERVER")}
        self.env.update(HOME=str(self.tmp / "home"), ORBIT_CLAUDE_BIN=FAKE, ORBIT_CLAUDE_PROJECTS=str(self.tmp / "projects"),
                        ORBIT_NO_NOTIFY="1", ORBIT_RETRY_WAIT="0.2", PYTHONPATH=str(ROOT))
        self.servers: list[subprocess.Popen] = []

    def tearDown(self):
        for p in self.servers:
            if p.poll() is None:
                p.send_signal(signal.SIGUSR1)  # (stop with its agents)
                try:
                    p.wait(15)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            p.log.close()
        self.pids += [x["pid"] for x in self.inputs()]
        super().tearDown()

    def serve(self, wait: bool = True) -> tuple[subprocess.Popen, int]:
        port = free_port()
        log = open(self.tmp / f"serve-{port}.log", "wb")
        p = subprocess.Popen([sys.executable, "-m", "orbit", "--port", str(port), "--state-dir", str(self.state),
                              "--data-root", str(self.tmp / "data")], cwd=str(ROOT), env=self.env,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        p.log = log
        self.servers.append(p)
        if wait:
            self.assertTrue(until(lambda: self._up(port) or p.poll() is not None, 20), "the server did not start")
            self.assertIsNone(p.poll(), (self.tmp / f"serve-{port}.log").read_text())
        return p, port

    def _up(self, port):
        try:
            self.req(port, "GET", "/api/sessions")
            return True
        except OSError:
            return False

    def req(self, port, method, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, headers={"Content-Type": "application/json"},
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def inputs(self, sid=None):
        try:
            lines = (self.tmp / "projects" / "inputs.log").read_text().splitlines()
        except OSError:
            return []
        return [x for x in map(json.loads, lines) if sid is None or x["sid"] == sid]

    def card(self, port, sid, cid, states, timeout=20):
        c = None

        def ok():
            nonlocal c
            c = next((x for x in self.req(port, "GET", f"/api/sessions/{sid}/cards")[1]["cards"] if x["id"] == cid), None)
            return c is not None and c["state"] in states
        self.assertTrue(until(ok, timeout, 0.1), f"card {cid} never reached {states}: {c and c['state']}")
        return c

    def running(self, port, text="slow", states=("running",)):
        _, s = self.req(port, "POST", "/api/sessions", {"cwd": self.work, "name": "x"})
        self.req(port, "POST", f"/api/sessions/{s['id']}/send", {"text": text})
        self.card(port, s["id"], 1, states)
        self.assertTrue(until(lambda: self.inputs(s["id"])))
        return s["id"], self.inputs(s["id"])[0]["pid"]

    def test_restart_and_a_second_server(self):
        """SIGTERM hands the agent off; a second server on the same state dir exits at once and touches nothing;
        the next server reads on, and the request completes on the same process."""
        one, port = self.serve()
        self.assertEqual(self.req(port, "GET", "/api/harness-server")[1]["mode"], "hosted")
        sid, pid = self.running(port)
        two, _ = self.serve(wait=False)
        self.assertEqual(two.wait(15), 1)
        self.assertIn(b"another Orbit Server", (self.tmp / f"serve-{_}.log").read_bytes())
        self.assertTrue(alive(pid))
        self.assertEqual(self.card(port, sid, 1, ("running",))["state"], "running")
        one.send_signal(signal.SIGTERM)
        self.assertEqual(one.wait(15), 0)
        self.assertTrue(alive(pid))
        three, port3 = self.serve()
        self.assertEqual(self.card(port3, sid, 1, ("completed",))["answer"], "slept")
        self.assertEqual([x["pid"] for x in self.inputs(sid)], [pid])

    def test_quit_and_stop_agents(self):
        """⌥⌘Q: POST /api/shutdown {"agents": "stop"}: every agent and the Harness Server end."""
        one, port = self.serve()
        hs = self.req(port, "GET", "/api/harness-server")[1]["pid"]
        sid, pid = self.running(port)
        self.assertEqual(self.req(port, "POST", "/api/shutdown", {"agents": "stop"})[0], 200)
        self.assertEqual(one.wait(20), 0)
        self.assertTrue(until(lambda: not alive(pid) and not alive(hs), 10))
        self.assertFalse(list((self.state / "handoff").glob("*.json")) if (self.state / "handoff").is_dir() else [])

    def test_sigusr1_stops_agents_too(self):
        one, port = self.serve()
        hs = self.req(port, "GET", "/api/harness-server")[1]["pid"]
        sid, pid = self.running(port, "hello", ("completed",))  # (an agent between requests)
        one.send_signal(signal.SIGUSR1)
        self.assertEqual(one.wait(20), 0)
        self.assertTrue(until(lambda: not alive(pid) and not alive(hs), 10))

    def test_a_crash(self):
        """No hand-off: the agent the dead server left is ended before the next one starts any; its request resumes."""
        one, port = self.serve()
        sid, pid = self.running(port)
        one.kill()  # (the server we started)
        one.wait()
        self.assertTrue(alive(pid))  # the Harness Server keeps it
        two, port2 = self.serve()
        self.assertFalse(alive(pid))
        self.assertTrue(until(lambda: len(self.inputs(sid)) == 2, 15))
        again = self.inputs(sid)[1]
        self.assertIn("Orbit's server restarted", again["text"])
        log = (self.state / "harness" / "server.log").read_text()
        self.assertLess(log.index(f"pid {pid} exited"), log.index(f"pid {again['pid']} started"))

    def test_forced_direct(self):
        self.env["ORBIT_HARNESS_SERVER"] = "0"
        one, port = self.serve()
        self.assertEqual(self.req(port, "GET", "/api/harness-server")[1]["mode"], "direct")
        _, s = self.req(port, "POST", "/api/sessions", {"cwd": self.work})
        self.req(port, "POST", f"/api/sessions/{s['id']}/send", {"text": "hello"})
        self.assertEqual(self.card(port, s["id"], 1, ("completed",))["answer"], "answer: hello")
        self.assertFalse((self.state / "harness").exists())


    # ------------------------------------------------------------------ orbit stop (§10.6.1), signals at start-up
    def stop_cli(self, *args):
        from orbit.cli import orbit as cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
            cli.main(["stop", "--state-dir", str(self.state), *args])
        return e.exception.code, out.getvalue()

    def test_orbit_stop_hands_off_and_with_agents_ends_them(self):
        """`orbit stop` finds its server by <state>/server.json (a port of its own, never 8770): the agent runs on
        and the next server takes it; `orbit stop --agents` ends it and the Harness Server."""
        one, port = self.serve()
        info = json.loads((self.state / "server.json").read_text())
        self.assertEqual((info["pid"], info["port"]), (one.pid, port))
        self.assertEqual(self.req(port, "POST", "/api/shutdown", {"pid": one.pid + 1_000_000})[0], 409)  # not its pid
        hs = self.req(port, "GET", "/api/harness-server")[1]["pid"]
        sid, pid = self.running(port)
        code, out = self.stop_cli()
        self.assertEqual(code, 0, out)
        self.assertIn("run on in the Harness Server", out)
        self.assertEqual(one.wait(10), 0)
        self.assertFalse((self.state / "server.json").exists())
        self.assertTrue(alive(pid) and alive(hs))
        two, port2 = self.serve()
        self.assertEqual(self.card(port2, sid, 1, ("completed",))["answer"], "slept")
        self.assertEqual([x["pid"] for x in self.inputs(sid)], [pid])
        code, out = self.stop_cli("--agents")
        self.assertEqual(code, 0, out)
        self.assertIn("with its agents", out)
        self.assertEqual(two.wait(10), 0)
        self.assertTrue(until(lambda: not alive(pid) and not alive(hs), 10))
        self.assertIn("No Orbit Server runs", self.stop_cli()[1])

    def test_orbit_stop_in_direct_mode_says_the_agents_end(self):
        self.env["ORBIT_HARNESS_SERVER"] = "0"
        one, port = self.serve()
        sid, pid = self.running(port, "hello", ("completed",))
        code, out = self.stop_cli()
        self.assertEqual(code, 0, out)
        self.assertIn("ended too", out)
        self.assertEqual(one.wait(10), 0)
        self.assertTrue(until(lambda: not alive(pid), 10))

    def test_a_signal_while_it_starts(self):
        """SIGTERM while the server takes the hand-offs: acted on once it is up (a hand-off again), never cut in half."""
        one, port = self.serve()
        sid, pid = self.running(port)
        one.send_signal(signal.SIGTERM)
        self.assertEqual(one.wait(15), 0)
        two, _ = self.serve(wait=False)

        def started():
            try:
                return json.loads((self.state / "server.json").read_text())["pid"] == two.pid
            except (OSError, ValueError, KeyError):
                return False
        self.assertTrue(until(started, 20, 0.005))
        two.send_signal(signal.SIGTERM)
        self.assertEqual(two.wait(20), 0)
        self.assertTrue((self.state / "handoff" / f"{sid}.json").exists())  # taken, then handed off again
        self.assertTrue(alive(pid))
        three, port3 = self.serve()
        self.assertEqual(self.card(port3, sid, 1, ("completed",))["answer"], "slept")
        self.assertEqual([x["pid"] for x in self.inputs(sid)], [pid])


PY = shlex.quote(sys.executable)
FAKE_CODEX = f"{PY} {shlex.quote(str(HERE / 'fake_codex.py'))}"
FAKE_AGY = f"{PY} {shlex.quote(str(HERE / 'fake_agy.py'))}"
FAKE_OPENCODE = f"{PY} {shlex.quote(str(HERE / 'fake_opencode.py'))}"
PROTO_TOML = f"""[plugin]
name = "handco"
version = "0.1.0"

[harness.handfake]
label = "Hand Fake"
command = [{json.dumps(sys.executable)}, {json.dumps(str(HERE / "fake_protocol_agent.py"))}]
"""


def logged(path: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    except OSError:
        return []


class PhaseTwoDeskTest(HostedDeskTest):
    """§10: Codex (app-server), agy, OpenCode and a protocol agent handed off; two Desks on one state dir play a
    restart."""

    KEYS = ("ORBIT_CODEX_BIN", "ORBIT_CODEX_SESSIONS", "CODEX_HOME", "ORBIT_CODEX_EXEC", "ORBIT_AGY_BIN",
            "ORBIT_AGY_CONVERSATIONS", "ORBIT_OPENCODE_BIN", "ORBIT_OPENCODE_DB", "FAKE_PROTOCOL_DIR", "FAKE_SUB_PAUSE",
            "FAKE_THREAD_DELAY")

    def setUp(self):
        super().setUp()
        self._env2 = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)
        self.codex, self.agy, self.proto = self.tmp / "codex" / "sessions", self.tmp / "agy", self.tmp / "proto"
        self.opencode = self.tmp / "opencode"
        os.environ.update(ORBIT_CODEX_BIN=FAKE_CODEX, ORBIT_CODEX_SESSIONS=str(self.codex), CODEX_HOME=str(self.tmp / "codex"),
                          ORBIT_AGY_BIN=FAKE_AGY, ORBIT_AGY_CONVERSATIONS=str(self.agy), FAKE_PROTOCOL_DIR=str(self.proto),
                          ORBIT_OPENCODE_BIN=FAKE_OPENCODE, ORBIT_OPENCODE_DB=str(self.opencode / "opencode.db"))
        from orbit.drivers.codex import CodexDriver
        CodexDriver._no_app.discard(FAKE_CODEX)
        self.plugged = False

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            for f in (self.codex / "stdin.log", self.agy / "stdin.log", self.proto / "stdin.log", self.proto / "token.log",
                      self.opencode / "argv.log"):
                self.pids += [x["pid"] for x in logged(f) if alive(x["pid"])]
            if self.plugged:
                from orbit import plugins
                plugins.unload()
            for k, v in self._env2.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def plug(self):
        """The protocol agent's plugin, loaded for this state dir (each call: as a new server start does)."""
        from orbit import plugins
        d = self.tmp / "plugins" / "handco"
        d.mkdir(parents=True, exist_ok=True)
        (d / "plugin.toml").write_text(PROTO_TOML)
        self.plugged = True
        found = plugins.load(self.state, [str(d)])
        self.assertTrue(next(p for p in found if p["name"] == "handco")["ok"], found)

    def running(self, d, harness, text, mode="default", states=("running",)):
        sid = d.create(self.work, harness=harness, mode=mode)["id"]
        d.send(sid, text)
        self.card(d, sid, 1, states)
        return sid, d.sessions[sid].driver.proc.pid

    def handed(self, a, sid):
        a.shutdown()
        self.assertTrue((self.state / "handoff" / f"{sid}.json").exists(), "not handed off")
        self.assertFalse(json.loads((self.state / "sessions.json").read_text())["sessions"][0].get("interrupted"))
        return json.loads((self.state / "handoff" / f"{sid}.json").read_text())

    def not_handed(self, a, sid):
        a.shutdown()
        self.assertFalse((self.state / "handoff" / f"{sid}.json").exists(), "handed off")
        self.assertTrue(json.loads((self.state / "sessions.json").read_text())["sessions"][0]["interrupted"])

    # ------------------------------------------------------------------ Codex (app-server)
    def test_codex_restart_mid_request(self):
        """The same card completes on the same process with the same id; no second initialize or thread/* written,
        no "server restarted" request; a request sent while it catches up runs there too."""
        a = self.desk()
        sid, pid = self.running(a, "codex", "slow 3")
        h = self.handed(a, sid)
        self.assertEqual((h["harness"], h["driver"]["exec"]), ("codex", False))
        b = self.desk()
        self.assertEqual(b.sessions[sid].driver.proc.pid, pid)
        b.send(sid, "next")
        c1 = self.card(b, sid, 1, ("completed",))
        self.assertEqual(c1["answer"], "slept")
        self.assertEqual(self.card(b, sid, 2, ("completed",))["answer"], "answer: next")
        got = logged(self.codex / "inputs.log")
        self.assertEqual([x["pid"] for x in got], [pid, pid])
        self.assertFalse(any("restarted" in x["text"] for x in got))
        read = [x["method"] for x in logged(self.codex / "stdin.log") if x["pid"] == pid]
        self.assertEqual(read.count("initialize"), 1)
        self.assertEqual(len([m for m in read if m.startswith("thread/")]), 1)
        self.assertEqual(read.count("turn/start"), 2)

    def test_codex_background_subagent_survives_a_restart(self):
        os.environ["FAKE_SUB_PAUSE"] = "3"
        a = self.desk()
        sid, pid = self.running(a, "codex", "subagents", states=("completed",))
        straggler = lambda d: next(st for st in d.cards(sid)[0]["steps"] if st.get("agent") == "straggler")
        self.assertFalse(straggler(a)["done"])
        self.handed(a, sid)
        b = self.desk()
        self.assertEqual(b.sessions[sid].public()["agents"], 1)  # still at work
        self.assertTrue(until(lambda: straggler(b)["done"], 15))
        st = straggler(b)
        self.assertFalse(st.get("lost"))
        self.assertEqual(st["result"], "straggler: done at last")
        self.assertEqual(b.sessions[sid].public()["agents"], 0)

    def test_codex_nested_subagent_after_a_restart(self):
        """A subagent's own subagent: its work after the restart still goes under the top one's step."""
        os.environ["FAKE_SUB_PAUSE"] = "3"
        a = self.desk()
        sid, _ = self.running(a, "codex", "nested", states=("completed",))
        subs = self.handed(a, sid)["driver"]["subs"].values()
        self.assertEqual(sorted(v["root"] is None for v in subs), [False, True])  # (top, and inner under it)
        b = self.desk()
        top = lambda: next(st for st in b.cards(sid)[0]["steps"] if st.get("agent") == "top")
        self.assertTrue(until(lambda: top()["done"], 15))
        self.assertEqual(top()["result"], "top: done, inner tested")
        self.assertIn("make test", [x.get("summary") for x in top()["sub"]])
        self.assertFalse(any(st.get("agent") == "inner" for st in b.cards(sid)[0]["steps"]))

    def test_codex_server_request_in_the_gap(self):
        """An approval asked while no server read it is answered by the next one (by mode), to the same process."""
        a = self.desk()
        sid, pid = self.running(a, "codex", "gapask 2", mode="bypassPermissions")
        self.handed(a, sid)
        self.assertTrue(until(lambda: not logged(self.codex / "answers.log") and
                              b'requestApproval' in self._journal(), 10))
        b = self.desk()
        self.assertEqual(self.card(b, sid, 1, ("completed",))["answer"], "answer: cmd=accept")
        self.assertEqual(logged(self.codex / "answers.log"), [{"cmd": "accept", "pid": pid}])

    def _journal(self) -> bytes:
        out = b""
        for f in sorted((self.state / "harness" / "procs").glob("*/journal.*")):
            for line in f.read_bytes().splitlines():
                try:
                    out += base64.b64decode(json.loads(line).get("b64") or "")
                except ValueError:
                    pass
        return out

    def test_codex_exec_is_not_handed_off(self):
        os.environ["ORBIT_CODEX_EXEC"] = "1"
        a = self.desk()
        sid, pid = self.running(a, "codex", "slow 5")
        self.assertTrue(a.sessions[sid].driver.exec)
        self.not_handed(a, sid)
        self.assertTrue(until(lambda: not alive(pid), 10))

    def test_codex_a_request_waiting_for_its_thread_is_not_handed_off(self):
        os.environ["FAKE_THREAD_DELAY"] = "4"
        a = self.desk()
        sid = a.create(self.work, harness="codex")["id"]
        drv = a.sessions[sid].driver
        self.assertTrue(until(lambda: drv._initialized))
        a.send(sid, "hello")
        self.assertTrue(until(lambda: drv._queued is not None))
        self.not_handed(a, sid)

    def test_codex_exec_turned_on_meanwhile(self):
        """ORBIT_CODEX_EXEC=1 at the next start: the process handed off is an app-server, read on as one."""
        a = self.desk()
        sid, pid = self.running(a, "codex", "slow 3")
        self.handed(a, sid)
        os.environ["ORBIT_CODEX_EXEC"] = "1"
        b = self.desk()
        drv = b.sessions[sid].driver
        self.assertEqual((drv.proc.pid, drv.exec), (pid, False))
        self.assertEqual(self.card(b, sid, 1, ("completed",))["answer"], "slept")

    # ------------------------------------------------------------------ agy
    def test_agy_restart_mid_request(self):
        a = self.desk()
        sid, pid = self.running(a, "agy", "slow 3")
        self.assertEqual(self.handed(a, sid)["harness"], "agy")
        b = self.desk()
        self.assertEqual(b.sessions[sid].driver.proc.pid, pid)
        b.send(sid, "next")
        self.assertEqual(self.card(b, sid, 1, ("completed",))["answer"], "slept")
        self.assertEqual(self.card(b, sid, 2, ("completed",))["answer"], "answer: next")
        got = logged(self.agy / "inputs.log")
        self.assertEqual([x["pid"] for x in got], [pid, pid])
        self.assertFalse(any("restarted" in x["text"] for x in got))
        self.assertEqual(len([x for x in logged(self.agy / "stdin.log") if x["pid"] == pid]), 2)  # (the requests only)

    # ------------------------------------------------------------------ OpenCode
    def test_opencode_restart_mid_request(self):
        """The same `opencode serve` reads on: the next server subscribes again (the address and password carried),
        the card completes there, and the next request goes to the same process and session."""
        a = self.desk()
        sid, pid = self.running(a, "opencode", "slow 3")
        h = self.handed(a, sid)
        self.assertEqual(h["harness"], "opencode")
        self.assertTrue(h["driver"]["base"].startswith("http://127.0.0.1:") and h["driver"]["password"])
        b = self.desk()
        self.assertEqual(b.sessions[sid].driver.proc.pid, pid)
        b.send(sid, "next")
        c1 = self.card(b, sid, 1, ("completed",))
        self.assertEqual(c1["answer"], "slept")
        self.assertEqual([st["summary"] for st in c1["steps"] if st["kind"] == "tool"], ["sleep 3"])  # (once)
        self.assertEqual(self.card(b, sid, 2, ("completed",))["answer"], "answer: next")
        got = logged(self.opencode / "inputs.log")
        self.assertEqual([x["pid"] for x in got], [pid, pid])
        self.assertEqual(len({x["conv"] for x in got}), 1)
        self.assertFalse(any("restarted" in x["text"] for x in got))

    def test_opencode_request_ended_in_the_gap(self):
        """A request that ended while no server listened ends when the next one catches up (its parts, then
        `/session/status`)."""
        a = self.desk()
        sid, pid = self.running(a, "opencode", "slow 1")
        self.handed(a, sid)
        time.sleep(2.5)  # (it ends meanwhile)
        b = self.desk()
        c = self.card(b, sid, 1, ("completed",))
        self.assertEqual(c["answer"], "slept")
        self.assertEqual(b.sessions[sid].driver.proc.pid, pid)

    def test_agy_an_answer_streamed_across_a_restart(self):
        """The deltas before the restart and after it make one answer (`parts` carried over)."""
        a = self.desk()
        sid, pid = self.running(a, "agy", "split 2", states=("thinking",))
        self.assertTrue(until(lambda: a.sessions[sid].driver.parts))  # (agy's deltas wait for the step's end)
        self.assertEqual(self.handed(a, sid)["driver"]["parts"], {"1": ["first half, "]})
        b = self.desk()
        self.assertEqual(self.card(b, sid, 1, ("completed",))["answer"], "first half, second half")

    # ------------------------------------------------------------------ a protocol agent
    def test_protocol_agent_restart_mid_request(self):
        """The same process reads on; the next server never runs `--describe` (the hand-off says what it said), and
        the agent's plugin token is still recognised."""
        self.plug()
        a = self.desk()
        sid, pid = self.running(a, "handfake", "slow 3")
        self.assertIn("described", self.handed(a, sid)["driver"])
        self.plug()  # (the next server loads its plugins again)
        described = len([x for x in logged(self.proto / "argv.log") if "--describe" in x])
        b = self.desk()
        self.assertEqual(b.sessions[sid].driver.proc.pid, pid)
        self.assertEqual(len([x for x in logged(self.proto / "argv.log") if "--describe" in x]), described)
        b.send(sid, "next")
        self.assertEqual(self.card(b, sid, 1, ("completed",))["answer"], "done")
        self.assertEqual(self.card(b, sid, 2, ("completed",))["answer"], "answer: next")
        read = [json.loads(x["line"]) for x in logged(self.proto / "stdin.log") if x["pid"] == pid]
        self.assertEqual([x["type"] for x in read], ["request", "request"])
        self.assertFalse(any("restarted" in x["text"] for x in read))
        from orbit import plugins
        token = next(x["token"] for x in logged(self.proto / "token.log") if x["pid"] == pid)
        self.assertEqual(plugins.TOKENS.get(token), "handco")

    def test_protocol_agent_whose_plugin_is_gone(self):
        """B cannot attach it (no such harness now): cut off, its process ended with the unattached ones."""
        self.plug()
        a = self.desk()
        sid, pid = self.running(a, "handfake", "slow 6")
        self.handed(a, sid)
        from orbit import plugins
        plugins.unload()
        b = self.desk()
        self.assertFalse(alive(pid))
        self.assertEqual(b.cards(sid)[0]["state"], "paused")
        self.assertTrue(b.sessions[sid].interrupted)

    # ------------------------------------------------------------------ the Desk
    def test_no_stop_or_steer_while_stopping(self):
        from orbit.desk.session import Stopping
        d = self.desk(hosted=False)
        sid, _ = self.running(d, "claude", "slow")
        d.stopping_all = True
        with self.assertRaises(Stopping):
            d.stop(sid)
        with self.assertRaises(Stopping):
            d.steer(sid, "also this")
        d.stopping_all = False
        d.stop(sid)
        self.card(d, sid, 1, ("cancelled",))

    def test_catching_up_ends_only_at_live(self):
        from orbit.drivers.base import DriverEvent
        d = self.desk(hosted=False)
        sid = d.create(self.work)["id"]
        s = d.sessions[sid]
        self.assertTrue(until(lambda: s.state == "ready"))
        s.catching_up = True
        d._on_event(s, DriverEvent("state", {"state": "ready", "error": "a protocol agent's error"}))
        self.assertTrue(s.catching_up)
        d._on_event(s, DriverEvent("state", {"state": "ready", "live": True}))
        self.assertFalse(s.catching_up)
        s.catching_up = True
        d._on_event(s, DriverEvent("state", {"state": "not ready", "error": "gone"}))
        self.assertFalse(s.catching_up)

    def test_resume_command_says_whether_its_agent_is_alive(self):
        d = self.desk()
        sid = d.create(self.work)["id"]
        d.send(sid, "hello")
        self.card(d, sid, 1, ("completed",))
        self.assertTrue(d.resume_command(sid)["alive"])  # (open between requests)
        d.sessions[sid].catching_up = True
        old, d.sessions[sid].driver = d.sessions[sid].driver, None
        self.assertTrue(d.resume_command(sid)["alive"])  # (being read on after a restart)
        d.sessions[sid].catching_up = False
        old.close()
        self.assertFalse(d.resume_command(sid)["alive"])

    def test_plugin_tokens_survive_a_restart(self):
        from orbit import plugins
        self.plug()
        first = {n: t for t, n in plugins.TOKENS.items()}
        self.plug()
        self.assertEqual({n: t for t, n in plugins.TOKENS.items()}, first)
        f = self.state / "plugin-tokens.json"
        self.assertEqual(json.loads(f.read_text()), first)
        self.assertEqual(os.stat(f).st_mode & 0o777, 0o600)


class CheckpointTest(unittest.TestCase):
    """Every driver's checkpoint is plain JSON: restored on a fresh instance, it checkpoints the same."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._exec = os.environ.pop("ORBIT_CODEX_EXEC", None)

    def tearDown(self):
        if self._exec is not None:
            os.environ["ORBIT_CODEX_EXEC"] = self._exec
        self._tmp.cleanup()

    def make(self, cls):
        return cls(session_id="s1", cwd=self.tmp, model="", mode="default", emit=lambda e: None, resume=True,
                   state_dir=self.tmp)

    def round_trip(self, drv):
        cp = drv.checkpoint()
        fresh = self.make(type(drv))
        fresh.restore(json.loads(json.dumps(cp)))
        fresh.busy = cp["busy"]  # (attach() takes it with the process)
        self.assertEqual(fresh.checkpoint(), cp)
        return fresh

    def test_claude(self):
        from orbit.drivers.claude import ClaudeDriver
        d = self.make(ClaudeDriver)
        d.agent_calls, d.turn_text, d.busy = {"t2", "t1"}, ["so far"], True
        self.assertEqual(self.round_trip(d).agent_calls, {"t1", "t2"})

    def test_codex_with_a_nested_subagent(self):
        from orbit.drivers.codex import CodexDriver
        d = self.make(CodexDriver)
        d._rpc_id, d._calls, d._thread, d._model, d._turn = 7, {7: "turn/steer"}, "th", "m", "turn-1"
        d.started_items, d._rollouts = {"b", "a"}, {"th": "/x/rollout-th.jsonl"}
        top = {"id": "call_top", "name": "top", "done": False, "last": "", "steps": {"s2", "s1"}, "root": None}
        d._subs = {"sub-th": top, "inner-th": {"id": "call_top", "root": top, "done": False}}
        fresh = self.round_trip(d)
        self.assertEqual(fresh._calls, {7: "turn/steer"})
        self.assertIs(fresh._subs["inner-th"]["root"], fresh._subs["sub-th"])
        self.assertTrue(fresh._initialized and not fresh.exec)
        with self.assertRaises(ValueError):  # (an exec process is never handed off)
            self.make(CodexDriver).restore({**d.checkpoint(), "exec": True})

    def test_agy(self):
        from orbit.drivers.agy import AgyDriver
        d = self.make(AgyDriver)
        d.conversation, d.parts, d.started_steps = "conv", {3: ["a", "b"]}, {2, 1}
        fresh = self.round_trip(d)
        self.assertEqual((fresh.parts, fresh.started_steps), ({3: ["a", "b"]}, {1, 2}))

    def test_opencode(self):
        from orbit.drivers.opencode import OpenCodeDriver
        d = self.make(OpenCodeDriver)
        d.base, d.conv, d.commands, d._sent, d._said, d._subs = "http://127.0.0.1:4096", "ses_1", ["review"], True, {"p2", "p1"}, {"ses_2": "p3"}
        d._task_steps, d._asks, d.turn_text, d.busy = {"p3"}, {"per_1": "OpenCode asks permission: bash"}, ["so far"], True
        fresh = self.round_trip(d)
        self.assertEqual((fresh._said, fresh._subs, fresh.password, fresh._open), ({"p1", "p2"}, {"ses_2": "p3"}, d.password, True))

    def test_protocol(self):
        from orbit.drivers.protocol import protocol_driver
        cls = protocol_driver("cpfake", {"command": ["/bin/false"]})
        said = {"protocol": 1, "capabilities": {"steer": True, "terminal": True}, "efforts": ["low"]}
        cls.primed({"described": said})  # (what the hand-off carries: never `--describe`)
        self.assertEqual((cls.described, cls.steers, cls.has_terminal, cls.efforts), (said, True, True, ("low",)))
        d = self.make(cls)
        d.seq, d.req_id, d.conversation, d.terminal = 3, "r3", "conv", ["sh", "-c", "cat"]
        self.assertEqual(self.round_trip(d).req_id, "r3")

    def test_codex_safety(self):
        """can_hand_off: the handshake done, the thread open, nothing queued for it, no Stop pending, a running
        turn's id known; never exec."""
        from orbit.drivers.codex import CodexDriver

        class Proc:  # (a hosted process, as far as can_hand_off looks)
            detach = None

            def poll(self):
                return None
        d = self.make(CodexDriver)
        d.proc, d.ready, d._initialized, d._thread = Proc(), True, True, "th"
        self.assertTrue(d.can_hand_off())
        for k, v in (("_queued", "x"), ("_stop_pending", True), ("_initialized", False), ("_thread", None)):
            old = getattr(d, k)
            setattr(d, k, v)
            self.assertFalse(d.can_hand_off(), k)
            setattr(d, k, old)
        d.busy, d.req_proc = True, d.proc
        self.assertFalse(d.can_hand_off())  # (turn/start not answered yet)
        d._turn = "turn-1"
        self.assertTrue(d.can_hand_off())
        d._set_exec(True)
        self.assertFalse(d.can_hand_off())

    def test_the_stop_timer_leaves_a_detached_process_alone(self):
        from orbit.drivers import process
        from orbit.drivers.claude import ClaudeDriver
        sent = []

        class Pipe:
            closed = False

            def write(self, s):
                pass

            def flush(self):
                pass

        class Proc:
            detached, stdin = False, Pipe()

            def poll(self):
                return None

            def send_signal(self, sig):
                sent.append(sig)

            def kill(self):
                sent.append("kill")

            def wait(self, t=None):
                raise subprocess.TimeoutExpired("x", t)
        d = self.make(ClaudeDriver)
        d.proc = d.req_proc = Proc()
        d.ready = d.busy = True
        old, process.STOP_GRACE = process.STOP_GRACE, 0.1
        try:
            d.stop()
            d.proc.detached = True  # (handed off meanwhile)
            time.sleep(0.4)
        finally:
            process.STOP_GRACE = old
        self.assertEqual(sent, [])


class AgentRetryTest(unittest.TestCase):
    def test_orbit_agent_retries_a_refused_connection(self):
        """An agent that outlives a restart may call orbit-agent in the gap: it waits for the server (30 s at most)."""
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from orbit.cli import agent
        port = free_port()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = []

        def later():
            time.sleep(1.0)
            srv.append(HTTPServer(("127.0.0.1", port), H))
            srv[0].handle_request()
        th = threading.Thread(target=later, daemon=True)
        th.start()
        old = os.environ.get("ORBIT_PORT")
        os.environ["ORBIT_PORT"] = str(port)
        try:
            t = time.time()
            self.assertEqual(agent.call("GET", "/api/x"), {"ok": True})
            self.assertGreater(time.time() - t, 0.9)
        finally:
            th.join(5)
            if srv:
                srv[0].server_close()
            if old is None:
                os.environ.pop("ORBIT_PORT", None)
            else:
                os.environ["ORBIT_PORT"] = old


if __name__ == "__main__":
    unittest.main()
