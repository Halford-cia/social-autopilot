"""Production entry points with a locked temporary ledger and simulated transports."""

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from social import cli
from social.claude import HAIKU, SONNET, RunLimit
from social.inbox import BOOKING
from social.planner import CT, plan
from social.runtime import State, process_inbox, tick_brand, weekly

IG = {"accountId": "<eden-instagram>", "name": "@stayeden"}  # a resolved destination (449r2 P0-1)


class AuthoringPath(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = self.enterContext(State(self.root / "ledger"))
        self.api, self.issues, self.writer = Mock(), Mock(), Mock()
        self.issues.ensure.return_value = 123
        self.issues.read.return_value = ("", [])
        self.cfg = cli.load_example()

    def test_generated_week_is_saved_once_and_reviewed_before_publish(self):
        now = datetime(2026, 11, 30, tzinfo=CT)
        composed = [r["text"] for r in plan("eden", now.date(), lambda _: None)["days"]]
        composed[0] = (
            "Which would you pick first, a long soak or a suspended king bed? Treehouse Serenity Dome has room for two."
        )
        self.writer.generate.return_value = json.dumps(composed)
        with (
            patch("social.runtime.check"),
            patch("social.runtime.media_picker", return_value=lambda _: None),
        ):
            for _ in range(2):
                weekly("eden", self.cfg, self.store, now, self.api, self.issues, self.writer)
        self.writer.generate.assert_called_once()
        self.assertEqual(self.writer.generate.call_args.args[0], SONNET)
        self.assertIn(composed[0], self.issues.ensure.call_args.args[1])
        self.api.post.assert_not_called()

    def test_inbox_haiku_after_routing_only_and_unsafe_output_handed_off(self):
        now = datetime(2026, 11, 30, 18, tzinfo=UTC)
        targets = {"instagram": {"accountId": "account"}}
        template = dict(
            platform="instagram",
            accountId="account",
            postId="post",
            isAuthor=False,
            status="posted",
            createdAt=now.isoformat(),
        )
        records = [
            template | dict(id="1", text="Are you available on 12/20/2026?"),
            template | dict(id="2", text="What time is check-in?"),
            template | dict(id="3", text="Where are you located?"),
        ]
        # The feed lists the three questions; none has a reply under it (Blotato's parentCommentId filter).
        self.api.get.side_effect = lambda path, **p: {
            "items": records if path == "/comments" and "parentCommentId" not in p else []
        }
        self.api.post.return_value = {"id": "accepted"}
        self.writer.generate.side_effect = ["Happy to help! " + BOOKING, "We confirm your dates."]
        for brand in ("eden", "acacia"):
            self.store.data["inboxSince"][brand + "/instagram/account"] = (now - timedelta(hours=1)).isoformat()
            process_inbox(brand, targets, self.store, now, self.api, self.issues, self.writer, self.cfg["blotato"])
        self.assertEqual(self.writer.generate.call_count, 2)
        for call in self.writer.generate.call_args_list:
            self.assertEqual(call.args[0], HAIKU)
            self.assertNotIn("12/20/2026", str(call))
        self.api.post.assert_called_once()
        self.assertEqual(self.api.post.call_args.args[1]["text"], "Happy to help! " + BOOKING)
        self.assertEqual(self.issues.ensure.call_count, 5)

    def test_limit_escapes_stage_handler(self):
        with (
            patch("social.runtime.check_destinations", return_value=({"instagram": IG}, [])),
            patch.dict(self.store.data, {"approvedDestinations": {"eden": {"instagram": IG}}}),
            patch("social.runtime.check"),
            patch("social.runtime.process_inbox", side_effect=RunLimit("monthly limit")),
            patch("social.runtime.monday_digest") as digest,
        ):
            with self.assertRaises(RunLimit):
                tick_brand(
                    "eden", self.cfg, self.store, datetime.now(UTC), self.api, self.issues, self.writer
                )
            digest.assert_not_called()

    def test_month_exhausted_at_start_stops_before_any_provider_or_key(self):
        self.store.data["anthropicSpendNanoUsd"] = {datetime.now(UTC).strftime("%Y-%m"): 10**15}
        self.store.save()
        # Release the lock so the real CLI acquires it.
        self.store.lock.close()
        config = self.root / "config.json"
        config.write_text(json.dumps(self.cfg))
        with (
            patch.object(sys, "argv", ["social", "tick", "--config", str(config)]),
            patch("social.cli.DATA", self.root),
            patch("social.cli.validate_config"),
            patch("social.cli.secret", side_effect=AssertionError("key read")),
            patch("social.cli.Issues", side_effect=AssertionError("GitHub call")),
            patch("social.cli.tick_brand") as tick,
        ):
            self.assertEqual(cli.main(), 1)
            tick.assert_not_called()

    def test_photo_failure_does_not_buy_the_same_week_twice(self):
        now = datetime(2026, 11, 30, tzinfo=CT)
        self.writer.generate.side_effect = cli.DemoWriter().generate
        picker = Mock(side_effect=OSError("mock interrupted upload"))
        with patch("social.runtime.check"), patch("social.runtime.media_picker", return_value=picker):
            with self.assertRaises(OSError):
                weekly("eden", self.cfg, self.store, now, self.api, self.issues, self.writer)
            picker.side_effect = None
            picker.return_value = None
            weekly("eden", self.cfg, self.store, now, self.api, self.issues, self.writer)
        self.writer.generate.assert_called_once()

    def test_revalidate_saved_copy_before_opening_external_preview(self):
        now = datetime(2026, 11, 30, tzinfo=CT)
        batch = plan("eden", now.date(), lambda _: None, self.cfg["brands"]["eden"]["targets"])
        batch["days"][0]["text"] = "Unreviewed placeholder"
        self.store.data["batches"]["eden/2026-11-30"] = {"batch": batch}
        with patch("social.runtime.check"), self.assertRaises(ValueError):
            weekly("eden", self.cfg, self.store, now, self.api, self.issues, self.writer)
        self.issues.ensure.assert_not_called()
