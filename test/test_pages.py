import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from social.inbox import BOOKING
from social.runtime import State, process_inbox

BUDGET = {"contactChargeLimitCents": 100, "contactChargeCents": 2, "billingPeriodDay": 1}


class FacebookPages(unittest.TestCase):
    def test_unassigned_dm_is_handed_off_without_reply_or_raw_text(self):
        now = datetime(2026, 11, 30, 19, tzinfo=UTC)
        item = dict(
            platform="facebook",
            direction="incoming",
            status="delivered",
            recipientId=None,
            id="fixture",
            text="How do I book?",
            senderId="private-sender",
            createdAt=now.isoformat(),
        )
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            store.data["inboxSince"]["eden/facebook"] = (now - timedelta(hours=1)).isoformat()
            api, issues = Mock(), Mock()
            issues.ensure.return_value = 3
            api.get.side_effect = lambda path, **params: {"items": [item] if path == "/messages" else []}
            process_inbox(
                "eden", {"facebook": {"accountId": "parent", "pageId": "page"}}, store, now, api, issues, Mock()
            )
            issues.ensure.assert_called_once()
            api.post.assert_not_called()
            self.assertNotIn("private-sender", str(issues.ensure.call_args))

    def test_shared_login_cannot_send_an_eden_reply_to_acacia_comment(self):
        now = datetime(2026, 11, 30, 19, tzinfo=UTC)
        comment = dict(
            id="comment",
            accountId="parent",
            isAuthor=False,
            platform="facebook",
            postId="post",
            status="posted",
            text="How do I book?",
            createdAt=now.isoformat(),
            parentCommentId=None,
        )
        def run(page):
            with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
                store.data["inboxSince"]["eden/facebook"] = (now - timedelta(minutes=30)).isoformat()
                api, issues, writer = Mock(), Mock(), Mock()
                # A reply that passes validation, so only the Page check can stop the send.
                writer.generate.return_value = "Happy to help! " + BOOKING
                api.post.return_value = {"id": "sent"}

                def get(path, **params):
                    if path == "/comments":
                        return {"items": [comment]}
                    if path == "/posts":
                        return {"items": [{"id": "post", "account": {"id": "parent", "subaccount": {"id": page}}}]}
                    return {"items": []}

                api.get.side_effect = get
                target = {"facebook": {"accountId": "parent", "pageId": "eden-page"}}
                process_inbox("eden", target, store, now, api, issues, writer, BUDGET)
                return api, writer

        api, writer = run("eden-page")  # control: the same comment on Stay Eden's own Page
        api.post.assert_called_once()
        api, writer = run("acacia-page")
        api.post.assert_not_called()
        writer.generate.assert_not_called()
