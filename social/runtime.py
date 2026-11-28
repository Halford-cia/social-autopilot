import calendar
import fcntl
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from .api import RemoteError, pages, secret
from .claude import RunLimit, validate_limits
from .digest import bounds, digest
from .inbox import route
from .media import media_picker, recheck_company, recheck_repo
from .planner import BRANDS, CT, LABELS, caption, plan, preview, verify_copy
from .sources import check, refresh_repo
from .workflow import publish
from .writing import reply_text, write_week


class State:
    """Run one tick at a time and fsync each write intent before contacting the API."""

    def __init__(self, root):
        self.root = root
        self.file = root / "state.json"

    def __enter__(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = (self.root / "lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.data = (
                json.loads(self.file.read_text())
                if self.file.exists()
                else {
                    "batches": {},
                    "approvals": {},
                    "writes": {},
                    "inboxSince": {},
                    "events": {},
                    "digests": {},
                }
            )
        except BaseException:
            self.lock.close()
            raise
        return self

    def save(self):
        tmp = self.root / "state.tmp"
        with tmp.open("w") as out:
            json.dump(self.data, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, self.file)
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def __exit__(self, *_):
        self.lock.close()


def validate_contact_limits(limits):
    if not isinstance(limits, dict) or set(limits) != {
        "contactChargeLimitCents",
        "contactChargeCents",
        "billingPeriodDay",
    }:
        raise ValueError("Configure the Blotato contact budget")
    if any(type(v) is not int for v in limits.values()) or limits["contactChargeLimitCents"] < 0:
        raise ValueError("Configure the Blotato contact budget")
    if limits["contactChargeCents"] < 1 or not 1 <= limits["billingPeriodDay"] <= 31:
        raise ValueError("Configure the Blotato contact budget")


def validate_config(cfg):
    validate_limits(cfg["claude"])
    validate_contact_limits(cfg.get("blotato"))
    owners = cfg["approverId"]
    if type(owners) is not int or owners < 1:
        raise ValueError("Set the GitHub numeric ID of the approver")
    if set(cfg["brands"]) != set(BRANDS):
        raise ValueError("Configure both brands")
    used = set()
    for brand, block in cfg["brands"].items():
        targets = block["targets"]
        required = {"instagram", "facebook"} | ({"googlebusiness"} if brand == "acacia" else set())
        if set(targets) != required or type(block["autoPublish"]) is not bool:
            raise ValueError("Check brand channels and autoPublish")
        for platform, dest in targets.items():
            fields = {"accountId"} | {"facebook": {"pageId"}, "googlebusiness": {"locationId"}}.get(
                platform, set()
            )
            if set(dest) != fields or not all(
                isinstance(v, str) and v.strip() and not v.startswith("<") for v in dest.values()
            ):
                raise ValueError("Set the account and destination IDs")
            identity = (platform, dest.get("pageId") or dest.get("locationId") or dest["accountId"])
            if identity in used:
                raise ValueError("A social destination belongs to only one brand")
            used.add(identity)


BRAND_WORDS = {"acacia": "acacia", "eden": "eden"}


def check_destinations(brand, cfg, api):
    """Resolve each configured destination to the name Blotato reports for it.

    Returns ({platform: {**configured IDs, "name": name}}, [(platform, reason)]). The IDs travel with
    the name so an approval binds the exact account and Page or location, not just a display name.
    A destination that is missing, on the wrong platform, unnamed, or named for the other brand is
    left out (held), and the rest keep working. A failure to list accounts at all raises.
    """
    targets = cfg["brands"][brand]["targets"]
    accounts = {item["id"]: item for item in api.get("/users/me/accounts")["items"]}
    other = next(word for b, word in BRAND_WORDS.items() if b != brand)
    resolved, problems = {}, []
    for platform, target in targets.items():
        aid = target["accountId"]
        account = accounts.get(aid) or {}
        if account.get("platform") != platform:
            problems.append((platform, "not connected on this platform"))
            continue
        if platform == "instagram":
            handle = account.get("username")
            name = "@" + handle.strip() if isinstance(handle, str) and handle.strip() else None
        else:
            try:
                subs = api.get("/users/me/accounts/" + quote(aid, safe="") + "/subaccounts")["items"]
            except (RemoteError, KeyError, TypeError):
                problems.append((platform, "destinations could not be read"))
                continue
            want = target.get("pageId") or target.get("locationId")
            match = [s for s in subs if s.get("accountId") == aid and s.get("id") == want]
            name = match[0].get("name") if len(match) == 1 else None
            if not match:
                problems.append((platform, "not connected"))
                continue
            name = name.strip() if isinstance(name, str) and name.strip() else None
        if not name:
            problems.append((platform, "no name reported"))
        elif other in name.casefold():
            problems.append((platform, "named for the other brand"))
        else:
            resolved[platform] = dict(target, name=name)
    return resolved, problems


def event_ref(brand, kind, eid):
    return hashlib.sha256(f"{brand}/{kind}/{eid}".encode()).hexdigest()[:20]


def notice(issues, title, message):
    return issues.ensure("Social · " + title, message)


def period_start(now, day):
    """Start of the Blotato billing period that contains now (UTC date).

    The period is anchored on `day` (1 to 31). In a shorter month it starts on that month's last
    day, as month-end subscription anchors do (an anchor of 31 starts Feb 28, then Mar 31).
    """
    today = now.astimezone(UTC).date()

    def anchor(month_start):
        last = calendar.monthrange(month_start.year, month_start.month)[1]
        return month_start.replace(day=min(day, last))

    this = anchor(today.replace(day=1))
    if today >= this:
        return this
    return anchor((today.replace(day=1) - timedelta(days=1)).replace(day=1))


class Contacts:
    """Worst-case Blotato active-contact charges, reserved before each send and never released.

    Blotato counts each person reached per connected account once per billing period and bills
    extra contacts on non-legacy plans. The allowance is ignored here: every new contact is assumed
    chargeable, so the period never exceeds the configured limit.
    """

    def __init__(self, limits, store, now):
        limits = limits or {"contactChargeLimitCents": 0, "contactChargeCents": 1, "billingPeriodDay": 1}
        self.limit = limits["contactChargeLimitCents"]
        self.charge = limits["contactChargeCents"]
        self.period = period_start(now, limits["billingPeriodDay"]).isoformat()
        periods = store.data.setdefault("blotatoContacts", {})
        self.ledger = periods.setdefault(self.period, {"contacts": [], "reservedCents": 0})

    def allows(self, contact):
        # A zero limit is the documented off switch: it stops returning contacts too.
        if self.limit <= 0:
            return False
        return contact in self.ledger["contacts"] or self.ledger["reservedCents"] + self.charge <= self.limit

    def reserve(self, contact):
        if contact not in self.ledger["contacts"]:
            self.ledger["contacts"].append(contact)
            self.ledger["reservedCents"] += self.charge


def contact_key(kind, platform, dest, item):
    """One contact per person per connected account. Stored only as a hash."""
    account = dest.get("pageId") or dest["accountId"]
    person = item.get("senderId") if kind == "messages" else item.get("authorId")
    # An unidentified commenter counts as a new contact every time.
    person = person or "unknown/" + str(item.get("id"))
    return hashlib.sha256(f"{platform}/{account}/{person}".encode()).hexdigest()[:24]


def first_in_conversation(api, platform, dest, item):
    """True only when the provider's full history of this conversation is exactly this message."""
    try:
        seen = set()
        for row in pages(
            api.get,
            "/messages",
            conversationId=item["conversationId"],
            accountId=dest["accountId"],
            platform=platform,
            limit=250,
        ):
            seen.add(row["id"])
            if row["id"] != item["id"]:
                return False
        return seen == {item["id"]}
    except (RemoteError, KeyError, TypeError, ValueError):
        return False


# Blotato keeps messages for 45 days; a day of margin keeps the retained history complete.
RETAINED = timedelta(days=44)


def fresh_conversation(api, platform, dest, item, activated, now):
    """Positive evidence that Blotato holds this conversation from its start and that it is new.

    Blotato imports no messages from before an account connects and keeps 45 days. A conversation
    counts as new only when Blotato reports it started after this destination's inbox activation
    (which follows the connection) and inside the retained window, and its retained history is
    exactly this message. Anything else, including a read error, stays with the host.
    """
    cid = item["conversationId"]
    try:
        conv = api.get("/conversations/" + quote(cid, safe=""))
        if conv.get("id") != cid or conv.get("accountId") != dest["accountId"] or conv.get("platform") != platform:
            return False
        started = datetime.fromisoformat(conv["createdAt"])
        if not activated <= started <= now or started < now - RETAINED:
            return False
    except (RemoteError, KeyError, TypeError, ValueError, AttributeError):
        return False
    return first_in_conversation(api, platform, dest, item)


def linked_handoffs(holds, key):
    """Every handoff issue ever opened for a DM thread. Older ledgers stored a single number."""
    found = holds.get(key) or []
    return [found] if isinstance(found, int) else list(found)


def process_inbox(brand, targets, store, now, api, issues, writer, contact_limits=None):
    state = store.data
    contacts = Contacts(contact_limits, store, now)
    cursors = state["inboxSince"]
    starts = state.setdefault("activated", {})
    fb_posts = None
    for platform, dest in targets.items():
        if platform == "googlebusiness":
            continue
        key = brand + "/" + platform
        if key not in cursors:
            # A destination's first eligible cycle only records its start time. Anything that
            # arrived earlier is answered by hand in Blotato.
            cursors[key] = starts[key] = now.isoformat()
            store.save()
            continue
        activated = datetime.fromisoformat(starts.setdefault(key, cursors[key]))
        # Each destination keeps its own last successful read, so an outage on one platform cannot
        # skip that platform's arrivals. A small overlap catches delayed delivery; the event
        # ledger prevents repeat replies.
        since = max(
            now - timedelta(days=45), datetime.fromisoformat(cursors[key]) - timedelta(minutes=5), activated
        )
        for kind in ("comments", "messages"):
            params = dict(platform=platform, accountId=dest["accountId"], limit=100)
            if kind == "comments":
                params["since"] = since.isoformat()
            records = []
            for item in pages(api.get, "/" + kind, **params):
                when = datetime.fromisoformat(item["createdAt"])
                if when < since:
                    if kind == "messages":  # documented newest-first
                        break
                    continue
                if when > now or item.get("platform") != platform:
                    continue
                records.append(item)
            groups = {}
            if kind == "messages":
                for item in records:
                    groups.setdefault(item.get("conversationId"), []).append(item)
            for item in records:
                ref = event_ref(brand, kind, item["id"])
                if ref in state["events"]:
                    continue
                action, payload = route(brand, kind, item, dest, now)
                if action == "ignore":
                    continue
                if (
                    platform == "facebook"
                    and kind == "messages"
                    and item.get("recipientId") != dest["pageId"]
                ):
                    if not item.get("recipientId"):
                        notice(
                            issues,
                            "Facebook · unassigned message " + event_ref("fb", kind, item["id"]),
                            f"Review the incoming Facebook message received {item['createdAt']} in Blotato. Its Page could not be verified.",
                        )
                    continue  # the connected login may administer both brands
                if platform == "facebook" and kind == "comments":
                    if fb_posts is None:
                        fb_posts = {
                            p["id"]: (p.get("account") or {}).get("subaccount")
                            for p in pages(
                                api.get,
                                "/posts",
                                platform="facebook",
                                status="published",
                                since=(now - timedelta(days=90)).isoformat(),
                                until=now.isoformat(),
                            )
                        }
                    sub = fb_posts.get(item.get("postId")) or {}
                    if sub.get("id") != dest["pageId"]:
                        if not sub:
                            notice(
                                issues,
                                "Facebook · unassigned comment " + event_ref("fb", kind, item["id"]),
                                f"Review the Facebook comment received {item['createdAt']} in Blotato. Its Page could not be verified.",
                            )
                        continue
                thread_key, cleared = None, False
                holds = state.setdefault("threads", {})
                if kind == "messages" and item.get("conversationId"):
                    thread_key = event_ref(brand, platform, item["conversationId"])
                    linked = linked_handoffs(holds, thread_key)
                    # Handoffs stay linked to their thread. Any open one, including one Hank
                    # reopened, keeps the thread with the host.
                    still_open = next((n for n in linked if issues.open(n)), None)
                    if still_open is not None:
                        state["events"][ref] = {
                            "brand": brand,
                            "kind": kind,
                            "platform": platform,
                            "time": item["createdAt"],
                            "status": "human",
                            "issue": still_open,
                        }
                        store.save()
                        continue
                    # Hank closed every handoff in this thread: the host cleared what came before.
                    cleared = bool(linked)
                if kind == "messages" and action == "send":
                    thread = groups.get(item.get("conversationId"), [])
                    if not item.get("conversationId") or len(thread) != 1:
                        action, payload = "human", None
                    elif not cleared and not fresh_conversation(api, platform, dest, item, activated, now):
                        # The conversation may hold an open service request from before Blotato
                        # captured it, or older than its retention. The host keeps it.
                        action, payload = "human", None
                contact = contact_key(kind, platform, dest, item) if action == "send" else None
                if action == "send" and not contacts.allows(contact):
                    action, payload = "human", None
                    if contacts.limit <= 0:
                        notice(
                            issues,
                            "Blotato automatic replies off · " + contacts.period,
                            "`blotato.contactChargeLimitCents` is 0, so automatic replies are off. Eligible "
                            "Stay Eden questions go to the host with no reply, returning contacts included.",
                        )
                    else:
                        notice(
                            issues,
                            "Blotato reply budget · " + contacts.period,
                            "The contact budget for this Blotato billing period is used. Eligible Stay Eden "
                            "questions now go to the host with no automatic reply. Raise "
                            "`blotato.contactChargeLimitCents` in the local config to resume replies.",
                        )
                if action == "send":
                    try:
                        text = reply_text(writer, payload["text"])
                    except (RemoteError, ValueError):
                        text = None
                    if text is None:
                        action, payload = "human", None
                    else:
                        payload["text"] = text
                record = {"brand": brand, "kind": kind, "platform": platform, "time": item["createdAt"]}
                if action in ("human", "draft"):
                    body = (
                        f"Review the {platform} {kind[:-1]} received {item['createdAt']} in Blotato's inbox. "
                        "Open the original there to read and answer it."
                    )
                    if action == "draft":
                        body += "\n\nSuggested draft: " + payload
                    record["issue"] = notice(issues, BRANDS[brand] + " · inbox " + ref, body)
                    record["status"] = "human"
                    if thread_key:
                        linked = linked_handoffs(holds, thread_key)
                        holds[thread_key] = linked + ([] if record["issue"] in linked else [record["issue"]])
                else:
                    record["status"] = "uncertain"
                    state["events"][ref] = record
                    contacts.reserve(contact)  # kept even if the send's outcome is unknown
                    store.save()
                    result = api.post("/" + kind, payload)
                    if result.get("id"):
                        record.update(status="queued", id=result["id"])
                state["events"][ref] = record
                store.save()
        cursors[key] = now.isoformat()  # both feeds of this destination were read
        store.save()


# Blotato documents that an unmatched submission reads `in-progress` and that a polling deadline
# is not proof of failure. Past this deadline the row stops polling and needs a person.
POLL_DEADLINE = timedelta(hours=24)
DELIVERY_NOTES = {
    "held": {
        "source": "Not submitted: the photo's source was withdrawn or changed after the preview. "
        "Prepare any replacement in a new preview.",
        "destination": "Not submitted: the destination Blotato reports differs from the approved preview. "
        "Check the connected accounts before posting anything by hand.",
    },
    "unresolved": "Blotato still had not confirmed this post a day after its slot. Check it in Blotato's "
    "calendar and reconcile by hand. The Mini has stopped polling it.",
}


def audit_delivery(brand, store, now, api, issues):
    for key, row in store.data["writes"].items():
        if not key.startswith(brand + "/") or row["status"] in ("published", "expired"):
            continue
        if row["status"] in ("accepted", "scheduled") and (
            row["status"] != "scheduled" or datetime.fromisoformat(row["due"]) < now
        ):
            response = api.get("/posts/" + quote(row["id"], safe=""))
            status = response["status"]
            if status in ("scheduled", "published", "failed"):
                row["status"] = status
                store.save()
            if row["status"] in ("accepted", "scheduled") and datetime.fromisoformat(row["due"]) + POLL_DEADLINE < now:
                row["status"] = "unresolved"
                store.save()
        if row["status"] in ("uncertain", "failed", "held", "unresolved") and not row.get("issue"):
            note = DELIVERY_NOTES.get(row["status"])
            if isinstance(note, dict):
                note = note.get(row.get("reason"), "Not submitted. Check this row in Blotato.")
            row["issue"] = notice(
                issues,
                key + " · check delivery",
                note or "Check the scheduled time and publication status in Blotato before any manual retry.",
            )
            store.save()
    for ref, row in store.data["events"].items():
        # An unresolved reply stays here until its issue exists, so a GitHub outage cannot lose it.
        if row["brand"] != brand or row.get("status") not in ("queued", "uncertain", "failed", "unresolved"):
            continue
        if row["status"] == "queued":
            result = api.get("/" + row["kind"] + "/" + quote(row["id"], safe=""))
            if result["status"] in ("failed", "posted", "sent"):
                row["status"] = result["status"]
                store.save()
            elif datetime.fromisoformat(row["time"]) + POLL_DEADLINE < now:
                row["status"] = "unresolved"
                store.save()
        if row["status"] in ("uncertain", "failed", "unresolved") and not row.get("issue"):
            row["issue"] = notice(
                issues,
                BRANDS[brand] + " · check reply " + ref,
                f"Check the {row['platform']} inbox for the item received {row['time']}."
                + (" Blotato never confirmed the reply; the Mini stopped polling it." if row["status"] == "unresolved" else ""),
            )
            store.save()


def source_checker(brand, cfg):
    """recheck(day) for publish: the photo's source must still be eligible and unchanged.

    The Stay Eden checkout is fetched from upstream once per cycle before any photo is checked, so a
    photo the owner removed on GitHub is held. If the fetch fails, no photo is cleared this cycle
    ("retry") and recheck.failed records why, for the caller's alert.
    """
    keys = {}

    def recheck(day):
        origin = day.get("source")
        if not isinstance(origin, dict):
            return "held"  # media with no recorded source cannot be rechecked
        try:
            if brand == "acacia" and origin.get("kind") == "companycam":
                if "k" not in keys:
                    keys["k"] = secret("COMPANYCAM_API_KEY")
                return recheck_company(origin, keys["k"])
            if brand == "eden" and origin.get("kind") == "repo":
                if "fetched" not in keys:
                    keys["fetched"] = False
                    refresh_repo(cfg["edenRepo"])
                    keys["fetched"] = True
                if not keys["fetched"]:
                    return "retry"
                return recheck_repo(origin, cfg["edenRepo"])
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            recheck.failed.append(type(exc).__name__)
            return "retry"
        return "held"

    recheck.failed = []
    return recheck


def weekly(brand, cfg, store, now, api, issues, writer, resolved=None):
    local = now.astimezone(CT).date()
    monday = local - timedelta(days=local.weekday())
    existing = brand + "/" + monday.isoformat() in store.data["batches"]
    upcoming = local.weekday() == 6 or (local.weekday() != 0 and not existing)
    start = monday + timedelta(days=7) if upcoming else monday
    block = cfg["brands"][brand]
    targets = block["targets"]
    check(brand, cfg, live=True)
    record_key = brand + "/" + start.isoformat()
    records = store.data["batches"]
    draft = records.get(record_key, {})
    if "batch" not in draft:

        def compose(brand, week, cards):
            if "captions" not in draft:
                draft["captions"] = write_week(brand, week, cards, writer)
                records[record_key] = draft
                store.save()
            return draft["captions"]

        pick = media_picker(brand, cfg, api, secret("COMPANYCAM_API_KEY") if brand == "acacia" else None)
        batch = plan(brand, start, pick, targets, compose=compose, destinations=resolved)
        records[record_key] = {"batch": batch}
        store.save()
    # On Sunday an approval for the outgoing week's remaining slots also counts.
    for week in dict.fromkeys([monday, start]):
        record = records.get(brand + "/" + week.isoformat())
        if not record or "batch" not in record:
            continue
        batch = record["batch"]
        verify_copy(batch)
        if batch["brand"] != brand or batch["week"] != week.isoformat() or batch["targets"] != targets:
            raise ValueError("Pending batch destinations changed; review original batch")
        check(
            brand,
            cfg,
            [caption(brand, day, p) for day in batch["days"] for p in day["platforms"]],
            live=True,
        )
        body = preview(batch)
        if "issue" not in record:
            record["issue"] = notice(issues, BRANDS[brand] + " · approve " + week.isoformat(), body)
            store.save()
        issue_body, comments = issues.read(record["issue"])
        recheck = source_checker(brand, cfg)
        outcome = publish(
            batch,
            store.data,
            store.save,
            now,
            block["autoPublish"],
            cfg["approverId"],
            issue_body,
            comments,
            lambda payload: api.post("/posts", payload),
            clock=lambda: datetime.now(CT),
            current=resolved,
            recheck=recheck,
        )
        if recheck.failed:
            notice(
                issues,
                f"{BRANDS[brand]} · photo source · {now.astimezone(CT).date()} needs attention",
                f"A photo's source could not be checked ({recheck.failed[0]}), so its posts were not "
                "submitted this cycle. They wait for a later cycle and expire if their slot passes.",
            )
        if outcome == "destinations":
            notice(
                issues,
                BRANDS[brand] + " · approve " + week.isoformat() + " · destinations changed",
                "This week was not scheduled automatically: its destinations differ from the ones you last "
                "approved. Check the names in the preview, then approve it there if they are right.",
            )


def digest_targets(brand, store, week):
    """The destinations posts went to that week, independent of this cycle's lookups.

    The week's own preview names them; failing that, the destinations Hank last confirmed.
    """
    record = store.data["batches"].get(brand + "/" + week) or {}
    shown = (record.get("batch") or {}).get("destinations")
    if shown is None:
        shown = store.data.get("approvedDestinations", {}).get(brand, {})
    return {p: d for p, d in shown.items() if isinstance(d, dict)}


def monday_digest(brand, store, now, api, issues):
    local = now.astimezone(CT).date()
    previous = (local - timedelta(days=local.weekday() + 7)).isoformat()
    digest_key = brand + "/" + previous
    if digest_key not in store.data["digests"]:
        targets = digest_targets(brand, store, previous)
        lo, hi = bounds(previous)
        rows = pages(
            api.get,
            "/posts",
            status="published",
            since=lo.isoformat(),
            until=(hi - timedelta(milliseconds=1)).isoformat(),
        )
        report = digest(brand, previous, targets, rows)
        store.data["digests"][digest_key] = notice(issues, BRANDS[brand] + " · digest " + previous, report)
        store.save()


def tick_brand(brand, cfg, store, now, api, issues, writer):
    targets = cfg["brands"][brand]["targets"]
    day = now.astimezone(CT).date()
    try:
        resolved, problems = check_destinations(brand, cfg, api)
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        notice(
            issues,
            f"{BRANDS[brand]} · destinations · {day} needs attention",
            f"Blotato's connected accounts could not be read ({type(exc).__name__}). Nothing for this brand "
            "ran this cycle. The next cycle retries.",
        )
        raise
    failed = []
    for platform, reason in problems:
        failed.append(platform)
        notice(
            issues,
            f"{BRANDS[brand]} · {LABELS[platform]} destination · {day} needs attention",
            f"The configured {LABELS[platform]} destination is {reason} in Blotato. Its posts and inbox are "
            "paused; the brand's other verified destinations keep working. Reconnect it in Blotato and "
            "compare the IDs with `python -m social.cli accounts`.",
        )
    verified = {p: t for p, t in targets.items() if p in resolved}
    # The inbox acts only for accounts Hank confirmed by approving a preview that named them.
    confirmed = store.data.get("approvedDestinations", {}).get(brand, {})
    inbox_targets = {
        p: t for p, t in verified.items() if p != "googlebusiness" and confirmed.get(p) == resolved[p]
    }
    changed = [p for p in verified if p != "googlebusiness" and p in confirmed and confirmed[p] != resolved[p]]
    for platform in changed:
        failed.append(platform)
        notice(
            issues,
            f"{BRANDS[brand]} · {LABELS[platform]} inbox paused · {day}",
            f"Blotato now reports this {LABELS[platform]} as “{resolved[platform]['name']}”, and its name or "
            "configured ID differs from the account you approved. Its inbox is paused until you approve a "
            "week whose preview shows the right account.",
        )

    def inbox():
        if not inbox_targets:
            return
        check(brand, cfg, live=True)
        process_inbox(brand, inbox_targets, store, now, api, issues, writer, cfg.get("blotato"))

    stages = {
        "inbox": inbox,
        "digest": lambda: monday_digest(brand, store, now, api, issues),
        "weekly": lambda: weekly(brand, cfg, store, now, api, issues, writer, resolved),
        "delivery": lambda: audit_delivery(brand, store, now, api, issues),
    }
    for stage, run in stages.items():
        try:
            run()
        except RunLimit:
            raise
        except (RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            failed.append(stage)
            notice(
                issues,
                f"{BRANDS[brand]} · {stage} · {now.astimezone(CT).date()} needs attention",
                f"The {stage} operation stopped ({type(exc).__name__}). Check the Mini configuration and "
                "the provider dashboard. The next cycle retries eligible work.",
            )
    if failed:
        raise RuntimeError("Incomplete stages: " + ", ".join(failed))
