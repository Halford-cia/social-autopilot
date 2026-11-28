from datetime import datetime

from .api import RemoteError
from .planner import caption, identity, preview, verify_copy


def post_body(batch, day, platform):
    target = batch["targets"][platform]
    options = {"targetType": platform}
    if platform == "facebook":
        options["pageId"] = target["pageId"]
    if platform == "googlebusiness":
        options["locationId"] = target["locationId"]
    return {
        "scheduledTime": day["time"],
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


def publish(
    batch, state, save, now, auto, owner, issue_body, comments, send, clock=None, *, current=None, recheck=None
):
    """Claim before sending. An unknown POST outcome needs operator reconciliation.

    current: destination names Blotato reports now ({platform: name}); a row is only sent to the
    destination shown in the approved preview. recheck(day) -> "ok" | "held" | "retry" confirms a
    photo's source is still eligible and unchanged before each submission.
    """
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
        if auto is not True or len(prior) < 2:
            return
        # An automatic week only goes to the destinations Hank last confirmed on his phone.
        if any(shown.get(p) != confirmed.get(brand, {}).get(p) for p in batch["targets"]):
            return "destinations"
    if approved and (week not in state["approvals"].get(brand, []) or (shown and confirmed.get(brand) != shown)):
        # Hank approves a batch. Delivery failures and expired slots have their own ledger.
        state["approvals"][brand] = sorted({*state["approvals"].get(brand, []), week})
        if shown:
            confirmed[brand] = dict(shown)
        save()
    writes = state["writes"]
    verdicts = {}
    for day in batch["days"]:
        for platform in day["platforms"]:
            key = "/".join((brand, day["time"][:10], platform))
            if key in writes:
                continue
            due = datetime.fromisoformat(day["time"])
            moment = clock() if clock else now
            if due <= moment:
                writes[key] = {"status": "expired"}
                save()
                continue
            if current is not None and current.get(platform) != shown.get(platform):
                if current.get(platform) is None and shown.get(platform) is not None:
                    continue  # disconnected now; retry on a later cycle until the slot passes
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
            payload = post_body(batch, day, platform)
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
                if result.get("scheduledTime") and datetime.fromisoformat(result["scheduledTime"]) == due:
                    writes[key]["status"] = "accepted"
            save()
