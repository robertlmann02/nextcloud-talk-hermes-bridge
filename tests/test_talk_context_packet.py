import importlib
import os
import tempfile
import unittest
from unittest import mock


class TalkContextPacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"TALK_CONTEXT_DIR": self.tmp.name}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.mod = importlib.import_module("nextcloud_talk_hermes_bridge.talk_context")

    def test_large_memory_context_preserves_packet_header(self):
        large_memory = "MEMORY-START\n" + ("memory row\n" * 500)
        with mock.patch.object(self.mod, "MAX_PACKET_CHARS", 1300), mock.patch.object(
            self.mod, "build_local_memory_context", return_value=large_memory
        ):
            packet = self.mod.build_context_packet(
                "room-token",
                "unit-test-app",
                "Unit Assistant",
                current_message="remember the customer install estimate",
                namespace="unit",
            )

        self.assertLessEqual(len(packet), 1300)
        self.assertTrue(packet.startswith("NEXTCLOUD TALK CONTEXT PACKET"))
        self.assertIn("Assistant/persona for this bridge: Unit Assistant.", packet)
        self.assertIn("Do not mix identities across assistants, profiles, or users.", packet)
        self.assertIn("LOCAL MEMORY CONTEXT:", packet)
        self.assertIn("...[local memory context truncated]", packet)
    def test_context_packet_prioritizes_current_message_over_stale_room_state(self):
        self.mod.append_turn("room-token", "user", "Alex", "Draft an email about printer setup", 1, app_name="unit-test-app")
        self.mod.append_turn("room-token", "assistant", "Unit Assistant", "Here is the email draft", 0, app_name="unit-test-app")

        current = "What did we have on the GitHub page before for new Linux users?"
        with mock.patch.object(self.mod, "build_local_memory_context", return_value=""):
            packet = self.mod.build_context_packet(
                "room-token",
                "unit-test-app",
                "Unit Assistant",
                current_message=current,
                namespace="unit",
            )

        self.assertIn("CONTEXT PRIORITY / STALE-CONTEXT GUARD", packet)
        self.assertIn("Newest user message controls the task", packet)
        self.assertIn(f"Current user message (highest priority): {current}", packet)
        self.assertIn("Working room state (lower priority; may be stale):", packet)
        self.assertIn("session_search for prior wording", packet)
        self.assertIn("git history as source of truth", packet)


if __name__ == "__main__":
    unittest.main()
