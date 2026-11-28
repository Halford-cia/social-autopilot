"""Regression tests for review 449r (PR #1). Every provider is mocked."""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from social.api import RemoteError
from social.cli import load_example
from social.digest import digest
from social.inbox import BOOKING
from social.issues import Issues
from social.media import FLAGS, media_picker, recheck_company, sanitize
from social.planner import CT, identity, plan, preview
from social.runtime import State, audit_delivery, check_destinations, process_inbox, tick_brand
from social.workflow import publish

FIXTURES = Path(__file__).parent / "fixtures"
CLEAR = dict.fromkeys(FLAGS, False)
BUDGET = {"contactChargeLimitCents": 100, "contactChargeCents": 2, "billingPeriodDay": 1}
TARGETS = {"instagram": {"accountId": "ig"}, "facebook": {"accountId": "fb", "pageId": "page"}}
NAMES = {"instagram": "@stayeden_fredericksburgtx", "facebook": "Stay Eden"}


def valid_writer():
    """Captions from the offline demo writer; a reply that passes reply validation."""
    from social.claude import HAIKU
    from social.cli import DemoWriter

    writer = Mock()
    writer.generate.side_effect = lambda model, *a: (
        "Happy to help! " + BOOKING if model == HAIKU else DemoWriter().generate(model, *a)
    )
    return writer


def jpeg(size=(800, 600)):
    out = BytesIO()
    Image.new("RGB", size).save(out, "JPEG")
    return out.getvalue()


class FramedPortrait(unittest.TestCase):
    """P0-1: a clear from the language model is not enough. The exported pixels get a face check."""

    def test_final_exported_bytes_go_through_the_face_check(self):
        seen = []

        def faces(data):
            seen.append(data)
            return True

        out = sanitize(jpeg(), "eden", lambda *_: CLEAR, faces=faces)
        self.assertEqual(seen, [out])
        self.assertIsNone(sanitize(jpeg(), "eden", lambda *_: CLEAR, faces=lambda _: False))

    def test_face_check_fails_closed_without_the_detector(self):
        from social import media

        with patch.dict(sys.modules, {"Vision": None}):
            self.assertFalse(media.face_free(jpeg()))

    @unittest.skipUnless(sys.platform == "darwin", "Apple Vision runs on the Mini")
    def test_framed_portrait_on_the_wall_is_held_even_when_the_model_clears_it(self):
        framed = (FIXTURES / "framed-portrait-room.webp").read_bytes()
        empty = (FIXTURES / "empty-room.webp").read_bytes()
        # The pinned model returned "clear" for the framed portrait in the review.
        self.assertIsNone(sanitize(framed, "eden", lambda *_: CLEAR))
        self.assertIsNotNone(sanitize(empty, "eden", lambda *_: CLEAR))


class EditedApproval(unittest.TestCase):
    """P0-2: only a fresh, unedited comment by Hank approves."""

    def setUp(self):
        self.batch = plan("eden", date(2026, 11, 30), lambda _: None, TARGETS)
        self.state = {"approvals": {}, "writes": {}}
        self.sent = []

    def run_with(self, comment):
        publish(
            self.batch,
            self.state,
            lambda: None,
            datetime(2026, 11, 28, tzinfo=UTC),
            False,
            123,
            preview(self.batch),
            [comment],
            lambda body: self.sent.append(body) or {"postSubmissionId": "x"},
        )

    def test_edited_or_unknown_edit_state_never_approves(self):
        cmd = "/approve " + identity(self.batch)
        for comment in [
            {"user": {"id": 123}, "body": cmd, "edited": True},
            {"user": {"id": 123}, "body": cmd},
        ]:
            self.run_with(comment)
        self.assertEqual(self.sent, [])
        self.assertNotIn("eden", self.state["approvals"])
        self.run_with({"user": {"id": 123}, "body": cmd, "edited": False})
        self.assertEqual(len(self.sent), 7)

    def test_issue_reader_marks_any_edit_metadata_as_edited(self):
        issues = Issues.__new__(Issues)
        issues.base, issues.actor = "repos/owner/ops", 9

        def node(author, **edit):
            return dict(
                body="/approve x",
                createdAt="2026-11-28T12:00:00Z",
                author={"databaseId": author},
                lastEditedAt=edit.get("lastEditedAt"),
                includesCreatedEdit=edit.get("includesCreatedEdit", False),
                editor=edit.get("editor"),
            )

        comments = [
            node(123),
            node(123, lastEditedAt="2026-11-28T13:00:00Z", editor={"login": "moderator"}),
            node(123, includesCreatedEdit=True),
        ]
        response = {
            "data": {
                "repository": {
                    "issue": {
                        "body": "preview",
                        "state": "OPEN",
                        "author": {"databaseId": 9},
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": comments,
                        },
                    }
                }
            }
        }
        with patch.object(Issues, "api", return_value=response) as api:
            body, read = issues.read(5)
        self.assertEqual(api.call_args.args[0], "graphql")
        self.assertEqual(body, "preview")
        self.assertEqual([c["edited"] for c in read], [False, True, True])
        self.assertEqual(read[0]["user"]["id"], 123)


def accounts_api(ig_name="stayeden_fredericksburgtx", page_name="Stay Eden", pages_=("page",)):
    api = Mock()

    def get(path, **params):
        if path == "/users/me/accounts":
            return {
                "items": [
                    dict(id="ig", platform="instagram", username=ig_name, fullname="x"),
                    dict(id="fb", platform="facebook", username="", fullname="Login"),
                ]
            }
        if path.endswith("/subaccounts"):
            return {"items": [dict(id=p, accountId="fb", name=page_name) for p in pages_]}
        return {"items": []}

    api.get.side_effect = get
    return api


def eden_cfg():
    cfg = load_example()
    cfg["approverId"] = 123
    cfg["brands"]["eden"]["targets"] = json.loads(json.dumps(TARGETS))
    return cfg


class DestinationIdentity(unittest.TestCase):
    """P0-3: resolved names are bound into the approval, and the inbox waits for that approval."""

    def test_destination_named_for_the_other_brand_is_refused(self):
        resolved, problems = check_destinations("eden", eden_cfg(), accounts_api(ig_name="acaciaroofing"))
        self.assertNotIn("instagram", resolved)
        self.assertEqual([p for p, _ in problems], ["instagram"])
        resolved, problems = check_destinations("eden", eden_cfg(), accounts_api())
        self.assertEqual((resolved, problems), (NAMES, []))

    def test_preview_and_token_show_the_resolved_destinations(self):
        batch = plan("eden", date(2026, 11, 30), lambda _: None, TARGETS, destinations=NAMES)
        text = preview(batch)
        self.assertIn("@stayeden_fredericksburgtx", text)
        self.assertIn("Stay Eden", text)
        swapped = plan(
            "eden",
            date(2026, 11, 30),
            lambda _: None,
            TARGETS,
            destinations=NAMES | {"facebook": "Acacia Roofing"},
        )
        self.assertNotEqual(identity(batch), identity(swapped))

    def test_inbox_waits_for_an_approved_week_with_the_same_destinations(self):
        now = datetime(2099, 12, 1, 12, tzinfo=CT)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            api, issues = accounts_api(), Mock()
            issues.ensure.return_value = 1
            issues.read.return_value = ("", [])
            with (
                patch("social.runtime.check"),
                patch("social.runtime.media_picker", return_value=lambda _: None),
            ):
                tick_brand("eden", eden_cfg(), store, now, api, issues, valid_writer())
                inbox_reads = [c for c in api.get.call_args_list if c.args[0] in ("/messages", "/comments")]
                self.assertEqual(inbox_reads, [])
                self.assertNotIn("eden", store.data["inboxSince"])
                store.data["approvedDestinations"] = {"eden": dict(NAMES)}
                tick_brand("eden", eden_cfg(), store, now, api, issues, valid_writer())
                self.assertIn("eden", store.data["inboxSince"])
                # A destination renamed or swapped since the approval pauses the inbox again.
                store.data["approvedDestinations"] = {"eden": NAMES | {"facebook": "Other Page"}}
                api.get.reset_mock()
                with self.assertRaises(RuntimeError):
                    tick_brand("eden", eden_cfg(), store, now, api, issues, valid_writer())
                reads = [c.kwargs.get("platform") for c in api.get.call_args_list if c.args[0] == "/messages"]
                self.assertNotIn("facebook", reads)

    def test_rows_are_held_when_the_destination_differs_from_the_approved_preview(self):
        batch = plan("eden", date(2026, 11, 30), lambda _: None, TARGETS, destinations=NAMES)
        state, sent = {"approvals": {}, "writes": {}}, []
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
            current=NAMES | {"facebook": "Acacia Roofing"},
        )
        self.assertEqual(sent, [])
        self.assertTrue(all(row["status"] == "held" for row in state["writes"].values()))

    def test_automatic_week_needs_destinations_hank_approved(self):
        state = {"approvals": {"eden": ["2026-11-16", "2026-11-23"]}, "writes": {}}
        state["approvedDestinations"] = {"eden": NAMES | {"facebook": "Other Page"}}
        batch = plan("eden", date(2026, 11, 30), lambda _: None, TARGETS, destinations=NAMES)
        sent = []
        publish(
            batch,
            state,
            lambda: None,
            datetime(2026, 11, 28, tzinfo=UTC),
            True,
            123,
            preview(batch),
            [],
            lambda body: sent.append(body) or {"postSubmissionId": "x"},
            current=NAMES,
        )
        self.assertEqual(sent, [])


def dm(mid, text, stamp, conversation="thread", sender="guest"):
    return dict(
        id=mid,
        conversationId=conversation,
        senderId=sender,
        platform="instagram",
        direction="incoming",
        status="delivered",
        text=text,
        createdAt=stamp.isoformat(),
    )


class ConversationContext(unittest.TestCase):
    """P0-4: a follow-up in a conversation that began before activation goes to the host."""

    def run_inbox(self, history):
        now = datetime(2026, 11, 30, 12, tzinfo=CT)
        new = dm("new", "Are you available?", now)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            store.data["inboxSince"]["eden"] = (now - timedelta(minutes=15)).isoformat()
            api, issues, writer = Mock(), Mock(), valid_writer()
            issues.ensure.return_value = 4
            api.post.return_value = {"id": "sent"}

            def get(path, **params):
                if path != "/messages":
                    return {"items": []}
                if params.get("conversationId") == "thread":
                    return {"items": [new, *history(now)]}
                return {"items": [new]}

            api.get.side_effect = get
            process_inbox("eden", {"instagram": {"accountId": "a"}}, store, now, api, issues, writer, BUDGET)
            return api, issues, writer

    def test_refund_request_before_activation_keeps_the_thread_with_the_host(self):
        api, issues, writer = self.run_inbox(
            lambda now: [dm("old", "I need a host about my refund", now - timedelta(hours=1))]
        )
        api.post.assert_not_called()
        writer.generate.assert_not_called()
        issues.ensure.assert_called_once()

    def test_first_message_of_a_new_conversation_still_gets_a_reply(self):
        api, issues, _ = self.run_inbox(lambda now: [])
        api.post.assert_called_once()
        issues.ensure.assert_not_called()


class ContactBudget(unittest.TestCase):
    """P0-6: each new Blotato active contact reserves its worst-case charge before the send."""

    def test_reply_budget_counts_distinct_contacts_and_holds_the_rest(self):
        now = datetime(2026, 11, 30, 12, tzinfo=CT)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            store.data["inboxSince"]["eden"] = (now - timedelta(minutes=15)).isoformat()
            api, issues, writer = Mock(), Mock(), valid_writer()
            issues.ensure.return_value = 4
            api.post.return_value = {"id": "sent"}
            batch = [dm("1", "How do I book?", now, "c1", "guest-1")]
            api.get.side_effect = lambda path, **p: {
                "items": [m for m in batch if p.get("conversationId") in (None, m["conversationId"])]
                if path == "/messages"
                else []
            }
            limits = BUDGET | {"contactChargeLimitCents": 2}
            process_inbox("eden", {"instagram": {"accountId": "a"}}, store, now, api, issues, writer, limits)
            self.assertEqual(api.post.call_count, 1)
            period = store.data["blotatoContacts"]["2026-11-01"]
            self.assertEqual(period["reservedCents"], 2)
            self.assertNotIn("guest-1", json.dumps(store.data))
            now += timedelta(minutes=15)
            batch = [dm("2", "How do I book?", now, "c2", "guest-2")]
            process_inbox("eden", {"instagram": {"accountId": "a"}}, store, now, api, issues, writer, limits)
            self.assertEqual(api.post.call_count, 1)
            self.assertTrue(any("budget" in c.args[0] for c in issues.ensure.call_args_list))

    def test_uncertain_send_keeps_its_reservation(self):
        now = datetime(2026, 11, 30, 12, tzinfo=CT)
        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            store.data["inboxSince"]["eden"] = (now - timedelta(minutes=15)).isoformat()
            api, issues = Mock(), Mock()
            message = dm("1", "How do I book?", now, "c1", "guest-1")
            api.get.side_effect = lambda path, **p: {"items": [message] if path == "/messages" else []}
            api.post.side_effect = RemoteError()
            with self.assertRaises(RemoteError):
                process_inbox(
                    "eden", {"instagram": {"accountId": "a"}}, store, now, api, issues, valid_writer(), BUDGET
                )
            saved = json.loads(store.file.read_text())
            self.assertEqual(saved["blotatoContacts"]["2026-11-01"]["reservedCents"], 2)

    def test_example_config_sets_an_explicit_contact_budget(self):
        from social.runtime import validate_config

        cfg = load_example()
        self.assertEqual(set(cfg["blotato"]), set(BUDGET))
        cfg["blotato"]["contactChargeLimitCents"] = -1
        with self.assertRaises(ValueError):
            validate_config(cfg)


class WithdrawnPhoto(unittest.TestCase):
    """P0-5: the source photo is rechecked before each pending submission."""

    def photo(self, **changes):
        return (
            dict(
                id="7",
                status="active",
                processing_status="processed",
                internal=False,
                hash="abc",
                updated_at=10,
                uris=[dict(type="original", uri="https://static.companycam.com/7.jpg")],
            )
            | changes
        )

    def fetcher(self, photo, tags):
        def fetch(method, url, headers):
            if "/photos/7/tags" in url:
                return tags
            if "/photos/7" in url:
                return photo
            raise AssertionError(url)

        return fetch

    def test_recheck_holds_untagged_internal_or_changed_photos(self):
        source = {"kind": "companycam", "id": "7", "version": "10/abc"}
        both = [{"value": "after"}, {"value": "drone"}]
        self.assertEqual(recheck_company(source, "k", self.fetcher(self.photo(), both)), "ok")
        for photo, tags in [
            (self.photo(), [{"value": "after"}]),
            (self.photo(internal=True), both),
            (self.photo(hash="new"), both),
        ]:
            self.assertEqual(recheck_company(source, "k", self.fetcher(photo, tags)), "held")

        def missing(*_):
            raise RemoteError(404)

        def down(*_):
            raise RemoteError(503)

        self.assertEqual(recheck_company(source, "k", missing), "held")
        self.assertEqual(recheck_company(source, "k", down), "retry")

    def test_held_photo_rows_are_not_submitted_and_raise_one_alert(self):
        url = "https://example.org/safe.jpg"
        source = {"kind": "companycam", "id": "7", "version": "10/abc"}
        batch = plan("eden", date(2026, 11, 30), lambda _: {"url": url, "source": source}, TARGETS)
        self.assertEqual(batch["days"][0]["source"], source)
        self.assertNotIn('"7"', preview(batch))
        state, sent = {"approvals": {}, "writes": {}, "events": {}}, []
        comment = {"user": {"id": 123}, "body": "/approve " + identity(batch), "edited": False}
        verdicts = iter(["held"] + ["ok"] * 6)
        publish(
            batch,
            state,
            lambda: None,
            datetime(2026, 11, 28, tzinfo=UTC),
            False,
            123,
            preview(batch),
            [comment],
            lambda body: (
                sent.append(body) or {"postSubmissionId": "x", "scheduledTime": body["scheduledTime"]}
            ),
            recheck=lambda day: next(verdicts),
        )
        self.assertEqual(len(sent), 12)
        held = [k for k, row in state["writes"].items() if row["status"] == "held"]
        self.assertEqual(sorted(held), ["eden/2026-11-30/facebook", "eden/2026-11-30/instagram"])
        store, issues, api = Mock(data=state), Mock(), Mock()
        issues.ensure.return_value = 9
        api.get.return_value = {"status": "scheduled"}
        audit_delivery("eden", store, datetime(2026, 11, 28, tzinfo=UTC), api, issues)
        audit_delivery("eden", store, datetime(2026, 11, 28, tzinfo=UTC), api, issues)
        self.assertEqual(issues.ensure.call_count, 2)
        self.assertTrue(all("withdrawn or changed" in c.args[1] for c in issues.ensure.call_args_list))


def git_repo(tmp, files):
    repo = Path(tmp) / "stays"
    (repo / "public/images").mkdir(parents=True)
    for name, data in files.items():
        (repo / "public/images" / name).write_bytes(data)
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    run("init", "-q")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "fixture")
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


class MissingRepoPhoto(unittest.TestCase):
    """P1-2: a missing candidate image is skipped; repository failures stay visible."""

    def setUp(self):
        out = BytesIO()
        Image.new("RGB", (640, 480)).save(out, "WEBP")
        self.webp = out.getvalue()

    def test_missing_first_candidate_tries_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = git_repo(tmp, {"cherry-3.webp": self.webp})
            api = Mock()
            api.upload.return_value = "https://example.org/u.jpg"
            with patch("social.media.sanitize", return_value=b"clean") as clean:
                picked = media_picker("eden", {"edenRepo": str(repo)}, api)("cherry")
            self.assertEqual(picked["url"], "https://example.org/u.jpg")
            self.assertEqual(picked["source"]["path"], "public/images/cherry-3.webp")
            self.assertEqual(clean.call_count, 1)
            with patch("social.media.sanitize", return_value=b"clean"):
                self.assertIsNone(media_picker("eden", {"edenRepo": str(repo)}, api)("oak"))

    def test_broken_repository_still_raises(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(subprocess.SubprocessError):
            media_picker("eden", {"edenRepo": tmp}, Mock())("cherry")


class BoundedPolling(unittest.TestCase):
    """P1-3: a submission that never resolves is marked and alerted once."""

    def test_in_progress_a_month_after_the_slot_becomes_unresolved(self):
        due = datetime(2026, 12, 1, 12, tzinfo=CT)
        state = {
            "writes": {
                "eden/2026-12-01/facebook": {"status": "scheduled", "due": due.isoformat(), "id": "s"}
            },
            "events": {},
        }
        store, api, issues = Mock(data=state), Mock(), Mock()
        api.get.return_value = {"status": "in-progress"}
        issues.ensure.return_value = 3
        for days in (1, 10, 30):
            audit_delivery("eden", store, due + timedelta(days=days), api, issues)
        self.assertEqual(state["writes"]["eden/2026-12-01/facebook"]["status"], "unresolved")
        self.assertEqual(issues.ensure.call_count, 1)

    def test_queued_reply_that_never_resolves_is_alerted(self):
        sent = datetime(2026, 12, 1, 12, tzinfo=CT)
        state = {
            "writes": {},
            "events": {
                "r": dict(
                    brand="eden",
                    kind="messages",
                    platform="instagram",
                    time=sent.isoformat(),
                    status="queued",
                    id="m",
                )
            },
        }
        store, api, issues = Mock(data=state), Mock(), Mock()
        api.get.return_value = {"status": "pending"}
        issues.ensure.return_value = 3
        for days in (2, 3):
            audit_delivery("eden", store, sent + timedelta(days=days), api, issues)
        self.assertEqual(state["events"]["r"]["status"], "unresolved")
        self.assertEqual(issues.ensure.call_count, 1)


class PersistentPause(unittest.TestCase):
    """P1-4: the documented pause survives a login."""

    def test_runbook_pause_disables_and_resume_enables(self):
        text = (Path(__file__).parents[1] / "README.md").read_text()
        self.assertIn('launchctl disable "gui/$(id -u)/com.acacia.social-autopilot"', text)
        self.assertIn('launchctl enable "gui/$(id -u)/com.acacia.social-autopilot"', text)
        pause, resume = text.split("To pause the Mini", 1)[1].split("To resume", 1)
        self.assertLess(pause.index("launchctl disable"), pause.index("launchctl bootout"))
        resume = resume.split("```", 2)[1]
        self.assertLess(resume.index("launchctl enable"), resume.index("launchctl bootstrap"))


class DisconnectedDestination(unittest.TestCase):
    """P1-5: one disconnected destination alerts by name and leaves the others running."""

    def test_gbp_disconnect_alerts_and_keeps_inbox_and_delivery(self):
        cfg = load_example()
        cfg["approverId"] = 123
        cfg["brands"]["acacia"]["targets"] = {
            "instagram": {"accountId": "ig"},
            "facebook": {"accountId": "fb", "pageId": "page"},
            "googlebusiness": {"accountId": "gb", "locationId": "accounts/1/locations/2"},
        }
        now = datetime(2099, 12, 1, 12, tzinfo=CT)

        def get(path, **params):
            if path == "/users/me/accounts":
                return {
                    "items": [
                        dict(id="ig", platform="instagram", username="acaciaroofing"),
                        dict(id="fb", platform="facebook", username=""),
                    ]
                }
            if path.endswith("/subaccounts"):
                return {"items": [dict(id="page", accountId="fb", name="Acacia Roofing")]}
            return {"items": []}

        with tempfile.TemporaryDirectory() as tmp, State(Path(tmp)) as store:
            api, issues = Mock(), Mock()
            api.get.side_effect = get
            issues.ensure.return_value = 1
            issues.read.return_value = ("", [])
            store.data["approvedDestinations"] = {
                "acacia": {"instagram": "@acaciaroofing", "facebook": "Acacia Roofing"}
            }
            store.data["writes"]["acacia/2099-11-20/facebook"] = {
                "status": "uncertain",
                "due": "2099-11-20T10:00:00-06:00",
            }
            with (
                patch("social.runtime.check"),
                patch("social.runtime.media_picker", return_value=lambda _: None),
                patch("social.runtime.secret", return_value="k"),
                self.assertRaises(RuntimeError),
            ):
                tick_brand("acacia", cfg, store, now, api, issues, valid_writer())
            titles = [c.args[0] for c in issues.ensure.call_args_list]
            self.assertTrue(any("Google Business" in t for t in titles))
            self.assertIn("acacia", store.data["inboxSince"])
            self.assertTrue(any("check delivery" in t for t in titles))


class DigestDates(unittest.TestCase):
    """P2: top-post dates are Central Time."""

    def test_evening_post_keeps_its_central_date(self):
        row = dict(
            id="one",
            platform="instagram",
            account={"id": "ig"},
            postTime="2026-12-07T01:30:00Z",
            state={"type": "published", "analytics": {"latest": {"metrics": {"reachCount": "5"}}}},
        )
        report = digest("eden", "2026-11-30", {"instagram": {"accountId": "ig"}}, [row])
        self.assertIn("- 2026-12-06 ·", report)
