from datetime import date, datetime, time, timedelta

from .planner import BRANDS, CT


def bounds(week):
    start = datetime.combine(date.fromisoformat(week), time(), CT)
    return start, start + timedelta(days=7)


def digest(brand, week, targets, rows):
    start, end = bounds(week)
    measured, total, excluded = [], 0, set()
    seen = set()
    for row in rows:
        platform, account, state = row["platform"], row.get("account") or {}, row["state"]
        dest = targets.get(platform)
        if not dest or account.get("id") != dest["accountId"] or state["type"] != "published":
            continue
        if not start <= datetime.fromisoformat(row["postTime"]) < end or row["id"] in seen:
            continue
        seen.add(row["id"])
        if platform == "googlebusiness":
            excluded.add("Google Business Profile")
            continue
        if platform == "facebook" and (account.get("subaccount") or {}).get("id") != dest["pageId"]:
            continue
        total += 1
        snapshot = ((state.get("analytics") or {}).get("latest") or {}).get("metrics") or {}
        reach = snapshot.get("reachCount")
        if isinstance(reach, (str, int)) and str(reach).isdecimal():
            measured.append((int(reach), platform, row["id"], row["postTime"][:10]))
    measured.sort(reverse=True)
    label = f"{sum(r[0] for r in measured):,}" if measured else "unavailable"
    lines = [
        f"## {BRANDS[brand]} · {week} through {(end.date() - timedelta(days=1)).isoformat()}",
        "",
        f"Sum of post reach: {label} ({len(measured)}/{total} IG/FB posts measured). Audiences may overlap.",
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
