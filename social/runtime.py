import fcntl
import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta
from urllib.parse import quote

from .api import RemoteError, pages, secret
from .claude import RunLimit, validate_limits
from .digest import bounds, digest
from .inbox import route
from .media import media_picker
from .planner import BRANDS, CT, caption, plan, preview, verify_copy
from .sources import check
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


def validate_config(cfg):
    validate_limits(cfg["claude"])
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


def check_destinations(brand, cfg, api):
    targets = cfg["brands"][brand]["targets"]
    accounts = {item["id"]: item["platform"] for item in api.get("/users/me/accounts")["items"]}
    for platform, target in targets.items():
        aid = target["accountId"]
        if accounts.get(aid) != platform:
            raise ValueError("Connected account does not match platform")
        if platform != "instagram":
            subs = api.get("/users/me/accounts/" + quote(aid, safe="") + "/subaccounts")["items"]
            ids = {s["id"] for s in subs if s["accountId"] == aid}
            if (target.get("pageId") or target.get("locationId")) not in ids:
                raise ValueError("Destination is not connected")


def event_ref(brand, kind, eid):
    return hashlib.sha256(f"{brand}/{kind}/{eid}".encode()).hexdigest()[:20]


def notice(issues, title, message):
    return issues.ensure("Social · " + title, message)


def process_inbox(brand, targets, store, now, api, issues, writer):
    state = store.data
    if brand not in state["inboxSince"]:
        state["inboxSince"][brand] = now.isoformat()
        store.save()
        return
    # A small overlap catches delayed delivery. The event ledger prevents repeat replies.
    watermark = datetime.fromisoformat(state["inboxSince"][brand])
    since = max(now - timedelta(days=45), watermark - timedelta(minutes=5))
    activated = state.setdefault("activated", {}).setdefault(brand, state["inboxSince"][brand])
    since = max(since, datetime.fromisoformat(activated))
    fb_posts = None
    for platform, dest in targets.items():
        if platform == "googlebusiness":
            continue
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
                thread_key = None
                holds = state.setdefault("threads", {})
                if kind == "messages" and item.get("conversationId"):
                    thread_key = event_ref(brand, platform, item["conversationId"])
                    if thread_key in holds:
                        if issues.open(holds[thread_key]):
                            state["events"][ref] = {
                                "brand": brand,
                                "kind": kind,
                                "platform": platform,
                                "time": item["createdAt"],
                                "status": "human",
                                "issue": holds[thread_key],
                            }
                            store.save()
                            continue
                        del holds[thread_key]
                if kind == "messages" and action == "send":
                    thread = groups.get(item.get("conversationId"), [])
                    if not item.get("conversationId") or len(thread) != 1:
                        action, payload = "human", None
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
                        holds[thread_key] = record["issue"]
                else:
                    record["status"] = "uncertain"
                    state["events"][ref] = record
                    store.save()
                    result = api.post("/" + kind, payload)
                    if result.get("id"):
                        record.update(status="queued", id=result["id"])
                state["events"][ref] = record
                store.save()
    state["inboxSince"][brand] = now.isoformat()
    store.save()


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
        if row["status"] in ("uncertain", "failed") and not row.get("issue"):
            row["issue"] = notice(
                issues,
                key + " · check delivery",
                "Check the scheduled time and publication status in Blotato before any manual retry.",
            )
            store.save()
    for ref, row in store.data["events"].items():
        if row["brand"] != brand or row.get("status") not in ("queued", "uncertain", "failed"):
            continue
        if row["status"] == "queued":
            result = api.get("/" + row["kind"] + "/" + quote(row["id"], safe=""))
            if result["status"] in ("failed", "posted", "sent"):
                row["status"] = result["status"]
                store.save()
        if row["status"] in ("uncertain", "failed") and not row.get("issue"):
            row["issue"] = notice(
                issues,
                BRANDS[brand] + " · check reply " + ref,
                f"Check the {row['platform']} inbox for the item received {row['time']}.",
            )
            store.save()


def weekly(brand, cfg, store, now, api, issues, writer):
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
        batch = plan(brand, start, pick, targets, compose=compose)
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
        publish(
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
        )


def monday_digest(brand, targets, store, now, api, issues):
    local = now.astimezone(CT).date()
    previous = (local - timedelta(days=local.weekday() + 7)).isoformat()
    digest_key = brand + "/" + previous
    if digest_key not in store.data["digests"]:
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
    check_destinations(brand, cfg, api)

    def inbox():
        check(brand, cfg, live=True)
        process_inbox(brand, targets, store, now, api, issues, writer)

    stages = {
        "inbox": inbox,
        "digest": lambda: monday_digest(brand, targets, store, now, api, issues),
        "weekly": lambda: weekly(brand, cfg, store, now, api, issues, writer),
        "delivery": lambda: audit_delivery(brand, store, now, api, issues),
    }
    failed = []
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
