import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from social.api import Blotato, pages
from social.cli import demo, load_example
from social.runtime import State, process_inbox, validate_config


class Transport(unittest.TestCase):
    def test_media_uses_clean_bytes_only_and_no_key_on_second_host(self):
        send = Mock(
            side_effect=[
                {
                    "presignedUrl": "https://storage.example/upload?sig=private",
                    "publicUrl": "https://storage.example/safe.jpg",
                },
                b"",
            ]
        )
        b = Blotato("fixture", send)
        self.assertEqual(b.upload(b"sanitized"), "https://storage.example/safe.jpg")
        one, two = send.call_args_list
        self.assertEqual(one.args[0:2], ("POST", "https://backend.blotato.com/v2/media/uploads"))
        self.assertEqual(json.loads(one.args[3])["filename"], "photo.jpg")
        self.assertNotIn("blotato-api-key", two.args[2])
        self.assertEqual(two.args[3], b"sanitized")

    def test_cursor_repetition_fails(self):
        get = Mock(return_value={"items": [1], "cursor": "same"})
        with self.assertRaises(ValueError):
            list(pages(get, "/comments", accountId="a"))

    def test_state_lock_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            with State(Path(tmp)) as store:
                store.data["writes"]["row"] = {"status": "uncertain"}
                store.save()
                with self.assertRaises(BlockingIOError):
                    with State(Path(tmp)):
                        pass
            with State(Path(tmp)) as next_store:
                self.assertEqual(next_store.data["writes"]["row"]["status"], "uncertain")

    def test_demo_has_no_network_and_no_config(self):
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            out = demo(date(2026, 11, 30))
            self.assertIn("Stay Eden", out)
            self.assertIn("Acacia Roofing", out)
            self.assertIn("mock", out.lower())


class Operational(unittest.TestCase):
    def test_brand_config_cannot_share_an_ig_destination(self):
        cfg = load_example()
        cfg["approverId"] = 123
        for i, (brand, block) in enumerate(cfg["brands"].items()):
            for platform, dest in block["targets"].items():
                for key in dest:
                    dest[key] = platform + key + str(i)
        cfg["brands"]["eden"]["targets"]["instagram"] = cfg["brands"]["acacia"]["targets"]["instagram"]
        with self.assertRaises(ValueError):
            validate_config(cfg)

    def test_handoff_before_cursor_moves_and_no_raw_message_in_state(self):
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            api, issues = Mock(), Mock()
            issues.ensure.return_value = 7
            now = datetime.now(timezone.utc)
            store.data["inboxSince"]["eden"] = "2026-01-01T00:00:00+00:00"
            row = dict(
                id="c",
                platform="instagram",
                isAuthor=False,
                accountId="a",
                postId="p",
                status="posted",
                text="Private fixture inquiry",
                createdAt=now.isoformat(),
                parentCommentId=None,
            )
            api.get.side_effect = lambda path, **params: {"items": [row] if path == "/comments" else []}
            process_inbox("eden", {"instagram": {"accountId": "a"}}, store, now, api, issues)
            process_inbox("eden", {"instagram": {"accountId": "a"}}, store, now, api, issues)
            self.assertEqual(issues.ensure.call_count, 1)
            api.post.assert_not_called()
            self.assertNotIn(row["text"], str(issues.ensure.call_args))
            self.assertNotIn(row["text"], json.dumps(store.data))
