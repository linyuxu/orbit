"""External Codex conversations keep their history when rollout filenames change."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orbit.desk import Desk
from orbit.drivers.codex import CodexDriver
from orbit.drivers.codex_store import find_rollout
from orbit.drivers.external import codex_sessions


class CodexStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sessions = self.root / "sessions [copied]"
        self.sessions.mkdir()
        env = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root / "home"),
                                          "ORBIT_CODEX_SESSIONS": str(self.sessions)})
        env.start()
        self.addCleanup(env.stop)

    def rollout(self, filename, thread, text="original request", mtime=100):
        path = self.sessions / "2026" / "11" / "14" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"type": "session_meta", "payload": {"id": thread, "cwd": str(self.root)}},
            {"type": "response_item", "payload": {"role": "user", "content": [
                {"type": "input_text", "text": text}]}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "UserMessage", "content": [{"text": text}]}}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "AgentMessage", "content": [{"text": "original answer"}]}}},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        ]
        path.write_text("\n".join(map(json.dumps, rows)) + "\n")
        os.utime(path, (mtime, mtime))
        return str(path)

    def test_attach_suffixed_rollout_and_reload_history(self):
        thread = "original-thread"
        self.rollout(f"rollout-2026-11-14-{thread}_continuation.jsonl", thread)
        discovered = codex_sessions()
        self.assertEqual([(x["id"], x["first"]) for x in discovered], [(thread, "original request")])
        state = self.root / "state"
        desk = Desk(state)
        self.addCleanup(desk.shutdown)
        with mock.patch.object(CodexDriver, "start"):
            session = desk.attach("codex", thread, discovered[0]["cwd"], group="main")
        cards = desk.cards(session["id"])
        self.assertEqual([(c["text"], c["answer"]) for c in cards], [("original request", "original answer")])
        self.assertEqual(desk.get(session["id"]).driver.linked(), thread)
        opening = json.loads(desk.get(session["id"]).driver._open_thread())
        self.assertEqual(opening["method"], "thread/resume")
        self.assertEqual(opening["params"]["threadId"], thread)
        reloaded = Desk(state)
        self.addCleanup(reloaded.shutdown)
        self.assertEqual(reloaded.cards(session["id"]), cards)

    def test_metadata_rejects_filename_collision(self):
        wanted = self.rollout("rollout-2026-original-thread_copy.jsonl", "original-thread")
        self.rollout("rollout-2026-original-thread.jsonl", "different-thread", mtime=200)
        self.assertEqual(find_rollout("original-thread"), wanted)

    def test_renamed_file_discovered_and_resolved_by_metadata(self):
        wanted = self.rollout("rollout-imported.jsonl", "original-thread")
        self.assertEqual(codex_sessions()[0]["id"], "original-thread")
        self.assertEqual(find_rollout("original-thread"), wanted)

    def test_newest_matching_rollout(self):
        self.rollout("rollout-2026-original-thread.jsonl", "original-thread")
        latest = self.rollout("rollout-2026-original-thread_continuation.jsonl", "original-thread", mtime=200)
        self.assertEqual(find_rollout("original-thread"), latest)

    def test_wrong_metadata_and_missing_thread_return_none(self):
        self.rollout("rollout-2026-original-thread.jsonl", "different-thread")
        self.assertIsNone(find_rollout("original-thread"))
        self.assertIsNone(find_rollout(""))


if __name__ == "__main__":
    unittest.main()
