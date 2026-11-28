"""Regression tests for review 449r5 (PR #1). Every provider is mocked; nothing calls a live API."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from test_review_449r import BUDGET, dm, valid_writer
from test_review_449r2 import PHOTO, Ticks, sunday, valid_cfg
from test_review_449r3 import blotato, sent_to, slots
from test_review_449r4 import approve, fb_row

from social.api import RemoteError
from social.claude import RunLimit
from social.planner import CT
from social.runtime import State, event_ref, monday_digest, process_inbox, tick_brand
from social.workflow import record_confirmation

FIXTURES = Path(__file__).parent / "fixtures"


class ClosedHandoffBeyondRetention(unittest.TestCase):
    """P0-1: a closed handoff clears a conversation only while Blotato still holds everything since the close."""

    def run_gap(self, brand, gap_days, host_wrote_again):
        start = datetime(2026, 11, 30, 8, tzinfo=CT)
        close, host = start + timedelta(hours=2), start + timedelta(hours=4)
        follow = host + timedelta(days=gap_days)
        rows = []
        api, issues, writer = Mock(), Mock(), valid_writer()
        clock = {"now": start}
        issues.ensure.side_effect = lambda *a: issues.ensure.call_count
        issues.open.side_effect = lambda n: n != 1 or clock["now"] < close
        issues.closed_at.side_effect = lambda n: close.isoformat() if n == 1 else None
        api.post.return_value = {"id": "response"}

        def get(path, **params):
            if path == "/messages":  # Blotato keeps 45 days, newest first
                now = clock["now"]
                kept = [m for m in rows if now - timedelta(days=45) <= datetime.fromisoformat(m["createdAt"]) <= now]
                return {"items": list(reversed(kept))}
            if path.startswith("/conversations/"):
                return {"id": "thread", "accountId": "a", "platform": "instagram", "createdAt": rows[0]["createdAt"]}
            return {"items": []}

        api.get.side_effect = get
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:

            def tick(t):
                clock["now"] = t
                process_inbox(brand, {"instagram": {"accountId": "a"}}, store, t, api, issues, writer, BUDGET)

            tick(start)
            rows.append(dm("request", "Please change the dates of our stay.", start + timedelta(minutes=10)))
            tick(start + timedelta(minutes=15))
            self.assertEqual(issues.ensure.call_count, 1)  # the handoff
            host_row = dict(direction="outgoing", status="sent", senderId="stay-eden-account")
            rows.append(dm("resolved", "I moved your dates.", start + timedelta(hours=1)) | host_row)
            tick(close + timedelta(minutes=15))
            if host_wrote_again:
                rows.append(dm("host", "Can we speak when you arrive about the refund?", host) | host_row)
            tick(host + timedelta(minutes=15))
            for day in range(1, gap_days):
                tick(host + timedelta(days=day))
            rows.append(dm("follow", "Are you available?", follow))
            tick(follow + timedelta(minutes=5))
            return api, writer, store.data["events"][event_ref(brand, "messages", "follow")]

    def test_question_after_the_hosts_message_aged_out_goes_to_the_host(self):
        api, writer, event = self.run_gap("eden", 46, True)
        api.post.assert_not_called()
        writer.generate.assert_not_called()  # no Haiku call
        self.assertEqual(event["status"], "human")

    def test_a_close_older_than_retention_never_clears_even_with_no_visible_later_message(self):
        api, writer, event = self.run_gap("eden", 46, False)
        api.post.assert_not_called()
        writer.generate.assert_not_called()
        self.assertEqual(event["status"], "human")

    def test_control_44_days_with_the_host_message_still_visible_goes_to_the_host(self):
        api, writer, event = self.run_gap("eden", 44, True)
        api.post.assert_not_called()
        self.assertEqual(event["status"], "human")

    def test_control_a_recent_resolved_handoff_with_nothing_since_gets_one_answer(self):
        api, writer, event = self.run_gap("eden", 1, False)
        api.post.assert_called_once()
        writer.generate.assert_called_once()
        self.assertEqual(event["status"], "queued")


def top_comment(cid, who, text, when):
    return dict(
        id=cid,
        postId="post",
        accountId="ig",
        platform="instagram",
        authorId=who,
        isAuthor=False,
        text=text,
        status="posted",
        parentCommentId=None,
        createdAt=when.isoformat(),
    )


class SecondTopLevelComment(unittest.TestCase):
    """P0-2: an open handoff covers the same guest's other top-level comments on that post."""

    DAY = datetime(2026, 11, 30, 8, tzinfo=CT)

    def run_day(self, brand, second_author, before_upgrade=False):
        feed, api, issues, writer = [], Mock(), Mock(), valid_writer()
        api.post.return_value = {"id": "queued"}
        issues.ensure.side_effect = lambda *a: issues.ensure.call_count
        issues.open.return_value = True
        listed = []

        def get(path, **params):
            if path == "/comments" and "parentCommentId" not in params:
                listed.append(params)
                rows = [c for c in feed if params.get("postId") in (None, c["postId"])]
                return {"items": list(reversed(rows))}
            return {"items": []}

        api.get.side_effect = get
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:

            def tick(h, m=0):
                process_inbox(brand, {"instagram": {"accountId": "ig"}}, store, self.DAY.replace(hour=h, minute=m), api, issues, writer, BUDGET)

            tick(8)
            refund = "I need to talk to the host about my refund. I will check back here at noon."
            feed.append(top_comment("first", "one", refund, self.DAY.replace(hour=9)))
            tick(9, 15)
            self.assertEqual(store.data["events"][event_ref(brand, "comments", "first")]["status"], "human")
            if before_upgrade:
                # A ledger from before this fix linked the handoff only to the first comment's own thread.
                root = event_ref(brand, "instagram/comments", "first")
                store.data["threads"] = {k: v for k, v in store.data["threads"].items() if k == root}
            tick(11, 45)
            feed.append(top_comment("second", second_author, "Are you available?", self.DAY.replace(hour=12)))
            tick(12, 15)
            return api, writer, store.data["events"][event_ref(brand, "comments", "second")], listed

    def test_same_guest_second_comment_stays_with_the_host(self):
        api, writer, event, _ = self.run_day("eden", "one")
        api.post.assert_not_called()
        writer.generate.assert_not_called()
        self.assertEqual(event["status"], "human")

    def test_same_guest_second_comment_held_for_a_handoff_opened_before_the_upgrade(self):
        api, writer, event, listed = self.run_day("eden", "one", before_upgrade=True)
        api.post.assert_not_called()
        writer.generate.assert_not_called()
        self.assertEqual(event["status"], "human")
        self.assertTrue(any(p.get("postId") == "post" for p in listed))  # the post's comments were read

    def test_control_a_different_commenters_question_gets_one_answer(self):
        api, writer, event, _ = self.run_day("eden", "two")
        api.post.assert_called_once()
        writer.generate.assert_called_once()
        self.assertEqual(event["status"], "queued")

    def test_control_acacia_never_replies(self):
        api, writer, event, _ = self.run_day("acacia", "one")
        api.post.assert_not_called()
        self.assertEqual(event["status"], "human")

    def test_unidentified_commenter_is_handed_to_the_host(self):
        from social.runtime import same_author_handed_off

        api, issues = Mock(), Mock()
        item = top_comment("x", None, "Are you available?", self.DAY)
        self.assertTrue(same_author_handed_off(api, "eden", "instagram", {"accountId": "ig"}, item, {}, {}, issues))
        api.get.side_effect = RemoteError(503)
        item["authorId"] = "one"
        self.assertTrue(same_author_handed_off(api, "eden", "instagram", {"accountId": "ig"}, item, {}, {}, issues))


def record_for(store, su):
    return store.data["batches"]["eden/" + (su.date() + timedelta(days=1)).isoformat()]


class PageMissingAtPreview(unittest.TestCase):
    """P1-1: a Page that was unavailable when Sunday's preview was built holds only Facebook."""

    def week(self, page_absent):
        cfg = valid_cfg()
        pages = {"page": "Stay Eden"}
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            api = blotato(pages)
            run = Ticks(self, store, cfg, api)
            su = sunday()
            run.approve(su - timedelta(weeks=2))
            run.approve(su - timedelta(weeks=1))
            cfg["brands"]["eden"]["autoPublish"] = True
            if page_absent:
                pages.clear()
            run.tick(su)
            record = record_for(store, su)
            pages.update(page="Stay Eden")  # Facebook recovers before Monday's slot
            for slot in slots(record["batch"]):
                run.tick(slot)
            notices = [t for t in run.numbers if "destinations changed" in t]
            return api, record, notices

    def test_verified_instagram_rows_post_and_facebook_stays_held(self):
        api, record, notices = self.week(True)
        self.assertEqual(sorted(record["batch"]["destinations"]), ["instagram"])
        self.assertEqual(len(sent_to(api, "instagram")), 7)
        self.assertEqual(sent_to(api, "facebook"), [])  # held, as the preview promised
        self.assertEqual(notices, [])

    def test_control_page_available_at_preview_time_sends_both_feeds(self):
        api, _, notices = self.week(False)
        self.assertEqual((len(sent_to(api, "instagram")), len(sent_to(api, "facebook"))), (7, 7))
        self.assertEqual(notices, [])

    def test_a_shown_feed_that_differs_from_the_confirmation_is_skipped_with_a_notice(self):
        cfg = valid_cfg()
        names = {"page": "Stay Eden"}
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            api = blotato(names)
            run = Ticks(self, store, cfg, api)
            su = sunday()
            run.approve(su - timedelta(weeks=2))
            run.approve(su - timedelta(weeks=1))
            cfg["brands"]["eden"]["autoPublish"] = True
            names["page"] = "Stay Eden Lodges"  # Blotato now reports a different name for the Page
            run.tick(su)
            record = record_for(store, su)
            run.tick(slots(record["batch"])[0])
            self.assertEqual(len(sent_to(api, "instagram")), 1)
            self.assertEqual(sent_to(api, "facebook"), [])
            self.assertTrue([t for t in run.numbers if "destinations changed" in t])


class ApprovalOrderAfterCorrection(unittest.TestCase):
    """P1-2: approving the original preview after its replacement never restores the discarded Page."""

    def week(self, replacement_first):
        cfg = valid_cfg()
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            posts = []
            api = blotato({"page": "Stay Eden", "page2": "Stay Eden"}, posts=posts)
            run = Ticks(self, store, cfg, api)
            su = sunday()
            run.tick(su)
            original = record_for(store, su)
            monday = slots(original["batch"])[0]
            cfg["brands"]["eden"]["targets"]["facebook"]["pageId"] = "page2"  # corrected before either approval
            run.tick(su + timedelta(hours=1))
            corrected = original["replacement"]
            first, second = (corrected, original) if replacement_first else (original, corrected)
            approve(run, first["issue"], first["batch"])
            run.tick(su + timedelta(hours=2))
            approve(run, second["issue"], second["batch"])
            run.tick(su + timedelta(hours=4))
            run.tick(su + timedelta(hours=5))
            api.get.reset_mock()
            run.tick(su + timedelta(hours=6))
            read_b = any(
                c.args[0] == "/messages" and c.kwargs.get("platform") == "facebook" for c in api.get.call_args_list
            )
            confirmed = store.data["approvedDestinations"]["eden"]["facebook"]["pageId"]
            for slot in slots(original["batch"]):
                run.tick(slot)
            fb_pages = [b["post"]["target"]["pageId"] for b in sent_to(api, "facebook")]
            posts.append(fb_row("reached", "page2", monday, 123))
            run.tick(monday + timedelta(days=7))
            report = run.bodies[store.data["digests"]["eden/" + original["batch"]["week"]]]
            return read_b, confirmed, fb_pages, len(sent_to(api, "instagram")), report

    def test_original_approved_last_keeps_the_corrected_page(self):
        read_b, confirmed, fb_pages, ig, report = self.week(True)
        self.assertEqual(confirmed, "page2")
        self.assertTrue(read_b)  # the corrected Page's inbox keeps running
        self.assertEqual(fb_pages, ["page2"] * 7)
        self.assertEqual(ig, 7)
        self.assertIn("Sum of post reach: 123 (1/1 IG/FB posts measured)", report)

    def test_control_replacement_approved_last(self):
        read_b, confirmed, fb_pages, ig, report = self.week(False)
        self.assertEqual((confirmed, read_b, ig), ("page2", True, 7))
        self.assertIn("Sum of post reach: 123 (1/1 IG/FB posts measured)", report)


class UpgradedMidweekLedger(unittest.TestCase):
    """P1-3: a ledger written by 9e2ace7 after a midweek Page correction still counts the first Page."""

    def setUp(self):
        self.ledger = json.loads((FIXTURES / "9e2ace7-midweek-ledger.json").read_text())
        self.assertNotIn("destinationHistory", self.ledger)  # really the earlier format

    def report(self, edit=None):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "state.json").write_text(json.dumps(self.ledger))
            with State(Path(tmp)) as store:
                week = "2099-11-30"
                times = slots(store.data["batches"]["eden/" + week]["batch"])
                if edit:
                    edit(store, times)
                rows = [fb_row("p1", "page", times[0], 123), fb_row("p2", "page2", times[2], 47)]
                api = blotato({"page": "Stay Eden", "page2": "Stay Eden"}, posts=rows)
                issues = Mock()
                issues.ensure.return_value = 1
                monday_digest("eden", store, times[-1] + timedelta(days=1), api, issues)
                return [c.args[1] for c in issues.ensure.call_args_list if "digest " + week in c.args[0]]

    def test_both_pages_count_from_the_delivery_evidence(self):
        reports = self.report()
        self.assertEqual(len(reports), 1)
        self.assertIn("Sum of post reach: 170 (2/2 IG/FB posts measured)", reports[0])
        self.assertNotIn("Partial", reports[0])

    def test_a_later_confirmation_of_the_corrected_page_does_not_erase_monday(self):
        def later(store, times):
            next_sunday = times[-1] - timedelta(hours=1)
            record_confirmation(store.data, "eden", store.data["approvedDestinations"]["eden"], next_sunday)

        reports = self.report(later)
        self.assertIn("Sum of post reach: 170 (2/2 IG/FB posts measured)", reports[0])

    def test_unrecoverable_destination_is_marked_partial(self):
        def unknown(store, times):
            store.data["writes"]["eden/" + times[0].date().isoformat() + "/facebook"]["batch"] = "gone"

        reports = self.report(unknown)
        self.assertIn("Partial", reports[0])
        self.assertIn("Facebook Page", reports[0].split("Partial", 1)[1])


class SundayDrafterOutage(unittest.TestCase):
    """P1-4: failing to draft next week never expires this week's approved Sunday posts."""

    def week(self, failure):
        cfg = valid_cfg()
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            api = blotato({"page": "Stay Eden"})
            run = Ticks(self, store, cfg, api)
            batch = run.approve(sunday())
            times = slots(batch)
            for slot in times[:-1]:
                run.tick(slot)
            with patch("social.runtime.write_week", side_effect=failure):
                for offset in range(-30, 31, 15):
                    run.tick(times[-1] + timedelta(minutes=offset))
            run.tick(times[-1] + timedelta(hours=1))
            status = store.data["writes"]["eden/" + times[-1].date().isoformat() + "/instagram"]["status"]
            weekly_notices = [t for t in run.numbers if "· weekly ·" in t]
            return api, status, weekly_notices

    def test_approved_sunday_posts_go_out_while_drafting_fails(self):
        api, status, notices = self.week(RemoteError(503))
        self.assertEqual((len(sent_to(api, "instagram")), len(sent_to(api, "facebook"))), (7, 7))
        self.assertNotEqual(status, "expired")
        self.assertTrue(notices)  # the drafting failure is still reported

    def test_the_shared_budget_stop_still_stops_the_cycle(self):
        cfg = valid_cfg()
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            api = blotato({"page": "Stay Eden"})
            run = Ticks(self, store, cfg, api)
            times = slots(run.approve(sunday()))
            for slot in times[:-1]:
                run.tick(slot)
            sent = api.post.call_count
            with (
                patch("social.runtime.write_week", side_effect=RunLimit("Claude budget reached")),
                patch("social.runtime.check"),
                patch("social.runtime.media_picker", return_value=lambda _: dict(PHOTO)),
                patch("social.runtime.recheck_repo", return_value="ok"),
            ):
                with self.assertRaises(RunLimit):
                    tick_brand("eden", cfg, store, times[-1], api, run.issues, valid_writer())
            self.assertEqual(api.post.call_count, sent)


if __name__ == "__main__":
    unittest.main()
