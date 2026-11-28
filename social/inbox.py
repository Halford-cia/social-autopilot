"""Only complete, single-intent questions qualify. Unrecognized text stays in the provider inbox."""

import re
from datetime import datetime, timedelta

from .planner import BOOK

BOOKING = "Pick your stay, then check dates and book through the listing. " + BOOK
AREA = "Our stays are near Fredericksburg, Texas, about 15 to 20 minutes from downtown, near wineries and Luckenbach."
MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DAY = r"(?:[1-9]|[12][0-9]|3[01])(?:st|nd|rd|th)?"
DATE = rf"(?:{MONTH} {DAY}|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?|\d{{4}}-\d{{2}}-\d{{2}})"
WHEN = rf"(?:this weekend|next weekend|tonight|{DATE}(?:\s*(?:-|–|to|through)\s*(?:{DATE}|{DAY}))?)"
STAY = r"(?:it|this|cherry cove|oak retreat|pecan haven|cloud dome|lunar dome|treehouse serenity dome|walnut horizon|cozy tiny home|das grun haus|the hidden tavern)"
FAQ = [
    (
        rf"(?:is {STAY} available|are you available|(?:do you have |any )?availability)(?: (?:for |on )?{WHEN})?",
        BOOKING,
    ),
    (r"how (?:can|do) (?:i|we) make a reservation", BOOKING),
    (r"how far are you from downtown(?: fredericksburg)?", AREA),
    (
        r"(?:how (?:do|can) (?:i|we) (?:book|reserve)(?: a stay)?|where (?:do|can) (?:i|we) book|booking link)",
        BOOKING,
    ),
    (
        r"(?:availability|(?:any|do you have) availability(?: this weekend)?|(?:are you|is (?:it|this)) available(?: this weekend)?|how (?:do|can) i check availability)",
        BOOKING,
    ),
    (r"(?:where are (?:you|the stays)(?: located)?|what area are you in|where is stay eden)", AREA),
]


def classify(text):
    if not isinstance(text, str):
        return None
    normalized = " ".join(text.casefold().strip().split()).rstrip("?!.")
    normalized = re.sub(r"^(?:hi|hello|hey)[,!]? +", "", normalized)
    return next((reply for pattern, reply in FAQ if re.fullmatch(pattern, normalized)), None)


def route(brand, kind, item, dest, now):
    dm = kind == "messages"
    if (dm and item.get("direction") == "outgoing") or (not dm and item.get("isAuthor") is True):
        return "ignore", None
    if not dm and item.get("accountId") != dest["accountId"]:
        return "ignore", None
    if brand == "acacia":
        return (
            "draft",
            "Thanks for reaching out to Acacia Roofing. Our office can help with your roofing question.",
        )
    reply = classify(item.get("text"))
    if not reply:
        return "human", None
    try:
        age = now - datetime.fromisoformat(item["createdAt"])
    except (KeyError, TypeError, ValueError):
        return "human", None
    if age < timedelta() or age >= timedelta(hours=24):
        return "human", None
    if dm:
        if (
            item.get("direction") != "incoming"
            or item.get("status") != "delivered"
            or item.get("payload")
            or not item.get("senderId")
            or (item["platform"] == "facebook" and item.get("recipientId") != dest["pageId"])
        ):
            return "human", None
        target = {"targetType": item["platform"]}
        if item["platform"] == "facebook":
            target["pageId"] = dest["pageId"]
        return "send", dict(
            accountId=dest["accountId"], recipientId=item["senderId"], text=reply, target=target
        )
    if (
        item.get("isAuthor") is not False
        or item.get("status") != "posted"
        or item.get("parentCommentId")
        or not item.get("postId")
    ):
        return "human", None
    return "send", dict(postId=item["postId"], parentCommentId=item["id"], text=reply)
