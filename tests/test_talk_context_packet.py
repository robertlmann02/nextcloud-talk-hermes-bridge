import importlib
import os
import sqlite3
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

    def test_local_memory_defaults_raw_message_retrieval_to_current_room(self):
        db_path = os.path.join(self.tmp.name, "memory.sqlite3")
        marker = "room_scope_marker"
        room_a = "room-a"
        room_b = "room-b"
        msg_a = f"{marker} alpha room only"
        msg_b = f"{marker} beta other room should not leak"
        with mock.patch.dict(
            os.environ,
            {
                "TALK_LOCAL_MEMORY_CONTEXT": "1",
                "TALK_MEMORY_DB_PATH": db_path,
                "TALK_MEMORY_RETRIEVAL_SCOPE": "room",
            },
            clear=False,
        ):
            self.mod.sync_local_memory_message(room_a, "user", "Alex", msg_a, namespace="unit", message_id=101)
            self.mod.sync_local_memory_message(room_b, "user", "Alex", msg_b, namespace="unit", message_id=102)
            packet_a = self.mod.build_local_memory_context(marker, room_a, namespace="unit", limit=10)
            packet_b = self.mod.build_local_memory_context(marker, room_b, namespace="unit", limit=10)

        self.assertIn("LOCAL SQLITE MEMORY CONTEXT", packet_a)
        self.assertIn("Retrieval scope: room. Current Talk session_id: talk_room-a.", packet_a)
        self.assertIn(msg_a, packet_a)
        self.assertNotIn(msg_b, packet_a)
        self.assertIn(msg_b, packet_b)
        self.assertNotIn(msg_a, packet_b)

    def test_workspace_scope_escape_hatch_can_cross_room_retrieve(self):
        db_path = os.path.join(self.tmp.name, "memory.sqlite3")
        marker = "workspace_scope_marker"
        msg_a = f"{marker} alpha room"
        msg_b = f"{marker} beta room"
        with mock.patch.dict(
            os.environ,
            {
                "TALK_LOCAL_MEMORY_CONTEXT": "1",
                "TALK_MEMORY_DB_PATH": db_path,
                "TALK_MEMORY_RETRIEVAL_SCOPE": "workspace",
            },
            clear=False,
        ):
            self.mod.sync_local_memory_message("room-a", "user", "Alex", msg_a, namespace="unit", message_id=201)
            self.mod.sync_local_memory_message("room-b", "user", "Alex", msg_b, namespace="unit", message_id=202)
            packet = self.mod.build_local_memory_context(marker, "room-a", namespace="unit", limit=10)

        self.assertIn("Retrieval scope: workspace. Current Talk session_id: talk_room-a.", packet)
        self.assertIn(msg_a, packet)
        self.assertIn(msg_b, packet)

    def test_room_scoped_retrieval_keeps_durable_namespace_memories(self):
        db_path = os.path.join(self.tmp.name, "memory.sqlite3")
        marker = "durable_scope_marker"
        with mock.patch.dict(
            os.environ,
            {
                "TALK_LOCAL_MEMORY_CONTEXT": "1",
                "TALK_MEMORY_DB_PATH": db_path,
                "TALK_MEMORY_RETRIEVAL_SCOPE": "room",
            },
            clear=False,
        ):
            conn = sqlite3.connect(db_path)
            self.mod._init_memory_tables(conn)
            conn.execute(
                "INSERT INTO memories(id, namespace, content, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("mem1", "unit", f"{marker} durable namespace fact", "active", self.mod._now_iso(), self.mod._now_iso()),
            )
            conn.execute(
                "INSERT INTO memories_fts(id, namespace, memory_type, content, source) VALUES (?, ?, ?, ?, ?)",
                ("mem1", "unit", "fact", f"{marker} durable namespace fact", "test"),
            )
            conn.commit()
            conn.close()
            packet = self.mod.build_local_memory_context(marker, "room-a", namespace="unit", limit=10)

        self.assertIn(f"{marker} durable namespace fact", packet)


if __name__ == "__main__":
    unittest.main()
