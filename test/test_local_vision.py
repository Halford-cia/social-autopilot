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
            prompt = args[args.index("-p") + 1]
            schema = json.loads(args[args.index("--json-schema") + 1])
            self.assertEqual(set(schema["required"]), {"scene", "risks", "decision"})
            self.assertIn("house_number", prompt)
            self.assertIn("--mmproj", args)
            self.assertNotIn("http", str(args))
            return Mock(
                stdout=b'{"scene":"finished roof detail","risks":[],"decision":"clear"}', returncode=0
            )

        with patch("social.media.subprocess.run", side_effect=run):
            self.assertEqual(visual_check(b"mock pixels", "acacia"), flags)
        self.assertFalse(seen[0].exists())

    def test_inspector_failure_or_malformed_answer_cannot_clear(self):
        with patch("social.media.subprocess.run", side_effect=subprocess.TimeoutExpired("mock", 1)):
            self.assertIsNone(visual_check(b"photo", "eden"))
        with patch("social.media.subprocess.run", return_value=Mock(stdout=b"unexpected", returncode=0)):
            self.assertIsNone(visual_check(b"photo", "eden"))

    def test_scene_must_match_brand_and_any_observed_risk_overrides_clear(self):
        observations = [
            {"scene": "rental interior", "risks": [], "decision": "clear"},
            {"scene": "finished roof detail", "risks": ["face"], "decision": "clear"},
            {"scene": "finished roof detail", "risks": [], "decision": "hold"},
            {"scene": "finished roof detail", "decision": "clear"},
            {"scene": "finished roof detail", "risks": ["unknown"], "decision": "clear"},
        ]
        for observation in observations:
            response = Mock(stdout=json.dumps(observation).encode(), returncode=0)
            with patch("social.media.subprocess.run", return_value=response):
                flags = visual_check(b"fixture", "acacia")
            self.assertTrue(flags is None or (set(flags) == set(FLAGS) and any(flags.values())))
