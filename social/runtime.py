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
from .planner import (
    BRANDS,
    CT,
    LABELS,
    b98b864_preview,
    caption,
    current_format,
    identity,
    plan,
    preview,
    replacement,
    verify_copy,
)
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


def handoffs_closed_at(issues, linked):
    """When Hank last closed this thread's handoffs (the latest close), or None if any close time is
    missing or unreadable. Called only once every linked handoff is closed."""
    try:
        return max(datetime.fromisoformat(issues.closed_at(n)) for n in linked)
    except (TypeError, ValueError):
        return None


def after_resolution(api, platform, dest, item, closed):
    """The host's closed handoff cleared this conversation up to its close time, and nothing more.

    True only when this message arrived after that close and Blotato's history of the conversation
    holds nothing else after it: no later guest message and no later host message (Blotato lists the
    host's own messages as `outgoing`). A host who writes again after closing the handoff is still
    working the conversation, so a guest's next question stays with the host. A read error also does.
    """
    try:
        if datetime.fromisoformat(item["createdAt"]) <= closed:
            return False
        found = False
        for row in pages(
            api.get,
            "/messages",
            conversationId=item["conversationId"],
            accountId=dest["accountId"],
            platform=platform,
            limit=250,
        ):
            if row["id"] == item["id"]:
                found = True
            elif datetime.fromisoformat(row["createdAt"]) > closed:
                return False
        return found
    except (RemoteError, KeyError, TypeError, ValueError):
        return False


def linked_handoffs(holds, key):
    """Every handoff issue ever opened for a DM thread. Older ledgers stored a single number."""
    found = holds.get(key) or []
    return [found] if isinstance(found, int) else list(found)


def inbox_key(brand, platform, dest):
    """Cursor and inbox start belong to one verified destination: a replacement account or Page
    starts its own inbox, and the same destination recovering from an outage keeps its cursor."""
    return "/".join([brand, platform, dest["accountId"]] + ([dest["pageId"]] if dest.get("pageId") else []))


def adopt_legacy_cursors(brand, store):
    """Bind each cursor from a ledger that kept one cursor per platform (before 449r3) to its owner.

    The owner is the destination Hank had confirmed, with its IDs, when the previous version ran.
    This runs at the start of the first cycle on this version, before any approval in that cycle
    can change the confirmation. Without IDs that establish the owner (round-two ledgers stored
    names only), the old cursor is dropped and each destination starts at its first eligible cycle.
    """
    state = store.data
    cursors, starts = state["inboxSince"], state.setdefault("activated", {})
    confirmed = state.get("approvedDestinations", {}).get(brand, {})
    changed = False
    for platform in ("instagram", "facebook"):
        legacy = brand + "/" + platform
        if legacy not in cursors and legacy not in starts:
            continue
        owner = confirmed.get(platform)
        needed = ("accountId", "pageId") if platform == "facebook" else ("accountId",)
        if isinstance(owner, dict) and all(isinstance(owner.get(k), str) and owner[k] for k in needed):
            key = inbox_key(brand, platform, owner)
            if key not in cursors and legacy in cursors:
                cursors[key] = cursors[legacy]
                starts[key] = starts.get(legacy, cursors[key])
        cursors.pop(legacy, None)
        starts.pop(legacy, None)
        changed = True
    if changed:
        store.save()


def thread_replies(api, platform, dest, item):
    """True when the public thread under this top-level comment has any reply, or cannot be read.

    A reply is a guest's clarification (which may change the question) or a person answering; either
    way the thread is not a simple question any more. Blotato lists direct replies by parent ID.
    """
    try:
        for row in pages(
            api.get,
            "/comments",
            parentCommentId=item["id"],
            platform=platform,
            accountId=dest["accountId"],
            limit=250,
        ):
            if row.get("id") != item["id"]:
                return True
        return False
    except (RemoteError, KeyError, TypeError, ValueError):
        return True


def process_inbox(brand, targets, store, now, api, issues, writer, contact_limits=None):
    state = store.data
    contacts = Contacts(contact_limits, store, now)
    cursors = state["inboxSince"]
    starts = state.setdefault("activated", {})
    fb_posts = None
    for platform, dest in targets.items():
        if platform == "googlebusiness":
            continue
        key = inbox_key(brand, platform, dest)
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
                thread_key, linked = None, []
                holds = state.setdefault("threads", {})
                if kind == "comments":
                    # One public thread: a top-level comment and its direct replies.
                    root = item.get("parentCommentId") or item["id"]
                    thread_key = event_ref(brand, platform + "/comments", root)
                    if action == "send":
                        owner = next((n for n in linked_handoffs(holds, thread_key) if issues.open(n)), None)
                        if owner is not None:
                            # Someone in this thread was already handed to the host.
                            state["events"][ref] = {
                                "brand": brand,
                                "kind": kind,
                                "platform": platform,
                                "time": item["createdAt"],
                                "status": "human",
                                "issue": owner,
                            }
                            store.save()
                            continue
                        if thread_replies(api, platform, dest, item):
                            action, payload = "human", None
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
                if kind == "messages" and action == "send":
                    thread = groups.get(item.get("conversationId"), [])
                    if not item.get("conversationId") or len(thread) != 1:
                        action, payload = "human", None
                    elif linked:
                        # Hank closed every handoff in this thread. That clears the history up to his
                        # close, not anything the host or guest wrote after it.
                        closed = handoffs_closed_at(issues, linked)
                        if closed is None or not after_resolution(api, platform, dest, item, closed):
                            action, payload = "human", None
                    elif not fresh_conversation(api, platform, dest, item, activated, now):
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
        for batch, holder, title in previews(brand, week.isoformat(), record, targets, resolved, store, issues):
            publish_preview(brand, cfg, store, now, api, issues, batch, holder, title, resolved)


def earlier_render(record, issues, store):
    """True when the saved preview's issue holds the b98b864 rendering of its batch (names with no
    Page or location ID). That approval cannot match today's preview, so the week would otherwise
    stall with no error. The answer is kept once known; an issue body matching neither rendering
    (an edited preview) is not, and stays held by the exact-body check."""
    if record.get("render") in ("current", "b98b864"):
        return record["render"] == "b98b864"
    if issues is None or "issue" not in record:
        return False
    body, _ = issues.read(record["issue"])
    if body == preview(record["batch"]):
        record["render"] = "current"
    elif body == b98b864_preview(record["batch"]):
        record["render"] = "b98b864"
    else:
        return False
    store.save()
    return record["render"] == "b98b864"


def previews(brand, week, record, targets, resolved, store, issues=None):
    """The previews that may post this week: the saved one, and a replacement for any destination
    whose configured IDs changed since it was saved (all of them for a batch saved in the round-two
    format, which bound only names, or still shown in the b98b864 rendering, which showed no IDs). A
    replacement keeps the week's receipts and needs its own approval; the saved preview keeps
    posting to the destinations that did not change."""
    batch = record["batch"]
    if batch["brand"] != brand or batch["week"] != week:
        raise ValueError("Pending batch does not match its week; review original batch")
    legacy = resolved is not None and (not current_format(batch) or earlier_render(record, issues, store))
    changed = sorted(set(targets) if legacy else {p for p in targets if batch["targets"].get(p) != targets[p]})
    if changed and resolved is None:
        raise ValueError("Pending batch destinations changed; review original batch")
    out = [] if legacy else [(batch, record, BRANDS[brand] + " · approve " + week)]
    if changed:
        want = {p: targets[p] for p in changed}
        held = record.get("replacement")
        if (
            not held
            or held["batch"]["targets"] != want
            or any(p in resolved and p not in held["batch"]["destinations"] for p in want)
        ):
            # New or corrected again, or a destination verified only now: a fresh preview and token.
            held = {"batch": replacement(batch, want, resolved)}
            record["replacement"] = held
            store.save()
        token = identity(held["batch"])
        out.append((held["batch"], held, f"{BRANDS[brand]} · approve {week} · corrected destinations {token[:8]}"))
    return out


def publish_preview(brand, cfg, store, now, api, issues, batch, record, title, resolved):
    block = cfg["brands"][brand]
    verify_copy(batch)
    check(
        brand,
        cfg,
        [caption(brand, day, p) for day in batch["days"] for p in day["platforms"]],
        live=True,
    )
    body = preview(batch)
    if "issue" not in record:
        record["issue"] = notice(issues, title, body)
        record["render"] = "current"
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
        targets=block["targets"],
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
            title + " · destinations changed",
            "This week was not posted automatically: its destinations differ from the ones you last "
            "approved. Check the names and IDs in the preview, then approve it there if they are right.",
        )


def digest_targets(brand, store, week):
    """The destinations each feed posted to, with the time each took effect, independent of this
    cycle's lookups and of the current config.

    Only destinations Hank confirmed by approving a preview count: an automatic week posts only to
    those, and a preview nobody approved (a replacement included) adds nothing. A ledger from before
    this history was kept counts the destination Hank last confirmed for the whole week.
    """
    history = store.data.get("destinationHistory", {}).get(brand, {})
    out = {p: list(h) for p, h in history.items() if h}
    for p, d in store.data.get("approvedDestinations", {}).get(brand, {}).items():
        if p not in out and isinstance(d, dict):
            out[p] = [{"since": None, "destination": d}]
    return out


MEASURED = ("instagram", "facebook")


def monday_digest(brand, store, now, api, issues):
    """The digest waits while an IG/FB destination is still unknown (no preview named it and Hank has
    not confirmed it), so the first Monday does not close an empty report before the first approval.
    By Sunday it is posted anyway, marked partial, naming what could not be measured."""
    local = now.astimezone(CT).date()
    previous = (local - timedelta(days=local.weekday() + 7)).isoformat()
    digest_key = brand + "/" + previous
    if digest_key not in store.data["digests"]:
        targets = digest_targets(brand, store, previous)
        missing = [p for p in MEASURED if p not in targets]
        if missing and local.weekday() < 6:
            return
        lo, hi = bounds(previous)
        rows = pages(
            api.get,
            "/posts",
            status="published",
            since=lo.isoformat(),
            until=(hi - timedelta(milliseconds=1)).isoformat(),
        )
        report = digest(brand, previous, targets, rows)
        if missing:
            report += (
                "\n\nPartial: not measured: "
                + ", ".join(LABELS[p] for p in missing)
                + ". No preview named it that week and it was not confirmed by Sunday."
            )
        store.data["digests"][digest_key] = notice(issues, BRANDS[brand] + " · digest " + previous, report)
        store.save()


def tick_brand(brand, cfg, store, now, api, issues, writer):
    targets = cfg["brands"][brand]["targets"]
    day = now.astimezone(CT).date()
    adopt_legacy_cursors(brand, store)
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
