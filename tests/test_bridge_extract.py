import importlib
import os
import unittest
from unittest import mock


def load_bridge():
    os.environ.setdefault("TALK_BOT_SECRET", "test-secret")
    os.environ.setdefault("NEXTCLOUD_URL", "https://nextcloud.example.test")
    return importlib.import_module("nextcloud_talk_hermes_bridge.bridge")


def base_payload(content, object_type="Note", actor=None, activity_type="Create"):
    return {
        "type": activity_type,
        "actor": actor or {"id": "users/alex", "name": "Alex"},
        "object": {"type": object_type, "id": 42, "content": content},
        "target": {"id": "room-token"},
    }


class BridgeExtractTests(unittest.TestCase):
    def test_extract_text_note(self):
        bridge = load_bridge()
        ev = bridge.extract(base_payload('{"message":"Hello {@mention}"}'))
        self.assertEqual(
            ev,
            {
                "token": "room-token",
                "message": "Hello",
                "message_id": 42,
                "actor_name": "Alex",
            },
        )

    def test_extract_ignores_bot_actor(self):
        bridge = load_bridge()
        payload = base_payload('{"message":"loop"}', actor={"id": "bots/assistant", "name": "Bot"})
        self.assertIsNone(bridge.extract(payload))

    def test_extract_accepts_file_shared_voice_non_note_without_actor_name_crash(self):
        bridge = load_bridge()
        payload = base_payload(
            '{"message":"file_shared","parameters":{"share":"123","metaData":{"messageType":"voice-message","mimeType":"audio/ogg"}}}',
            object_type="File",
            actor={"id": "users/alex"},
        )
        ev = bridge.extract(payload)
        self.assertEqual(ev["actor_name"], "User")
        self.assertIn("voice-message", ev["message"])
        self.assertIn("audio/ogg", ev["message"])

    def test_extract_accepts_non_create_file_shared_voice(self):
        bridge = load_bridge()
        payload = base_payload(
            '{"message":"file_shared","parameters":{"share":"123","metaData":{"messageType":"voice-message","mimeType":"audio/mpeg"}}}',
            object_type="File",
            actor={"id": "users/alex", "name": "Alex"},
            activity_type="Update",
        )
        ev = bridge.extract(payload)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["actor_name"], "Alex")
        self.assertIn("voice-message", ev["message"])
        self.assertIn("audio/mpeg", ev["message"])

    def test_extract_accepts_rendered_file_placeholder_payload(self):
        bridge = load_bridge()
        payload = base_payload(
            '{"message":"{file}","parameters":{"file":{"type":"file","name":"Talk recording.mp3","path":"/Talk/Talk recording.mp3","mimetype":"audio/mpeg"}}}',
            actor={"id": "users/alex", "name": "Alex"},
            activity_type="Activity",
        )
        ev = bridge.extract(payload)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["actor_name"], "Alex")
        self.assertIn("audio/mpeg", ev["message"])
        self.assertIn("Talk recording.mp3", ev["message"])

    def test_extract_transcribes_audio_when_local_transcription_available(self):
        bridge = load_bridge()
        payload = base_payload(
            '{"message":"{file}","parameters":{"file":{"type":"file","name":"Talk recording.ogg","path":"/Talk/Talk recording.ogg","mimetype":"audio/ogg"}}}',
            activity_type="Activity",
        )
        with mock.patch.object(bridge, "transcribe_from_talk_params", return_value="hello from the voice note"):
            ev = bridge.extract(payload)
        self.assertIn("Transcription: hello from the voice note", ev["message"])

    def test_extract_adds_vision_context_for_image_payload(self):
        bridge = load_bridge()
        payload = base_payload(
            '{"message":"{file}","parameters":{"file":{"type":"file","name":"photo.jpg","path":"/Talk/photo.jpg","mimetype":"image/jpeg"}}}',
            activity_type="Activity",
        )
        with mock.patch.object(
            bridge,
            "describe_talk_image_for_vision",
            return_value=(
                "Local readable Talk image file for Hermes vision: /tmp/photo.jpg\n"
                "Instruction: before answering about this upload, call the vision_analyze tool on that local image path."
            ),
        ):
            ev = bridge.extract(payload)
        self.assertIn("image/jpeg", ev["message"])
        self.assertIn("Local readable Talk image file for Hermes vision: /tmp/photo.jpg", ev["message"])
        self.assertIn("vision_analyze", ev["message"])

    def test_extract_keeps_image_event_when_local_copy_unavailable(self):
        bridge = load_bridge()
        payload = base_payload(
            '{"message":"{file}","parameters":{"file":{"type":"file","name":"photo.png","path":"/Talk/photo.png","mimetype":"image/png"}}}',
            activity_type="Activity",
        )
        with mock.patch.object(bridge, "describe_talk_image_for_vision", return_value=""):
            ev = bridge.extract(payload)
        self.assertIn("This appears to be an image", ev["message"])

    def test_extract_rejects_unrelated_non_create_event(self):
        bridge = load_bridge()
        payload = base_payload('{"message":"not a share"}', activity_type="Update")
        self.assertIsNone(bridge.extract(payload))

    def test_extract_rejects_unrelated_non_note_object(self):
        bridge = load_bridge()
        payload = base_payload("plain unrelated payload", object_type="CalendarObject")
        self.assertIsNone(bridge.extract(payload))
    def test_ask_includes_nextcloud_ai_context_when_available(self):
        bridge = load_bridge()
        popen_result = mock.Mock()
        popen_result.communicate.return_value = ("Hermes final reply", "")
        popen_result.returncode = 0
        with mock.patch.object(bridge, "build_nextcloud_ai_context", return_value="NEXTCLOUD AI / DOCUMENT CONTEXT\nCandidate files:\n1. Manual.pdf"):
            with mock.patch.object(bridge.subprocess, "Popen", return_value=popen_result) as popen:
                reply = bridge.ask("find the manual PDF", "Alex", "BASE CONTEXT", token="room-token", reply_to=42)
        self.assertEqual(reply, "Hermes final reply")
        cmd = popen.call_args.args[0]
        prompt = cmd[cmd.index("-q") + 1]
        self.assertIn("BASE CONTEXT", prompt)
        self.assertIn("NEXTCLOUD AI / DOCUMENT CONTEXT", prompt)
        self.assertIn("Manual.pdf", prompt)

    def test_ask_exposes_skills_toolset_and_skill_status_rule(self):
        bridge = load_bridge()
        popen_result = mock.Mock()
        popen_result.communicate.return_value = ("Done", "")
        popen_result.returncode = 0
        with mock.patch.object(bridge.subprocess, "Popen", return_value=popen_result) as popen:
            bridge.ask("save this as a skill", "Alex", "", token="room-token", reply_to=42)
        cmd = popen.call_args.args[0]
        self.assertIn("--toolsets", cmd)
        self.assertIn("skills", cmd[cmd.index("--toolsets") + 1])
        prompt = cmd[cmd.index("-q") + 1]
        self.assertIn("Skill-management visibility rule", prompt)
        self.assertIn("Skills changed:", prompt)
        self.assertIn("Name every skill changed", prompt)


if __name__ == "__main__":
    unittest.main()
