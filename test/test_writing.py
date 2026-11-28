import json
import unittest
from datetime import date
from unittest.mock import Mock

from social.claude import HAIKU, SONNET
from social.inbox import AREA, BOOKING
from social.planner import plan, verify_copy
from social.writing import reply_text, write_week

WEEK = date(2026, 11, 30)


class Writing(unittest.TestCase):
    def test_sonnet_composes_and_bound_copy_survives_publish_check(self):
        writer = Mock()
        batch = plan("eden", WEEK, lambda _: None)
        new = [d["text"] for d in batch["days"]]
        new[0] = (
            "Which would you pick first, a long soak or a suspended king bed? Treehouse Serenity Dome has room for two."
        )
        writer.generate.return_value = json.dumps(new)
        result = write_week("eden", WEEK, list(range(7)), writer)
        self.assertEqual(result, new)
        self.assertEqual(writer.generate.call_args.args[0], SONNET)
        self.assertIn("Stay Eden", writer.generate.call_args.args[1])
        for row, text in zip(batch["days"], result):
            row["text"] = text
        verify_copy(batch)
        batch["days"][0]["text"] += " Every stay is available."
        with self.assertRaises(ValueError):
            verify_copy(batch)

    def test_unsupported_facts_wrong_day_or_incomplete_week_are_blocked(self):
        for brand in ["eden", "acacia"]:
            baseline = [d["text"] for d in plan(brand, WEEK, lambda _: None)["days"]]
            for bad in [baseline[:-1], baseline[::-1], [baseline[0] + " Only $75!"] + baseline[1:]]:
                writer = Mock()
                writer.generate.return_value = json.dumps(bad)
                with self.assertRaises(ValueError):
                    write_week(brand, WEEK, list(range(7)), writer)

    def test_haiku_writes_only_with_classified_public_facts(self):
        writer = Mock()
        writer.generate.return_value = "Happy to help! " + BOOKING
        self.assertEqual(reply_text(writer, BOOKING), writer.generate.return_value)
        self.assertEqual(writer.generate.call_args.args[0], HAIKU)
        for bad in [
            AREA,
            BOOKING + " You're confirmed.",
            "Check in at four.",
            "Book through another site.",
        ]:
            writer.generate.return_value = bad
            self.assertIsNone(reply_text(writer, BOOKING))
