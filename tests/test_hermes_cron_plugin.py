from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest


PLUGIN_PATH = Path(__file__).resolve().parents[1] / "hermes_plugins" / "nextcloud_talk" / "adapter.py"


def load_adapter():
    spec = importlib.util.spec_from_file_location("nextcloud_talk_adapter_test", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    status = 200

    def __init__(self, body: bytes = b'{"ok": true, "post_status": 201}'):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self._body


class _CaptureContext:
    def __init__(self):
        self.kwargs = None

    def register_platform(self, **kwargs):
        self.kwargs = kwargs


class TestHermesCronPlugin(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_registers_native_nextcloud_talk_cron_target(self):
        adapter = load_adapter()
        ctx = _CaptureContext()

        adapter.register(ctx)

        self.assertEqual(ctx.kwargs["name"], "nextcloud_talk")
        self.assertEqual(ctx.kwargs["cron_deliver_env_var"], "NEXTCLOUD_TALK_HOME_ROOM")
        self.assertTrue(callable(ctx.kwargs["standalone_sender_fn"]))
        self.assertIn("NEXTCLOUD_TALK_DELIVER_URL", ctx.kwargs["required_env"])
        self.assertIn("NEXTCLOUD_TALK_DELIVER_SECRET", ctx.kwargs["required_env"])

    def test_standalone_sender_posts_to_bridge_deliver_endpoint(self):
        adapter = load_adapter()
        os.environ["NEXTCLOUD_TALK_DELIVER_URL"] = "http://127.0.0.1:8788/deliver"
        os.environ["NEXTCLOUD_TALK_DELIVER_SECRET"] = "secret"
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(req.header_items())
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = asyncio.run(adapter._standalone_send(None, "room-token", "cron output"))

        self.assertTrue(result["success"])
        self.assertEqual(captured["url"], "http://127.0.0.1:8788/deliver")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(captured["payload"]["room_token"], "room-token")
        self.assertEqual(captured["payload"]["message"], "cron output")
        self.assertEqual(captured["payload"]["actor"], "hermes-cron")

    def test_standalone_sender_maps_hermes_thread_id_to_talk_thread_id(self):
        adapter = load_adapter()
        os.environ["NEXTCLOUD_TALK_DELIVER_URL"] = "http://127.0.0.1:8788/deliver"
        os.environ["NEXTCLOUD_TALK_DELIVER_SECRET"] = "secret"
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = asyncio.run(adapter._standalone_send(None, "room-token", "cron output", thread_id="123"))

        self.assertTrue(result["success"])
        self.assertEqual(captured["payload"]["thread_id"], "123")
        self.assertNotIn("reply_to", captured["payload"])

    def test_env_enablement_sets_home_channel_when_configured(self):
        adapter = load_adapter()
        os.environ["NEXTCLOUD_TALK_DELIVER_URL"] = "http://127.0.0.1:8788/deliver"
        os.environ["NEXTCLOUD_TALK_DELIVER_SECRET"] = "secret"
        os.environ["NEXTCLOUD_TALK_HOME_ROOM"] = "room-token"

        enabled = adapter._env_enablement()

        self.assertEqual(enabled["home_channel"]["chat_id"], "room-token")
        self.assertEqual(enabled["extra"]["deliver_url"], "http://127.0.0.1:8788/deliver")

    def test_validate_config_returns_boolean(self):
        adapter = load_adapter()
        os.environ.pop("NEXTCLOUD_TALK_DELIVER_URL", None)
        os.environ.pop("NEXTCLOUD_TALK_DELIVER_SECRET", None)
        self.assertIs(adapter.validate_config(None), False)

        os.environ["NEXTCLOUD_TALK_DELIVER_URL"] = "http://127.0.0.1:8788/deliver"
        os.environ["NEXTCLOUD_TALK_DELIVER_SECRET"] = "secret"
        self.assertIs(adapter.validate_config(None), True)

    def test_gateway_startup_contract_methods_exist(self):
        adapter = load_adapter()
        instance = adapter.NextcloudTalkCronAdapter(config=type("Config", (), {"extra": {}})())

        for name in (
            "set_message_handler",
            "set_fatal_error_handler",
            "set_session_store",
            "set_busy_session_handler",
            "set_topic_recovery_fn",
            "set_authorization_check",
            "set_platform_event_handler",
        ):
            self.assertTrue(callable(getattr(instance, name)))

        instance.set_message_handler(lambda event: None)
        instance.set_fatal_error_handler(lambda adp: None)
        instance.set_session_store(object())
        instance.set_busy_session_handler(lambda event, session: False)
        instance.set_topic_recovery_fn(lambda source: None)
        instance.set_authorization_check(lambda source: True)
        instance.set_platform_event_handler(lambda event, source: None)
        self.assertEqual(instance.platform.value, "nextcloud_talk")
        self.assertTrue(asyncio.run(instance.connect()))
        asyncio.run(instance.disconnect())

    def test_live_adapter_send_uses_bridge_deliver_endpoint(self):
        adapter = load_adapter()
        os.environ["NEXTCLOUD_TALK_DELIVER_URL"] = "http://127.0.0.1:8788/deliver"
        os.environ["NEXTCLOUD_TALK_DELIVER_SECRET"] = "secret"

        def fake_urlopen(req, timeout=0):
            return _FakeResponse(b'{"ok": true, "post_id": "abc"}')

        instance = adapter.NextcloudTalkCronAdapter(config=type("Config", (), {"extra": {}})())
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = asyncio.run(instance.send("room-token", "cron output"))

        self.assertTrue(result.success)
        self.assertEqual(result.message_id, "abc")


if __name__ == "__main__":
    unittest.main()
