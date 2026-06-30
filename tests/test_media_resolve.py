import unittest
from pathlib import Path
from unittest import mock

from nextcloud_talk_hermes_bridge import talk_media_resolve as media


class MediaResolveOptionalToolTests(unittest.TestCase):
    def test_run_converts_missing_optional_command_to_failed_completed_process(self):
        with mock.patch.object(media.subprocess, "run", side_effect=FileNotFoundError("sudo")):
            proc = media._run(["sudo", "-n", "stat", "-c", "%s", "/example"])
        self.assertEqual(proc.returncode, 127)
        self.assertIn("sudo", proc.stderr)

    def test_file_size_returns_minus_one_when_stat_helper_missing(self):
        fake_path = mock.Mock(spec=Path)
        fake_path.stat.side_effect = PermissionError("not readable")
        with mock.patch.object(media.subprocess, "run", side_effect=FileNotFoundError("sudo")):
            self.assertEqual(media._file_size(fake_path), -1)


if __name__ == "__main__":
    unittest.main()
