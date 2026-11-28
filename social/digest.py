from datetime import date, datetime, time, timedelta

from .planner import BRANDS, CT


def bounds(week):
    start = datetime.combine(date.fromisoformat(week), time(), CT)
    return start, start + timedelta(days=7)


def effective(history, when):
    """The destination in effect at `when`: the latest confirmation that started at or before it.
    The first confirmation of a feed counts from the beginning. A bare destination dict is one
    confirmation with no start time."""
    if isinstance(history, dict):
        return history
    current = None
    for n, entry in enumerate(history or []):
        since = entry.get("since")
        if n == 0 or since is None or datetime.fromisoformat(since) <= when:
            current = entry["destination"]
    return current


def digest(brand, week, targets, rows):
    """targets: per platform, a destination dict or its confirmation history
    ([{"since": iso | None, "destination": {...}}]). A post counts once, and only on the destination
    that was confirmed for its feed when it was published."""
    start, end = bounds(week)
    measured, total, excluded = [], 0, set()
    follows = []
    seen = set()
    for row in rows:
        platform, account, state = row["platform"], row.get("account") or {}, row["state"]
        if state["type"] != "published" or row["id"] in seen:
            continue
        when = datetime.fromisoformat(row["postTime"])
        if not start <= when < end:
            continue
        dest = effective(targets.get(platform), when)
        if not dest or account.get("id") != dest["accountId"]:
            continue
        seen.add(row["id"])
        if platform == "googlebusiness":
            excluded.add("Google Business Profile")
            continue
        if platform == "facebook" and (account.get("subaccount") or {}).get("id") != dest["pageId"]:
            continue
        total += 1
        snapshot = ((state.get("analytics") or {}).get("latest") or {}).get("metrics") or {}
        count = snapshot.get("followsCount")
        if isinstance(count, (str, int)) and str(count).isdecimal():
            follows.append(int(count))
        reach = snapshot.get("reachCount")
        if isinstance(reach, (str, int)) and str(reach).isdecimal():
            local_day = datetime.fromisoformat(row["postTime"]).astimezone(CT).date().isoformat()
            measured.append((int(reach), platform, row["id"], local_day))
    measured.sort(reverse=True)
    label = f"{sum(r[0] for r in measured):,}" if measured else "unavailable"
    lines = [
        f"## {BRANDS[brand]} · {week} through {(end.date() - timedelta(days=1)).isoformat()}",
        "",
        f"Sum of post reach: {label} ({len(measured)}/{total} IG/FB posts measured). Audiences may overlap.",
        f"Post-attributed follows: {sum(follows) if follows else 'unavailable'} ({len(follows)}/{total} IG/FB posts measured).",
        "Follower change: unavailable. Blotato's documented IG/FB analytics expose no account follower history.",
        "",
        "**Top posts by reported reach**",
        "",
    ]
    for reach, platform, pid, day in measured[:3]:
        lines.append(f"- {day} · {platform} · Blotato post {pid} · {reach:,} reached")
    if not measured:
        lines.append("No measured posts in this window.")
    if excluded:
        lines.append("Google Business Profile reach is unavailable from Blotato.")
    if measured:
        lines.append(
            f"\nTry next: repeat the subject of {measured[0][1]} post {measured[0][2]} at a second posting time and compare reach."
        )
    else:
        lines.append(
            "\nTry next: check that metrics collection is enabled, then compare two posts with reported reach."
        )
    return "\n".join(lines)
