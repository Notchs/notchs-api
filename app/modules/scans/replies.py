"""What the bot says after a scan. Every reply answers the customer's own message, so it is free."""

from app.modules.auth.enums import TokenType
from app.modules.auth.tokens import lifetime
from app.modules.scans.models import RejectReason
from app.modules.scans.service import MAX_VENUES_PER_DAY

LINK_MINUTES = int(lifetime(TokenType.MAGIC_LINK).total_seconds() // 60)
_LINK_LINE = f"It works once and expires in {LINK_MINUTES} minutes:\n{{link}}"

SCAN_AT_VENUE = "Scan the QR at a venue to get stamped."
TRY_QUESTION = "What did you try? You can pick more than one."
TRY_BUTTON = "Choose"
TRY_VISIT_CLOSED = "This visit is closed."
TRY_NOT_ON_MENU = "That isn't on this venue's menu any more."


def stamp_text(venue_name: str, notch_no: int, points: int, link: str | None) -> str:
    """The link, when there is one, stays on the last line."""
    stamp = f"Stamp added at {venue_name}! Notch #{notch_no:04d}. +{points} points."
    if link is None:
        return f"{stamp}\n\nSend LOGIN to see your rank in your city."
    return f"{stamp}\n\nNow you can see your rank in your city. {_LINK_LINE.format(link=link)}"


def tried_text(tag_name: str) -> str:
    return f"Got it: {tag_name}."


def no_code_text(link: str | None) -> str:
    if link is None:
        return f"You're on Notchs! {SCAN_AT_VENUE} Send LOGIN to open your passport."
    return f"You're on Notchs! {SCAN_AT_VENUE}\n\nYour passport login link. " + _LINK_LINE.format(
        link=link
    )


def rejection_text(reason: RejectReason, venue_name: str | None) -> str | None:
    """None: no reply at all (blocked customers)."""
    place = venue_name or "this venue"
    match reason:
        case RejectReason.WRONG_CODE:
            return "That code didn't match. Please scan the QR at the counter again."
        case RejectReason.EXPIRED_CODE:
            return "That code has expired. Please scan the QR again."
        case RejectReason.ALREADY_TODAY:
            return f"You're already notched at {place} today. Come back tomorrow!"
        case RejectReason.DAILY_LIMIT:
            return (
                f"You've collected {MAX_VENUES_PER_DAY} stamps today, the daily limit. "
                "See you tomorrow!"
            )
        case RejectReason.CODE_USED:
            return "Lots of people just scanned. Wait 30 seconds and scan again."
        case RejectReason.UNKNOWN_VENUE | RejectReason.VENUE_UNAVAILABLE:
            return "We can't stamp at this venue right now."
        case RejectReason.BLOCKED_CUSTOMER:
            return None
