import unittest
from datetime import date, datetime, timezone

from social.planner import identity, plan, preview
from social.workflow import publish

D = date(2026, 11, 30)
NOW = datetime(2026, 11, 28, tzinfo=timezone.utc)
TARGETS = {
    "instagram": {"accountId": "ig"},
    "facebook": {"accountId": "fb", "pageId": "page"},
    "googlebusiness": {"accountId": "gb", "locationId": "accounts/a/locations/b"},
}


class Workflow(unittest.TestCase):
    def setUp(self):
        self.batch = plan("eden", D, lambda _: "https://example.org/safe.jpg", TARGETS)
        self.state = {"approvals": {}, "writes": {}}
        self.calls = []
        self.manual = {"user": {"id": 123}, "body": "/approve " + identity(self.batch)}

    def run_batch(self, comments=(), auto=False, body=None, sink=None):
        def send(payload):
            self.calls.append(payload)
            return {"postSubmissionId": "test-id", "scheduledTime": payload["scheduledTime"]}

        return publish(
            self.batch,
            self.state,
            lambda: None,
            NOW,
            auto,
            123,
            preview(self.batch) if body is None else body,
            comments,
            sink or send,
        )

    def test_bound_to_content_approver_brand_and_switch(self):
        self.state["approvals"]["acacia"] = ["2026-11-02", "2026-11-09"]
        for comments, body in [
            ([], None),
            ([self.manual | {"user": {"id": 99}}], None),
            ([self.manual], "edited"),
        ]:
            self.run_batch(comments, True, body)
        self.assertEqual(self.calls, [])
        self.state["approvals"]["eden"] = ["2026-11-02", "2026-11-09"]
        self.run_batch()
        self.assertEqual(self.calls, [])
        self.run_batch(auto=True)
        self.assertEqual(len(self.calls), 14)
        self.assertNotIn("2026-11-30", self.state["approvals"]["eden"])

    def test_manual_and_restart(self):
        self.run_batch([self.manual])
        self.run_batch([self.manual])
        self.assertEqual(len(self.calls), 14)
        self.assertEqual(self.state["approvals"]["eden"], ["2026-11-30"])
        facebook = self.calls[1]
        self.assertIn("scheduledTime", facebook)
        self.assertNotIn("scheduledTime", facebook["post"])
        self.assertEqual(facebook["post"]["target"], {"targetType": "facebook", "pageId": "page"})

    def test_uncertain_write_is_never_repeated(self):
        def timeout(body):
            self.calls.append(body)
            raise TimeoutError()

        with self.assertRaises(TimeoutError):
            self.run_batch([self.manual], sink=timeout)
        self.run_batch([self.manual])
        self.assertEqual(len(self.calls), 14)
        self.assertEqual(self.state["approvals"]["eden"], [self.batch["week"]])

    def test_two_approved_weeks_count_even_if_first_slots_have_passed(self):
        def send(body):
            return {"postSubmissionId": "test", "scheduledTime": body["scheduledTime"]}

        for day in (date(2026, 11, 30), date(2026, 12, 7)):
            batch = plan("eden", day, lambda _: None, TARGETS)
            manual = [{"user": {"id": 123}, "body": "/approve " + identity(batch)}]
            late = datetime.fromisoformat(batch["days"][1]["time"])
            publish(batch, self.state, lambda: None, late, False, 123, preview(batch), manual, send)
        self.assertEqual(self.state["approvals"]["eden"], ["2026-11-30", "2026-12-07"])
        third = plan("eden", date(2026, 12, 14), lambda _: None, TARGETS)
        publish(third, self.state, lambda: None, NOW, True, 123, preview(third), [], send)
        self.assertIn("eden/2026-12-14/facebook", self.state["writes"])
