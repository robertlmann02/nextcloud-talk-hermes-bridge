import importlib
import hmac
import hashlib
import os
import urllib.parse
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

    def test_ask_exposes_skills_toolset_and_skill_status_rule_in_ephemeral_prompt(self):
        bridge = load_bridge()
        popen_result = mock.Mock()
        popen_result.communicate.return_value = ("Done", "")
        popen_result.returncode = 0
        with mock.patch.object(bridge.subprocess, "Popen", return_value=popen_result) as popen:
            bridge.ask("save this as a skill", "Alex", "", token="room-token", reply_to=42)
        cmd = popen.call_args.args[0]
        env = popen.call_args.kwargs["env"]
        self.assertIn("--toolsets", cmd)
        self.assertIn("skills", cmd[cmd.index("--toolsets") + 1])
        prompt = cmd[cmd.index("-q") + 1]
        self.assertNotIn("Skill-management visibility rule", prompt)
        self.assertNotIn("running inside a Nextcloud Talk bridge", prompt)
        ephemeral = env["HERMES_EPHEMERAL_SYSTEM_PROMPT"]
        self.assertIn("Skill-management visibility rule", ephemeral)
        self.assertIn("Skills changed:", ephemeral)
        self.assertIn("Name every skill changed", ephemeral)

    def test_ask_exposes_current_request_and_source_history_rule_in_ephemeral_prompt(self):
        bridge = load_bridge()
        popen_result = mock.Mock()
        popen_result.communicate.return_value = ("Done", "")
        popen_result.returncode = 0
        with mock.patch.object(bridge.subprocess, "Popen", return_value=popen_result) as popen:
            bridge.ask("what did we have before on the GitHub page?", "Alex", "STALE ROOM CONTEXT", token="room-token", reply_to=42)
        cmd = popen.call_args.args[0]
        env = popen.call_args.kwargs["env"]
        prompt = cmd[cmd.index("-q") + 1]
        self.assertNotIn("running inside a Nextcloud Talk bridge", prompt)
        self.assertNotIn("current user message as the controlling request", prompt)
        self.assertIn("STALE ROOM CONTEXT", prompt)
        self.assertIn("current user message as the controlling request", env["HERMES_EPHEMERAL_SYSTEM_PROMPT"])
        self.assertIn("git/file history", env["HERMES_EPHEMERAL_SYSTEM_PROMPT"])

    def test_default_prompt_persists_only_per_turn_payload_and_sets_ephemeral_persona(self):
        bridge = load_bridge()
        popen_result = mock.Mock()
        popen_result.communicate.return_value = ("Done", "")
        popen_result.returncode = 0
        context_packet = "NEXTCLOUD TALK CONTEXT PACKET\nCurrent user message (highest priority): hello"
        with mock.patch.object(bridge.subprocess, "Popen", return_value=popen_result) as popen:
            bridge.ask("hello", "Alex", context_packet, token="room-token", reply_to=42)
        cmd = popen.call_args.args[0]
        env = popen.call_args.kwargs["env"]
        prompt = cmd[cmd.index("-q") + 1]
        self.assertIn(context_packet, prompt)
        self.assertIn("Alex wrote in Nextcloud Talk:\nhello", prompt)
        self.assertNotIn("Role/persona:", prompt)
        self.assertNotIn("running inside a Nextcloud Talk bridge", prompt)
        self.assertIn("Role/persona:", env["HERMES_EPHEMERAL_SYSTEM_PROMPT"])
        self.assertIn("running inside a Nextcloud Talk bridge", env["HERMES_EPHEMERAL_SYSTEM_PROMPT"])

    def test_legacy_persona_prompt_fallback_embeds_persona_in_user_payload(self):
        bridge = load_bridge()
        with mock.patch.dict(os.environ, {"TALK_PERSONA_SYSTEM_PROMPT": "0"}, clear=False):
            prompt = bridge.build_prompt("hello", "Alex", "BASE CONTEXT")
        self.assertIn("BASE CONTEXT", prompt)
        self.assertIn("Role/persona:", prompt)
        self.assertIn("running inside a Nextcloud Talk bridge", prompt)
        self.assertIn("Alex wrote in Nextcloud Talk:\nhello", prompt)

    def test_resume_session_adds_resume_arg_and_preserves_ephemeral_persona(self):
        bridge = load_bridge()
        popen_result = mock.Mock()
        popen_result.communicate.return_value = ("Done", "")
        popen_result.returncode = 0
        with mock.patch.object(bridge, "RESUME_SESSION", "session-123"), \
             mock.patch.object(bridge.subprocess, "Popen", return_value=popen_result) as popen:
            bridge.ask("hello", "Alex", "BASE CONTEXT", token="room-token", reply_to=42)
        cmd = popen.call_args.args[0]
        self.assertIn("--resume", cmd)
        self.assertEqual(cmd[cmd.index("--resume") + 1], "session-123")
        self.assertIn("running inside a Nextcloud Talk bridge", popen.call_args.kwargs["env"]["HERMES_EPHEMERAL_SYSTEM_PROMPT"])

    def test_slash_status_posts_without_calling_hermes(self):
        bridge = load_bridge()
        ev = {"token": "room-token", "message": "/status", "message_id": 7, "actor_name": "Alex"}
        with mock.patch.object(bridge, "ask") as ask, mock.patch.object(bridge, "post", return_value=201) as post:
            bridge.handle(ev)
        ask.assert_not_called()
        posted = post.call_args.args[1]
        self.assertIn("status", posted.lower())
        self.assertIn("Hermes profile", posted)
        self.assertIn("Toolsets", posted)

    def test_slash_reset_uses_context_reset(self):
        bridge = load_bridge()
        ev = {"token": "room-token", "message": "Assistant /reset", "message_id": 8, "actor_name": "Alex"}
        with mock.patch.object(bridge, "reset_context", return_value=2) as reset, mock.patch.object(bridge, "post", return_value=201) as post:
            bridge.handle(ev)
        reset.assert_called_once_with("room-token", bridge.APP_NAME)
        self.assertIn("Removed 2 context file", post.call_args.args[1])

    def test_acknowledge_received_is_disabled_by_default(self):
        bridge = load_bridge()
        with mock.patch.dict(os.environ, {"TALK_RECEIVED_REACTION": ""}, clear=False), \
             mock.patch.object(bridge, "react") as react:
            self.assertFalse(bridge.acknowledge_received("room-token", 42))
        react.assert_not_called()

    def test_handle_adds_optional_received_reaction_before_hermes(self):
        bridge = load_bridge()
        ev = {"token": "room-token", "message": "hello", "message_id": 42, "actor_name": "Alex"}
        call_order = []

        def fake_ack(token, message_id):
            call_order.append(("ack", token, message_id))
            return True

        def fake_ask(*args, **kwargs):
            call_order.append(("ask", args[0]))
            return "final reply"

        with mock.patch.dict(os.environ, {"TALK_RECEIVED_REACTION": "👀"}, clear=False), \
             mock.patch.object(bridge, "acknowledge_received", side_effect=fake_ack) as ack, \
             mock.patch.object(bridge, "ask", side_effect=fake_ask), \
             mock.patch.object(bridge, "append_turn"), \
             mock.patch.object(bridge, "sync_local_memory_message"), \
             mock.patch.object(bridge, "post", return_value=201):
            bridge.handle(ev)
        ack.assert_called_once_with("room-token", 42)
        self.assertEqual(call_order[0], ("ack", "room-token", 42))
        self.assertEqual(call_order[1], ("ask", "hello"))

    def test_received_reaction_failure_does_not_block_reply(self):
        bridge = load_bridge()
        ev = {"token": "room-token", "message": "hello", "message_id": 42, "actor_name": "Alex"}
        with mock.patch.object(bridge, "acknowledge_received", side_effect=RuntimeError("reaction failed")), \
             mock.patch.object(bridge, "ask", return_value="final reply") as ask, \
             mock.patch.object(bridge, "append_turn"), \
             mock.patch.object(bridge, "sync_local_memory_message"), \
             mock.patch.object(bridge, "post", return_value=201):
            bridge.handle(ev)
        ask.assert_called_once()

    def test_react_posts_signed_talk_reaction(self):
        bridge = load_bridge()

        class FakeResponse:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, _n):
                return b"{}"

        captured = {}

        def fake_urlopen(req, timeout):
            captured["req"] = req
            captured["timeout"] = timeout
            return FakeResponse()

        with mock.patch.object(bridge.secrets, "token_hex", return_value="a" * 64), \
             mock.patch.object(bridge.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertTrue(bridge.react("room-token", 42, "👀"))

        req = captured["req"]
        self.assertEqual(captured["timeout"], 20)
        self.assertEqual(req.full_url, "https://nextcloud.example.test/ocs/v2.php/apps/spreed/api/v1/bot/room-token/reaction/42")
        fields = urllib.parse.parse_qs(req.data.decode())
        self.assertEqual(fields["reaction"], ["👀"])
        expected_sig = hmac.new(b"test-secret", (("a" * 64) + "👀").encode(), hashlib.sha256).hexdigest()
        self.assertEqual(req.headers["X-nextcloud-talk-bot-random"], "a" * 64)
        self.assertEqual(req.headers["X-nextcloud-talk-bot-signature"], expected_sig)

    def test_post_can_create_thread_or_reply_to_thread(self):
        bridge = load_bridge()

        class FakeResponse:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, _n):
                return b"{}"

        captured = []

        def fake_urlopen(req, timeout):
            captured.append(urllib.parse.parse_qs(req.data.decode()))
            return FakeResponse()

        with mock.patch.object(bridge.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertEqual(bridge.post("room-token", "new thread", thread_title="Daily reports"), 201)
            self.assertEqual(bridge.post("room-token", "same thread", thread_id=123, silent=True, reference_id="cron-42"), 201)

        self.assertEqual(captured[0]["threadTitle"], ["Daily reports"])
        self.assertNotIn("replyTo", captured[0])
        self.assertEqual(captured[1]["threadId"], ["123"])
        self.assertEqual(captured[1]["silent"], ["true"])
        self.assertEqual(captured[1]["referenceId"], ["cron-42"])

    def test_post_reply_to_takes_precedence_over_thread_fields(self):
        bridge = load_bridge()

        class FakeResponse:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, _n):
                return b"{}"

        captured = {}

        def fake_urlopen(req, timeout):
            captured.update(urllib.parse.parse_qs(req.data.decode()))
            return FakeResponse()

        with mock.patch.object(bridge.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertEqual(bridge.post("room-token", "reply", reply_to=42, thread_title="Ignored", thread_id=123), 201)

        self.assertEqual(captured["replyTo"], ["42"])
        self.assertNotIn("threadTitle", captured)
        self.assertNotIn("threadId", captured)

    def test_approval_prompt_redacts_secret_values(self):
        bridge = load_bridge()
        prompt = bridge._format_talk_approval_prompt("DANGEROUS COMMAND: run curl -H 'Authorization: Bearer abcdefghijklmnop' TOKEN=supersecretvalue")
        self.assertIn("Approval required", prompt)
        self.assertIn("Bearer <redacted>", prompt)
        self.assertIn("TOKEN=<redacted>", prompt)
        self.assertNotIn("abcdefghijklmnop", prompt)
        self.assertNotIn("supersecretvalue", prompt)

    def test_pending_approval_is_room_scoped(self):
        bridge = load_bridge()
        fake_stdin = mock.Mock()
        with mock.patch.object(bridge, "APPROVAL_TIMEOUT", 5), mock.patch.object(bridge, "post", return_value=201):
            bridge._request_talk_approval("room-a", 10, "DANGEROUS COMMAND: delete test\nChoice [o/s/a/D]:", fake_stdin)
            self.assertIsNone(bridge.resolve_pending_approval("room-b", "/approve once"))
            self.assertEqual(bridge.resolve_pending_approval("room-a", "/approve session"), "session")
        # Let the waiter write the mapped CLI choice.
        import time
        deadline = time.time() + 2
        while not fake_stdin.write.called and time.time() < deadline:
            time.sleep(0.05)
        fake_stdin.write.assert_called_with("s\n")
        fake_stdin.flush.assert_called_once()

    def test_ask_approval_mode_uses_stdin_pipe_and_omits_yolo(self):
        bridge = load_bridge()
        popen_result = mock.Mock()
        popen_result.returncode = 0
        with mock.patch.object(bridge, "APPROVAL_PROMPTS", True), \
             mock.patch.object(bridge, "_collect_process_with_talk_approvals", return_value=("Done", "", False, False)), \
             mock.patch.object(bridge.subprocess, "Popen", return_value=popen_result) as popen:
            reply = bridge.ask("do protected thing", "Alex", "", token="room-token", reply_to=42)
        self.assertEqual(reply, "Done")
        cmd = popen.call_args.args[0]
        self.assertNotIn("--yolo", cmd)
        self.assertIs(popen.call_args.kwargs["stdin"], bridge.subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()
