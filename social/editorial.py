"""Reviewed public copy and the language permitted around each fact."""

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

# Each choice describes only the pictured property. Keep qualifications in a single,
# indivisible fact block (particularly warranty, coverage and pricing for Acacia).
STAYS = {
    "serenity": ("Treehouse Serenity Dome has room for two.", "a long soak", "a suspended king bed"),
    "cloud": (
        "Cloud Dome sits on a wooded bluff and sleeps two.",
        "a private hot tub",
        "an outdoor shower",
    ),
    "walnut": (
        "Walnut Horizon sleeps four and has a full kitchen.",
        "breakfast in the kitchen",
        "coffee on the observation deck",
    ),
    "grun": ("Das Grun Haus is a two-bedroom cottage for up to six.", "yard games", "time at the fire pit"),
    "cherry": (
        "Cherry Cove is a canvas stay for two.",
        "a film on the projector",
        "a dip in the cowboy pool",
    ),
    "oak": (
        "Oak Retreat is a luxury tent for two.",
        "a soak in the private hot tub",
        "a film on the projector",
    ),
    "tavern": (
        "The Hidden Tavern has five bedrooms and a bunkroom.",
        "finding the speakeasy",
        "a soak in one of two hot tubs",
    ),
    "pecan": (
        "Pecan Haven is a tent treehouse about ten feet up.",
        "a night under canvas",
        "a morning in the trees",
    ),
    "cozy": (
        "Cozy Tiny Home has room for two.",
        "a chapter in the reading loft",
        "a dip in the cowboy pool",
    ),
    "lunar": (
        "Lunar Dome is a geodome for two.",
        "a treetop outdoor shower",
        "a soak in the private hot tub",
    ),
}
ROOF_TOPICS = [
    "your roof inspection",
    "the decking inspection",
    "documenting roof damage",
    "cleanup",
    "the workmanship warranty",
    "the service area",
    "synthetic underlayment",
    "ice and water shield",
    "metal roofing",
    "emergency tarping",
    "your signed estimate",
    "low-slope roofing",
    "contractor credentials",
    "office hours",
    "the replacement scope",
    "the adjuster visit",
    "your written inspection report",
]
PATTERNS = {
    "eden": (
        "Which would you pick first, {a} or {b}?",
        "What sounds better for the weekend, {a} or {b}?",
        "Start with {a}. Save {b} for later.",
        "Leave time for {a}.",
        "Make room for {a} and {b}.",
    ),
    "acacia": (
        "What would you ask about {a}?",
        "Have a question about {a}?",
        "Keep this in mind when you ask about {a}.",
        "Ask us about {a}.",
    ),
}


def material(brand, card):
    source, original = CARDS[brand][card]
    if brand == "eden":
        fact, *topics = STAYS[source]
    else:
        # An introductory question can be replaced; its complete answer stays together.
        fact = original.partition("? ")[2] or original
        topics = [ROOF_TOPICS[card]]
    return {"fact": fact, "topics": topics}


def validate_caption(brand, card, text):
    if type(card) is not int or not 0 <= card < len(CARDS[brand]) or not isinstance(text, str):
        raise ValueError("Invalid caption")
    # Immutable pre-migration previews are still reviewable under their original hash.
    if text == CARDS[brand][card][1]:
        return
    entry = material(brand, card)
    fact, topics = entry["fact"], entry["topics"]
    if text == fact:
        return
    for i, a in enumerate(topics):
        for pattern in PATTERNS[brand]:
            line = pattern.format(a=a, b=topics[(i + 1) % len(topics)])
            if text in (line + " " + fact, fact + " " + line):
                return
    raise ValueError("Caption contains unreviewed copy")
