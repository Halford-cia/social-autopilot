"""A bounded editorial calendar. Only reviewed cards can reach a publisher."""

import hashlib
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
BRANDS = {"eden": "Stay Eden", "acacia": "Acacia Roofing"}
BOOK = "https://stayeden.netlify.app/stays/"
# Keys identify a source property or a FACTS.md section, never a customer/job.
CARDS = {
    "eden": [
        (
            "serenity",
            "A suspended king bed or a long soak? Treehouse Serenity Dome has room for two. Pick your first stop.",
        ),
        (
            "cloud",
            "A wooded bluff, a private hot tub, and two of you at Cloud Dome. Who gets to pick the weekend?",
        ),
        (
            "walnut",
            "Coffee on the observation deck or breakfast in the full kitchen? Walnut Horizon has room for four.",
        ),
        ("grun", "Pull a chair up to the fire pit. Das Grun Haus is a two-bedroom cottage for up to six."),
        ("cherry", "Movie under canvas tonight? Cherry Cove has a projector and room for two."),
        (
            "oak",
            "Pick a film for Oak Retreat. This canvas stay for two comes with a projector and a private hot tub.",
        ),
        (
            "tavern",
            "Send this one to the group chat. The Hidden Tavern has five bedrooms, two hot tubs and a speakeasy to find.",
        ),
        ("pecan", "Sleep under canvas, about ten feet up. Who would you take to Pecan Haven?"),
        (
            "cozy",
            "Pack the book you keep meaning to finish. Cozy Tiny Home has a reading loft for a quiet chapter.",
        ),
        ("lunar", "Start under the treetop shower. End in the hot tub. Which sounds better at Lunar Dome?"),
        ("grun", "Yard games before dinner, a fire pit after. Bring up to six to Das Grun Haus."),
        (
            "serenity",
            "Cowboy pool or soaking tub? At Treehouse Serenity Dome you can leave that decision for later.",
        ),
        (
            "tavern",
            "Five bedrooms and a bunkroom at The Hidden Tavern. Who in your group will find the speakeasy first?",
        ),
        (
            "walnut",
            "Bring the breakfast fixings. Walnut Horizon has a full kitchen, an observation deck and space for four.",
        ),
        ("cherry", "Projector or cowboy pool? Cherry Cove is a canvas stay for two near Fredericksburg."),
        (
            "lunar",
            "Leave a little time for the outdoor shower at Lunar Dome. The wineries and Luckenbach are nearby.",
        ),
        (
            "cozy",
            "A chapter in the reading loft, then a dip in the cowboy pool. Save Cozy Tiny Home for a weekend for two.",
        ),
    ],
    "acacia": [
        (
            "§12,16",
            "Wondering what a roof inspection looks like? A free Acacia inspection usually takes 30 to 60 minutes. You get a written report and photos.",
        ),
        (
            "§16",
            "What happens after the shingles come off? We inspect the decking on every roof replacement.",
        ),
        (
            "§11",
            "Preparing to talk with your insurer? We can inspect the roof, document damage with photos and provide an estimate. You file the claim. Your insurer decides what the policy covers.",
        ),
        ("§16", "Cleanup counts. Every Acacia roof replacement includes a magnetic nail sweep."),
        (
            "§8",
            "What does the workmanship warranty cover? Our five-year transferable workmanship warranty covers installation defects.",
        ),
        (
            "§12",
            "Wondering whether we work in your area? Acacia serves homeowners within 90 miles of Austin.",
        ),
        (
            "§16",
            "Look past the shingles. Every replacement includes synthetic underlayment over the whole deck.",
        ),
        (
            "§16",
            "Where does ice and water shield go? We install ice and water shield in the valleys and around penetrations (pipes, vents, chimneys, skylights and walls).",
        ),
        ("§16", "Considering a metal roof? We install standing seam and corrugated metal roofing."),
        (
            "§12",
            "Storm damage and an exposed roof? Emergency tarping available. Ask the office about your roof.",
        ),
        (
            "§13",
            "The price on your signed estimate is the price you pay. If we find hidden damage like rotted decking, we show you and price it before any extra work.",
        ),
        ("§16", "Looking at a flat or low-slope roof? We install TPO, modified bitumen and EPDM."),
        (
            "§2,3",
            "Checking credentials before you choose a roofer? Acacia is BBB Accredited, A+ and an IKO ROOFPRO Select Contractor.",
        ),
        (
            "§7",
            "Planning a call about your roof? Our office hours are Monday through Saturday, 7 AM to 6 PM CT. Closed Sunday.",
        ),
        (
            "§16",
            "Ask what goes into a replacement. Our work includes full tear-off, decking inspection, step flashing and drip edge.",
        ),
        (
            "§11",
            "Keep the roof photos with your estimate. We can meet the adjuster on site to show the damage. You decide what to accept.",
        ),
        (
            "§16",
            "Considering a roof inspection? Every free inspection comes with a written report and photos. Keep them with your home records.",
        ),
    ],
}
TIMES = {
    "eden": [(19, 30), (12, 15), (12, 15), (19, 30), (19, 30), (9, 30), (19, 30)],
    "acacia": [(8, 15), (12, 15), (10, 0), (12, 15), (10, 0), (9, 30), (12, 15)],
}
ANCHOR = date(2026, 11, 30)


def plan(brand, start, media, targets=None):
    if start.weekday() != 0 or brand not in CARDS:
        raise ValueError("Expected a brand and a Monday in Central Time")
    days = []
    for i in range(7):
        day = start + timedelta(days=i)
        card = (day - ANCHOR).days % len(CARDS[brand])
        source, text = CARDS[brand][card]
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
        if day["text"] != CARDS[batch["brand"]][day["card"]][1]:
            raise ValueError("Unreviewed copy")


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
