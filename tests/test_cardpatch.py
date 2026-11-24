"""Card patches (orbit/desk/cardpatch.py): a streaming card's changes go to clients that ask (?delta=1) as what changed,
not the whole card each time; the others get whole cards as before."""

import copy
import json
import os
import threading
import time
import unittest
import urllib.request

import test_skeleton
from orbit.desk import cardpatch


class OpsTest(unittest.TestCase):
    def roundtrip(self, old, new):
        ops = cardpatch.ops(old, new)
        self.assertEqual({**cardpatch.apply(old, ops), "rev": new.get("rev")}, {**new, "rev": new.get("rev")}, ops)
        return ops

    def test_text_grows_steps_come_and_change(self):
        old = {"id": 3, "sid": "s", "state": "running", "rev": 4, "answer": "Hel", "steps": [
            {"kind": "text", "text": "abc", "done": False}, {"kind": "tool", "name": "Bash", "done": False}]}
        new = copy.deepcopy(old)
        new.update(rev=5, answer="Hello", tokens=12)
        new["steps"][0]["text"] = "abcdef"
        new["steps"][1]["done"] = True
        new["steps"][1]["output"] = "ok"
        new["steps"].append({"kind": "text", "text": "next", "done": False})
        ops = self.roundtrip(old, new)
        self.assertIn(["a", "answer", "lo"], ops)
        self.assertIn(["sa", 0, "text", "def"], ops)  # (only what was added)
        self.assertIn(["ss", 1, "done", True], ops)
        self.assertIn(["S", 2, new["steps"][2]], ops)
        self.assertFalse([o for o in ops if o[0] == "s" and o[1] == "rev"])  # (the revision is the patch's head)

    def test_removed_fields_fewer_steps_and_replaced_values(self):
        old = {"id": 1, "state": "running", "rev": 1, "error": "x", "followups": [1], "steps": [{"a": 1}, {"b": 2}, {"c": 3}]}
        new = {"id": 1, "state": "running", "rev": 2, "followups": [1, 2], "steps": [{"a": 1}, {"b": 5}]}
        ops = self.roundtrip(old, new)
        self.assertIn(["d", "error"], ops)
        self.assertIn(["L", 2], ops)
        self.assertIn(["s", "followups", [1, 2]], ops)
        self.roundtrip({"id": 1, "rev": 1, "state": "running"}, {"id": 1, "rev": 2, "state": "running", "steps": [{"x": 1}]})
        self.roundtrip({"id": 1, "rev": 1, "steps": [{"x": "abc"}]}, {"id": 1, "rev": 2, "steps": [{"x": "zbc"}, "odd"]})

    def test_whole_when_state_changes_or_no_base(self):
        a = {"id": 1, "sid": "s", "state": "running", "rev": 1}
        self.assertIsNone(cardpatch.patch(None, a))
        self.assertIsNone(cardpatch.patch(a, {**a, "state": "completed", "rev": 2}, {"running", "thinking"}))
        self.assertEqual(cardpatch.patch(a, {**a, "state": "thinking", "rev": 2}, {"running", "thinking"})["ops"],
                         [["s", "state", "thinking"]])  # (between states of work: patched)
        p = cardpatch.patch(a, {**a, "rev": 2, "answer": "x"})
        self.assertEqual((p["t"], p["base"], p["rev"], p["ops"]), ("cardp", 1, 2, [["s", "answer", "x"]]))


class StreamTest(test_skeleton.SkeletonTest):
    def follow(self, delta, out, stop):
        def run():
            url = f"http://127.0.0.1:{self.port}/api/events" + ("?delta=1" if delta else "")
            with urllib.request.urlopen(url, timeout=30) as r:
                for line in r:
                    if line.startswith(b"data: "):
                        out.append(line[6:].rstrip(b"\n"))
                    if stop.is_set():
                        return
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return th

    def test_a_delta_client_builds_the_same_card_from_far_less(self):
        """A flood of text and tool steps: the client with patches ends with exactly the card the server has, from a
        fraction of the bytes the whole-card client got; a patch it cannot apply makes it fetch that card."""
        os.environ["FAKE_FLOOD"] = "12"
        try:
            s = self.new_session("Flood")
            delta, full, stop = [], [], threading.Event()
            ths = [self.follow(True, delta, stop), self.follow(False, full, stop)]
            time.sleep(0.3)
            self.req("POST", f"/api/sessions/{s['id']}/send", {"text": "flood please"})
            final = self.wait_card(s["id"], 1, ("completed",), timeout=30)
            time.sleep(0.5)
        finally:
            os.environ.pop("FAKE_FLOOD", None)
            stop.set()
        mine = lambda raw: [json.loads(x) for x in raw if b'"sid": "' + s["id"].encode() + b'"' in x]
        d_evs = [e for e in mine(delta) if e["t"] in ("card", "cardp") and e.get("card", e).get("id", e.get("id")) == 1]
        f_evs = [e for e in mine(full) if e["t"] == "card" and e["card"]["id"] == 1]
        self.assertFalse([e for e in mine(full) if e["t"] == "cardp"])  # (a client that did not ask: whole cards only)
        self.assertGreater(sum(e["t"] == "cardp" for e in d_evs), 10)
        card = None
        for e in d_evs:
            if e["t"] == "card":
                card = e["card"]
            else:
                self.assertEqual(card["rev"], e["base"])  # (each patch starts from the version before it)
                card = {**cardpatch.apply(card, e["ops"]), "rev": e["rev"]}
        _, got = self.req("GET", f"/api/sessions/{s['id']}/cards/1?exact=1")
        self.assertEqual(card, got["card"])
        self.assertEqual(card, final)
        d_bytes = sum(len(x) for x in delta if b'"sid": "' + s["id"].encode() + b'"' in x)
        f_bytes = sum(len(x) for x in full if b'"sid": "' + s["id"].encode() + b'"' in x)
        self.assertLess(d_bytes, f_bytes * 0.15, (d_bytes, f_bytes))
        self.assertEqual(self.req("GET", f"/api/sessions/{s['id']}/cards/99?exact=1")[0], 404)


if __name__ == "__main__":
    unittest.main()
