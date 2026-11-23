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
        d.handle({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "thinking"}}})
        d.handle({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hmm"}}})
        d.handle({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}}})
        d.handle({"type": "stream_event", "parent_tool_use_id": "t1",
                   "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "sub"}}})
        self.assertEqual([(e.kind, e.data.get("text")) for e in got], [("thinking", ""), ("thinking", "hmm"), ("delta", "Hel")])


if __name__ == "__main__":
    unittest.main()
