import unittest
from datetime import date

from social.planner import CARDS, identity, plan, preview, verify_copy
from social.sources import validate_sources


class Planning(unittest.TestCase):
    def test_two_brand_week(self):
        for brand, channels in [("eden", 2), ("acacia", 3)]:
            batch = plan(brand, date(2026, 11, 30), lambda key: "https://example.org/a.jpg")
            self.assertEqual(len(batch["days"]), 7)
            self.assertEqual(len({d["text"] for d in batch["days"]}), 7)
            self.assertTrue(all(len(d["platforms"]) == channels for d in batch["days"]))
            self.assertIn(identity(batch), preview(batch))
            self.assertIn("2026-11-30", preview(batch))
            verify_copy(batch)
            batch["days"][1]["text"] += " Our guest said this is the best!"
            with self.assertRaises(ValueError):
                verify_copy(batch)

    def test_skipped_photo_and_daylight_saving(self):
        batch = plan("acacia", date(2027, 3, 8), lambda key: None)
        self.assertEqual(batch["days"][0]["time"][-6:], "-06:00")
        self.assertEqual(batch["days"][-1]["time"][-6:], "-05:00")
        self.assertEqual(batch["days"][0]["platforms"], ["facebook", "googlebusiness"])
        self.assertIn("Instagram held", preview(batch))
        with self.assertRaises(ValueError):
            plan("eden", date(2026, 12, 1), lambda key: None)

    def test_source_drift_blocks(self):
        with self.assertRaises(ValueError):
            validate_sources("eden", b"updated brief")
        self.assertGreater(len(CARDS["acacia"]), 7)
