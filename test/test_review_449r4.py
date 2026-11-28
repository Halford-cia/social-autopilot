"""Regression tests for review 449r4 (PR #1). Every provider is mocked; nothing calls a live API."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from test_review_449r import BUDGET, dm, valid_writer
from test_review_449r2 import Ticks, dests, sunday, valid_cfg
from test_review_449r3 import blotato, sent_to, slots, titles

from social.planner import CT, identity, preview
from social.runtime import State, event_ref, inbox_key, process_inbox

FIXTURES = Path(__file__).parent / "fixtures"
APPROVER = 123


def approve(run, number, batch):
    run.comments[number] = [{"user": {"id": APPROVER}, "body": "/approve " + identity(batch), "edited": False}]


def outgoing(mid, text, stamp, conversation="thread"):
    """A message the host typed in Blotato's inbox (or the Instagram app), not one the Mini sent."""
    return dm(mid, text, stamp, conversation) | dict(direction="outgoing", senderId="stay-eden-account")


class LaterHostMessage(unittest.TestCase):
    """P0: a closed handoff clears only the history the host resolved, not what the host wrote later."""

    DAY = datetime(2026, 11, 30, tzinfo=CT)

    def run_day(self, host_wrote_again):
        at = lambda h, m=0: self.DAY.replace(hour=h, minute=m)
        conversation = [dm("m1", "We need to change our reservation dates", at(8))]
        reply = outgoing("h1", "Done, your new dates are set.", at(8, 30))
        later = outgoing("h2", "When can we talk about the guest's refund?", at(10))
        question = dm("m2", "Are you available?", at(12, 10))
        closed = at(9).isoformat()  # Hank closed the handoff after resolving the change
        timeline = [(at(8), [conversation[0]]), (at(8, 30), [reply])]
        if host_wrote_again:
            timeline.append((at(10), [later]))
        timeline.append((at(12, 10), [question]))
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            dest = {"accountId": "a"}
            start = at(7).isoformat()
            key = inbox_key("eden", "instagram", dest)
            store.data["inboxSince"][key] = store.data.setdefault("activated", {})[key] = start
            api, issues, writer = Mock(), Mock(), valid_writer()
            api.post.return_value = {"id": "sent"}
            issues.ensure.return_value = 7
            now = {"t": at(8)}
            issues.open.side_effect = lambda n: now["t"] < at(9)
            issues.closed_at.side_effect = lambda n: None if now["t"] < at(9) else closed

            def visible():
                return [m for when, ms in timeline if when <= now["t"] for m in ms]

            def get(path, **params):
                if path.startswith("/conversations/"):
                    return {"id": "thread", "accountId": "a", "platform": "instagram", "createdAt": at(8).isoformat()}
                if path == "/messages":
                    return {"items": sorted(visible(), key=lambda m: m["createdAt"], reverse=True)}
                return {"items": []}

            api.get.side_effect = get
            for tick in (at(8), at(9, 15), at(10, 15), at(10, 30), at(12, 15)):
                now["t"] = tick
                process_inbox("eden", {"instagram": dest}, store, tick, api, issues, writer, BUDGET)
            return api, writer, store

    def test_host_follow_up_after_the_closed_handoff_keeps_the_question_with_the_host(self):
        api, writer, store = self.run_day(host_wrote_again=True)
        api.post.assert_not_called()
        writer.generate.assert_not_called()  # no Haiku call either
        self.assertEqual(store.data["events"][event_ref("eden", "messages", "m2")]["status"], "human")

    def test_control_a_simple_question_after_the_resolved_history_gets_one_answer(self):
        api, writer, _ = self.run_day(host_wrote_again=False)
        api.post.assert_called_once()
        writer.generate.assert_called_once()  # one Haiku call for the one answer
        self.assertEqual(api.post.call_args.args[1]["recipientId"], "guest")


class PreviousRender(unittest.TestCase):
    """P1-1: a preview rendered by b98b864 (no Page ID) gets a replacement instead of stalling silently."""

    def setUp(self):
        self.saved = json.loads((FIXTURES / "b98b864-preview.json").read_text())
        self.batch, self.body = self.saved["batch"], self.saved["body"]

    def seeded(self, store, body):
        week = self.batch["week"]
        run = Ticks(self, store, valid_cfg(), self.api)
        store.data["batches"]["eden/" + week] = {"batch": self.batch, "issue": 1}
        run.numbers["Stay Eden · approve " + week] = 1
        run.bodies[1] = body
        approve(run, 1, self.batch)
        # What b98b864 recorded when it applied that approval on Sunday.
        store.data["approvals"]["eden"] = [week]
        store.data["approvedDestinations"] = {"eden": dests()}
        monday = self.batch["days"][0]["time"]
        store.data["writes"]["eden/" + monday[:10] + "/instagram"] = {
            "status": "accepted", "id": "old", "due": monday, "batch": self.saved["token"]
        }
        return run

    def test_fixture_is_the_previous_rendering_and_keeps_its_token(self):
        self.assertEqual(identity(self.batch), self.saved["token"])
        self.assertNotIn("Page ID", self.body)
        self.assertNotEqual(preview(self.batch), self.body)

    def test_upgrade_offers_a_new_approval_and_keeps_receipts(self):
        self.api = blotato({"page": "Stay Eden"})
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            run = self.seeded(store, self.body)
            run.tick(sunday())
            record = store.data["batches"]["eden/" + self.batch["week"]]
            self.assertIn("replacement", record)
            new = record["replacement"]["batch"]
            self.assertEqual(run.bodies[record["replacement"]["issue"]], preview(new))
            self.assertIn("Page ID `page`", preview(new))
            for slot in slots(new)[:2]:
                run.tick(slot)  # the old approval alone posts nothing
            self.assertEqual(self.api.post.call_count, 0)
            approve(run, record["replacement"]["issue"], new)
            for slot in slots(new)[2:]:
                run.tick(slot)
            # Monday's receipt is kept; Tuesday expired while waiting; Wednesday to Sunday go out.
            self.assertEqual(len(sent_to(self.api, "instagram")), 5)
            self.assertEqual(len(sent_to(self.api, "facebook")), 5)
            monday = self.batch["days"][0]["time"][:10]
            self.assertEqual(store.data["writes"]["eden/" + monday + "/instagram"]["id"], "old")
            self.assertFalse([t for t in titles(run) if "· weekly ·" in t])

    def test_control_an_edited_previous_preview_still_holds(self):
        self.api = blotato({"page": "Stay Eden"})
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            run = self.seeded(store, self.body.replace("Stay Eden”", "Stay Eden (edited)”", 1))
            run.tick(sunday())
            for slot in slots(self.batch):
                run.tick(slot)
            self.assertEqual(self.api.post.call_count, 0)
            self.assertNotIn("replacement", store.data["batches"]["eden/" + self.batch["week"]])


def facebook_dm(mid, page, stamp, conversation):
    return dm(mid, "Are you available?", stamp, conversation) | dict(platform="facebook", recipientId=page)


def with_inbox(api, messages):
    """Add a DM feed to the Blotato mock: messages appear once their creation time has passed."""
    base = api.get.side_effect
    clock = {"now": None}

    def get(path, **params):
        if path == "/messages" and params.get("platform") == "facebook":
            items = [m for m in messages if datetime.fromisoformat(m["createdAt"]) <= clock["now"]]
            if params.get("conversationId"):
                items = [m for m in items if m["conversationId"] == params["conversationId"]]
            return {"items": items}
        if path.startswith("/conversations/"):
            m = next(m for m in messages if path.endswith(m["conversationId"]))
            return {"id": m["conversationId"], "accountId": "fb", "platform": "facebook", "createdAt": m["createdAt"]}
        return base(path, **params)

    api.get.side_effect = get
    return clock


class LegacyCursorOwner(unittest.TestCase):
    """P1-2: on upgrade, a platform-wide inbox cursor goes only to the Page it was confirmed for."""

    def upgrade(self, page):
        cfg = valid_cfg()
        api = blotato({"page": "Stay Eden", "page2": "Stay Eden"})
        monday = sunday() + timedelta(days=1)
        question = facebook_dm("q", page, monday.replace(hour=9, minute=30), "thread-" + page)
        clock = with_inbox(api, [question])
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            run = Ticks(self, store, cfg, api)
            run.approve(sunday())  # this week's preview, Page A, approved
            # The previous version's ledger: one cursor per platform, the inbox started Sunday on Page A.
            for platform in ("instagram", "facebook"):
                started = sunday().isoformat()
                for book in (store.data["inboxSince"], store.data.setdefault("activated", {})):
                    for k in [k for k in book if k.startswith("eden/" + platform + "/")]:
                        del book[k]
                    book["eden/" + platform] = started
            if page == "page2":
                cfg["brands"]["eden"]["targets"]["facebook"]["pageId"] = "page2"  # the operator corrects it
            for t in (monday.replace(hour=8), monday.replace(hour=12)):
                clock["now"] = t
                run.tick(t)
            record = store.data["batches"]["eden/" + monday.date().isoformat()]
            if page == "page2":
                approve(run, record["replacement"]["issue"], record["replacement"]["batch"])
            for t in (monday.replace(hour=12, minute=15), monday.replace(hour=12, minute=30)):
                clock["now"] = t
                run.tick(t)
            return [c for c in api.post.call_args_list if c.args[0] == "/messages"]

    def test_replacement_page_does_not_inherit_the_old_pages_inbox_start(self):
        self.assertEqual(self.upgrade("page2"), [])

    def test_control_the_same_page_keeps_its_cursor_through_the_upgrade(self):
        replies = self.upgrade("page")
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].args[1]["target"]["pageId"], "page")


class CloseTimeAndOwner(unittest.TestCase):
    """Edges of the P0 and P1-2 fixes."""

    def test_github_close_time_in_utc_z_form_is_read(self):
        from social.runtime import handoffs_closed_at

        issues = Mock()
        issues.closed_at.side_effect = {7: "2026-11-30T15:00:00Z", 8: "2026-11-30T16:30:00Z"}.get
        self.assertEqual(handoffs_closed_at(issues, [7, 8]), datetime(2026, 11, 30, 10, 30, tzinfo=CT))
        issues.closed_at.side_effect = lambda n: None  # reopened in between: no clearance
        self.assertIsNone(handoffs_closed_at(issues, [7]))

    def test_a_round_two_confirmation_without_ids_starts_every_inbox_fresh(self):
        from social.runtime import adopt_legacy_cursors

        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            store.data["inboxSince"] = {"eden/instagram": "2026-11-29T12:00:00-06:00", "eden/facebook": "x"}
            store.data["activated"] = {"eden/instagram": "2026-11-29T12:00:00-06:00"}
            store.data["approvedDestinations"] = {
                "eden": {"instagram": dests()["instagram"], "facebook": "Stay Eden"}  # names only for the Page
            }
            adopt_legacy_cursors("eden", store)
            self.assertEqual(store.data["inboxSince"], {"eden/instagram/ig": "2026-11-29T12:00:00-06:00"})
            self.assertEqual(store.data["activated"], {"eden/instagram/ig": "2026-11-29T12:00:00-06:00"})


class AutomaticWeek(unittest.TestCase):
    """P1-3 and P1-4: corrected destinations in a week that posts automatically."""

    def auto_run(self, store, api, cfg):
        cfg["brands"]["eden"]["autoPublish"] = True
        run = Ticks(self, store, cfg, api)
        week = (sunday().date() + timedelta(days=1)).isoformat()
        store.data["approvals"]["eden"] = ["2099-11-16", "2099-11-23"]  # two approved weeks on Page A
        store.data["approvedDestinations"] = {"eden": dests()}
        return run, week

    def test_corrected_preview_waits_for_its_own_approval(self):
        cfg = valid_cfg()
        api = blotato({"page": "Stay Eden", "page2": "Stay Eden"})
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            run, week = self.auto_run(store, api, cfg)
            cfg["brands"]["eden"]["targets"]["facebook"]["pageId"] = "page2"
            run.tick(sunday())  # week three's preview shows Page B
            self.assertIn("page2", run.bodies[store.data["batches"]["eden/" + week]["issue"]])
            cfg["brands"]["eden"]["targets"]["facebook"]["pageId"] = "page"  # corrected back, before the slot
            first = slots(store.data["batches"]["eden/" + week]["batch"])[0]
            for t in (first - timedelta(hours=5), first):
                run.tick(t)
            record = store.data["batches"]["eden/" + week]
            self.assertIn("replacement", record)
            self.assertEqual(run.comments, {})  # no approval on either issue
            self.assertEqual(sent_to(api, "facebook"), [])
            self.assertEqual(len(sent_to(api, "instagram")), 1)  # the unchanged feed keeps posting

    def test_approving_a_new_page_keeps_the_unchanged_instagram_feed_running(self):
        cfg = valid_cfg()
        api = blotato({"page": "Stay Eden", "page2": "Stay Eden"})
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            run, week = self.auto_run(store, api, cfg)
            run.tick(sunday())
            times = slots(store.data["batches"]["eden/" + week]["batch"])
            run.tick(times[0])  # Monday: both automatic posts go out
            self.assertEqual((len(sent_to(api, "instagram")), len(sent_to(api, "facebook"))), (1, 1))
            cfg["brands"]["eden"]["targets"]["facebook"]["pageId"] = "page2"
            run.tick(times[1] - timedelta(hours=4))
            record = store.data["batches"]["eden/" + week]
            approve(run, record["replacement"]["issue"], record["replacement"]["batch"])
            run.tick(times[1] - timedelta(hours=2))
            for t in times[1:]:
                run.tick(t)
            self.assertEqual(len(sent_to(api, "instagram")), 7)
            fb = [b["post"]["target"]["pageId"] for b in sent_to(api, "facebook")]
            self.assertEqual(fb, ["page"] + ["page2"] * 6)


def fb_row(pid, page, stamp, reach):
    return dict(
        id=pid,
        platform="facebook",
        account={"id": "fb", "subaccount": {"id": page}},
        postTime=stamp.isoformat(),
        state={"type": "published", "analytics": {"latest": {"metrics": {"reachCount": str(reach)}}}},
    )


class MidweekPageDigest(unittest.TestCase):
    """P1-5: the digest counts each approved Page for the part of the week it was in use, and only those."""

    def week(self, approve_replacement, rows):
        cfg = valid_cfg()
        posts = []
        api = blotato({"page": "Stay Eden", "page2": "Stay Eden"}, posts=posts)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            run = Ticks(self, store, cfg, api)
            batch = run.approve(sunday())
            times = slots(batch)
            run.tick(times[0])
            cfg["brands"]["eden"]["targets"]["facebook"]["pageId"] = "page2"
            run.tick(times[1] - timedelta(hours=4))
            record = store.data["batches"]["eden/" + batch["week"]]
            if approve_replacement:
                approve(run, record["replacement"]["issue"], record["replacement"]["batch"])
            for t in times[1:]:
                run.tick(t)
            posts.extend(rows(times))
            run.tick(times[0] + timedelta(days=7, hours=1))  # next Monday
            title = "Social · Stay Eden · digest " + batch["week"]
            return [c.args[1] for c in run.issues.ensure.call_args_list if c.args[0] == title]

    def test_both_successively_approved_pages_count(self):
        reports = self.week(True, lambda t: [fb_row("a1", "page", t[0], 123), fb_row("b1", "page2", t[2], 47)])
        self.assertEqual(len(reports), 1)
        self.assertIn("Sum of post reach: 170 (2/2 IG/FB posts measured)", reports[0])

    def test_an_unapproved_replacement_page_is_not_counted(self):
        reports = self.week(False, lambda t: [fb_row("a1", "page", t[0], 123), fb_row("x", "page2", t[3], 999)])
        self.assertEqual(len(reports), 1)
        self.assertIn("Sum of post reach: 123 (1/1 IG/FB posts measured)", reports[0])
        self.assertNotIn("999", reports[0])


if __name__ == "__main__":
    unittest.main()
