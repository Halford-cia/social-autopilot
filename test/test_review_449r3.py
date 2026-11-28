"""Regression tests for review 449r3 (PR #1). Every provider is mocked; nothing calls a live API."""

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from social.api import RemoteError
from social.planner import CT, identity, plan, preview
from social.runtime import State, process_inbox
from social.workflow import publish
from test_review_449r import BUDGET, dm, valid_writer
from test_review_449r2 import PHOTO, TARGETS, Ticks, dests, sunday, valid_cfg


def blotato(page_names, *, posts=()):
    """Mock Blotato: connected accounts, Facebook Pages {id: name}, published posts, submissions."""
    api = Mock()

    def get(path, **params):
        if path == "/users/me/accounts":
            return {
                "items": [
                    dict(id="ig", platform="instagram", username="stayeden_fredericksburgtx"),
                    dict(id="fb", platform="facebook", username=""),
                ]
            }
        if path.endswith("/subaccounts"):
            return {"items": [dict(id=i, accountId="fb", name=n) for i, n in page_names.items()]}
        if path == "/posts":
            return {"items": list(posts)}
        if path.startswith("/posts/"):
            return {"status": "in-progress"}
        return {"items": []}

    api.get.side_effect = get
    api.post.side_effect = lambda path, body: {"postSubmissionId": "s-" + str(api.post.call_count)}
    return api


def sent_to(api, platform):
    return [c.args[1] for c in api.post.call_args_list if c.args[1]["post"]["target"]["targetType"] == platform]


def titles(run):
    return [c.args[0] for c in run.issues.ensure.call_args_list]


def every_quarter(start, end):
    t = start
    while t <= end:
        yield t
        t += timedelta(minutes=15)


class LateChangeAfterSubmission(unittest.TestCase):
    """P0-1: nothing sits in Blotato's queue where a later switch-off or withdrawal cannot reach it."""

    def eden_auto(self, switch):
        start = date(2026, 12, 14)
        batch = plan("eden", start, lambda _: dict(PHOTO), TARGETS, destinations=dests())
        state = {
            "approvals": {"eden": ["2026-11-30", "2026-12-07"]},
            "approvedDestinations": {"eden": dests()},
            "writes": {},
        }
        sent = []
        friday = datetime(2026, 12, 18, 16, 30, tzinfo=CT)
        for now in every_quarter(friday, friday + timedelta(hours=3, minutes=15)):
            publish(
                batch,
                state,
                lambda: None,
                now,
                switch(now),
                123,
                preview(batch),
                [],
                lambda body: sent.append(body) or {"postSubmissionId": "x"},
                clock=lambda: now,
                current=dests(),
                recheck=lambda _: "ok",
            )
        return state, sent

    def test_switch_off_at_530_stops_the_730_post(self):
        off = datetime(2026, 12, 18, 17, 30, tzinfo=CT)
        state, sent = self.eden_auto(lambda now: now < off)
        self.assertEqual(sent, [])
        self.assertFalse([k for k in state["writes"] if "2026-12-18" in k])

    def test_control_with_the_switch_on_the_post_goes_at_its_slot_with_no_future_schedule(self):
        state, sent = self.eden_auto(lambda now: True)
        self.assertEqual(len(sent), 2)
        for body in sent:
            # Sent when the slot arrives, after every check: Blotato publishes it immediately.
            self.assertNotIn("scheduledTime", body)
            self.assertNotIn("scheduledTime", body["post"])
        friday = {k: r["status"] for k, r in state["writes"].items() if "2026-12-18" in k}
        self.assertEqual(set(friday.values()), {"accepted"})

    def test_tag_withdrawn_two_hours_before_the_slot_holds_every_row(self):
        targets = {
            "instagram": {"accountId": "ig"},
            "facebook": {"accountId": "fb", "pageId": "page"},
            "googlebusiness": {"accountId": "gb", "locationId": "accounts/1/locations/2"},
        }
        names = {"instagram": "@acaciaroofing", "facebook": "Acacia Roofing", "googlebusiness": "Acacia Roofing"}
        source = {"kind": "companycam", "id": "7", "version": "10/abc"}
        batch = plan(
            "acacia",
            date(2026, 12, 14),
            lambda _: {"url": "https://example.org/safe.jpg", "source": source},
            targets,
            destinations=dests(targets, names),
        )
        state = {"approvals": {}, "writes": {}}
        comment = {"user": {"id": 123}, "body": "/approve " + identity(batch), "edited": False}
        sent, rechecks = [], []
        withdrawn = datetime(2026, 12, 18, 8, tzinfo=CT)
        for now in every_quarter(datetime(2026, 12, 18, 7, tzinfo=CT), datetime(2026, 12, 18, 10, 30, tzinfo=CT)):

            def recheck(day, now=now):
                rechecks.append(now)
                return "held" if now >= withdrawn else "ok"

            publish(
                batch,
                state,
                lambda: None,
                now,
                False,
                123,
                preview(batch),
                [comment],
                lambda body: sent.append(body) or {"postSubmissionId": "x"},
                clock=lambda now=now: now,
                current=dests(targets, names),
                recheck=recheck,
            )
        self.assertEqual(sent, [])
        friday = {k: r["status"] for k, r in state["writes"].items() if "2026-12-18" in k}
        self.assertEqual(friday, dict.fromkeys(friday, "held"))
        self.assertEqual(len(friday), 3)
        self.assertTrue(rechecks)  # the source was checked again at the slot

    def test_a_slot_missed_by_more_than_the_grace_period_expires_unsent(self):
        batch = plan("eden", date(2026, 12, 14), lambda _: dict(PHOTO), TARGETS, destinations=dests())
        state = {"approvals": {}, "writes": {}}
        comment = {"user": {"id": 123}, "body": "/approve " + identity(batch), "edited": False}
        late = datetime(2026, 12, 14, 19, 30, tzinfo=CT) + timedelta(hours=1)
        sent = []
        publish(
            batch, state, lambda: None, late, False, 123, preview(batch), [comment],
            lambda body: sent.append(body) or {"postSubmissionId": "x"},
            clock=lambda: late, current=dests(), recheck=lambda _: "ok",
        )
        self.assertEqual(sent, [])
        self.assertEqual(state["writes"]["eden/2026-12-14/instagram"]["status"], "expired")


def comment(cid, text, stamp, parent=None):
    return dict(
        id=cid,
        platform="instagram",
        accountId="a",
        authorId="fixture-guest",
        postId="post",
        isAuthor=False,
        status="posted",
        parentCommentId=parent,
        text=text,
        createdAt=stamp.isoformat(),
    )


class CommentThread(unittest.TestCase):
    """P0-2: a public question is not answered when its thread holds a clarification or a handoff."""

    def run_inbox(self, feed, replies):
        now = datetime(2026, 11, 30, 12, 50, tzinfo=CT)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            store.data["inboxSince"]["eden/instagram/a"] = (now - timedelta(hours=1)).isoformat()
            api, issues, writer = Mock(), Mock(), valid_writer()
            issues.ensure.side_effect = lambda title, body: 10 + issues.ensure.call_count
            issues.open.return_value = True
            api.post.return_value = {"id": "sent"}

            def get(path, **params):
                if path == "/comments" and "parentCommentId" in params:
                    if isinstance(replies, Exception):
                        raise replies
                    return {"items": [c for c in replies if c["parentCommentId"] == params["parentCommentId"]]}
                return {"items": feed if path == "/comments" else []}

            api.get.side_effect = get
            process_inbox("eden", {"instagram": {"accountId": "a"}}, store, now, api, issues, writer, BUDGET)
            return api, issues, writer

    def thread(self):
        t = datetime(2026, 11, 30, 12, 5, tzinfo=CT)
        parent = comment("P", "Are you available?", t)
        child = comment("C", "Actually I need help with a refund for my last stay", t + timedelta(minutes=45), "P")
        return parent, child

    def test_refund_clarification_under_the_question_stops_the_reply(self):
        parent, child = self.thread()
        for feed in ([child, parent], [parent, child]):
            api, issues, writer = self.run_inbox(feed, [child])
            api.post.assert_not_called()
            writer.generate.assert_not_called()
            issues.ensure.assert_called()

    def test_unreadable_replies_keep_the_thread_with_the_host(self):
        parent, _ = self.thread()
        api, issues, writer = self.run_inbox([parent], RemoteError(503))
        api.post.assert_not_called()
        writer.generate.assert_not_called()

    def test_open_handoff_in_the_thread_holds_the_question_even_with_no_listed_reply(self):
        now = datetime(2026, 11, 30, 12, 50, tzinfo=CT)
        parent = comment("P", "Are you available?", now - timedelta(minutes=3))
        child = comment("C", "Actually I need help with a refund", now - timedelta(minutes=2), "P")
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            store.data["inboxSince"]["eden/instagram/a"] = (now - timedelta(hours=1)).isoformat()
            api, issues, writer = Mock(), Mock(), valid_writer()
            issues.ensure.return_value = 21
            issues.open.return_value = True
            api.post.return_value = {"id": "sent"}
            feed = {"items": [child]}
            api.get.side_effect = lambda path, **p: (
                {"items": []} if "parentCommentId" in p else feed if path == "/comments" else {"items": []}
            )
            target = {"instagram": {"accountId": "a"}}
            process_inbox("eden", target, store, now, api, issues, writer, BUDGET)  # the reply is handed off
            feed = {"items": [parent]}  # the question arrives late; the guest's reply is no longer listed
            process_inbox("eden", target, store, now + timedelta(minutes=5), api, issues, writer, BUDGET)
            api.post.assert_not_called()
            writer.generate.assert_not_called()
            from social.runtime import event_ref

            self.assertEqual(store.data["events"][event_ref("eden", "comments", "P")]["issue"], 21)

    def test_control_a_question_with_no_replies_gets_one_answer(self):
        parent, _ = self.thread()
        api, issues, _ = self.run_inbox([parent], [])
        api.post.assert_called_once()
        self.assertEqual(api.post.call_args.args[1]["parentCommentId"], "P")


def legacy_batch(start):
    """A round-two batch: destinations stored as name strings, no IDs."""
    batch = plan("eden", start, lambda _: dict(PHOTO), TARGETS)
    batch["destinations"] = {"instagram": "@stayeden_fredericksburgtx", "facebook": "Stay Eden"}
    return batch


def slots(batch):
    return [datetime.fromisoformat(d["time"]) for d in batch["days"]]


class LegacyPreview(unittest.TestCase):
    """P1-1: a pending round-two preview is replaced, not crashed on, and its receipts are kept."""

    def test_old_preview_gets_a_replacement_with_its_own_approval(self):
        now = sunday()
        week = (now.date() + timedelta(days=1)).isoformat()
        old = legacy_batch(date.fromisoformat(week))
        first_slot = old["days"][0]["time"]
        receipt = {"status": "accepted", "id": "old", "due": first_slot, "batch": "x"}
        api = blotato({"page": "Stay Eden"})
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            store.data["batches"]["eden/" + week] = {"batch": old, "issue": 1}
            store.data["writes"]["eden/" + first_slot[:10] + "/instagram"] = dict(receipt)
            run = Ticks(self, store, valid_cfg(), api)
            run.numbers["Stay Eden · approve " + week] = 1
            run.bodies[1] = "round-two preview"
            run.tick(now)  # raised AttributeError before the fix
            record = store.data["batches"]["eden/" + week]
            self.assertIn("replacement", record)
            new = record["replacement"]["batch"]
            self.assertNotEqual(record["replacement"]["issue"], 1)
            self.assertEqual(run.bodies[record["replacement"]["issue"]], preview(new))
            self.assertIn("page", preview(new))  # the Page ID is shown
            self.assertEqual(store.data["writes"]["eden/" + first_slot[:10] + "/instagram"], receipt)
            # The old approval command does not post; the replacement's own command does.
            run.comments[1] = [{"user": {"id": 123}, "body": "/approve " + identity(old), "edited": False}]
            run.tick(slots(new)[0])
            self.assertEqual(api.post.call_count, 0)
            run.comments[record["replacement"]["issue"]] = [
                {"user": {"id": 123}, "body": "/approve " + identity(new), "edited": False}
            ]
            for slot in slots(new):
                run.tick(slot)
            self.assertEqual(len(sent_to(api, "instagram")), 6)  # Monday's Instagram receipt is kept, not resent
            self.assertEqual(len(sent_to(api, "facebook")), 7)
            self.assertEqual(store.data["writes"]["eden/" + first_slot[:10] + "/instagram"]["id"], "old")
            self.assertFalse([t for t in titles(run) if "· weekly ·" in t])


class CorrectedPage(unittest.TestCase):
    """P1-2: a corrected Page ID gets its own preview; the rest of the approved week keeps going."""

    def test_instagram_continues_and_the_new_page_waits_for_its_own_approval(self):
        cfg = valid_cfg()
        api = blotato({"page": "Stay Eden", "page2": "Stay Eden"})
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            run = Ticks(self, store, cfg, api)
            now = sunday()
            batch = run.approve(now)
            cfg["brands"]["eden"]["targets"]["facebook"]["pageId"] = "page2"
            times = slots(batch)
            run.tick(times[0])  # Monday: Instagram goes, the corrected Page waits
            self.assertEqual(len(sent_to(api, "instagram")), 1)
            self.assertEqual(sent_to(api, "facebook"), [])
            record = store.data["batches"]["eden/" + batch["week"]]
            new = record["replacement"]["batch"]
            self.assertEqual(set(new["targets"]), {"facebook"})
            body = run.bodies[record["replacement"]["issue"]]
            self.assertEqual(body, preview(new))
            self.assertIn("page2", body)  # identically named Pages are told apart by ID
            self.assertIn("corrected destinations", [t for t in titles(run) if "approve" in t][-1])
            run.comments[record["replacement"]["issue"]] = [
                {"user": {"id": 123}, "body": "/approve " + identity(new), "edited": False}
            ]
            for slot in times[1:]:
                run.tick(slot)
            self.assertEqual(len(sent_to(api, "instagram")), 7)
            fb = sent_to(api, "facebook")
            self.assertEqual(len(fb), 6)
            self.assertEqual({b["post"]["target"]["pageId"] for b in fb}, {"page2"})
            self.assertFalse([t for t in titles(run) if "· weekly ·" in t])
            # Hank's approval of the corrected Page also confirms it; Instagram stays confirmed.
            confirmed = store.data["approvedDestinations"]["eden"]
            self.assertEqual(confirmed["facebook"]["pageId"], "page2")
            self.assertEqual(confirmed["instagram"]["accountId"], "ig")


class ReplacementPageInbox(unittest.TestCase):
    """P1-3: a different Page starts its own inbox; the same Page recovering keeps its cursor."""

    def run_inbox(self, store, now, dest, messages, page):
        api, issues, writer = Mock(), Mock(), valid_writer()
        api.post.return_value = {"id": "sent"}
        issues.ensure.return_value = 3

        def get(path, **params):
            if path == "/messages":
                if params.get("conversationId"):
                    return {"items": [m for m in messages if m["conversationId"] == params["conversationId"]]}
                return {"items": messages}
            if path.startswith("/conversations/"):
                m = messages[0]
                return {"id": m["conversationId"], "accountId": "fb", "platform": "facebook", "createdAt": m["createdAt"]}
            return {"items": []}

        api.get.side_effect = get
        process_inbox("eden", {"facebook": dest}, store, now, api, issues, writer, BUDGET)
        return api

    def question(self, page):
        q = dm("q-" + page, "Are you available?", datetime(2026, 12, 7, 9, 30, tzinfo=CT), "thread-" + page)
        return q | dict(platform="facebook", recipientId=page)

    def setup_old_page(self, store):
        old = {"accountId": "fb", "pageId": "pageA"}
        self.run_inbox(store, datetime(2026, 11, 30, 12, tzinfo=CT), old, [], "pageA")
        self.run_inbox(store, datetime(2026, 12, 7, 8, tzinfo=CT), old, [], "pageA")
        return old

    def test_new_page_does_not_answer_a_question_from_before_its_approval(self):
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            self.setup_old_page(store)
            new = {"accountId": "fb", "pageId": "pageB"}
            q = [self.question("pageB")]
            for now in (datetime(2026, 12, 7, 12, tzinfo=CT), datetime(2026, 12, 7, 12, 15, tzinfo=CT)):
                api = self.run_inbox(store, now, new, q, "pageB")
                api.post.assert_not_called()

    def test_same_page_recovering_from_an_outage_keeps_its_cursor(self):
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            old = self.setup_old_page(store)
            api = self.run_inbox(store, datetime(2026, 12, 7, 12, 15, tzinfo=CT), old, [self.question("pageA")], "pageA")
            api.post.assert_called_once()


class FirstDigest(unittest.TestCase):
    """P1-4: the first Monday digest waits for confirmed destinations instead of closing empty."""

    def row(self):
        return dict(
            id="fb-post",
            platform="facebook",
            account={"id": "fb", "subaccount": {"id": "page"}},
            postTime=datetime(2099, 12, 2, 12, tzinfo=CT).isoformat(),
            state={"type": "published", "analytics": {"latest": {"metrics": {"reachCount": "123"}}}},
        )

    def digests(self, run):
        return [c.args[1] for c in run.issues.ensure.call_args_list if "digest" in c.args[0]]

    def test_digest_after_the_first_confirmation_counts_last_weeks_reach(self):
        now = datetime(2099, 12, 7, 9, tzinfo=CT)
        api = blotato({"page": "Stay Eden"}, posts=[self.row()])
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            run = Ticks(self, store, valid_cfg(), api)
            run.tick(now)
            self.assertEqual(self.digests(run), [])
            self.assertNotIn("eden/2099-11-30", store.data["digests"])
            record = store.data["batches"]["eden/2099-12-07"]
            run.comments[record["issue"]] = [
                {"user": {"id": 123}, "body": "/approve " + identity(record["batch"]), "edited": False}
            ]
            run.tick(now + timedelta(minutes=15))
            run.tick(now + timedelta(minutes=30))
            reports = self.digests(run)
            self.assertEqual(len(reports), 1)
            self.assertIn("123", reports[0])
            self.assertIn("(1/1 IG/FB posts measured)", reports[0])

    def test_digest_never_confirmed_is_posted_as_partial_by_sunday(self):
        api = blotato({"page": "Stay Eden"}, posts=[self.row()])
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            run = Ticks(self, store, valid_cfg(), api)
            run.tick(datetime(2099, 12, 7, 9, tzinfo=CT))
            self.assertEqual(self.digests(run), [])
            run.tick(datetime(2099, 12, 13, 9, tzinfo=CT))
            reports = self.digests(run)
            self.assertEqual(len(reports), 1)
            self.assertIn("Partial", reports[0])


class NegativeBudgetAssertion(unittest.TestCase):
    """P1-5: the 449r negative-budget test fails when the contact validator is removed."""

    def test_original_test_detects_a_missing_validator(self):
        import test_review_449r

        case = test_review_449r.ContactBudget("test_example_config_sets_an_explicit_contact_budget")
        with patch("social.runtime.validate_contact_limits", lambda limits: None):
            result = unittest.TestResult()
            case.run(result)
        self.assertEqual(len(result.failures) + len(result.errors), 1)
        # And it passes with the validator in place.
        result = unittest.TestResult()
        test_review_449r.ContactBudget("test_example_config_sets_an_explicit_contact_budget").run(result)
        self.assertTrue(result.wasSuccessful())


if __name__ == "__main__":
    unittest.main()
