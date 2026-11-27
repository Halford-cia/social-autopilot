import unittest
from datetime import datetime, timezone

from social.digest import digest
from social.inbox import classify, route

NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)
TARGET = {"accountId": "a", "pageId": "p"}


class Inbox(unittest.TestCase):
    def test_simple_only(self):
        for s in ["Are you available?", "How do I book?", "Where are you located?"]:
            self.assertTrue(classify(s))
        for s in [
            "How do I book and cancel?",
            "What time is check-in?",
            "Where is my refund?",
            "Can I get a discount?",
            "Where are you located? Our door code is 1234",
        ]:
            self.assertIsNone(classify(s))

    def test_acacia_never_replies_even_to_easy_questions(self):
        msg = dict(
            direction="incoming",
            status="delivered",
            text="How do I book?",
            id="m",
            senderId="guest",
            recipientId="p",
            platform="facebook",
            createdAt=NOW.isoformat(),
        )
        self.assertEqual(route("acacia", "messages", msg, TARGET, NOW)[0], "draft")
        action, body = route("eden", "messages", msg, TARGET, NOW)
        self.assertEqual(action, "send")
        self.assertEqual(body["recipientId"], "guest")
        for changes in [dict(payload={"selection": {}}), dict(recipientId="wrong"), dict(text="Refund?")]:
            self.assertNotEqual(route("eden", "messages", msg | changes, TARGET, NOW)[0], "send")
        self.assertEqual(
            route("eden", "messages", msg | {"direction": "outgoing"}, TARGET, NOW)[0], "ignore"
        )

    def test_no_reply_to_nested_comments_or_other_account(self):
        c = dict(
            status="posted",
            platform="instagram",
            accountId="a",
            postId="z",
            id="c",
            isAuthor=False,
            parentCommentId=None,
            text="Where are you located?",
            createdAt=NOW.isoformat(),
        )
        for update in [dict(parentCommentId="nested"), dict(accountId="other"), dict(isAuthor=True)]:
            self.assertNotEqual(route("eden", "comments", c | update, {"accountId": "a"}, NOW)[0], "send")
        self.assertEqual(route("eden", "comments", c, {"accountId": "a"}, NOW)[1]["parentCommentId"], "c")


class Digest(unittest.TestCase):
    def test_measured_zero_pending_and_brand_scope(self):
        def row(i, account, reach):
            return dict(
                id=i,
                platform="instagram",
                account={"id": account},
                postTime="2026-11-24T15:00:00Z",
                state={"type": "published", "analytics": {"latest": {"metrics": {"reachCount": reach}}}},
            )

        report = digest(
            "eden",
            "2026-11-23",
            {"instagram": {"accountId": "ig"}},
            [
                row("one", "ig", "20"),
                row("two", "ig", "0"),
                row("three", "ig", None),
                row("foreign", "other", "9800"),
            ],
        )
        for term in ["20", "2/3", "unavailable", "one", "next"]:
            self.assertIn(term, report.lower())
        self.assertNotIn("foreign", report)

    def test_post_attributed_follows_report_coverage_separately_from_net_change(self):
        def post(pid, metrics):
            return {
                "id": pid,
                "platform": "instagram",
                "account": {"id": "ig"},
                "postTime": "2026-11-24T15:00:00Z",
                "state": {"type": "published", "analytics": {"latest": {"metrics": metrics}}},
            }

        report = digest(
            "eden",
            "2026-11-23",
            {"instagram": {"accountId": "ig"}},
            [post("one", {"followsCount": "6"}), post("two", {"followsCount": "0"}), post("three", {})],
        )
        self.assertIn("Post-attributed follows: 6 (2/3", report)
        self.assertIn("Follower change: unavailable", report)
