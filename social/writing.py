"""Compose public text through Claude. Validate the full answer before it can leave the Mini."""

import json

from .claude import HAIKU, SONNET
from .editorial import BOOK, BRANDS, PATTERNS, material, validate_caption
from .inbox import AREA, BOOKING

VOICE = {
    "eden": "Write as a warm Hill Country host. Start from a concrete detail, let guests picture a choice, and leave some space.",
    "acacia": "Write as an Austin roofer speaking with a homeowner. Use plain English, practical questions and the exact qualifications supplied.",
}


def write_week(brand, start, cards, writer):
    system = (
        f"Write seven {BRANDS[brand]} social captions. {VOICE[brand]} "
        "For each day use its complete fact verbatim. Compose an optional opening or closing line "
        "using one of the supplied patterns verbatim, filling a and b with different topics from "
        "that day. Vary the order, rhythm and focus across the week. Use a single space between "
        "sentences. Omit links, hashtags, prices, testimonials and other claims. "
        "Return just a JSON array of seven complete caption strings in day order."
    )
    prompt = {
        "week": start.isoformat(),
        "patterns": PATTERNS[brand],
        "days": [material(brand, card) for card in cards],
    }
    texts = json.loads(writer.generate(SONNET, system, json.dumps(prompt), 1536))
    if not isinstance(texts, list) or len(texts) != 7 or len(cards) != 7:
        raise ValueError("Incomplete caption batch")
    for card, text in zip(cards, texts):
        validate_caption(brand, card, text)
    return texts


REPLY_FACTS = {
    BOOKING: (
        BOOKING,
        "Choose a stay to see available dates and book through its listing. " + BOOK,
        "You can check dates and book through the listing for your stay. " + BOOK,
    ),
    AREA: (
        AREA,
        "We're near Fredericksburg, about a 15 to 20 minute drive from downtown.",
        "Stay Eden is near Fredericksburg, Texas, with wineries and Luckenbach nearby.",
    ),
}
GREETINGS = ("", "Happy to help! ", "Thanks for asking! ")


def reply_text(writer, answer):
    facts = REPLY_FACTS[answer]
    system = (
        "Write a short Stay Eden reply for a single eligible FAQ. Choose an accurate full answer "
        "and optionally open with a supplied greeting. Keep its exact words, punctuation and link. "
        "Return only the complete plain-text reply."
    )
    text = writer.generate(HAIKU, system, json.dumps({"answers": facts, "greetings": GREETINGS}), 256)
    if any(text == greeting + fact for greeting in GREETINGS for fact in facts):
        return text
    return None
