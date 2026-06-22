import importlib
import json
import os
import unittest
from unittest import mock


def load_bridge():
    os.environ.setdefault("TALK_BOT_SECRET", "test-secret")
    os.environ.setdefault("NEXTCLOUD_URL", "https://nextcloud.example.test")
    return importlib.import_module("nextcloud_talk_hermes_bridge.bridge")


class FakeHandlerMixin:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.body = b""

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass


class ExAppEndpointTests(unittest.TestCase):
    def make_handler(self, path):
        bridge = load_bridge()

        class TestHandler(FakeHandlerMixin, bridge.Handler):
            pass

        h = TestHandler()
        h.path = path
        h.wfile = mock.Mock()
        h.wfile.write.side_effect = lambda data: setattr(h, "body", h.body + data)
        return h

    def test_heartbeat_returns_json_ok(self):
        h = self.make_handler("/heartbeat")
        h.do_GET()
        self.assertEqual(h.status, 200)
        payload = json.loads(h.body.decode())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["app_id"], "hermes_talk_bridge")

    def test_enabled_acknowledges_appapi_lifecycle(self):
        h = self.make_handler("/enabled?enabled=1")
        h.do_PUT()
        self.assertEqual(h.status, 200)
        self.assertEqual(json.loads(h.body.decode()), {"error": ""})

    def test_init_acknowledges_appapi_lifecycle(self):
        h = self.make_handler("/init")
        h.rfile = mock.Mock()
        h.headers = {}
        h.do_POST()
        self.assertEqual(h.status, 200)
        self.assertEqual(json.loads(h.body.decode()), {})


if __name__ == "__main__":
    unittest.main()
