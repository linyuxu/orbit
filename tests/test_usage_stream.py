"""Plan usage parsing (the status bar's "Used") and Claude Code's streamed deltas."""

import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from orbit.services import usage
from orbit.drivers.claude import ClaudeDriver

CLAUDE_USAGE = """You are currently using your subscription to power your Claude Code usage

Current week (all models): 94% used · resets Nov 22 at 2am (America/Los_Angeles)
Current week (Fable): 86% used · resets Nov 22 at 1:59am (America/Los_Angeles)

What's contributing to your usage?
"""


class UsageTest(unittest.TestCase):
    def test_claude_usage_lines(self):
        u = usage.parse_claude(CLAUDE_USAGE)
        self.assertEqual(u["plan"], "subscription")
        self.assertEqual([(x["label"], x["used"]) for x in u["limits"]], [("wk", 94.0), ("Fable", 86.0)])
        at = datetime.fromtimestamp(u["limits"][0]["resets_at"])
        self.assertEqual((at.month, at.day, at.hour, at.minute), (11, 22, 2, 0))

    def test_resets_parsing(self):
        now = datetime(2026, 11, 21, 13, 0)
        self.assertEqual(datetime.fromtimestamp(usage._resets_at("4:30pm", now)), datetime(2026, 11, 21, 16, 30))
        self.assertEqual(datetime.fromtimestamp(usage._resets_at("9am (UTC)", now)), datetime(2026, 11, 22, 9, 0))
        self.assertIsNone(usage._resets_at("whenever", now))

    def test_agy_usage_lines(self):
        """agy -p /usage (Antigravity): tab-separated windows, "Remaining" turned into used, the tightest first; a fake
        agy stands in (exit 2: an API key, no account quota: not shown)."""
        text = ("Gemini Models\tWeekly Limit Remaining\t99%\t2026-12-01T04:52:00Z\n"
                "Gemini Models\tFive Hour Limit Remaining\t100%\t2026-11-24T21:29:19Z\n"
                "Claude and GPT models\tWeekly Limit Remaining\t40%\t2026-12-01T16:29:19Z\n"
                "Claude and GPT models\tFive Hour Limit Remaining\t0%\t2026-11-24T21:29:19Z\n\n")
        lim = usage.parse_agy(text)
        self.assertEqual([(x["label"], x["used"]) for x in lim],
                         [("Claude/GPT 5h", 100.0), ("Claude/GPT wk", 60.0), ("Gemini wk", 1.0), ("Gemini 5h", 0.0)])
        self.assertEqual(lim[2]["resets_at"], datetime.fromisoformat("2026-12-01T04:52:00+00:00").timestamp())
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "agy"
            (Path(d) / "out.txt").write_text(text)
            for body, want in ((f"cat '{d}/out.txt'", "limits"),
                               ("echo 'needs an account' >&2; exit 2", "missing"), ("echo boom >&2; exit 1", "error")):
                exe.write_text(f"#!/bin/sh\n[ \"$1 $2\" = \"-p /usage\" ] || exit 9\n{body}\n")
                exe.chmod(0o755)
                with mock.patch.dict(os.environ, {"ORBIT_AGY_USAGE_BIN": str(exe)}):
                    u = usage.agy_usage()
                self.assertEqual(u["label"], "Antigravity")
                if want == "limits":
                    self.assertEqual(len(u["limits"]), 4)
                else:
                    self.assertTrue(u.get(want), u)

    def test_codex_rate_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "sessions" / "2026" / "11" / "21"
            d.mkdir(parents=True)
            later = time.time() + 3600
            ev = {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                "primary": {"used_percent": 39.0, "window_minutes": 10080, "resets_at": later},
                "secondary": {"used_percent": 12.0, "window_minutes": 300, "resets_at": time.time() - 10},
                "plan_type": "prolite"}}}
            (d / "rollout-x.jsonl").write_text(json.dumps({"type": "session_meta"}) + "\n" + json.dumps(ev) + "\n")
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                u = usage.codex_usage()
        self.assertEqual(u["plan"], "Prolite")
        self.assertEqual([(x["label"], x["used"]) for x in u["limits"]], [("wk", 39.0), ("5h", 0.0)])  # 5h has reset


class StreamTest(unittest.TestCase):
    def test_partial_messages_become_deltas(self):
        got = []
        d = ClaudeDriver(session_id="x", cwd="/tmp", model="", mode="default", emit=got.append, resume=False)
        self.assertIn("--include-partial-messages", d.command())
        d.busy = True  # (in a request: while idle, the first of them would begin a turn of its own, announced by `auto`)
        d.handle({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "thinking"}}})
        d.handle({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hmm"}}})
        d.handle({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}}})
        d.handle({"type": "stream_event", "parent_tool_use_id": "t1",
                   "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "sub"}}})
        self.assertEqual([(e.kind, e.data.get("text")) for e in got], [("thinking", ""), ("thinking", "hmm"), ("delta", "Hel")])


if __name__ == "__main__":
    unittest.main()
