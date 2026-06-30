import unittest
from unittest import mock

from nextcloud_talk_hermes_bridge import talk_voice_transcribe as voice


class VoiceTranscribeOptionalToolTests(unittest.TestCase):
    def test_missing_docker_returns_empty_transcription_instead_of_crashing(self):
        with mock.patch.object(voice.subprocess, "run", side_effect=FileNotFoundError("docker")):
            self.assertEqual(voice.transcribe_from_talk_params({"share": "123"}), "")

    def test_run_converts_missing_optional_command_to_failed_completed_process(self):
        with mock.patch.object(voice.subprocess, "run", side_effect=FileNotFoundError("docker")):
            proc = voice._run(["docker", "exec", "nextcloud", "php", "-v"])
        self.assertEqual(proc.returncode, 127)
        self.assertIn("docker", proc.stderr)


if __name__ == "__main__":
    unittest.main()
