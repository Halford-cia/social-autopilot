"""Regression tests for review 449r2 (PR #1). Every provider is mocked; Git remotes are local paths."""

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from social.cli import load_example
from social.planner import CT, identity, plan, preview
from social.runtime import State, audit_delivery, process_inbox, tick_brand, validate_config
from social.workflow import publish
from test_review_449r import BUDGET, dm, valid_writer

TARGETS = {"instagram": {"accountId": "ig"}, "facebook": {"accountId": "fb", "pageId": "page"}}
SOURCE = {"kind": "repo", "path": "public/images/x-2.webp", "blob": "b"}
PHOTO = {"url": "https://example.org/safe.jpg", "source": SOURCE}


def dests(targets=TARGETS, names=None):
    """The destinations check_destinations resolves: each configured ID plus the name Blotato reports."""
    names = names or {"instagram": "@stayeden_fredericksburgtx", "facebook": "Stay Eden"}
    return {p: dict(targets[p], name=names[p]) for p in targets if p in names}


def valid_cfg():
    """A config that passes validate_config as it stands."""
    cfg = load_example()
    cfg["approverId"] = 123
    cfg["operationsRepo"] = "owner/ops"
    for brand, block in cfg["brands"].items():
        for platform, dest in block["targets"].items():
            for key in dest:
                dest[key] = f"{brand}-{platform}-{key}"
    cfg["brands"]["eden"]["targets"] = json.loads(json.dumps(TARGETS))
    return cfg


def blotato(page_names, *, posts=(), subaccounts_fail=False):
    """Mock Blotato: connected accounts, Facebook Pages {id: name}, published posts and submissions."""
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
            if subaccounts_fail:
                from social.api import RemoteError

                raise RemoteError(503)
            return {"items": [dict(id=i, accountId="fb", name=n) for i, n in page_names.items()]}
        if path == "/posts":
            return {"items": list(posts)}
        if path.startswith("/posts/"):
            return {"status": "scheduled"}
        return {"items": []}

    api.get.side_effect = get
    api.post.side_effect = lambda path, body: {
        "postSubmissionId": "s-" + body["scheduledTime"],
        "scheduledTime": body["scheduledTime"],
    }
    return api


def sunday(year=2099, month=11, day=29):
    now = datetime(year, month, day, 12, tzinfo=CT)
    return now - timedelta(days=(now.weekday() + 1) % 7)


def fb_posts(api):
    return [c.args[1] for c in api.post.call_args_list if c.args[1]["post"]["target"]["targetType"] == "facebook"]


class Ticks:
    """Drive tick_brand like launchd does, with Hank approving the week's preview from his phone."""

    def __init__(self, test, store, cfg, api):
        self.test, self.store, self.cfg, self.api = test, store, cfg, api
        self.numbers, self.bodies, self.comments = {}, {}, {}
        self.issues = Mock()

        def ensure(title, body):
            if title not in self.numbers:
                self.numbers[title] = len(self.numbers) + 1
                self.bodies[self.numbers[title]] = body
            return self.numbers[title]

        self.issues.ensure.side_effect = ensure
        self.issues.read.side_effect = lambda n: (self.bodies[n], self.comments.get(n, []))
        self.issues.open.return_value = True

    def tick(self, now):
        with (
            patch("social.runtime.check"),
            patch("social.runtime.media_picker", return_value=lambda _: dict(PHOTO)),
            patch("social.runtime.recheck_repo", return_value="ok"),
            patch("social.runtime.refresh_repo", create=True),
        ):
            try:
                tick_brand("eden", self.cfg, self.store, now, self.api, self.issues, valid_writer())
            except RuntimeError:
                pass  # a held destination raises after the cycle; the cycle still ran

    def approve(self, now):
        """Hank's exact, unedited approval of the preview prepared this Sunday."""
        self.tick(now)
        week = (now.date() + timedelta(days=1)).isoformat()
        record = self.store.data["batches"]["eden/" + week]
        batch = record["batch"]
        self.test.assertEqual(self.bodies[record["issue"]], preview(batch))
        self.comments[record["issue"]] = [
            {"user": {"id": 123}, "body": "/approve " + identity(batch), "edited": False}
        ]
        self.tick(now)
        return batch


class HeldDestination(unittest.TestCase):
    """P0-1: a destination the preview marks held never receives a post."""

    def test_page_named_for_the_other_brand_receives_nothing_after_approval(self):
        cfg = valid_cfg()
        api = blotato({"page": "Acacia Roofing"})
        with (
            tempfile.TemporaryDirectory() as tmp,
            State(Path(tmp)) as store,
            patch("social.workflow.SUBMIT_AHEAD", timedelta(days=30), create=True),
        ):
            run = Ticks(self, store, cfg, api)
            now = sunday()
            batch = run.approve(now)
            self.assertIn("Facebook Page: not verified", preview(batch))
            for hours in range(1, 24 * 8, 6):  # every slot of the week comes due
                run.tick(now + timedelta(hours=hours))
            self.assertEqual(fb_posts(api), [])
            ig = [c for c in api.post.call_args_list if c.args[1]["post"]["target"]["targetType"] == "instagram"]
            self.assertEqual(len(ig), 7)

    def test_same_page_name_on_a_different_page_id_needs_a_fresh_approval(self):
        cfg = valid_cfg()
        api = blotato({"page": "Stay Eden", "page2": "Stay Eden"})
        with (
            tempfile.TemporaryDirectory() as tmp,
            State(Path(tmp)) as store,
            patch("social.workflow.SUBMIT_AHEAD", timedelta(days=30), create=True),
        ):
            run = Ticks(self, store, cfg, api)
            first = sunday()
            run.approve(first)
            run.approve(first + timedelta(days=7))
            self.assertEqual(len(store.data["approvals"]["eden"]), 2)
            sent = api.post.call_count
            cfg["brands"]["eden"]["autoPublish"] = True
            cfg["brands"]["eden"]["targets"]["facebook"]["pageId"] = "page2"
            third = first + timedelta(days=15, hours=-6)  # Monday 6 a.m. of the third week
            run.tick(third)
            run.tick(third + timedelta(hours=6))
            self.assertIn("eden/" + third.date().isoformat(), store.data["batches"])
            self.assertEqual(api.post.call_count, sent)
            titles = [c.args[0] for c in run.issues.ensure.call_args_list]
            self.assertTrue(any("destinations changed" in t for t in titles))

    def test_inbox_needs_the_approved_page_id_not_just_its_name(self):
        cfg = valid_cfg()
        cfg["brands"]["eden"]["targets"]["facebook"]["pageId"] = "page2"
        api = blotato({"page": "Stay Eden", "page2": "Stay Eden"})
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            store.data["approvedDestinations"] = {"eden": dests()}  # Hank approved Page "page"
            run = Ticks(self, store, cfg, api)
            run.tick(sunday())
            run.tick(sunday() + timedelta(minutes=15))
            reads = [c.kwargs.get("platform") for c in api.get.call_args_list if c.args[0] == "/messages"]
            self.assertIn("instagram", reads)
            self.assertNotIn("facebook", reads)

    def test_publish_never_sends_to_a_destination_missing_from_the_preview(self):
        held = dests(names={"instagram": "@stayeden_fredericksburgtx"})
        batch = plan("eden", date(2026, 11, 30), lambda _: dict(PHOTO), TARGETS, destinations=held)
        self.assertTrue(all("facebook" not in d["platforms"] for d in batch["days"]))
        batch["days"][0]["platforms"].append("facebook")  # even if a row lists it
        state, sent = {"approvals": {}, "writes": {}}, []
        # Hank fixes the Page connection after approving: Blotato now verifies it, the preview did not.
        comment = {"user": {"id": 123}, "body": "/approve " + identity(batch), "edited": False}
        publish(
            batch,
            state,
            lambda: None,
            datetime(2026, 11, 28, tzinfo=UTC),
            False,
            123,
            preview(batch),
            [comment],
            lambda body: sent.append(body) or {"postSubmissionId": "x"},
            current=dests(),
            recheck=lambda _: "ok",
            ahead=timedelta(days=30),
        )
        self.assertEqual({b["post"]["target"]["targetType"] for b in sent}, {"instagram"})
        self.assertFalse([k for k in state["writes"] if k.endswith("/facebook")])


def conversation(createdAt, cid="thread"):
    return {"id": cid, "accountId": "a", "platform": "instagram", "createdAt": createdAt.isoformat()}


class FreshConversation(unittest.TestCase):
    """P0-2: an automatic DM reply needs positive evidence the conversation is new or host-cleared."""

    def run_inbox(self, store, now, messages, started, issues):
        api, writer = Mock(), valid_writer()
        api.post.return_value = {"id": "sent"}

        def get(path, **params):
            if path == "/messages":
                if params.get("conversationId") is None:
                    return {"items": messages[:1]}  # the newest arrival in this cycle's feed
                return {"items": [m for m in messages if m["conversationId"] == params["conversationId"]]}
            if path.startswith("/conversations/"):
                return conversation(started)
            return {"items": []}

        api.get.side_effect = get
        process_inbox("eden", {"instagram": {"accountId": "a"}}, store, now, api, issues, writer, BUDGET)
        return api, writer

    def test_conversation_older_than_blotato_capture_goes_to_the_host(self):
        now = datetime(2026, 11, 30, 12, tzinfo=CT)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            start = (now - timedelta(minutes=15)).isoformat()
            store.data["inboxSince"].update({"eden": start, "eden/instagram": start})
            issues = Mock()
            issues.ensure.return_value = 4
            # Blotato imported nothing older; only the new question is retained.
            new = dm("new", "Are you available?", now)
            api, writer = self.run_inbox(store, now, [new], now - timedelta(days=60), issues)
            api.post.assert_not_called()
            writer.generate.assert_not_called()
            issues.ensure.assert_called_once()

    def test_conversation_started_after_activation_still_gets_a_reply(self):
        now = datetime(2026, 11, 30, 12, tzinfo=CT)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            start = (now - timedelta(minutes=15)).isoformat()
            store.data["inboxSince"].update({"eden": start, "eden/instagram": start})
            issues = Mock()
            api, _ = self.run_inbox(store, now, [dm("new", "Are you available?", now)], now, issues)
            api.post.assert_called_once()
            issues.ensure.assert_not_called()

    def test_reopened_handoff_takes_the_thread_back_to_the_host(self):
        now = datetime(2026, 11, 30, 12, tzinfo=CT)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            start = (now - timedelta(minutes=15)).isoformat()
            store.data["inboxSince"].update({"eden": start, "eden/instagram": start})
            issues, state = Mock(), {"open": True}
            issues.ensure.return_value = 4
            issues.open.side_effect = lambda n: state["open"]
            began = now - timedelta(minutes=1)
            refund = dm("1", "I need a host about my refund", now)
            self.run_inbox(store, now, [refund], began, issues)
            state["open"] = False  # Hank closes the handoff
            now += timedelta(minutes=15)
            follow = dm("2", "How do I book?", now)
            api, _ = self.run_inbox(store, now, [follow, refund], began, issues)
            api.post.assert_called_once()  # host-cleared: one simple follow-up gets its answer
            state["open"] = True  # the next day Hank reopens the same issue
            now += timedelta(days=1)
            again = dm("3", "Are you available?", now)
            handoffs = issues.ensure.call_count
            api, writer = self.run_inbox(store, now, [again, follow, refund], began, issues)
            api.post.assert_not_called()
            writer.generate.assert_not_called()
            # The reopened handoff itself owns the thread: no second handoff is opened for it.
            self.assertEqual(issues.ensure.call_count, handoffs)
            from social.runtime import event_ref

            self.assertEqual(store.data["events"][event_ref("eden", "messages", "3")]["issue"], 4)


class ZeroContactLimit(unittest.TestCase):
    """P0-3: contactChargeLimitCents 0 stops every automatic reply, returning contacts included."""

    def test_returning_commenter_gets_no_reply_after_the_limit_is_zero(self):
        now = datetime(2026, 11, 3, 12, tzinfo=CT)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            start = (now - timedelta(minutes=15)).isoformat()
            store.data["inboxSince"].update({"eden": start, "eden/instagram": start})
            api, issues, writer = Mock(), Mock(), valid_writer()
            issues.ensure.return_value = 4
            api.post.return_value = {"id": "sent"}
            comment = dict(
                platform="instagram",
                accountId="a",
                authorId="fixture-guest",
                postId="post",
                isAuthor=False,
                status="posted",
                parentCommentId=None,
                text="How do I book?",
            )
            items = [comment | dict(id="1", createdAt=now.isoformat())]
            api.get.side_effect = lambda path, **p: {"items": items if path == "/comments" else []}
            target = {"instagram": {"accountId": "a"}}
            process_inbox("eden", target, store, now, api, issues, writer, BUDGET)
            self.assertEqual(api.post.call_count, 1)
            now += timedelta(days=1)
            items = [comment | dict(id="2", createdAt=now.isoformat())]
            writer.generate.reset_mock()
            process_inbox("eden", target, store, now, api, issues, writer, BUDGET | {"contactChargeLimitCents": 0})
            self.assertEqual(api.post.call_count, 1)
            writer.generate.assert_not_called()


def week_batch(brand, start, targets, names, source):
    return plan(
        brand,
        start,
        lambda _: {"url": "https://example.org/safe.jpg", "source": source},
        targets,
        destinations=dests(targets, names),
    )


class QueuedAfterChange(unittest.TestCase):
    """P0-4: a slot is submitted only near its time, after the switch and photo are checked again."""

    def test_switch_off_on_tuesday_stops_fridays_automatic_post(self):
        start = date(2026, 12, 14)
        batch = week_batch("eden", start, TARGETS, None, SOURCE)
        state = {
            "approvals": {"eden": ["2026-11-30", "2026-12-07"]},
            "approvedDestinations": {"eden": dests()},
            "writes": {},
        }
        sent = []

        def tick(now, auto):
            publish(
                batch,
                state,
                lambda: None,
                now,
                auto,
                123,
                preview(batch),
                [],
                lambda body: sent.append(body) or {"postSubmissionId": "x", "scheduledTime": body["scheduledTime"]},
                clock=lambda: now,
                current=dests(),
                recheck=lambda _: "ok",
            )

        tick(datetime(2026, 12, 13, 12, tzinfo=CT), True)  # Sunday: the week is prepared
        tick(datetime(2026, 12, 15, 9, tzinfo=CT), False)  # Tuesday: the switch goes off
        tick(datetime(2026, 12, 18, 17, tzinfo=CT), False)  # Friday, 2 h 30 before 7:30 p.m.
        self.assertFalse([b for b in sent if b["scheduledTime"].startswith("2026-12-18")])
        control = []
        sent = control
        tick(datetime(2026, 12, 18, 17, tzinfo=CT), True)  # with the switch on it goes
        self.assertTrue(any(b["scheduledTime"].startswith("2026-12-18") for b in control))

    def test_photo_withdrawn_on_tuesday_holds_fridays_rows(self):
        targets = {
            "instagram": {"accountId": "ig"},
            "facebook": {"accountId": "fb", "pageId": "page"},
            "googlebusiness": {"accountId": "gb", "locationId": "accounts/1/locations/2"},
        }
        names = {"instagram": "@acaciaroofing", "facebook": "Acacia Roofing", "googlebusiness": "Acacia Roofing"}
        source = {"kind": "companycam", "id": "7", "version": "10/abc"}
        batch = week_batch("acacia", date(2026, 12, 14), targets, names, source)
        state = {"approvals": {}, "writes": {}}
        comment = {"user": {"id": 123}, "body": "/approve " + identity(batch), "edited": False}
        sent, photo = [], {"verdict": "ok"}

        def tick(now):
            publish(
                batch,
                state,
                lambda: None,
                now,
                False,
                123,
                preview(batch),
                [comment],
                lambda body: sent.append(body) or {"postSubmissionId": "x", "scheduledTime": body["scheduledTime"]},
                clock=lambda: now,
                current=dests(targets, names),
                recheck=lambda day: photo["verdict"],
            )

        tick(datetime(2026, 12, 13, 12, tzinfo=CT))
        photo["verdict"] = "held"  # the PM removes the tag on Tuesday
        tick(datetime(2026, 12, 15, 9, tzinfo=CT))
        tick(datetime(2026, 12, 18, 8, tzinfo=CT))  # Friday 10:00 a.m. slot is 2 h away
        self.assertFalse([b for b in sent if b["scheduledTime"].startswith("2026-12-18")])
        friday = {k: r["status"] for k, r in state["writes"].items() if "2026-12-18" in k}
        self.assertEqual(set(friday.values()), {"held"})
        self.assertEqual(len(friday), 3)


class DestinationCursor(unittest.TestCase):
    """P1-1: each destination keeps its own last successful read."""

    def test_facebook_message_from_its_outage_is_still_handled(self):
        t0 = datetime(2026, 11, 30, 12, tzinfo=CT)
        both = {"instagram": {"accountId": "a"}, "facebook": {"accountId": "fb", "pageId": "page"}}
        question = dm("fb-1", "Can we bring two dogs and check in at midnight?", t0 + timedelta(minutes=20))
        question |= dict(platform="facebook", conversationId="fbthread", recipientId="page")
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            start = (t0 - timedelta(minutes=15)).isoformat()
            store.data["inboxSince"].update({"eden": start, "eden/instagram": start, "eden/facebook": start})
            api, issues = Mock(), Mock()
            issues.ensure.return_value = 5
            feed = {"facebook": []}
            api.get.side_effect = lambda path, **p: {
                "items": feed.get(p.get("platform"), []) if path == "/messages" else []
            }
            process_inbox("eden", both, store, t0, api, issues, valid_writer(), BUDGET)
            for minutes in (15, 30):  # the Page lookup fails: Facebook is left out of these cycles
                process_inbox(
                    "eden", {"instagram": both["instagram"]}, store, t0 + timedelta(minutes=minutes), api, issues,
                    valid_writer(), BUDGET,
                )
            feed["facebook"] = [question]
            process_inbox("eden", both, store, t0 + timedelta(minutes=45), api, issues, valid_writer(), BUDGET)
            issues.ensure.assert_called_once()
            self.assertEqual(len(store.data["events"]), 1)


class PartialDigest(unittest.TestCase):
    """P1-2: a Page lookup failure on Monday does not drop that Page from the week's digest."""

    def test_facebook_reach_is_counted_from_the_weeks_confirmed_destinations(self):
        now = datetime(2099, 12, 7, 9, tzinfo=CT)
        previous = date(2099, 11, 30)
        row = dict(
            id="fb-post",
            platform="facebook",
            account={"id": "fb", "subaccount": {"id": "page"}},
            postTime=datetime(2099, 12, 2, 12, tzinfo=CT).isoformat(),
            state={"type": "published", "analytics": {"latest": {"metrics": {"reachCount": "123"}}}},
        )
        api = blotato({"page": "Stay Eden"}, posts=[row], subaccounts_fail=True)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            batch = week_batch("eden", previous, TARGETS, None, SOURCE)
            store.data["batches"]["eden/" + previous.isoformat()] = {"batch": batch, "issue": 1}
            store.data["approvedDestinations"] = {"eden": dests()}
            run = Ticks(self, store, valid_cfg(), api)
            run.tick(now)
            digests = [c.args[1] for c in run.issues.ensure.call_args_list if "digest" in c.args[0]]
            self.assertEqual(len(digests), 1)
            self.assertIn("123", digests[0])
            self.assertIn("(1/1 IG/FB posts measured)", digests[0])


class UpstreamPhoto(unittest.TestCase):
    """P1-3: the Stay Eden photo is checked against current upstream main, not a stale local ref."""

    def setUp(self):
        out = BytesIO()
        Image.new("RGB", (640, 480)).save(out, "WEBP")
        self.webp = out.getvalue()
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def git(self, repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            check=True,
            capture_output=True,
        )

    def repos(self):
        upstream = self.tmp / "upstream"
        (upstream / "public/images").mkdir(parents=True)
        (upstream / "public/images/cherry-2.webp").write_bytes(self.webp)
        self.git(upstream, "init", "-q", "-b", "main")
        self.git(upstream, "add", ".")
        self.git(upstream, "commit", "-qm", "photos")
        clone = self.tmp / "mini"
        subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True, capture_output=True)
        return upstream, clone

    def test_photo_removed_upstream_is_held(self):
        from social.runtime import source_checker
        from social.sources import blob_sha

        upstream, clone = self.repos()
        source = {"kind": "repo", "path": "public/images/cherry-2.webp", "blob": blob_sha(self.webp)}
        cfg = {"edenRepo": str(clone)}
        self.assertEqual(source_checker("eden", cfg)({"source": source}), "ok")
        self.git(upstream, "rm", "-q", "public/images/cherry-2.webp")
        self.git(upstream, "commit", "-qm", "owner removes the photo")
        self.assertEqual(source_checker("eden", cfg)({"source": source}), "held")

    def test_unrefreshable_ref_does_not_clear_the_photo(self):
        from social.runtime import source_checker
        from social.sources import blob_sha

        upstream, clone = self.repos()
        source = {"kind": "repo", "path": "public/images/cherry-2.webp", "blob": blob_sha(self.webp)}
        self.git(clone, "remote", "set-url", "origin", str(self.tmp / "gone"))
        check = source_checker("eden", {"edenRepo": str(clone)})
        self.assertNotEqual(check({"source": source}), "ok")
        self.assertTrue(check.failed)


class OverdueReplyAlert(unittest.TestCase):
    """P1-4: an unresolved reply whose issue could not be opened is alerted on a later tick."""

    def test_issue_is_retried_after_github_recovers(self):
        sent = datetime(2026, 12, 1, 12, tzinfo=CT)
        row = dict(brand="eden", kind="messages", platform="instagram", time=sent.isoformat(), status="queued", id="m")
        state = {"writes": {}, "events": {"r": row}}
        store, api, issues = Mock(data=state), Mock(), Mock()
        api.get.return_value = {"status": "pending"}
        issues.ensure.side_effect = RuntimeError("GitHub operation failed")
        with self.assertRaises(RuntimeError):
            audit_delivery("eden", store, sent + timedelta(days=2), api, issues)
        self.assertEqual(row["status"], "unresolved")
        issues.ensure.side_effect = None
        issues.ensure.return_value = 8
        audit_delivery("eden", store, sent + timedelta(days=2, minutes=15), api, issues)
        issues.ensure.assert_called()
        self.assertEqual(row["issue"], 8)


class MonthEndBilling(unittest.TestCase):
    """P1-5: billing periods anchored on the 29th, 30th or 31st are accepted and roll over correctly."""

    def test_month_end_days_validate(self):
        for day in (29, 30, 31):
            cfg = valid_cfg()
            cfg["blotato"]["billingPeriodDay"] = day
            validate_config(cfg)

    def test_period_start_follows_the_month_end_anchor(self):
        from social.runtime import period_start

        cases = [
            (31, date(2027, 2, 28), date(2027, 2, 28)),
            (31, date(2027, 3, 15), date(2027, 2, 28)),
            (31, date(2027, 3, 31), date(2027, 3, 31)),
            (31, date(2027, 4, 30), date(2027, 4, 30)),
            (30, date(2028, 2, 29), date(2028, 2, 29)),
            (30, date(2028, 3, 29), date(2028, 2, 29)),
            (29, date(2027, 1, 28), date(2026, 12, 29)),
            (1, date(2027, 1, 1), date(2027, 1, 1)),
        ]
        for day, today, expected in cases:
            now = datetime.combine(today, datetime.min.time(), UTC) + timedelta(hours=12)
            self.assertEqual(period_start(now, day), expected, (day, today))


class BudgetValidation(unittest.TestCase):
    """P1-6: the negative-budget test starts from a valid config and changes only the budget."""

    def test_negative_contact_limit_alone_is_rejected(self):
        cfg = valid_cfg()
        validate_config(cfg)
        cfg["blotato"]["contactChargeLimitCents"] = -1
        with self.assertRaisesRegex(ValueError, "Blotato contact budget"):
            validate_config(cfg)


class ActivationRunbook(unittest.TestCase):
    """P1-7: the install step states when the inbox actually starts."""

    def test_install_section_names_the_inbox_coverage_gap(self):
        text = (Path(__file__).parents[1] / "README.md").read_text()
        section = text.split("### Enable the schedule", 1)[1].split("To pause the Mini", 1)[0]
        self.assertNotIn("can receive replies from that point", section)
        self.assertIn("first approved preview", section)
        self.assertIn("only records its start time", section)
        self.assertIn("by hand", section)
