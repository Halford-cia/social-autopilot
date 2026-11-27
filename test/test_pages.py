import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from social.runtime import State, process_inbox


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
            store.data["inboxSince"]["eden"] = (now - timedelta(hours=1)).isoformat()
            api, issues = Mock(), Mock()
            issues.ensure.return_value = 3
            api.get.side_effect = lambda path, **params: {"items": [item] if path == "/messages" else []}
            process_inbox(
                "eden", {"facebook": {"accountId": "parent", "pageId": "page"}}, store, now, api, issues
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
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            store.data["inboxSince"]["eden"] = (now - timedelta(minutes=30)).isoformat()
            api, issues = Mock(), Mock()

            def get(path, **params):
                if path == "/comments":
                    return {"items": [comment]}
                if path == "/posts":
                    return {
                        "items": [
                            {"id": "post", "account": {"id": "parent", "subaccount": {"id": "acacia-page"}}}
                        ]
                    }
                return {"items": []}

            api.get.side_effect = get
            process_inbox(
                "eden", {"facebook": {"accountId": "parent", "pageId": "eden-page"}}, store, now, api, issues
            )
            api.post.assert_not_called()
