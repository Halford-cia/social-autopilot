import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from social.cli import DemoWriter, load_example
from social.media import company_photos
from social.planner import CT, identity, plan
from social.runtime import State, tick_brand


class Boundaries(unittest.TestCase):
    def test_partial_week_start_prepares_next_full_week(self):
        now = datetime(2099, 12, 3, 12, tzinfo=CT)
        now -= timedelta(days=(now.weekday() - 4) % 7)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            cfg = load_example()
            api, issues = Mock(), Mock()
            api.get.return_value = {"items": []}
            issues.ensure.return_value = 1
            issues.read.return_value = "", []
            with (
                patch("social.runtime.check_destinations", return_value=({}, [])),
                patch("social.runtime.check"),
                patch("social.runtime.media_picker", return_value=lambda _: None),
            ):
                tick_brand("eden", cfg, store, now, api, issues, DemoWriter())
            week = next(iter(store.data["batches"].values()))["batch"]["week"]
            self.assertEqual(week, (now.date() + timedelta(days=3)).isoformat())

    def test_weekly_photo_outage_doesnt_suppress_monday_digest(self):
        now = datetime(2099, 12, 7, 12, tzinfo=CT)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            cfg = load_example()
            api, issues = Mock(), Mock()
            api.get.return_value = {"items": []}
            issues.ensure.return_value = 1
            with (
                patch("social.runtime.check_destinations", return_value=({}, [])),
                patch("social.runtime.check"),
                patch("social.runtime.media_picker", side_effect=RuntimeError("media unavailable")),
            ):
                with self.assertRaises(RuntimeError):
                    tick_brand("eden", cfg, store, now, api, issues, DemoWriter())
            self.assertTrue(store.data["digests"])

    def test_companycam_tag_prefilter_never_replaces_exact_photo_tags(self):
        def fetch(method, url, headers):
            self.assertEqual(method, "GET")
            if "/v2/tags?" in url:
                return [{"value": "after", "id": "1"}, {"value": "drone", "id": "2"}]
            if "/v2/photos?" in url:
                self.assertIn("tag_ids=2", url)
                return [
                    dict(
                        id=str(i), uris=[dict(type="original", uri=f"https://static.companycam.com/{i}.jpg")]
                    )
                    for i in [3, 4]
                ]
            return [{"value": "drone"}] if "/3/tags?" in url else [{"value": "drone"}, {"value": "after"}]

        self.assertEqual(list(company_photos("mock", fetch)), ["https://static.companycam.com/4.jpg"])

    def test_identity_includes_actual_rendered_cta(self):
        now = datetime(2026, 11, 30, tzinfo=CT)
        batch = plan("eden", now.date(), lambda _: None)
        old = identity(batch)
        with patch("social.planner.BOOK", "https://example.org/new-booking"):
            self.assertNotEqual(identity(batch), old)
