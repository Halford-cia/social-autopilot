"""A bounded editorial calendar. Only reviewed cards can reach a publisher."""

import hashlib
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .editorial import BOOK, BRANDS, CARDS, validate_caption

CT = ZoneInfo("America/Chicago")
TIMES = {
    "eden": [(19, 30), (12, 15), (12, 15), (19, 30), (19, 30), (9, 30), (19, 30)],
    "acacia": [(8, 15), (12, 15), (10, 0), (12, 15), (10, 0), (9, 30), (12, 15)],
}
ANCHOR = date(2026, 11, 30)


LABELS = {"instagram": "Instagram", "facebook": "Facebook Page", "googlebusiness": "Google Business Profile"}


def channels(brand, url):
    """The feeds a day's post goes to: Instagram needs a photo."""
    return (["instagram"] if url else []) + ["facebook"] + (["googlebusiness"] if brand == "acacia" else [])


def current_format(batch):
    """A batch whose preview bound each verified destination to its IDs (449r2 and later)."""
    shown = batch.get("destinations")
    return isinstance(shown, dict) and all(isinstance(d, dict) for d in shown.values())


def replacement(batch, targets, destinations):
    """A new preview for the destinations whose configured IDs changed (or for a whole batch saved in
    an older format). It keeps the week's captions, times and photos, lists only those destinations,
    and carries its own approval token. Rows already sent for this week stay in the ledger."""
    dests = json.loads(json.dumps({p: destinations[p] for p in targets if p in destinations}))
    days = [dict(d, platforms=[p for p in channels(batch["brand"], d["media"]) if p in dests]) for d in batch["days"]]
    return dict(
        brand=batch["brand"],
        week=batch["week"],
        targets=json.loads(json.dumps(targets)),
        days=days,
        destinations=dests,
        replaces=identity(batch),
    )


def plan(brand, start, media, targets=None, *, compose=None, destinations=None):
    if start.weekday() != 0 or brand not in CARDS:
        raise ValueError("Expected a brand and a Monday in Central Time")
    cards = [((start - ANCHOR).days + i) % len(CARDS[brand]) for i in range(7)]
    texts = compose(brand, start, cards) if compose else [CARDS[brand][n][1] for n in cards]
    if len(texts) != 7:
        raise ValueError("Expected seven captions")
    for card, text in zip(cards, texts):
        validate_caption(brand, card, text)
    days = []
    for i in range(7):
        day = start + timedelta(days=i)
        card, text = cards[i], texts[i]
        source = CARDS[brand][card][0]
        picked = media(source)
        # A picker returns the hosted URL plus a private source reference for the pre-send recheck.
        url, origin = (picked["url"], picked["source"]) if isinstance(picked, dict) else (picked, None)
        platforms = channels(brand, url)
        if destinations is not None:
            # A destination Blotato could not verify is held for the whole week: no row lists it.
            platforms = [p for p in platforms if p in destinations]
        days.append(
            dict(
                card=card,
                text=text,
                media=url,
                source=origin,
                platforms=platforms,
                time=datetime.combine(day, time(*TIMES[brand][i]), CT).isoformat(),
            )
        )
    # Copies: the saved batch never shares a dict with the live config or this cycle's lookups.
    batch = dict(brand=brand, week=start.isoformat(), targets=json.loads(json.dumps(targets or {})), days=days)
    if destinations is not None:
        batch["destinations"] = json.loads(json.dumps(destinations))
    return batch


def verify_copy(batch):
    for day in batch["days"]:
        validate_caption(batch["brand"], day["card"], day["text"])


def caption(brand, day, platform):
    if brand == "eden":
        cta = (
            "Find your stay in the link in our bio."
            if platform == "instagram"
            else "Explore the stays: " + BOOK
        )
    else:
        cta = "Learn more at acaciaroofs.com."
    return day["text"] + " " + cta


def identity(batch):
    captions = [
        caption(batch["brand"], day, platform) for day in batch["days"] for platform in day["platforms"]
    ]
    encoded = json.dumps([batch, captions], sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def preview(batch):
    lines = [
        f"## {BRANDS[batch['brand']]} · week of {batch['week']}",
        "",
        "Central Time. Each row is one feed post per listed destination. Tap a photo to inspect it.",
        "",
    ]
    if batch.get("replaces"):
        lines += [
            "**Corrected destinations for this week.** Only the destinations listed below are posted from "
            "this preview, from its approval on. Posts already sent this week stay as they are, and the "
            "week’s other destinations keep their earlier approval.",
            "",
        ]
    if "destinations" in batch:
        # The names Blotato reports for the configured IDs, so a swapped account shows here. The Page
        # or location ID tells identically named destinations apart.
        def shown(p):
            d = batch["destinations"].get(p)
            if not isinstance(d, dict) or not d.get("name"):
                return "not verified, held: nothing is posted there this week"
            ref = {"facebook": ("Page ID", "pageId"), "googlebusiness": ("location", "locationId")}.get(p)
            return f"“{d['name']}”" + (f" ({ref[0]} `{d.get(ref[1])}`)" if ref else "")

        lines += [
            "**Destinations (as Blotato reports them)**",
            "",
            *[f"- {LABELS[p]}: " + shown(p) for p in LABELS if p in batch["targets"]],
            "",
            "Approving also confirms these are this brand’s accounts. The inbox for these accounts "
            + ("(automatic replies included) " if batch["brand"] == "eden" else "")
            + "starts only after that.",
            "",
        ]
    for d in batch["days"]:
        stamp = datetime.fromisoformat(d["time"]).strftime("%a %I:%M %p")
        media = f"[Photo]({d['media']})" if d["media"] else "Instagram held · no cleared photo"
        where = ", ".join(d["platforms"]) or "no destination (held)"
        lines += [f"**{stamp}** · {where} · {media}  ", d["text"], ""]
    ctas = (
        "Instagram ends with “Find your stay in the link in our bio.” Facebook ends with “Explore the stays: "
        + BOOK
        + "”"
        if batch["brand"] == "eden"
        else "Each caption ends with “Learn more at acaciaroofs.com.”"
    )
    return "\n".join(
        lines + [ctas, "", f"Approve this exact week by commenting `/approve {identity(batch)}`."]
    )
