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

    def test_env_enablement_sets_home_channel_when_configured(self):
        adapter = load_adapter()
        os.environ["NEXTCLOUD_TALK_DELIVER_URL"] = "http://127.0.0.1:8788/deliver"
        os.environ["NEXTCLOUD_TALK_DELIVER_SECRET"] = "secret"
        os.environ["NEXTCLOUD_TALK_HOME_ROOM"] = "room-token"

        enabled = adapter._env_enablement()

        self.assertEqual(enabled["home_channel"]["chat_id"], "room-token")
        self.assertEqual(enabled["extra"]["deliver_url"], "http://127.0.0.1:8788/deliver")


if __name__ == "__main__":
    unittest.main()
