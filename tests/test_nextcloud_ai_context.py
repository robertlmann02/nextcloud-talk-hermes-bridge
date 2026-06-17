import importlib
import json
import os
import unittest
from unittest import mock


def subprocess_result(stdout="", returncode=0):
    class Result:
        def __init__(self):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode
    return Result()


class NextcloudAiContextTests(unittest.TestCase):
    def setUp(self):
        for key in list(os.environ):
            if key.startswith("NEXTCLOUD_AI_") or key in {"NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_APP_PASSWORD", "SSHPASS"}:
                os.environ.pop(key, None)
        self.mod = importlib.import_module("nextcloud_talk_hermes_bridge.nextcloud_ai_context")

    def test_disabled_by_default_returns_empty(self):
        with mock.patch.object(self.mod, "_ocs_file_search") as search:
            self.assertEqual(self.mod.build_nextcloud_ai_context("find the Example PDF"), "")
            search.assert_not_called()

    def test_casual_message_returns_empty_when_enabled(self):
        os.environ["NEXTCLOUD_AI_CONTEXT"] = "1"
        with mock.patch.object(self.mod, "_ocs_file_search") as search:
            self.assertEqual(self.mod.build_nextcloud_ai_context("good morning"), "")
            search.assert_not_called()

    def test_document_question_formats_mocked_results(self):
        os.environ["NEXTCLOUD_AI_CONTEXT"] = "1"
        with mock.patch.object(
            self.mod,
            "_ocs_file_search",
            return_value=[{"title": "Example Manual.pdf", "path": "/Manuals/Example Manual.pdf", "mime": "application/pdf"}],
        ) as search:
            text = self.mod.build_nextcloud_ai_context("find the example manual PDF", token="room", actor="Alex")
        search.assert_called_once()
        self.assertIn("NEXTCLOUD AI / DOCUMENT CONTEXT", text)
        self.assertIn("Example Manual.pdf", text)
        self.assertIn("/Manuals/Example Manual.pdf", text)
        self.assertIn("application/pdf", text)

    def test_search_failure_is_silent(self):
        os.environ["NEXTCLOUD_AI_CONTEXT"] = "1"
        with mock.patch.object(self.mod, "_ocs_file_search", side_effect=OSError("network down")):
            self.assertEqual(self.mod.build_nextcloud_ai_context("find the example manual PDF"), "")

    def test_context_is_truncated(self):
        os.environ["NEXTCLOUD_AI_CONTEXT"] = "1"
        os.environ["NEXTCLOUD_AI_CONTEXT_MAX_CHARS"] = "500"
        results = [{"title": "x" * 200, "path": "/" + "y" * 300, "mime": "application/pdf"} for _ in range(8)]
        with mock.patch.object(self.mod, "_ocs_file_search", return_value=results):
            text = self.mod.build_nextcloud_ai_context("find the giant document PDF")
        self.assertLessEqual(len(text), 520)
        self.assertIn("...[truncated]", text)

    def test_ocs_file_search_parses_nextcloud_response(self):
        os.environ["NEXTCLOUD_URL"] = "https://nextcloud.example.test"
        os.environ["NEXTCLOUD_AI_USER"] = "test-user"
        os.environ["NEXTCLOUD_AI_APP_PASSWORD"] = "app-password"
        body = json.dumps({
            "ocs": {
                "data": {
                    "entries": [
                        {
                            "title": "Service Manual.pdf",
                            "subline": "PDF document",
                            "link": "https://nextcloud.example.test/f/123",
                            "resource": {"path": "/Manuals/Service Manual.pdf", "mimeType": "application/pdf"},
                        }
                    ]
                }
            }
        }).encode()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, _limit):
                return body

        with mock.patch("urllib.request.urlopen", return_value=Response()) as urlopen:
            results = self.mod._ocs_file_search("service manual")
        self.assertEqual(results[0]["title"], "Service Manual.pdf")
        self.assertEqual(results[0]["path"], "/Manuals/Service Manual.pdf")
        req = urlopen.call_args.args[0]
        self.assertIn("/ocs/v2.php/search/providers/files/search?", req.full_url)
        self.assertEqual(req.headers["Ocs-apirequest"], "true")
        self.assertIn("Basic ", req.headers["Authorization"])


    def test_ssh_file_search_formats_mocked_results(self):
        os.environ["NEXTCLOUD_AI_CONTEXT"] = "1"
        os.environ["NEXTCLOUD_AI_CONTEXT_MODE"] = "ssh_files_search"
        with mock.patch.object(
            self.mod,
            "_ssh_file_search",
            return_value=[{"title": "Example Scan.pdf", "path": "/srv/files/Example Scan.pdf", "subline": "SSH filesystem match"}],
        ) as search:
            text = self.mod.build_nextcloud_ai_context("find the example scan PDF")
        search.assert_called_once()
        self.assertIn("NEXTCLOUD AI / DOCUMENT CONTEXT", text)
        self.assertIn("Example Scan.pdf", text)
        self.assertIn("/srv/files/Example Scan.pdf", text)

    def test_ssh_file_search_builds_password_command_without_leaking_password(self):
        os.environ["NEXTCLOUD_AI_CONTEXT_LIMIT"] = "3"
        os.environ["NEXTCLOUD_AI_SSH_HOST"] = "files.example.test"
        os.environ["NEXTCLOUD_AI_SSH_USER"] = "bridge-user"
        os.environ["NEXTCLOUD_AI_SSH_PASSWORD"] = "secret-password"
        os.environ["NEXTCLOUD_AI_SSH_SEARCH_ROOTS"] = "/srv/nextcloud-data/files"
        completed = subprocess_result(stdout="/srv/nextcloud-data/files/Service Manual.pdf\n")
        with mock.patch.object(self.mod, "_has_command", return_value=True), mock.patch("subprocess.run", return_value=completed) as run:
            results = self.mod._ssh_file_search("service manual")
        self.assertEqual(results[0]["title"], "Service Manual.pdf")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0:2], ["sshpass", "-e"])
        self.assertNotIn("secret-password", " ".join(cmd))
        self.assertEqual(run.call_args.kwargs["env"]["SSHPASS"], "secret-password")

    def test_ssh_file_search_requires_one_auth_method(self):
        os.environ["NEXTCLOUD_AI_SSH_HOST"] = "files.example.test"
        os.environ["NEXTCLOUD_AI_SSH_USER"] = "bridge-user"
        os.environ["NEXTCLOUD_AI_SSH_PASSWORD"] = "secret-password"
        os.environ["NEXTCLOUD_AI_SSH_KEY_FILE"] = "/home/bridge/.ssh/id_ed25519"
        os.environ["NEXTCLOUD_AI_SSH_SEARCH_ROOTS"] = "/srv/files"
        with mock.patch("subprocess.run") as run:
            self.assertEqual(self.mod._ssh_file_search("manual"), [])
        run.assert_not_called()

if __name__ == "__main__":
    unittest.main()
