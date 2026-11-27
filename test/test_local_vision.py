import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from social.media import FLAGS, visual_check


class LocalVision(unittest.TestCase):
    def test_screen_in_local_process_and_discard_candidate_file(self):
        flags = dict.fromkeys(FLAGS, False)
        seen = []

        def run(args, **options):
            photo = Path(args[args.index("--image") + 1])
            self.assertEqual(photo.read_bytes(), b"mock pixels")
            self.assertEqual(photo.stat().st_mode & 0o077, 0)
            seen.append(photo)
            self.assertIn("--mmproj", args)
            self.assertNotIn("http", str(args))
            return Mock(stdout=json.dumps(flags).encode(), returncode=0)

        with patch("social.media.subprocess.run", side_effect=run):
            self.assertEqual(visual_check(b"mock pixels", "acacia"), flags)
        self.assertFalse(seen[0].exists())

    def test_inspector_failure_or_malformed_answer_cannot_clear(self):
        with patch("social.media.subprocess.run", side_effect=subprocess.TimeoutExpired("mock", 1)):
            self.assertIsNone(visual_check(b"photo", "eden"))
        with patch("social.media.subprocess.run", return_value=Mock(stdout=b"unexpected", returncode=0)):
            self.assertIsNone(visual_check(b"photo", "eden"))
