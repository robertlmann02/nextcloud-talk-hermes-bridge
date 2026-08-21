import importlib
import io
import json
import os
import unittest
from unittest import mock


def load_bridge():
    os.environ.setdefault("TALK_BOT_SECRET", "test-secret")
    os.environ.setdefault("TALK_DELIVER_SECRET", "deliver-secret")
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

    def test_deliver_requires_bearer_token(self):
        h = self.make_handler("/deliver")
        h.rfile = io.BytesIO(b'{"room_token":"abc","message":"hello"}')
        h.headers = {"Content-Length": str(len(h.rfile.getvalue()))}
        h.do_POST()
        self.assertEqual(h.status, 401)

    def test_deliver_posts_and_records_assistant_turn(self):
        payload = b'{"room_token":"abc123","message":"scheduled report","actor":"cron"}'
        h = self.make_handler("/deliver")
        h.rfile = io.BytesIO(payload)
        h.headers = {"Content-Length": str(len(payload)), "Authorization": "Bearer deliver-secret"}
        bridge = load_bridge()
        with mock.patch.object(bridge, "post", return_value=201) as post, \
             mock.patch.object(bridge, "append_turn") as append_turn, \
             mock.patch.object(bridge, "sync_local_memory_message") as sync_memory:
            h.do_POST()
        self.assertEqual(h.status, 200)
        self.assertEqual(json.loads(h.body.decode()), {
            "ok": True,
            "status": "delivered",
            "room_token": "abc123",
            "post_status": 201,
        })
        post.assert_called_once_with(
            "abc123",
            "scheduled report",
            0,
            thread_title="",
            thread_id=0,
            silent=False,
            reference_id="",
        )
        append_turn.assert_called_once_with(
            "abc123", "assistant", bridge.ASSISTANT_NAME, "scheduled report", 0, app_name=bridge.APP_NAME
        )
        sync_memory.assert_called_once()

    def test_deliver_still_succeeds_when_optional_memory_sync_fails(self):
        payload = b'{"room_token":"abc123","message":"scheduled report","actor":"cron"}'
        h = self.make_handler("/deliver")
        h.rfile = io.BytesIO(payload)
        h.headers = {"Content-Length": str(len(payload)), "Authorization": "Bearer deliver-secret"}
        bridge = load_bridge()
        with mock.patch.object(bridge, "post", return_value=201), \
             mock.patch.object(bridge, "append_turn"), \
             mock.patch.object(bridge, "sync_local_memory_message", side_effect=RuntimeError("bad memory db")):
            h.do_POST()
        self.assertEqual(h.status, 200)
        self.assertEqual(json.loads(h.body.decode()), {
            "ok": True,
            "status": "delivered",
            "room_token": "abc123",
            "post_status": 201,
        })

    def test_deliver_validates_payload(self):
        payload = b'{"room_token":"abc123"}'
        h = self.make_handler("/deliver")
        h.rfile = io.BytesIO(payload)
        h.headers = {"Content-Length": str(len(payload)), "Authorization": "Bearer deliver-secret"}
        h.do_POST()
        self.assertEqual(h.status, 400)
        self.assertEqual(json.loads(h.body.decode()), {"ok": False, "error": "missing message"})

    def test_deliver_passes_thread_fields_to_post(self):
        payload = b'{"room_token":"abc123","message":"threaded report","threadTitle":"Daily reports","threadId":123,"silent":true,"referenceId":"cron-42"}'
        h = self.make_handler("/deliver")
        h.rfile = io.BytesIO(payload)
        h.headers = {"Content-Length": str(len(payload)), "Authorization": "Bearer deliver-secret"}
        bridge = load_bridge()
        with mock.patch.object(bridge, "post", return_value=201) as post, \
             mock.patch.object(bridge, "append_turn"), \
             mock.patch.object(bridge, "sync_local_memory_message"):
            h.do_POST()
        self.assertEqual(h.status, 200)
        post.assert_called_once_with(
            "abc123",
            "threaded report",
            0,
            thread_title="Daily reports",
            thread_id=123,
            silent=True,
            reference_id="cron-42",
        )


if __name__ == "__main__":
    unittest.main()
