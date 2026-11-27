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


def plan(brand, start, media, targets=None, *, compose=None):
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
        url = media(source)
        platforms = (["instagram"] if url else []) + ["facebook"]
        if brand == "acacia":
            platforms.append("googlebusiness")
        days.append(
            dict(
                card=card,
                text=text,
                media=url,
                platforms=platforms,
                time=datetime.combine(day, time(*TIMES[brand][i]), CT).isoformat(),
            )
        )
    return dict(brand=brand, week=start.isoformat(), targets=targets or {}, days=days)


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
    for d in batch["days"]:
        stamp = datetime.fromisoformat(d["time"]).strftime("%a %I:%M %p")
        media = f"[Photo]({d['media']})" if d["media"] else "Instagram held · no cleared photo"
        lines += [f"**{stamp}** · {', '.join(d['platforms'])} · {media}  ", d["text"], ""]
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
