import unittest
from unittest.mock import patch

from social.inbox import classify
from social.sources import check_remote_blob


class Policy(unittest.TestCase):
    def test_date_questions_only(self):
        for query in [
            "Is Cherry Cove available December 5-7?",
            "Do you have availability for 12/5 to 12/7?",
            "Hi, how do I make a reservation?",
            "How far are you from downtown Fredericksburg?",
        ]:
            self.assertTrue(classify(query), query)
        for query in [
            "Is Cherry Cove available December 5-7 if I bring a dog?",
            "How do I book with a discount?",
            "Do you have availability for a wedding?",
            "Is the refund available December 5?",
        ]:
            self.assertIsNone(classify(query))

    def test_a_stale_local_ref_cannot_satisfy_a_live_fact_check(self):
        with patch("social.sources.subprocess.check_output", return_value=b"wrong\n"):
            with self.assertRaises(ValueError):
                check_remote_blob("docs/marketing/FACTS.md", b"cached content")
