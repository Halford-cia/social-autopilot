from datetime import datetime, timedelta

from .api import RemoteError
from .planner import caption, identity, preview, verify_copy


def post_body(batch, day, platform, *, scheduled=False):
    """The Publish Post body. Production sends at the slot, so `scheduledTime` is omitted and Blotato
    publishes immediately (documented: no root `scheduledTime` and no `useNextFreeSlot`)."""
    target = batch["targets"][platform]
    options = {"targetType": platform}
    if platform == "facebook":
        options["pageId"] = target["pageId"]
    if platform == "googlebusiness":
        options["locationId"] = target["locationId"]
    return {
        **({"scheduledTime": day["time"]} if scheduled else {}),
        "post": {
            "accountId": target["accountId"],
            "target": options,
            "content": {
                "text": caption(batch["brand"], day, platform),
                "platform": platform,
                "mediaUrls": [day["media"]] if day["media"] else [],
            },
        },
    }


def approves(comment, owner, token):
    """Hank's own comment, never edited by anyone, with the exact command for this batch."""
    return (
        (comment.get("user") or {}).get("id") == owner
        and comment.get("edited") is False
        and str(comment.get("body", "")).strip() == "/approve " + token
    )


# Rows stay in the local ledger until their slot arrives and are then sent for immediate publication,
# in the same cycle that rechecked the approval or the autoPublish switch, the preview, the
# destination and the photo source. Nothing waits in Blotato's queue, so a later switch-off, closed
# issue or withdrawn photo always reaches every post that has not gone out. (Blotato documents no
# link from a submission to its schedule, so a queued post could not be cancelled reliably.)
# SUBMIT_AHEAD stays zero in production; unit tests of other guards raise it to submit early.
SUBMIT_AHEAD = timedelta(0)
# A slot is still sent this long after its time (a 15-minute cycle, plus a missed or slow one).
# Later than that it expires and is never posted late.
LATE_GRACE = timedelta(minutes=30)


def record_confirmation(state, brand, shown, now):
    """Keep each confirmed destination with the time it took effect, for the Monday digest.

    A destination confirmed before this history existed is kept first, with no start time: it counts
    from the beginning until the next confirmation of that feed. A later approval of a different
    destination starts it at the moment the approval was applied (no post goes there earlier).
    """
    history = state.setdefault("destinationHistory", {}).setdefault(brand, {})
    before = state.get("approvedDestinations", {}).get(brand, {})
    for platform, dest in shown.items():
        if not isinstance(dest, dict):
            continue
        entries = history.setdefault(platform, [])
        if not entries and isinstance(before.get(platform), dict) and before[platform] != dest:
            entries.append({"since": None, "destination": dict(before[platform])})
        if not entries or entries[-1]["destination"] != dest:
            entries.append({"since": now.isoformat() if entries else None, "destination": dict(dest)})


def bound(shown, target):
    """The preview verified this destination and it carries the configured IDs."""
    return isinstance(shown, dict) and bool(shown.get("name")) and all(shown.get(k) == v for k, v in target.items())


def publish(
    batch,
    state,
    save,
    now,
    auto,
    owner,
    issue_body,
    comments,
    send,
    clock=None,
    *,
    current=None,
    recheck=None,
    ahead=None,
    targets=None,
):
    """Claim before sending. An unknown POST outcome needs operator reconciliation.

    current: destinations Blotato reports now ({platform: {IDs..., "name"}}). A row is only sent to a
    destination the approved preview verified, with the same name and IDs Blotato reports now; a
    destination the preview marked held never receives a post. recheck(day) -> "ok" | "held" |
    "retry" confirms a photo's source is still eligible and unchanged. targets: the destinations
    configured now; a platform whose configured IDs differ from the batch's is left to the
    replacement preview for the corrected destination (no ledger entry here). A row is sent once its
    slot arrives (`ahead`, default SUBMIT_AHEAD = 0) and expires LATE_GRACE after it.
    """
    ahead = SUBMIT_AHEAD if ahead is None else ahead
    verify_copy(batch)
    brand, week = batch["brand"], batch["week"]
    token = identity(batch)
    approved = any(approves(c, owner, token) for c in comments)
    prior = set(w for w in state["approvals"].get(brand, []) if w < week)
    shown = batch.get("destinations") or {}
    confirmed = state.setdefault("approvedDestinations", {})
    if preview(batch) != issue_body:
        return
    if not approved:
        # A replacement preview promises a hold until its own approval, so autoPublish never sends it.
        if batch.get("replaces") or auto is not True or len(prior) < 2:
            return
        # An automatic week only goes to the destinations Hank last confirmed on his phone. Only the
        # feeds this preview still owns are compared: a feed whose configured destination changed
        # belongs to its replacement preview, and this one sends nothing there.
        owned = [p for p in batch["targets"] if targets is None or targets.get(p) == batch["targets"][p]]
        if any(shown.get(p) != confirmed.get(brand, {}).get(p) for p in owned):
            return "destinations"
    applied = state.setdefault("appliedApprovals", {}).setdefault(brand, [])
    if approved and token not in applied:
        # Hank approves a batch. Each approval is applied once, so an older approved preview that is
        # still open never takes back a destination Hank confirmed later. Delivery failures and
        # expired slots have their own ledger.
        state["approvals"][brand] = sorted({*state["approvals"].get(brand, []), week})
        if shown:
            record_confirmation(state, brand, shown, now)
            # A replacement preview confirms only the corrected destinations it names.
            confirmed[brand] = {**confirmed.get(brand, {}), **shown} if batch.get("replaces") else dict(shown)
        applied.append(token)
        save()
    writes = state["writes"]
    verdicts = {}
    for day in batch["days"]:
        for platform in day["platforms"]:
            key = "/".join((brand, day["time"][:10], platform))
            if key in writes:
                continue
            target = batch["targets"].get(platform)
            if targets is not None and targets.get(platform) != target:
                continue  # the configured destination changed: its replacement preview covers it
            due = datetime.fromisoformat(day["time"])
            moment = max(now, clock()) if clock else now
            if moment - due > LATE_GRACE:
                writes[key] = {"status": "expired"}
                save()
                continue
            if due - now > ahead:
                continue  # not yet: the cycle at the slot rechecks everything and sends
            if target is None or not bound(shown.get(platform), target):
                continue  # held in the preview: nothing is ever sent there for this week
            if current is None or current.get(platform) is None:
                continue  # not verified now; retry on a later cycle until the slot passes
            if current[platform] != shown[platform]:
                writes[key] = {"status": "held", "reason": "destination", "due": day["time"], "batch": token}
                save()
                continue
            if day["media"] and recheck is not None:
                if day["time"] not in verdicts:
                    verdicts[day["time"]] = recheck(day)
                verdict = verdicts[day["time"]]
                if verdict == "retry":
                    continue
                if verdict != "ok":
                    writes[key] = {"status": "held", "reason": "source", "due": day["time"], "batch": token}
                    save()
                    continue
            scheduled = due > (max(now, clock()) if clock else now)  # only when a test raises `ahead`
            payload = post_body(batch, day, platform, scheduled=scheduled)
            writes[key] = {"status": "uncertain", "due": day["time"], "batch": token}
            save()
            try:
                result = send(payload)
            except RemoteError as exc:
                if exc.status == 429:  # documented rejection, safe to try on a later tick
                    del writes[key]
                    save()
                raise
            if result.get("postSubmissionId"):
                writes[key]["id"] = result["postSubmissionId"]
                if not scheduled:
                    # Documented: an immediate publish answers with no scheduledTime.
                    if not result.get("scheduledTime"):
                        writes[key]["status"] = "accepted"
                elif result.get("scheduledTime") and datetime.fromisoformat(result["scheduledTime"]) == due:
                    writes[key]["status"] = "accepted"
            save()
