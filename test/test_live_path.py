"""Offline integration through the same tick used by launchd."""

import tempfile
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from social.cli import DemoWriter, load_example
from social.media import FLAGS, candidate_url, sanitize
from social.planner import CT, identity
from social.runtime import State, process_inbox, tick_brand


class LivePath(unittest.TestCase):
    def test_repo_style_webp_reaches_vision(self):
        data = BytesIO()
        Image.new("RGB", (640, 480)).save(data, "WEBP")
        inspect = Mock(return_value=dict.fromkeys(FLAGS, False))
        self.assertIsNotNone(sanitize(data.getvalue(), "eden", inspect))
        inspect.assert_called_once()

    def test_private_metadata_and_unknown_subject_fail_closed(self):
        clean = {
            "tags": ["after", "drone"],
            "uris": [{"type": "original", "uri": "https://static.companycam.com/a.jpg"}],
        }
        for fields in [{"internal": True}, {"metadata": {"street_sign": True}}, {"house_number": "fixture"}]:
            self.assertIsNone(candidate_url(clean | fields))

    def test_separate_ticks_preview_then_exact_phone_approval(self):
        cfg = load_example()
        cfg["approverId"] = 123
        targets = {"instagram": {"accountId": "ig"}, "facebook": {"accountId": "fb", "pageId": "p"}}
        cfg["brands"]["eden"]["targets"] = targets
        now = datetime(2099, 11, 29, 12, tzinfo=CT)
        now = now - timedelta(days=(now.weekday() + 1) % 7)  # Sunday
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            issues, api = Mock(), Mock()
            issues.ensure.return_value = 1

            def get(path, **params):
                if path.endswith("subaccounts"):
                    return {"items": [dict(accountId="fb", id="p")]}
                if path.endswith("/accounts"):
                    return {
                        "items": [dict(id="ig", platform="instagram"), dict(id="fb", platform="facebook")]
                    }
                if path.startswith("/posts/"):
                    return dict(status="scheduled")
                return {"items": []}

            api.get.side_effect = get
            api.post.side_effect = lambda path, body: dict(
                postSubmissionId="fixture", scheduledTime=body["scheduledTime"]
            )
            with (
                patch("social.runtime.check"),
                patch("social.runtime.media_picker", return_value=lambda _: "https://example.org/safe.jpg"),
            ):
                issues.read.return_value = ("", [])
                tick_brand("eden", cfg, store, now, api, issues, DemoWriter())
                api.post.assert_not_called()
                record = next(iter(store.data["batches"].values()))
                from social.planner import preview

                cmd = "/approve " + identity(record["batch"])
                issues.read.return_value = (
                    preview(record["batch"]),
                    [{"user": {"id": cfg["approverId"]}, "body": cmd}],
                )
                tick_brand("eden", cfg, store, now, api, issues, DemoWriter())
                tick_brand("eden", cfg, store, now, api, issues, DemoWriter())
                self.assertEqual(api.post.call_count, 14)

    def test_handoff_persists_for_followup_in_same_dm_thread(self):
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            now = datetime(2026, 11, 30, 12, tzinfo=CT)
            api, issues = Mock(), Mock()
            issues.ensure.return_value = 1
            issues.open.return_value = True
            store.data["inboxSince"]["eden"] = (now - timedelta(minutes=15)).isoformat()

            def message(mid, text, stamp):
                return dict(
                    id=mid,
                    conversationId="thread",
                    senderId="test",
                    platform="instagram",
                    direction="incoming",
                    status="delivered",
                    text=text,
                    createdAt=stamp.isoformat(),
                )

            msg = message("first", "Refund?", now)
            api.get.side_effect = lambda path, **params: {"items": [msg] if path == "/messages" else []}
            process_inbox("eden", {"instagram": {"accountId": "a"}}, store, now, api, issues, Mock())
            now += timedelta(minutes=15)
            msg = message("second", "How do I book?", now)
            process_inbox("eden", {"instagram": {"accountId": "a"}}, store, now, api, issues, Mock())
            api.post.assert_not_called()
