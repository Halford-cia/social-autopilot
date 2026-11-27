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


def publish(batch, state, save, now, auto, owner, issue_body, comments, send, clock=None):
    """Claim before sending. An unknown POST outcome needs operator reconciliation."""
    verify_copy(batch)
    brand, week = batch["brand"], batch["week"]
    token = identity(batch)
    approved = any(
        c.get("user", {}).get("id") == owner and c.get("body", "").strip() == "/approve " + token
        for c in comments
    )
    prior = set(w for w in state["approvals"].get(brand, []) if w < week)
    if preview(batch) != issue_body or not (approved or (auto is True and len(prior) >= 2)):
        return
    writes = state["writes"]
    keys = []
    for day in batch["days"]:
        for platform in day["platforms"]:
            key = "/".join((brand, day["time"][:10], platform))
            keys.append(key)
            if key in writes:
                continue
            due = datetime.fromisoformat(day["time"])
            current = clock() if clock else now
            if due <= current:
                writes[key] = {"status": "expired"}
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
    if approved and all(
        writes[k]["status"] in ("accepted", "scheduled", "published") and writes[k].get("batch") == token
        for k in keys
    ):
        state["approvals"][brand] = sorted(set(state["approvals"].get(brand, [])) | {week})
        save()
